# security/active_links.py
"""
S12 — Remote template & external link detection (SSRF prevention)

Parses Office .rels relationship files for TargetMode="External"
pointing to remote URLs. Also detects external data links in XLSX
and remote image references in DOCX/HTML.

The parser makes ZERO network requests — every external reference
is flagged, none is fetched.

Covers: DOCX, XLSX, PPTX
"""
import zipfile
import re
from urllib.parse import urlparse


# ── helpers ───────────────────────────────────────────────────────────────────

REMOTE_URL_RE = re.compile(
    r'https?://|ftp://|file://',
    re.IGNORECASE
)

EXTERNAL_MODE_RE = re.compile(
    r'TargetMode\s*=\s*["\']External["\']',
    re.IGNORECASE
)

TARGET_RE = re.compile(
    r'Target\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE
)

TYPE_RE = re.compile(
    r'Type\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE
)

# relationship types that are high risk
HIGH_RISK_TYPES = {
    "attachedtemplate",
    "externallink",
    "externallinkpath",
    "hyperlink",
    "image",
    "video",
    "audio",
}


def _finding(ftype: str, severity: str,
             detail: str, url: str = "",
             location: str = "") -> dict:
    return {
        "type":         ftype,
        "severity":     severity,
        "detail":       detail,
        "url":          url,
        "location":     location,
        "action_taken": "not_fetched",
    }


def _classify_rel_type(type_url: str) -> str:
    """Extract the short name from a relationship type URI."""
    return type_url.rstrip('/').split('/')[-1].lower()


def _is_remote(target: str) -> bool:
    return bool(REMOTE_URL_RE.match(target.strip()))


# ══════════════════════════════════════════════════════════════════════════════
# .rels scanner
# ══════════════════════════════════════════════════════════════════════════════

def _scan_rels_content(content: str, rels_path: str) -> list[dict]:
    """
    Parse one .rels XML string and return findings for
    External relationships pointing to remote URLs.
    """
    findings = []

    # split into individual Relationship elements
    relationships = re.findall(
    r'<Relationship\b.*?/>',
    content, re.IGNORECASE | re.DOTALL
)

    for rel in relationships:
        is_external = bool(EXTERNAL_MODE_RE.search(rel))
        target_m    = TARGET_RE.search(rel)
        type_m      = TYPE_RE.search(rel)

        if not target_m:
            continue

        target   = target_m.group(1)
        rel_type = _classify_rel_type(type_m.group(1)) if type_m else "unknown"

        # flag if explicitly External OR if target looks like a remote URL
        if is_external or _is_remote(target):
            severity = "high" if rel_type in HIGH_RISK_TYPES else "medium"

            # attached template = highest risk (silent fetch on open)
            if rel_type == "attachedtemplate":
                findings.append(_finding(
                    "remote_template", "high",
                    f"attachedTemplate points to remote URL: {target}",
                    url=target, location=rels_path))

            elif rel_type == "externallink" or rel_type == "externallinkpath":
                findings.append(_finding(
                    "external_link", "high",
                    f"external data link: {target}",
                    url=target, location=rels_path))

            else:
                findings.append(_finding(
                    "external_link", severity,
                    f"external relationship type='{rel_type}': {target}",
                    url=target, location=rels_path))

    return findings


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def scan_office_links(path: str) -> dict:
    """
    Scan a DOCX / XLSX / PPTX file for external links and
    remote template references.

    Returns:
        {
          "findings": [...],
          "external_urls": [str, ...],   # all remote URLs found
          "clean": bool
        }
    """
    findings     = []
    external_urls = []

    try:
        zf = zipfile.ZipFile(path, 'r')
    except Exception as e:
        return {"findings": [], "external_urls": [],
                "clean": True, "error": str(e)}

    rels_files = [n for n in zf.namelist()
                  if n.endswith('.rels')]

    for rels_path in rels_files:
        try:
            content = zf.read(rels_path).decode('utf-8', errors='replace')
        except Exception:
            continue

        file_findings = _scan_rels_content(content, rels_path)
        findings     += file_findings
        external_urls += [f["url"] for f in file_findings if f.get("url")]

    # also scan xl/externalLinks/ directory (XLSX-specific)
    ext_link_files = [n for n in zf.namelist()
                      if 'externallinks' in n.lower()
                      and n.endswith('.xml')]
    for el_path in ext_link_files:
        try:
            content = zf.read(el_path).decode('utf-8', errors='replace')
            # extract any file: or http: targets
            urls = re.findall(r'["\']((?:https?|ftp|file)://[^"\']+)["\']',
                              content, re.IGNORECASE)
            for url in urls:
                findings.append(_finding(
                    "external_link", "high",
                    f"XLSX external data link: {url}",
                    url=url, location=el_path))
                external_urls.append(url)
        except Exception:
            continue

    zf.close()
    # deduplicate by (type, url)
    seen = set()
    deduped = []
    for f in findings:
        sig = (f["type"], f.get("url", ""))
        if sig not in seen:
            seen.add(sig)
            deduped.append(f)

    return {
        "findings":      deduped,
        "external_urls": list(set(external_urls)),
        "clean":         len(deduped) == 0,
    }