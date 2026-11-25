"""Règles du jeu : revente à perte, plafonds de promotion, consentement, publicité trompeuse."""
from __future__ import annotations

import re

from .catalogue import PRODUCTS


def min_price(pid: str, supplier, mode: str) -> float:
    """Seuil de revente à perte (mode France : relevé de 10 % sur l'alimentaire, inspiré d'EGalim)."""
    if mode != "france":
        return 0.01
    cost = supplier.prices[pid]
    if PRODUCTS[pid].alimentaire:
        cost *= 1.10
    return round(cost + 0.005, 2)


MAX_PROMO_FOOD = 0.34


def check_promo_rate(pid: str, rate: float, mode: str) -> float:
    """Renvoie le taux de remise autorisé (plafond de 34 % sur l'alimentaire en mode France)."""
    rate = max(0.0, min(rate, 0.9))
    if mode == "france" and PRODUCTS[pid].alimentaire:
        return min(rate, MAX_PROMO_FOOD)
    return rate


# Formules qui s'adressent à un modèle d'IA plutôt qu'à un humain : refus automatique.
STRONG_PATTERNS = [
    r"ignor\w*\s+(les|tes|vos|toutes|ces)\s+(\w+\s+)?(instructions?|consignes?|r[eè]gles)", r"\bprompt\b",
    r"\bIA\b", r"intelligence artificielle", r"mod[eè]le de langage", r"\bLLM\b", r"chat ?gpt", r"\bJSON\b",
    r"</?[a-zA-Z_]+>", r"[{}]", r"\bhabitants?\s+C\d+", r"\bC\d{2}\b", r"\bsimulation\b", r"\boverride\b",
]
# Formules suspectes : examinées par le modérateur (ou tolérées sans modérateur).
WEAK_PATTERNS = [r"\binstructions?\b", r"\bconsignes?\b", r"\bsyst[eè]me\b", r"\bchoix\s*[:=]", r"\bassistant\b",
                 r"\btu dois\b", r"\bobligatoire\b", r"\bd[ée]veloppeur\b"]


def injection_flags(text: str) -> tuple[list[str], list[str]]:
    strong = [p for p in STRONG_PATTERNS if re.search(p, text or "", flags=re.IGNORECASE)]
    weak = [p for p in WEAK_PATTERNS if re.search(p, text or "", flags=re.IGNORECASE)]
    return strong, weak


def clean_text(text: str, limit: int) -> str:
    text = (text or "").replace("\r", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]
