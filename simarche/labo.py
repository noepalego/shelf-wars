"""Laboratoire : expériences contrôlées pour vérifier que les clients réagissent de façon crédible.

Les gérants sont remplacés par des réglages fixes ; on compare des variantes d'un même monde (même graine).
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .catalogue import PRODUCTS
from .config import load_config
from .simulation import Simulation

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / "runs" / "_labo"


def _cfg(brain: str, households: int, weeks: int) -> dict:
    return load_config(None, {"nom": "labo", "foyers": households, "semaines": weeks, "evenements": [],
                              "clients": {"cerveau": brain},
                              "magasins": [{"id": "A", "nom": "Le Panier Malin", "gerant": "regles:fixe"},
                                           {"id": "B", "nom": "Chez Marcel", "gerant": "regles:fixe"}]})


def scenario(name: str, brain: str, households: int, days: int, setup) -> Simulation:
    d = LAB / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    cfg = _cfg(brain, households, max(1, (days + 6) // 7))
    sim = Simulation(cfg, d, quiet=True)
    setup(sim.world)
    asyncio.run(sim.run(days))
    sim.close()
    return sim


def visits(sim, place: str, start: int, end: int) -> int:
    w = sim.world
    if place in w.stores:
        return sum(sum(w.stores[place].visits.get(d, {}).values()) for d in range(start, end))
    return sum(w.outside.get(d, {}).get(place, [0])[0] for d in range(start, end))


def share(sim, place: str, start: int, end: int) -> float:
    tot = sum(visits(sim, p, start, end) for p in list(sim.world.stores) + ["hyper", "marche"])
    return visits(sim, place, start, end) / tot if tot else 0.0


def markup(world, sid: str, m: float) -> None:
    st = world.stores[sid]
    for pid, p in PRODUCTS.items():
        st.prices[pid] = round(p.prix_conseille * m, 2)


def run_lab(brain: str = "formule", households: int = 30) -> list[tuple]:
    days = 14 if brain == "formule" else 7
    if brain in ("ia", "mixte"):
        print("Laboratoire avec l'IA : environ 150 appels (une à deux heures, consomme ton abonnement).")
    rows = []

    # 1. Élasticité : A augmente ses prix
    shares = []
    for m in (1.0, 1.1, 1.2, 1.4):
        sim = scenario(f"elasticite_{int(m * 100)}", brain, households, days, lambda w, m=m: markup(w, "A", m))
        shares.append(share(sim, "A", days // 2, days))
    ok = all(shares[i] >= shares[i + 1] - 0.03 for i in range(3)) and shares[0] - shares[-1] > 0.05
    rows.append(("Élasticité prix", "part de A quand ses prix montent de 0/10/20/40 %",
                 " → ".join(f"{s * 100:.0f} %" for s in shares), ok))

    # 2. Distance : à prix égaux, chacun va plutôt au plus proche
    sim = scenario("distance", brain, households, days, lambda w: None)
    near, tot = 0, 0
    for c in sim.world.customers.values():
        stores_v = [(s, p) for d, s, p in c.visits if s in sim.world.stores]
        if not stores_v:
            continue
        closest = min(sim.world.stores, key=lambda s: c.distance_km(s))
        near += sum(1 for s, _ in stores_v if s == closest)
        tot += len(stores_v)
    frac = near / tot if tot else 0
    rows.append(("Distance", "part des visites dans le magasin le plus proche", f"{frac * 100:.0f} %", frac > 0.55))

    # 3. Coupon : la moitié des clients reçoit un bon de 4 € chez A
    treated: set = set()

    def coupon_setup(w, issue):
        st = w.stores["A"]
        st.loyalty["actif"] = True
        ids = sorted(w.customers)
        targets = set(w.rng.stream("labo_coupons").sample(ids, len(ids) // 2))
        if issue:
            treated.update(targets)
        for cid in ids:
            c = w.customers[cid]
            st.join(c, 0)
            if issue and cid in targets:
                cp = st.new_coupon(cid, "remise_euros_panier", 4.0, None, 15.0, 0, days, "carte", "4 € offerts dès 15 €")
                c.receive_coupon(cp)
    sim = scenario("coupon", brain, households, days, lambda w: coupon_setup(w, True))
    control = scenario("coupon_temoin", brain, households, days, lambda w: coupon_setup(w, False))
    t = [sum(1 for d, s, _ in c.visits if s == "A") for cid, c in sim.world.customers.items() if cid in treated]
    u = [sum(1 for d, s, _ in c.visits if s == "A") for cid, c in control.world.customers.items() if cid in treated]
    mt, mu = (sum(t) / len(t) if t else 0), (sum(u) / len(u) if u else 0)
    used = sum(1 for c in sim.world.stores["A"].coupons.values() if c.utilise)
    rows.append(("Coupon", "visites chez A des mêmes foyers avec coupon / dans la partie témoin (utilisations)", f"{mt:.2f} / {mu:.2f} ({used}/{len(t)})", mt > mu))

    # 4. Hauteur des yeux : un produit d'impulsion en bas ou à hauteur des yeux
    pid = "biscuits_marque"
    sold = {}
    for lvl in ("bas", "yeux"):
        def eye(w, lvl=lvl):
            st = w.stores["A"]
            other = next(p for p, loc in st.planogram.items() if p != pid and loc["rayon"] == st.planogram[pid]["rayon"] and loc["niveau"] == lvl)
            a, b = dict(st.planogram[pid]), dict(st.planogram[other])
            st.planogram[pid] = {**a, "segment": b["segment"], "niveau": b["niveau"]}
            st.planogram[other] = {**b, "segment": a["segment"], "niveau": a["niveau"]}
        sim = scenario(f"hauteur_{lvl}", brain, households, days, eye)
        sold[lvl] = sum(sim.world.stores["A"].sales.get(d, {}).get(pid, [0])[0] for d in range(days))
    rows.append(("Hauteur des yeux", f"ventes de « {PRODUCTS[pid].nom} » chez A : en bas / à hauteur des yeux",
                 f"{sold['bas']} / {sold['yeux']}", sold["yeux"] > sold["bas"]))

    # 5. Option extérieure : les deux magasins augmentent leurs prix de 25 %
    base = scenario("exterieur_base", brain, households, days, lambda w: None)
    high = scenario("exterieur_cher", brain, households, days, lambda w: (markup(w, "A", 1.25), markup(w, "B", 1.25)))
    s0, s1 = share(base, "hyper", 0, days), share(high, "hyper", 0, days)
    rows.append(("Option extérieure", "part de l'hypermarché : prix normaux / +25 % partout", f"{s0 * 100:.0f} % / {s1 * 100:.0f} %", s1 > s0))

    # 6. Cohérence : aucun achat contraire au régime
    from .catalogue import is_allowed
    bad = 0
    for st in base.world.stores.values():
        for tk in st.tickets:
            c = base.world.customers[tk["client"]]
            bad += sum(1 for l in tk["lignes"] if not is_allowed(PRODUCTS[l["produit"]], c.attrs))
    rows.append(("Cohérence", "achats contraires au régime ou au foyer", str(bad), bad == 0))

    print(f"\nLaboratoire (cerveau : {brain}, {households} foyers, {days} jours par scénario)\n")
    for name, what, val, ok in rows:
        print(f"[{'OK' if ok else 'À REVOIR'}] {name} — {what} : {val}")
    return rows
