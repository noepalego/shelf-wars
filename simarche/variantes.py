"""Foyers variantes : plus de clients sans plus d'appels au modèle.

Chaque foyer piloté par le modèle (le « foyer-source ») entraîne `clients.variantes_par_foyer` autres foyers de la même
famille sociologique : même type de foyer, même quartier, même culture et même régime, mais une autre adresse, d'autres
revenus, des traits voisins, son propre garde-manger, son propre portefeuille de bons, sa propre carte de fidélité et ses
propres souvenirs des magasins (attente, ruptures, prix vus). Aucune variante n'appelle le modèle :

- Lieu : la variante part du choix de son foyer-source (celui du jour, sinon son dernier lieu de courses) et le garde,
  sauf si sa propre situation le rend nettement moins bon qu'une autre option : la formule de choix évalue chaque lieu
  pour la variante (distance depuis son adresse, bons et prix membres qu'elle détient, ses croyances), avec un bonus
  `attachement_source` pour le lieu du foyer-source. Un magasin peut donc capter une variante sans capter sa source.
- Panier : la variante reprend le dernier panier de son foyer-source dans ce magasin (mêmes produits, quantités mises
  à l'échelle de ses propres besoins), ce qui n'est pas en rayon ou pas couvert est choisi par la formule.
- Bilan de la semaine : la variante reprend les changements d'opinion de son foyer-source sur les magasins qu'elle a
  elle-même fréquentés ; elle répond aux questionnaires avec la formule ; elle ne publie pas d'avis.
- Bouche-à-oreille : ce qu'on dit à un foyer-source est aussi entendu par ses variantes (même entourage).
"""
from __future__ import annotations

import math

from .catalogue import PRODUCTS
from .cerveaux import FormulaBrain, gumbel
from .enquetes import formula_response

FORMULA = FormulaBrain()


def is_variant(c) -> bool:
    return bool(c.attrs.get("source"))


def count(cfg: dict) -> int:
    return int((cfg.get("clients") or {}).get("variantes_par_foyer", 0) or 0)


def recent_places(c, day: int, horizon: int = 10) -> str | None:
    """Dernier lieu de courses choisi (hors maison) dans les `horizon` derniers jours."""
    for d, place in reversed(c.__dict__.get("choix_recents", [])):
        if d < day - horizon:
            break
        if place != "maison":
            return place
    return None


def remember_choice(c, day: int, place: str) -> None:
    log = c.__dict__.setdefault("choix_recents", [])
    log.append((day, place))
    del log[:-20]


def remember_basket(c, store_id: str, day: int, liste: dict, achats: list) -> None:
    c.__dict__.setdefault("paniers_recents", {})[store_id] = {"jour": day, "liste": dict(liste),
                                                              "achats": [dict(a) for a in achats or []]}


def split(ctxs: list[dict]) -> tuple[list[int], list[int]]:
    src = [i for i, x in enumerate(ctxs) if not is_variant(x["client"])]
    var = [i for i, x in enumerate(ctxs) if is_variant(x["client"])]
    return src, var


# ============================================================================ lieu
def choose_place(world, ctx: dict, anchor: str | None, rnd, attachment: float) -> dict:
    utilities = FORMULA.place_utilities(ctx)
    best, best_u, why = "maison", -9.0, ""
    for place, u, reason in utilities:
        u += gumbel(rnd) * 0.5
        if anchor and place == anchor:
            u += attachment
        if u > best_u:
            best, best_u, why = place, u, reason
    label = "comme son foyer-source" if best == anchor else "son propre choix"
    return {"choix": best, "raison": f"{why} ({label})", "cerveau": "variante"}


async def choose_places(sim, ctxs: list[dict], rnd) -> list[dict]:
    w = sim.world
    src, var = split(ctxs)
    out: list[dict | None] = [None] * len(ctxs)
    if src:
        res = await sim.brain.choose_places([ctxs[i] for i in src], rnd)
        for i, r in zip(src, res):
            out[i] = r
    today = {ctxs[i]["client"].id: out[i]["choix"] for i in src}
    attachment = float(w.cfg["clients"].get("attachement_source", 1.5))
    vrnd = w.rng.stream("variantes_lieux")
    for i in var:
        c = ctxs[i]["client"]
        source = w.customers.get(c.attrs["source"])
        anchor = today.get(c.attrs["source"]) or (recent_places(source, w.day) if source else None)
        if anchor == "maison":
            anchor = None
        out[i] = choose_place(w, ctxs[i], anchor, vrnd, attachment)
    return out


