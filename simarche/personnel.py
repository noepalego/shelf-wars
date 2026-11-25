"""Personnel salarié d'un magasin : contrats, plannings, présence, salaires, intérim et mise en rayon.

Droit du travail simplifié (inspiré du droit français) : contrats de 3 à 12 créneaux par semaine, embauche effective
le lundi suivant une semaine de recrutement, changement de planning au bout de 7 jours, CDD non rompable avant son
terme (prime de fin de contrat de 10 %), CDI avec période d'essai de 8 semaines (préavis d'une semaine), puis deux
semaines de préavis et une semaine de salaire d'indemnité après 8 mois d'ancienneté. Absences et démissions aléatoires.
"""
from __future__ import annotations

from . import temps

POSTES = ("caisse", "rayon")
CONTRATS = ("CDI", "CDD")
JOURS_TYPES = ("semaine", "samedi", "dimanche")
MIN_CRENEAUX, MAX_CRENEAUX = 3, 12
MAX_SALARIES = 8
ESSAI_SEMAINES = 8
PRENOMS = ["Léa", "Karim", "Inès", "Thomas", "Chloé", "Mehdi", "Sarah", "Julien", "Aïcha", "Lucas", "Camille", "Yanis",
           "Manon", "Hugo", "Nadia", "Théo", "Emma", "Rayan", "Jade", "Antoine", "Lina", "Maxime", "Zoé", "Bilal"]


def _slot_open_on(jours: str, creneau: str) -> bool:
    return not (jours == "dimanche" and creneau not in ("matin", "jour"))


def shifts_per_week(creneaux: list[dict]) -> int:
    n = 0
    for c in creneaux:
        if not _slot_open_on(c["jours"], c["creneau"]):
            continue
        n += 5 if c["jours"] == "semaine" else 1
    return n


def clean_creneaux(raw, slots: list[str]) -> tuple[list[dict], list[str]]:
    out, problems = [], []
    for c in raw or []:
        if not isinstance(c, dict) or c.get("jours") not in JOURS_TYPES or c.get("creneau") not in slots:
            problems.append(f"créneau invalide : {c}")
            continue
        if not _slot_open_on(c["jours"], c["creneau"]):
            problems.append(f"le magasin est fermé le dimanche {c['creneau']}")
            continue
        item = {"jours": c["jours"], "creneau": c["creneau"]}
        if item not in out:
            out.append(item)
    return out, problems


def employed(e: dict, day: int) -> bool:
    return e["debut"] <= day and (e.get("fin") is None or day <= e["fin"])


def current(store, day: int) -> list[dict]:
    """Salariés sous contrat ce jour-là ou dont l'embauche est prévue."""
    return [e for e in store.staff if e.get("fin") is None or e["fin"] >= day]


def _patterns(e: dict, day: int) -> list[dict]:
    if e.get("nouveaux_creneaux") is not None and day >= e.get("changement_le", 10 ** 9):
        e["creneaux"] = e.pop("nouveaux_creneaux")
        e.pop("changement_le", None)
    return e["creneaux"]


def weekly_cost(e: dict) -> float:
    return shifts_per_week(e["creneaux"]) * e["cout_creneau"]


def plan_day(store, day: int, cfg: dict, rnd) -> dict:
    """Présence du jour, caisses ouvertes, intérim et capacité de mise en rayon (calculés avant l'ouverture)."""
    eco = cfg["economie"]
    slots = temps.slots(cfg)
    dtype = temps.day_type(day)
    present = {s: {"caisse": 0, "rayon": 0} for s in slots}
    names = {s: [] for s in slots}
    wages, shifts, absences = 0.0, 0, []
    for e in store.staff:
        if not employed(e, day):
            continue
        for s in slots:
            if not store.is_open(day, s):
                continue
            if not any(p["jours"] == dtype and p["creneau"] == s for p in _patterns(e, day)):
                continue
            if rnd.random() < eco["absenteisme"]:
                absences.append({"employe": e["id"], "prenom": e["prenom"], "creneau": s})
                continue
            present[s][e["poste"]] += 1
            names[s].append({"name": e["prenom"], "role": e["poste"]})
            shifts += 1
            wages += e["cout_creneau"]
    auto = int(store.effect("capacite_caisses"))
    registers, interim, base = {}, {}, 0
    for s in slots:
        if not store.is_open(day, s):
            continue
        base += 1
        wanted = max(1, min(3, int(store.checkouts.get((dtype, s), 1))))
        own = min(3, 1 + present[s]["caisse"])
        interim[s] = max(0, wanted - own)
        registers[s] = min(3, max(wanted, own)) + auto
    shelf = {s: present[s]["rayon"] * eco["mise_en_rayon_creneau"] for s in slots}
    opening = int(base_shelving(store.cfg) + store.effect("capacite_rayon"))
    plan = {"jour": day, "presents": present, "noms": names, "caisses": registers, "interim": interim, "salaires": round(wages, 2),
            "creneaux_salaries": shifts, "creneaux_interim": sum(interim.values()), "creneaux_base": base,
            "absences": absences, "mise_en_rayon_ouverture": opening, "mise_en_rayon": shelf,
            "rayon_present": any(present[s]["rayon"] for s in slots)}
    store.day_plan = plan
    store.work_log[day] = {k: plan[k] for k in ("creneaux_salaries", "creneaux_interim", "creneaux_base", "salaires")}
    store.work_log[day]["absences"] = len(absences)
    return plan


