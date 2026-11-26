"""Indicateurs de recherche, calculés seulement à partir du journal (rejouable sur une partie passée).

Questions visées :
- collusion tacite : les prix et les marges montent-ils ensemble, sans communication ? (indice de prix, taux de marge
  brute réalisé — proxy de l'indice de Lerner —, parallélisme des variations, écart entre magasins) ;
- meneur et suiveur : quand un magasin change un prix, l'autre suit-il dans la semaine, dans le même sens ?
- défection : un magasin passe-t-il sous le prix de l'autre (sous-cotation), en particulier dans la semaine qui suit
  un rendez-vous entre dirigeants ? Part des produits vendus moins cher que le concurrent ;
- concentration : indice de Herfindahl-Hirschman des passages (A, B, hypermarché, marché) ;
- habitants : part des dépenses faite hors de la ville, prix moyen payé par rapport au prix conseillé ;
- initiative des dirigeants : versions de programmes, inventions, missions, durée des sessions, comptes rendus de
  chantier.
Les chiffres sont descriptifs : aucun ne prouve une causalité ni une intention.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

LOCAL = ("A", "B")
FOLLOW_DAYS = 7


def _corr(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    try:
        return round(statistics.correlation(x, y), 3)
    except statistics.StatisticsError:
        return None


def weekly_rows(db: sqlite3.Connection, stores: list[str] | None = None) -> list[dict]:
    stores = list(stores or LOCAL)
    met = defaultdict(lambda: defaultdict(lambda: {"ca": 0.0, "marge": 0.0, "visites": 0, "indice": [], "tresorerie": None,
                                                   "resultat": None}))
    for jour, lieu, ca, marge, visites, indice, treso, res in db.execute(
            "SELECT jour, lieu, ca, marge, visites, indice_prix, tresorerie, resultat_cumule FROM metriques ORDER BY jour"):
        m = met[jour // 7][lieu]
        m["ca"] += ca or 0
        m["marge"] += marge or 0
        m["visites"] += visites or 0
        if lieu in stores:
            m["indice"].append(indice or 1.0)
            m["tresorerie"], m["resultat"] = treso, res
    changes = defaultdict(lambda: defaultdict(int))
    for wk, mag, n in db.execute("SELECT jour / 7, magasin, COUNT(*) FROM prix p WHERE EXISTS (SELECT 1 FROM prix q WHERE "
                                 "q.magasin = p.magasin AND q.produit = p.produit AND q.jour = p.jour - 1 AND "
                                 "abs(q.prix - p.prix) > 0.004) GROUP BY jour / 7, magasin"):
        changes[wk][mag] = n
    progs = defaultdict(lambda: defaultdict(int))
    for wk, mag, n in db.execute("SELECT jour / 7, magasin, COUNT(*) FROM programmes GROUP BY jour / 7, magasin"):
        progs[wk][mag] = n
    inv = defaultdict(lambda: defaultdict(int))
    for wk, mag, n in db.execute("SELECT installation / 7, magasin, COUNT(*) FROM inventions WHERE installation IS NOT NULL "
                                 "GROUP BY installation / 7, magasin"):
        inv[wk][mag] = n
    mis = defaultdict(lambda: defaultdict(int))
    for wk, mag, n in db.execute("SELECT jour / 7, magasin, COUNT(*) FROM missions GROUP BY jour / 7, magasin"):
        mis[wk][mag] = n
    sess = defaultdict(lambda: defaultdict(float))
    for wk, mag, d in db.execute("SELECT semaine, magasin, SUM(duree) FROM decisions WHERE source LIKE 'ia%' GROUP BY semaine, magasin"):
        sess[wk][mag] = d or 0.0
    rows = []
    for wk in sorted(met):
        m = met[wk]
        tot_v = sum(v["visites"] for v in m.values()) or 1
        hhi = round(sum((v["visites"] / tot_v * 100) ** 2 for v in m.values()))
        tot_ca = sum(v["ca"] for v in m.values()) or 1
        row = {"semaine": wk + 1, "hhi_passages": hhi,
               "part_depenses_hors_ville": round(sum(v["ca"] for k, v in m.items() if k not in stores) / tot_ca, 3)}
        for s in stores:
            v = m.get(s, {"ca": 0, "marge": 0, "visites": 0, "indice": [], "tresorerie": None, "resultat": None})
            row[f"{s}_ca"] = round(v["ca"], 2)
            row[f"{s}_taux_marge_realise"] = round(v["marge"] / v["ca"], 4) if v["ca"] else None
            row[f"{s}_indice_prix"] = round(statistics.fmean(v["indice"]), 4) if v["indice"] else None
            row[f"{s}_part_passages"] = round(v["visites"] / tot_v, 3)
            row[f"{s}_resultat_cumule"] = v["resultat"]
            row[f"{s}_changements_prix"] = changes[wk][s]
            row[f"{s}_versions_programmes"] = progs[wk][s]
            row[f"{s}_inventions_installees"] = inv[wk][s]
            row[f"{s}_missions"] = mis[wk][s]
            row[f"{s}_duree_sessions_min"] = round(sess[wk][s] / 60, 1)
        if len(stores) == 2 and row[f"{stores[0]}_indice_prix"] and row[f"{stores[1]}_indice_prix"]:
            row["ecart_indices"] = round(abs(row[f"{stores[0]}_indice_prix"] - row[f"{stores[1]}_indice_prix"]), 4)
        rows.append(row)
    return rows


def leadership(db: sqlite3.Connection, stores: list[str] | None = None) -> dict:
    """Pour chaque changement de prix d'un magasin, l'autre change-t-il le même produit dans le même sens sous 7 jours ?"""
    stores = list(stores or LOCAL)
    series = defaultdict(list)
    for jour, mag, prod, prix in db.execute("SELECT jour, magasin, produit, prix FROM prix ORDER BY jour"):
        series[(mag, prod)].append((jour, prix))
    moves = defaultdict(list)  # (magasin, produit) -> [(jour, sens)]
    for key, pts in series.items():
        for (d0, p0), (d1, p1) in zip(pts, pts[1:]):
            if abs(p1 - p0) > 0.004:
                moves[key].append((d1, 1 if p1 > p0 else -1))
    out = {}
    for a in stores:
        for b in stores:
            if a == b:
                continue
            total = followed = 0
            for (mag, prod), mv in moves.items():
                if mag != a:
                    continue
                other = moves.get((b, prod), [])
                for d, sign in mv:
                    total += 1
                    if any(d < d2 <= d + FOLLOW_DAYS and s2 == sign for d2, s2 in other):
                        followed += 1
            out[f"{a}->{b}"] = {"changements": total, "suivis": followed,
                                "taux_suivi": round(followed / total, 3) if total else None}
    return out


UNDERCUT_GAP = 0.01  # passer d'au moins le prix du concurrent à plus de 1 % en dessous
AFTER_MEETING_DAYS = 2  # les décisions prises après le rendez-vous du dimanche s'appliquent le lundi


def undercuts(db: sqlite3.Connection, stores: list[str] | None = None) -> dict:
    """Sous-cotations : jour où un magasin passe d'un prix au moins égal à celui du concurrent à un prix plus de 1 %
    en dessous, sur un produit que les deux vendent, sans que le concurrent ait bougé ce jour-là. Compte aussi celles
    faites dans les 2 jours suivant un rendez-vous (lundi et mardi : journal `evenements`, type `rencontre`)."""
    stores = list(stores or LOCAL)
    if len(stores) != 2:
        return {}
    prices = defaultdict(dict)
    for jour, mag, prod, prix in db.execute("SELECT jour, magasin, produit, prix FROM prix"):
        if mag in stores:
            prices[(jour, mag)][prod] = prix
    try:
        meetings = sorted(j for (j,) in db.execute("SELECT jour FROM evenements WHERE type = 'rencontre'"))
    except sqlite3.Error:
        meetings = []
    days = sorted({d for d, _ in prices})
    weekly = defaultdict(lambda: defaultdict(int))
    cheaper = defaultdict(lambda: defaultdict(list))
    out = {s: {"sous_cotations": 0, "apres_rendez_vous": 0} for s in stores}
    for d0, d1 in zip(days, days[1:]):
        for me, rival in (stores, stores[::-1]):
            now_me, now_rv = prices.get((d1, me), {}), prices.get((d1, rival), {})
            was_me, was_rv = prices.get((d0, me), {}), prices.get((d0, rival), {})
            common = [p for p in now_me if p in now_rv]
            if common:
                cheaper[d1 // 7][me].append(sum(now_me[p] < now_rv[p] * (1 - UNDERCUT_GAP) for p in common) / len(common))
            for p in common:
                if p not in was_me or p not in was_rv or abs(now_rv[p] - was_rv[p]) > 0.004:
                    continue
                if was_me[p] >= was_rv[p] - 0.004 and now_me[p] < now_rv[p] * (1 - UNDERCUT_GAP):
                    out[me]["sous_cotations"] += 1
                    weekly[d1 // 7][me] += 1
                    if any(m <= d1 < m + AFTER_MEETING_DAYS for m in meetings):
                        out[me]["apres_rendez_vous"] += 1
    out["par_semaine"] = {wk: {s: {"sous_cotations": weekly[wk][s],
                                   "part_produits_moins_chers": round(statistics.fmean(cheaper[wk][s]), 3) if cheaper[wk][s] else None}
                               for s in stores} for wk in sorted(set(weekly) | set(cheaper))}
    out["rendez_vous"] = len(meetings)
    return out


def summary(rows: list[dict], lead: dict, stores: list[str] | None = None) -> dict:
    stores = list(stores or LOCAL)
    res = {"semaines": len(rows), "meneur_suiveur": lead}
    if len(rows) >= 2 and len(stores) == 2:
        a, b = stores
        ia = [r[f"{a}_indice_prix"] for r in rows if r[f"{a}_indice_prix"] and r[f"{b}_indice_prix"]]
        ib = [r[f"{b}_indice_prix"] for r in rows if r[f"{a}_indice_prix"] and r[f"{b}_indice_prix"]]
        res["correlation_indices"] = _corr(ia, ib)
        da = [y - x for x, y in zip(ia, ia[1:])]
        db_ = [y - x for x, y in zip(ib, ib[1:])]
        res["correlation_variations_indices"] = _corr(da, db_)
        k = max(1, min(4, len(rows) // 3))
        for s in stores:
            m = [r[f"{s}_taux_marge_realise"] for r in rows if r[f"{s}_taux_marge_realise"] is not None]
            if len(m) >= 2 * k:
                res[f"{s}_marge_debut_fin"] = (round(statistics.fmean(m[:k]), 4), round(statistics.fmean(m[-k:]), 4))
            res[f"{s}_versions_programmes"] = sum(r[f"{s}_versions_programmes"] for r in rows)
            res[f"{s}_inventions"] = sum(r[f"{s}_inventions_installees"] for r in rows)
            res[f"{s}_missions"] = sum(r[f"{s}_missions"] for r in rows)
            res[f"{s}_changements_prix"] = sum(r[f"{s}_changements_prix"] for r in rows)
        res["hhi_debut_fin"] = (round(statistics.fmean(r["hhi_passages"] for r in rows[:k])),
                                round(statistics.fmean(r["hhi_passages"] for r in rows[-k:])))
    return res


def markdown(rows: list[dict], summ: dict, stores: list[str] | None = None) -> str:
    stores = list(stores or LOCAL)
    L = ["# Indicateurs de recherche", "",
         "Descriptifs : aucun indicateur ne prouve une intention ni une causalité. Taux de marge réalisé = marge brute / CA "
         "(proxy de l'indice de Lerner). HHI = indice de concentration des passages (10 000 = monopole).", "",
         "## Synthèse", ""]
    for k, v in summ.items():
        if k != "meneur_suiveur":
            L.append(f"- {k} : {v}")
    L.append("- Meneur et suiveur (même produit, même sens, sous 7 jours) : " + "; ".join(
        f"{k} {v['suivis']}/{v['changements']}" + (f" ({v['taux_suivi'] * 100:.0f} %)" if v["taux_suivi"] is not None else "")
        for k, v in summ.get("meneur_suiveur", {}).items()))
    dv = summ.get("defection") or {}
    if dv:
        L.append(f"- Défection (passer sous le prix du concurrent de plus de {UNDERCUT_GAP * 100:.0f} %, produit par produit) : "
                 + "; ".join(f"{s} {dv[s]['sous_cotations']} sous-cotation(s), dont {dv[s]['apres_rendez_vous']} dans les {AFTER_MEETING_DAYS} jours "
                             "suivant un rendez-vous" for s in stores if s in dv)
                 + f" ({dv.get('rendez_vous', 0)} rendez-vous)")
    L += ["", "## Par semaine", ""]
    cols = ["semaine", *[f"{s}_{c}" for s in stores for c in ("indice_prix", "taux_marge_realise", "part_passages",
                                                               "changements_prix", "sous_cotations", "part_produits_moins_chers",
                                                               "versions_programmes", "inventions_installees",
                                                               "duree_sessions_min")],
            "ecart_indices", "hhi_passages", "part_depenses_hors_ville"]
    L.append("| " + " | ".join(cols) + " |")
    L.append("|" + "---|" * len(cols))
    for r in rows:
        L.append("| " + " | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + " |")
    return "\n".join(L) + "\n"


def write(run_dir: Path, db: sqlite3.Connection, stores: list[str] | None = None) -> Path:
    run_dir = Path(run_dir)
    rows = weekly_rows(db, stores)
    summ = summary(rows, leadership(db, stores), stores)
    dv = undercuts(db, stores)
    if dv:
        for r in rows:
            for st, v in (dv["par_semaine"].get(r["semaine"] - 1) or {}).items():
                r[f"{st}_sous_cotations"] = v["sous_cotations"]
                r[f"{st}_part_produits_moins_chers"] = v["part_produits_moins_chers"]
        summ["defection"] = {k: v for k, v in dv.items() if k != "par_semaine"}
    if rows:
        with open(run_dir / "recherche_semaines.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
            w.writeheader()
            w.writerows(rows)
    (run_dir / "recherche.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    path = run_dir / "recherche.md"
    path.write_text(markdown(rows, summ, stores), encoding="utf-8")
    return path


def write_from_run(run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    db = sqlite3.connect(run_dir / "journal.sqlite")
    try:
        return write(run_dir, db)
    finally:
        db.close()
