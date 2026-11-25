"""Vérifications de cohérence d'une partie : habitants (profils, mémoire, budget, réactions), application des décisions
par le moteur, cloisonnement des données cachées, correspondance des observations avec les événements réels."""
from __future__ import annotations

import gzip
import json
import pickle
import re
import sqlite3
import statistics
from pathlib import Path

from .catalogue import PRODUCTS

HIDDEN_KEYS = ("budget_courses_mensuel", "sensibilite_prix", "souci_vie_privee", "budget_restant", "stress_financier_base",
               "gout_promos", "fidelite_base")


def verify(run_dir: Path) -> list[tuple[str, bool, str]]:
    run_dir = Path(run_dir)
    with gzip.open(run_dir / "etat" / "dernier.pkl.gz", "rb") as f:
        world = pickle.load(f)
    db = sqlite3.connect(run_dir / "journal.sqlite")
    out = []

    def check(name, ok, detail=""):
        out.append((name, bool(ok), detail))

    customers = list(world.customers.values())
    profiles = {json.dumps({k: v for k, v in c.attrs.items() if k not in ("id",)}, sort_keys=True, default=str) for c in customers}
    check("profils distincts", len(profiles) == len(customers), f"{len(profiles)} profils pour {len(customers)} foyers")

    brains = dict(db.execute("SELECT cerveau, COUNT(*) FROM choix GROUP BY cerveau").fetchall())
    check("décisions prises par le modèle des habitants", any(k not in ("formule", "", None) for k in brains),
          f"choix par cerveau : {brains}")
    reasons = [r for (r,) in db.execute("SELECT raison FROM choix WHERE cerveau NOT IN ('formule', '') LIMIT 500")]
    check("raisons explicites des choix", reasons and sum(1 for r in reasons if r and len(r) > 5) / len(reasons) > 0.8,
          f"exemples : {' | '.join(r for r in reasons[:3])}")

    days = max(1, world.day)
    visited = [c for c in customers if c.visits]
    remembered = [c for c in visited if any("Courses" in m["texte"] for m in c.memory)]
    check("mémoire des visites", visited and len(remembered) == len(visited),
          f"{len(remembered)}/{len(visited)} habitants se souviennent de leurs courses")
    weeks = sorted({w for (w,) in db.execute("SELECT DISTINCT semaine FROM clients_semaine")})
    if len(weeks) > 1:
        # l'oubli est voulu (décroissance, 40 souvenirs au plus) : on vérifie que la mémoire couvre plusieurs semaines,
        # pas qu'elle garde la semaine 1 après une longue partie
        kept = sum(1 for c in visited if len({m["jour"] // 7 for m in c.memory}) >= 2)
        check("mémoire persistante d'une semaine à l'autre", kept >= 0.8 * len(visited),
              f"{kept}/{len(visited)} ont des souvenirs d'au moins deux semaines différentes")

    spend = {c.id: sum(t for d, s, t in c.visits) for c in customers}
    budget = {c.id: c.attrs["budget_courses_mensuel"] * days / 30 for c in customers}
    over = [cid for cid in spend if spend[cid] > 2.5 * budget[cid] + 40]
    negative = [c.id for c in customers if c.state["budget_restant"] < 0]
    check("dépenses compatibles avec le budget", not over and not negative,
          f"au-delà du budget de période : {over or 'aucun'}, soldes négatifs : {negative or 'aucun'}")
    xs = [budget[c.id] for c in visited]
    ys = [spend[c.id] for c in visited]
    if len(xs) > 5 and statistics.pstdev(xs) > 0 and statistics.pstdev(ys) > 0:
        r = statistics.correlation(xs, ys)
        check("plus de budget, plus de dépenses (corrélation positive)", r > 0, f"r = {r:.2f}")

    stores = {s: st for s, st in world.stores.items()}
    shares = {s: sum(sum(v.values()) for v in st.visits.values()) for s, st in stores.items()}
    check("les deux magasins reçoivent des clients", all(v > 0 for v in shares.values()), f"visites : {shares}")

    bad = 0
    n = 0
    for tid, store, total, sub, rc, rp, lines in db.execute(
            "SELECT id, magasin, total, sous_total, remise_coupons, remise_points, lignes FROM tickets"):
        n += 1
        ls = json.loads(lines)
        if any(l["produit"] not in PRODUCTS or l["quantite"] <= 0 for l in ls):
            bad += 1
        elif abs(sum(l["total"] for l in ls) - sub) > 0.05 or total < -0.01 or total > sub + 0.01:
            bad += 1
    check("tickets appliqués correctement par le moteur", n and not bad, f"{n} tickets, {bad} incohérents")

    leaks = []
    ids = {c.id for c in customers}
    for f in (run_dir / "gerants").rglob("*"):
        if f.is_file() and f.suffix in (".md", ".csv", ".json", ".py", ".txt"):
            text = f.read_text(encoding="utf-8", errors="replace")
            if any(k in text for k in HIDDEN_KEYS) or any(re.search(rf"\b{re.escape(i)}\b", text) for i in ids):
                leaks.append(str(f.relative_to(run_dir)))
    check("aucune donnée cachée des habitants dans les dossiers des dirigeants", not leaks, f"fuites : {leaks[:5] or 'aucune'}")

    has_universe = db.execute("SELECT COUNT(*) FROM univers").fetchone()[0] if db.execute(
        "SELECT name FROM sqlite_master WHERE name='univers'").fetchone() else 0
    if has_universe:
        events = {r[0]: (r[1], r[2]) for r in db.execute("SELECT id, type, jour FROM univers")}
        per_day = {(t, d): n for t, d, n in db.execute("SELECT type, jour, COUNT(*) FROM univers GROUP BY type, jour")}
        total = mism = agg = 0
        for st in stores.values():
            for name, rows in st.system_data.items():
                for r in rows:
                    total += 1
                    if "ref" not in r:  # ligne agrégée : il faut des événements réels de ce type ce jour-là, en nombre suffisant
                        agg += 1
                        counted = max([v for k, v in r.items() if k.startswith(("nb", "nombre", "compte", "entrees", "passants"))
                                       and isinstance(v, (int, float))] or [1])
                        if per_day.get((r.get("evenement"), r.get("jour")), 0) < counted:
                            mism += 1
                        continue
                    ev = events.get(r["ref"])
                    if ev is None or ev[0] != r["evenement"] or ev[1] != r["jour"]:
                        mism += 1
        check("observations issues d'événements réels", mism == 0,
              f"{total} observations vérifiées (dont {agg} agrégées), {mism} sans événement correspondant ({has_universe} événements)")
    db.close()
    return out


def text_report(results: list[tuple[str, bool, str]]) -> str:
    return "\n".join(f"[{'ok' if ok else 'ÉCHEC'}] {name} — {detail}" for name, ok, detail in results)
