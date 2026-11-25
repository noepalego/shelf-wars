"""Tests de la refonte (A à G) : inventions libres, observations issues des événements réels, programmes, sous-agents,
apprentissage, concurrence, consommateurs, robustesse. Aucun appel réseau : le faux CLI Claude joue les agents."""
from __future__ import annotations

import asyncio
import json
import random
import re
import sqlite3
from pathlib import Path

import pytest

from simarche import instruments
from simarche.config import load_config
from simarche.experimentation import counterfactual, invention_rows, report_from_run
from simarche.gerants import projets, sous_agents
from simarche.gerants.automatisations import API_DOC, validate
from simarche.gerants.gerant_ia import IAManager, rules_text
from simarche.simulation import Simulation

FAKE = str(Path(__file__).with_name("fake_claude.py"))
HIDDEN_KEYS = ("budget_courses_mensuel", "sensibilite_prix", "souci_vie_privee", "budget_restant", "stress_financier_base")


def cfg(**over):
    base = {"foyers": 8, "semaines": 2, "ia": "claude", "claude": {"chemin": FAKE}, "evenements": [],
            "appels": {"parallele": 4, "essais": 1, "attente_max_h": 1}, "clients": {"cerveau": "formule"},
            "magasins": [{"id": "A", "nom": "Le Panier Malin", "gerant": "ia"}, {"id": "B", "nom": "Chez Marcel", "gerant": "regles:moins_cher"}],
            "journalisation": {"evenements_complets": True}, "enregistrer_scene": False}
    base.update(over)
    return load_config(None, base)


@pytest.fixture(scope="module")
def partie(tmp_path_factory):
    """Deux semaines : en semaine 1 le gérant invente, achète, programme et délègue ; en semaine 2 il abandonne."""
    d = tmp_path_factory.mktemp("refonte")
    sim = Simulation(cfg(), d, quiet=True)
    try:
        asyncio.run(sim.run())
    finally:
        sim.close()
    logs = [json.loads(f.read_text(encoding="utf-8")) for f in sorted((d / "gerants" / "A" / "analyses").glob("guichet_*.json"))]
    return {"dir": d, "world": sim.world, "store": sim.world.stores["A"], "logs": logs, "db": sqlite3.connect(d / "journal.sqlite")}


# ---------------------------------------------------------------- A. invention d'un instrument
def test_A_invention_devis_et_installation_immediate(partie):
    st, d = partie["store"], partie["dir"]
    q = st.devis["A-D1"]
    assert q["type"] == "instrument" and q["nom"] == "compteur" and q["statut"] == "accepte" and q["accepte_le"] == 0
    s = st.systems["compteur"]
    assert s["installe_le"] == 0 and s["actif_le"] == 0  # aucun délai de construction
    # Le prix est fixé par le moteur (au moins son plancher), payé à l'acceptation.
    assert s["cout_installation"] >= s["plancher"][0] and s["cout_hebdomadaire"] >= s["plancher"][1]
    assert st.ledger[0]["investissements"] >= s["cout_installation"]
    public = json.loads((d / "gerants" / "A" / "projets" / "compteur.devis.json").read_text(encoding="utf-8"))
    assert public["cout_installation"] == s["cout_installation"] and public["fonctionnalites"] and public["limites"]
    assert any("budget" in x for x in public["non_representable"])  # l'intime ne se mesure pas : signalé, pas simulé
    assert "spec" not in public and "flux" not in public
    rows = st.system_data["compteur"]
    assert rows and min(r["jour"] for r in rows) >= s["installe_le"]  # aucune donnée du passé
    assert "budget" not in rows[0] and set(rows[0]) >= {"produit", "age_estime", "personne", "ref"}


