"""Azure-only OCR settings. No local/VLM configuration is changed here."""
from __future__ import annotations
import math
import os
from urllib.parse import urlsplit
from models.errors import AppError

# Azure Global Standard USD list rates; override for another deployment/pricing tier.
RATES = {"gpt-5.4-nano": (0.20, 1.25), "gpt-5.4-mini": (0.75, 4.50)}


def provider() -> str:
    value = os.getenv("AXTRACT_OCR_PROVIDER", "local").strip().lower()
    if value not in {"local", "azure"}:
        raise AppError("OCR_CONFIG", "AXTRACT_OCR_PROVIDER must be local or azure.", 503)
    return value


def _number(name, default, low, high, kind=float):
    try:
        value = kind(os.getenv(name, str(default)))
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError
        return value
    except ValueError:
        raise AppError("OCR_CONFIG", f"Invalid {name}; expected {low} through {high}.", 503) from None


def rates(config):
    model = config.get("model", config["deployment"])
    known = RATES.get(model)
    input_rate = config.get("input_price")
    output_rate = config.get("output_price")
    if input_rate is not None and output_rate is not None:
        return input_rate, output_rate
    return known


def configuration() -> dict:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
    key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
    if not endpoint or not key or not deployment:
        raise AppError("OCR_CONFIG", "Azure OCR requires AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT in backend/.env or the environment.", 503)
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path.rstrip("/") not in {"", "/openai/v1", "/openai/v1/responses"}):
        raise AppError("OCR_CONFIG", "Use an HTTPS Azure resource endpoint or its /openai/v1/ URL, without query parameters.", 503)
    endpoint = endpoint.removesuffix("/responses")
    base = endpoint if parsed.path.rstrip("/") else endpoint + "/openai/v1"
    result = dict(url=base + "/responses", key=key, deployment=deployment,
        model=os.getenv("AXTRACT_AZURE_MODEL", deployment).strip(),
        timeout=_number("AXTRACT_AZURE_TIMEOUT_SECONDS", 90, 1, 300),
        tokens=_number("AXTRACT_AZURE_MAX_OUTPUT_TOKENS", 4096, 256, 128000, int),
        edge=_number("AXTRACT_AZURE_MAX_IMAGE_EDGE", 4096, 512, 6000, int),
        dpi=_number("AXTRACT_AZURE_RENDER_DPI", 220, 72, 400),
        dense_dpi=_number("AXTRACT_AZURE_DENSE_DPI", 300, 72, 400),
        quality_reread=_number("AXTRACT_AZURE_QUALITY_REREADS", 1, 0, 1, int),
        native_hint_chars=_number("AXTRACT_AZURE_NATIVE_HINT_CHARS", 2000, 0, 6000, int),
        pixels=_number("AXTRACT_AZURE_MAX_PIXELS", 12000000, 262144, 20000000, int),
        retries=_number("AXTRACT_AZURE_MAX_RETRIES", 1, 0, 3, int),
        max_requests=_number("AXTRACT_AZURE_MAX_REQUESTS_PER_PAGE", 3, 1, 8, int),
        doc_seconds=_number("AXTRACT_AZURE_DOCUMENT_SECONDS", 180, 30, 1800),
        budget=_number("AXTRACT_AZURE_MAX_USD_PER_PAGE", 0, 0, 1),
        mode=os.getenv("AXTRACT_AZURE_MODE", "cloud").strip().lower(),
        detail=os.getenv("AXTRACT_AZURE_DETAIL", "original").strip().lower(),
        crops=os.getenv("AXTRACT_AZURE_CROPS", "off").strip().lower(),
        fallback=os.getenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "").strip(),
        fallback_model=os.getenv("AXTRACT_AZURE_FALLBACK_MODEL", "").strip(),
        prompt_version=os.getenv("AXTRACT_AZURE_PROMPT", "accuracy").strip().lower(),
        spread_split=_number("AXTRACT_AZURE_SPREAD_SPLIT", 1, 0, 1, int),
        scan_enhance=_number("AXTRACT_AZURE_SCAN_ENHANCE", 0, 0, 1, int),
        scan_upscale=_number("AXTRACT_AZURE_SCAN_UPSCALE", 1, 1, 3),
        scan_proofread=_number("AXTRACT_AZURE_SCAN_PROOFREAD", 0, 0, 1, int),
        native_spelling=_number("AXTRACT_AZURE_NATIVE_SPELLING", 1, 0, 1, int),
        reasoning=os.getenv("AXTRACT_AZURE_REASONING", "default").strip().lower(),
        scan_reasoning=os.getenv("AXTRACT_AZURE_SCAN_REASONING", "low").strip().lower(),
        verbosity=os.getenv("AXTRACT_AZURE_VERBOSITY", "high").strip().lower(),
        fallback_policy=os.getenv("AXTRACT_AZURE_FALLBACK_POLICY", "errors").strip().lower())
    for name, allowed in [("prompt_version", {"legacy", "layout", "accuracy"}), ("reasoning", {"default", "none", "low", "medium", "high"}), ("scan_reasoning", {"default", "none", "low", "medium", "high"}), ("mode", {"cloud", "hybrid"}), ("detail", {"high", "original"}),
                           ("crops", {"off", "auto", "always"}), ("fallback_policy", {"errors", "dense"}), ("verbosity", {"low", "medium", "high"})]:
        if result[name] not in allowed:
            raise AppError("OCR_CONFIG", f"Invalid Azure OCR {name}: choose {', '.join(sorted(allowed))}.", 503)
    for key_name, env_name in [("input_price", "AXTRACT_AZURE_INPUT_PRICE"), ("output_price", "AXTRACT_AZURE_OUTPUT_PRICE")]:
        if os.getenv(env_name, "").strip():
            result[key_name] = _number(env_name, 0, 0, 1000)
    if result["budget"] and rates(result) and rates(result)[1] <= 0:
        raise AppError("OCR_CONFIG", "A USD cap requires a positive output-token price.", 503)
    if result["budget"] and not rates(result):
        raise AppError("OCR_CONFIG", "A USD cap requires a known AXTRACT_AZURE_MODEL or explicit input/output prices per million tokens.", 503)
    if result["budget"] and result["fallback"] and (result["fallback_model"] or result["fallback"]) not in RATES:
        raise AppError("OCR_CONFIG", "A USD cap requires a known AXTRACT_AZURE_FALLBACK_MODEL for the fallback deployment.", 503)
    return result


def public_profile(config):
    """Reproduction metadata, with credentials and resource URL excluded."""
    return {k: v for k, v in config.items() if k not in {"key", "url", "native_reference", "audit_note", "native_lines", "region_note"}}
