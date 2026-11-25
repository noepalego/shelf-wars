"""Boucle principale : journées, semaines, gérants, clients, code des gérants, sauvegardes et reprise."""
from __future__ import annotations

import asyncio
import statistics
import gzip
import json
import os
import pickle
import shutil
import time
from collections import defaultdict
from pathlib import Path

from . import concurrence_externe, instruments, personnel, systemes, temps
from .catalogue import PRODUCTS
from .cerveaux import projected_basket, IABrain, FormulaBrain, MixedBrain, SyncFormula
from .clients import PLACES_EXT
from .communication import post_review, recent_reviews
from .config import load_config, save_config, uses_ia
from .enquetes import invite, submit
from .evenements import economic_events, life_events
from .geometrie import visitor_path
from .gerants import actionnaire, projets, rencontre, sous_agents
from .gerants.guichet import Guichet
from .gerants.auteur import AuteurManager
from .gerants.automatisations import AutomationRunner
from .gerants.decisions import apply_decisions, apply_price, empty_decisions, merge_partials, run_price_surveys, sanitize_partial, store_keys
from .gerants.donnees import member_view, snapshot
from .gerants.gerant_ia import IAManager, TeamManager
from .gerants.gerant_regles import RuleManager
from .gerants.strategies import ExperimentalManager, POLICIES
from .instantanes import record_scene
from .journal import Journal
from .llm import Passerelle
from .monde import World
from .perception import covered_needs, outside_offer, walk
from .population import ATTRIBUTE_FAMILIES, persona_long
from .registre import Registre, heure_dans, verite_personne
from .rng import clamp
from .social import apply_talk, select_pairs
from . import variantes

MAX_TRIPS_PER_FRAME = 8


def _dd_list():
    return [0, 0.0]


def _dd_outside():
    return defaultdict(_dd_list)


def _dd_int():
    return defaultdict(int)


class FrozenManager:
    """Direction gelée (rejeux comparatifs) : aucune session, les décisions et les programmes en place continuent."""
    kind = "gel"

    async def decide(self):
        return empty_decisions(), {"sans_session": True}