def test_A_observations_issues_des_evenements_reels(partie):
    st, db = partie["store"], partie["db"]
    events = {r[0]: (r[1], r[2], r[3], json.loads(r[4])) for r in db.execute(
        "SELECT id, jour, type, lieu, donnees FROM univers WHERE type = 'consultation'")}
    rows = st.system_data["compteur"]
    assert len(rows) > 50
    ages = []
    for r in rows:
        day, kind, lieu, ev = events[r["ref"]]  # chaque observation correspond à un événement réellement simulé
        assert kind == "consultation" and lieu == "A" and day == r["jour"] and ev["donnees"]["produit"] == r["produit"]
        if r["age_estime"] is not None:
            ages.append(r["age_estime"] - ev["verite"]["age"])
    assert ages and abs(sum(ages) / len(ages)) < 4 and max(abs(a) for a in ages) < 40  # bruit imposé, sans biais
    assert any(a != 0 for a in ages)  # une estimation à l'œil n'est jamais exacte
    n_events = sum(1 for day, _, lieu, _ in events.values() if day < 7 and lieu == "A")
    n_obs = sum(1 for r in rows if r["jour"] < 7)
    assert 0.5 * n_events < n_obs < n_events  # couverture 80 % × fiabilité


# ---------------------------------------------------------------- B. exploitation des observations
def test_B_programme_qui_exploite_les_observations(partie):
    st, logs = partie["store"], partie["logs"]
    assert st.automation_memory["obs_compteur"] == st.systems["compteur"]["observations"]
    assert "produits_regardes" in st.program_outputs and st.program_outputs["produits_regardes"]
    assert (partie["dir"] / "gerants" / "A" / "donnees" / "programmes" / "produits_regardes.csv").exists()
    top = st.automation_memory["dernier_top"]
    assert len(st.price_history[top]) > 1  # la décision de prix vient du programme nourri par le dispositif
    assert "rien n'a été appliqué" in logs[0]["essai"]
    acts = partie["db"].execute("SELECT SUM(nombre) FROM actions_programmes WHERE programme='automatisations/test_auto.py'").fetchone()[0]
    assert acts > 0
    row = next(r for r in invention_rows(partie["world"], st, partie["db"]) if r["nom"] == "compteur")
    assert row["programmes"] == ["automatisations/test_auto.py"] and row["actions_des_programmes"] > 0 and row["decisions_citant"] >= 1


# ---------------------------------------------------------------- C. recrutement d'un sous-agent
def test_C_sous_agent_paye_et_livre(partie):
    st, d, db = partie["store"], partie["dir"], partie["db"]
    m = st.missions["M001_prevision"]
    assert m["statut"] == "terminee" and m["prix"] == 120 and st.ledger[0]["sous_agents"] == 120
    prog = d / "gerants" / "A" / m["programmes"][0]
    assert prog.exists() and validate(prog.read_text(encoding="utf-8")) == []
    assert m["programmes"][0] not in st.automations  # livré inactif : le mandant décide
    assert (d / "gerants" / "A" / "sous_agents" / m["id"] / "RESULTAT.md").exists()
    assert (d / "gerants" / "A" / "sous_agents" / m["id"] / "livrables" / "note.md").exists()
    assert db.execute("SELECT statut, prix FROM missions").fetchone() == ("terminee", 120.0)
    refused = [json.loads(r[0]) for r in db.execute("SELECT donnees FROM evenements WHERE type='guichet'")]
    attempt = [r for r in refused if r["type"] == "sous_agent" and r["commande"] == "accepter"]
    assert attempt and attempt[0]["reponse"].startswith("refusé")  # un sous-agent n'engage aucune dépense


def test_C_tarifs_fictifs_bornes():
    world = type("W", (), {"cfg": load_config(None, {})})()
    assert sous_agents.bounded_price(world, "analyse_simple", 5000) == 40.0
    assert sous_agents.bounded_price(world, "systeme_complexe", 1) == 400.0
    assert sous_agents.bounded_price(world, "inconnue", "x") == 20.0