def weekly_turnover(store, day: int, cfg: dict, rnd) -> list[str]:
    """Démissions (préavis d'une semaine). Renvoie les notes pour le rapport."""
    notes = []
    for e in store.staff:
        if employed(e, day) and e.get("fin") is None and rnd.random() < cfg["economie"]["demission_hebdo"]:
            e["fin"] = day + 6
            e["depart"] = "démission"
            notes.append(f"{e['prenom']} ({e['id']}, {e['poste']}) a démissionné : dernier jour le {temps.short_label(e['fin'])}")
    return notes


def apply(world, store, p: dict, ok: list, ko: list) -> None:
    """Applique la partie `personnel` d'une décision (recrutements, plannings, ruptures de contrat)."""
    if not isinstance(p, dict):
        return
    eco = world.cfg["economie"]
    day = world.day
    slots = temps.slots(world.cfg)
    for x in (p.get("recruter") or [])[:4]:
        if not isinstance(x, dict):
            continue
        poste, contrat = x.get("poste"), x.get("contrat", "CDI")
        if poste not in POSTES or contrat not in CONTRATS:
            ko.append(f"recrutement refusé : poste (caisse | rayon) ou contrat (CDI | CDD) invalide ({poste}, {contrat})")
            continue
        if len(current(store, day)) >= MAX_SALARIES:
            ko.append(f"recrutement refusé : {MAX_SALARIES} salariés au plus dans un magasin de cette taille")
            continue
        creneaux, problems = clean_creneaux(x.get("creneaux"), slots)
        n = shifts_per_week(creneaux)
        if not MIN_CRENEAUX <= n <= MAX_CRENEAUX:
            ko.append(f"recrutement refusé : un contrat compte de {MIN_CRENEAUX} à {MAX_CRENEAUX} créneaux par semaine "
                      f"(ici {n})" + (f" ; {'; '.join(problems[:2])}" if problems else ""))
            continue
        debut = day + 7
        fin = None
        if contrat == "CDD":
            weeks = int(x.get("duree_semaines") or 0)
            if not 1 <= weeks <= 26:
                ko.append("recrutement refusé : un CDD dure de 1 à 26 semaines (duree_semaines)")
                continue
            fin = debut + 7 * weeks - 1
        store.staff_seq += 1
        cost = eco["salaire_creneau"] * (1 + (eco["prime_fin_cdd"] if contrat == "CDD" else 0))
        e = {"id": f"{store.id}-E{store.staff_seq:02d}", "prenom": PRENOMS[(store.staff_seq * 7 + ord(store.id[0])) % len(PRENOMS)],
             "poste": poste, "contrat": contrat, "creneaux": creneaux, "debut": debut, "fin": fin,
             "cout_creneau": round(cost, 2), "embauche_le": day}
        store.staff.append(e)
        store.pay(day, "recrutement", eco["cout_recrutement"])
        ok.append(f"recrutement : {e['prenom']} ({e['id']}), {poste}, {contrat}, {n} créneaux par semaine, à partir du "
                  f"{temps.short_label(debut)} (coût {weekly_cost(e):.0f} € par semaine)")
    by_id = {e["id"]: e for e in store.staff}
    for x in (p.get("modifier") or [])[:8]:
        e = by_id.get(str((x or {}).get("employe", "")))
        if not e or (e.get("fin") is not None and e["fin"] < day):
            ko.append(f"planning : salarié inconnu ou parti « {(x or {}).get('employe')} »")
            continue
        creneaux, problems = clean_creneaux(x.get("creneaux"), slots)
        n = shifts_per_week(creneaux)
        if not MIN_CRENEAUX <= n <= MAX_CRENEAUX:
            ko.append(f"planning de {e['id']} refusé : de {MIN_CRENEAUX} à {MAX_CRENEAUX} créneaux par semaine (ici {n})")
            continue
        if e["debut"] > day:  # pas encore arrivé : on corrige directement son contrat
            e["creneaux"] = creneaux
        else:
            e["nouveaux_creneaux"], e["changement_le"] = creneaux, day + 7
        ok.append(f"planning de {e['prenom']} ({e['id']}) : {n} créneaux par semaine à partir du "
                  f"{temps.short_label(max(day, e['debut']) if e['debut'] > day else day + 7)}")
    for eid in (p.get("licencier") or [])[:8]:
        e = by_id.get(str(eid))
        if not e or (e.get("fin") is not None and e["fin"] < day):
            ko.append(f"fin de contrat : salarié inconnu ou déjà parti « {eid} »")
            continue
        if e["debut"] > day:
            e["fin"], e["depart"] = e["debut"] - 1, "embauche annulée"
            ok.append(f"embauche de {e['prenom']} ({e['id']}) annulée (frais de recrutement non remboursés)")
            continue
        if e["contrat"] == "CDD":
            ko.append(f"{e['prenom']} ({e['id']}) est en CDD : le contrat ne peut pas être rompu avant son terme "
                      f"({temps.short_label(e['fin'])})")
            continue
        if e.get("depart"):
            ko.append(f"{e['prenom']} ({e['id']}) part déjà le {temps.short_label(e['fin'])}")
            continue
        seniority = day - e["debut"]
        if seniority < 7 * ESSAI_SEMAINES:
            e["fin"], e["depart"] = day + 6, "fin de période d'essai"
            ok.append(f"fin de période d'essai de {e['prenom']} ({e['id']}) : dernier jour le {temps.short_label(e['fin'])}")
        else:
            e["fin"], e["depart"] = day + 13, "licenciement"
            if seniority >= 35 * 7:
                severance = weekly_cost(e)
                store.pay(day, "indemnites", severance)
                ok.append(f"indemnité de licenciement versée à {e['prenom']} : {severance:.2f} €")
            ok.append(f"licenciement de {e['prenom']} ({e['id']}) : deux semaines de préavis, dernier jour le "
                      f"{temps.short_label(e['fin'])}")


