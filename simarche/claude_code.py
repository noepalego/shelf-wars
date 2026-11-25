"""Moteur Claude Code : appels `claude -p` avec l'abonnement Claude (Pro ou Max), sans clé API.

- Habitants, modération : aucun outil (`--tools ""`), consignes en invite système, JSON imposé (`--json-schema`).
- Gérants : session dans leur dossier avec lecture, écriture et Python (`--restricted` confine les outils de
  fichiers au dossier ; le terminal n'accepte que `python …`). Chaque commande est journalisée pour l'anti-triche.

Connexion (une seule fois) : `python -m simarche claude-login`, ou `claude auth login` dans un terminal.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .llm import IAError, SharedFiles, empty_result, extract_json, run_process

MANAGER_TOOLS = "Read,Write,Edit,Glob,Grep,Bash"
PYTHON_COMMANDS = ("Bash(python *)", "Bash(python3 *)", "Bash(py *)")
LIMIT_RE = re.compile(r"usage limit|limit reached|hit your (?:\w+ )?limit|rate.?limit|too many requests|\b429\b|"
                      r"out of (?:extra )?usage|quota|limite d'utilisation", re.I)
AUTH_RE = re.compile(r"not logged in|/login|invalid api key|authentication_error|oauth token|please log ?in|"
                     r"credit balance is too low", re.I)
RESET_EPOCH_RE = re.compile(r"\|\s*(\d{10})\b")
TZ_RE = re.compile(r"\(([A-Za-z]+(?:/[A-Za-z0-9_+-]+)+)\)")
RESET_RE = re.compile(r"resets?\s+(?:at\s+|à\s+)?(?:(mon|tue|wed|thu|fri|sat|sun|lun|mar|mer|jeu|ven|sam|dim)\w*\.?\s+)?"
                      r"(\d{1,2})(?:[:h](\d{2}))?\s*(am|pm)?", re.I)
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
        "lun": 0, "mar": 1, "mer": 2, "jeu": 3, "ven": 4, "sam": 5, "dim": 6}
HOST_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK", "CLAUDE_PREVIEW")
HOST_NAMES = ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT")


def find_claude(explicit: str | None = None) -> str | None:
    """Trouve le CLI Claude Code : chemin donné, PATH, installateur natif, npm, puis application de bureau."""
    if explicit:
        return str(explicit) if Path(explicit).exists() else None
    w = shutil.which("claude")
    if w and Path(w).parent.name.lower() != "windowsapps":
        return w
    home = Path.home()
    for c in (home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude",
              home / ".claude" / "local" / "claude.exe", home / ".claude" / "local" / "claude",
              Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"):
        if c.exists():
            return str(c)
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    cands = []
    if local.exists():
        for pattern in ("Claude-Data/*/claude-code/*/*/claude.exe", "AnthropicClaude/*/claude-code/*/*/claude.exe",
                        "Claude/claude-code/*/*/claude.exe"):
            cands += list(local.glob(pattern))
    if cands:
        return str(max(cands, key=lambda p: p.stat().st_mtime))
    return None


def command_prefix(exe: str) -> list[str]:
    """Un script Python (faux CLI des tests) se lance avec l'interpréteur courant."""
    return [sys.executable, exe] if exe.lower().endswith(".py") else [exe]


def claude_env(keep_api_key: bool = False) -> dict:
    """Environnement d'un appel autonome : sans les variables d'une session Claude Code hôte, avec le Python du projet."""
    env = dict(os.environ)
    hosted = "CLAUDECODE" in env or "CLAUDE_CODE_ENTRYPOINT" in env
    for k in list(env):
        if k in HOST_NAMES or k.startswith(HOST_PREFIXES):
            del env[k]
    if hosted:  # adresse propre à la session hôte (application de bureau)
        env.pop("ANTHROPIC_BASE_URL", None)
    if not keep_api_key:  # l'abonnement plutôt qu'une facturation à l'usage
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(k, None)
    scripts = str(Path(sys.executable).parent)
    env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("DISABLE_AUTOUPDATER", "1")
    # Ni télémétrie, ni rapport d'erreurs, ni vérification de mise à jour : environ 0,5 s de moins par appel
    # (des centaines d'appels par semaine simulée), sans effet sur les réponses des modèles.
    env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    return env


