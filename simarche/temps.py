"""Calendrier de la simulation : jours, semaines, créneaux, saisons, météo."""
from __future__ import annotations

import datetime as dt

DEBUT = dt.date(2027, 1, 4)  # un lundi
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]
CRENEAUX = ["matin", "midi", "soir"]

# Horizon caché : les dirigeants ne voient que des dates, jamais un numéro de semaine (ni durée, ni fin de partie).
# Activé par la simulation (gerants.horizon_cache) ; la console et le journal gardent les numéros (brut=True).
MASQUER_SEMAINES = False


def date_of(day: int) -> dt.date:
    return DEBUT + dt.timedelta(days=day)


def week_of(day: int) -> int:
    return day // 7


def dow(day: int) -> int:
    return day % 7


def day_name(day: int) -> str:
    return JOURS[dow(day)]


def label(day: int, brut: bool = False) -> str:
    d = date_of(day)
    if MASQUER_SEMAINES and not brut:
        return f"{day_name(day)} {d.day} {MOIS[d.month - 1]}"
    return f"{day_name(day)} {d.day} {MOIS[d.month - 1]} (semaine {week_of(day) + 1})"


def short_label(day: int, brut: bool = False) -> str:
    d = date_of(day)
    if MASQUER_SEMAINES and not brut:
        return f"{day_name(day)[:3]} {d.day:02d}/{d.month:02d}"
    return f"S{week_of(day) + 1} {day_name(day)[:3]} {d.day:02d}/{d.month:02d}"


def week_tag(week: int) -> str:
    """Nom d'une semaine (0 = première) vu par les dirigeants : « S5 », ou « sem. du 01/02 » si l'horizon est caché."""
    if MASQUER_SEMAINES:
        d = date_of(week * 7)
        return f"sem. du {d.day:02d}/{d.month:02d}"
    return f"S{week + 1}"


def week_name(week: int) -> str:
    """« la semaine 5 », ou « la semaine du lundi 1 février » si l'horizon est caché."""
    if MASQUER_SEMAINES:
        d = date_of(week * 7)
        return f"la semaine du lundi {d.day} {MOIS[d.month - 1]}"
    return f"la semaine {week + 1}"


def slots(cfg: dict) -> list[str]:
    return list(CRENEAUX) if cfg.get("creneaux", True) else ["jour"]


def day_type(day: int) -> str:
    w = dow(day)
    return "samedi" if w == 5 else "dimanche" if w == 6 else "semaine"


def store_open(day: int, slot: str) -> bool:
    d = date_of(day)
    if (d.month, d.day) in ((12, 25), (1, 1), (5, 1)):
        return False
    if dow(day) == 6:  # dimanche : matin seulement
        return slot in ("matin", "jour")
    return True


def hyper_open(day: int) -> bool:
    d = date_of(day)
    return dow(day) != 6 and (d.month, d.day) not in ((12, 25), (1, 1), (5, 1))


def market_open(day: int, slot: str) -> bool:
    return dow(day) == 5 and slot in ("matin", "jour")


def season_factor(day: int, need: str, product_id: str | None = None) -> float:
    """Multiplicateur de consommation selon la saison et les fêtes."""
    d = date_of(day)
    w = week_of(day)
    f = 1.0
    summer = d.month in (6, 7, 8)
    if need == "boissons" and summer:
        f *= 1.25
    if product_id == "glace":
        f *= 3.0 if summer else 0.5
    if need == "snacks" and (d.month == 12 and d.day >= 15):
        f *= 1.35
    if product_id in ("vin", "biere") and (d.month == 12 and d.day >= 18):
        f *= 1.6
    if product_id == "chocolat" and d.month == 3 and d.day >= 20:
        f *= 2.0  # Pâques 2027 : 28 mars
    if w in (34, 35) and need in ("petit_dej", "snacks"):
        f *= 1.15  # rentrée
    return f


def next_weather(prev: str, day: int, rnd) -> tuple[str, bool]:
    """Chaîne de Markov simple ; renvoie (temps, canicule)."""
    d = date_of(day)
    summer = d.month in (6, 7, 8)
    winter = d.month in (11, 12, 1, 2)
    p_rain = 0.35 if winter else 0.18 if summer else 0.28
    if prev == "pluie":
        p_rain = min(0.7, p_rain + 0.25)
    r = rnd.random()
    if r < p_rain:
        w = "pluie"
    elif r < p_rain + 0.35:
        w = "nuageux"
    else:
        w = "soleil"
    heat = summer and d.month in (7, 8) and w == "soleil" and rnd.random() < 0.25
    return w, heat
