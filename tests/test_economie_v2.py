"""Régressions aux interfaces du moteur économique et des dirigeants."""
import asyncio
from random import Random

import pytest

from simarche import temps
from simarche.catalogue import PRODUCTS
from simarche.config import load_config
from simarche.gerants.decisions import apply_decisions
from simarche.gerants.rapport import week_score
from simarche.simulation import Simulation


@pytest.fixture
def sim(tmp_path):
    cfg = load_config(overrides={"semaines": 2, "clients": {"cerveau": "formule"},
                                 "economie": {"duree_amortissement_jours": 180},
                                 "magasins": [{"id": sid, "nom": f"Commerce {sid}", "gerant": "regles:fixe"}
                                              for sid in "AB"]})
    simulation = Simulation(cfg, tmp_path, quiet=True)
    yield simulation
    simulation.close()


def test_retours_id_et_campagne(sim):
    w = sim.world
    c = next(iter(w.customers.values()))
    c.beliefs["A"].update(connait=True, visites=1)
    c.pending_surveys["A"] = {"id": "A-Q1", "jour": 0, "questions": ["Quel prix ?"]}
    response = {"magasin": "Commerce A", "invitation": "A-Q2", "reponses": ["Abordable."], "lier_carte": False}
    sim.apply_reflection({"client": c}, {"reponses_enquete": [response]})
    assert not w.stores["A"].survey_answers
    response["invitation"] = "A-Q1"
    sim.apply_reflection({"client": c}, {"reponses_enquete": [response],
                                         "avis": {"publier": True, "magasin": "Commerce A", "note": 5, "texte": "Bien."}})
    assert w.stores["A"].survey_answers[0]["reponses"] == ["Abordable."]
    assert w.reviews[0]["magasin"] == "A"
    assert "A" not in c.pending_surveys


def test_paie_et_caisse_apres_remises(sim):
    w = sim.world
    c = next(iter(w.customers.values()))
    c.attrs["jour_paie"] = 30
    february_end = next(d for d in range(60) if (temps.date_of(d).month, temps.date_of(d).day) == (2, 28))
    c.state["budget_restant"] = 0
    assert c.days_to_payday(february_end - 1) == 1
    c.on_new_day(february_end, Random(1))
    assert c.state["budget_restant"] == c.attrs["budget_courses_mensuel"]
    st = w.stores["A"]
    pid = "lait_mdd"
    st.prices[pid] = 2
    cp = st.new_coupon(c.id, "remise_euros_panier", 1, None, 2, 0, 7, "carte", "")
    c.receive_coupon(cp)
    items = [{"produit": pid, "quantite": 1}]
    first = st.checkout(c, items, 0, "matin", Random(1), budget=0.5)
    assert first["total"] == 0 and not cp.utilise
    second = st.checkout(c, items, 0, "matin", Random(1), budget=1)
    assert second["total"] == 1 and cp.utilise


def test_coupon_teste_sans_promotion_et_plafond_de_depense(sim):
    w = sim.world
    st = w.stores["A"]
    for c in list(w.customers.values())[:4]:
        st.join(c, 0)
    coupon = {"offre": "remise_euros_panier", "valeur": 2, "panier_min": 0, "texte": "2 € offerts",
              "distribution": "tous_les_membres", "canal": "carte", "budget": 2, "temoin_pct": 50,
              "declencheur_produit": "lait_mdd", "quantite_min": 2}
    cash = st.cash
    _, invalid = asyncio.run(apply_decisions(w, st, {"coupons": [{**coupon, "texte": "3 € offerts"}]}))
    assert invalid and st.cash == cash and not st.coupons
    ok, ko = asyncio.run(apply_decisions(w, st, {"coupons": [coupon]}))
    assert ok and not ko and not st.promotions
    st.prices["lait_mdd"] = 2
    st._add_lot("lait_mdd", 8, 0, 1)
    trial = next(iter(st.coupon_trials.values()))
    treated = [w.customers[m.client] for m in st.members.values() if trial["membres"][m.id] == "traite"]
    assert len(treated) == 2
    subtotals = [st.checkout(c, [{"produit": "lait_mdd", "quantite": 2}], 0, "matin", Random(1))["total"] for c in treated]
    assert subtotals == [2, 4]
    assert trial["depense"] == 2
    assert {row["foyers_assignes"] for row in st.campaign_results()} == {2}


def test_horaires_grossiste_et_vue_du_rival(sim):
    w = sim.world
    a, b = (w.stores[sid] for sid in "AB")
    w.freeze_competition()
    try:
        b.slogan = "Nouveau"
        w.deliver_competitor_message("A", "Nouveau")
        asyncio.run(apply_decisions(w, b, {"horaires": [{"jours": "dimanche", "creneau": "soir", "ouvert": True}]}))
        assert w.public_information("B")["slogan"] != "Nouveau"
        assert "soir" not in w.public_information("B")["horaires"][6]
        assert not a.inbox_from_competitor
    finally:
        w.publish_competition()
    assert w.public_information("B")["slogan"] == "Nouveau"
    assert b.is_open(6, "soir") and not a.is_open(6, "soir")
    assert a.inbox_from_competitor == ["Nouveau"]
    pid = "pommes"
    assert PRODUCTS[pid].frais
    w.supplier.shortage(0, [pid], 5)
    assert not b.order(pid, 4, 0, w.supplier)[0]
    assert b.order(pid, 1, 0, w.supplier, "local")[0]
    assert b.on_order[-1][0] == 1


def test_amortissement_et_prix_percu(sim):
    w = sim.world
    a = w.stores["A"]
    opening_cash = a.cash
    a.invest(0, "investissements", 180)
    a.daily_costs(0)
    assert a.cash == opening_cash - 180 - w.cfg["economie"]["charges_fixes_jour"]
    assert a.net_assets() == 179
    assert week_score(a, 0)["resultat"] == pytest.approx(a.profit)
    c = next(iter(w.customers.values()))
    w.cfg["option_exterieure"] = False
    c.state["budget_restant"] = 100
    c.state["garde_manger"]["lait"] = 0
    for pid, p in PRODUCTS.items():
        if p.besoin == "lait":
            c.beliefs["A"]["prix_ref"][pid] = p.prix_conseille * 1.8
    ctx = sim.place_context(c, "matin", "grosse")
    opts = {opt["lieu"]: opt for opt in ctx["options"]}
    assert opts["A"]["panier_estime"] > opts["B"]["panier_estime"]