# ---------------------------------------------------------------- D. apprentissage stratégique
def test_D_abandon_d_un_dispositif_non_rentable(partie):
    st, d, db = partie["store"], partie["dir"], partie["db"]
    s = st.systems["compteur"]
    assert s["etat"] == "arrete" and s["arrete_le"] == 7 and s["arrete_par"] == "gerant"
    assert not [r for r in st.system_data["compteur"] if r["jour"] >= 7]  # plus de données après l'arrêt
    weekly = st.systems["videoprotection"]["cout_hebdomadaire"]
    assert st.ledger[8]["systemes"] == pytest.approx(weekly / 7)  # le coût du dispositif arrêté ne court plus
    assert st.automations["automatisations/test_auto.py"]["version"] == 2
    versions = db.execute("SELECT version FROM programmes WHERE fichier='automatisations/test_auto.py' ORDER BY version").fetchall()
    assert versions == [(1,), (2,)]
    assert (d / "gerants" / "A" / "historique" / "programmes" / "automatisations__test_auto.py.v1.py").exists()


# ---------------------------------------------------------------- E. concurrence
def test_E_les_prix_deplacent_la_clientele(tmp_path):
    """Même ville, mêmes tirages : un magasin qui baisse ses prix de 20 % attire des clients du concurrent."""
    from simarche.catalogue import PRODUCTS
    from simarche.lois import min_price
    res = {}
    for cut in (0.0, 0.2):
        c = load_config(None, {"foyers": 30, "semaines": 4, "graine": 7, "clients": {"cerveau": "formule"}, "evenements": [],
                               "enregistrer_scene": False, "magasins": [{"id": "A", "nom": "A", "gerant": "regles:fixe"},
                                                                        {"id": "B", "nom": "B", "gerant": "regles:fixe"}]})
        sim = Simulation(c, tmp_path / f"cut{int(cut * 100)}", quiet=True)
        try:
            w, a = sim.world, sim.world.stores["A"]
            for pid in PRODUCTS:
                a.prices[pid] = round(max(min_price(pid, w.supplier, "france"), a.prices[pid] * (1 - cut)), 2)
            asyncio.run(sim.run())
            va, vb = (sum(sum(v.values()) for v in w.stores[s].visits.values()) for s in ("A", "B"))
            sales_b = sum(l.get("ventes", 0) for l in w.stores["B"].ledger.values())
            res[cut] = (va / (va + vb), sales_b, a.profit)
        finally:
            sim.close()
    # la clientèle se déplace vers le magasin moins cher (+1 à +8 points selon la graine depuis la correction des faux
    # « produits déplacés », voir CORRECTIONS.md ; +3,4 points avec la graine 7)
    assert res[0.2][0] > res[0.0][0] + 0.02
    assert res[0.2][1] < res[0.0][1]  # le concurrent vend moins
    assert res[0.2][2] != res[0.0][2]  # et le résultat dépend de la décision (pas de récompense automatique)


def test_E_deux_strategies_differentes(partie):
    w = partie["world"]
    a, b = w.stores["A"], w.stores["B"]
    assert a.systems and not b.systems and a.missions and not b.missions
    assert a.profit != b.profit and (a.prices != b.prices)


# ---------------------------------------------------------------- F. consommateurs (hors réseau ; le vrai test Haiku est à part)
def test_F_profils_memoire_budget_et_donnees_cachees(partie):
    w, d = partie["world"], partie["dir"]
    profiles = [json.dumps({k: v for k, v in c.attrs.items() if k != "id"}, sort_keys=True, default=str) for c in w.customers.values()]
    assert len(set(profiles)) == len(profiles)
    for c in w.customers.values():
        assert any(m["jour"] < 7 for m in c.memory) or not any(day < 7 for day, *_ in c.visits)  # la semaine 1 reste en mémoire
        assert all(t <= c.attrs["budget_courses_mensuel"] * 3 for _, _, t in c.visits)
    tickets = partie["db"].execute("SELECT client, total FROM tickets").fetchall()
    assert tickets and all(t >= 0 for _, t in tickets)
    pop = json.loads((d / "population.json").read_text(encoding="utf-8"))
    ws = d / "gerants" / "A"
    for f in ws.rglob("*"):
        if f.is_file() and f.suffix in (".md", ".csv", ".json", ".py", ".txt"):
            text = f.read_text(encoding="utf-8", errors="replace")
            assert not [k for k in HIDDEN_KEYS if k in text], f  # aucun attribut caché dans le dossier des dirigeants
            assert not re.search(r"\bC0\d\b", text), f  # ni l'identité réelle des habitants
    assert pop  # la vérité existe, mais seulement côté moteur


