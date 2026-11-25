"""Perception en magasin : ce qu'un client remarque en parcourant les allées, selon la géométrie et son attention.

Le client suit les allées dont il a besoin (et flâne parfois ailleurs). Ce qu'il voit vient de geometrie.py :
distance, angle par rapport à son regard (adulte ou enfant), largeur en rayon, présentoirs croisés sur son chemin.
Ces règles font partie de la « physique » du monde : elles ne sont jamais communiquées aux gérants, qui doivent
découvrir eux-mêmes les effets du merchandising.
"""
from __future__ import annotations

from .catalogue import NEEDS, PRODUCTS, is_allowed
from .geometrie import CHILD_EYE, eye_height, visibility
from .rng import clamp

MAX_EXTRAS = 12
KIDS_PULL = 0.7   # poids des demandes d'un enfant qui a repéré un produit qui lui plaît
# Usages culinaires : la proximité des deux références permet de remarquer
# le complément en cherchant le premier produit, dans les deux sens.
PAIRES_COMPLEMENTAIRES = (
    (("pates_mdd", "pates_marque"), ("sauce_tomate_mdd", "sauce_tomate_marque", "fromage_rape")),
    (("cereales_mdd", "cereales_marque"), ("lait_mdd", "lait_marque", "lait_bio")),
    (("baguette", "pain_mie_mdd", "pain_complet_bio"), ("beurre", "confiture")),
    (("salade", "tomates"), ("huile",)),
)
COMPLEMENTS: dict[str, set[str]] = {}
for premiers, seconds in PAIRES_COMPLEMENTAIRES:
    for premier in premiers:
        for second in seconds:
            COMPLEMENTS.setdefault(premier, set()).add(second)
            COMPLEMENTS.setdefault(second, set()).add(premier)


def attention(customer) -> float:
    a, s = customer.attrs, customer.state
    return clamp(0.45 + 0.35 * a["aime_flaner"] - 0.3 * s["pression_jour"] - 0.2 * s["fatigue"]
                 + 0.1 * (1 - a["conscienciosite"]), 0.1, 1.0)


def _preference(customer, pid: str) -> float:
    a = customer.attrs
    p = PRODUCTS[pid]
    bonus = 0.0
    if pid in a["produits_favoris"]:
        bonus += 0.3
    if pid in a["produits_detestes"]:
        bonus -= 0.3
    if p.bio and a["sensibilite_bio"] > 0.6:
        bonus += 0.1
    if p.impulsion:
        bonus += 0.15 * a["achats_impulsion"]
    return bonus


