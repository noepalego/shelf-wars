"""Tournoi apparié sur le moteur Simarché. Une partie de référence fournit les deux côtés."""
from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from tempfile import NamedTemporaryFile, TemporaryDirectory

from .config import load_config
from .gerants.strategies import retention_results
from .simulation import Simulation


POLICIES = {
    "temoin": "Témoin",
    "tarification": "Tarification dynamique",
    "relance": "Relance ciblée",
    "rayon": "Placement en rayon",
    "fraicheur": "Fraîcheur",
    "adaptatif": "Gérant adaptatif",
}
SCENARIOS = {"calme": "Marché calme", "choc": "Chocs d’approvisionnement"}
SHOCKS = (
    {"semaine": 5, "type": "inflation", "categories": ["lait", "laitiers", "proteines"],
     "hausse": 0.18, "label": "Lait, laitages et protéines : hausse de 18 % des prix de gros"},
    {"semaine": 9, "type": "penurie", "produits": ["oeufs_mdd", "oeufs_plein_air"],
     "jours": 10, "label": "Pénurie d’œufs pendant dix jours chez le grossiste"},
)


@dataclass(frozen=True)
class Game:
    scenario: str
    seed: int
    policy: str
    side: str
    weeks: int
    households: int
    opponent: str = "temoin"


def _jobs(seeds, weeks, households):
    for scenario in SCENARIOS:
        for seed in seeds:
            yield Game(scenario, seed, "temoin", "A", weeks, households)
            for policy in POLICIES:
                if policy != "temoin":
                    for side in ("A", "B"):
                        yield Game(scenario, seed, policy, side, weeks, households)
            yield Game(scenario, seed, "adaptatif", "A", weeks, households, opponent="adaptatif")


def _events(scenario, weeks):
    return [event for event in SHOCKS if scenario == "choc" and event["semaine"] <= weeks]


async def _trajectory(sim, game):
    sides = ("A", "B") if game.policy == game.opponent else (game.side,)
    rows = {side: [] for side in sides}
    issued = dict.fromkeys(sides, 0)
    for week in range(1, game.weeks + 1):
        await sim.run(7)
        for side in sides:
            store = sim.world.stores[side]
            last = store.strategy_history[-1] if store.strategy_history else {}
            totals = store.week_totals(week - 1)
            count = sum(c.jour_utilisation is not None and c.jour_utilisation // 7 == week - 1
                        for c in store.coupons.values())
            rows[side].append({
                "week": week,
                "profit": round(store.profit, 2),
                "visits": totals["visites"],
                "waste": round(totals.get("demarque", 0), 2),
                "revenue": round(totals.get("ventes", 0), 2),
                "grossMargin": round(totals.get("ventes", 0) - totals.get("cout_des_ventes", 0), 2),
                "labelCost": round(totals.get("etiquettes", 0), 2),
                "coupons": count,
                "issued": store.coupon_seq - issued[side],
                "arm": last.get("arm") if last.get("week") == week else None,
                "closed": store.bankrupt,
                **retention_results(store, (week - 1) * 7),
            })
            issued[side] = store.coupon_seq
    return [{"scenario": game.scenario, "seed": game.seed, "side": side,
             "policy": game.policy, "weeks": rows[side]} for side in sides]


def _play_game(game):
    shops = []
    for side, name in (("A", "Le Panier Malin"), ("B", "Chez Marcel")):
        policy = game.policy if side == game.side else game.opponent
        shops.append({"id": side, "nom": name, "gerant": f"regles:{policy}"})
    events = [{k: v for k, v in event.items() if k != "label"} for event in _events(game.scenario, game.weeks)]
    cfg = load_config(overrides={"nom": "tournoi", "graine": game.seed, "semaines": game.weeks,
                                 "foyers": game.households, "magasins": shops, "evenements": events,
                                 "clients": {"cerveau": "formule"}, "sauvegarde_quotidienne": False,
                                 "enregistrer_scene": False})
    with TemporaryDirectory(prefix="simarche-tournoi-") as path:
        sim = Simulation(cfg, Path(path), quiet=True)
        try:
            return asyncio.run(_trajectory(sim, game))
        finally:
            sim.close()


def _summary(runs):
    controls = {(r["scenario"], r["seed"], r["side"]): r["weeks"] for r in runs if r["policy"] == "temoin"}
    groups = defaultdict(list)
    for run in runs:
        weeks = run["weeks"]
        control = controls[run["scenario"], run["seed"], run["side"]]
        entry = {"seed": run["seed"], "profit": weeks[-1]["profit"],
                 "deltaProfit": weeks[-1]["profit"] - control[-1]["profit"]}
        for metric in ("visits", "waste", "coupons"):
            entry["delta" + metric.capitalize()] = sum(row[metric] for row in weeks) - sum(row[metric] for row in control)
        groups[run["scenario"], run["policy"]].append(entry)

    result = []
    for (scenario, policy), entries in groups.items():
        by_seed = defaultdict(list)
        for entry in entries:
            by_seed[entry["seed"]].append(entry["deltaProfit"])
        averages = {key: round(fmean(entry[key] for entry in entries), 2)
                    for key in ("profit", "deltaProfit", "deltaVisits", "deltaWaste", "deltaCoupons")}
        result.append({"scenario": scenario, "policy": policy, **averages,
                       "seedWins": sum(fmean(pair) > 0 for pair in by_seed.values()), "seeds": len(by_seed)})
    return result


def run_tournament(output: Path, *, seeds=(41, 42, 43, 44), weeks=12, households=30, workers=1) -> dict:
    """Exporte des trajectoires hebdomadaires anonymes à explorer en ligne ou hors connexion."""
    seeds = list(seeds)
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("Choisis au moins une graine et utilise chaque graine une seule fois.")
    if weeks < 1 or households < 4 or workers < 1:
        raise ValueError("Minimum : une semaine, quatre foyers et un processus.")
    jobs = list(_jobs(seeds, weeks, households))
    if workers == 1:
        games = list(map(_play_game, jobs))
    else:
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
            games = list(pool.map(_play_game, jobs))
    runs = []
    duels = []
    for job, game in zip(jobs, games):
        if job.opponent == "temoin":
            runs.extend(game)
        else:
            duels.extend(game)
    data = {
        "version": 1,
        "meta": {"weeks": weeks, "households": households, "seeds": seeds, "simulations": len(jobs),
                 "benchmarkSimulations": len(jobs) - len(duels) // 2, "duelSimulations": len(duels) // 2,
                 "policies": [{"id": k, "label": v} for k, v in POLICIES.items()],
                 "scenarios": [{"id": k, "label": v,
                                "events": [{"week": e["semaine"], "label": e["label"]} for e in _events(k, weeks)]}
                               for k, v in SCENARIOS.items()]},
        "runs": runs, "duels": duels, "summary": _summary(runs),
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write("window.SIMARCHE=")
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write(";\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return data
