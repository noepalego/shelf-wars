"""Guichet du moteur : ce que les dirigeants (et leurs sous-agents) demandent au moteur pendant leur session.

L'agent lance `python outils/moteur.py <commande> …` dans son dossier. Ce petit client (bibliothèque standard
seulement) dépose une requête dans `.moteur/requetes/` et attend la réponse dans `.moteur/reponses/`. Pendant la
session, la simulation surveille ce dossier et répond : devis de l'arbitre, acceptation (paiement puis installation
immédiate), missions de sous-agents, essais de programmes, activation, état. Le moteur valide tout : le client
peut être modifié par l'agent sans rien changer aux règles.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from .. import temps
from ..llm import STOP_ERRORS
from . import projets, sous_agents
from .automatisations import AutomationRunner, HOOKS, validate

CLIENT = r'''"""Guichet du moteur de la simulation (outil fourni par le moteur, réécrit à chaque session).

Usage : python outils/moteur.py <commande> [arguments]

  etat                                  trésorerie, dispositifs, devis, missions, programmes
  devis projets/<nom>.md                faire évaluer une invention par l'arbitre (rien n'est payé)
  mission missions/<nom>.md             faire chiffrer une mission confiée à un sous-agent (rien n'est payé)
  accepter <devis>                      accepter un devis : paiement, puis installation ou mission immédiate
  refuser <devis>                       refuser un devis
  arreter <dispositif>                  arrêter un dispositif en service (plus de coût hebdomadaire)
  tester <fichier.py> [declencheur]     essayer un programme sur les données actuelles (rien n'est appliqué)
  activer <fichier.py>                  activer un programme (nouvelle version s'il a changé)
  desactiver <fichier.py>               suspendre un programme
  attendre <requete>                    récupérer une réponse qui n'était pas encore prête
  apport <montant> <motif>              demander du capital à l'actionnaire (si la partie en a un)
  verifier <decision.json>              vérifier à blanc un brouillon de décision (rien n'est appliqué)
"""
import json
import os
import sys
import time
import uuid

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".moteur")
ATTENTE_S = 100


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("aide", "-h", "--help"):
        print(__doc__)
        return
    if args[0] == "attendre" and len(args) > 1:
        rid = args[1]
    else:
        rid = uuid.uuid4().hex[:10]
        folder = os.path.join(BASE, "requetes")
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, rid + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"id": rid, "commande": args[0], "arguments": args[1:], "t": time.time()}, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(folder, rid + ".json"))
    rep = os.path.join(BASE, "reponses", rid + ".json")
    t0 = time.time()
    while time.time() - t0 < ATTENTE_S:
        if os.path.exists(rep):
            try:
                with open(rep, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                time.sleep(0.2)
                continue
            print(data.get("texte", ""))
            if data.get("details") not in (None, {}, []):
                print(json.dumps(data["details"], ensure_ascii=False, indent=1))
            return
        time.sleep(0.3)
    print("Toujours en cours (requête " + rid + "). Continue ton travail et relance plus tard : "
          "python outils/moteur.py attendre " + rid)


if __name__ == "__main__":
    main()
'''

COMMANDES_SOUS_AGENT = {"etat", "devis", "tester", "verifier"}


def install_client(folder: Path) -> None:
    (folder / "outils").mkdir(parents=True, exist_ok=True)
    (folder / "outils" / "moteur.py").write_text(CLIENT, encoding="utf-8")
    for sub in ("requetes", "reponses"):
        (folder / ".moteur" / sub).mkdir(parents=True, exist_ok=True)


class Guichet:
    def __init__(self, sim):
        self.sim = sim
        self.boxes: dict[Path, dict] = {}
        self.loop_task: asyncio.Task | None = None
        self.inflight: set[asyncio.Task] = set()
        self.background: dict[str, set] = {}   # magasin → missions en cours d'exécution
        self.seen: set[str] = set()

    @property
    def world(self):
        return self.sim.world

    # ------------------------------------------------------------ ouverture pendant une session
    def session(self, folder: Path, store, role: str, kind: str = "dirigeant", mission: str | None = None):
        guichet = self

        class _Ctx:
            async def __aenter__(self):
                install_client(folder)
                guichet.boxes[Path(folder)] = {"store": store, "role": role, "kind": kind, "mission": mission, "ws": Path(folder)}
                if guichet.loop_task is None or guichet.loop_task.done():
                    guichet.loop_task = asyncio.create_task(guichet._loop())
                return guichet

            async def __aexit__(self, *exc):
                await guichet._scan(Path(folder))  # dernières requêtes déposées juste avant la fin
                guichet.boxes.pop(Path(folder), None)
                return False
        return _Ctx()

    async def _loop(self) -> None:
        while self.boxes:
            for folder in list(self.boxes):
                await self._scan(folder)
            await asyncio.sleep(0.25)

    async def _scan(self, folder: Path) -> None:
        box = self.boxes.get(folder)
        if box is None:
            return
        req_dir = folder / ".moteur" / "requetes"
        for f in sorted(req_dir.glob("*.json")) if req_dir.exists() else []:
            key = str(f)
            if key in self.seen:
                continue
            self.seen.add(key)
            try:
                req = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            t = asyncio.create_task(self._serve(folder, box, req, f))
            self.inflight.add(t)
            t.add_done_callback(self.inflight.discard)

    async def drain(self, store_id: str | None = None) -> None:
        """Attend les réponses en cours et les missions lancées (avant de simuler la semaine)."""
        while self.inflight:
            await asyncio.gather(*list(self.inflight), return_exceptions=True)
        tasks = [t for sid, ts in self.background.items() if store_id in (None, sid) for t in ts]
        if tasks:
            res = await asyncio.gather(*tasks, return_exceptions=True)
            for r in res:
                if isinstance(r, STOP_ERRORS):
                    raise r

    async def _serve(self, folder: Path, box: dict, req: dict, path: Path) -> None:
        rid = str(req.get("id") or path.stem)[:40]
        try:
            text, details = await self.handle(box, str(req.get("commande", "")), [str(a) for a in req.get("arguments", [])][:6])
        except STOP_ERRORS:
            text, details = "le moteur est momentanément indisponible (limite d'usage) : réessaie plus tard", None
        except Exception as e:  # une requête mal formée ne doit jamais arrêter la simulation
            text, details = f"erreur du guichet : {type(e).__name__} : {str(e)[:200]}", None
        out = folder / ".moteur" / "reponses"
        out.mkdir(parents=True, exist_ok=True)
        tmp = out / f"{rid}.tmp"
        tmp.write_text(json.dumps({"id": rid, "texte": text, "details": details}, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(out / f"{rid}.json")
        try:
            path.unlink()
        except OSError:
            pass
        w = self.world
        w.journal.event(w.day, "guichet", box["store"].id, {"role": box["role"], "type": box["kind"], "mission": box["mission"],
                                                            "commande": req.get("commande"), "arguments": req.get("arguments"),
                                                            "reponse": text[:600]})

    # ------------------------------------------------------------ commandes
    async def handle(self, box: dict, cmd: str, args: list[str]) -> tuple[str, object]:
        w, st, role, ws = self.world, box["store"], box["role"], box["ws"]
        if box["kind"] == "sous_agent" and cmd not in COMMANDES_SOUS_AGENT:
            return ("refusé : un sous-agent ne peut engager aucune dépense ni rien activer ; ton mandant décidera à partir "
                    "de ton rapport (tu peux demander un devis : il lui sera transmis)", None)
        if cmd == "etat":
            return "état du magasin", self.state(st)
        if cmd in ("devis", "mission") and not args:
            return f"usage : python outils/moteur.py {cmd} <fichier.md>", None
        if cmd == "devis":
            q, msg = await projets.quote(w, st, role, args[0], ws, origine=box["kind"], prioritaire=True)
            return msg, projets.public_quote(q) if q else None
        if cmd == "mission":
            q, msg = await sous_agents.quote(w, st, role, args[0], ws, prioritaire=True)
            return msg, sous_agents.public_quote(q) if q else None
        if cmd in ("accepter", "refuser") and args:
            q = st.devis.get(args[0])
            if q is None:
                return f"devis inconnu : {args[0]} (voir python outils/moteur.py etat)", None
            ws_main = self.sim.workspace(st)
            if cmd == "refuser":
                return (projets.refuse if q["type"] == "instrument" else sous_agents.refuse)(w, st, args[0], role, ws_main)[1], None
            if q["type"] == "instrument":
                ok, msg = projets.accept(w, st, args[0], role, ws_main)
                if ok:
                    st.evenements_internes.append({"jour": w.day, "type": "instrument_installe", "nom": q["nom"]})
                return msg, None
            ok, msg, mission = sous_agents.accept(w, st, args[0], role)
            if not ok:
                return msg, None
            task = asyncio.create_task(sous_agents.execute(self.sim, st, mission, ws_main))
            self.background.setdefault(st.id, set()).add(task)
            task.add_done_callback(lambda t: self.background.get(st.id, set()).discard(t))
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=90)
            except asyncio.TimeoutError:
                return (msg + f" La mission {mission['id']} est en cours ; son résultat arrivera dans "
                              f"sous_agents/{mission['id']}/RESULTAT.md (demande-le avec : python outils/moteur.py etat)."), None
            return msg + " " + sous_agents.summary(mission), sous_agents.public_mission(mission)
        if cmd == "arreter" and args:
            return projets.stop(w, st, args[0], role)[1], None
        if cmd in ("activer", "desactiver") and args:
            from .decisions import apply_decisions
            ok, ko = await apply_decisions(w, st, {"automatisations": [{"fichier": args[0], "actif": cmd == "activer"}]},
                                           self.sim.workspace(st), role=role, allowed=None)
            return "; ".join(ok + ko) or "rien à faire", None
        if cmd == "verifier" and args:
            from .decisions import check_decision
            path = (ws / args[0]).resolve()
            if not path.is_relative_to(ws.resolve()) or not path.exists():
                return f"fichier introuvable dans ton dossier : {args[0]}", None
            try:
                draft = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as e:
                return f"JSON illisible : {e}", None
            summary, issues = check_decision(draft, ws)
            head = ("aucun problème détecté" if not issues else f"{len(issues)} ligne(s) seraient refusées (le reste serait appliqué)")
            return f"vérification à blanc, rien n'est appliqué : {head}", {"lignes_par_champ": summary, "problemes": issues[:60]}
        if cmd == "apport":
            from . import actionnaire
            return actionnaire.request_capital(w, st, role, args), None
        if cmd == "tester" and args:
            return await self.test_program(box, args[0], args[1] if len(args) > 1 else None)
        return f"commande inconnue : {cmd} (python outils/moteur.py aide)", None

    def state(self, st) -> dict:
        w = self.world
        return {"date": temps.label(w.day), "tresorerie": round(st.cash, 2), "resultat_cumule": round(st.profit, 2),
                "dispositifs": {n: {"etat": s.get("etat"), "cout_hebdomadaire": s.get("cout_hebdomadaire"),
                                    "observations": s.get("observations", 0), "installation": s.get("installation", "")[:200]}
                                for n, s in st.systems.items()},
                "devis": [projets.public_quote(q) if q["type"] == "instrument" else sous_agents.public_quote(q)
                          for q in st.devis.values() if q["statut"] == "propose"],
                "missions": [sous_agents.public_mission(m) for m in list(st.missions.values())[-10:]],
                "programmes": {k: {"actif": v["actif"], "version": v.get("version"), "auteur": v.get("auteur"), "erreurs": v.get("erreurs", 0)}
                               for k, v in st.automations.items()},
                **({"actionnaire": _actionnaire_state(w, st)} if _actionnaire_active(w) else {})}

    async def test_program(self, box: dict, fichier: str, hook: str | None) -> tuple[str, object]:
        ws, st = box["ws"], box["store"]
        path = (ws / fichier).resolve()
        if not path.is_relative_to(ws.resolve()) or path.suffix != ".py" or not path.exists():
            return f"programme introuvable dans ton dossier : {fichier}", None
        code = path.read_text(encoding="utf-8", errors="replace")
        errs = validate(code)
        if errs:
            return "programme refusé par le bac à sable : " + " ; ".join(errs), None
        data = self.sim.hook_data(st, slot="soir")
        sample_obs = next(((n, rows[-1]) for n, rows in (data.get("systemes") or {}).items() if rows), ("aucun", {}))
        last_ticket = data["tickets"][-1] if data.get("tickets") else {"ticket": "T0", "membre": None, "articles": [], "total": 0}
        samples = {"a_chaque_ticket": (last_ticket,), "a_chaque_observation": sample_obs,
                   "a_chaque_changement_prix": ({"jour": data["jour"], "produit": "lait_mdd", "ancien_prix": 1.0, "nouveau_prix": 1.1},),
                   "a_chaque_evenement": ({"jour": data["jour"], "type": "rayon_vide", "produit": "lait_mdd"},)}
        hooks = [hook] if hook in HOOKS else [h for h in HOOKS if f"def {h}(" in code]
        jobs = [(h, samples.get(h, ())) for h in hooks]
        runner = AutomationRunner()
        try:
            res = await asyncio.to_thread(runner.call, {fichier: code}, None, data, jobs=jobs,
                                          factor=st.effect("calcul"))
        finally:
            runner.close()
        actions = [{k: v for k, v in a.items() if k != "source"} for a in res.get("actions", [])][:40]
        return (f"essai de {fichier} ({', '.join(hooks)}) : {len(res.get('actions', []))} action(s) proposée(s), rien n'a été "
                "appliqué"), {"actions": actions, "notes": res.get("logs", [])[:30], "erreurs": res.get("erreurs", {})}


def _actionnaire_active(w) -> bool:
    from . import actionnaire
    return actionnaire.active(w)


def _actionnaire_state(w, st) -> dict:
    from . import actionnaire
    return actionnaire.public_state(w, st)


def now() -> float:
    return time.time()