def describe(e: dict, slots_order: list[str]) -> str:
    def pat(c):
        return f"{c['jours']} {c['creneau']}"
    text = f"{e['id']} {e['prenom']} — {e['poste']}, {e['contrat']}, {shifts_per_week(e['creneaux'])} créneaux/semaine " \
           f"({', '.join(pat(c) for c in e['creneaux'])}), {e['cout_creneau']:.2f} € par créneau, depuis le {temps.short_label(e['debut'])}"
    if e.get("nouveaux_creneaux") is not None:
        text += f" ; nouveau planning le {temps.short_label(e['changement_le'])} : {', '.join(pat(c) for c in e['nouveaux_creneaux'])}"
    if e.get("fin") is not None:
        text += f" ; fin le {temps.short_label(e['fin'])}" + (f" ({e['depart']})" if e.get("depart") else " (fin du CDD)")
    return text


def rows(store, day: int) -> list[dict]:
    out = []
    for e in store.staff:
        if e.get("fin") is not None and e["fin"] < day - 28:
            continue
        out.append({"employe": e["id"], "prenom": e["prenom"], "poste": e["poste"], "contrat": e["contrat"],
                    "creneaux": [dict(c) for c in e["creneaux"]], "creneaux_par_semaine": shifts_per_week(e["creneaux"]),
                    "cout_creneau": e["cout_creneau"], "debut": e["debut"], "fin": e.get("fin"), "depart": e.get("depart", ""),
                    "nouveaux_creneaux": e.get("nouveaux_creneaux"), "en_poste": employed(e, day)})
    return out


def base_shelving(cfg: dict) -> int:
    """Articles mis en rayon chaque matin par l'équipe de base. Elle est dimensionnée pour la ville : avec des foyers
    variantes (clients.variantes_par_foyer), la ville compte (1 + N) fois plus de foyers et l'équipe de base suit."""
    k = int((cfg.get("clients") or {}).get("variantes_par_foyer", 0) or 0)
    return int(cfg["economie"]["mise_en_rayon_base_jour"] * (1 + k))

