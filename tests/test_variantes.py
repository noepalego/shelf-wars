"""Foyers variantes : plus de clients, aucun appel supplémentaire au modèle."""
from __future__ import annotations

import asyncio
from pathlib import Path

from simarche import personnel, temps, variantes
from simarche.clients import id_number
from simarche.config import load_config
from simarche.simulation import Simulation

FAKE = str(Path(__file__).with_name("fake_claude.py"))


def _cfg(k, **over):
    return load_config(None, {"foyers": 8, "semaines": 1, "ia": "claude", "claude": {"chemin": FAKE}, "evenements": [],
                              "appels": {"essais": 1}, "clients": {"cerveau": "formule", "variantes_par_foyer": k},
                              "enregistrer_scene": False, "rapport_final": False,
                              "magasins": [{"id": "A", "nom": "A", "gerant": "regles:equilibre"},
                                           {"id": "B", "nom": "B", "gerant": "regles:equilibre"}], **over})


def test_sources_identiques_et_variantes_crees(tmp_path):
    s0 = Simulation(_cfg(0), tmp_path / "k0", quiet=True)
    s2 = Simulation(_cfg(2), tmp_path / "k2", quiet=True)
    try:
        assert len(s0.world.customers) == 8 and len(s2.world.customers) == 24
        for cid, c in s0.world.customers.items():
            assert s2.world.customers[cid].attrs == c.attrs  # les foyers-sources ne changent pas
        v = s2.world.customers["C01b"]
        src = s2.world.customers["C01"]
        assert v.attrs["source"] == "C01" and v.attrs["quartier"] == src.attrs["quartier"]
        assert v.attrs["type_foyer"] == src.attrs["type_foyer"] and v.attrs["proches"] == []
        assert s2.world.variantes["C01"] == ["C01b", "C01c"]
        assert personnel.base_shelving(s2.cfg) == 3 * personnel.base_shelving(s0.cfg)
        assert id_number("C07") == 7 and id_number("C07b") != id_number("C07c")
    finally:
        s0.close()
        s2.close()
        temps.MASQUER_SEMAINES = False


def test_partie_avec_variantes(tmp_path):
    sim = Simulation(_cfg(2, semaines=2), tmp_path, quiet=True)
    try:
        asyncio.run(sim.run())
        db = sim.world.journal.db
        brains = dict(db.execute("select cerveau, count(*) from choix group by cerveau").fetchall())
        assert brains.get("variante", 0) > 0 and brains.get("formule", 0) > 0
        # Les variantes achètent vraiment : des tickets à leur nom existent.
        buyers = {t["client"] for st in sim.world.stores.values() for t in st.tickets if t.get("client")}
        if buyers:
            assert any(variantes.is_variant(sim.world.customers[c]) for c in buyers if c in sim.world.customers)
        visited = sum(1 for c in sim.world.customers.values() if variantes.is_variant(c) and c.visits)
        assert visited > 0
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_aucun_appel_de_plus_au_modele(tmp_path):
    counts = {}
    for k in (0, 2):
        sim = Simulation(_cfg(k, clients={"cerveau": "ia", "taille_lot": 2, "variantes_par_foyer": k}), tmp_path / f"k{k}", quiet=True)
        try:
            asyncio.run(sim.run(max_days=1))
            counts[k] = dict(sim.world.journal.db.execute(
                "select role, count(*) from appels_llm where role like 'client%' group by role").fetchall())
        finally:
            sim.close()
            temps.MASQUER_SEMAINES = False
    assert counts[2].get("client_lieu") == counts[0].get("client_lieu")
    assert sum(counts[2].values()) <= sum(counts[0].values()) + 1


def test_panier_repris_et_mis_a_l_echelle():
    import random

    class C:
        attrs = {"source": "C01"}

    from simarche.catalogue import PRODUCTS
    pid = next(p for p, x in PRODUCTS.items() if x.besoin == "lait")
    need = PRODUCTS[pid].besoin
    bctx = {"client": C(), "liste": {need: 8}, "articles": [], "enfants": False,
            "fidelite": {"propose": False, "donnees": [], "bonus": 0}}
    # Formule de base sans articles : on vérifie seulement la reprise du modèle de la source.
    bctx["articles"] = [{"produit": pid, "besoin": need, "cherche": True, "prix": 1.0, "prix_membre": None}]
    template = {"liste": {need: 4}, "achats": [{"produit": pid, "quantite": 2, "date_courte": False}]}
    from simarche.variantes import FORMULA
    FORMULA.choose_basket = lambda ctx, rnd: {"achats": [], "carte_fidelite": "sans_objet", "accepte_emails": False,
                                              "accepte_sms": False, "raison": ""}
    try:
        res = variantes.echo_basket(bctx, template, random.Random(1))
    finally:
        del FORMULA.choose_basket
    assert res["achats"] == [{"produit": pid, "quantite": 4, "date_courte": False}]  # 2 × (8 / 4)
