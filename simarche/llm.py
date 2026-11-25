"""Passerelle vers les modèles : Claude Code (abonnement Claude) ou Codex (abonnement ChatGPT).

Chaque appel lance un CLI en mode non interactif, connecté à ton compte : aucune clé API n'est utilisée.
- Claude : `claude -p` (voir claude_code.py), modèles Claude Sonnet pour les gérants, Claude Haiku pour les habitants.
- Codex : `codex exec`, le CLI fourni avec l'application Codex.

Les appels « décision » n'ont aucun outil. Les sessions de gérant ont des outils limités à leur dossier.
La passerelle choisit le modèle selon le profil (gerant, client, moderation, analyse), met les réponses en
cache, compte l'usage et gère la limite de l'abonnement : pause, arrêt propre ou bascule vers le moteur de secours.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

# Fonctions de Codex inutiles pour une simple décision : on les coupe pour réduire le coût de chaque appel.
NO_TOOL_FEATURES = ["shell_tool", "unified_exec", "apps", "plugins", "multi_agent", "image_generation", "view_image",
                    "browser_use", "browser_use_external", "computer_use", "goals", "skill_search", "hooks",
                    "in_app_browser", "tool_suggest", "sleep_tool", "code_mode_host", "remote_plugin",
                    "workspace_dependencies", "skill_mcp_dependency_install", "plugin_discovery_prefetch",
                    "deferred_tool_world_state", "incremental_tools"]
NO_TOOL_CONFIG = ['include_apply_patch_tool=false', 'include_plan_tool=false', 'web_search="disabled"',
                  'include_permissions_instructions=false', 'include_environment_context=false',
                  'tools.view_image=false', 'include_apps_instructions=false', 'skills.enabled=false']
# Le gérant garde un terminal et l'édition de fichiers, rien d'autre.
MANAGER_FEATURES_OFF = ["apps", "plugins", "multi_agent", "image_generation", "browser_use", "browser_use_external",
                        "computer_use", "goals", "skill_search", "hooks", "in_app_browser", "tool_suggest",
                        "remote_plugin", "skill_mcp_dependency_install", "plugin_discovery_prefetch"]
MANAGER_CONFIG = ['web_search="disabled"', 'include_apps_instructions=false', 'skills.enabled=false']

LIMIT_RE = re.compile(r"usage limit|rate limit|quota|too many requests|\b429\b|limite d'utilisation|hit your limit", re.I)
WAIT_RE = re.compile(r"try again in\s+(?:(\d+)\s*(?:hours?|h))?\s*(?:(\d+)\s*(?:minutes?|min|m))?", re.I)
DEFAULT_WAIT_S = 15 * 60
CODEX_AUTH_RE = re.compile(r"not logged in|codex login|\b401\b|unauthori[sz]ed|authentication (?:failed|required)|"
                           r"no (?:valid )?credentials", re.I)


class IAError(RuntimeError):
    """Un appel au modèle a échoué (après les essais prévus) ou le moteur est inutilisable."""


CodexError = IAError  # ancien nom


class UsageLimitError(IAError):
    """La limite d'usage de l'abonnement est atteinte (et la configuration demande d'arrêter)."""


class IAFatale(IAError):
    """Le moteur ne peut pas répondre du tout (non connecté, CLI introuvable…) : la partie s'arrête."""


STOP_ERRORS = (UsageLimitError, IAFatale)  # erreurs qui arrêtent la partie au lieu d'un repli silencieux


def empty_result() -> dict:
    return {"ok": False, "data": None, "text": None, "usage": {}, "cost_usd": None, "commands": [], "files": [],
            "reads": [], "denials": [], "error": None, "duration": 0.0, "cached": False, "limit": False,
            "wait_s": None, "fatal": False}


def extract_json(text: str | None):
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def wait_seconds(message: str) -> float:
    """Durée d'attente annoncée par Codex (« try again in 2 hours 5 minutes »), 15 min sinon."""
    m = WAIT_RE.search(message or "")
    if m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + 60
    return DEFAULT_WAIT_S


def kill_tree(proc) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
        else:
            proc.kill()
    except Exception:
        pass


async def run_process(cmd: list[str], stdin: str, timeout: float, cwd: Path | None = None, env: dict | None = None):
    """Lance un processus, lui donne le texte en entrée et renvoie (code, stdout, stderr, durée, erreur)."""
    t0 = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, cwd=str(cwd) if cwd else None, env=env)
    except OSError as e:
        return None, "", "", 0.0, f"lancement impossible : {e}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode("utf-8")), timeout=timeout)
    except asyncio.TimeoutError:
        kill_tree(proc)
        return None, "", "", round(time.time() - t0, 1), f"délai dépassé ({timeout:.0f} s)"
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), round(time.time() - t0, 1), None


