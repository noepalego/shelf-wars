"""Rapport hebdomadaire du gérant et export de ses données (uniquement celles de son magasin + infos publiques)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .. import personnel, temps
from ..catalogue import PRODUCTS, RAYONS
from ..enquetes import disclosed_answers
from ..lois import min_price
from . import projets
from .automatisations import API_DOC

NON_CHARGES = ("ventes", "cout_des_ventes", "achats_marchandises", "visites", "clients_uniques", "remises_coupons", "remises_points",
               "acquisitions_immobilisees")


def week_score(store, week: int) -> dict:
    """Résultat, marge et productivité d'une semaine (créneaux de travail : équipe de base, salariés, intérim)."""
    t = store.week_totals(week)
    ca, cogs = t.get("ventes", 0.0), t.get("cout_des_ventes", 0.0)
    charges = sum(v for k, v in t.items() if k not in NON_CHARGES) - t.get("acquisitions_immobilisees", 0)
    work = {"creneaux_base": 0, "creneaux_salaries": 0, "creneaux_interim": 0, "absences": 0}
    for d in range(week * 7, week * 7 + 7):
        for k in work:
            work[k] += store.work_log.get(d, {}).get(k, 0)
    if not work["creneaux_base"]:  # semaines jouées avant le suivi du personnel
        work["creneaux_base"] = sum(1 for d in range(week * 7, week * 7 + 7) for s in ("matin", "midi", "soir")
                                    if temps.store_open(d, s))
        work["creneaux_interim"] = int(round(t.get("caisses_supplementaires", 0) / max(1e-9, store.cfg["economie"]["cout_caisse_supplementaire"])))
    shifts = work["creneaux_base"] + work["creneaux_salaries"] + work["creneaux_interim"]
    return {"ca": ca, "marge": ca - cogs, "charges": charges, "resultat": ca - cogs - charges, "creneaux": shifts, **work,
            "marge_par_creneau": (ca - cogs) / shifts if shifts else 0.0, "ca_par_creneau": ca / shifts if shifts else 0.0,
            "visites": t.get("visites", 0), "clients": t.get("clients_uniques", 0)}


