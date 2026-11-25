import random

import pytest

from simarche.cerveaux import FormulaBrain, projected_basket, projected_savings
from simarche.config import load_config
from simarche.monde import World
from simarche.perception import walk
from simarche.simulation import Simulation


@pytest.fixture
def scene():
    cfg = load_config(overrides={"foyers": 8})
    world = World(cfg)
    sim = Simulation.__new__(Simulation)
    sim.world, sim.cfg = world, cfg
    c = next(iter(world.customers.values()))
    c.attrs.update(regime="omnivore", allergies=[], produits_favoris=[], produits_detestes=[],
                   receptivite_coupons=1, sensibilite_prix=1)
    c.state["budget_restant"] = 40
    return sim, c, world.stores["A"]


def displays(store, *products):
    store.listed = {p: p in products for p in store.listed}


def frequency(customer, store, needs, product, samples=500):
    shown = 0
    filled = 0
    for seed in range(samples):
        visit = walk(customer, store, 0, "matin", needs, random.Random(seed), False)
        shown += any(it["produit"] == product for it in visit["articles"])
        filled += not visit["introuvables"]
    return shown / samples, filled / samples


def test_attention_sur_les_variantes_recherchees(scene):
    _, c, st = scene
    displays(st, "lait_mdd", "lait_marque", "lait_bio")
    c.attrs["aime_flaner"] = 0
    c.state.update(pression_jour=1, fatigue=1)
    st.planogram["lait_mdd"]["niveau"] = "bas"
    low, essential = frequency(c, st, {"lait": 2}, "lait_mdd")
    st.planogram["lait_mdd"]["niveau"] = "yeux"
    at_eye, _ = frequency(c, st, {"lait": 2}, "lait_mdd")
    c.attrs["aime_flaner"] = 1
    c.state.update(pression_jour=0, fatigue=0)
    unhurried, _ = frequency(c, st, {"lait": 2}, "lait_mdd")
    assert essential > 0.93
    assert at_eye > low + 0.12
    assert unhurried > at_eye + 0.06


def test_complement_visible_a_cote_des_pates(scene):
    _, c, st = scene
    displays(st, "pates_mdd", "sauce_tomate_mdd")
    sauce = st.planogram["sauce_tomate_mdd"]
    st.planogram["pates_mdd"].update(rayon=2, segment=0, niveau="yeux")
    sauce.update(rayon=2, segment=3, niveau="mains")
    far, _ = frequency(c, st, {"feculents": 4}, "sauce_tomate_mdd")
    sauce["segment"] = 0
    near, _ = frequency(c, st, {"feculents": 4}, "sauce_tomate_mdd")
    assert far + 0.10 < near < 0.85
    st.lots["sauce_tomate_mdd"].clear()
    assert frequency(c, st, {"feculents": 4}, "sauce_tomate_mdd", 20)[0] == 0


def test_coupon_valeur_seuil_validite_et_limite_de_deux(scene):
    sim, c, st = scene

    def give(offer, value, product=None, threshold=0, first=0, last=7):
        cp = st.new_coupon(c.id, offer, value, product, threshold, first, last, "carte", "")
        c.receive_coupon(cp)
        return cp

    threshold = give("remise_euros_panier", 4, threshold=8)
    product = give("remise_pct_produit", 50, "lait_mdd")
    irrelevant = give("produit_offert", 1, "papier_toilette")
    future = give("remise_euros_panier", 8, first=2)
    expired = give("remise_euros_panier", 8, first=-4, last=-1)
    spent = give("remise_euros_panier", 8)
    spent.utilise = True
    other = sim.world.stores["B"]
    foreign = other.new_coupon(c.id, "remise_euros_panier", 2, None, 0, 0, 7, "carte", "")
    c.receive_coupon(foreign)
    ctx = sim.place_context(c, "matin", "petite")
    offers = ctx["offres_coupons"]["A"]
    assert offers == [threshold, product, irrelevant]
    assert all(cp not in offers for cp in (future, expired, spent, foreign))
    prices = {"lait_mdd": 2}
    subtotal, lines = projected_basket(c, {"lait": 5}, 10, prices)
    assert projected_savings(offers, subtotal, lines, 0, c) == 9
    c.state["budget_restant"] = 4
    subtotal, lines = projected_basket(c, {"lait": 5}, 10, prices)
    assert subtotal == 4
    assert projected_savings(offers, subtotal, lines, 0, c) == 2
    assert projected_savings([irrelevant], subtotal, lines, 0, c) == 0


def test_coupon_change_le_panier_sans_avancer_la_remise(scene):
    sim, c, st = scene
    displays(st, "lait_mdd", "lait_marque")
    c.state["prix_juste"].update(lait_mdd=1, lait_marque=1)
    c.attrs.update(exigence_qualite=0, sensibilite_bio=0, sensibilite_local=0, attrait_mdd=0.4, gouts={})
    st.prices.update(lait_mdd=1, lait_marque=1.45)
    cp = st.new_coupon(c.id, "produit_offert", 1, "lait_marque", 0, 0, 7, "carte", "")
    c.receive_coupon(cp)
    place_ctx = sim.place_context(c, "matin", "petite")
    place_ctx.update(liste={"lait": 1}, budget=8)
    # Les deux variantes sont visibles afin d'isoler le choix du produit.
    articles = [{"produit": p, "besoin": "lait", "cherche": True, **st.shelf_offer(p, 0, "matin", False)}
                for p in ("lait_mdd", "lait_marque")]
    visit = {"articles": articles, "ruptures": [], "introuvables": []}
    ctx = sim.basket_context(c, st, visit, place_ctx, False)
    brain = FormulaBrain()
    control = dict(ctx, offres_coupons_ici=[])
    def first(which, seed):
        return brain.choose_basket(which, random.Random(seed))["achats"][0]["produit"]
    assert sum(first(ctx, i) == "lait_marque" for i in range(80)) > 65
    assert sum(first(control, i) == "lait_marque" for i in range(80)) < 15
    assert not cp.utilise and articles[1]["prix"] == 1.45
    basket = brain.choose_basket(ctx, random.Random(0))
    ticket = st.checkout(c, basket["achats"], 0, "matin", random.Random(0))
    assert ticket["remise_coupons"] == pytest.approx(1.45)
    assert ticket["total"] == 0


def test_un_coupon_inutilisable_ne_deplace_pas_les_visites(scene):
    sim, c, st = scene
    c.beliefs["B"] = c.beliefs["A"].copy()
    cp = st.new_coupon(c.id, "remise_euros_panier", 4, None, 99, 0, 7, "carte", "")
    c.receive_coupon(cp)
    context = sim.place_context(c, "matin", "petite")
    context.update(liste={"lait": 6}, budget=8, options=[
        {"lieu": sid, "minutes": 5, "cout": 0} for sid in ("A", "B")])
    brain = FormulaBrain()

    def visits(ctx):
        return sum(brain.choose_place(ctx, random.Random(i))["choix"] == "A" for i in range(100))

    control = visits(dict(context, offres_coupons={}))
    assert visits(context) == control
    cp.panier_min = 4
    assert visits(context) > control + 25
