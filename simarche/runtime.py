"""Interpréteur Python des agents, utilisable dans la sandbox de Codex.

Sous Windows, le `python.exe` d'un environnement virtuel n'est qu'un lanceur : il exécute l'interpréteur d'origine
(`…\\AppData\\Local\\Programs\\Python\\Python3xx\\python.exe`). La sandbox de Codex interdit la lecture de ce dossier :
chaque `python …` lancé par un agent échouait avec « did not find executable at … : Access is denied ».

On construit donc, une fois pour toutes, une copie autonome de l'interpréteur dans le projet (`.runtime/python/`,
bibliothèque standard comprise) qui voit les paquets du venv (pandas, numpy…) par un fichier `.pth`. Le dossier du
projet est lisible depuis la sandbox ; l'interpréteur ne dépend plus de rien d'autre.

`preflight()` vérifie avant le lancement d'une partie, dans la vraie sandbox de Codex et sans appeler de modèle, que
`python`, pandas et le guichet du moteur fonctionnent : une partie ne démarre plus avec des outils cassés.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".runtime" / "python"
# Inutile aux agents et volumineux : non copié.
SKIP_LIB = {"site-packages", "test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "__pycache__"}
STAMP = "simarche_runtime.txt"


def _base() -> Path:
    return Path(getattr(sys, "_base_executable", sys.executable)).resolve().parent if os.name == "nt" else Path(sys.base_prefix)


def _venv_site_packages() -> list[str]:
    import site
    return [p for p in site.getsitepackages() if Path(p).name == "site-packages" and Path(p).exists()]


def executable() -> Path:
    return RUNTIME / ("python.exe" if os.name == "nt" else "bin/python3")


def ensure(force: bool = False) -> Path | None:
    """Construit (si besoin) l'interpréteur autonome ; renvoie son dossier, ou None hors Windows (inutile)."""
    if os.name != "nt":
        return None
    base = Path(sys.base_prefix)
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} {base}"
    stamp = RUNTIME / STAMP
    if not force and stamp.exists() and stamp.read_text(encoding="utf-8").strip() == version and executable().exists():
        _write_pth()
        return RUNTIME
    tmp = RUNTIME.with_name("python_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for f in base.iterdir():
        if f.is_file() and f.suffix.lower() in (".exe", ".dll"):
            shutil.copy2(f, tmp / f.name)
    if (base / "DLLs").exists():
        shutil.copytree(base / "DLLs", tmp / "DLLs")
    lib = tmp / "Lib"
    lib.mkdir()
    for item in (base / "Lib").iterdir():
        if item.name in SKIP_LIB:
            continue
        if item.is_dir():
            shutil.copytree(item, lib / item.name, ignore=shutil.ignore_patterns("__pycache__", "test", "tests"))
        else:
            shutil.copy2(item, lib / item.name)
    (lib / "site-packages").mkdir()
    shutil.rmtree(RUNTIME, ignore_errors=True)
    tmp.rename(RUNTIME)
    _write_pth()
    stamp.write_text(version, encoding="utf-8")
    return RUNTIME


def _write_pth() -> None:
    """Les paquets du venv (pandas…) sans ses fichiers .pth : le code du moteur n'est jamais importable."""
    sp = RUNTIME / "Lib" / "site-packages"
    sp.mkdir(parents=True, exist_ok=True)
    (sp / "simarche_venv.pth").write_text("\n".join(_venv_site_packages()) + "\n", encoding="utf-8")


def agent_env(env: dict | None = None) -> dict:
    """Environnement des sessions d'agents : notre interpréteur en tête du PATH, rien qui renvoie au venv."""
    env = dict(os.environ if env is None else env)
    for k in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"):
        env.pop(k, None)
    folder = ensure()
    if folder is None:
        return env
    venv_scripts = str(Path(sys.executable).parent).lower()
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p and p.rstrip("\\/").lower() != venv_scripts]
    env["PATH"] = os.pathsep.join([str(folder), str(folder / "Scripts")] + parts)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def allowed_prefixes() -> list[str]:
    """Emplacements hors du dossier de l'agent qu'il peut toucher sans être en faute (outils, système)."""
    out = [str(RUNTIME), str(Path(sys.prefix)), str(Path(sys.executable).parent), str(Path(sys.executable).resolve().parent),
           str(_base())]
    return [p.replace("/", "\\").lower().rstrip("\\") for p in out if p]


def system_prefixes() -> list[str]:
    """Dossiers du système et des programmes installés : on peut y lancer un exécutable (le shell, node…) sans faute ;
    y lire un fichier reste un accès hors du dossier."""
    out = [os.environ.get(v, "") for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles")]
    out += [r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)"]
    return sorted({p.replace("/", "\\").lower().rstrip("\\") for p in out if p}, key=len, reverse=True)


PREFLIGHT_SCRIPT = r"""@echo off
python -c "import sys, pandas; print('python', sys.version.split()[0], 'pandas', pandas.__version__)"
if errorlevel 1 exit /b 11
python outils\moteur.py aide > nul
if errorlevel 1 exit /b 12
python -c "open('analyses/_ecriture.txt','w').write('ok')"
if errorlevel 1 exit /b 13
echo PRET
"""


def preflight(codex_exe: str, timeout: float = 120) -> tuple[bool, str]:
    """Lance dans la sandbox de Codex (mêmes réglages que les sessions de gérant) un petit script : python, pandas,
    guichet, écriture. Aucun modèle n'est appelé. Renvoie (ok, message)."""
    if os.name != "nt":
        return True, "vérification inutile hors Windows"
    from .gerants.guichet import install_client
    work = Path(tempfile.mkdtemp(prefix="simarche_preflight_", dir=str(ROOT / ".runtime")))
    try:
        install_client(work)
        (work / "analyses").mkdir(exist_ok=True)
        (work / "verif.bat").write_text(PREFLIGHT_SCRIPT, encoding="utf-8")
        cmd = [codex_exe, "sandbox", "-P", ":workspace", "-C", str(work), "-c", 'windows.sandbox="elevated"',
               str(work / "verif.bat")]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=agent_env(), cwd=str(work),
                               encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"sandbox de Codex inutilisable : {e}"
        out = (p.stdout + p.stderr).strip()
        ok = p.returncode == 0 and "PRET" in out
        return ok, out[-600:]
    finally:
        shutil.rmtree(work, ignore_errors=True)
