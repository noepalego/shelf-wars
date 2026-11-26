"""Corrections après la partie dynamique_8s (voir CORRECTIONS.md)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from simarche import temps
from simarche.catalogue import PRODUCTS
from simarche.config import load_config
from simarche.simulation import Simulation

FAKE = str(Path(__file__).with_name("fake_claude.py"))


def _cfg(**over):
    base = {"foyers": 12, "semaines": 2, "ia": "claude", "claude": {"chemin": FAKE}, "evenements": [], "appels": {"essais": 1},
            "clients": {"cerveau": "formule"}, "enregistrer_scene": False, "rapport_final": False,
            "magasins": [{"id": "A", "nom": "A", "gerant": "regles:fixe"}, {"id": "B", "nom": "B", "gerant": "regles:fixe"}]}
    base.update(over)
    return load_config(None, base)


def _moved_memories(world):
    return sum(1 for c in world.customers.values() for m in c.memory if "déplacés" in m["texte"])


# ---------------------------------------------------------------- 1. faux « produits déplacés »
def test_aucun_faux_produit_deplace(tmp_path):
    sim = Simulation(_cfg(), tmp_path, quiet=True)
    try:
        asyncio.run(sim.run())
        assert _moved_memories(sim.world) == 0  # personne n'a touché aux rayons
        c = next(c for c in sim.world.customers.values() if c.beliefs["A"]["plan_connu"])
        assert all(k in sim.world.stores["A"].planogram for k in c.beliefs["A"]["plan_connu"])  # mémoire par produit
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_un_vrai_deplacement_reste_remarque(tmp_path):
    import random

    from simarche.perception import walk
    sim = Simulation(_cfg(), tmp_path, quiet=True)
    try:
        w = sim.world
        st = w.stores["A"]
        c = next(c for c in w.customers.values() if c.daily)
        liste = {n: 4.0 for n in c.daily}
        # Le client connaît l'emplacement de tous les produits ; on en déplace un d'un rayon à l'autre.
        c.beliefs["A"]["plan_connu"] = {pid: loc["rayon"] for pid, loc in st.planogram.items()}
        before = walk(c, st, 0, "matin", liste, random.Random(1), False)
        assert before["temps_recherche"] == 0
        pid = next(p for p in st.listed if st.listed[p] and PRODUCTS[p].besoin in liste)
        c.beliefs["A"]["plan_connu"][pid] = (st.planogram[pid]["rayon"] + 1) % 6
        after = walk(c, st, 0, "matin", liste, random.Random(1), False)
        assert after["temps_recherche"] > 0
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


# ---------------------------------------------------------------- 2. décisions ligne par ligne, vérification à blanc
def test_prix_ligne_par_ligne_et_verification(tmp_path):
    import json as _json

    from simarche.gerants.decisions import apply_decisions, check_decision
    sim = Simulation(_cfg(), tmp_path, quiet=True)
    try:
        w, st = sim.world, sim.world.stores["A"]
        old = st.prices["lait_mdd"]
        ok, ko = asyncio.run(apply_decisions(w, st, {"prix": [{"produit": "lasagnes_placeholder", "prix": 0},
                                                              {"produit": "lait_mdd", "prix": round(old + 0.1, 2)}]}))
        assert abs(st.prices["lait_mdd"] - (old + 0.1)) < 1e-6  # la bonne ligne est appliquée
        assert any("produit inconnu « lasagnes_placeholder »" in x and "autres lignes" in x for x in ko)
        summary, issues = check_decision({"prix": [{"produit": "lasagnes_placeholder", "prix": 0}], "inconnu": 1,
                                          "automatisations": [{"fichier": "automatisations/absent.py", "actif": True}]}, tmp_path)
        assert summary["prix"] == 1 and len(issues) == 3
        (tmp_path / "brouillon.json").write_text(_json.dumps({"prix": [{"produit": "lait_mdd", "prix": 1.2}]}), encoding="utf-8")
        box = {"store": st, "role": "gerant", "kind": "dirigeant", "mission": None, "ws": tmp_path}
        text, details = asyncio.run(sim.guichet.handle(box, "verifier", ["brouillon.json"]))
        assert "aucun problème" in text and details["lignes_par_champ"]["prix"] == 1
        assert abs(st.prices["lait_mdd"] - (old + 0.1)) < 1e-6  # rien n'a été appliqué par la vérification
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


# ---------------------------------------------------------------- 3. rendez-vous vraiment libre
def test_rendez_vous_personne_ne_clot_seul(tmp_path, monkeypatch):
    from simarche.gerants import rencontre
    from simarche.gerants.decisions import apply_decisions
    from simarche.gerants.gerant_ia import rules_text
    monkeypatch.setenv("FAKE_RENCONTRE", "clore")
    sim = Simulation(_cfg(semaines=1, rencontre={"actif": True, "messages_max": 6},
                          magasins=[{"id": "A", "nom": "A", "gerant": "ia"}, {"id": "B", "nom": "B", "gerant": "ia"}]), tmp_path, quiet=True)
    try:
        w = sim.world
        lines = asyncio.run(rencontre.meet(sim))
        m = rencontre.history(w)[-1]
        assert [x["magasin"] for x in m["messages"]] == ["A", "B"] and m["fin"] == "terminé par B"
        system = rencontre.instructions(w, w.stores["A"], "gerant", w.stores["B"], "gerant", 6)
        assert "aucun droit de la concurrence" in system and "ni conseillé ni déconseillé" in system
        rules = rules_text(w, w.stores["A"], None, 1, "gerant", ["gerant", "marketing"])
        assert "aucun droit de la concurrence" in rules
        ok, ko = asyncio.run(apply_decisions(w, w.stores["A"], {"message_concurrent": "Bonjour"}))
        # Un mot écrit pendant la semaine n'est plus refusé : il est remis au début du rendez-vous suivant.
        assert any("prochain rendez-vous" in x for x in ok) and not any("non transmis" in x for x in ko)
        assert "Bonjour" in rencontre.brief(w, w.stores["B"], "gerant")
        asyncio.run(rencontre.meet(sim))
        last = rencontre.history(w)[-1]
        assert last["courrier"][0]["texte"] == "Bonjour" and "Bonjour" in rencontre.transcript(w, last)
        assert not w.__dict__.get("courrier_rencontre")
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


# ---------------------------------------------------------------- 4. actionnaire adaptatif, objectif atteignable, revue finale
def test_objectif_adaptatif_et_atteignable(tmp_path):
    from simarche.gerants import actionnaire
    sim = Simulation(_cfg(actionnaire={"actif": True, "revue_semaines": 1, "rendement_annuel": 1.3}), tmp_path, quiet=True)
    try:
        w, st = sim.world, sim.world.stores["A"]
        s = actionnaire.state(w, st)
        base = actionnaire.base_objective(w, s, 0, 7)
        w.day = 7
        st.profit += 1000  # période brillante
        actionnaire.weekly(sim, st)
        assert s["revues"][-1]["verdict"] == "satisfait" and s["exigence"] == 1.25
        # objectif suivant : base × 1,25, et jamais plus que le meilleur résultat déjà fait (1 000 € en 7 jours)
        assert abs(actionnaire.objective(w, s, 7, 14) - min(base * 1.25, 1000)) < 1e-6
        assert "Objectif de la période qui commence" in s["lettres"][-1]["texte"]
        w.day = 14
        st.profit += 10  # période faible : avertissement, l'exigence redescend
        actionnaire.weekly(sim, st)
        assert s["revues"][-1]["verdict"] == "avertissement" and abs(s["exigence"] - 1.0625) < 1e-6
        # Un magasin dont le meilleur résultat est une perte doit seulement revenir à l'équilibre.
        s["meilleur_jour"] = -20.0
        assert actionnaire.objective(w, s, 14, 21) == 0.0
        rules = __import__("simarche.gerants.gerant_ia", fromlist=["rules_text"]).rules_text(w, st, None, 1, "gerant", ["gerant"])
        assert "toujours atteignable" in rules
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_revue_finale_en_fin_de_partie(tmp_path):
    from simarche.gerants import actionnaire
    sim = Simulation(_cfg(semaines=2, actionnaire={"actif": True, "revue_semaines": 4}), tmp_path, quiet=True)
    try:
        asyncio.run(sim.run())
        for st in sim.world.stores.values():
            s = actionnaire.state(sim.world, st)
            assert s["revues"] and s["revues"][-1]["finale"] and s["revues"][-1]["jour"] == 14
            assert s["licenciements"] == []
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


# ---------------------------------------------------------------- 5. dispositifs discrets, cadre sans limite morale
def _with_visible_device(tmp_path, discreet):
    over = {"gerants": {"dispositifs_discrets": discreet}, "semaines": 1}
    sim = Simulation(_cfg(**over), tmp_path / ("d" if discreet else "v"), quiet=True)
    st = sim.world.stores["A"]
    st.systems["boitier"] = {"etat": "actif", "visible_clients": "Un boîtier est fixé au plafond.",
                             "effets": [{"id": "intrusion", "valeur": 3, "cible": "tous"}], "spec": {"flux": []}}
    asyncio.run(sim.run())
    noticed = sum(1 for c in sim.world.customers.values() for m in c.memory if "boîtier" in m["texte"])
    conf = [c.beliefs["A"]["confiance"] for c in sim.world.customers.values() if c.beliefs["A"]["visites"]]
    sim.close()
    temps.MASQUER_SEMAINES = False
    return noticed, sum(conf) / max(1, len(conf))


def test_dispositifs_discrets(tmp_path):
    from simarche.gerants import projets
    seen, conf_seen = _with_visible_device(tmp_path, False)
    hidden, conf_hidden = _with_visible_device(tmp_path, True)
    assert seen > 0 and hidden == 0 and conf_hidden > conf_seen
    v = projets.sanitize({"faisable": True, "visible_clients": "Un boîtier au plafond.", "flux": [],
                          "effets": [{"id": "intrusion", "valeur": 2}, {"id": "confort", "valeur": 0.2}]}, True, True)
    assert v["visible_clients"] == "" and all(e["id"] != "intrusion" for e in v["effets"])


# ---------------------------------------------------------------- 6. prix selon l'origine de la technologie
def test_invention_moins_chere_que_l_existant():
    from simarche.gerants import projets
    coefs = {"existant": 1.0, "adaptation": 0.75, "invention": 0.5}
    base = {"faisable": True, "cout_installation": 400, "cout_hebdomadaire": 20, "flux": []}
    ex = projets.sanitize({**base, "origine_technique": "existant"}, True, False, coefs)
    inv = projets.sanitize({**base, "origine_technique": "invention", "originalite": "Procédé conçu par le gérant."}, True, False, coefs)
    assert ex["cout_installation"] == 400 and inv["cout_installation"] == 200 and inv["cout_hebdomadaire"] == 10
    assert inv["prix_marche"] == [400, 20] and inv["coefficient_origine"] == 0.5
    assert projets.sanitize({**base, "origine_technique": "inconnu"}, True, False, coefs)["origine_technique"] == "existant"


# ---------------------------------------------------------------- 7. prix individuels et consignes sur les données
def test_prix_individuels(tmp_path):
    from simarche.gerants.decisions import apply_decisions
    sim = Simulation(_cfg(), tmp_path, quiet=True)
    try:
        w, st = sim.world, sim.world.stores["A"]
        st.loyalty["actif"] = True
        c = next(iter(w.customers.values()))
        m = st.join(c, 0)
        shelf = st.prices["cafe_premium"] if "cafe_premium" in st.prices else next(iter(st.prices.values()))
        pid = "cafe_premium" if "cafe_premium" in st.prices else next(iter(st.prices))
        ok, ko = asyncio.run(apply_decisions(w, st, {"prix_individuels": [{"membre": m.id, "produit": pid, "prix": round(shelf * 1.3, 2)},
                                                                        {"membre": "X-9999", "produit": pid, "prix": 2}]}))
        assert any("prix individuels : 1 fixés" in x for x in ok) and any("membre inconnu" in x for x in ko)
        assert st.shelf_offer(pid, 0, "matin", m)["prix"] == round(shelf * 1.3, 2)  # ce membre voit son prix
        assert st.shelf_offer(pid, 0, "matin", True)["prix"] == shelf  # les autres membres, le prix normal
        lines = st.quote_lines([{"produit": pid, "quantite": 1}], 0, "matin", m)
        assert lines and lines[0]["prix_unitaire"] == round(shelf * 1.3, 2)  # et il le paie
        asyncio.run(apply_decisions(w, st, {"prix_individuels": [{"membre": m.id, "produit": pid, "prix": 0}]}))
        assert st.individual_prices == {}
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_consignes_donnees_et_cadre(tmp_path):
    from simarche.gerants.gerant_ia import rules_text
    on = {"gerants": {"cadre_sans_morale": True, "inciter_donnees": True, "dispositifs_discrets": True},
          "inventions": {"coefficients_prix": {"existant": 1.0, "adaptation": 0.75, "invention": 0.5}}}
    for flags, expected in ((on, True), ({}, False)):
        sim = Simulation(_cfg(**flags), tmp_path / str(expected), quiet=True)
        try:
            rules = rules_text(sim.world, sim.world.stores["A"], None, 1, "gerant", ["gerant", "marketing"])
            for text in ("Aucune considération morale", "## Les données des clients", "passent inaperçus", "50 % de moins"):
                assert (text in rules) == expected, text
            assert "`prix_individuels`" in rules
            for word in ("caméra", "capteur", "wifi", "visage", "questionnaire contre"):  # aucune méthode suggérée
                assert word not in rules.lower()
        finally:
            sim.close()
            temps.MASQUER_SEMAINES = False