def _zone(message: str):
    """Fuseau horaire cité par le message (« resets 12:10am (America/New_York) »), s'il est connu de Python."""
    m = TZ_RE.search(message or "")
    if not m:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(m.group(1))
    except Exception:  # base des fuseaux absente (Windows sans tzdata) : heure locale
        return None


def reset_wait_seconds(message: str, now: dt.datetime | None = None) -> float | None:
    """Secondes avant la fin de la limite annoncée (« resets 3pm », « resets Mon 9am », « …|1767225600 »)."""
    m = RESET_EPOCH_RE.search(message or "")
    if m:
        return max(60.0, int(m.group(1)) - (now or dt.datetime.now()).timestamp() + 120)
    if now is None:
        tz = _zone(message)
        now = dt.datetime.now(tz) if tz else dt.datetime.now()
    m = RESET_RE.search(message or "")
    if not m:
        return None
    day, hour, minute, ampm = m.group(1), int(m.group(2)), int(m.group(3) or 0), (m.group(4) or "").lower()
    if hour > 23 or minute > 59:
        return None
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day:
        ahead = (DAYS[day.lower()[:3]] - now.weekday()) % 7
        target += dt.timedelta(days=ahead)
        if target <= now:
            target += dt.timedelta(days=7)
    elif target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds() + 120


def parse_stream(stdout: str) -> dict:
    """Lit la sortie `--output-format stream-json` : outils utilisés, sortie structurée et résultat final."""
    out = {"result": None, "structured": None, "commands": [], "files": [], "reads": [], "text": None}
    pending: dict[str, dict] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        msg = ev.get("message")  # certains événements (système, nouvelle tentative…) portent un simple texte
        content = msg.get("content") if isinstance(msg, dict) else None
        if t == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    out["text"] = block["text"]
                if block.get("type") != "tool_use":
                    continue
                name, inp = block.get("name"), block.get("input")
                if isinstance(inp, str):
                    inp = extract_json(inp)
                if not isinstance(inp, dict):
                    inp = {}
                if name == "StructuredOutput":
                    out["structured"] = inp
                elif name == "Bash":
                    cmd = {"commande": inp.get("command"), "code": None, "sortie": ""}
                    out["commands"].append(cmd)
                    pending[block.get("id", "")] = cmd
                elif name in ("Write", "Edit", "NotebookEdit"):
                    out["files"].append(inp.get("file_path") or inp.get("notebook_path"))
                elif name in ("Read", "Glob", "Grep"):
                    out["reads"].append(inp.get("file_path") or inp.get("path") or inp.get("pattern"))
        elif t == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in pending:
                    cmd = pending[block["tool_use_id"]]
                    body = block.get("content")
                    if isinstance(body, list):
                        body = " ".join(str(b.get("text", "")) for b in body if isinstance(b, dict))
                    cmd["sortie"] = str(body or "")[-400:]
                    cmd["code"] = 1 if block.get("is_error") else 0
        elif t == "result" and isinstance(ev.get("result", ""), (str, type(None))):
            out["result"] = ev
    return out


def usage_of(result: dict) -> dict:
    u = result.get("usage") or {}
    cached = int(u.get("cache_read_input_tokens") or 0)
    total_in = int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + cached
    thinking = int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
    return {"input_tokens": total_in, "cached_input_tokens": cached, "output_tokens": int(u.get("output_tokens") or 0),
            "reasoning_output_tokens": thinking}


