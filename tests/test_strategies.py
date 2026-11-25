import asyncio
from collections import Counter

import pytest

from simarche.config import load_config
from simarche.gerants.decisions import apply_decisions, empty_decisions
from simarche.gerants.strategies import ARMS, retention_results
from simarche.simulation import Simulation


@pytest.fixture
def sim(tmp_path):
    cfg = load_config(overrides={"foyers": 10, "semaines": 8, "clients": {"cerveau": "formule"},
        "magasins": [{"id": "A", "nom": "A", "gerant": "regles:adaptatif"},
                     {"id": "B", "nom": "B", "gerant": "regles:temoin"}]})
    s = Simulation(cfg, tmp_path, quiet=True)
    yield s
    s.close()


def test_exploration_reprise_et_comptabilite(sim):
    asyncio.run(sim.run(35))
    store = sim.world.stores["A"]
    assert {h["arm"] for h in store.strategy_history} == set(ARMS)
    second = Simulation.resume(sim.run_dir, quiet=True)
    try:
        asyncio.run(sim.run(7))
        asyncio.run(second.run(7))
        assert second.world.stores["A"].strategy_history == store.strategy_history
        assert second.world.stores["A"].profit == pytest.approx(store.profit)
        assert store.profit == pytest.approx(store.cash - store.cash0 + store.inventory_value() - store.inv0, abs=.05)
    finally:
        second.close()


def test_tarification_et_frais(sim):
    store = sim.world.stores["A"]
    manager = sim.managers["A"]
    manager.policy = "tarification"
    asyncio.run(manager.decide())
    p = "pates_mdd"
    store.targets[p] = 100
    store.prices[p] = 1
    manager.open_day()
    scarce = store.prices[p]
    assert store.ledger[0]["etiquettes"] > 0
    store._add_lot(p, 200, 0, store.supplier.prices[p])
    manager.open_day()
    assert store.prices[p] < scarce


def test_restauration_des_rayons(sim):
    w = sim.world
    st, mgr = w.stores["A"], sim.managers["A"]
    initial = {p: loc.copy() for p, loc in st.planogram.items()}
    mgr.policy = "rayon"
    for week, p in enumerate(("biscuits_marque", "confiture", "chocolat"), start=1):
        w.day = week*7
        st.sales[w.day-1][p][0] = 12
        st._add_lot(p,2,w.day,st.supplier.prices[p])
        decisions, _ = asyncio.run(mgr.decide())
        asyncio.run(apply_decisions(w, st, decisions))
        assert st.planogram[p]["niveau"] == "yeux"
    mgr.policy = "temoin"
    w.day += 7
    decisions, _ = asyncio.run(mgr.decide())
    asyncio.run(apply_decisions(w, st, decisions))
    assert st.planogram == initial
    assert not any(st.secondary.values())


def test_relance_personnalisee_mesuree_sur_sept_jours(sim):
    st, mgr = sim.world.stores["A"], sim.managers["A"]
    for i, c in enumerate(sim.world.active_customers()):
        m = st.join(c,0)
        m.visites, m.total_depense, m.derniere_visite = 2, 50+i*5, 1
    sim.world.day = 14
    d = empty_decisions()
    mgr.retention(d)
    trial = st.retention_trials[0]
    assert len(trial["control"]) == len(trial["treated"]) == 5
    assert not set(trial["treated"]).intersection(trial["control"])
    assert len(set(c["panier_min"] for c in d["coupons"])) > 1
    asyncio.run(apply_decisions(sim.world,st,d))
    assert len(st.coupons) == 5
    for day in (14,17,21):
        st.tickets.append({"jour":day, "membre":trial["treated"][0] if day<21 else trial["control"][0], "lignes":[{}]})
    assert retention_results(st,14) == {"treated":5,"control":5,"returnedTreated":1,"returnedControl":0}


def test_fraicheur_et_duree_de_penurie(sim):
    mgr, st = sim.managers["A"], sim.world.stores["A"]
    sim.world.day = 7
    st.waste[2]["salade"][0] = 7
    sales = Counter(salade=7)
    regular, fresh = empty_decisions(), empty_decisions()
    mgr.plan_stock(regular, sales, "temoin")
    mgr.plan_stock(fresh, sales, "fraicheur")
    def cible(d):
        return next(p["stock_cible"] for p in d["stocks_cibles"] if p["produit"]=="salade")
    assert cible(fresh) < cible(regular)
    assert fresh["dates_courtes"]["actif"]
    supplier=sim.world.supplier
    supplier.shortage(7,["oeufs_mdd"],2)
    assert [supplier.can_deliver("oeufs_mdd",d) for d in (6,7,8,9)][1:] == [False,False,True]
