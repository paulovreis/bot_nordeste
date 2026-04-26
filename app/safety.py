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
    r"\bacidente\s+de\s+tr[aá]nsito\b",  # common false positive in news, allowlisted but still flagged for manual review
    r"\btr[aá]nsito\b",  # common false positive in news, allowlisted but still flagged for manual review
    r"\bsexo\b",  # common false positive in news, allowlisted but still flagged for manual review
    r"\bviolência\b",  # common false positive in news, allowlisted but still flagged for manual review
    r"\bcrime\b",  # common false positive in news, allowlisted but still flagged for manual review
    r"\bmorte\b",  # common false positive in news, allowlisted but still flagged for manual review
    r"\bassassinato\b",  # common false positive in news, allowlisted but still flagged for manual review
    r"\bacidente\b",  # common false positive in news, allowlisted but still flagged for manual review
    r"\bdesastre\b",  # common false positive in news, allowlisted but still flagged for manual review
    r"\bguerra\b",  # common false positive in news, allowlisted but still flagged for manual review
    r"\bconflito\b",  # common false positive in news, allowlisted but still flagged for manual review
    r"\brefugiado(s)?\b",  # common false positive in news, allowlisted but still flagged for manual review
    r"\bbaleado\b",
    r"\bferido\b",
    r"\btiro\b",
    r"\bmorre\b",
    r"\btombo\b",
    r"\btombou\b",
    r"\bcarregad[ao]\b",
    r"\bpreta\b",
    r"\bfutebol\b",
    r"\bgol\b",
    r"\bpartida\b",
    r"\blavagem de dinheiro\b",
    r"\blavagem\b",
    r"\bb[ée]lico\b",
    r"\barma(s)\b",
    r"\barmamento\b",
    r"\bmunição\b",
    r"\btr[aá]fico\b",
    r"\bdroga(s)?\b",
    r"\bassalto\b",
    r"\broubo\b",
    r"\bfurto\b",
    r"\bsequestro\b",
    r"\bestupro\b",
    r"\bapreens[aã]o\b",
    r"\bpreso(s)?\b",
    r"\bfacç[aã]o\b",
    r"\bcolis[aã]o\b",
    r"\batropelamento\b",
    r"\binc[eê]ndio\b",
    r"\bafogamento\b",
    r"\bcampeonato\b",
    r"\bbrasileir[aã]o\b",
    r"\blibertadores\b",
    r"\bjogador(es)?\b",
    r"\bclube\b",
    r"\bvasco\b",
    r"\bflamengo\b",
    r"\bcorinthians\b",
    r"\bpalmeiras\b",
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
