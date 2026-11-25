"""Tests avec les vrais modèles (abonnement Claude) : ignorés par défaut.

    $env:SIMARCHE_RESEAU = "1"; .\\.venv\\Scripts\\python.exe -m pytest tests/test_reseau.py -s

1. Arbitre Sonnet → devis → installation immédiate → observations réelles → sous-agent Sonnet → programme actif.
2. Une semaine complète : 30 habitants Claude Haiku, deux équipes de direction Sonnet, puis `verifier`.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from simarche.config import load_config
from simarche.gerants import projets, sous_agents
from simarche.gerants.decisions import apply_decisions
from simarche.gerants.rapport import export_workspace
from simarche.llm import Passerelle
from simarche.simulation import Simulation
from simarche.verification import verify

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.environ.get("SIMARCHE_RESEAU") != "1", reason="appels réels désactivés (SIMARCHE_RESEAU=1)")

INVENTION = """# Rayon qui écoute les mains
1. Dispositif : des barres sur le bord des étagères des rayons 2 et 3 qui détectent quand une main prend un article puis
   le repose, et un boîtier qui estime l'âge des personnes devant le rayon.
2. Objectif : repérer les produits pris puis reposés pour corriger leur prix.
3. Principe : poids et proximité sur chaque tablette, horodatage.
4. Informations : produit pris, produit reposé, quantité, âge apparent, et ce que la personne pensait du prix.
5. Ressources : barres sur deux rayons, boîtier de calcul, lien avec la caisse.
6. Traitement : comptage par produit et tranche d'âge chaque soir.
7. Usage : un programme ajuste les prix des produits souvent reposés.
"""

MISSION = """# Ajustement des prix à partir des articles reposés
Écris un programme (format de automatisations/LISEZMOI.md) qui, chaque soir, compte par produit les articles pris puis
reposés observés par le dispositif « rayon_qui_ecoute » (ctx.donnees), publie ce tableau avec ctx.publier, et baisse de
3 % le prix d'un produit reposé au moins 3 fois dans la journée, sans descendre sous son seuil de revente à perte.
Teste-le avec python outils/moteur.py tester. Livre une courte note.
"""


def test_arbitre_et_sous_agent_reels(tmp_path):
    cfg = load_config(ROOT / "configs" / "laboratoire.yaml", {"semaines": 1, "clients": {"cerveau": "formule"},
                                                             "appels": {"en_cas_de_limite": "arreter"}, "enregistrer_scene": False,
                                                             "journalisation": {"evenements_complets": True}, "rapport_final": False})
    cfg["magasins"] = [dict(m, gerant="regles:fixe") for m in cfg["magasins"]]
    sim = Simulation(cfg, tmp_path, quiet=True)
    sim.ia = sim.world.ia = Passerelle(cfg, tmp_path, sim.journal)
    try:
        w, st = sim.world, sim.world.stores["A"]
        ws = sim.workspace(st)
        export_workspace(w, st, ws, None)
        (ws / "projets").mkdir(exist_ok=True)
        (ws / "projets" / "rayon_qui_ecoute.md").write_text(INVENTION, encoding="utf-8")
        q, msg = asyncio.run(projets.quote(w, st, "gerant", "projets/rayon_qui_ecoute.md", ws))
        assert q and q["statut"] == "propose", msg
        assert q["verdict"]["spec"]["flux"] and q["verdict"]["capacites_non_representables"]  # « ce que pensait la personne »
        cash = st.cash
        ok, _ = projets.accept(w, st, q["id"], "gerant", ws)
        assert ok and st.cash == pytest.approx(cash - q["verdict"]["cout_installation"])
        asyncio.run(sim.run(max_days=2))
        assert st.system_data.get("rayon_qui_ecoute")
        export_workspace(w, st, ws, None)
        (ws / "missions").mkdir(exist_ok=True)
        (ws / "missions" / "reposes.md").write_text(MISSION, encoding="utf-8")

        async def mission():
            mq, _ = await sous_agents.quote(w, st, "marketing", "missions/reposes.md", ws)
            good, _, m = sous_agents.accept(w, st, mq["id"], "marketing")
            assert good
            return await sous_agents.execute(sim, st, m, ws)
        m = asyncio.run(mission())
        assert m["statut"] in ("terminee", "partielle") and m["programmes"], m
        prog = m["programmes"][0].split(" (")[0]
        ok, ko = asyncio.run(apply_decisions(w, st, {"automatisations": [{"fichier": prog, "actif": True}]}, ws, role="marketing"))
        assert ok and not ko, ko
        asyncio.run(sim.run(max_days=3))
        assert st.automations[prog]["actif"] and st.automations[prog]["erreurs"] == 0
    finally:
        sim.close()


def test_semaine_complete_30_habitants_haiku(tmp_path):
    cfg = load_config(ROOT / "configs" / "laboratoire.yaml", {"semaines": 1, "appels": {"en_cas_de_limite": "arreter"},
                                                             "journalisation": {"evenements_complets": True}, "enregistrer_scene": False})
    assert cfg["foyers"] == 30 and cfg["clients"]["cerveau"] == "ia" and "haiku" in cfg["modeles"]["claude"]["client"]["modele"]
    sim = Simulation(cfg, tmp_path, quiet=True)
    try:
        asyncio.run(sim.run())
    finally:
        sim.close()
    res = verify(tmp_path)
    failed = [(n, d) for n, ok, d in res if not ok]
    assert not failed, failed