class Simulation:
    def __init__(self, cfg: dict, run_dir: Path, world: World | None = None, quiet: bool = False):
        self.cfg = cfg
        temps.MASQUER_SEMAINES = bool((cfg.get("gerants") or {}).get("horizon_cache", True))
        self.run_dir = Path(run_dir)
        (self.run_dir / "etat").mkdir(parents=True, exist_ok=True)
        self.quiet = quiet
        self.journal = Journal(self.run_dir / "journal.sqlite")
        self.ia = Passerelle(cfg, self.run_dir, self.journal) if uses_ia(cfg) else None
        self.codex = self.ia  # ancien nom
        fresh = world is None
        if fresh:
            world = World(cfg, self.journal, self.ia)
            world.outside = defaultdict(_dd_outside)
            save_config(cfg, self.run_dir / "config.yaml")
            pop = {c.id: {"attributs": c.attrs, "persona": persona_long(c.attrs)} for c in world.customers.values()}
            (self.run_dir / "population.json").write_text(json.dumps(pop, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
            self.journal.event(0, "debut_partie", "monde", {"foyers": len(world.customers), "config": cfg.get("nom"),
                                                            "ia": cfg["ia"] if self.ia else None})
        else:
            world.journal, world.ia = self.journal, self.ia
            world.cfg = cfg
            if "carburant" in (getattr(world, "cfg_chocs", None) or {}):
                cfg.setdefault("hypermarche", {})["carburant"] = world.cfg_chocs["carburant"]
            concurrence_externe.restore(world)
            for store in world.stores.values():
                store.cfg = cfg
            if not hasattr(world, "outside"):
                world.outside = defaultdict(_dd_outside)
        self.world = world
        world.__dict__.setdefault("registre", Registre())
        self.atelier = instruments.Atelier(world, world.rng.stream("instruments"))
        world.registre.ecouteur = self.atelier
        self.guichet = Guichet(self)
        self.prog_actions: dict = defaultdict(_dd_int)
        cerveau = cfg["clients"]["cerveau"]
        self.brain = IABrain(world) if cerveau == "ia" else MixedBrain(world) if cerveau == "mixte" else SyncFormula()
        self.formula = FormulaBrain()
        self.managers = {}
        world.__dict__.setdefault("team_board", {})
        for m in cfg["magasins"]:
            g = m.get("gerant", "ia")
            st = world.stores[m["id"]]
            st.team = ["gerant"]
            st.domaines_stricts = bool(cfg["gerants"].get("domaines_stricts", False))
            style = g.partition(":")[2] or "equilibre"
            team = [r for r in ("gerant", "marketing") if r in (cfg["gerants"].get("equipe") or ["gerant"])]
            if cfg.get("gel_direction"):
                manager = FrozenManager()
            elif g == "ia" and len(team) > 1:
                manager = TeamManager(world, st, self.run_dir, team)
            elif g == "ia":
                manager = IAManager(world, st, self.run_dir, "gerant", ["gerant"])
            elif g == "auteur":
                manager = AuteurManager(world, st, self.run_dir)
            elif style in POLICIES:
                manager = ExperimentalManager(world, st, style)
            else:
                manager = RuleManager(world, st, style)
            self.managers[m["id"]] = manager
            for agent in getattr(manager, "agents", [manager]):
                if hasattr(agent, "guichet"):
                    agent.guichet = self.guichet
        self.runners = {sid: AutomationRunner() for sid in world.stores}
        self.pending: dict[str, list[tuple[str, dict]]] = defaultdict(list)  # décisions proposées par le code
        self.t0 = time.time()

    def workspace(self, st) -> Path:
        ws = self.run_dir / "gerants" / st.id
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    # ------------------------------------------------------------------ persistance
    @classmethod
    def resume(cls, run_dir: Path, quiet: bool = False, *, overrides: dict | None = None) -> "Simulation":
        run_dir = Path(run_dir)
        cfg = load_config(run_dir / "config.yaml", overrides)
        with gzip.open(run_dir / "etat" / "dernier.pkl.gz", "rb") as f:
            world = pickle.load(f)
        return cls(cfg, run_dir, world, quiet)

    def save(self) -> None:
        p = self.run_dir / "etat" / "dernier.pkl.gz"
        tmp = p.with_name("dernier.tmp")
        with gzip.open(tmp, "wb", compresslevel=3) as f:
            pickle.dump(self.world, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, p)
        if self.world.day % 7 == 0 and self.world.day > 0:
            shutil.copyfile(p, self.run_dir / "etat" / f"semaine_{self.world.day // 7:02d}.pkl.gz")
        status = {"jour": self.world.day, "semaines": self.cfg["semaines"], "termine": self.world.finished,
                  "date": temps.label(min(self.world.day, self.cfg["semaines"] * 7 - 1), brut=True), "maj": time.time(),
                  "ia": self.ia.nom if self.ia else None}
        (self.run_dir / "statut.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")

    def close(self) -> None:
        for r in self.runners.values():
            r.close()
        self.journal.close()
        if self.ia:
            self.ia.close()

    # ------------------------------------------------------------------ boucle
    async def run(self, max_days: int | None = None) -> None:
        total = int(self.cfg["semaines"]) * 7
        done = 0
        try:
            while self.world.day < total:
                d = self.world.day
                if temps.dow(d) == 0 and temps.week_of(d) not in self.world.week_started:
                    await self.start_week()
                await self.run_day()
                self.world.day += 1
                done += 1
                if self.world.day >= total:
                    self.world.finished = True
                if self.cfg.get("sauvegarde_quotidienne", True) or self.world.day % 7 == 0 or (max_days and done >= max_days):
                    self.save()
                if max_days and done >= max_days:
                    break
        finally:
            self.journal.commit()
        if self.world.finished and actionnaire.active(self.world) and not self.world.__dict__.get("revue_finale_faite"):
            for line in actionnaire.final_review(self):  # la période en cours est jugée, sans licenciement
                self.log(line)
            self.world.revue_finale_faite = True
            self.journal.commit()
            self.save()
        if self.world.finished and self.cfg.get("rapport_final", True):
            from .experimentation import write_final_report
            try:
                path = write_final_report(self)
                self.log(f"Rapport final : {path}")
            except Exception as e:  # le rapport ne doit jamais faire échouer une partie terminée
                self.log(f"rapport final impossible : {e}")

    def log(self, text: str) -> None:
        if not self.quiet:
            print(text, flush=True)

    # ------------------------------------------------------------------ semaine
    async def start_week(self) -> None:
        w = self.world
        day = w.day
        week = day // 7
        if self.ia:
            self.ia.day = day
        economic_events(w, day)
        line = concurrence_externe.weekly(w, day)
        self.log(f"\n=== Semaine {week + 1} — direction des magasins ===")
        if line:
            self.log(line)
        trnd = w.rng.stream("personnel_semaine")
        for st in w.stores.values():
            for note in personnel.weekly_turnover(st, day, self.cfg, trnd):
                st.report_notes.append(note)
                self.journal.event(day, "demission", st.id, {"texte": note})
        # L'actionnaire juge d'abord (une direction licenciée est remplacée avant le rendez-vous et les sessions).
        if actionnaire.active(w):
            for st in w.stores.values():
                for line in actionnaire.weekly(self, st):
                    self.log(line)
        if rencontre.active(w) and any(isinstance(m, (IAManager, TeamManager)) for m in self.managers.values()):
            for line in await rencontre.meet(self):
                self.log(line)
            self.journal.commit()

        async def one(sid):
            st = w.stores[sid]
            if st.bankrupt:
                return
            mgr = self.managers[sid]
            agents = mgr.agents if isinstance(mgr, TeamManager) else [mgr]
            rejected, sessions = [], 0
            for agent in agents:  # l'un après l'autre : le second voit ce que le premier vient de décider
                data, meta = await agent.decide()
                sessions += 0 if meta.get("sans_session") else 1
                rejected += await self.apply_session(st, agent, data, meta)
            if sessions:
                st.report_notes, st.inbox_from_competitor = [], []
                st.prog_stats = {}  # tableau de bord des outils : remis à zéro après chaque session
                st.rejected = rejected[-40:]
            else:
                self.log(f"  {sid} « {st.nom} » : pas de session cette semaine (les programmes continuent)")
                st.rejected = (st.rejected + rejected)[-40:]

        w.freeze_competition()
        try:
            results = await asyncio.gather(*(one(s) for s in w.stores), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        finally:
            w.publish_competition()
        # Phase des sous-agents : missions commandées dans les décisions, puis attente de celles lancées au guichet.
        async def missions(sid):
            st = w.stores[sid]
            todo, st.missions_a_executer = list(st.missions_a_executer), []
            for m in todo:
                self.log(f"  {sid} : mission {m['id']} confiée à un sous-agent…")
                await sous_agents.execute(self, st, m, self.workspace(st))
                self.log(f"  {sid} : mission {m['id']} — {m['statut']}")
        await asyncio.gather(*(missions(s) for s in w.stores))
        await self.guichet.drain()
        run_price_surveys(w)
        w.week_started.add(week)
        self.journal.commit()

    async def apply_session(self, st, mgr, data, meta) -> list[str]:
        """Applique la décision d'une session (ou d'une semaine sans session) ; renvoie les refus, étiquetés par rôle."""
        w = self.world
        day, week = w.day, w.day // 7
        duo = bool(getattr(mgr, "duo", False))
        role = getattr(mgr, "role", None)
        label = getattr(mgr, "label", "gérant")
        who = f"{st.id} « {st.nom} »" + (f" — {label}" if duo else "")
        source = getattr(mgr, "kind", "?") + (f":{role}" if duo else "")
        quiet = bool(meta.get("sans_session"))
        refused = []
        if meta.get("suspect"):
            fine = self.cfg["economie"]["amende_lecture_interdite"]
            st.pay(day, "amendes", fine)
            refused.append(f"accès interdit hors du dossier détecté ({len(meta['suspect'])} commande(s)) : amende de {fine:.0f} €")
            self.journal.incident(day, st.id, "acces_interdit", meta["suspect"])
        if meta.get("technique"):
            # Incident d'infrastructure (interpréteur, droit d'accès du système, erreur de syntaxe…) : noté, jamais
            # sanctionné. Le monde simulé ne paie pas pour un problème de l'ordinateur hôte.
            self.journal.incident(day, st.id, "erreur_technique", meta["technique"][:20])
        if data is None:
            if not st.bankrupt:
                refused.append(f"la session n'a pas abouti ({(meta.get('erreur') or '')[:150]}) : aucune décision appliquée")
                self.journal.decision(week, st.id, source, None, refused, "", meta.get("duree", 0.0))
                self.log(f"  {who} : session échouée — {meta.get('erreur', '')[:160]}")
        else:
            allowed = mgr.allowed_keys() if hasattr(mgr, "allowed_keys") else None
            accepted, ko = await apply_decisions(w, st, data, getattr(mgr, "ws", None), role=role, allowed=allowed)
            refused += ko
            if not quiet:
                self.journal.direction(week, st.id, role or "gerant", data, accepted, refused)
                self.journal.decision(week, st.id, source, data, refused, data.get("analyse", ""), meta.get("duree", 0.0))
                self.journal.event(day, "decisions", st.id, {"acceptees": accepted, "refusees": refused, "moteur": meta.get("moteur"),
                                                             "role": role if duo else None})
                self.log(f"  {who} : {len(accepted)} actions, {len(refused)} refus"
                         + (f", {meta.get('commandes', 0)} commandes, {meta.get('duree', 0):.0f} s" if meta.get("duree") else ""))
                if data.get("analyse"):
                    self.log(f"    {data['analyse'][:260]}")
        return [(f"[{label}] " if duo else "") + x for x in refused]

    async def end_of_week(self) -> None:
        w = self.world
        day = w.day
        week = day // 7
        rnd = w.rng.stream("semaine")
        for sid, st in w.stores.items():
            self.run_hook(st, "chaque_dimanche")
            self.run_pending_hooks(st)
        await self.flush_code_decisions()
        for sid, st in w.stores.items():  # notoriété apportée par des équipements (enseigne, vitrine…)
            p = st.effect("notoriete") if st.systems else 0.0
            if p > 0:
                for c in w.active_customers():
                    b = c.beliefs.get(sid)
                    if b is not None and not b["connait"] and rnd.random() < p:
                        b["connait"] = True
                        c.remember(day, f"Remarqué le magasin {st.nom}.", 4, sid, ("magasin",))
        pairs = select_pairs(w, rnd)
        if pairs:
            results = await self.brain.talk(pairs, rnd)
            for p, r in zip(pairs, results):
                apply_talk(w, p, r)
                if w.variantes:
                    variantes.share_talk(w, p, r, apply_talk)
        ctxs, ctxs_var = [], []
        for c in w.active_customers():
            visited = sorted({s for d, s, _ in c.visits if d > day - 7 and s in w.stores})
            if c.week_log or c.pending_surveys or any(m["jour"] > day - 7 for m in c.inbox):
                (ctxs_var if variantes.is_variant(c) else ctxs).append(
                    {"client": c, "magasins_visites": visited, "enquetes": dict(c.pending_surveys)})
        by_source = {}
        if ctxs:
            results = await self.brain.reflect(ctxs, rnd)
            for ctx, r in zip(ctxs, results):
                self.apply_reflection(ctx, r)
                by_source[ctx["client"].id] = r
        if ctxs_var:
            variantes.reflect(self, ctxs_var, by_source)
        visits = {sid: sum(sum(st.visits.get(d, {}).values()) for d in range(day - 6, day + 1)) for sid, st in w.stores.items()}
        tot = sum(visits.values()) or 1
        for sid, st in w.stores.items():
            st.recent_share = visits[sid] / tot
            if st.survey and st.survey["jour"] <= day - 6:
                st.survey = None
            st.bounce_back = [b for b in st.bounce_back if b["fin"] >= day]
        for c in w.customers.values():
            if c.active:
                self.journal.customer_week(week, c.id, {"croyances": {k: {kk: vv for kk, vv in v.items() if kk not in ("prix_ref", "plan_connu")}
                                                                       for k, v in c.beliefs.items()},
                                                        "budget_restant": c.state["budget_restant"], "manques": c.state["manques"],
                                                        "intention": c.intention, "souvenirs": c.recall(day, 5),
                                                        "membre": [s for s in w.stores if w.stores[s].member_of(c.id)],
                                                        "sensibilite_prix": c.attrs["sensibilite_prix"], "statut": c.attrs["statut"]})
                c.pending_surveys = {}
                c.weekly_decay()
        self.journal.commit()
        self.write_research()

    def write_research(self) -> None:
        """Indicateurs de recherche (recherche.md, recherche_semaines.csv), mis à jour chaque dimanche soir."""
        from . import recherche
        try:
            recherche.write(self.run_dir, self.journal.db, list(self.world.stores))
        except Exception as e:  # un indicateur ne doit jamais arrêter une partie
            self.journal.event(self.world.day, "recherche_impossible", "monde", {"erreur": str(e)[:300]})

    def apply_reflection(self, ctx: dict, r: dict) -> None:
        w = self.world
        c = ctx["client"]
        day = w.day
        for m in r.get("magasins", []) or []:
            sid = w.resolve_store(m.get("magasin"))
            if sid is None or not c.beliefs[sid]["connait"]:
                self.journal.event(day, "retour_client_rejete", c.id, {"canal": "croyance", "motif": "magasin inconnu du foyer"})
                continue
            b = c.beliefs[sid]
            before = (b["image_prix"], b["satisfaction"])
            ip = clamp(float(m.get("image_prix", 0)), -2, 2)
            b["image_prix"] = round(b["image_prix"] + clamp(ip - b["image_prix"], -0.8, 0.8), 3)
            for k in ("qualite", "confiance", "satisfaction"):
                v = clamp(float(m.get(k, b[k])), 0, 10)
                b[k] = round(b[k] + clamp(v - b[k], -1.5, 1.5), 2)
            if (b["image_prix"], b["satisfaction"]) != before:
                w.registre.emettre("changement_opinion", "domicile", day, None, None, client=c.id,
                                   donnees={"magasin": sid, "avant": before, "apres": (b["image_prix"], b["satisfaction"])})
        if r.get("souvenir"):
            c.remember(day, str(r["souvenir"])[:300], 6, None, ("bilan",))
        if r.get("intention"):
            c.intention = str(r["intention"])[:200]
        av = r.get("avis") or {}
        sid = w.resolve_store(av.get("magasin"))
        if av.get("publier") and sid in w.stores and c.beliefs[sid]["visites"] > 0 and av.get("texte"):
            post_review(w, c, sid, int(av.get("note", 3)), str(av["texte"]))
            w.stores[sid].evenements_internes.append({"jour": day, "type": "avis", "note": int(av.get("note", 3)),
                                                      "texte": str(av["texte"])[:300]})
        elif av.get("publier"):
            self.journal.event(day, "retour_client_rejete", c.id, {"canal": "avis", "motif": "avis vide ou magasin non visité"})
        for rep in r.get("reponses_enquete", []) or []:
            if submit(w, c, rep):
                w.stores[w.resolve_store(rep["magasin"])].evenements_internes.append({"jour": day, "type": "reponse_enquete"})
            else:
                self.journal.event(day, "retour_client_rejete", c.id, {"canal": "enquete", "motif": "invitation ou réponses invalides"})

    # ------------------------------------------------------------------ journée
    async def run_day(self) -> None:
        w = self.world
        cfg = self.cfg
        day = w.day
        if self.ia:
            self.ia.day = day
        w.daily_weather()
        w.registre.nouveau_jour(day)
        self.watched = self.watched_events()
        w.supplier.tick(day, w.rng.stream("fournisseur"))
        prnd = w.rng.stream("personnel")
        for st in w.stores.values():
            st.expire(day)
            st.receive(day)
            st.slot_prices = {k: v for k, v in st.slot_prices.items() if k[0] >= day}
            projets.tick(w, st, day)
            personnel.plan_day(st, day, cfg, prnd)
            instruments.elaguer(st, day)
            for k in [k for k in st.slot_waits if k[0] < day - 14]:
                del st.slot_waits[k]
        life_events(w, day, w.rng.stream("vie"))
        for c in w.active_customers():
            c.on_new_day(day, w.rng.stream("humeur"))
            c.clean_wallet(w.stores, day)
            c.inbox = [m for m in c.inbox if m["jour"] > day - 14]
        for st in w.stores.values():
            manager = self.managers[st.id]
            if isinstance(manager, ExperimentalManager):
                manager.open_day()
            self.run_hook(st, "chaque_matin", extra=["periodique"])
            self.run_pending_hooks(st)
        await self.flush_code_decisions()
        if w.pending_price_surveys:
            run_price_surveys(w)
        slots = temps.slots(cfg)
        prnd = w.rng.stream("plans")
        ctxs = []
        for c in sorted(w.active_customers(), key=lambda x: x.id):
            plan = c.plan_day(day, slots, prnd)
            if plan:
                ctxs.append(self.place_context(c, plan[0], plan[1]))
        choices = await variantes.choose_places(self, ctxs, w.rng.stream("choix")) if ctxs else []
        visits_by_slot = defaultdict(list)
        outside = defaultdict(list)

        def schedule(ctx, choice):
            c = ctx["client"]
            place = choice["choix"]
            variantes.remember_choice(c, day, place)
            self.journal.choice(day, ctx["creneau"], c.id, place, ctx["trajet"], choice.get("raison", ""), choice.get("cerveau", ""))
            for message in c.inbox:
                message["vu"] = True
            if place == "maison":
                return
            ctx["cout_choisi"] = next(opt["cout"] for opt in ctx["options"] if opt["lieu"] == place)
            self.emit_trip(c, place, ctx)
            if place in w.stores:
                visits_by_slot[ctx["creneau"]].append((c, w.stores[place], ctx))
            else:
                outside[ctx["creneau"]].append((c, place, ctx))

        for ctx, choice in zip(ctxs, choices):
            schedule(ctx, choice)
        brnd = w.rng.stream("paniers_ctx")
        crnd = w.rng.stream("caisse")
        srnd = w.rng.stream("capteurs")
        scene = cfg.get("enregistrer_scene", True)
        opened: set[str] = set()
        for index, slot in enumerate(slots):
            followups = []
            for c, place, ctx in outside[slot]:
                total = self.outside_trip(c, place, ctx)
                followups.append((ctx, place, total))
            # Mise en rayon : équipe de base à l'ouverture, employés de rayon pendant leur créneau.
            empty_at_start = {}
            for st in w.stores.values():
                if st.bankrupt or not st.is_open(day, slot):
                    continue
                plan = st.day_plan if st.day_plan.get("jour") == day else {}
                cap = plan.get("mise_en_rayon", {}).get(slot, 0)
                if st.id not in opened:
                    cap += plan.get("mise_en_rayon_ouverture", 10 ** 6)
                    opened.add(st.id)
                st.shelve(day, cap)
                if "etat_rayon" in self.watched:
                    self.emit_shelves(st, day, slot)
            # Chacun voit le magasin tel qu'il est à son arrivée : prix du créneau, stock restant, rayons du jour.
            basket_ctxs = []
            first_legs = {}
            for c, st, ctx in visits_by_slot.get(slot, []):
                if st.bankrupt or not st.is_open(day, slot):
                    c.spend(ctx["cout_choisi"])
                    self.journal.event(day, "fermeture_imprevue", st.id, {"creneau": slot})
                    followups.append((ctx, st.id, 0.0))
                    continue
                kids = c.attrs["courses_avec_enfants"] > 0 and brnd.random() < c.attrs["courses_avec_enfants"]
                perceived = walk(c, st, day, slot, ctx["liste"], brnd, kids)
                f = st.effect("recherche")
                if f != 1.0:
                    perceived["temps_recherche"] = round(perceived.get("temps_recherche", 0) * f, 1)
                log = st.shelving_log[day]
                for pid in perceived.get("ruptures", []):
                    st.stockouts[day].add(pid)
                    if st.in_reserve(pid, day) > 0:
                        log["ruptures_reserve"] = log.get("ruptures_reserve", 0) + 1
                basket_ctxs.append(self.basket_context(c, st, perceived, ctx, kids))
                first_legs[c.id] = ctx
            baskets = await variantes.choose_baskets(self, basket_ctxs, w.rng.stream("paniers")) if basket_ctxs else []
            items = list(zip(basket_ctxs, baskets))
            crnd.shuffle(items)
            trips = defaultdict(list)
            for bctx, bres in items:
                ticket = self.checkout(bctx, bres, crnd)
                followups.append((first_legs[bctx["client"].id], bctx["lieu"], ticket["total"]))
                if scene and len(trips[bctx["lieu"]]) < MAX_TRIPS_PER_FRAME:
                    path = visitor_path(tuple(sorted(bctx["perceived"].get("rayons_visites", []))))
                    trips[bctx["lieu"]].append({"client": bctx["client"].id, "path": [[round(x, 2), round(z, 2)] for x, z in path],
                                                "articles": sum(l["quantite"] for l in ticket["lignes"]), "total": ticket["total"]})
            self.atelier.fin_periode("creneau")
            for st in w.stores.values():
                if st.is_open(day, slot):
                    self.run_hook(st, "chaque_creneau", slot=slot)
                self.run_pending_hooks(st, slot)
            await self.flush_code_decisions()
            if scene and any(shop.is_open(day, slot) for shop in w.stores.values()):
                for st in w.stores.values():
                    st.scene_trips = trips.get(st.id, [])
                record_scene(w, day, slot)
            if cfg.get("marche_local", {}).get("trajets_enchaines"):
                by_slot = defaultdict(list)
                for ctx, place, total in followups:
                    c = ctx["client"]
                    if ctx.get("precedent") or not any(c.days_left(n) < 0.9 for n in ctx["urgent"]):
                        continue
                    remaining = round(ctx["budget"] - ctx["cout_choisi"] - total, 2)
                    next_slot = next((later for later in slots[index + 1:] if later in c.allowed_slots(day, slots)), None)
                    if remaining > 0 and next_slot:
                        by_slot[next_slot].append(self.place_context(c, next_slot, "petite", previous=place, budget=remaining))
                for next_slot, candidates in by_slot.items():
                    choices = await variantes.choose_places(self, candidates, w.rng.stream("choix"))
                    for ctx, choice in zip(candidates, choices):
                        schedule(ctx, choice)
        for c in w.active_customers():
            c.consume(day)
        for st in w.stores.values():
            st.shelving_log[day]["reste_en_reserve"] = sum(st.in_reserve(p, day) for p in PRODUCTS)
            st.daily_costs(day)
            if not st.bankrupt:
                st.replenish(day, w.supplier)
            if not st.bankrupt and st.cash < cfg["economie"]["seuil_faillite"]:
                st.bankrupt = True
                self.journal.event(day, "faillite", st.id, {"tresorerie": st.cash})
                self.log(f"  !!! {st.nom} fait faillite (trésorerie {st.cash:.0f} €)")
        self.atelier.fin_periode("jour")
        for st in w.stores.values():
            self.run_pending_hooks(st)
        await self.flush_code_decisions()
        if (cfg.get("journalisation") or {}).get("evenements_complets"):
            self.journal.universe(w.registre.du_jour)
        self.record_metrics(day)
        if temps.dow(day) == 6:
            await self.end_of_week()
        if scene:
            for st in w.stores.values():
                st.scene_trips = []
            record_scene(w, day, "cloture")
        self.journal.commit()
        if not self.quiet:
            parts = []
            for sid, st in w.stores.items():
                led = st.ledger.get(day, {})
                parts.append(f"{sid} {led.get('ventes', 0):6.0f} € ({sum(st.visits.get(day, {}).values())} clients)")
            ext = w.outside.get(day, {})
            parts.append(f"hyper {ext.get('hyper', [0])[0]} / marché {ext.get('marche', [0])[0]}")
            el = time.time() - self.t0
            print(f"{temps.short_label(day, brut=True)} | {' | '.join(parts)} | {w.weather_text()} | {el / 60:.1f} min", flush=True)

    def place_context(self, c, slot: str, trip: str, *, previous=None, budget=None) -> dict:
        w = self.world
        cfg = self.cfg
        day = w.day
        options = []
        for sid, st in w.stores.items():
            if sid == previous or st.bankrupt or not st.is_open(day, slot):
                continue
            minutes, mode, cost = c.travel(sid, cfg, w.weather, day=day, slot=slot, previous=previous)
            if minutes <= 60:
                options.append({"lieu": sid, "minutes": minutes, "mode": mode, "cout": cost})
        if cfg.get("option_exterieure", True):
            if trip == "grosse" and temps.hyper_open(day):
                minutes, mode, cost = c.travel("hyper", cfg, w.weather)
                if minutes < 999:
                    options.append({"lieu": "hyper", "minutes": minutes, "mode": mode, "cout": cost})
            if previous != "marche" and temps.market_open(day, slot):
                minutes, mode, cost = c.travel("marche", cfg, w.weather, day=day, slot=slot, previous=previous)
                if minutes <= 40:
                    options.append({"lieu": "marche", "minutes": minutes, "mode": mode, "cout": cost})
        budget = c.trip_budget(day, trip) if budget is None else min(budget, c.state["budget_restant"])
        options = [opt for opt in options if opt["cout"] <= budget]
        options.append({"lieu": "maison", "minutes": 0, "mode": "", "cout": 0.0})
        liste = c.shopping_list(trip)
        refs = c.state["prix_juste"]
        for opt in options:
            place = opt["lieu"]
            if place == "maison":
                continue
            known = dict(c.beliefs[place]["prix_ref"])
            if place in w.stores:
                known.update({pid: price for pid, (price, expiry) in c.expected_prices[place].items() if expiry >= day})
            else:
                known.update({item["produit"]: item["prix"] for item in outside_offer(place, liste, c, cfg)})
            observed = [price / refs[pid] for pid, price in known.items() if pid in refs and refs[pid] > 0]
            relative = statistics.median(observed) if observed else 1.0
            prices = {pid: known.get(pid, price * relative) for pid, price in refs.items()}
            needs = liste if place in w.stores else {n: liste[n] for n in covered_needs(place, liste)}
            estimate, _ = projected_basket(c, needs, budget, prices, capped=False)
            baseline, _ = projected_basket(c, needs, budget, refs, capped=False)
            opt.update(panier_estime=estimate, ecart_prix=(estimate - baseline) / max(5.0, baseline))
        offers = {sid: c.valid_coupons(st, day) for sid, st in w.stores.items()}
        coupons = {sid: [cp.describe() + f" (jusqu'au {temps.short_label(cp.fin)})" for cp in active]
                   for sid, active in offers.items()}
        point_rates = {sid: st.loyalty["points_par_euro"] * st.loyalty["valeur_point"]
                       for sid, st in w.stores.items() if st.loyalty["actif"] and st.member_of(c.id)}
        rnd = w.rng.stream("avis")
        avis = []
        for sid in w.stores:
            if rnd.random() < c.attrs["lit_avis"] * 0.5:
                avis += recent_reviews(w, sid, 2)
        bonus = {sid: st.attraction_for(c.attrs) for sid, st in w.stores.items() if st.systems}
        return {"client": c, "creneau": slot, "trajet": trip, "options": options, "liste": liste, "precedent": previous,
                "urgent": [n for n in liste if c.days_left(n) < 1], "budget": budget, "coupons": coupons,
                "offres_coupons": offers, "valeurs_points": point_rates, "bonus_systemes": bonus,
                "messages": [m for m in c.inbox if not m.get("vu")], "avis": avis, "souvenirs": c.recall(day, 4)}

    def basket_context(self, c, st, perceived: dict, ctx: dict, kids: bool) -> dict:
        day = self.world.day
        m = st.member_of(c.id)
        L = st.loyalty
        labels = {"age": "âge", "quartier": "quartier", "foyer": "composition du foyer", "email": "e-mail", "telephone": "téléphone"}
        offers = c.valid_coupons(st, day)
        return {"client": c, "lieu": st.id, "creneau": ctx["creneau"], "trajet": ctx["trajet"], "mode": ctx.get("mode_choisi", ""),
                "articles": perceived["articles"],
                "ruptures": perceived["ruptures"], "introuvables": perceived["introuvables"], "liste": ctx["liste"],
                "budget": ctx["budget"], "cout_trajet": ctx.get("cout_choisi", 0.0), "enfants": kids, "coupons_ici": [cp.describe() for cp in offers], "perceived": perceived,
                "offres_coupons_ici": offers, "valeur_points": L["points_par_euro"] * L["valeur_point"] if m and L["actif"] else 0.0,
                "fidelite": {"membre": m is not None, "points": m.points if m else 0, "propose": L["actif"] and m is None,
                             "donnees": [labels[x] for x in L["donnees_demandees"]], "bonus": L["bonus_inscription"],
                             "avantages": f"{L['points_par_euro']:g} point(s) par euro, 1 point = {L['valeur_point']:g} €"
                                          + (f", {L['bonus_inscription']} points offerts à l'inscription" if L["bonus_inscription"] else "")
                                          + (", prix membres sur certains produits" if st.member_prices else "")}}

    def validate_basket(self, bctx: dict, bres: dict) -> list[dict]:
        c = bctx["client"]
        seen = {it["produit"]: it for it in bctx["articles"]}
        merged: dict[tuple, int] = {}
        for a in bres.get("achats", []) or []:
            pid = a.get("produit")
            try:
                q = int(a.get("quantite", 0))
            except (TypeError, ValueError):
                continue
            if pid not in seen or q <= 0:
                continue
            short = bool(a.get("date_courte")) and "date_courte" in seen[pid]
            merged[(pid, short)] = min(15, merged.get((pid, short), 0) + q)
        items = [{"produit": p, "quantite": q, "date_courte": s} for (p, s), q in merged.items()]

        for item in items:
            item["cherche"] = seen[item["produit"]].get("cherche", False)
        return items

    def checkout(self, bctx: dict, bres: dict, rnd) -> dict:
        w = self.world
        day = w.day
        c = bctx["client"]
        st = w.stores[bctx["lieu"]]
        slot = bctx["creneau"]
        raw = self.chosen_items(bctx, bres)
        items = self.validate_basket(bctx, bres)
        if bres.get("carte_fidelite") == "oui" and st.loyalty["actif"] and st.member_of(c.id) is None:
            c.state["_consent_email"] = bool(bres.get("accepte_emails"))
            c.state["_consent_sms"] = bool(bres.get("accepte_sms"))
            m = st.join(c, day)
            st.evenements_internes.append({"jour": day, "type": "nouveau_membre", "membre": m.id, "donnees": dict(m.donnees)})
            c.remember(day, f"Inscrit(e) à la carte de fidélité de {st.nom}.", 3, st.id, ("fidelite",))
            self.journal.event(day, "adhesion", st.id, {"client": c.id, "membre": m.id, "email": m.consent_email, "sms": m.consent_sms})
        wait = st.wait_time(day, slot, rnd)
        travel_cost = bctx.get("cout_trajet", 0.0)
        budget = max(0.0, min(bctx["budget"], c.state["budget_restant"]) - travel_cost)
        ticket = st.checkout(c, items, day, slot, rnd, budget=round(budget, 2))
        ticket["attente"] = wait
        ticket["raison"] = bres.get("raison", "")
        self.journal.ticket(ticket)
        c.spend(ticket["total"] + travel_cost)
        c.store_purchases(ticket["lignes"])
        facts = c.update_after_visit(st, bctx["perceived"], ticket, day, wait)
        n = sum(l["quantite"] for l in ticket["lignes"])
        c.remember(day, f"Courses chez {st.nom} : {ticket['total']:.2f} € ({n} articles)" + (f" ; {', '.join(facts[:2])}" if facts else ""),
                   2, st.id, ("courses",))
        c.last_trip_day = day
        if bctx["trajet"] == "grosse":
            c.last_big_trip = day
        c.visits.append((day, st.id, ticket["total"]))
        c.visits = c.visits[-60:]
        self.emit_visit(bctx, raw, items, ticket, wait)
        for l in ticket["lignes"]:
            if st.on_shelf(l["produit"], day) <= 0:
                st.evenements_internes.append({"jour": day, "type": "rayon_vide", "produit": l["produit"], "creneau": slot})
        for cid in ticket["coupons"]:
            cp = st.coupons.get(cid)
            if cp and cp.offre == "filleul" and cp.parrain and cp.parrain in w.customers:
                p = w.customers[cp.parrain]
                rew = st.new_coupon(p.id, "remise_euros_panier", cp.valeur, None, 0.0, day, day + 30, "carte",
                                    f"Merci d'avoir parrainé {c.attrs['prenom']}", cp.campagne)
                p.receive_coupon(rew)
                p.remember(day, f"{c.attrs['prenom']} a utilisé mon parrainage chez {st.nom} : {cp.valeur:.0f} € pour moi.", 4, st.id, ("coupon",))
        if st.survey and st.survey["cible"] == "clients_en_caisse" and day < st.survey["jour"] + 7 and rnd.random() < 0.5:
            invite(c, st, st.survey)
        if any(v["actif"] for v in st.automations.values()) and ticket["lignes"]:
            m = st.member_of(c.id)
            t = {"ticket": ticket["id"], "membre": m.id if m else None, "fiche_membre": member_view(m) if m else None,
                 "total": ticket["total"], "sous_total": ticket["sous_total"], "remise_coupons": ticket["remise_coupons"],
                 "coupons": list(ticket["coupons"]), "jour": day, "creneau": slot,
                 "articles": [{"produit": l["produit"], "quantite": l["quantite"], "prix_unitaire": l["prix_unitaire"],
                               "total": l["total"], "promo": l["promo"]} for l in ticket["lignes"]]}
            self.run_hook(st, "a_chaque_ticket", t, slot)
        return ticket

    # ------------------------------------------------------------------ registre d'événements (vérité du moteur)
    def watched_events(self) -> set[str]:
        """Types d'événements utiles aujourd'hui : observés par au moins un instrument (ou tous, si le journal complet
        de l'univers est demandé)."""
        if (self.cfg.get("journalisation") or {}).get("evenements_complets"):
            return set(instruments.EVENEMENTS)
        out = set()
        for st in self.world.stores.values():
            for s in st.systems.values():
                if s.get("etat") == "actif":
                    out |= {f["evenement"] for f in (s.get("spec") or {}).get("flux", [])}
        return out

    def emit_trip(self, c, place: str, ctx: dict) -> None:
        """Trajet d'un habitant vers un commerce : visible dans la rue et devant les magasins sur son chemin."""
        w = self.world
        opts = {o["lieu"]: o for o in ctx["options"]}
        o = opts.get(place, {})
        mode = o.get("mode", "")
        ctx["mode_choisi"] = mode
        if not self.watched & {"trajet_ville", "passage"}:
            return
        day, slot = w.day, ctx["creneau"]
        hh = heure_dans(slot, w.rng.stream("heures"))
        ver = verite_personne(c, mode=mode)
        if "trajet_ville" in self.watched:
            w.registre.emettre("trajet_ville", "ville", day, slot, hh, client=c.id, verite=ver,
                               donnees={"origine_quartier": c.attrs.get("quartier"), "destination": place,
                                        "mode_deplacement": mode, "duree_min": o.get("minutes")})
        if "passage" in self.watched:
            for sid in w.stores:
                so = opts.get(sid)
                if so is not None and (sid == place or so["minutes"] <= o.get("minutes", 0)):
                    w.registre.emettre("passage", f"abords:{sid}", day, slot, hh, client=c.id, verite=ver,
                                       donnees={"mode_deplacement": mode})

    def emit_shelves(self, st, day: int, slot: str) -> None:
        hh = {"matin": "08:00", "midi": "12:00", "soir": "15:00", "jour": "08:00"}.get(slot, "08:00")
        for pid, listed in st.listed.items():
            if not listed:
                continue
            off = st.shelf_offer(pid, day, slot, False)
            self.world.registre.emettre("etat_rayon", st.id, day, slot, hh, donnees={
                "produit": pid, "rayon": st.planogram[pid]["rayon"], "prix_affiche": off.get("prix"),
                "promo_affichee": off.get("promo") or "", "en_rayon": st.on_shelf(pid, day)})

    @staticmethod
    def chosen_items(bctx: dict, bres: dict) -> dict:
        """Articles que l'habitant a pris en rayon (avant les reposes faute de budget)."""
        seen = {it["produit"] for it in bctx["articles"]}
        out: dict = defaultdict(int)
        for a in bres.get("achats", []) or []:
            pid = a.get("produit")
            try:
                q = int(a.get("quantite", 0))
            except (TypeError, ValueError):
                continue
            if pid in seen and q > 0:
                out[pid] = min(15, out[pid] + q)
        return dict(out)

    def emit_visit(self, bctx: dict, raw: dict, items: list[dict], ticket: dict, wait: float) -> None:
        """Ce qui s'est réellement passé pendant une visite, geste par geste (instruments.py en tire les observations)."""
        if not self.watched:
            return
        w = self.world
        day, slot, c = w.day, bctx["creneau"], bctx["client"]
        st = w.stores[bctx["lieu"]]
        reg = w.registre
        hh = heure_dans(slot, w.rng.stream("heures"))
        mode = bctx.get("mode", "")
        ver = verite_personne(c, enfants=bool(bctx["enfants"]), mode=mode)
        a = c.attrs
        ver["repond"] = round(clamp(0.1 + 0.35 * (1 - a.get("souci_vie_privee", 0.3)) + 0.15 * a.get("gout_promos", 0.5)), 3)
        vid = f"{day}-{slot}-{c.id}-{st.id}"
        perceived = bctx["perceived"]
        watched = self.watched

        def emit(kind, **d):
            if kind in watched:
                reg.emettre(kind, st.id, day, slot, hh, client=c.id, verite=ver, donnees={"visite": vid, **d})

        def rayon(pid):
            return st.planogram[pid]["rayon"]

        seen = {it["produit"]: it for it in bctx["articles"]}
        emit("entree", mode_deplacement=mode)
        rayons = list(perceived.get("rayons_visites", []))
        n = sum(l["quantite"] for l in ticket["lignes"])
        duree = round(3 + 1.5 * len(rayons) + perceived.get("temps_recherche", 0) + (wait or 0) + 0.3 * n, 1)
        emit("deplacement", rayons=rayons, duree_min=duree)
        for pid, it in seen.items():
            emit("consultation", produit=pid, rayon=rayon(pid), niveau=it.get("niveau"), prix_affiche=it.get("prix"),
                 promo_affichee=it.get("promo") or "")
        for pid in bctx["ruptures"]:
            emit("rupture_constatee", produit=pid, rayon=rayon(pid))
        final: dict = defaultdict(int)
        for it in items:
            final[it["produit"]] += it["quantite"]
        for pid, q in raw.items():
            it = seen[pid]
            emit("prise", produit=pid, quantite=q, rayon=rayon(pid), prix_affiche=it.get("prix"), promo_affichee=it.get("promo") or "")
            back = q - final.get(pid, 0)
            if back > 0:
                emit("remise_en_rayon", produit=pid, quantite=back, rayon=rayon(pid))
        bought_needs = {PRODUCTS[p].besoin for p in final}
        for pid in bctx["ruptures"]:
            emit("abandon", produit=pid, cause="rupture", besoin=PRODUCTS[pid].besoin)
        for need in bctx["introuvables"]:
            emit("abandon", produit=None, cause="introuvable", besoin=need)
        for pid, it in seen.items():
            if it.get("cherche") and pid not in final and PRODUCTS[pid].besoin not in bought_needs:
                emit("abandon", produit=pid, cause="renonce", besoin=PRODUCTS[pid].besoin)
        plan = st.day_plan if st.day_plan.get("jour") == day else {}
        if ticket["lignes"]:
            emit("attente_caisse", attente_min=wait, caisses_ouvertes=plan.get("caisses", {}).get(slot, 1))
            emit("achat", ticket=ticket["id"], total=ticket["total"], membre=ticket["membre"] or "", quantite=n,
                 coupons=list(ticket["coupons"]),
                 articles=[{"produit": l["produit"], "quantite": l["quantite"], "prix": l["prix_unitaire"], "promo": l["promo"]}
                           for l in ticket["lignes"]])
        emit("sortie", duree_min=duree, achat_effectue=bool(ticket["lignes"]),
             satisfaction=max(1, min(5, round(c.beliefs[st.id]["satisfaction"] / 2))))

    def outside_trip(self, c, place: str, ctx: dict) -> float:
        w = self.world
        day = w.day
        needs = {n: q for n, q in ctx["liste"].items() if n in covered_needs(place, ctx["liste"])}
        items = outside_offer(place, needs, c, self.cfg)
        fake = {"client": c, "articles": items, "liste": needs, "enfants": False,
                "fidelite": {"propose": False, "donnees": [], "bonus": 0}}
        res = self.formula.choose_basket(fake, w.rng.stream("exterieur"))
        prices = {it["produit"]: it["prix"] for it in items}
        lines = [{"produit": a["produit"], "quantite": a["quantite"]} for a in res["achats"] if a["produit"] in prices]
        travel_cost = ctx.get("cout_choisi", 0.0)
        remaining = round(max(0.0, min(ctx["budget"], c.state["budget_restant"]) - travel_cost), 2)
        for line in lines:
            unit = prices[line["produit"]]
            qty = min(line["quantite"], int((remaining + 1e-8) / unit))
            line["quantite"] = qty
            remaining = round(remaining - qty * unit, 2)
        lines = [line for line in lines if line["quantite"]]
        total = round(sum(prices[l["produit"]] * l["quantite"] for l in lines), 2)
        c.spend(total + travel_cost)
        c.store_purchases(lines)
        b = c.beliefs[place]
        b["connait"] = True
        b["visites"] += 1
        b["habitude"] = min(10.0, b["habitude"] + 1)
        b["derniere_visite"] = day
        b["satisfaction"] = round(0.85 * b["satisfaction"] + 0.15 * (7.5 if place == "marche" else 6.5), 2)
        c.remember(day, f"Courses {'à l’hypermarché' if place == 'hyper' else 'au marché'} : {total:.2f} €.", 3, place, ("courses",))
        c.last_trip_day = day
        if ctx["trajet"] == "grosse":
            c.last_big_trip = day
        c.visits.append((day, place, total))
        rec = w.outside[day][place]
        rec[0] += 1
        rec[1] += total
        self.journal.event(day, "courses_exterieures", c.id, {"lieu": place, "total": total})
        return total

    # ------------------------------------------------------------------ code des gérants
    def hook_data(self, st, light: bool = False, slot: str | None = None) -> dict:
        data = snapshot(self.world, st, light)
        data["creneau"] = slot
        return data

    def run_hook(self, st, hook: str, ticket: dict | None = None, slot: str | None = None, extra: list | None = None) -> None:
        jobs = [(hook, (ticket,) if hook == "a_chaque_ticket" else ())] + [(h, ()) for h in (extra or [])]
        self.run_jobs(st, jobs, light=(hook == "a_chaque_ticket"), slot=slot)

    def run_pending_hooks(self, st, slot: str | None = None) -> None:
        """Observations nouvelles, changements de prix et événements du magasin : un appel groupé par magasin."""
        jobs = [("a_chaque_observation", (name, rec)) for name, rec in self.atelier.prendre_nouvelles(st.id)[-3000:]]
        changes, st.price_changes = st.price_changes, []
        jobs += [("a_chaque_changement_prix", (ch,)) for ch in changes]
        evs, st.evenements_internes = st.evenements_internes, []
        jobs += [("a_chaque_evenement", (e,)) for e in evs[-500:]]
        if jobs:
            self.run_jobs(st, jobs, light=True, slot=slot)

    def run_jobs(self, st, jobs: list, light: bool = False, slot: str | None = None) -> None:
        hooks = {h for h, _ in jobs}
        codes = {k: v["code"] for k, v in st.automations.items() if v["actif"] and any(f"def {h}(" in v["code"] for h in hooks)}
        if not codes or st.bankrupt:
            return
        data = self.hook_data(st, light=light, slot=slot)
        res = self.runners[st.id].call(codes, None, data, jobs=jobs, factor=st.effect("calcul"))
        stats = st.__dict__.setdefault("prog_stats", {})
        for name in codes:
            rec = stats.setdefault(name, {"executions": 0, "actions": 0, "types": {}, "erreurs": 0})
            rec["executions"] += 1
        for name in res.get("erreurs", {}):
            if name in stats:
                stats[name]["erreurs"] += 1
        mem = res.get("memoire", {})
        try:
            if isinstance(mem, dict) and len(json.dumps(mem, default=str)) < 500000:
                st.automation_memory = mem
            elif isinstance(mem, dict):
                st.report_notes.append("mémoire des programmes non enregistrée : plus de 500 000 caractères")
        except (TypeError, ValueError):
            pass
        label = "+".join(sorted(hooks))
        for name, err in res.get("erreurs", {}).items():
            if name in st.automations:
                st.automations[name]["erreurs"] += 1
                st.report_notes.append(f"programme {name} ({label}) en erreur : {err[:200]}")
                if st.automations[name]["erreurs"] >= 3:
                    st.automations[name]["actif"] = False
                    st.report_notes.append(f"programme {name} suspendu après 3 erreurs")
                    self.journal.event(self.world.day, "programme_suspendu", st.id, {"fichier": name, "erreur": err[:300]})
        for line in res.get("logs", [])[:20]:
            st.report_notes.append(line)
        st.report_notes = st.report_notes[-120:]
        self.apply_automation_actions(st, res.get("actions", []))

    def apply_automation_actions(self, st, actions: list[dict]) -> None:
        w = self.world
        day = w.day
        for a in actions[:500]:
            kind = a.get("type")
            author = st.automations.get(a.get("source", ""), {}).get("auteur")
            keys = store_keys(st, author)
            if a.get("source"):
                self.prog_actions[st.id][a["source"]] += 1
                rec = st.__dict__.setdefault("prog_stats", {}).setdefault(
                    a["source"], {"executions": 0, "actions": 0, "types": {}, "erreurs": 0})
                rec["actions"] += 1
                tags = ([f"decider:{k}" for k in a["decision"]] if kind == "decider" and isinstance(a.get("decision"), dict)
                        else [str(kind)])
                for tag in tags[:6]:
                    rec["types"][tag] = rec["types"].get(tag, 0) + 1
            need = {"fixer_prix": "prix", "emettre_coupon": "coupons"}.get(kind)
            if need and keys is not None and need not in keys:
                st.report_notes.append(f"[{a.get('source', '')}] refusé : « {need} » ne relève pas du rôle qui a activé ce programme")
                continue
            if kind == "decider":
                if isinstance(a.get("decision"), dict) and len(self.pending[st.id]) < 50:
                    self.pending[st.id].append((str(a.get("source", "")), a["decision"]))
            elif kind == "publier":
                nom = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in str(a.get("nom", "")))[:60] or "table"
                rows = [r for r in (a.get("lignes") or []) if isinstance(r, dict)][:5000]
                cur = [] if a.get("remplacer", True) else st.program_outputs.get(nom, [])
                st.program_outputs[nom] = (cur + rows)[-5000:]
            elif kind == "annoter":
                m = st.member_by_id(a.get("membre", ""))
                if m:
                    if a.get("tags") is not None:
                        m.tags = list(a["tags"])
                    if a.get("note") is not None:
                        m.notes = a["note"]
            elif kind == "fixer_prix":
                _, notes = apply_price(w, st, a.get("produit"), a.get("prix"), a.get("creneau"))
                st.report_notes.extend(notes)
            elif kind == "emettre_coupon" and len(self.pending[st.id]) < 50:
                fields = ("offre", "valeur", "produit", "panier_min", "jours_validite", "texte")
                coupon = {key: value for key in fields if (value := a.get(key)) is not None}
                coupon["membres"] = [a.get("membre", "")]
                self.pending[st.id].append((str(a.get("source", "")), {"coupons": [coupon]}))

    async def flush_code_decisions(self) -> None:
        """Applique les décisions proposées par le code des gérants (mêmes règles que leurs sessions)."""
        w = self.world
        for sid in list(self.pending):
            items, self.pending[sid] = self.pending[sid], []
            st = w.stores[sid]
            if not items or st.bankrupt:
                continue
            groups: dict = defaultdict(list)  # par auteur : chacun agit avec ses propres leviers
            for src, d in items:
                groups[st.automations.get(src, {}).get("auteur")].append((src, d))
            for author, group in groups.items():
                sources = sorted({s for s, _ in group if s}) or ["programme"]
                tag = "[" + ", ".join(sources) + "]"
                cleaned, problems = [], []
                for _, d in group:
                    c, p = sanitize_partial(d, temps.slots(self.cfg))
                    cleaned.append(c)
                    problems += p
                merged = merge_partials(cleaned)
                accepted, refused = (await apply_decisions(w, st, merged, None, role=author,
                                                           allowed=store_keys(st, author))) if merged else ([], [])
                refused = problems + refused
                for line in accepted[:10]:
                    st.report_notes.append(f"{tag} {line}")
                for line in refused[:10]:
                    st.report_notes.append(f"{tag} refusé : {line}")
                st.report_notes = st.report_notes[-120:]
                self.journal.event(w.day, "decision_code", sid, {"sources": sources, "decision": merged, "auteur": author,
                                                                  "acceptees": accepted, "refusees": refused})

    # ------------------------------------------------------------------ indicateurs
    def record_metrics(self, day: int) -> None:
        w = self.world
        for sid, st in w.stores.items():
            led = st.ledger.get(day, {})
            self.journal.metrics(day, sid, {
                "ca": round(led.get("ventes", 0), 2), "marge": round(led.get("ventes", 0) - led.get("cout_des_ventes", 0), 2),
                "visites": sum(st.visits.get(day, {}).values()), "clients": len(st.visitors.get(day, set())),
                "tresorerie": round(st.cash, 2), "resultat_cumule": round(st.profit, 2),
                "indice_prix": st.price_index(day, w.refs), "taux_marge": st.margin_rate(),
                "promos": sum(1 for p in st.promotions if p.debut <= day <= p.fin), "membres": len(st.members),
                "demarque": round(led.get("demarque", 0), 2),
                "marketing": round(led.get("marketing", 0) + led.get("veille", 0), 2),
                "salaires": round(led.get("salaires", 0) + led.get("caisses_supplementaires", 0), 2),
                "creneaux_travail": sum(st.work_log.get(day, {}).get(k, 0) for k in ("creneaux_base", "creneaux_salaries", "creneaux_interim")),
                "systemes": sum(1 for s in st.systems.values() if s.get("etat") == "actif")})
            self.journal.prices(day, sid, {pid: st.prices[pid] for pid in PRODUCTS if st.listed[pid]})
            obs = {n: sum(1 for r in (st.system_data.get(n) or []) if r.get("jour") == day) for n in st.systems}
            if any(obs.values()):
                self.journal.observations(day, sid, {k: v for k, v in obs.items() if v})
            if self.prog_actions.get(sid):
                self.journal.program_actions(day, sid, dict(self.prog_actions.pop(sid)))
        for place in PLACES_EXT:
            rec = w.outside.get(day, {}).get(place, [0, 0.0])
            self.journal.metrics(day, place, {"ca": round(rec[1], 2), "visites": rec[0], "clients": rec[0]})


def new_run_dir(base: Path, name: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:40]
    d = Path(base) / f"{stamp}_{safe}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def attribute_families() -> dict:
    return ATTRIBUTE_FAMILIES