# ============================================================================ panier
def echo_basket(bctx: dict, template: dict | None, rnd) -> dict:
    """Panier d'une variante : celui de son foyer-source, mis à l'échelle de ses besoins ; la formule complète."""
    base = FORMULA.choose_basket(bctx, rnd)  # carte, e-mails, achats d'impulsion et besoins non couverts
    if not template:
        base["raison"] = "achats habituels (variante)"
        return base
    visible = {it["produit"] for it in bctx["articles"]}
    liste = bctx["liste"]
    src_liste = template["liste"]
    kept, covered = [], set()
    for line in template["achats"]:
        pid = line.get("produit")
        p = PRODUCTS.get(pid)
        if p is None or pid not in visible:
            continue
        need = p.besoin
        if need in liste:
            ratio = liste[need] / src_liste[need] if src_liste.get(need) else 1.0
            covered.add(need)
        elif need in src_liste:
            continue  # la source en avait besoin, pas la variante
        else:
            ratio = 1.0  # achat d'impulsion de la source
            if rnd.random() < 0.5:
                continue
        q = max(1, min(12, int(math.floor(int(line.get("quantite", 1)) * ratio + 0.5))))
        kept.append({"produit": pid, "quantite": q, "date_courte": bool(line.get("date_courte"))})
    extra = [a for a in base["achats"] if PRODUCTS[a["produit"]].besoin not in covered
             and a["produit"] not in {k["produit"] for k in kept}]
    base["achats"] = kept + extra
    base["raison"] = "comme d'habitude chez nous (variante)"
    return base


async def choose_baskets(sim, bctxs: list[dict], rnd) -> list[dict]:
    w = sim.world
    src, var = split(bctxs)
    out: list[dict | None] = [None] * len(bctxs)
    if src:
        res = await sim.brain.choose_baskets([bctxs[i] for i in src], rnd)
        for i, r in zip(src, res):
            out[i] = r
            remember_basket(bctxs[i]["client"], bctxs[i]["lieu"], w.day, bctxs[i]["liste"], r.get("achats"))
    vrnd = w.rng.stream("variantes_paniers")
    for i in var:
        c = bctxs[i]["client"]
        source = w.customers.get(c.attrs["source"])
        template = (source.__dict__.get("paniers_recents") or {}).get(bctxs[i]["lieu"]) if source else None
        r = echo_basket(bctxs[i], template, vrnd)
        r["cerveau"] = "variante"
        out[i] = r
    return out


# ============================================================================ semaine
def reflect(sim, ctxs_var: list[dict], by_source: dict[str, dict]) -> None:
    """Bilan des variantes : changements d'opinion de la source (magasins fréquentés par la variante), questionnaires."""
    w = sim.world
    rnd = w.rng.stream("variantes_bilan")
    for ctx in ctxs_var:
        c = ctx["client"]
        r_src = by_source.get(c.attrs["source"]) or {}
        visited = set(ctx["magasins_visites"])
        opinions = [m for m in (r_src.get("magasins") or []) if w.resolve_store(m.get("magasin")) in visited]
        answers = []
        for sid, survey in ctx["enquetes"].items():
            rep = formula_response(c, sid, survey, rnd)
            if rep:
                answers.append(rep)
        sim.apply_reflection(ctx, {"magasins": opinions, "reponses_enquete": answers})


def share_talk(world, pair: dict, res: dict, apply_talk) -> None:
    """Le bouche-à-oreille reçu par un foyer-source est aussi entendu par ses variantes."""
    for vid in world.variantes.get(pair["autre"].id, []):
        v = world.customers.get(vid)
        if v is not None and v.active:
            apply_talk(world, {**pair, "autre": v, "parrainage": None}, res)