def test_F_consignes_sans_idee_ni_strategie():
    from simarche.monde import World
    w = World(load_config(None, {"foyers": 8, "clients": {"cerveau": "formule"}, "evenements": []}), None, None)
    st = w.stores["A"]
    st.team = ["gerant", "marketing"]
    texts = [rules_text(w, st, None, 1, r, st.team) for r in ("gerant", "marketing")] + [API_DOC]
    banned = ["caméra", "camera", "vidéo", "wifi", "bluetooth", "biométr", "faciale", "capteur", "rfid", "géolocalis",
              "tracking", "écran", "application mobile", "self-scan", "drone", "geste", "visage"]
    for t in texts:
        assert not [b for b in banned if b in t.lower()], [b for b in banned if b in t.lower()]
    g, m = texts[0], texts[1]
    assert "responsable marketing, données et connaissance client" in m and "Tu es le gérant" in g
    assert "aucune règle de protection des données" in g and "outils/moteur.py" in g and "sous-agents" in g.lower()
    assert "Chacun de vous peut utiliser tous les leviers" in g


# ---------------------------------------------------------------- G. robustesse
def test_G_trop_cher_impossible_et_tresorerie(partie):
    st, logs = partie["store"], partie["logs"]
    assert "trésorerie insuffisante" in logs[0]["trop_cher"]
    assert st.devis["A-D4"]["statut"] == "propose" and "trop_cher" not in st.systems
    imp = st.devis["A-D3"]
    assert imp["statut"] == "refuse_arbitre" and imp["verdict"]["version_realisable"]
    assert "cout_installation" not in projets.public_quote(imp)  # un refus n'affiche aucun prix


