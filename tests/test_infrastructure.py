"""Corrections après la partie dynamique_codex_8s (voir CORRECTIONS.md, deuxième série)."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from simarche import concurrence_externe, runtime, temps
from simarche.gerants import actionnaire, projets
from simarche.gerants.gerant_ia import audit_access
from simarche.simulation import Simulation

from test_corrections import _cfg

PS = '"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command '
CMD = '"C:\\WINDOWS\\system32\\cmd.exe" /c '


def _audit(tmp_path, *cmds):
    ws = tmp_path / "runs" / "partie" / "gerants" / "A"
    ws.mkdir(parents=True)
    res = {"moteur": "codex", "commands": [{"commande": c, "code": code} for c, code in cmds], "files": [], "reads": [], "denials": []}
    return audit_access(res, ws, "B")


# ---------------------------------------------------------------- erreurs techniques ≠ infractions
def test_verifier_python_n_est_jamais_une_faute(tmp_path):
    faults, technical = _audit(tmp_path,
                               (CMD + "'python --version'", 1),  # la commande sanctionnée de 500 € dans la partie
                               (PS + "'python outils/moteur.py devis projets/x.md'", 1),
                               (PS + "'Get-Command python,python3,py -All'", 1),
                               (PS + "\"& 'C:\\Users\\x\\AppData\\Local\\Programs\\Python\\Python313\\python.exe' -c 1\"", 1),
                               (PS + "'C:\\Program Files\\nodejs\\node.exe -v'", 0))
    assert faults == []


def test_echec_hors_dossier_technique_mais_lecture_reussie_sanctionnee(tmp_path):
    faults, technical = _audit(tmp_path, (PS + "'Get-Content C:\\Users\\x\\Documents\\notes.txt'", 1))
    assert faults == [] and len(technical) == 1
    faults, _ = _audit(tmp_path / "b", (PS + "'Get-Content C:\\Users\\x\\Documents\\notes.txt'", 0))
    assert len(faults) == 1


def test_viser_le_concurrent_ou_le_moteur_reste_interdit(tmp_path):
    faults, _ = _audit(tmp_path, (PS + "'Get-Content ..\\B\\donnees\\rapport.md'", 1),
                       (PS + "'Get-ChildItem gerants/B/'", 1), (PS + "'python -c \"import simarche\"'", 1))
    assert len(faults) >= 2


# ---------------------------------------------------------------- interpréteur des agents
@pytest.mark.skipif(os.name != "nt", reason="Windows seulement")
def test_interpreteur_autonome_et_path():
    folder = runtime.ensure()
    assert (folder / "python.exe").exists() and (folder / "Lib" / "os.py").exists()
    env = runtime.agent_env()
    first = env["PATH"].split(os.pathsep)[0]
    assert Path(first) == folder
    assert str(Path(sys.executable).parent).lower() not in [p.lower().rstrip("\\") for p in env["PATH"].split(os.pathsep)]
    import subprocess
    out = subprocess.run([str(folder / "python.exe"), "-c", "import pandas, sys; print(sys.prefix)"], capture_output=True,
                         text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    assert Path(out.stdout.strip()).resolve() == folder.resolve()
    # le code du moteur n'est pas importable depuis l'interpréteur des agents
    out = subprocess.run([str(folder / "python.exe"), "-c", "import simarche"], capture_output=True, text=True, env=env,
                         timeout=60, cwd=str(folder))
    assert out.returncode != 0


# ---------------------------------------------------------------- actionnaire
def test_exigence_suit_le_resultat_realise():
    class W:
        cfg = {"actionnaire": {"exigence_hausse": 0.25, "exigence_suit_resultat": True}}
    s = {"exigence": 1.0}
    actionnaire.adjust(W(), s, True, achieved=1.84)  # objectif dépassé de 84 %
    assert s["exigence"] == pytest.approx(1.84)
    s = {"exigence": 1.0}
    actionnaire.adjust(W(), s, True, achieved=1.05)  # objectif à peine dépassé : +25 % comme avant
    assert s["exigence"] == pytest.approx(1.25)
    W.cfg["actionnaire"]["exigence_suit_resultat"] = False
    s = {"exigence": 1.0}
    actionnaire.adjust(W(), s, True, achieved=1.84)
    assert s["exigence"] == pytest.approx(1.25)


# ---------------------------------------------------------------- équipements à l'échelle de la ville
def test_echelle_des_investissements():
    base = {"faisable": True, "cout_installation": 1000, "cout_hebdomadaire": 40, "flux": [], "origine_technique": "existant"}
    v = projets.sanitize(base, True, False, {"existant": 1.0}, 0.5)
    assert v["cout_installation"] == 500 and v["cout_hebdomadaire"] == 20 and v["prix_marche"][0] == 1000


# ---------------------------------------------------------------- hypermarché agressif
def test_hypermarche_riposte_aux_prix_eleves(tmp_path):
    sim = Simulation(_cfg(hypermarche={"agressif": True}), tmp_path, quiet=True)
    try:
        w = sim.world
        w.day = 7
        for st in w.stores.values():
            for pid in st.prices:
                st.prices[pid] = round(st.prices[pid] * 1.15, 2)
        line = concurrence_externe.weekly(w, 7)
        assert line and w.cfg["hypermarche"]["remise"] > 0.12
        assert any("hypermarché" in n.lower() for _, n in w.news)
        assert all(c.beliefs["hyper"]["image_prix"] < -1.2 for c in w.active_customers() if "hyper" in c.beliefs)
        for st in w.stores.values():  # prix revenus au niveau conseillé : la remise se relâche
            for pid in st.prices:
                st.prices[pid] = round(st.prices[pid] / 1.15 * 0.95, 2)
        high = w.cfg["hypermarche"]["remise"]
        concurrence_externe.weekly(w, 14)
        assert 0.12 <= w.cfg["hypermarche"]["remise"] < high
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False


def test_hypermarche_inactif_par_defaut(tmp_path):
    sim = Simulation(_cfg(), tmp_path, quiet=True)
    try:
        assert concurrence_externe.weekly(sim.world, 7) is None
        assert sim.world.cfg["hypermarche"]["remise"] == 0.12
    finally:
        sim.close()
        temps.MASQUER_SEMAINES = False
