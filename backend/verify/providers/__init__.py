"""Secondary validation providers."""

from verify.providers.base import ProviderCandidate, ProviderError, ValidationProvider
from verify.providers.docling import DoclingProvider
from verify.providers.pdf_crosscheck import PdfCrossCheckProvider


def default_providers(include_heavy: bool = False) -> list[ValidationProvider]:
    """Cheap providers always; heavy ones only when explicitly asked (escalation)."""
    providers: list[ValidationProvider] = [PdfCrossCheckProvider()]
    if include_heavy:
        providers.append(DoclingProvider())
    return providers


__all__ = ["DoclingProvider", "PdfCrossCheckProvider", "ProviderCandidate", "ProviderError", "ValidationProvider", "default_providers"]
