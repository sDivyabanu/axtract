"""Cloud path contracts tested without credentials, network or local model loading."""
import httpx
import pytest
from PIL import Image

from extractors.azure_ocr_extractor import AzureOCRExtractor, configuration
from models.errors import AppError
from utils import deadline


@pytest.fixture(autouse=True)
def azure_env(monkeypatch):
    monkeypatch.setenv("AXTRACT_OCR_PROVIDER", "azure")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-secret-never-log")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "my-nano-deployment")
    monkeypatch.setenv("AXTRACT_AZURE_MAX_RETRIES", "0")
    monkeypatch.setenv("AXTRACT_AZURE_MODE", "cloud")
    monkeypatch.setenv("AXTRACT_AZURE_MAX_IMAGE_EDGE", "4096")
    monkeypatch.setenv("AXTRACT_AZURE_RENDER_DPI", "220")
    monkeypatch.setenv("AXTRACT_AZURE_DETAIL", "original")
    monkeypatch.setenv("AXTRACT_AZURE_MAX_USD_PER_PAGE", "0")
    monkeypatch.setenv("AXTRACT_AZURE_CROPS", "off")
    monkeypatch.delenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", raising=False)
    deadline.start(55)
    yield
    deadline.start(55)


def mock_api(monkeypatch, payload, status=200):
    calls = []
    def post(client, url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(status, json=payload, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.Client, "post", post)
    return calls


def complete(text="Hello **world**"):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": text}]}], "usage": {"input_tokens": 42, "output_tokens": 5}}


def test_routing_preserves_local_and_office(monkeypatch):
    from extractors.registry import get_extractor
    assert isinstance(get_extractor("pdf"), AzureOCRExtractor)
    assert isinstance(get_extractor("png"), AzureOCRExtractor)
    assert get_extractor("docx").name != AzureOCRExtractor.name
    monkeypatch.setenv("AXTRACT_OCR_PROVIDER", "local")
    assert get_extractor("pdf").name == "pdf-router"
    assert get_extractor("png").name == "rapidocr"
    monkeypatch.setenv("AXTRACT_OCR_PROVIDER", "typo")
    with pytest.raises(AppError, match="must be"):
        get_extractor("pdf")


def test_missing_key_fails_before_network(monkeypatch, tmp_path):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY")
    with pytest.raises(AppError, match="requires"):
        AzureOCRExtractor().extract(tmp_path / "nonexistent.pdf")


@pytest.mark.parametrize("endpoint", ["http://example.com", "https://example.com/api/projects/x", "https://u:p@example.com", "https://example.com?api-key=secret"])
def test_invalid_endpoint(monkeypatch, endpoint):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)
    with pytest.raises(AppError):
        configuration()


def test_image_request_and_provenance(monkeypatch, tmp_path):
    calls = mock_api(monkeypatch, complete())
    path = tmp_path / "image.png"
    Image.new("RGBA", (25, 40), (0, 0, 0, 0)).save(path)
    result = AzureOCRExtractor().extract(path)
    block = result.blocks[0]
    assert not result.errors and result.page_count == 1
    assert block.content == "Hello **world**"
    assert block.confidence is None and block.bbox is None and block.requires_review
    assert block.metadata["usage"]["input_tokens"] == 42
    url, request = calls[0]
    assert url == "https://example.openai.azure.com/openai/v1/responses"
    assert request["json"]["model"] == "my-nano-deployment"
    assert request["json"]["store"] is False
    assert request["json"]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("status,payload,code", [
    (429, {"error": "test-secret-never-log"}, "CLOUD_OCR_TRANSIENT"),
    (401, {}, "CLOUD_OCR_FAILED"),
    (200, {"status": "incomplete"}, "CLOUD_OCR_INCOMPLETE"),
    (200, {"status": "completed", "output": []}, "CLOUD_OCR_FAILED"),
    (200, {"status": "completed", "output": None}, "CLOUD_OCR_FAILED"),
    (200, {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": None}]}]}, "CLOUD_OCR_FAILED"),
    (200, {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}, "CLOUD_OCR_REFUSED"),
])
def test_errors_not_silent_fallback(monkeypatch, tmp_path, status, payload, code):
    calls = mock_api(monkeypatch, payload, status)
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 30)).save(path)
    result = AzureOCRExtractor().extract(path)
    assert not result.blocks and result.errors[0].code == code
    assert "test-secret-never-log" not in result.errors[0].message
    assert len(calls) == 1


def test_pdf_page_order_and_partial_failure(monkeypatch, tmp_path):
    import pymupdf
    path = tmp_path / "pages.pdf"
    with pymupdf.open() as doc:
        doc.new_page(); doc.new_page()
        doc.save(path)
    def transcribe(self, image, config):
        if getattr(self, "called", False):
            raise AppError("CLOUD_OCR_FAILED", "second page failed", 502)
        self.called = True
        return "Page one", {}
    monkeypatch.setattr(AzureOCRExtractor, "_transcribe", transcribe)
    result = AzureOCRExtractor().extract(path)
    assert result.page_count == 2
    assert [b.page for b in result.blocks] == [1]
    assert result.errors[0].page == 2


def test_parse_skips_local_region_models(monkeypatch, tmp_path):
    from services import parse_service
    monkeypatch.setenv("AXTRACT_VERIFY", "0")
    mock_api(monkeypatch, complete("# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |"))
    def no_regions(*args, **kwargs):
        pytest.fail("local region recognition must not run on cloud transcription")
    monkeypatch.setattr(parse_service, "route_regions", no_regions)
    path = tmp_path / "page.png"
    Image.new("RGB", (40, 50), "white").save(path)
    response = parse_service.parse_path(path, path.name, persistent_preview=False)
    assert response.status == "success"
    assert "# Title" in response.markdown and "| 1 | 2 |" in response.markdown


@pytest.mark.parametrize("suffix", ["", "/openai/v1/", "/openai/v1/responses"])
def test_endpoint_url_forms(monkeypatch, suffix):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com" + suffix)
    assert configuration()["url"] == "https://example.openai.azure.com/openai/v1/responses"
