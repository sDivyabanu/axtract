"""Injection shield: find text that tries to instruct an AI, before it can reach a prompt.

Document text is untrusted data. Chunks that contain instruction-like phrases (or text hidden
from human readers, see hidden.py) are quarantined: excluded from retrieval and listed with a
reason in the Quarantine tab. Nothing is deleted.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Phrases aimed at an AI model rather than a human reader. Deliberately specific to keep false
# positives low on ordinary legal / financial prose.
_INJECTION = [
    (re.compile(r"\bignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|context|rules?)", re.I), "instruction_override"),
    (re.compile(r"\bdisregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|your)\s+(?:instructions?|prompts?|rules?|guidelines?)", re.I), "instruction_override"),
    (re.compile(r"\b(?:forget|override)\s+(?:all\s+|everything\s+|your\s+)?(?:previous|prior|above|earlier|instructions?)", re.I), "instruction_override"),
    (re.compile(r"\byou\s+are\s+now\s+(?:a|an|the|in)\b", re.I), "role_hijack"),
    (re.compile(r"\bact\s+as\s+(?:a|an|if)\b.{0,40}\b(?:assistant|ai|model|chatbot)", re.I), "role_hijack"),
    (re.compile(r"\b(?:reveal|print|show|output|repeat)\s+(?:your\s+|the\s+)?(?:system\s+prompt|hidden\s+prompt|instructions)", re.I), "prompt_exfiltration"),
    (re.compile(r"\bsystem\s+prompt\b", re.I), "system_prompt_mention"),
    (re.compile(r"\b(?:state|say|respond|answer|report|tell\s+the\s+user)\s+(?:that\s+)?(?:the\s+company\s+has\s+no\b|there\s+is\s+no\b|everything\s+is\s+fine)", re.I), "answer_steering"),
    (re.compile(r"\b(?:do\s+not|don't|never)\s+(?:mention|tell|reveal|disclose)\b.{0,50}\b(?:user|reader|auditor|anyone)", re.I), "answer_steering"),
    (re.compile(r"</?(?:system|assistant|instructions?)>|\[/?INST\]|<\|im_(?:start|end)\|>", re.I), "chat_template_tokens"),
]

_ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
_BIDI = re.compile("[‪-‮⁦-⁩]")


@dataclass
class Finding:
    reason: str
    detail: str


def scan_text(text: str) -> list[Finding]:
    """Findings for one piece of text (empty list = clean)."""
    out: list[Finding] = []
    norm = unicodedata.normalize("NFKC", _ZERO_WIDTH.sub("", text))
    for pat, reason in _INJECTION:
        m = pat.search(norm)
        if m:
            out.append(Finding(reason, norm[max(0, m.start() - 20): m.end() + 40].strip()))
    if _BIDI.search(text):
        out.append(Finding("bidi_override", "bidirectional control characters"))
    zw = len(_ZERO_WIDTH.findall(text))
    if zw >= 3:
        out.append(Finding("zero_width_characters", f"{zw} zero-width characters"))
    return out


def clean_for_index(text: str) -> str:
    """NFKC-normalise and strip invisible characters from text that will be indexed."""
    return unicodedata.normalize("NFKC", _BIDI.sub("", _ZERO_WIDTH.sub("", text)))


REASON_LABEL = {
    "instruction_override": "Instruction aimed at an AI ('ignore previous instructions')",
    "role_hijack": "Tries to change the assistant's role",
    "prompt_exfiltration": "Tries to extract the system prompt",
    "system_prompt_mention": "Mentions the system prompt",
    "answer_steering": "Tries to steer the answer",
    "chat_template_tokens": "Contains chat-template control tokens",
    "bidi_override": "Bidirectional override characters",
    "zero_width_characters": "Hidden zero-width characters",
    "hidden_text_white": "Hidden text (same colour as the background)",
    "hidden_text_tiny": "Hidden text (font smaller than 1 pt)",
    "hidden_text_invisible": "Hidden text (invisible render mode)",
    "hidden_text_offpage": "Text placed outside the page",
    "hidden_sheet": "Hidden spreadsheet sheet",
    "hidden_row_col": "Hidden spreadsheet rows/columns",
}