class SharedFiles:
    """Fichiers de consignes et de schémas, nommés par leur empreinte (réutilisés d'un appel à l'autre)."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def file(self, content: str, suffix: str) -> Path:
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        p = self.folder / f"{h}{suffix}"
        if not p.exists():
            p.write_text(content, encoding="utf-8")
        return p


# ============================================================================ Codex
def find_codex(explicit: str | None = None) -> str | None:
    if explicit and Path(explicit).exists():
        return str(explicit)
    if os.name == "nt":
        packages = Path(sys.prefix) / "codex-cli" / "node_modules" / "@openai"
        local = next(packages.glob("codex-win32-*/vendor/*/bin/codex.exe"), None)
        if local:
            return str(local)
    w = shutil.which("codex")
    if w and not (Path(w).parent.name.lower() == "windowsapps"):
        return w
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    if base.exists():
        cands = sorted(base.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cands:
            return str(cands[0])
    return None


def parse_events(stdout: str) -> dict:
    out = {"text": None, "usage": {}, "commands": [], "files": [], "errors": []}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type", "")
        if t == "item.completed":
            item = ev.get("item", {})
            it = item.get("type")
            if it == "agent_message":
                out["text"] = item.get("text")
            elif it == "command_execution":
                out["commands"].append({"commande": item.get("command"), "code": item.get("exit_code"),
                                        "sortie": (item.get("aggregated_output") or "")[-400:]})
            elif it == "file_change":
                for ch in item.get("changes", []) or []:
                    out["files"].append(ch.get("path"))
            elif it == "error":
                out["errors"].append(item.get("message", ""))
        elif t == "turn.completed":
            u = ev.get("usage", {}) or {}
            for k, v in u.items():
                if isinstance(v, (int, float)):
                    out["usage"][k] = out["usage"].get(k, 0) + v
        elif t == "turn.failed":
            out["errors"].append((ev.get("error") or {}).get("message", "échec du tour"))
        elif t == "error":
            out["errors"].append(ev.get("message", "erreur"))
    return out


class CodexBackend:
    nom = "codex"

    def __init__(self, cfg: dict, run_dir: Path):
        self.exe = find_codex((cfg.get("codex") or {}).get("chemin"))
        if not self.exe:
            raise IAError("Codex est introuvable. Installe l'application Codex (ou le CLI) et connecte-toi avec ton compte "
                          "ChatGPT, indique codex.chemin dans la configuration, ou passe à Claude (ia: claude).")
        from .runtime import agent_env
        # Interpréteur autonome lisible dans la sandbox (le python du venv n'y fonctionne pas : voir runtime.py).
        self.env = agent_env()
        if (Path(self.exe).parent.parent / "codex-package.json").is_file():
            # Un CLI autonome n'a pas l'identité du paquet Windows de l'application hôte.
            self.env.pop("CODEX_WINDOWS_REGISTERED_CORE", None)
            self.env.pop("CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY", None)
        self.dir = Path(run_dir) / "codex"
        (self.dir / "vide").mkdir(parents=True, exist_ok=True)
        (self.dir / "sorties").mkdir(parents=True, exist_ok=True)
        self.files = SharedFiles(self.dir / "fichiers")

    async def exec(self, prompt: str, *, model: str, effort: str, schema, instructions, cwd, tools, timeout,
                   max_turns=None) -> dict:
        out_file = self.dir / "sorties" / f"{uuid.uuid4().hex}.txt"
        cmd = [self.exe, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
               "-m", model, "-c", f'model_reasoning_effort="{effort}"', "--json", "-o", str(out_file)]
        if tools:
            if instructions:  # Codex lit AGENTS.md dans le dossier de travail
                (Path(cwd) / "AGENTS.md").write_text(instructions, encoding="utf-8")
            cmd += ["-s", "workspace-write", "-C", str(cwd)]
            if os.name == "nt":
                cmd += ["-c", 'windows.sandbox="elevated"']
            for f in MANAGER_FEATURES_OFF:
                cmd += ["--disable", f]
            for kv in MANAGER_CONFIG:
                cmd += ["-c", kv]
        else:
            cmd += ["-s", "read-only", "-C", str(cwd or self.dir / "vide")]
            for f in NO_TOOL_FEATURES:
                cmd += ["--disable", f]
            for kv in NO_TOOL_CONFIG:
                cmd += ["-c", kv]
            if instructions:
                cmd += ["-c", f'model_instructions_file="{self.files.file(instructions, ".md").as_posix()}"']
        if schema is not None:
            cmd += ["--output-schema", str(self.files.file(json.dumps(schema, ensure_ascii=False), ".json"))]
        cmd.append("-")
        res = empty_result()
        code, stdout, stderr, res["duration"], err = await run_process(cmd, prompt, timeout, env=self.env)
        if err:
            res["error"] = err
            return res
        ev = parse_events(stdout)
        res["commands"], res["files"] = ev["commands"], ev["files"]
        u = ev["usage"]
        res["usage"] = {"input_tokens": u.get("input_tokens", 0), "cached_input_tokens": u.get("cached_input_tokens", 0),
                        "output_tokens": u.get("output_tokens", 0), "reasoning_output_tokens": u.get("reasoning_output_tokens", 0)}
        text = out_file.read_text(encoding="utf-8") if out_file.exists() else None
        try:
            out_file.unlink(missing_ok=True)
        except OSError:
            pass
        text = text or ev["text"]
        res["text"] = text
        if code != 0 or ev["errors"]:
            msg = " | ".join(ev["errors"]) or stderr.strip()[-500:] or f"code de sortie {code}"
            if not text or code != 0:
                res["error"] = msg
                if LIMIT_RE.search(msg):
                    res["limit"], res["wait_s"] = True, wait_seconds(msg)
                elif CODEX_AUTH_RE.search(msg):
                    res["fatal"] = True
                    res["error"] = f"Codex n'est pas connecté à ton compte ChatGPT (codex login) : {msg[:200]}"
                return res
        if schema is not None:
            data = extract_json(text)
            if not isinstance(data, dict):
                res["error"] = "JSON illisible : " + (text or "")[:200]
                return res
            res["data"] = data
        res["ok"] = True
        return res


# ============================================================================ passerelle
async def sleep_until(deadline: float, step: float = 30.0) -> None:
    """Attend jusqu'à une heure donnée de l'horloge murale, par petits pas : si l'ordinateur se met en veille,
    l'attente reprend dès le réveil au lieu de prolonger la pause de la durée de la veille."""
    while True:
        left = deadline - time.time()
        if left <= 0:
            return
        await asyncio.sleep(min(step, left))


