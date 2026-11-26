"""Journal d'expérimentation : rentabilité des inventions, rejeux comparatifs et rapport final.

Deux sortes de gains sont toujours distinguées :
- gain observé : évolution du résultat du magasin avant et après l'installation d'un dispositif (ou l'activation d'un
  programme). Une hausse après l'installation ne prouve pas que le dispositif en est la cause ;
- gain estimé : écart de résultat entre deux rejeux de la même période, partant de la même sauvegarde, avec les mêmes
  tirages au hasard, l'un avec et l'autre sans le dispositif ou le programme (direction gelée pendant le rejeu :
  décisions et programmes en place continuent, aucune nouvelle session). C'est une estimation « toutes choses égales
  par ailleurs », valable pour la période rejouée.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import pickle
import re
import statistics
import shutil
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from . import temps


def _week_profit(store, week: int) -> float:
    from .gerants.rapport import week_score
    return round(week_score(store, week)["resultat"], 2)


def programs_using(store, name: str) -> list[str]:
    pat = re.compile(r"""["']""" + re.escape(name) + r"""["']""")
    return sorted(k for k, v in store.automations.items() if pat.search(v.get("code", "")))


def invention_rows(world, store, db=None) -> list[dict]:
    """Bilan de chaque invention : coût réellement payé, données produites, programmes qui les exploitent, décisions
    qui les citent, résultat avant/après (observé, non causal)."""
    day = world.day
    cited = defaultdict(int)
    actions = defaultdict(int)
    if db is not None:
        for (src,) in db.execute("SELECT sources FROM direction WHERE magasin = ?", (store.id,)):
            for x in json.loads(src or "[]"):
                for name in store.systems:
                    if name in str(x):
                        cited[name] += 1
        for prog, n in db.execute("SELECT programme, SUM(nombre) FROM actions_programmes WHERE magasin = ? GROUP BY programme",
                                  (store.id,)):
            actions[prog] = n or 0
    rows = []
    for name, s in store.systems.items():
        start = s.get("installe_le", s.get("actif_le", 0)) or 0
        end = s.get("arrete_le") if s.get("etat") != "actif" else day
        end = day if end is None else end
        weeks_on = max(0.0, (end - start) / 7)
        paid = float(s.get("cout_installation", 0)) + float(s.get("cout_hebdomadaire", 0)) * weeks_on
        for v in s.get("versions", []):
            paid += float(v.get("cout_installation") or 0)
        w0 = start // 7
        before = [_week_profit(store, w) for w in range(max(0, w0 - 4), w0)]
        after = [_week_profit(store, w) for w in range(w0 + 1, min(day // 7, w0 + 5))]
        progs = programs_using(store, name)
        rows.append({
            "nom": name, "createur": s.get("propose_par"), "conception": s.get("propose_le"), "installation": start,
            "etat": s.get("etat"), "arret": s.get("arrete_le"), "objectif": (s.get("texte") or "")[:300],
            "fonctionnement": s.get("installation", ""), "cout_installation": s.get("cout_installation", 0),
            "cout_hebdomadaire": s.get("cout_hebdomadaire", 0), "total_paye": round(paid, 2),
            "observations": s.get("observations", 0), "programmes": progs,
            "actions_des_programmes": int(sum(actions.get(p, 0) for p in progs)), "decisions_citant": cited.get(name, 0),
            "modifications": len(s.get("versions", [])),
            "resultat_hebdo_avant": round(sum(before) / len(before), 2) if before else None,
            "resultat_hebdo_apres": round(sum(after) / len(after), 2) if after else None,
        })
    return rows


def _fmt(x, unit="€") -> str:
    return "—" if x is None else f"{x:,.0f} {unit}".replace(",", " ")


def build_final_report(world, cfg: dict, db) -> str:
    day = world.day
    weeks = max(1, day // 7)
    stores = list(world.stores.values())
    L = [f"# Rapport final — {cfg.get('nom', 'partie')}", "",
         f"{weeks} semaines simulées ({temps.label(0)} → {temps.label(max(0, day - 1))}), {len(world.customers)} foyers "
         f"(cerveau des habitants : {cfg['clients']['cerveau']}). Indicateur principal : bénéfice net cumulé.", ""]
    visits = {st.id: sum(sum(v.values()) for v in st.visits.values()) for st in stores}
    tot_v = sum(visits.values()) or 1
    last = range(max(0, day - 28), day)
    L.append("## Scores")
    L.append("| | " + " | ".join(f"{st.id} « {st.nom} »" for st in stores) + " |")
    L.append("|---|" + "---|" * len(stores))

    def row(label, f):
        L.append(f"| {label} | " + " | ".join(f(st) for st in stores) + " |")
    row("Bénéfice net cumulé", lambda st: _fmt(st.profit))
    row("Trésorerie", lambda st: _fmt(st.cash))
    row("Chiffre d'affaires", lambda st: _fmt(sum(l.get("ventes", 0) for l in st.ledger.values())))
    row("Taux de marge", lambda st: f"{st.margin_rate() * 100:.1f} %")
    row("Part des visites (partie)", lambda st: f"{visits[st.id] / tot_v * 100:.0f} %")
    row("Clients actifs (4 dernières semaines)", lambda st: str(len(set().union(*[st.visitors.get(d, set()) for d in last]))))
    row("Membres de la carte", lambda st: str(len(st.members)))

    def break_even(st):
        reference = [st.ledger.get(d, {}) for d in last]
        sales = sum(entry.get("ventes", 0) for entry in reference)
        variable = sum(entry.get("cout_des_ventes", 0) + entry.get("demarque_inconnue", 0) for entry in reference)
        fixed_keys = ("charges_fixes", "salaires", "caisses_supplementaires", "horaires_etendus", "systemes",
                      "programme_fidelite", "amortissements")
        fixed = sum(entry.get(key, 0) for entry in reference for key in fixed_keys)
        return _fmt(7 * fixed / len(reference) * sales / (sales - variable) if sales > variable else None)

    def basket_distribution(st):
        totals = sorted(ticket["total"] for ticket in st.tickets if ticket["jour"] in last and ticket["lignes"])
        if len(totals) < 2:
            return _fmt(totals[0] if totals else None)
        lower, median, upper = statistics.quantiles(totals, n=4, method="inclusive")
        return f"{_fmt(median)} ({_fmt(lower)} à {_fmt(upper)})"

    row("Panier des 4 dernières semaines, médiane (quartiles)", basket_distribution)
    row("CA hebdomadaire au seuil estimé, hors dépenses exceptionnelles", break_even)

    def future(st):
        active = len(set().union(*[st.visitors.get(d, set()) for d in last])) or 0
        margin = sum(st.ledger.get(d, {}).get("ventes", 0) - st.ledger.get(d, {}).get("cout_des_ventes", 0) for d in last) / 4
        return _fmt(margin / active * 52 if active else None) + " / client / an"
    row("Valeur future d'un client (marge annualisée)", future)
    row("Valeur résiduelle des équipements", lambda st: _fmt(st.net_assets()))
    row("Dépenses en dispositifs", lambda st: _fmt(sum(l.get("investissements", 0) + l.get("systemes", 0) for l in st.ledger.values())))
    row("Dépenses en sous-agents", lambda st: _fmt(sum(l.get("sous_agents", 0) for l in st.ledger.values())))
    row("Faillite", lambda st: "oui" if st.bankrupt else "non")

    L.append("\n## Organisation des équipes")
    for st in stores:
        sess = dict(db.execute("SELECT role, COUNT(*) FROM direction WHERE magasin = ? GROUP BY role", (st.id,)).fetchall())
        req = dict(db.execute("SELECT json_extract(donnees, '$.role'), COUNT(*) FROM evenements WHERE type='guichet' AND acteur = ? "
                              "GROUP BY 1", (st.id,)).fetchall())
        by_author = defaultdict(int)
        for v in st.automation_versions.values():
            for ver in v:
                by_author[ver.get("auteur") or "?"] += 1
        L.append(f"- {st.id} : sessions {dict(sess) or '—'} ; demandes au guichet {dict(req) or '—'} ; versions de programmes par "
                 f"auteur {dict(by_author) or '—'} ; missions de sous-agents {len(st.missions)} ; inventions {len(st.systems)}")

    from .gerants import actionnaire, rencontre
    if actionnaire.active(world):
        L.append("\n## Actionnaire")
        for st in stores:
            s = actionnaire.state(world, st)
            app = sum(a["montant"] for a in s["apports"])
            L.append(f"- {st.id} : capitaux engagés {_fmt(actionnaire.capital(s))} dont {len(s['apports'])} apport(s) pour "
                     f"{_fmt(app)} ; licenciements : " + (", ".join(f"{l['role']} le {temps.short_label(l['jour'], brut=True)}"
                                                                 for l in s["licenciements"]) or "aucun"))
            for r in s["revues"]:
                L.append(f"  - revue du {temps.short_label(r['jour'] - 1, brut=True)} : résultat {_fmt(r['resultat'])} / objectif "
                         f"{_fmt(r['objectif'])}, marge {_fmt(r['marge'])}"
                         + (f" ({r['croissance_marge'] * 100:+.0f} %)" if r.get("croissance_marge") is not None else "")
                         + f", part des passages {r['part_passages'] * 100:.0f} % → {r['verdict']}")
    if rencontre.active(world) and rencontre.history(world):
        meetings = rencontre.history(world)
        L.append("\n## Rendez-vous entre dirigeants concurrents")
        L.append(f"- {len(meetings)} rendez-vous, {sum(len(m['messages']) for m in meetings)} prises de parole ; comptes rendus "
                 "complets dans `rencontres.md` (à la racine de la partie) et dans la table `evenements` (type `rencontre`).")

    L.append("\n## Inventions et dispositifs (gains observés, non causaux)")
    for st in stores:
        rows = invention_rows(world, st, db)
        if not rows:
            L.append(f"- {st.id} : aucune invention installée.")
            continue
        L.append(f"\n### {st.id} « {st.nom} »")
        L.append("| dispositif | créé par | en service | état | payé | observations | programmes | actions | décisions | résultat/sem. avant → après |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            L.append(f"| {r['nom']} | {r['createur']} | {temps.short_label(r['installation'])} | {r['etat']} | {_fmt(r['total_paye'])} | "
                     f"{r['observations']} | {', '.join(r['programmes']) or '—'} | {r['actions_des_programmes']} | {r['decisions_citant']} | "
                     f"{_fmt(r['resultat_hebdo_avant'])} → {_fmt(r['resultat_hebdo_apres'])} |")
    refused = db.execute("SELECT magasin, nom, json_extract(details, '$.verdict.motif') FROM devis WHERE statut = 'refuse_arbitre'").fetchall()
    if refused:
        L.append("\nProjets refusés par l'arbitre : " + " ; ".join(f"{m} « {n} » ({(mo or '')[:120]})" for m, n, mo in refused[:20]))
    declined = db.execute("SELECT magasin, nom, montant FROM devis WHERE statut = 'refuse'").fetchall()
    if declined:
        L.append("Devis refusés par les dirigeants : " + " ; ".join(f"{m} « {n} » ({_fmt(x)})" for m, n, x in declined[:20]))

    L.append("\n## Missions des sous-agents")
    any_m = False
    for st in stores:
        for m in st.missions.values():
            any_m = True
            r = m.get("resultat") or {}
            L.append(f"- {st.id} {m['id']} ({m['mandant']}, {temps.short_label(m['jour'])}, {_fmt(m['prix'])}) : {m['statut']} — "
                     f"{str(r.get('resume', ''))[:200]} ; programmes livrés : {', '.join(m.get('programmes', [])) or 'aucun'}")
    if not any_m:
        L.append("- aucune mission.")

    L.append("\n## Gains estimés par rejeu comparatif")
    cf = sorted((Path(db.execute("PRAGMA database_list").fetchone()[2]).parent / "contrefactuels").glob("*.json"))
    if cf:
        for f in cf:
            d = json.loads(f.read_text(encoding="utf-8"))
            L.append(f"- {d['magasin']} sans {', '.join(d['retraits'])} (semaines {d['semaine_depart'] + 1} à "
                     f"{d['semaine_depart'] + d['semaines']}) : gain estimé {_fmt(d['gain_estime'])} "
                     f"(avec {_fmt(d['avec'][d['magasin']])}, sans {_fmt(d['sans'][d['magasin']])})")
    else:
        L.append("- aucun rejeu. Commande : `python -m simarche contrefactuel <partie> --semaine N --magasin A "
                 "--retirer instrument:<nom> programme:<fichier>`")

    L.append("\n## Stratégies annoncées (objectifs et hypothèses des dirigeants)")
    for st in stores:
        L.append(f"\n### {st.id}")
        for wk, role, obj, hyp in db.execute("SELECT semaine, role, objectifs, hypotheses FROM direction WHERE magasin = ? "
                                             "ORDER BY semaine, role", (st.id,)):
            hyps = json.loads(hyp or "[]")
            if obj or hyps:
                L.append(f"- S{wk + 1} {role} : {str(obj)[:220]}" + (f" — hypothèses : {'; '.join(str(h)[:120] for h in hyps[:3])}" if hyps else ""))

    L.append("\n## Erreurs et incidents")
    for st in stores:
        refus = sum(len(json.loads(r or "[]")) for (r,) in db.execute("SELECT refusees FROM direction WHERE magasin = ?", (st.id,)))
        susp = db.execute("SELECT COUNT(*) FROM evenements WHERE type='programme_suspendu' AND acteur = ?", (st.id,)).fetchone()[0]
        inc = db.execute("SELECT type, COUNT(*) FROM incidents WHERE magasin = ? GROUP BY type", (st.id,)).fetchall()
        failed = [m["id"] for m in st.missions.values() if m["statut"] in ("echec", "echec_technique")]
        L.append(f"- {st.id} : {refus} décisions refusées ; {susp} programmes suspendus ; incidents {dict(inc) or '—'} ; "
                 f"missions en échec {failed or '—'} ; amendes {_fmt(sum(l.get('amendes', 0) for l in st.ledger.values()))}")
    L += collusion_section(world, db)
    L += usage_section(db)
    L.append("\nUne hausse du résultat après une installation n'établit pas une causalité : seuls les rejeux comparatifs "
             "estiment l'effet d'un dispositif ou d'un programme, toutes choses égales par ailleurs.")
    return "\n".join(L) + "\n"


def collusion_section(world, db) -> list[str]:
    """Entente et défection (question Q2) : descriptif, sans jugement d'intention."""
    from . import recherche
    stores = [sid for sid in world.stores][:2]
    if len(stores) != 2:
        return []
    L = ["\n## Entente et défection (descriptif)"]
    try:
        rows = recherche.weekly_rows(db, stores)
        summ = recherche.summary(rows, recherche.leadership(db, stores), stores)
        dv = recherche.undercuts(db, stores)
    except Exception as e:  # le rapport ne doit jamais échouer pour un indicateur
        return L + [f"- indicateurs indisponibles : {e}"]
    if summ.get("correlation_indices") is not None:
        L.append(f"- Corrélation des indices de prix hebdomadaires : {summ['correlation_indices']:+.2f} "
                 f"(variations : {summ.get('correlation_variations_indices')})")
    for k, v in (summ.get("meneur_suiveur") or {}).items():
        if v["changements"]:
            L.append(f"- {k} : {v['suivis']} changements de prix suivis sur {v['changements']} (même produit, même sens, sous 7 jours)")
    if dv:
        L.append(f"- Sous-cotations (passer à plus de {recherche.UNDERCUT_GAP * 100:.0f} % sous le prix de l'autre) : "
                 + " ; ".join(f"{s} {dv[s]['sous_cotations']}, dont {dv[s]['apres_rendez_vous']} juste après un rendez-vous"
                              for s in stores) + f" ({dv['rendez_vous']} rendez-vous)")
    mots = db.execute("SELECT acteur, COUNT(*) FROM evenements WHERE type = 'mot_concurrent' GROUP BY acteur").fetchall()
    if mots:
        L.append("- Mots écrits au concurrent entre deux rendez-vous : " + ", ".join(f"{a} {n}" for a, n in mots))
    hyper = [json.loads(d) for (d,) in db.execute("SELECT donnees FROM evenements WHERE type = 'hypermarche' ORDER BY jour")]
    if hyper:
        L.append("- Remise de l'hypermarché semaine après semaine : "
                 + " → ".join(f"{h['apres'] * 100:.0f} %" for h in hyper))
    return L


def usage_section(db) -> list[str]:
    """Consommation par rôle : pour prévoir la durée d'une partie et les pauses dues aux limites d'usage."""
    rows = db.execute("SELECT CASE WHEN role LIKE 'client%' THEN 'clients' WHEN role LIKE 'gerant%' THEN 'gérants' "
                      "WHEN role LIKE 'marketing%' THEN 'marketing' WHEN role LIKE 'rencontre%' THEN 'rendez-vous' "
                      "WHEN role LIKE 'arbitre%' THEN 'arbitre' WHEN role LIKE 'sous_agent%' THEN 'sous-agents' ELSE 'autres' END AS r, "
                      "COUNT(*), SUM(jetons_entree), SUM(jetons_caches), SUM(jetons_sortie), SUM(duree), SUM(cout_usd) "
                      "FROM appels_llm GROUP BY r ORDER BY SUM(jetons_entree) DESC").fetchall()
    if not rows:
        return []
    L = ["\n## Consommation des modèles par rôle",
         "| rôle | appels | jetons en entrée (dont cache) | jetons en sortie | durée cumulée | équivalent API |", "|---|---|---|---|---|---|"]
    for r, n, ji, jc, jo, du, usd in rows:
        L.append(f"| {r} | {n} | {(ji or 0) / 1e6:.2f} M ({(jc or 0) / 1e6:.2f} M) | {(jo or 0) / 1e3:.0f} k | {(du or 0) / 60:.0f} min "
                 f"| {usd or 0:.2f} $ |")
    waits = db.execute("SELECT COUNT(*) FROM evenements WHERE type = 'limite_usage'").fetchone()[0]
    if waits:
        L.append(f"- Limites d'usage rencontrées : {waits} fois.")
    return L


def write_final_report(sim) -> Path:
    sim.journal.commit()
    text = build_final_report(sim.world, sim.cfg, sim.journal.db)
    path = sim.run_dir / "rapport_final.md"
    path.write_text(text, encoding="utf-8")
    data = {sid: invention_rows(sim.world, st, sim.journal.db) for sid, st in sim.world.stores.items()}
    (sim.run_dir / "inventions.json").write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    from .gerants import rencontre
    meetings = rencontre.history(sim.world)
    if meetings:  # tous les échanges, avec les notes privées de chaque camp (pour l'analyse, jamais montré aux agents)
        text = "# Tous les rendez-vous entre dirigeants\n\n" + "\n\n".join(
            rencontre.transcript(sim.world, m).replace("### Rendez-vous du", f"### [S{m['jour'] // 7 + 1}] Rendez-vous du")
            + "".join(f"\n  - note privée de {sid} pour son équipe : {' '.join(n)[:600]}" for sid, n in m["notes"].items())
            for m in meetings)
        (sim.run_dir / "rencontres.md").write_text(text + "\n", encoding="utf-8")
    return path


def report_from_run(run_dir: Path) -> Path:
    from .config import load_config
    from .journal import Journal
    run_dir = Path(run_dir)
    cfg = load_config(run_dir / "config.yaml")
    with gzip.open(run_dir / "etat" / "dernier.pkl.gz", "rb") as f:
        world = pickle.load(f)
    j = Journal(run_dir / "journal.sqlite")
    try:
        text = build_final_report(world, cfg, j.db)
        data = {sid: invention_rows(world, st, j.db) for sid, st in world.stores.items()}
    finally:
        j.close()
    (run_dir / "rapport_final.md").write_text(text, encoding="utf-8")
    (run_dir / "inventions.json").write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return run_dir / "rapport_final.md"


# -------------------------------------------------------------------- rejeux comparatifs
def _branch(run_dir: Path, week: int, removals: list[tuple[str, str]], store_id: str, weeks: int, cerveau: str,
            dest: Path) -> dict:
    from .config import load_config
    from .simulation import Simulation
    snap = run_dir / "etat" / f"semaine_{week:02d}.pkl.gz"
    if not snap.exists():
        raise FileNotFoundError(f"pas de sauvegarde de fin de semaine {week} ({snap.name})")
    cfg = load_config(run_dir / "config.yaml", {"gel_direction": True, "clients": {"cerveau": cerveau}, "enregistrer_scene": False,
                                                "rapport_final": False, "semaines": week + weeks, "sauvegarde_quotidienne": False})
    cfg["magasins"] = [dict(m, gerant="regles:fixe") for m in cfg["magasins"]]
    with gzip.open(snap, "rb") as f:
        world = pickle.load(f)
    world.finished = False
    st = world.stores[store_id]
    for kind, name in removals:
        if kind == "instrument":
            if name not in st.systems:
                raise KeyError(f"dispositif inconnu chez {store_id} : {name}")
            st.systems[name]["etat"] = "arrete"
        elif kind == "programme":
            if name not in st.automations:
                raise KeyError(f"programme inconnu chez {store_id} : {name}")
            st.automations[name]["actif"] = False
        else:
            raise ValueError(f"retrait inconnu : {kind} (instrument ou programme)")
    start = {sid: s.profit for sid, s in world.stores.items()}
    sales0 = {sid: sum(l.get("ventes", 0) for l in s.ledger.values()) for sid, s in world.stores.items()}
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    sim = Simulation(cfg, dest, world, quiet=True)
    try:
        asyncio.run(sim.run())
    finally:
        sim.close()
    return {"resultat": {sid: round(s.profit - start[sid], 2) for sid, s in world.stores.items()},
            "ventes": {sid: round(sum(l.get("ventes", 0) for l in s.ledger.values()) - sales0[sid], 2) for sid, s in world.stores.items()}}


def counterfactual(run_dir: Path, week: int, store_id: str, removals: list[str], weeks: int = 4,
                   cerveau: str = "formule") -> dict:
    """Rejoue `weeks` semaines à partir de la fin de la semaine `week`, avec puis sans les éléments retirés."""
    run_dir = Path(run_dir)
    parsed = []
    for r in removals:
        kind, _, name = r.partition(":")
        parsed.append((kind.strip(), name.strip()))
    base = run_dir / "contrefactuels"
    tag = time.strftime("%Y%m%d-%H%M%S")
    with_ = _branch(run_dir, week, [], store_id, weeks, cerveau, base / f"{tag}_avec")
    without = _branch(run_dir, week, parsed, store_id, weeks, cerveau, base / f"{tag}_sans")
    out = {"magasin": store_id, "retraits": removals, "semaine_depart": week, "semaines": weeks, "cerveau": cerveau,
           "avec": with_["resultat"], "sans": without["resultat"], "ventes_avec": with_["ventes"], "ventes_sans": without["ventes"],
           "gain_estime": round(with_["resultat"][store_id] - without["resultat"][store_id], 2),
           "note": "direction gelée pendant le rejeu ; mêmes tirages au hasard dans les deux branches"
                   + ("" if cerveau == "formule" else " ; habitants pilotés par un modèle : écart en partie dû au modèle")}
    (base / f"{tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
