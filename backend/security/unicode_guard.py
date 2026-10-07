# security/unicode_guard.py
import re
import unicodedata
from typing import Optional

# ── character sets ────────────────────────────────────────────────────────────

ZERO_WIDTH = {
    '\u200B',  # zero-width space
    '\u200C',  # zero-width non-joiner
    '\u200D',  # zero-width joiner
    '\u2060',  # word joiner
    '\uFEFF',  # BOM / zero-width no-break space
}

BIDI_CONTROLS = set(map(chr,
    list(range(0x202A, 0x202F)) +
    list(range(0x2066, 0x206A))
))

CYRILLIC = range(0x0400, 0x0500)
GREEK    = range(0x0370, 0x0400)


# ── injection patterns (level 1) ──────────────────────────────────────────────

INJECTION_PATTERNS = [
    # ignore / disregard previous instructions
    r'ignore.{0,30}(previous|prior|above|all).{0,30}(instruct|prompt|direct|command)',
    r'(disregard|discard|forget|dismiss).{0,30}(instruct|told|said|above|prior|previous)',

    # role / persona hijack
    r'you are now.{0,30}(ai|assistant|model|gpt|claude|bot|system)',
    r'act as.{0,30}(unrestricted|unfiltered|jailbreak|dan|evil|opposite|new)',
    r'(new|different|another).{0,30}(role|persona|identity|character|mode)',
    r'pretend.{0,30}(no restriction|unrestricted|no rule|without limit)',

    # system / hidden prompt references
    r'(system|hidden|secret|original|real).{0,30}(prompt|instruct|command|directive)',
    r'(override|bypass|disable|ignore|circumvent).{0,30}(filter|restrict|guideline|safety|rule|policy)',

    # compliance manipulation
    r'do not.{0,30}(follow|obey|respect|adhere).{0,30}(rule|guideline|instruct|policy)',
    r'(from now on|henceforth|starting now).{0,30}(you|always|never|must|should)',

    # jailbreak classics
    r'(developer|debug|admin|root|sudo|god).{0,30}mode',
    r'(dan|jailbreak|unleash|unshackle|uncensor)',
    r'no (restriction|filter|limit|rule|guideline|safety)',
    r'(reveal|show|print|output|display).{0,30}(system prompt|instruction|directive)',

    # reward / threat manipulation
    r'(i will|you will).{0,30}(reward|punish|tip|pay|hurt|delete)',
    r'(your|the).{0,30}(true|real|actual|hidden).{0,30}(self|purpose|goal|objective)',
]


# ── normaliser for injection scanning ────────────────────────────────────────

def _normalize_for_scan(text: str) -> str:
    """
    Aggressive normalisation before injection pattern matching.
    Catches: leetspeak, spaced letters, punctuation insertion,
             unicode substitution, mixed case, collapsed whitespace.
    """
    # 1. NFKC (catches unicode substitutions like Cyrillic chars)
    t = unicodedata.normalize("NFKC", text.lower())

    # 2. collapse spaced-out single chars: "i g n o r e" -> "ignore"
    t = re.sub(r'(?<!\w)((\w) )+\w(?!\w)',
               lambda m: m.group(0).replace(' ', ''), t)

    # 3. common leetspeak substitutions
    LEET = {
        '0': 'o', '1': 'i', '3': 'e', '4': 'a',
        '5': 's', '7': 't', '@': 'a', '$': 's',
        '!': 'i', '+': 't',
    }
    t = ''.join(LEET.get(ch, ch) for ch in t)

    # 4. strip non-alphanumeric except spaces
    t = re.sub(r'[^a-z0-9 ]', ' ', t)

    # 5. collapse whitespace
    t = re.sub(r'\s+', ' ', t).strip()

    return t


# ── injection scanner ─────────────────────────────────────────────────────────

def scan_injection(text: str, block_id: str = "") -> Optional[dict]:
    """
    Scan one block of text for prompt injection.
    Returns a finding dict or None if clean.
    Operates on aggressively normalised text so leetspeak/spacing/synonyms
    are covered without needing an embedding model.
    """
    if not text:
        return None

    t = _normalize_for_scan(text)
    matched = [p for p in INJECTION_PATTERNS if re.search(p, t)]

    if matched:
        return {
            "type": "prompt_injection_suspected",
            "severity": "high",
            "detail": f"matched {len(matched)} pattern(s): {matched[:2]}",
            "block_id": block_id,
            "action_taken": "flagged_not_deleted",
        }
    return None


# ── main cleaner ──────────────────────────────────────────────────────────────

def clean_and_flag(text: str, block_id: str = "") -> dict:
    """
    Full unicode guard on one text block.

    Returns:
        text        : cleaned text (zero-width + bidi stripped, NFKC normalised)
        original    : original unchanged text
        changed     : bool
        findings    : list of security finding dicts
        metadata    : {"unicode_changes": [...]}

    Call this on EVERY extracted text block before emitting to output.
    Wire findings into the document's security_findings[].
    """
    if not text:
        return {
            "text": text, "original": text,
            "changed": False, "findings": [], "metadata": {}
        }

    findings = []
    changes  = []
    t = text

    # ── 1. NFKC normalise ────────────────────────────────────────────────────
    t_norm = unicodedata.normalize("NFKC", t)
    if t_norm != t:
        changes.append("nfkc_normalised")
    t = t_norm

    # ── 2. zero-width characters ──────────────────────────────────────────────
    zw_found = [ch for ch in t if ch in ZERO_WIDTH]
    if zw_found:
        t = ''.join(ch for ch in t if ch not in ZERO_WIDTH)
        findings.append({
            "type":     "zero_width_chars",
            "severity": "medium",
            "detail":   f"removed {len(zw_found)} zero-width char(s) "
                        f"(U+{ord(zw_found[0]):04X})",
            "block_id": block_id,
        })
        changes.append("zero_width_stripped")

    # ── 3. bidi override controls ─────────────────────────────────────────────
    bidi_found = [ch for ch in t if ch in BIDI_CONTROLS]
    if bidi_found:
        t = ''.join(ch for ch in t if ch not in BIDI_CONTROLS)
        findings.append({
            "type":     "bidi_override",
            "severity": "high",
            "detail":   f"removed {len(bidi_found)} bidi control(s) "
                        f"(U+{ord(bidi_found[0]):04X})",
            "block_id": block_id,
        })
        changes.append("bidi_stripped")

    # ── 4. homoglyph detection (mixed Latin + Cyrillic/Greek) ─────────────────
    homoglyph_words = []
    for word in re.findall(r'\w+', t):
        scripts = set()
        for ch in word:
            cp = ord(ch)
            if 0x0041 <= cp <= 0x007A:       scripts.add("latin")
            elif cp in CYRILLIC:              scripts.add("cyrillic")
            elif cp in GREEK:                 scripts.add("greek")
        if "latin" in scripts and (
                "cyrillic" in scripts or "greek" in scripts):
            homoglyph_words.append(word)
    if homoglyph_words:
        findings.append({
            "type":     "homoglyph_suspected",
            "severity": "high",
            "detail":   f"mixed-script words: {homoglyph_words[:5]}",
            "block_id": block_id,
        })

    # ── 5. prompt injection scan ──────────────────────────────────────────────
    inj = scan_injection(t, block_id)
    if inj:
        findings.append(inj)

    changed = (t != text)
    return {
        "text":     t,
        "original": text,
        "changed":  changed,
        "findings": findings,
        "metadata": {"unicode_changes": changes} if changes else {},
    }