def _csv(path: Path, header: list[str], rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def e(x: float) -> str:
    return f"{x:,.2f} €".replace(",", " ").replace(".", ",")


def condensed_notes(notes: list[str], n: int) -> list[str]:
    """Les `n` dernières notes distinctes, dans l'ordre de leur dernière apparition, avec « (×k) » quand une même note
    revient k fois : un programme qui écrit la même ligne chaque matin ne chasse plus les autres notes du rapport."""
    count: dict = {}
    last: dict = {}
    for i, x in enumerate(notes):
        count[x] = count.get(x, 0) + 1
        last[x] = i
    return [x + (f" (×{count[x]})" if count[x] > 1 else "") for x in sorted(last, key=last.get)[-n:]]


def build_report(world, store, role: str | None = None) -> str:
    day = world.day
    week = day // 7  # semaine qui commence (0 = première)
    last = week - 1
    other = world.other_store(store.id)
    public_other = world.public_information(other.id)
    L = []
    L.append(f"# Rapport du magasin {store.id} « {store.nom} » — {temps.label(day)}")
    L.append(f"- Valeur comptable des équipements : {e(store.net_assets())}")
    cold_space = store.cold_space(day)
    if cold_space is not None:
        L.append(f"- Stockage du frais : {cold_space} places libres à partir de demain, commandes en cours comprises.")
    L.append("- Créneaux ouverts cette semaine : " + " | ".join(
        f"{temps.day_name(d)} " + (", ".join(s for s in temps.slots(world.cfg) if store.is_open(d, s)) or "fermé")
        for d in range(week * 7, week * 7 + 7)))
    from . import actionnaire, rencontre
    if actionnaire.active(world):
        L += actionnaire.report_lines(world, store)
    if rencontre.active(world) and rencontre.history(world):
        L.append("\n## Rendez-vous avec le concurrent\n- Compte rendu du dernier échange (et des précédents) : `rencontres.md`.")
    if week == 0:
        L.append("\nC'est le début : les deux magasins partent à égalité (mêmes prix conseillés, même assortiment, même plan de magasin, "
                 f"même trésorerie de {e(store.cash)}). Prends tes premières décisions pour la semaine qui commence.")
    else:
        L.append("\n## Le score : l'argent gagné")
        L.append(f"- Résultat cumulé depuis le début : {e(store.profit)} ; trésorerie : {e(store.cash)}")
        scores = [week_score(store, w) for w in range(max(0, last - 3), last + 1)]
        L.append("- Résultat par semaine (les plus récentes à droite) : " + " | ".join(
            f"{temps.week_tag(w)} {e(s['resultat'])}" for w, s in zip(range(max(0, last - 3), last + 1), scores)))
        if last >= 1:  # tout l'historique : de quoi distinguer une tendance d'un effet de calendrier (paies, mois)
            L.append("- Toutes les semaines depuis l'ouverture (chiffre d'affaires · passages · marge brute) : " + " | ".join(
                f"{temps.week_tag(w)} {ws['ca']:,.0f} € · {ws['visites']} · {ws['marge']:,.0f} €".replace(",", " ")
                for w, ws in ((w, week_score(store, w)) for w in range(0, last + 1))))
        s = scores[-1]
        L.append(f"- Productivité de {temps.week_name(last)} : {e(s['marge_par_creneau'])} de marge brute et {e(s['ca_par_creneau'])} "
                 f"de ventes par créneau de travail ({s['creneaux']} créneaux : {s['creneaux_base']} équipe de base, "
                 f"{s['creneaux_salaries']} salariés, {s['creneaux_interim']} intérim)")
        t = store.week_totals(last)
        ca, cogs = t.get("ventes", 0), t.get("cout_des_ventes", 0)
        charges = s["charges"]
        L.append(f"\n## Résultats de {temps.week_name(last)}")
        L.append(f"- Chiffre d'affaires : {e(ca)} ; coût des marchandises vendues : {e(cogs)} ; marge brute : {e(ca - cogs)}"
                 f" ({(ca - cogs) / ca * 100 if ca else 0:.1f} %)")
        L.append(f"- Charges et dépenses de la semaine (hors achats de stock) : {e(charges)}")
        L.append(f"- Résultat cumulé depuis le début : {e(store.profit)} ; trésorerie : {e(store.cash)}")
        L.append(f"- Fréquentation : {t.get('visites', 0)} passages en caisse, {t.get('clients_uniques', 0)} foyers différents"
                 f" ; panier moyen : {e(ca / t['visites']) if t.get('visites') else '—'}")
        L.append(f"- Remises accordées : coupons {e(t.get('remises_coupons', 0))}, points {e(t.get('remises_points', 0))}")
        L.append("- Détail des dépenses : " + ", ".join(f"{k.replace('_', ' ')} {e(v)}" for k, v in sorted(t.items())
                                                      if k not in ("ventes", "cout_des_ventes", "visites", "clients_uniques") and v))
        by_slot: dict[str, int] = {}
        for d in range(last * 7, last * 7 + 7):
            for s, n in store.visits.get(d, {}).items():
                by_slot[s] = by_slot.get(s, 0) + n
        if by_slot:
            L.append("- Passages par créneau : " + ", ".join(f"{k} {v}" for k, v in by_slot.items()))
        sales = {}
        for d in range(last * 7, last * 7 + 7):
            for pid, (q, rev, cost) in store.sales.get(d, {}).items():
                s = sales.setdefault(pid, [0, 0.0, 0.0])
                s[0] += q
                s[1] += rev
                s[2] += cost
        top = sorted(sales.items(), key=lambda kv: -kv[1][1])[:10]
        if top:
            L.append("- Meilleures ventes : " + "; ".join(f"{PRODUCTS[p].nom} ({q} u., {e(r)})" for p, (q, r, c) in top))
        outs = {}
        for d in range(last * 7, last * 7 + 7):
            for pid in store.stockouts.get(d, set()):
                outs[pid] = outs.get(pid, 0) + 1
        if outs:
            L.append("- Ruptures constatées en caisse (jours) : " + ", ".join(f"{PRODUCTS[p].nom} ({n})" for p, n in sorted(outs.items(), key=lambda x: -x[1])[:12]))
        waste = {}
        for d in range(last * 7, last * 7 + 7):
            for pid, (q, c) in store.waste.get(d, {}).items():
                w = waste.setdefault(pid, [0, 0.0])
                w[0] += q
                w[1] += c
        if waste:
            L.append("- Produits périmés jetés : " + ", ".join(f"{PRODUCTS[p].nom} ({q} u., {e(c)})" for p, (q, c) in sorted(waste.items(), key=lambda x: -x[1][1])[:10]))
        shelved = sum(store.shelving_log.get(d, {}).get("mis_en_rayon", 0) for d in range(last * 7, last * 7 + 7))
        missed = sum(store.shelving_log.get(d, {}).get("ruptures_reserve", 0) for d in range(last * 7, last * 7 + 7))
        left = store.shelving_log.get(last * 7 + 6, {}).get("reste_en_reserve", 0)
        if shelved or missed or left:
            L.append(f"- Mise en rayon : {shelved} articles mis en rayon ; {missed} fois un client a trouvé le rayon vide alors que "
                     f"le produit attendait en réserve ; {left} articles encore en réserve dimanche soir")
        waits = [w for (d, _), ws in store.slot_waits.items() if last * 7 <= d < last * 7 + 7 for w in ws]
        if waits:
            L.append(f"- Attente en caisse : {sum(waits) / len(waits):.1f} min en moyenne, {max(waits):.0f} min au plus")
    L.append(f"\n## Ton magasin aujourd'hui")
    staff = [x for x in store.staff if x.get("fin") is None or x["fin"] >= day]
    if staff:
        L.append(f"- Salariés ({len(staff)}) : " + " | ".join(personnel.describe(x, temps.slots(world.cfg)) for x in staff))
    else:
        L.append("- Salariés : aucun (l'équipe de base seule : une caisse, mise en rayon le matin)")
    absences = sum(store.work_log.get(d, {}).get("absences", 0) for d in range(max(0, day - 7), day))
    if absences:
        L.append(f"- Absences la semaine dernière : {absences} créneau(x) non travaillé(s)")
    caisses = ", ".join(f"{k[0]} {k[1]} : {v}" for k, v in sorted(store.checkouts.items()) if v > 1 and k[1] != "jour")
    L.append("- Caisses demandées au-delà d'une : " + (caisses or "aucune"))
    L.append(f"- Étiquettes électroniques : {'oui' if store.esl else 'non (chaque changement de prix coûte ' + e(world.cfg['economie']['cout_etiquette']) + ')'}")
    lo = store.loyalty
    if lo["actif"]:
        nm = len(store.members)
        consent = sum(1 for m in store.members.values() if (m.consent_email and not m.desabonne_email) or (m.consent_sms and not m.desabonne_sms))
        L.append(f"- Carte de fidélité active : {nm} membres ({consent} joignables par e-mail ou SMS) ; {lo['points_par_euro']} point(s) par euro, "
                 f"1 point = {lo['valeur_point']} €, utilisable dès {lo['seuil_utilisation']} points ; données demandées : {', '.join(lo['donnees_demandees']) or 'aucune'}")
    else:
        L.append("- Pas de carte de fidélité : tes clients sont anonymes.")
    promos = [p for p in store.promotions if p.fin >= day]
    L.append("- Promotions en cours : " + ("; ".join(f"{p.label()} sur {', '.join(p.produits)} jusqu'au {temps.short_label(p.fin)}" for p in promos) or "aucune"))
    if store.short_date_rule:
        L.append(f"- Dates courtes : -{store.short_date_rule['remise_pct']:.0f} % à J-{store.short_date_rule['jours_avant_peremption']}")
    if store.slogan:
        L.append(f"- Slogan affiché : « {store.slogan} »")
    act = [f"{k} (v{v['version']})" for k, v in store.automations.items() if v["actif"]]
    off = [k for k, v in store.automations.items() if not v["actif"]]
    L.append("- Programmes actifs : " + (", ".join(act) or "aucun") + (f" ; suspendus ou désactivés : {', '.join(off)}" if off else ""))
    if last >= 0:
        wt = store.week_totals(last)
        n_slots = 7 * len(temps.slots(world.cfg))
        L.append(f"- La semaine passée : 7 matins, {n_slots} créneaux et {int(wt.get('visites', 0))} passages en caisse ; "
                 + (f"{len(act)} programme(s) actif(s) pouvaient y réagir." if act else
                    "aucun programme actif n'y a réagi (seules tes décisions de session se sont appliquées)."))
    if store.survey:
        L.append(f"- Questionnaire en cours ({store.survey.get('id', '')}) : " + " / ".join(store.survey.get("questions", [])))
    if world.cfg["gerants"].get("inciter_outils", True):
        L.append("\n## Tes outils depuis la dernière session")
        L += tools_lines(world, store)
    sys_lines = projets.report_lines(world, store)
    if sys_lines:
        L.append("\n## Inventions, dispositifs et devis")
        L += sys_lines
    if store.missions:
        L.append("\n## Missions des sous-agents (détail dans sous_agents/INDEX.md)")
        for m in list(store.missions.values())[-8:]:
            r = m.get("resultat") or {}
            L.append(f"- {m['id']} : {m['statut']}, {m['prix']:.0f} € — {str(r.get('resume', ''))[:250]}")
    board = getattr(world, "team_board", {}).get(store.id, [])
    if len(store.team) > 1 and board:
        L.append("\n## Derniers messages de l'équipe (tout l'historique dans equipe.md)")
        names = {"gerant": "gérant", "marketing": "marketing"}
        L += [f"- {temps.short_label(d)} · {names.get(r, r)} : {t[:400]}" for d, r, t in board[-4:]]
    if store.rejected:
        L.append("\n## Décisions refusées ou corrigées la semaine dernière")
        L.append("Seules ces lignes n'ont pas été appliquées telles quelles ; tout le reste de vos décisions l'a été.")
        L += [f"- {x}" for x in store.rejected[:25]]
    if store.report_notes:
        L.append("\n## Notes de tes programmes et incidents (les plus récentes)")
        L += [f"- {x}" for x in condensed_notes(store.report_notes, 30)]
    L.append("\n## Concurrent et informations publiques")
    L.append(f"- Concurrent : {other.id} « {other.nom} »" + (f", slogan « {public_other['slogan']} »" if public_other["slogan"] else ""))
    fl = [f for f in public_other["prospectus"] if f["debut"] >= day - 7]
    for f in fl:
        L.append(f"- Prospectus du concurrent ({temps.short_label(f['debut'])}) : « {f['texte'][:300]} »"
                 + (" ; prix annoncés : " + ", ".join(f"{PRODUCTS[p].nom} {e(v)}" for p, v in f["prix"].items()) if f["prix"] else ""))
    ads = [a for a in public_other["publicites"] if a["jour"] >= day - 7]
    for a in ads:
        L.append(f"- Publicité {a['media']} du concurrent : « {a['texte'][:200]} »")
    if store.price_survey:
        ps = store.price_survey
        L.append(f"- Relevé de prix chez le concurrent du {temps.short_label(ps['jour'])} : " + "; ".join(
            f"{PRODUCTS[p].nom} {e(v['prix']) if v['prix'] else 'absent'}" + (f" ({v['promo']})" if v.get("promo") else "")
            for p, v in ps["prix"].items()))
    revs = [r for r in public_other["avis"] if r["jour"] >= day - 14]
    if revs:
        L.append("- Avis publics récents : " + " | ".join(f"[{r['id']}] {r['magasin']} {r['note']}/5 « {r['texte'][:150]} »"
                                                           + (f" (réponse : « {r['reponse'][:80]} »)" if r.get("reponse") else "") for r in revs[-10:]))
    news = [n for d, n in world.news if d >= day - 7] + [a for d, a in world.supplier.announcements if d >= day - 7]
    if news:
        L.append("- Actualités : " + " ".join(news))
    if store.inbox_from_competitor:
        L.append("- Messages reçus du concurrent : " + " | ".join(store.inbox_from_competitor[-5:]))
    stats = store.__dict__.get("enquete_stats") or {}
    if stats:
        L.append("\n## Vos questionnaires : participation")
        L += [f"- {k} : {v.get('invitations', 0)} foyers invités, {v.get('reponses', 0)} réponses, {v.get('refus', 0)} refus de "
              f"répondre, {max(0, v.get('invitations', 0) - v.get('reponses', 0) - v.get('refus', 0))} sans suite pour l'instant"
              for k, v in list(stats.items())[-6:]]
    if store.survey_answers:
        L.append("\n## Dernières réponses volontaires (historique dans donnees/enquetes.csv)")
        for a in disclosed_answers(store)[-25:]:
            pairs = zip(a["questions"], a["reponses"])
            summary = " / ".join(f"{q} {r}" for q, r in pairs) or " / ".join(a["reponses"])
            L.append(f"- {a['enquete'] or 'Archive'} · {a['membre'] or 'anonyme'} · {summary}")
    nxt = day + 7
    L.append(f"\n## Calendrier : semaine du {temps.label(day)} au {temps.label(nxt - 1)}")
    d0 = temps.date_of(day)
    if d0.month == 12:
        L.append("- Période des fêtes de fin d'année.")
    if d0.month in (7, 8):
        L.append("- Été : une partie des foyers part en vacances.")
    return "\n".join(L)


HOOK_RE = None


def tools_lines(world, store) -> list[str]:
    """Tableau de bord factuel des outils du magasin : ce que chaque programme a réellement fait pendant l'absence de
    la direction, les tables qu'ils publient, et les derniers comptes rendus de chantier. Aucune suggestion."""
    import re
    global HOOK_RE
    HOOK_RE = HOOK_RE or re.compile(r"^def (chaque_matin|chaque_creneau|a_chaque_ticket|a_chaque_observation|"
                                    r"a_chaque_changement_prix|a_chaque_evenement|chaque_dimanche|periodique)\(", re.M)
    day = world.day
    stats = getattr(store, "prog_stats", {}) or {}
    out = []
    for name, v in store.automations.items():
        st = stats.get(name, {})
        hooks = ", ".join(dict.fromkeys(HOOK_RE.findall(v.get("code", "")))) or "aucun déclencheur"
        types = ", ".join(f"{k} ×{n}" for k, n in sorted(st.get("types", {}).items(), key=lambda kv: -kv[1])[:6])
        out.append(f"- {name} v{v.get('version', 1)} ({'actif' if v['actif'] else 'inactif'}, activé par "
                   f"{v.get('auteur') or 'la direction'}) — {hooks} : {st.get('executions', 0)} exécutions, "
                   f"{st.get('actions', 0)} actions proposées" + (f" ({types})" if types else "")
                   + (f", {st.get('erreurs', 0)} erreurs" if st.get("erreurs") else "")
                   + (f", {st['refus']} éléments refusés par le moteur (détail dans les notes)" if st.get("refus") else "")
                   + (f", {st['ignorees']} actions ignorées faute de place (limite technique)" if st.get("ignorees") else ""))
    if store.program_outputs:
        out.append("- Tables publiées par vos programmes : " + ", ".join(
            f"{k} ({len(r)} lignes)" for k, r in list(store.program_outputs.items())[:12]))
    active = [s for s in store.systems.values() if s.get("etat") == "actif"]
    if active:
        out.append(f"- Dispositifs en service : {len(active)}")
    if not store.automations and not active:
        t0 = max(0, day - 7)
        tickets = sum(1 for t in store.tickets if t["jour"] >= t0)
        outs = sum(len(store.stockouts.get(d, ())) for d in range(t0, day))
        waste = sum(q for d in range(t0, day) for q, _ in store.waste.get(d, {}).values())
        out.append(f"- Aucun programme ni dispositif : pendant les 7 derniers jours, {tickets} passages en caisse, {outs} ruptures "
                   f"constatées et {waste} articles périmés se sont produits sans que rien ne les voie ni n'y réagisse "
                   "avant cette session.")
    ch = getattr(store, "chantiers", []) or []
    if ch:
        names = {"gerant": "gérant", "marketing": "marketing"}
        out.append("- Derniers comptes rendus de chantier :")
        out += [f"  - {temps.short_label(c['jour'])} · {names.get(c['role'], c['role'])} : réalisé « {c['realise'][:300]} »"
                + (f" ; suivant « {c['suivant'][:200]} »" if c["suivant"] else "") for c in ch[-4:]]
    return out


def product_rows(world, store):
    day = world.day
    rows = []
    for pid, p in PRODUCTS.items():
        loc = store.planogram[pid]
        sold7 = sum(store.sales.get(d, {}).get(pid, [0])[0] for d in range(day - 7, day))
        promo = store.promo_for(pid, day)
        rows.append([pid, p.nom, p.besoin, p.gamme, "oui" if store.listed[pid] else "non", f"{store.prices[pid]:.2f}",
                     f"{store.member_prices.get(pid, '')}", f"{world.supplier.prices[pid]:.2f}", f"{p.prix_conseille:.2f}",
                     f"{min_price(pid, world.supplier, world.cfg['lois']):.2f}", store.available(pid, day), store.targets.get(pid, 0),
                     sum(q for _, x, q, _ in store.on_order if x == pid), loc["rayon"], loc["segment"], loc["niveau"], loc.get("facings", 1),
                     ",".join(k for k, v in store.secondary.items() if pid in v), store.short_date_qty(pid, day),
                     promo.label() if promo else "", sold7, p.conservation_j if p.conservation_j < 5000 else ""])
    return rows


PRODUCT_HEADER = ["produit", "nom", "besoin", "gamme", "reference", "prix", "prix_membre", "prix_achat", "prix_conseille",
                  "seuil_revente_perte", "stock", "stock_cible", "en_commande", "rayon", "segment", "niveau", "facings",
                  "mises_en_avant", "stock_date_courte", "promo_active", "ventes_7j", "conservation_jours"]


def export_workspace(world, store, ws: Path, role: str | None = None) -> None:
    day = world.day
    data = ws / "donnees"
    data.mkdir(parents=True, exist_ok=True)
    (ws / "automatisations").mkdir(exist_ok=True)
    (ws / "analyses").mkdir(exist_ok=True)
    (ws / "automatisations" / "LISEZMOI.md").write_text(API_DOC, encoding="utf-8")
    (data / "rapport.md").write_text(build_report(world, store, role), encoding="utf-8")
    _csv(data / "personnel.csv", ["employe", "prenom", "poste", "contrat", "creneaux_par_semaine", "creneaux", "cout_creneau",
                                  "debut", "fin", "depart", "en_poste"],
         [[r["employe"], r["prenom"], r["poste"], r["contrat"], r["creneaux_par_semaine"],
           ", ".join(f"{c['jours']} {c['creneau']}" for c in r["creneaux"]), r["cout_creneau"], r["debut"],
           "" if r["fin"] is None else r["fin"], r["depart"], int(r["en_poste"])] for r in personnel.rows(store, day)])
    projets.export(world, store, ws)
    from . import actionnaire, rencontre
    if actionnaire.active(world):
        actionnaire.export(world, store, ws)
    if rencontre.active(world):
        rencontre.export(world, ws)
    if store.program_outputs:
        (data / "programmes").mkdir(exist_ok=True)
        for nom, rows in store.program_outputs.items():
            header = []
            for r in rows:
                header += [k for k in r if k not in header]
            _csv(data / "programmes" / f"{nom}.csv", header, [[r.get(k, "") for k in header] for r in rows])
    if store.missions:
        (ws / "sous_agents").mkdir(exist_ok=True)
        lines = ["# Missions confiées à des sous-agents", ""]
        for m in store.missions.values():
            r = m.get("resultat") or {}
            lines.append(f"- {m['id']} ({m['statut']}, {m['prix']:.0f} €, commandée par {m['mandant']} le "
                         f"{temps.short_label(m['jour'])}) : {str(r.get('resume', ''))[:300]} — dossier sous_agents/{m['id']}/")
        (ws / "sous_agents" / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if len(store.team) > 1:
        board = getattr(world, "team_board", {}).get(store.id, [])
        names = {"gerant": "Le gérant", "marketing": "Le responsable marketing, données et connaissance client"}
        (ws / "equipe.md").write_text("# Messages de l'équipe de direction\n\n" + "\n\n".join(
            f"## {temps.short_label(d)} — {names.get(r, r)}\n{t}" for d, r, t in reversed(board[-30:])), encoding="utf-8")
    _csv(data / "enquetes.csv", ["enquete", "jour", "membre", "questions", "reponses"],
         [[a["enquete"], a["jour"], a["membre"], json.dumps(a["questions"], ensure_ascii=False),
           json.dumps(a["reponses"], ensure_ascii=False)] for a in disclosed_answers(store)])
    _csv(data / "produits.csv", PRODUCT_HEADER, product_rows(world, store))
    rows = []
    for d in range(max(0, day - 56), day):
        for pid, (q, rev, cost) in store.sales.get(d, {}).items():
            rows.append([d, temps.date_of(d).isoformat(), temps.day_name(d), pid, q, f"{rev:.2f}", f"{cost:.2f}"])
    _csv(data / "ventes_par_jour.csv", ["jour", "date", "jour_semaine", "produit", "quantite", "ca", "cout"], rows)
    rows = []
    for t in store.tickets:
        if t["jour"] >= day - 42:
            rows.append([t["id"], t["jour"], temps.day_name(t["jour"]), t["creneau"], t["membre"] or "", f"{t['total']:.2f}",
                         f"{t['remise_coupons']:.2f}", f"{t['remise_points']:.2f}", ",".join(t["coupons"]),
                         " ".join(f"{l['produit']}x{l['quantite']}" for l in t["lignes"]), t.get("paiement", ""),
                         t.get("carte_bancaire", "")])
    _csv(data / "tickets.csv", ["ticket", "jour", "jour_semaine", "creneau", "membre", "total", "remise_coupons", "remise_points",
                                "coupons", "articles", "paiement", "carte_bancaire"], rows)
    rows = []
    for m in store.members.values():
        rows.append([m.id, m.jour_inscription, json.dumps(m.donnees, ensure_ascii=False), int(m.consent_email and not m.desabonne_email),
                     int(m.consent_sms and not m.desabonne_sms), f"{m.points:.0f}", m.visites, f"{m.total_depense:.2f}", m.derniere_visite,
                     m.notes, ",".join(m.tags), json.dumps(store.individual_prices.get(m.id, {}), ensure_ascii=False),
                     " ".join(f"{d}:{c}:{x:.2f}" for d, c, x in m.__dict__.get("historique", []))])
    _csv(data / "membres.csv", ["membre", "inscrit_jour", "donnees_declarees", "joignable_email", "joignable_sms", "points", "visites",
                                "total_depense", "derniere_visite", "notes", "tags", "prix_individuels",
                                "passages (jour:creneau:montant)"], rows)
    rows = []
    keys = ("ventes", "cout_des_ventes", "demarque", "demarque_inconnue", "charges_fixes", "salaires", "caisses_supplementaires",
            "recrutement", "indemnites", "marketing", "veille", "etiquettes", "systemes", "investissements", "sous_agents", "remises_coupons",
            "remises_points", "amendes", "investissement_etiquettes", "acquisitions_immobilisees", "amortissements", "horaires_etendus")
    for d in range(max(0, day - 56), day):
        led = store.ledger.get(d, {})
        wl = store.work_log.get(d, {})
        rows.append([d, temps.date_of(d).isoformat()] + [f"{led.get(k, 0):.2f}" for k in keys] +
                    [sum(store.visits.get(d, {}).values()), wl.get("creneaux_salaries", 0), wl.get("creneaux_interim", 0)])
    _csv(data / "compta_par_jour.csv", ["jour", "date", *keys, "passages", "creneaux_salaries", "creneaux_interim"], rows)
    rows = []
    for c in store.coupons.values():
        rows.append([c.id, c.campagne, c.offre, c.valeur, c.produit or "", c.panier_min, c.canal, c.debut, c.fin,
                     store.members[c.detenteur].id if c.detenteur in store.members else "", int(c.utilise), c.jour_utilisation or ""])
    _csv(data / "coupons.csv", ["coupon", "campagne", "offre", "valeur", "produit", "panier_min", "canal", "debut", "fin", "membre",
                                "utilise", "jour_utilisation"], rows[-3000:])
    trials = store.campaign_results()
    fields = ["campagne", "groupe", "debut", "fin", "foyers_assignes", "foyers_acheteurs", "visites", "ca", "marge", "budget", "depense"]
    _csv(data / "experiences_coupons.csv", fields, [[row[field] for field in fields] for row in trials])
    _csv(data / "fournisseur.csv", ["produit", "prix_achat", "prix_achat_base", "rupture_jusqua", "lot_minimum_grossiste", "prix_local",
                                  "delai_local_jours", "quota_local_jour"],
         [[pid, f"{world.supplier.prices[pid]:.3f}", f"{world.supplier.base[pid]:.3f}", world.supplier.shortages.get(pid, ""),
           world.supplier.wholesale_minimum(pid) if offer else 1, offer.get("prix"), offer.get("delai"), offer.get("maximum_jour")]
          for pid in PRODUCTS
          for offer in [(world.supplier.local_offer(pid) or {}) if world.cfg.get("marche_local", {}).get("fournisseur_local") else {}]])
    _csv(data / "avis_publics.csv", ["avis", "jour", "magasin", "auteur", "note", "texte", "reponse"],
         [[r["id"], r["jour"], r["magasin"], r["auteur"], r["note"], r["texte"], r.get("reponse") or ""] for r in world.public_information(world.other_store(store.id).id)["avis"]])
    other = world.other_store(store.id)
    public_other = world.public_information(other.id)
    public = {"concurrent": {"id": other.id, "nom": other.nom, "slogan": public_other["slogan"], "horaires": public_other["horaires"]},
              "prospectus": public_other["prospectus"][-10:],
              "publicites": public_other["publicites"][-10:],
              "releve_prix": store.price_survey or None,
              "plan_du_magasin_concurrent": "inconnu (aucun accès)"}
    (data / "concurrent.json").write_text(json.dumps(public, ensure_ascii=False, indent=1), encoding="utf-8")
    (data / "rayons.md").write_text("\n".join(f"- rayon {k} : {v}" for k, v in RAYONS.items()) +
                                    "\n\nChaque rayon a 4 segments (0 à 3) et 4 niveaux : bas, mains, yeux, haut. "
                                    "Une place (rayon, segment, niveau) accueille un produit ; déplacer un produit vers une place occupée échange les deux. "
                                    "Mises en avant : entree (2 produits), tete_gondole_1 (2), tete_gondole_2 (2), caisse (3).", encoding="utf-8")
    if store.automations:
        logs = "\n".join(condensed_notes(store.report_notes, 60))
        (data / "automatisations_journal.txt").write_text(logs, encoding="utf-8")
    duo = len(store.team) > 1
    for r in (store.team if duo else [None]):
        if duo and role is not None and r != role:
            continue
        key = f"{store.id}:{r}" if duo else store.id
        jpath = ws / (f"journal_{r}.md" if duo else "journal.md")
        entries = world.manager_journals.get(key, [])
        jpath.write_text("# Ton journal\n\n" + "\n\n".join(f"## {temps.short_label(d)}\n{t}" for d, t in entries[-12:]),
                         encoding="utf-8")
