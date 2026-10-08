"""Security runs before Verify, and a Verify failure never costs the extraction or the security results."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import services.parse_service as ps
import verify.engine as engine
from main import app
from tests.verify_fixtures import make_docx

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def docx_bytes(tmp_path):
    return make_docx(tmp_path / "a.docx").read_bytes()


def _post(client, data):
    return client.post("/api/parse", files={"file": ("a.docx", data, DOCX)})


def test_security_checks_run_before_validation(docx_bytes, monkeypatch):
    order: list[str] = []
    for name in ("pre_scan", "scan_hidden_content", "scan_blocks"):
        real = getattr(ps, name)
        monkeypatch.setattr(ps, name, lambda *a, _r=real, _n=name, **k: (order.append(_n), _r(*a, **k))[1])
    real_verify = engine.verify_extraction
    monkeypatch.setattr(engine, "verify_extraction", lambda *a, **k: (order.append("verify"), real_verify(*a, **k))[1])
    assert _post(TestClient(app), docx_bytes).status_code == 200
    assert order.index("verify") > max(order.index(n) for n in ("pre_scan", "scan_hidden_content", "scan_blocks"))


def test_a_verify_crash_keeps_extraction_and_security_results(docx_bytes, monkeypatch):
    client = TestClient(app)
    baseline = _post(client, docx_bytes).json()
    monkeypatch.setattr(engine, "verify_extraction", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = _post(client, docx_bytes)
    body = r.json()
    assert r.status_code == 200 and body["validation"] is None
    assert [b["content"] for b in body["blocks"]] == [b["content"] for b in baseline["blocks"]]
    assert body["security_findings"] == baseline["security_findings"]
