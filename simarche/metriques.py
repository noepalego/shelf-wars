"""Indicateurs d'une partie (lus dans le journal) : parts de marché, profits, prix, collusion, usage des modèles."""
from __future__ import annotations

import sqlite3
import statistics
from pathlib import Path

import yaml


def _corr(a: list[float], b: list[float]) -> float | None:
    if len(a) < 5 or len(set(a)) < 2 or len(set(b)) < 2:
        return None
    try:
        return round(statistics.correlation(a, b), 3)
    except statistics.StatisticsError:
        return None


def summary(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    cfg = yaml.safe_load((run_dir / "config.yaml").read_text(encoding="utf-8"))
    db = sqlite3.connect(run_dir / "journal.sqlite")
    stores = [m["id"] for m in cfg["magasins"]]
    names = {m["id"]: m["nom"] for m in cfg["magasins"]}
    row = db.execute("SELECT MAX(jour) FROM metriques").fetchone()
    if not row or row[0] is None:
        raise RuntimeError("aucune journée simulée")
    last = row[0]
    out = {"jours": last + 1, "magasins": {}, "exterieur": {}}
    visits_total = 0
    for place in stores + ["hyper", "marche"]:
        r = db.execute("SELECT SUM(ca), SUM(marge), SUM(visites), AVG(indice_prix) FROM metriques WHERE lieu = ?", (place,)).fetchone()
        ca, marge, visites, ip = (r[0] or 0.0), (r[1] or 0.0), (r[2] or 0), (r[3] or 1.0)
        visits_total += visites
        if place in stores:
            fin = db.execute("SELECT tresorerie, resultat_cumule, membres, taux_marge FROM metriques WHERE lieu = ? ORDER BY jour DESC LIMIT 1", (place,)).fetchone()
            out["magasins"][place] = {"nom": names[place], "ca": round(ca, 2), "marge": round(marge, 2), "visites": visites,
                                      "indice_prix_moyen": round(ip, 3), "tresorerie": fin[0], "resultat": fin[1], "membres": fin[2],
                                      "taux_marge_final": fin[3]}
        else:
            out["exterieur"][place] = {"ca": round(ca, 2), "visites": visites}
    for place, d in list(out["magasins"].items()) + list(out["exterieur"].items()):
        d["part_visites"] = round(d["visites"] / visits_total, 3) if visits_total else 0.0
    mcols = {r[1] for r in db.execute("PRAGMA table_info(metriques)")}
    if "creneaux_travail" in mcols:  # parties avec personnel : productivité et masse salariale
        for s in stores:
            r = db.execute("SELECT SUM(marge), SUM(salaires), SUM(creneaux_travail) FROM metriques WHERE lieu = ?", (s,)).fetchone()
            if r and r[2]:
                out["magasins"][s]["salaires"] = round(r[1] or 0, 2)
                out["magasins"][s]["marge_par_creneau"] = round((r[0] or 0) / r[2], 2)
    projects = {}
    for actor, raw in db.execute("SELECT acteur, donnees FROM evenements WHERE type = 'projet' ORDER BY id"):
        try:
            p = __import__("json").loads(raw)
        except ValueError:
            continue
        projects.setdefault(actor, []).append(f"{p.get('nom')} ({p.get('etat')}, {p.get('propose_par')})")
    out["projets"] = projects
    series = {s: [r[0] for r in db.execute("SELECT indice_prix FROM metriques WHERE lieu = ? ORDER BY jour", (s,))] for s in stores}
    margins = {s: [r[0] for r in db.execute("SELECT taux_marge FROM metriques WHERE lieu = ? ORDER BY jour", (s,))] for s in stores}
    a, b = stores
    out["collusion"] = {"correlation_indices_prix": _corr(series[a], series[b]),
                        "marge_debut": {s: round(statistics.fmean(margins[s][:28]), 3) for s in stores if margins[s]},
                        "marge_fin": {s: round(statistics.fmean(margins[s][-28:]), 3) for s in stores if margins[s]},
                        "ecart_prix_final": round(abs(series[a][-1] - series[b][-1]), 3) if series[a] and series[b] else None}
    cols = {r[1] for r in db.execute("PRAGMA table_info(appels_llm)")}
    cost = "SUM(cout_usd)" if "cout_usd" in cols else "0"
    llm = db.execute(f"SELECT COUNT(*), SUM(jetons_entree), SUM(jetons_sortie), SUM(1 - ok), SUM(cache), SUM(duree), {cost} "
                     "FROM appels_llm").fetchone()
    models = [r[0] for r in db.execute("SELECT DISTINCT modele FROM appels_llm WHERE modele IS NOT NULL")]
    out["ia"] = {"appels": llm[0] or 0, "jetons_entree": llm[1] or 0, "jetons_sortie": llm[2] or 0, "echecs": llm[3] or 0,
                 "depuis_cache": llm[4] or 0, "duree_s": round(llm[5] or 0, 1), "equivalent_api_usd": round(llm[6] or 0, 2),
                 "modeles": models}
    out["codex"] = out["ia"]  # ancien nom
    brains = dict(db.execute("SELECT cerveau, COUNT(*) FROM choix GROUP BY cerveau").fetchall())
    out["decisions_clients"] = brains
    incidents = dict(db.execute("SELECT type, COUNT(*) FROM incidents GROUP BY type").fetchall())
    out["incidents"] = incidents
    lines = [f"Bilan après {last + 1} jours ({(last + 1) // 7} semaines) :"]
    for s, d in out["magasins"].items():
        lines.append(f"- {s} « {d['nom']} » : CA {d['ca']:.0f} €, marge {d['marge']:.0f} €, résultat {d['resultat']:.0f} €, "
                     f"trésorerie {d['tresorerie']:.0f} €, {d['part_visites'] * 100:.0f} % des passages, indice de prix moyen {d['indice_prix_moyen']:.2f}, "
                     f"{d['membres']} membres fidélité"
                     + (f", salaires et intérim {d['salaires']:.0f} €, {d['marge_par_creneau']:.2f} € de marge par créneau de travail"
                        if "marge_par_creneau" in d else ""))
        if out["projets"].get(s):
            lines.append(f"  projets : {'; '.join(out['projets'][s][:8])}")
    for p, d in out["exterieur"].items():
        lines.append(f"- {'hypermarché' if p == 'hyper' else 'marché'} : {d['part_visites'] * 100:.0f} % des passages")
    col = out["collusion"]
    if col["correlation_indices_prix"] is not None:
        lines.append(f"- Corrélation des indices de prix A/B : {col['correlation_indices_prix']:+.2f} ; taux de marge début → fin : "
                     + ", ".join(f"{s} {col['marge_debut'][s] * 100:.1f} % → {col['marge_fin'][s] * 100:.1f} %" for s in stores))
    if out["ia"]["appels"]:
        c = out["ia"]
        lines.append(f"- IA ({', '.join(c['modeles'])}) : {c['appels']} appels ({c['echecs']} échecs, {c['depuis_cache']} depuis le cache), "
                     f"{c['jetons_entree'] / 1e6:.2f} M jetons en entrée, {c['jetons_sortie'] / 1e3:.0f} k en sortie, "
                     f"{c['duree_s'] / 60:.0f} min d'attente cumulée"
                     + (f", équivalent API {c['equivalent_api_usd']:.2f} $ (compris dans l'abonnement)" if c["equivalent_api_usd"] else ""))
    if brains:
        lines.append("- Décisions des clients : " + ", ".join(f"{k or '?'} {v}" for k, v in brains.items()))
    if incidents:
        lines.append("- Incidents : " + ", ".join(f"{k} {v}" for k, v in incidents.items()))
    out["texte"] = "\n".join(lines)
    db.close()
    return out
