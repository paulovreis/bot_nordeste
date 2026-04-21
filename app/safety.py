from __future__ import annotations

import re


_ADULT_PATTERNS = [
    # PT-BR common explicit terms (kept short/strict to reduce false positives)
    r"\bporno\b",
    r"\bpornografia\b",
    r"\bsex[o]?\b",
    r"\bsexy\b",
    r"\bnude[z]?\b",
    r"\bnu(a|o)s\b",
    r"\bconte[uú]do\s+adulto\b",
    r"\bacompanhante(s)?\b",
    r"\bgarota(s)?\s+de\s+programa\b",
    r"\bprostitui(c|ç)[aã]o\b",
    r"\bstrip(tease)?\b",
    r"\b18\+\b",
]

# allowlist to reduce false positives for politics/news (e.g. "sexo" in demographics)
_ALLOWLIST = [
    r"\bsexo\s+(masculino|feminino)\b",
    r"\bidentidade\s+de\s+g[eê]nero\b",
]

_ADULT_RE = re.compile("|".join(_ADULT_PATTERNS), re.IGNORECASE)
_ALLOW_RE = re.compile("|".join(_ALLOWLIST), re.IGNORECASE)


def classify_text(title: str, snippet: str | None, description: str | None) -> tuple[str, str | None]:
    text = " ".join([t for t in [title, snippet or "", description or ""] if t]).strip()
    if not text:
        return "unknown", None
    if _ALLOW_RE.search(text):
        # still might contain explicit content, but allowlisted contexts are common in journalism
        pass
    m = _ADULT_RE.search(text)
    if m:
        if _ALLOW_RE.search(text):
            return "unknown", "matched_adult_but_allowlisted"
        return "adult", f"matched:{m.group(0).lower()}"
    return "safe", None
