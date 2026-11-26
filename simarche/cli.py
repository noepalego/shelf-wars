"""Ligne de commande : python -m simarche <commande>."""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
SCENES_JS = ROOT / "sites" / "observatoire" / "dist" / "instantanes.js"


def resolve_run(name: str | None) -> Path:
    if name:
        p = Path(name)
        if p.exists():
            return p
        if (RUNS / name).exists():
            return RUNS / name
        matches = sorted(d for d in RUNS.glob(f"*{name}*") if d.is_dir() and (d / "config.yaml").exists())
        if matches:
            return matches[-1]
        raise SystemExit(f"Partie introuvable : {name}")
    runs = sorted((d for d in RUNS.glob("*") if (d / "config.yaml").exists() and not d.name.startswith("_")),
                  key=lambda d: d.stat().st_mtime)
    if not runs:
        raise SystemExit("Aucune partie dans le dossier runs/.")
    return runs[-1]


def check_relay(cfg: dict) -> None:
    """Le moteur de relais (Codex) est-il installé et connecté ? Simple avertissement, sans consommer d'usage."""
    if cfg.get("ia_secours") != "codex" or cfg["ia"] == "codex":
        return
    from .llm import find_codex
    exe = find_codex((cfg.get("codex") or {}).get("chemin"))
    if not exe:
        print("Relais Codex introuvable : aux limites d'usage de Claude, la partie fera des pauses.")
        return
    try:
        out = subprocess.run([exe, "login", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=60)
        text = (out.stdout + out.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        text = str(e)
    if "logged in" not in text.lower() or "not logged" in text.lower():
        print(f"Codex n'a pas l'air connecté ({text[:120]}) : lance « codex login » si tu veux qu'il prenne le relais.")


def check_codex_tools(cfg: dict) -> None:
    """Avant une partie avec Codex : python, pandas et le guichet marchent-ils dans la sandbox des agents ?
    (aucun modèle appelé). Une partie ne démarre plus avec des outils cassés."""
    if "codex" not in (cfg["ia"], cfg.get("ia_secours")):
        return
    from . import runtime
    from .llm import find_codex
    exe = find_codex((cfg.get("codex") or {}).get("chemin"))
    if not exe or Path(exe).stem.lower() != "codex":
        return  # pas de Codex réel (tests) : rien à vérifier
    ok, msg = runtime.preflight(exe)
    if ok:
        print(f"Outils des agents vérifiés dans la sandbox de Codex : {msg.splitlines()[0] if msg else 'ok'}.")
        return
    if cfg["ia"] == "codex":
        raise SystemExit("Les agents Codex n'auraient pas d'interpréteur Python utilisable dans leur sandbox :\n"
                         f"{msg}\nPartie non lancée (rien n'a été simulé).")
    print(f"Attention : Python ne fonctionne pas dans la sandbox du relais Codex ({msg[:200]}).")


def check_ia(cfg: dict) -> None:
    """Avant de lancer : Claude Code est-il installé et connecté ? (sans consommer d'usage)"""
    from .config import uses_ia
    if not uses_ia(cfg):
        return
    check_relay(cfg)
    check_codex_tools(cfg)
    if cfg["ia"] != "claude":
        return
    from .claude_code import auth_status, find_claude
    relay = f" (le relais {cfg['ia_secours']} prendra la suite)" if cfg.get("ia_secours") else ""
    exe = find_claude(cfg["claude"].get("chemin"))
    if not exe:
        if relay:
            print(f"Claude Code est introuvable{relay}.")
            return
        raise SystemExit("Claude Code est introuvable : installe-le (https://claude.com/claude-code), ouvre l'application Claude "
                         "une fois, ou indique claude.chemin dans la configuration. Sans IA : ajoute --rapide.")
    status = auth_status(exe, keep_api_key=bool(cfg["claude"].get("utiliser_cle_api")))
    if not status.get("loggedIn"):
        if relay:
            print(f"Claude Code n'est pas connecté{relay}.")
            return
        raise SystemExit("Claude Code n'est pas connecté à ton abonnement. Lance une fois :\n"
                         "  .\\.venv\\Scripts\\python.exe -m simarche claude-login\n"
                         "puis relance la partie (ou ajoute --rapide pour jouer sans IA).")


def cmd_run(args) -> None:
    from .config import load_config
    from .llm import IAError
    from .simulation import Simulation, new_run_dir

    overrides: dict = {}
    if args.semaines:
        overrides["semaines"] = args.semaines
    if args.foyers:
        overrides["foyers"] = args.foyers
    if args.graine is not None:
        overrides["graine"] = args.graine
    if args.ia:
        overrides["ia"] = args.ia
    if args.secours:
        overrides["ia_secours"] = None if args.secours == "aucun" else args.secours
    if args.clients:
        overrides["clients"] = {"cerveau": args.clients}
    if args.cadence:
        overrides["gerants"] = {"cadence_semaines": args.cadence}
    cfg = load_config(args.config, overrides)
    if args.gerants:
        for m in cfg["magasins"]:
            m["gerant"] = args.gerants
    if args.permuter_gerants:
        first, second = cfg["magasins"]
        first["gerant"], second["gerant"] = second.get("gerant", "ia"), first.get("gerant", "ia")
    if args.rapide:
        cfg["clients"]["cerveau"] = "formule"
        for m in cfg["magasins"]:
            if m.get("gerant", "ia") in ("ia", "auteur"):
                m["gerant"] = "regles:equilibre"
    check_ia(cfg)
    run_dir = new_run_dir(RUNS, args.nom or cfg["nom"])
    try:
        sim = Simulation(cfg, run_dir)
    except IAError as e:
        raise SystemExit(str(e))
    _banner(sim, run_dir)
    _go(sim, args.jours, run_dir)


def _banner(sim, run_dir: Path) -> None:
    cfg = sim.cfg
    ia = sim.ia
    print(f"Partie : {run_dir.name}")
    print(f"  {len(sim.world.active_customers())} foyers, {cfg['semaines']} semaines, lois « {cfg['lois']} »")
    cerveau = cfg["clients"]["cerveau"]
    print(f"  Clients : {cerveau}" + (f" ({ia.describe('client')})" if ia and cerveau in ("ia", "mixte") else ""))
    team = cfg["gerants"].get("equipe") or ["gerant"]
    for m in cfg["magasins"]:
        g = m.get("gerant", "ia")
        extra = ""
        if ia and g == "ia":
            cad = int(cfg["gerants"].get("cadence_semaines", 1))
            every = f", une session toutes les {cad} semaine(s)" if cad > 1 else ", une session par semaine"
            if len(team) > 1:
                print(f"  Magasin {m['id']} « {m['nom']} » : direction IA — gérant ({ia.describe('gerant')}) et responsable "
                      f"marketing et clients ({ia.describe('marketing')}){every}")
                continue
            extra = f" ({ia.describe('gerant')}{every})"
        elif ia and g == "auteur":
            extra = f" ({ia.describe('gerant')}, programme révisé toutes les {cfg['auteurs']['revision_semaines']} semaines)"
        print(f"  Magasin {m['id']} « {m['nom']} » : gérant {g}{extra}")
    if ia:
        print(f"  Moteur : {ia.nom} ({ia.exe})" + (f", secours : {cfg['ia_secours']}" if cfg.get("ia_secours") else ""))
        if cfg["gerants"].get("projets", True) and any(m.get("gerant", "ia") == "ia" for m in cfg["magasins"]):
            print(f"  Projets des agents évalués par un expert indépendant ({ia.describe('arbitre')})")
        for note in ia.notes:
            print(f"  ! {note}")
    print("  (Ctrl+C pour interrompre : la partie reprend avec « python -m simarche reprendre »)\n")


def keep_awake(on: bool) -> bool:
    """Empêche la mise en veille automatique de Windows pendant la partie (l'écran peut s'éteindre).
    Demande ordinaire d'un programme en cours d'exécution, levée à la fin de la partie ou si le processus s'arrête."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:
        return False


def _go(sim, jours, run_dir: Path) -> None:
    from .gerants.auteur import AuteurIndisponible
    from .llm import IAError, UsageLimitError
    awake = sim.cfg.get("empecher_veille", True) and keep_awake(True)
    if awake:
        print("  (mise en veille automatique du PC suspendue pendant la partie ; l'écran peut s'éteindre)\n")
    try:
        asyncio.run(sim.run(jours))
    except UsageLimitError as e:
        print(f"\nLimite d'usage de ton abonnement atteinte : {str(e)[:200]}")
        print(f"La partie est sauvegardée (fin de la dernière journée complète). Relance plus tard : python -m simarche reprendre {run_dir.name}")
    except AuteurIndisponible as e:
        print(f"\nEn attente d'un programme : {e}")
        print(f"Dépose programme.py et note.txt dans ce dossier, puis : python -m simarche reprendre {run_dir.name}")
    except IAError as e:
        print(f"\nArrêt : {str(e)[:400]}")
        print(f"La partie est sauvegardée. Reprise : python -m simarche reprendre {run_dir.name}")
    except KeyboardInterrupt:
        print(f"\nInterrompu. Reprise : python -m simarche reprendre {run_dir.name}")
    finally:
        sim.close()
        if awake:
            keep_awake(False)
    _summary(run_dir)
    _export_scenes(run_dir)


def _summary(run_dir: Path) -> None:
    from .metriques import summary
    try:
        s = summary(run_dir)
    except Exception as e:  # pas encore de données
        print(f"(résumé indisponible : {e})")
        return
    print("\n" + s["texte"])
    print(f"\nTableau de bord : python -m simarche tableau   |   Rapport final : python -m simarche rapport {run_dir.name}")


def _export_scenes(run_dir: Path) -> None:
    """Met à jour la visite 3D avec cette partie et les plus récentes."""
    from .instantanes import export_replays, runs_with_scenes
    try:
        recent = [d for d in runs_with_scenes(RUNS) if d.resolve() != Path(run_dir).resolve()][-4:]
        if Path(run_dir).resolve() in {d.resolve() for d in runs_with_scenes(RUNS)}:
            export_replays(recent + [Path(run_dir)], SCENES_JS)
            print(f"Visite 3D des magasins : {(SCENES_JS.parent / 'magasins.html').resolve()}")
    except Exception as e:
        print(f"(visite 3D non actualisée : {e})")


def cmd_resume(args) -> None:
    from .llm import IAError
    from .simulation import Simulation
    from .config import load_config
    run_dir = resolve_run(args.partie)
    overrides = {}
    if args.ia:
        overrides["ia"] = args.ia
    if args.secours:
        overrides["ia_secours"] = None if args.secours == "aucun" else args.secours
    check_ia(load_config(run_dir / "config.yaml", overrides))
    try:
        sim = Simulation.resume(run_dir, overrides=overrides)
    except IAError as e:
        raise SystemExit(str(e))
    if sim.world.finished:
        print("Cette partie est déjà terminée.")
        sim.close()
        _summary(run_dir)
        return
    _banner(sim, run_dir)
    print(f"Reprise au {sim.world.day}e jour.")
    _go(sim, args.jours, run_dir)


def cmd_list(args) -> None:
    for d in sorted(RUNS.glob("*")):
        st = d / "statut.json"
        if st.exists():
            s = json.loads(st.read_text(encoding="utf-8"))
            ia = f", {s['ia']}" if s.get("ia") else ""
            print(f"{d.name:45s} jour {s['jour']:3d}/{s['semaines'] * 7}  {'terminée' if s['termine'] else 'en cours'}  ({s['date']}{ia})")


def cmd_report(args) -> None:
    from .analyse import final_report
    run_dir = resolve_run(args.partie)
    path = asyncio.run(final_report(run_dir))
    print(f"Rapport écrit : {path}")


def cmd_bilan(args) -> None:
    from .experimentation import report_from_run
    path = report_from_run(resolve_run(args.partie))
    print(f"Rapport final écrit : {path}")


def cmd_research(args) -> None:
    from .recherche import write_from_run
    path = write_from_run(resolve_run(args.partie))
    print(f"Indicateurs de recherche écrits : {path}")


def audit_run(run_dir: Path) -> dict:
    """Rejoue l'audit d'accès (règles actuelles) sur toutes les sessions archivées d'une partie."""
    import json as _json
    from .gerants.gerant_ia import audit_access
    out = {"sessions": 0, "commandes": 0, "fautes": [], "techniques": 0}
    stores = sorted(p.name for p in (Path(run_dir) / "gerants").iterdir() if p.is_dir()) if (Path(run_dir) / "gerants").exists() else []
    for sid in stores:
        ws = Path(run_dir) / "gerants" / sid
        other = next((x for x in stores if x != sid), "B" if sid == "A" else "A")
        for f in sorted((ws / "historique").glob("semaine_*_commandes.json")):
            cmds = _json.loads(f.read_text(encoding="utf-8"))
            acc_file = f.with_name(f.name.replace("_commandes.json", "_acces.json"))
            acc = _json.loads(acc_file.read_text(encoding="utf-8")) if acc_file.exists() else {}
            res = {"commands": cmds if isinstance(cmds, list) else [], "files": acc.get("files", []),
                   "reads": acc.get("reads", []), "denials": acc.get("denials", []), "moteur": acc.get("moteur")}
            faults, tech = audit_access(res, ws, other)
            out["sessions"] += 1
            out["commandes"] += len(res["commands"]) + len(res["denials"])
            out["techniques"] += len(tech)
            out["fautes"] += [f"{sid} {f.name.replace('_commandes.json', '')} : {x[:200]}" for x in faults]
    return out


def cmd_audit(args) -> None:
    res = audit_run(resolve_run(args.partie))
    print(f"{res['sessions']} sessions, {res['commandes']} commandes et demandes rejouées : {len(res['fautes'])} faute(s), "
          f"{res['techniques']} incident(s) technique(s) non sanctionné(s).")
    for x in res["fautes"]:
        print("  - " + x)


def cmd_verify(args) -> None:
    from .verification import text_report, verify
    run_dir = resolve_run(args.partie)
    res = verify(run_dir)
    text = text_report(res)
    print(text)
    (run_dir / "verification.txt").write_text(text + "\n", encoding="utf-8")
    if not all(ok for _, ok, _ in res):
        raise SystemExit(1)


def cmd_counterfactual(args) -> None:
    from .experimentation import counterfactual, report_from_run
    run_dir = resolve_run(args.partie)
    print(f"Rejeu de {args.semaines} semaine(s) à partir de la fin de la semaine {args.semaine}, avec puis sans : "
          f"{', '.join(args.retirer)} ({args.magasin})…", flush=True)
    out = counterfactual(run_dir, args.semaine, args.magasin, args.retirer, args.semaines, args.cerveau)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    report_from_run(run_dir)


def cmd_interview(args) -> None:
    from .analyse import interview
    from .config import load_config
    from .llm import IAError
    run_dir = resolve_run(args.partie)
    check_ia({**load_config(run_dir / "config.yaml"), "clients": {"cerveau": "ia"}})
    history: list[tuple[str, str]] = []
    try:
        if args.question:
            print(asyncio.run(interview(run_dir, args.agent, args.semaine, args.question, history)))
            return
        print(f"Interview de {args.agent} (ligne vide pour quitter)")
        while True:
            try:
                q = input("\nToi : ").strip()
            except EOFError:
                break
            if not q:
                break
            a = asyncio.run(interview(run_dir, args.agent, args.semaine, q, history))
            history.append((q, a))
            print(f"{args.agent} : {a}")
    except IAError as e:
        raise SystemExit(f"Interview impossible : {str(e)[:400]}")


def cmd_lab(args) -> None:
    from .config import load_config
    from .labo import run_lab
    if args.cerveau != "formule":
        check_ia(load_config(ROOT / "configs" / "defaut.yaml", {"clients": {"cerveau": args.cerveau}}))
    run_lab(args.cerveau, args.foyers)


def cmd_tournament(args) -> None:
    from .tournoi import run_tournament
    results = run_tournament(args.sortie, seeds=tuple(args.graines), weeks=args.semaines,
                             households=args.foyers, workers=args.processus)
    print(f"{results['meta']['simulations']} parties terminées. Résultats : {args.sortie.resolve()}")


def cmd_scenes(args) -> None:
    from .instantanes import export_replays, runs_with_scenes
    if args.parties:
        dirs = [resolve_run(p) for p in args.parties]
    else:
        dirs = runs_with_scenes(RUNS)
        dirs = dirs if args.toutes else dirs[-5:]
    if not dirs:
        raise SystemExit("Aucune partie enregistrée en 3D (option enregistrer_scene) dans runs/.")
    data = export_replays(dirs, args.sortie)
    for g in data["games"]:
        print(f"  {g['id']} : {len(g['frames'])} instants")
    print(f"Écrit : {Path(args.sortie).resolve()}")
    print(f"Ouvre : {(Path(args.sortie).parent / 'magasins.html').resolve()}")


def cmd_dashboard(args) -> None:
    app = Path(__file__).resolve().parent / "ui" / "app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app), "--server.headless", "false"], cwd=str(ROOT))


def cmd_claude_test(args) -> None:
    from .claude_code import auth_status, find_claude
    from .config import load_config
    from .llm import IAError, Passerelle
    cfg = load_config(ROOT / "configs" / "defaut.yaml", {"ia": "claude", "ia_secours": None})
    exe = find_claude(cfg["claude"].get("chemin"))
    if not exe:
        raise SystemExit("Claude Code est introuvable : installe-le (https://claude.com/claude-code), ouvre l'application "
                         "Claude une fois, ou indique claude.chemin dans configs/defaut.yaml.")
    print(f"Claude Code trouvé : {exe}")
    status = auth_status(exe)
    if not status.get("loggedIn"):
        print("Connexion : non connecté. Lance « python -m simarche claude-login » (navigateur, compte Claude Pro ou Max), puis relance ce test.")
        if status.get("erreur"):
            print(f"  ({status['erreur']})")
        return
    print(f"Connexion : {status.get('authMethod', '?')}" + (f", abonnement {status['subscriptionType']}" if status.get("subscriptionType") else ""))
    tmp = RUNS / "_test_claude"
    ia = Passerelle(cfg, tmp)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}, "phrase": {"type": "string"}},
              "required": ["ok", "phrase"], "additionalProperties": False}
    try:
        for profil in ("client", "gerant"):
            res = asyncio.run(ia.run("test", f"Test ({profil}). Réponds ok = true et une phrase de bienvenue pour une simulation de commerce.",
                                     profil=profil, schema=schema, use_cache=False))
            u = res["usage"]
            print(f"{profil:7s} → {ia.describe(profil)} : {res['duration']:.1f} s, {res['data']} "
                  f"({u.get('input_tokens', 0)} jetons en entrée, {u.get('output_tokens', 0)} en sortie)")
    except IAError as e:
        print(f"Échec : {e}")
    finally:
        ia.close()


