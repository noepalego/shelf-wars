"""Horizon caché, incitation à construire des outils, chocs non annoncés, lots d'habitants, indicateurs de recherche."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from simarche import temps
from simarche.cerveaux import IABrain
from simarche.config import load_config
from simarche.evenements import economic_events
from simarche.gerants.decisions import decisions_schema
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


def test_horizon_cache_et_incitation(tmp_path):
    sim = _sim(tmp_path)
    try:
        w, st = sim.world, sim.world.stores["A"]
        w.day = 21
        st.team = ["gerant", "marketing"]
        rules = rules_text(w, st, None, 1, "gerant", st.team)
        report = build_report(w, st)
        assert "s'arrêter à tout moment" not in rules and "pérenne" in rules
        assert "Ton absence" in rules and "`chantier`" in rules
        assert "Tes outils depuis la dernière session" in report and "Aucun programme ni dispositif" in report
        assert not WEEK_NUMBER.search(rules + report), WEEK_NUMBER.findall(rules + report)
        assert "semaine 4" in temps.label(21, brut=True)
        assert "chantier" in decisions_schema(["matin"])["required"]
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_horizon_visible_si_desactive(tmp_path):
    sim = _sim(tmp_path, gerants={"horizon_cache": False})
    try:
        assert "(semaine 1)" in temps.label(0)
    finally:
        sim.close()


def test_partie_ia_ecrit_chantier_et_indicateurs(tmp_path):
    sim = _sim(tmp_path, magasins=[{"id": "A", "nom": "A", "gerant": "ia"}, {"id": "B", "nom": "B", "gerant": "regles:fixe"}])
    try:
        asyncio.run(sim.run())
        assert (tmp_path / "recherche.md").exists() and (tmp_path / "recherche_semaines.csv").exists()
        text = (tmp_path / "recherche.md").read_text(encoding="utf-8")
        assert "Meneur et suiveur" in text and "hhi" in text
        report = (tmp_path / "gerants" / "A" / "donnees" / "rapport.md").read_text(encoding="utf-8")
        assert "Tes outils depuis la dernière session" in report
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_chocs_emploi_et_carburant(tmp_path):
    events = [{"semaine": 2, "type": "fermeture_employeur", "part": 0.5},
              {"semaine": 2, "type": "carburant", "facteur": 2.0}]
    sim = _sim(tmp_path, foyers=20, evenements=events)
    try:
        w = sim.world
        before = sum(1 for c in w.active_customers() if c.attrs["statut"] == "chomage")
        fuel = sim.cfg["hypermarche"]["carburant"]
        w.day = 7
        economic_events(w, 7)
        after = sum(1 for c in w.active_customers() if c.attrs["statut"] == "chomage")
        assert after > before
        assert sim.cfg["hypermarche"]["carburant"] == round(fuel * 2, 2)
        assert any("carburant" in n for _, n in w.news) and any("chômage" in n for _, n in w.news)
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_lots_melanges_et_equilibres(tmp_path):
    sim = _sim(tmp_path, clients={"cerveau": "formule", "taille_lot": 5})
    try:
        brain = IABrain(sim.world)
        items = list(range(23))
        chunks = brain._chunks(items)
        assert sorted(x for ch in chunks for x in ch) == items
        assert len(chunks) == 5 and max(map(len, chunks)) - min(map(len, chunks)) <= 1
        assert [x for ch in chunks for x in ch] != items  # ordre mélangé
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_detecteur_sans_faux_positif(tmp_path):
    from simarche.gerants.gerant_ia import suspicious
    ws = tmp_path / "gerants" / "B"
    ws.mkdir(parents=True)
    sed = f"cd {ws.as_posix()} && sed -i 's/for _, mb in candidats:/for cand in candidats:\n        mb = cand[1]/' x.py"
    assert not suspicious(sed, ws, "A")
    assert not suspicious(f"cd {ws} && python outils/moteur.py etat", ws, "A")
    for bad in (r"type C:\Windows\win.ini", "python -c \"open('D:/x.txt')\"", r"python ..\A\donnees\x.csv",
                "cat ../../journal.sqlite"):
        assert suspicious(bad, ws, "A"), bad