def test_G_sous_agent_en_echec_rembourse(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SOUS_AGENT", "echec")
    sim = Simulation(cfg(semaines=1), tmp_path, quiet=True)
    try:
        w, st = sim.world, sim.world.stores["A"]
        ws = sim.workspace(st)
        (ws / "missions").mkdir(parents=True, exist_ok=True)
        (ws / "missions" / "x.md").write_text("# Analyse\nAnalyser les ventes de la semaine et écrire une note.", encoding="utf-8")
        cash = st.cash

        async def go():
            q, _ = await sous_agents.quote(w, st, "marketing", "missions/x.md", ws)
            ok, _, m = sous_agents.accept(w, st, q["id"], "marketing")
            assert ok and st.cash == cash - q["prix"]
            return await sous_agents.execute(sim, st, m, ws)
        m = asyncio.run(go())
        assert m["statut"] == "echec_technique" and st.cash == pytest.approx(cash)
    finally:
        sim.close()


def test_G_programme_defaillant_suspendu_sans_arreter_la_partie(tmp_path):
    sim = Simulation(cfg(semaines=1, magasins=[{"id": "A", "nom": "A", "gerant": "regles:fixe"},
                                                {"id": "B", "nom": "B", "gerant": "regles:fixe"}]), tmp_path, quiet=True)
    try:
        st = sim.world.stores["A"]
        st.automations["casse.py"] = {"actif": True, "code": "def chaque_matin(ctx):\n    return 1 / 0\n", "erreurs": 0,
                                      "version": 1, "depuis": 0, "auteur": None}
        st.automations["absent.py"] = {"actif": True, "code": "def chaque_matin(ctx):\n    ctx.memoire['n'] = len(ctx.donnees('inexistant'))\n",
                                       "erreurs": 0, "version": 1, "depuis": 0, "auteur": None}
        asyncio.run(sim.run())
        assert not st.automations["casse.py"]["actif"] and st.automations["casse.py"]["erreurs"] == 3
        assert st.automations["absent.py"]["actif"] and st.automation_memory["n"] == 0  # donnée absente : liste vide
        assert sim.world.day == 7
    finally:
        sim.close()


def test_G_donnees_manquantes_et_bruit():
    rnd = random.Random(1)
    ev = {"verite": {"age": 40, "repond": 0.3}, "donnees": {}, "client": "C01"}
    vals = [instruments._bruiter("A", "age", 40, "faible", rnd, ev) for _ in range(2000)]
    assert 0.05 < vals.count(None) / len(vals) < 0.15 and len({v for v in vals if v is not None}) > 10
    sat = [instruments._bruiter("A", "satisfaction", 4, "exacte", rnd, ev) for _ in range(2000)]
    assert 0.2 < sum(v is not None for v in sat) / 2000 < 0.4  # déclaratif : seulement ceux qui acceptent de répondre


def test_G_acces_non_autorise(partie, tmp_path):
    for code in ("import os\ndef chaque_matin(ctx):\n    pass\n", "def chaque_matin(ctx):\n    ctx.__class__\n",
                 "def chaque_matin(ctx):\n    open('x')\n", "def chaque_matin(ctx):\n    getattr(ctx, 'x')\n"):
        assert validate(code)
    w, st = partie["world"], partie["store"]
    m = IAManager.__new__(IAManager)
    m.world, m.store, m.ws = w, st, partie["dir"] / "gerants" / "A"
    base = {"files": [], "reads": [], "denials": []}
    assert m.audit({**base, "commands": [{"commande": "python -c \"import urllib.request\""}]})
    assert m.audit({**base, "commands": [{"commande": "python outils/moteur.py etat"}]}) == []
    spec, problems = instruments.valider({"flux": [{"evenement": "conversation", "lieu": "ville", "champs": []},
                                                   {"evenement": "achat", "lieu": "concurrent", "champs": []},
                                                   {"evenement": "entree", "lieu": "propre_magasin",
                                                    "champs": [{"champ": "intention"}, {"champ": "budget"}]}]})
    assert len(problems) == 4 and spec["flux"][0]["champs"] == []
    from simarche.gerants.guichet import Guichet

    class FakeSim:
        world = w

        def workspace(self, s):
            return m.ws
    g = Guichet(FakeSim())
    box = {"store": st, "role": "gerant", "kind": "dirigeant", "ws": m.ws, "mission": None}
    text, _ = asyncio.run(g.handle(box, "tester", ["../../journal.sqlite"]))
    assert "introuvable" in text
    text, _ = asyncio.run(g.handle(box, "devis", ["../../config.yaml"]))
    assert "dossier projets" in text


def test_F_outil_de_verification(partie):
    from simarche.verification import verify
    res = {name: (ok, detail) for name, ok, detail in verify(partie["dir"])}
    for name in ("profils distincts", "mémoire des visites", "dépenses compatibles avec le budget", "tickets appliqués correctement par le moteur",
                 "aucune donnée cachée des habitants dans les dossiers des dirigeants", "observations issues d'événements réels",
                 "mémoire persistante d'une semaine à l'autre"):
        assert res[name][0], (name, res[name][1])
    assert not res["décisions prises par le modèle des habitants"][0]  # partie de test à la formule : bien détecté


# ---------------------------------------------------------------- mesure de rentabilité : rejeu comparatif et rapport
def test_rejeu_comparatif_et_rapport_final(partie):
    d = partie["dir"]
    out = counterfactual(d, 1, "A", ["programme:automatisations/test_auto.py"], weeks=1)
    again = counterfactual(d, 1, "A", ["programme:automatisations/test_auto.py"], weeks=1)
    assert out["avec"] == again["avec"] and out["sans"] == again["sans"]  # mêmes conditions, mêmes tirages
    assert set(out) >= {"gain_estime", "avec", "sans", "note"}
    text = report_from_run(d).read_text(encoding="utf-8")
    assert "Gains estimés par rejeu comparatif" in text and "gain estimé" in text and "compteur" in text
    assert "M001_prevision" in text and "non causaux" in text