def cmd_claude_login(args) -> None:
    from .claude_code import find_claude, login
    from .config import load_config
    cfg = load_config(ROOT / "configs" / "defaut.yaml")
    exe = find_claude(cfg["claude"].get("chemin"))
    if not exe:
        raise SystemExit("Claude Code est introuvable (voir python -m simarche claude-test).")
    print(f"Connexion de Claude Code ({exe}) à ton compte Claude : une page va s'ouvrir dans ton navigateur.")
    code = login(exe)
    print("Connecté." if code == 0 else f"La connexion n'a pas abouti (code {code}).")


def cmd_codex_test(args) -> None:
    from .config import load_config
    from .llm import CodexBackend, IAError, Passerelle
    cfg = load_config(ROOT / "configs" / "defaut.yaml", {"ia": "codex", "ia_secours": None})
    tmp = RUNS / "_test_codex"
    try:
        backend = CodexBackend(cfg, tmp)
    except IAError as e:
        raise SystemExit(str(e))
    print(f"Codex trouvé : {backend.exe}")
    out = subprocess.run([backend.exe, "login", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("Connexion : " + (out.stdout or out.stderr).strip())
    ia = Passerelle(cfg, tmp)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}, "phrase": {"type": "string"}},
              "required": ["ok", "phrase"], "additionalProperties": False}
    try:
        res = asyncio.run(ia.run("test", "Réponds ok = true et une phrase de bienvenue pour une simulation de commerce.",
                                 profil="client", schema=schema, use_cache=False))
        print(f"Réponse de {ia.describe('client')} en {res['duration']:.1f} s : {res['data']}")
        print(f"Jetons : {res['usage']}")
    except IAError as e:
        print(f"Échec : {e}")
    finally:
        ia.close()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(prog="python -m simarche", description="Simarché : deux commerces pilotés par IA se disputent une ville de clients IA.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="lancer une nouvelle partie")
    r.add_argument("--config", default=str(ROOT / "configs" / "defaut.yaml"))
    r.add_argument("--nom")
    r.add_argument("--semaines", type=int)
    r.add_argument("--foyers", type=int)
    r.add_argument("--graine", type=int)
    r.add_argument("--jours", type=int, help="s'arrêter après N jours (essai)")
    r.add_argument("--ia", choices=["claude", "codex"], help="moteur des agents (par défaut : celui de la configuration)")
    r.add_argument("--secours", choices=["claude", "codex", "aucun"], help="moteur utilisé si la limite d'usage du premier est atteinte")
    r.add_argument("--clients", choices=["ia", "mixte", "formule"], help="cerveau des habitants")
    r.add_argument("--gerants", choices=["ia", "auteur"], help="type des deux gérants")
    r.add_argument("--permuter-gerants", action="store_true", help="échanger les politiques des deux magasins avec la même graine")
    r.add_argument("--cadence", type=int, help="une session de gérant toutes les N semaines")
    r.add_argument("--rapide", action="store_true", help="sans IA : clients à formule et gérants à règles")
    r.set_defaults(fn=cmd_run)
    rs = sub.add_parser("reprendre", help="reprendre une partie interrompue")
    rs.add_argument("partie", nargs="?")
    rs.add_argument("--jours", type=int)
    rs.add_argument("--ia", choices=["claude", "codex"], help="moteur principal pour cette reprise")
    rs.add_argument("--secours", choices=["claude", "codex", "aucun"], help="moteur de secours pour cette reprise")
    rs.set_defaults(fn=cmd_resume)
    sub.add_parser("liste", help="lister les parties").set_defaults(fn=cmd_list)
    rp = sub.add_parser("rapport", help="écrire le rapport de fin de partie")
    rp.add_argument("partie", nargs="?")
    rp.set_defaults(fn=cmd_report)
    iv = sub.add_parser("interview", help="interroger un client (C07) ou un gérant (A)")
    iv.add_argument("partie", nargs="?")
    iv.add_argument("--agent", required=True)
    iv.add_argument("--semaine", type=int)
    iv.add_argument("--question")
    iv.set_defaults(fn=cmd_interview)
    lb = sub.add_parser("labo", help="vérifier que les clients se comportent de façon crédible")
    lb.add_argument("--cerveau", default="formule", choices=["formule", "ia", "mixte"])
    lb.add_argument("--foyers", type=int, default=30)
    lb.set_defaults(fn=cmd_lab)
    tn = sub.add_parser("tournoi", help="comparer les politiques programmées et actualiser l'observatoire")
    tn.add_argument("--semaines", type=int, default=12)
    tn.add_argument("--foyers", type=int, default=30)
    tn.add_argument("--graines", nargs="+", type=int, default=[41, 42, 43, 44])
    tn.add_argument("--processus", type=int, default=4)
    tn.add_argument("--sortie", type=Path, default=ROOT / "sites" / "observatoire" / "dist" / "resultats.js")
    tn.set_defaults(fn=cmd_tournament)
    sc = sub.add_parser("scenes", help="exporter les parties pour la visite 3D des magasins")
    sc.add_argument("parties", nargs="*", help="parties à exporter (par défaut : les 5 plus récentes)")
    sc.add_argument("--toutes", action="store_true")
    sc.add_argument("--sortie", type=Path, default=SCENES_JS)
    sc.set_defaults(fn=cmd_scenes)
    bl = sub.add_parser("bilan", help="rapport final comparatif (stratégies, inventions, sous-agents, erreurs), sans IA")
    bl.add_argument("partie", nargs="?")
    bl.set_defaults(fn=cmd_bilan)
    rc = sub.add_parser("recherche", help="indicateurs de recherche (collusion tacite, meneur/suiveur, concentration, initiative)")
    rc.add_argument("partie", nargs="?")
    rc.set_defaults(fn=cmd_research)
    au = sub.add_parser("audit", help="rejouer l'audit d'accès des dirigeants sur une partie (règles actuelles)")
    au.add_argument("partie", nargs="?")
    au.set_defaults(fn=cmd_audit)
    vf = sub.add_parser("verifier", help="vérifier la cohérence d'une partie (habitants, moteur, données cachées, observations)")
    vf.add_argument("partie", nargs="?")
    vf.set_defaults(fn=cmd_verify)
    cf = sub.add_parser("contrefactuel", help="rejouer des semaines avec et sans un dispositif ou un programme (gain estimé)")
    cf.add_argument("partie", nargs="?")
    cf.add_argument("--semaine", type=int, required=True, help="départ : fin de cette semaine (sauvegarde etat/semaine_NN)")
    cf.add_argument("--magasin", required=True)
    cf.add_argument("--retirer", nargs="+", required=True, help="instrument:<nom> ou programme:<fichier>")
    cf.add_argument("--semaines", type=int, default=4)
    cf.add_argument("--cerveau", default="formule", choices=["formule", "mixte", "ia"])
    cf.set_defaults(fn=cmd_counterfactual)
    sub.add_parser("tableau", help="ouvrir le tableau de bord").set_defaults(fn=cmd_dashboard)
    sub.add_parser("claude-test", help="vérifier Claude Code et ton abonnement (2 petits appels)").set_defaults(fn=cmd_claude_test)
    sub.add_parser("claude-login", help="connecter Claude Code à ton compte Claude (une fois)").set_defaults(fn=cmd_claude_login)
    sub.add_parser("codex-test", help="vérifier la connexion à Codex").set_defaults(fn=cmd_codex_test)
    args = p.parse_args()
    args.fn(args)