class ClaudeBackend:
    nom = "claude"

    def __init__(self, cfg: dict, run_dir: Path):
        ccfg = cfg.get("claude") or {}
        self.exe = find_claude(ccfg.get("chemin"))
        if not self.exe:
            raise IAError("Claude Code est introuvable. Installe-le (https://claude.com/claude-code) ou ouvre l'application "
                          "Claude une fois, ou indique claude.chemin dans la configuration.")
        self.prefix = command_prefix(self.exe)
        self.env = claude_env(bool(ccfg.get("utiliser_cle_api")))
        self.dir = Path(run_dir) / "claude"
        (self.dir / "vide").mkdir(parents=True, exist_ok=True)
        self.files = SharedFiles(self.dir / "fichiers")
        self.max_turns = int(cfg["appels"].get("max_tours_gerant", 40))

    def turn_limit(self, tools: bool, max_turns: int | None) -> int:
        """Tours autorisés : la réponse structurée en consomme un (deux si le schéma n'est pas respecté du premier coup)."""
        return int(max_turns or (self.max_turns if tools else 6))

    def command(self, *, model: str, effort: str | None, schema, instructions, tools: bool, max_turns: int | None) -> list[str]:
        cmd = self.prefix + ["-p", "--output-format", "stream-json", "--verbose", "--model", model,
                             "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands",
                             "--setting-sources", ""]
        if effort:
            cmd += ["--effort", effort]
        if tools:
            cmd += ["--restricted", "--tools", MANAGER_TOOLS, "--permission-mode", "acceptEdits",
                    "--allowedTools", *PYTHON_COMMANDS, "--permission-prompts", "none",
                    "--max-turns", str(self.turn_limit(tools, max_turns))]
        else:
            cmd += ["--tools", "", "--max-turns", str(self.turn_limit(tools, max_turns))]
        if instructions:
            cmd += ["--system-prompt-file", str(self.files.file(instructions, ".md"))]
        if schema is not None:
            cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False, separators=(",", ":"))]
        return cmd

    async def exec(self, prompt: str, *, model: str, effort: str, schema, instructions, cwd, tools, timeout,
                   max_turns=None) -> dict:
        cmd = self.command(model=model, effort=effort, schema=schema, instructions=instructions, tools=tools, max_turns=max_turns)
        if schema is not None:  # --json-schema crée l'outil StructuredOutput : la réponse finale passe par lui
            prompt += "\n\n(Réponse finale : appelle l'outil StructuredOutput avec l'objet JSON demandé.)"
        res = empty_result()
        code, stdout, stderr, res["duration"], err = await run_process(cmd, prompt, timeout, cwd=cwd or self.dir / "vide",
                                                                       env=self.env)
        if err:
            res["error"] = err
            return res
        ev = parse_stream(stdout)
        res["commands"], res["files"], res["reads"] = ev["commands"], ev["files"], ev["reads"]
        result = ev["result"]
        if result is None:
            msg = (stderr.strip() or stdout.strip())[-500:] or f"code de sortie {code}"
            res["error"] = f"pas de résultat de Claude Code : {msg}"
            res["fatal"] = bool(AUTH_RE.search(msg))
            return res
        res["usage"] = usage_of(result)
        res["cost_usd"] = result.get("total_cost_usd")
        res["denials"] = result.get("permission_denials") or []
        text = result.get("result") if isinstance(result.get("result"), str) else None
        res["text"] = text or ev["text"]
        if result.get("is_error") or result.get("subtype") not in (None, "success"):
            msg = text or str(result.get("subtype") or "erreur")
            if result.get("subtype") == "error_max_turns":
                msg = f"nombre maximal de tours atteint ({self.turn_limit(tools, max_turns)})"
            res["error"] = msg[:500]
            if AUTH_RE.search(msg):
                res["fatal"] = True
                res["error"] = ("Claude Code n'est pas connecté à ton abonnement : lance « python -m simarche claude-login » "
                                f"(ou « claude auth login ») puis relance. Détail : {msg[:200]}")
            elif LIMIT_RE.search(msg) or result.get("api_error_status") == 429:
                res["limit"], res["wait_s"] = True, reset_wait_seconds(msg)
            return res
        if schema is not None:
            data = result.get("structured_output")
            if isinstance(data, str):
                data = extract_json(data)
            if not isinstance(data, dict):
                data = ev["structured"] if isinstance(ev["structured"], dict) else extract_json(res["text"])
            if not isinstance(data, dict):
                res["error"] = "JSON illisible : " + (res["text"] or "")[:200]
                return res
            res["data"] = data
        res["ok"] = True
        return res


# ============================================================================ connexion
def auth_status(exe: str, timeout: float = 60, keep_api_key: bool = False) -> dict:
    """État de connexion du CLI (sans consommer d'usage)."""
    try:
        out = subprocess.run(command_prefix(exe) + ["auth", "status"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout, env=claude_env(keep_api_key))
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"loggedIn": False, "erreur": str(e)}
    data = extract_json(out.stdout)
    if isinstance(data, dict):
        return data
    return {"loggedIn": False, "erreur": (out.stdout or out.stderr).strip()[:300]}


def login(exe: str) -> int:
    """Ouvre la connexion interactive (navigateur) avec le compte Claude de l'utilisateur."""
    return subprocess.call(command_prefix(exe) + ["auth", "login"], env=claude_env())


def wait_label(seconds: float) -> str:
    return time.strftime("%H:%M", time.localtime(time.time() + seconds))
