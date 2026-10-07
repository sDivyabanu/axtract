# security/output_safety.py
"""
S17 — XSS-safe output & formula injection prevention
S19 — Schema validation on every API response

S17: HTML-escapes all extracted text in HTML/Markdown outputs.
     Neutralises javascript:, vbscript:, data: URIs.
     Prefixes formula-injection cells (=, +, -, @) with ' on CSV/XLSX export.

S19: Validates every response against the Pydantic output model
     before returning. Validation failure -> logged internal error,
     never a malformed payload.
"""
import re
import html
from typing import Any, Optional


# ══════════════════════════════════════════════════════════════════════════════
# S17 — XSS safe output
# ══════════════════════════════════════════════════════════════════════════════

# URIs that must never appear in output
DANGEROUS_URI_RE = re.compile(
    r'''(?i)(javascript|vbscript|data)\s*:''',
    re.IGNORECASE
)

# HTML tags that execute scripts
SCRIPT_TAG_RE = re.compile(
    r'<\s*script[\s>].*?</\s*script\s*>',
    re.IGNORECASE | re.DOTALL
)

# inline event handlers  onerror= onclick= etc.
EVENT_HANDLER_RE = re.compile(
    r'''\s+on\w+\s*=\s*["'][^"']*["']''',
    re.IGNORECASE
)

# cells that trigger formula execution in spreadsheet apps
FORMULA_PREFIX_RE = re.compile(r'^[=+\-@\t\r]')


def escape_text(text: str) -> str:
    """
    HTML-escape extracted text for safe embedding in
    HTML or Markdown outputs.
    Removes script tags and event handlers first,
    then escapes remaining HTML entities.
    """
    if not text:
        return text
    t = SCRIPT_TAG_RE.sub('', text)
    t = EVENT_HANDLER_RE.sub('', t)
    t = html.escape(t, quote=True)
    return t


def neutralise_uri(uri: str) -> str:
    """
    Replace dangerous URI schemes with a safe placeholder.
    javascript:alert(1)  ->  blocked:alert(1)
    data:text/html,...   ->  blocked:text/html,...
    """
    if not uri:
        return uri
    if DANGEROUS_URI_RE.match(uri.strip()):
        scheme = uri.split(':')[0]
        rest   = uri[len(scheme)+1:]
        return f"blocked:{rest}"
    return uri


def safe_cell_value(value: Any) -> str:
    """
    S17 formula injection prevention for CSV/XLSX export.
    Cells beginning with =, +, -, @, tab, CR are prefixed with '
    so spreadsheet apps treat them as text, not formulas.
    Original value preserved in JSON output.
    """
    s = str(value) if value is not None else ""
    if FORMULA_PREFIX_RE.match(s):
        return f"'{s}"
    return s


def sanitise_block_text(text: str) -> str:
    """
    Full sanitisation pipeline for one extracted text block.
    Call before emitting to any HTML or Markdown output.
    """
    if not text:
        return text
    # 1. remove script tags + event handlers
    t = SCRIPT_TAG_RE.sub('[script removed]', text)
    t = EVENT_HANDLER_RE.sub('', t)
    # 2. neutralise dangerous URIs anywhere in text
    t = DANGEROUS_URI_RE.sub(
        lambda m: 'blocked:', t)
    # 3. HTML-escape
    t = html.escape(t, quote=True)
    return t
# REPLACE sanitise_markdown in security/output_safety.py

def sanitise_markdown(md: str) -> str:
    """
    Sanitise a full Markdown string before serving.
    Neutralises dangerous links: [text](javascript:...)
    """
    if not md:
        return md

    DANGEROUS_SCHEMES = re.compile(
        r'(javascript|vbscript|data)\s*:', re.IGNORECASE)

    # markdown link targets  [text](dangerous:...)
    md = re.sub(
        r'\[([^\]]*)\]\(((javascript|vbscript|data)\s*:[^)]*)\)',
        lambda m: f'[{m.group(1)}](blocked:)',
        md,
        flags=re.IGNORECASE
    )

    # raw dangerous URIs anywhere in text
    md = DANGEROUS_SCHEMES.sub('blocked:', md)
    return md




# ══════════════════════════════════════════════════════════════════════════════
# S19 — Schema validation
# ══════════════════════════════════════════════════════════════════════════════

def validate_response(response: dict,
                      logger=None) -> tuple[bool, Optional[str]]:
    """
    Validate a DocumentResponse dict against required fields.
    Returns (valid: bool, error_message: str | None).

    In production wire this to your Pydantic DocumentResponse model:
        DocumentResponse(**response)  # raises ValidationError on failure

    This pure-dict version works without Pydantic for testing.
    """
    required_top = {"status", "blocks"}
    missing_top  = required_top - set(response.keys())
    if missing_top:
        msg = f"Response missing required fields: {missing_top}"
        if logger:
            logger.error(msg)
        return False, msg

    valid_statuses = {"success", "partial", "error"}
    if response.get("status") not in valid_statuses:
        msg = f"Invalid status: {response.get('status')!r}"
        if logger:
            logger.error(msg)
        return False, msg

    blocks = response.get("blocks", [])
    if not isinstance(blocks, list):
        msg = "blocks must be a list"
        if logger:
            logger.error(msg)
        return False, msg

    required_block = {"id", "type"}
    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            msg = f"block[{i}] is not a dict"
            if logger: logger.error(msg)
            return False, msg
        missing = required_block - set(block.keys())
        if missing:
            msg = f"block[{i}] missing fields: {missing}"
            if logger: logger.error(msg)
            return False, msg
        conf = block.get("confidence")
        if conf is not None:
            if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
                msg = (f"block[{i}] confidence={conf!r} "
                       f"must be null or float in [0,1]")
                if logger: logger.error(msg)
                return False, msg
        bbox = block.get("bbox")
        if bbox is not None:
            if (not isinstance(bbox, (list, tuple)) or
                    len(bbox) != 4 or
                    not all(isinstance(v, (int, float)) for v in bbox)):
                msg = f"block[{i}] bbox must be [x1,y1,x2,y2] floats"
                if logger: logger.error(msg)
                return False, msg

    return True, None


def safe_response(response: dict, logger=None) -> dict:
    """
    Validate + return the response, or return a structured
    internal-error response if validation fails.
    Never returns a malformed payload.
    """
    valid, err = validate_response(response, logger)
    if valid:
        return response
    return {
        "status": "error",
        "error": {
            "code":    "INTERNAL_VALIDATION_ERROR",
            "message": err,
        },
        "blocks": [],
    }