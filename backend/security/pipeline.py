from pathlib import Path

from models.document import DocumentBlock, SecurityFinding


def pre_scan(
    file_path: Path,
    file_type: str,
) -> list[SecurityFinding]:
    """
    Security checks that require the original file.
    Must run while the temporary file still exists.
    """
    findings: list[SecurityFinding] = []

    if file_type in {"xlsx", "docx", "pptx"}:
        from security.office_scan import scan_office

        # scan_office returns {"findings": [...], ...}; iterate its findings list.
        raw_findings = scan_office(file_path).get("findings", [])

        for item in raw_findings:
            findings.append(
                SecurityFinding(
                    type=item["type"],
                    severity=item["severity"],
                    detail=item["detail"],
                    page=item.get("page"),
                    block_id=item.get("block_id") or None,
                    action_taken=item.get("action_taken", "flagged"),
                )
            )

    return findings
def scan_hidden_content(
    file_path: Path,
    file_type: str,
) -> tuple[list[dict], list[SecurityFinding]]:
    """
    Scan the original document for content that may be invisible
    or hidden from a human reader.

    Returns:
        hidden_content: content separated from the main document body
        findings: security findings generated during the scan
    """
    findings: list[SecurityFinding] = []
    hidden_content: list[dict] = []

    if file_type == "pdf":
        from security.hidden_content import scan_pdf_page
        import fitz

        with fitz.open(file_path) as doc:
            for page_num, page in enumerate(doc):
                result = scan_pdf_page(page, page_num + 1)

                hidden_content.extend(
                    result.get("hidden_content", [])
                )

                for item in result.get("findings", []):
                    findings.append(
                        SecurityFinding(
                            type=item["type"],
                            severity=item["severity"],
                            detail=item["detail"],
                            page=item.get("page"),
                            block_id=item.get("block_id") or None,
                            action_taken=item.get(
                                "action_taken",
                                "moved_to_hidden_content",
                            ),
                        )
                    )

    elif file_type == "xlsx":
        from security.hidden_content import scan_xlsx

        result = scan_xlsx(str(file_path))

        hidden_content.extend(
            result.get("hidden_content", [])
        )

        for item in result.get("findings", []):
            findings.append(
                SecurityFinding(
                    type=item["type"],
                    severity=item["severity"],
                    detail=item["detail"],
                    page=item.get("page"),
                    block_id=item.get("block_id") or None,
                    action_taken=item.get(
                        "action_taken",
                        "moved_to_hidden_content",
                    ),
                )
            )

    elif file_type == "pptx":
        from security.hidden_content import scan_pptx

        result = scan_pptx(str(file_path))

        hidden_content.extend(
            result.get("hidden_content", [])
        )

        for item in result.get("findings", []):
            findings.append(
                SecurityFinding(
                    type=item["type"],
                    severity=item["severity"],
                    detail=item["detail"],
                    page=item.get("page"),
                    block_id=item.get("block_id") or None,
                    action_taken=item.get(
                        "action_taken",
                        "moved_to_hidden_content",
                    ),
                )
            )

    return hidden_content, findings


def scan_blocks(
    blocks: list[DocumentBlock],
) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []

    from security.unicode_guard import clean_and_flag

    for block in blocks:
        if not block.content:
            continue

        result = clean_and_flag(
            block.content,
            block.id,
        )

        block.content = result["text"]

        for item in result["findings"]:
            findings.append(
                SecurityFinding(
                    type=item["type"],
                    severity=item["severity"],
                    detail=item["detail"],
                    block_id=block.id,
                    page=block.page,
                    bbox=block.bbox,
                    action_taken=item.get("action_taken", "flagged"),
                )
            )

            block.requires_review = True

    return findings