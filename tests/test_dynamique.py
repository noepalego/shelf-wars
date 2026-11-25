"""Actionnaire algorithmique (revues, avertissements, licenciement, apports) et rendez-vous entre dirigeants rivaux."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from simarche import temps
from simarche.config import load_config
from simarche.gerants import actionnaire, rencontre
from simarche.gerants.gerant_ia import rules_text
from simarche.gerants.rapport import build_report
from simarche.simulation import Simulation

FAKE = str(Path(__file__).with_name("fake_claude.py"))
WEEK_NUMBER = re.compile(r"\bS\d+\b|semaine \d+|Semaine \d+")


def _sim(tmp_path, **over):
    cfg = load_config(None, {"foyers": 8, "semaines": 2, "ia": "claude", "claude": {"chemin": FAKE}, "evenements": [],
                             "appels": {"essais": 1}, "clients": {"cerveau": "formule"}, "enregistrer_scene": False,
                             "rapport_final": False, **over})
    return Simulation(cfg, tmp_path, quiet=True)


def test_revues_avertissements_puis_licenciement(tmp_path):
    sim = _sim(tmp_path, actionnaire={"actif": True, "revue_semaines": 1, "avertissements_max": 2})
    try:
        w, st = sim.world, sim.world.stores["A"]
        st.team = ["gerant", "marketing"]
        w.manager_journals["A:gerant"] = [(3, "Mes notes secrètes.")]
        verdicts = []
        for week in (1, 2, 3):
            w.day = 7 * week
            st.profit -= 100  # chaque semaine perd 100 € : objectif manqué, pas de croissance
            actionnaire.weekly(sim, st)
            verdicts.append(actionnaire.state(w, st)["revues"][-1]["verdict"])
        assert verdicts == ["avertissement", "avertissement", "licenciement"]
        s = actionnaire.state(w, st)
        assert s["avertissements"] == 0 and s["nominations"]["gerant"] == 21
        assert w.manager_journals["A:gerant"] == []
        archive = list((tmp_path / "gerants" / "A" / "historique" / "predecesseurs").glob("journal_gerant_*.md"))
        assert archive and "Mes notes secrètes." in archive[0].read_text(encoding="utf-8")
        rules = rules_text(w, st, None, 1, "gerant", st.team)
        assert "Ton actionnaire" in rules and "après le licenciement de ton prédécesseur" in rules
        assert "DERNIER" not in actionnaire.warning_text(w, s)
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_objectif_atteint_et_apports(tmp_path):
    sim = _sim(tmp_path, actionnaire={"actif": True, "revue_semaines": 1, "plafond_apports": 1000, "plafond_par_confiance": 500})
    try:
        w, st = sim.world, sim.world.stores["A"]
        cash = st.cash
        assert actionnaire.request_capital(w, st, "gerant", ["50", "test"]).startswith("refusé")
        assert actionnaire.request_capital(w, st, "gerant", ["400"]).startswith("refusé")  # motif obligatoire
        msg = actionnaire.request_capital(w, st, "gerant", ["400", "agrandir", "le", "rayon", "frais"])
        assert msg.startswith("accordé") and abs(st.cash - cash - 400) < 1e-6 and st.profit == 0
        assert actionnaire.request_capital(w, st, "gerant", ["700", "encore"]).startswith("refusé")  # plafond 1000
        w.day = 7
        st.profit += 500
        actionnaire.weekly(sim, st)
        s = actionnaire.state(w, st)
        assert s["revues"][-1]["verdict"] == "satisfait" and s["confiance"] == 1
        assert abs(actionnaire.ceiling(w, s) - 1100) < 1e-6
        s["avertissements"] = 2
        assert "dernier avertissement" in actionnaire.request_capital(w, st, "gerant", ["200", "x"])
        report = build_report(w, st)
        assert "## L'actionnaire" in report
        assert not WEEK_NUMBER.search(report.split("## L'actionnaire")[1][:600])
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_guichet_apport(tmp_path):
    sim = _sim(tmp_path, actionnaire={"actif": True})
    try:
        st = sim.world.stores["A"]
        box = {"store": st, "role": "gerant", "kind": "dirigeant", "mission": None, "ws": tmp_path}
        text, _ = asyncio.run(sim.guichet.handle(box, "apport", ["1000", "nouveau", "rayon"]))
        assert text.startswith("accordé")
        text, _ = asyncio.run(sim.guichet.handle(dict(box, kind="sous_agent"), "apport", ["1000", "x"]))
        assert text.startswith("refusé")
        _, details = asyncio.run(sim.guichet.handle(box, "etat", []))
        assert details["actionnaire"]["capitaux_engages"] == 4000
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_partie_avec_rendez_vous_et_actionnaire(tmp_path):
    sim = _sim(tmp_path, semaines=2, actionnaire={"actif": True, "revue_semaines": 1},
               rencontre={"actif": True, "messages_max": 4})
    try:
        asyncio.run(sim.run())
        w = sim.world
        assert len(rencontre.history(w)) == 2
        m = rencontre.history(w)[-1]
        assert len(m["messages"]) == 4 and {x["magasin"] for x in m["messages"]} == {"A", "B"}
        assert m["messages"][0]["magasin"] == "B"  # l'ordre de parole alterne d'une semaine à l'autre
        for sid in ("A", "B"):
            ws = tmp_path / "gerants" / sid
            text = (ws / "rencontres.md").read_text(encoding="utf-8")
            assert "Comment se passe votre semaine" in text and not WEEK_NUMBER.search(text)
            assert "Rien d'important." not in text  # la note d'équipe reste privée
            assert "Rien d'important." in (ws / "equipe.md").read_text(encoding="utf-8")
            assert "Le rendez-vous avec le concurrent" in (ws / "CONSIGNES_gerant.md").read_text(encoding="utf-8")
            assert (ws / "donnees" / "actionnaire.md").exists()
        rows = w.journal.db.execute("select donnees from evenements where type='rencontre'").fetchall()
        assert len(rows) == 2 and json.loads(rows[0][0])["messages"]
        calls = w.journal.db.execute("select count(*) from appels_llm where role like 'rencontre_%'").fetchone()[0]
        assert calls == 8
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_desactive_par_defaut(tmp_path):
    sim = _sim(tmp_path)
    try:
        w, st = sim.world, sim.world.stores["A"]
        assert not actionnaire.active(w) and not rencontre.active(w)
        rules = rules_text(w, st, None, 1, "gerant", ["gerant", "marketing"])
        assert "Ton actionnaire" not in rules and "rendez-vous" not in rules.lower()
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False
