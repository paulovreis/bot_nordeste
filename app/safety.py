from __future__ import annotations

import re

# Each group covers a semantic category. Patterns use stem-based regex to capture
# conjugations and inflections without listing every word form individually.

_VIOLENCE = [
    # Matar (to kill): mata, matam, matou, mataram, matando, matado/s
    r"\bmat(ar|ou|aram|ando|ados?)\b",
    # Morrer (to die): morre, morrem, morreu, morreram, morrendo, morrer
    r"\bmorr(e[mr]?|eu|eram|endo|er)\b",
    # Morto/a/os/as (dead/killed)
    r"\bmort[oa]s?\b",
    r"\bóbito(s)?\b",
    # Assassinar: assassinato, assassinar, assassinou, assassinado
    r"\bassassin(ato|ar|ou|ando|ado)s?\b",
    r"\bhomicídio(s)?\b",
    r"\bfeminicídio(s)?\b",
    r"\bchacina(s)?\b",
    r"\blatrocínio(s)?\b",
    r"\bexecu[çc][aã]o(s)?\b",
    # Arma/Armado: arma, armas, armado/a/os/as, armamento/s
    r"\barm(as?|ad[oa]s?|amentos?)\b",
    # Atirar: atira, atirar, atirou, atiraram, atirando, atirado, atirador
    r"\batir(ar?|ou|aram|ando|ados?|ador)\b",
    r"\btiro(s)?\b",
    r"\btiroteio(s)?\b",
    # Baleado/a/os/as
    r"\bbalead[oa]s?\b",
    r"\bmuni[çc][aã]o\b",
    r"\barmamentos?\b",
    r"\bb[eé]lic[oa]s?\b",
    r"\bviolência\b",
    r"\bguerra(s)?\b",
    r"\bconflito(s)?\b",
    r"\brefugiado(s)?\b",
    r"\bmorte(s)?\b",
]

_CRIME = [
    r"\bcrime(s)?\b",
    r"\bcriminoso(s)?\b",
    r"\btr[aá]fico\b",
    r"\bdroga(s)?\b",
    r"\bassalto(s)?\b",
    r"\broubo(s)?\b",
    r"\bfurto(s)?\b",
    r"\bsequestro(s)?\b",
    r"\bestupro(s)?\b",
    r"\bpreso(s)?\b",
    r"\bapreens[aã]o\b",
    r"\bfac[cç][aã]o\b",
    r"\blavagem\s+de\s+dinheiro\b",
    r"\bassassinato(s)?\b",
]

_ACCIDENTS = [
    r"\bacidente(s)?\b",
    r"\bcolis[aã]o\b",
    r"\batropelamento(s)?\b",
    r"\binc[eê]ndio(s)?\b",
    r"\bafogamento(s)?\b",
    r"\bdesastre(s)?\b",
    r"\btombo(u)?\b",
]

_ADULT = [
    r"\bporn(o|ografia)?\b",
    r"\bsex[o]?\b",
    r"\bsexy\b",
    r"\bnude[z]?\b",
    r"\bnu[ao]s\b",
    r"\bconteúdo\s+adulto\b",
    r"\bacompanhante(s)?\b",
    r"\bgarota(s)?\s+de\s+programa\b",
    r"\bprostitui[çc][aã]o\b",
    r"\bstrip(tease)?\b",
    r"\b18\+\b",
]

_SPORTS = [
    r"\bfutebol\b",
    r"\bgol(s)?\b",
    r"\bpartida(s)?\b",
    r"\bcampeonato(s)?\b",
    r"\bbrasileir[aã]o\b",
    r"\blibertadores\b",
    r"\bjogador(es)?\b",
    r"\bclube(s)?\b",
    r"\bvasco\b",
    r"\bflamengo\b",
    r"\bcorinthians\b",
    r"\bpalmeiras\b",
]

_TRAVEL = [
    # Flights & tickets
    r"\bvoo(s)?\b",
    r"\bpassagem(ns)?\b",
    r"\bpassagens\b",
    r"\bmilhas\b",
    r"\bcia\s+a[eé]rea\b",
    r"\bcompanhia\s+a[eé]rea\b",
    r"\blatam\b",
    r"\bazul\s+(linhas|viagens|companhia)?\b",
    # Accommodation
    r"\bhotel(s|eis)?\b",
    r"\bpousada(s)?\b",
    r"\bhospedagem(ns)?\b",
    r"\bresort(s)?\b",
    r"\bapart(amento)?\s*hotel\b",
    # Trip & tourism
    r"\bviagem(ns)?\b",
    r"\bviagens\b",
    r"\broteiro(s)?\b",
    r"\bexcurs[aã]o(s)?\b",
    r"\bcruzeiro(s)?\b",
    r"\bpacote(s)?\s+(de\s+)?(viagem|tur(ismo|ístic[oa]))?\b",
    r"\btemporada(s)?\b",
    r"\balta\s+temporada\b",
    r"\bférias\b",
    # Booking & deals
    r"\breserva(s)?\b",
    r"\bpromo[çc][aã]o\s+(de\s+)?(viagem|passagem|voo)\b",
    r"\boferta(s)?\s+(de\s+)?(viagem|passagem|voo)\b",
    r"\bsaindo\s+de\b",
    r"\bida\s+e\s+volta\b",
    r"\bcheck[-\s]?in\b",
    r"\bcheck[-\s]?out\b",
    r"\bPorto Seguro\b",
]

_BLOCK_RE = re.compile(
    "|".join(_VIOLENCE + _CRIME + _ACCIDENTS + _ADULT + _SPORTS + _TRAVEL),
    re.IGNORECASE,
)

_ALLOWLIST = [
    r"\bsexo\s+(masculino|feminino)\b",
    r"\bidentidade\s+de\s+g[eê]nero\b",
]
_ALLOW_RE = re.compile("|".join(_ALLOWLIST), re.IGNORECASE)


def classify_text(title: str, snippet: str | None, description: str | None) -> tuple[str, str | None]:
    text = " ".join([t for t in [title, snippet or "", description or ""] if t]).strip()
    if not text:
        return "unknown", None
    m = _BLOCK_RE.search(text)
    if m:
        if _ALLOW_RE.search(text):
            return "unknown", "matched_adult_but_allowlisted"
        return "adult", f"matched:{m.group(0).lower()}"
    return "safe", None