def make_backend(name: str, cfg: dict, run_dir: Path):
    if name == "claude":
        from .claude_code import ClaudeBackend
        return ClaudeBackend(cfg, run_dir)
    if name == "codex":
        return CodexBackend(cfg, run_dir)
    raise IAError(f"Moteur inconnu : {name}")


class _NoLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Passerelle:
    """Point d'entrée unique des appels aux modèles pour toute la simulation."""

    def __init__(self, cfg: dict, run_dir: Path, journal=None):
        self.cfg = cfg
        self.acfg = cfg["appels"]
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.journal = journal
        names = [cfg["ia"]]
        if cfg.get("ia_secours") and cfg["ia_secours"] != cfg["ia"]:
            names.append(cfg["ia_secours"])
        self.backends = []
        self.notes: list[str] = []
        for name in names:
            try:
                self.backends.append(make_backend(name, cfg, self.run_dir))
            except IAError as e:
                self.notes.append(f"moteur {name} indisponible : {e}")
        if not self.backends:
            raise IAFatale(" ; ".join(self.notes))
        self.active = 0
        self.until = [0.0] * len(self.backends)   # moteur en limite d'usage jusqu'à cette heure
        self.dead = [False] * len(self.backends)  # moteur hors service pour la partie (non connecté…)
        self._fatal = ""
        self.cache = sqlite3.connect(self.run_dir / "cache_llm.sqlite")
        self.cache.execute("CREATE TABLE IF NOT EXISTS cache (cle TEXT PRIMARY KEY, role TEXT, reponse TEXT, cree REAL)")
        self.cache.commit()
        self._sem: asyncio.Semaphore | None = None
        self.waited_s = 0.0
        self._pause_until = 0.0
        self.day = None

    # ------------------------------------------------------------ état
    @property
    def backend(self):
        return self.backends[self.active]

    @property
    def nom(self) -> str:
        return self.backend.nom

    @property
    def exe(self) -> str:
        return self.backend.exe

    @property
    def sem(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(int(self.acfg.get("parallele", 3)))
        return self._sem

    @property
    def _resume_at(self) -> float:  # ancien nom
        return self._pause_until

    def model(self, profil: str, backend=None) -> tuple[str, str]:
        m = self.cfg["modeles"][(backend or self.backend).nom][profil]
        return m["modele"], m.get("effort", "medium")

    def describe(self, profil: str) -> str:
        model, effort = self.model(profil)
        return f"{model}, effort {effort}"

    # ------------------------------------------------------------ cache
    def _cache_get(self, key: str):
        row = self.cache.execute("SELECT reponse FROM cache WHERE cle = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _cache_put(self, key: str, role: str, res: dict) -> None:
        self.cache.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)", (key, role, json.dumps(res, ensure_ascii=False), time.time()))
        self.cache.commit()

    # ------------------------------------------------------------ appel
    async def run(self, role: str, prompt: str, *, profil: str, schema: dict | None = None, instructions: str | None = None,
                  cwd: Path | None = None, tools: bool = False, timeout: float | None = None, use_cache: bool = True,
                  max_turns: int | None = None, prioritaire: bool = False) -> dict:
        """`prioritaire` : appel fait pour le compte d'une session déjà en cours (arbitre, sous-agent) ; il ne prend pas
        de place dans la file d'appels simultanés, sinon une session qui attend son résultat pourrait la bloquer."""
        timeout = timeout or (self.acfg["timeout_gerant_s"] if tools else self.acfg["timeout_client_s"])
        attempts = int(self.acfg.get("essais", 3))
        last_err, failures = "inconnue", 0
        while True:
            async with (_NoLock() if prioritaire else self.sem):
                k = await self._ready()  # le moteur principal dès qu'il est disponible, sinon le relais
                backend = self.backends[k]
                model, effort = self.model(profil, backend)
                key = hashlib.sha256(json.dumps([backend.nom, role, model, effort, schema, instructions, prompt, tools,
                                                 str(cwd) if tools else ""], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                if use_cache:
                    hit = self._cache_get(key)
                    if hit is not None:
                        hit["cached"] = True
                        self._log(role, model, effort, hit, cached=True)
                        return hit
                try:
                    res = await backend.exec(prompt, model=model, effort=effort, schema=schema, instructions=instructions,
                                             cwd=cwd, tools=tools, timeout=timeout, max_turns=max_turns)
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception as e:  # réponse inattendue du moteur : on la traite comme un échec ordinaire
                    res = empty_result()
                    res["error"] = f"erreur interne en lisant la réponse : {type(e).__name__} : {e}"[:500]
            res["moteur"] = backend.nom
            self._log(role, model, effort, res)
            if res["ok"]:
                if use_cache:
                    self._cache_put(key, role, res)
                return res
            last_err = res.get("error") or "réponse invalide"
            if res.get("fatal"):
                self._on_fatal(k, last_err)  # lève IAFatale s'il ne reste aucun moteur
                continue
            if res.get("limit"):
                self._on_limit(k, res)  # une limite d'usage ne compte pas comme un essai
                continue
            failures += 1
            if failures >= attempts:
                if k == 0 or self.dead[0]:
                    raise IAError(f"{backend.nom} a échoué ({role}) après {attempts} essais : {last_err[:300]}")
                # Le relais échoue sans être en limite (modèle indisponible…) : on attend le moteur principal.
                self.until[k] = max(self.until[k], self.until[0], time.time() + 60)
                print(f"\n[IA] {backend.nom} échoue ({last_err[:120]}) : la partie attend {self.backends[0].nom}.", flush=True)
                failures = 0
                continue
            await asyncio.sleep(min(60, 5 * 2 ** failures))

    async def _ready(self) -> int:
        """Indice du moteur à utiliser : le premier de la liste qui est disponible (le moteur principal dès qu'il l'est
        de nouveau). Si tous sont en limite d'usage, attend la première reprise annoncée."""
        while True:
            alive = [k for k in range(len(self.backends)) if not self.dead[k]]
            if not alive:
                raise IAFatale(self._fatal or "aucun moteur d'IA disponible")
            now = time.time()
            free = [k for k in alive if self.until[k] <= now]
            if free:
                if free[0] != self.active:
                    self._switch_to(free[0])
                return free[0]
            await sleep_until(min(self.until[k] for k in alive))

    def _switch_to(self, new: int) -> None:
        old = self.active
        a, b = self.backends[old].nom, self.backends[new].nom
        self.active = new
        if new < old:
            text = f"{b} est de nouveau disponible : la partie repasse sur {b}"
        else:
            text = f"{a} : {'hors service' if self.dead[old] else 'limite d’usage atteinte'} ; la partie continue avec {b}"
        print(f"\n[IA] {text}.", flush=True)
        if self.journal:
            self.journal.event(self.day, "bascule_ia", a, {"vers": b, "message": text})

    def _switch(self, message: str, why: str = "limite d'usage atteinte") -> None:  # ancien nom
        if self.active + 1 < len(self.backends):
            self.until[self.active] = max(self.until[self.active], time.time() + DEFAULT_WAIT_S)
            self._switch_to(self.active + 1)

    def _on_fatal(self, k: int, message: str) -> None:
        self.dead[k] = True
        self._fatal = message
        print(f"\n[IA] {self.backends[k].nom} hors service : {message[:200]}", flush=True)
        if self.journal:
            self.journal.event(self.day, "moteur_hors_service", self.backends[k].nom, {"message": message[:300]})
        if all(self.dead):
            raise IAFatale(message)

    def _on_limit(self, k: int, res: dict) -> None:
        """Note la fin annoncée de la limite d'un moteur. Si aucun autre moteur n'est disponible, annonce la pause
        (ou arrête la partie, selon `en_cas_de_limite` et `attente_max_h`)."""
        message = res.get("error") or ""
        now = time.time()
        wait = float(res.get("wait_s") or DEFAULT_WAIT_S)
        self.until[k] = max(self.until[k], now + wait)
        if self.journal:
            self.journal.event(self.day, "limite_usage", self.backends[k].nom, {"message": message[:300], "attente_s": wait})
        alive = [i for i in range(len(self.backends)) if not self.dead[i]]
        if any(self.until[i] <= now for i in alive):
            return  # un autre moteur prend le relais (annoncé au prochain appel)
        resume = min(self.until[i] for i in alive)
        if resume <= self._pause_until:
            return  # pause déjà annoncée par un autre appel
        if self.acfg.get("en_cas_de_limite", "attendre") != "attendre":
            raise UsageLimitError(message)
        pause = resume - max(now, self._pause_until)
        max_wait = float(self.acfg.get("attente_max_h", 8)) * 3600
        if self.waited_s + pause > max_wait:
            raise UsageLimitError(f"Limite d'usage atteinte et attente maximale dépassée : {message[:200]}")
        self.waited_s += pause
        self._pause_until = resume
        names = " et ".join(self.backends[i].nom for i in alive)
        until = time.strftime("%H:%M", time.localtime(resume))
        print(f"\n[{names}] Limite d'usage de l'abonnement atteinte. Pause de {(resume - now) / 60:.0f} min "
              f"(reprise vers {until})…", flush=True)

    def _log(self, role: str, model: str, effort: str, res: dict, cached: bool = False) -> None:
        if self.journal:
            u = res.get("usage", {}) or {}
            self.journal.llm_call(self.day, role, model, effort, 0.0 if cached else res.get("duration", 0.0),
                                  u.get("input_tokens", 0), u.get("cached_input_tokens", 0), u.get("output_tokens", 0),
                                  u.get("reasoning_output_tokens", 0), bool(res.get("ok")), res.get("error"), cached,
                                  0.0 if cached else res.get("cost_usd"))

    def close(self) -> None:
        try:
            self.cache.close()
        except Exception:
            pass


CodexClient = Passerelle  # ancien nom