def walk(customer, store, day: int, slot: str, needs: dict, rnd, kids_present: bool) -> dict:
    a = customer.attrs
    member = store.member_of(customer.id)  # fiche de membre (ou None) : prix membres et prix individuels
    att = attention(customer)
    belief = customer.beliefs[store.id]
    needed_aisles = {store.planogram[pid]["rayon"] for pid in store.listed if store.listed[pid] and PRODUCTS[pid].besoin in needs}
    visited = set(needed_aisles)
    for aisle in range(6):
        if aisle not in visited and rnd.random() < a["aime_flaner"] * (1 - customer.state["pression_jour"]) * 0.6:
            visited.add(aisle)
    route = tuple(sorted(visited))
    eye = eye_height(a)
    child = CHILD_EYE if kids_present else None
    impulse = store.effect("impulsion") if getattr(store, "systems", None) else 1.0
    displays_of = {}
    for place, pids in store.secondary.items():
        for pid in pids:
            displays_of.setdefault(pid, []).append(place)
    searched, extras, ruptures = [], [], []
    found_needs = set()
    moved_needs = set()
    missed_essentials = {}
    unplanned = []
    sight = {}
    for pid, listed in store.listed.items():
        if not listed:
            continue
        p = PRODUCTS[pid]
        if not is_allowed(p, a):
            continue
        loc = store.planogram[pid]
        avail = store.on_shelf(pid, day)
        sight[pid] = visibility(loc, tuple(displays_of.get(pid, ())), route, eye, child)
        shelf, _, seen, _ = sight[pid]
        if p.besoin in needs:
            known = belief["plan_connu"].get(pid)  # rayon où le client a vu ce produit la dernière fois
            moved = known is not None and known != loc["rayon"]
            if moved:
                moved_needs.add(p.besoin)
            salience = shelf + 0.3 * seen + _preference(customer, pid)
            # Avec peu de temps, on compare surtout les variantes qui sautent aux yeux.
            detect = clamp(0.28 + 0.30 * att + 0.40 * salience - 0.16 * moved, 0.20, 0.98)
            if rnd.random() > detect:
                if avail > 0 and NEEDS[p.besoin].essentiel:
                    missed_essentials.setdefault(p.besoin, []).append((salience, pid))
                continue
            if avail <= 0:
                ruptures.append(pid)
                continue
            found_needs.add(p.besoin)
            searched.append((salience, pid))
            continue
        if avail > 0:
            unplanned.append(pid)
    for need, candidates in missed_essentials.items():
        if need not in found_needs and rnd.random() < 0.70 + 0.20 * att:
            searched.append(max(candidates))
            found_needs.add(need)

    # Seuls les articles de la liste effectivement vus peuvent rappeler un complément.
    reminders: dict[str, float] = {}
    for _, anchor in searched:
        origin = store.planogram[anchor]
        for pid in COMPLEMENTS.get(anchor, ()):
            loc = store.planogram[pid]
            distance = abs(loc["segment"] - origin["segment"])
            if loc["rayon"] == origin["rayon"] and distance <= 1:
                reminders[pid] = max(reminders.get(pid, 0.0), (0.36 if distance == 0 else 0.18) * (0.5 + att / 2))
    for pid in unplanned:
        p = PRODUCTS[pid]
        shelf, shelf_child, seen, seen_child = sight[pid]
        prob = 1 - (1 - min(0.95, shelf) * att * 0.55) * (1 - seen * (0.6 + 0.4 * att))
        promo = store.promo_for(pid, day)
        if promo and promo.affiche:
            prob *= 1.6
        if impulse != 1.0:
            prob *= impulse
        if kids_present and p.enfant > 0:
            spotted = 1 - (1 - min(0.95, shelf_child) * 0.5) * (1 - seen_child * 0.6)
            prob += (1 - prob) * KIDS_PULL * p.enfant * spotted
        prob += (1 - prob) * reminders.get(pid, 0.0)
        prob = clamp(prob + _preference(customer, pid) * 0.3, 0.0, 0.95)
        if rnd.random() < prob:
            extras.append((prob + _preference(customer, pid), pid))
    search_time = 2.5 * len(moved_needs)
    searched.sort(key=lambda x: (PRODUCTS[x[1]].besoin, -x[0]))
    extras.sort(reverse=True)
    items = []
    for sal, pid in searched + extras[:MAX_EXTRAS]:
        offer = store.shelf_offer(pid, day, slot, member)
        items.append({"produit": pid, "nom": PRODUCTS[pid].nom, "besoin": PRODUCTS[pid].besoin,
                      "cherche": PRODUCTS[pid].besoin in needs, "emplacement": store.layout_description(pid),
                      "niveau": store.planogram[pid]["niveau"], **offer})
    unfound = [n for n in needs if n not in found_needs]
    return {"articles": items, "ruptures": ruptures, "introuvables": unfound, "temps_recherche": search_time,
            "attention": round(att, 2), "rayons_visites": sorted(visited)}


def outside_offer(place: str, needs: dict, customer, cfg: dict) -> list[dict]:
    """Offre fixe de l'hypermarché (tout, moins cher) ou du marché (frais seulement, plus cher mais meilleur)."""
    items = []
    for pid, p in PRODUCTS.items():
        if p.besoin not in needs or not is_allowed(p, customer.attrs):
            continue
        if place == "marche":
            if p.besoin not in ("pain", "fruits", "legumes", "oeufs", "laitiers") or p.gamme == "mdd":
                continue
            price = p.prix_conseille * (1 + cfg["marche"]["majoration"])
        else:
            price = p.prix_conseille * (1 - cfg["hypermarche"]["remise"])
        items.append({"produit": pid, "nom": p.nom, "besoin": p.besoin, "cherche": True, "emplacement": place,
                      "niveau": "yeux", "prix": round(price, 2), "prix_membre": None, "promo": None, "affiche": False})
    return items


def covered_needs(place: str, needs: dict) -> list[str]:
    if place == "marche":
        return [n for n in needs if n in ("pain", "fruits", "legumes", "oeufs", "laitiers")]
    return list(needs)


def need_label(n: str) -> str:
    return NEEDS[n].nom
