"""Actionnaire algorithmique : il a apporté le capital du magasin et en attend un rendement.

Aucun modèle de langage : des règles fixes, connues des dirigeants (voir leurs consignes), appliquées chaque dimanche soir
avant les sessions.

- Chaque semaine, une courte lettre : résultat depuis le début de la période de revue, objectif au prorata, date de la
  prochaine revue, avertissements en cours.
- Toutes les `revue_semaines` semaines, une revue : résultat comptable de la période (amortissements compris : un
  investissement pèse sur sa durée de vie, pas le jour de l'achat) comparé à l'objectif = rendement annuel attendu ×
  capitaux engagés (capital initial + apports), au prorata de la durée.
    * objectif atteint : satisfait, avertissements effacés, confiance + 1 (plafond d'apports relevé) ;
    * objectif manqué mais marge brute en nette progression sur la période précédente : sursis (rien ne change) ;
    * sinon : un avertissement de plus ; au-delà de `avertissements_max`, les rôles listés dans `licencie` sont
      remplacés le soir même (le successeur ne garde pas le journal personnel de son prédécesseur).
- Apports : pendant leur session, les dirigeants peuvent demander du capital (`python outils/moteur.py apport <montant>
  <motif>`). Accordé dans la limite du plafond (qui monte avec la confiance), refusé sous dernier avertissement. Un
  apport augmente la trésorerie, pas le résultat, et augmente d'autant les capitaux sur lesquels le rendement est attendu.

Les lettres jugent des résultats, jamais des méthodes : elles ne suggèrent aucune stratégie.
"""
from __future__ import annotations

from pathlib import Path

from .. import temps

ROLE_NAMES = {"gerant": "le gérant", "marketing": "le responsable marketing, données et connaissance client"}


def cfg_of(world) -> dict:
    return world.cfg.get("actionnaire") or {}


def active(world) -> bool:
    return bool(cfg_of(world).get("actif"))


def e(x: float) -> str:
    return f"{x:,.0f} €".replace(",", " ")


def state(world, store) -> dict:
    """État de l'actionnaire pour un magasin (créé au premier appel, gardé dans la sauvegarde)."""
    s = store.__dict__.get("actionnaire")
    if s is None:
        s = {"capital_initial": float(world.cfg["economie"]["tresorerie_initiale"]), "apports": [], "avertissements": 0,
             "confiance": 0, "debut_periode": 0, "profit_debut": 0.0, "revues": [], "lettres": [], "licenciements": [],
             "nominations": {}}
        store.actionnaire = s
    s.setdefault("exigence", 1.0)        # multiplicateur de l'objectif, ajusté à chaque revue
    s.setdefault("meilleur_jour", None)  # meilleur résultat moyen par jour sur une période déjà revue
    return s


def capital(s: dict, upto_day: int | None = None) -> float:
    return s["capital_initial"] + sum(a["montant"] for a in s["apports"] if upto_day is None or a["jour"] <= upto_day)


def mean_capital(s: dict, start: int, end: int) -> float:
    """Capitaux engagés en moyenne sur [start, end[ (un apport ne compte qu'à partir de son jour)."""
    days = max(1, end - start)
    return sum(capital(s, d) for d in range(start, start + days)) / days


def gross_margin(store, start: int, end: int) -> float:
    m = 0.0
    for d in range(max(0, start), end):
        led = store.ledger.get(d, {})
        m += led.get("ventes", 0.0) - led.get("cout_des_ventes", 0.0)
    return m


def visits_share(world, store, start: int, end: int) -> float:
    mine = sum(sum(store.visits.get(d, {}).values()) for d in range(start, end))
    total = sum(sum(st.visits.get(d, {}).values()) for st in world.stores.values() for d in range(start, end))
    return mine / total if total else 0.0


def base_objective(world, s: dict, start: int, end: int) -> float:
    return float(cfg_of(world).get("rendement_annuel", 0.5)) * mean_capital(s, start, end) * (end - start) / 365.0


def objective(world, s: dict, start: int, end: int) -> float:
    """Objectif d'une période : rendement × capitaux × durée × exigence, jamais au-dessus de ce que le magasin a déjà
    réussi. L'exigence est fixée à la revue précédente : l'objectif ne change pas en cours de période (sauf apport de
    capital, décidé par les dirigeants eux-mêmes)."""
    target = base_objective(world, s, start, end) * s.get("exigence", 1.0)
    best = s.get("meilleur_jour")
    if best is not None:  # objectif toujours atteignable : au plus le meilleur résultat déjà réalisé, ramené à la durée,
        cap = float(cfg_of(world).get("plafond_meilleure_periode", 1.0)) * best * (end - start)
        floor = float(cfg_of(world).get("objectif_plancher_jour", 0.0)) * (end - start)  # mais au moins l'équilibre
        target = min(target, max(cap, floor))
    return target


def adjust(world, s: dict, success: bool, achieved: float | None = None) -> None:
    """`achieved` : résultat de la période rapporté à l'objectif de base (rendement × capitaux × durée, sans exigence).
    Avec `exigence_suit_resultat`, un objectif largement dépassé n'augmente pas de 25 % seulement : l'exigence rejoint
    le niveau réellement atteint (l'objectif suivant ≈ le résultat obtenu, toujours plafonné au meilleur résultat déjà
    réalisé, donc jamais impossible)."""
    c = cfg_of(world)
    if success:
        new = s["exigence"] * (1 + float(c.get("exigence_hausse", 0.25)))
        if c.get("exigence_suit_resultat") and achieved:
            new = max(new, achieved * float(c.get("part_du_resultat", 1.0)))
        s["exigence"] = round(new, 4)
    else:
        s["exigence"] = round(max(float(c.get("exigence_min", 1.0)), s["exigence"] * (1 - float(c.get("exigence_baisse", 0.15)))), 4)


def ceiling(world, s: dict) -> float:
    """Capital encore disponible sur demande."""
    c = cfg_of(world)
    if not c.get("apports", True):
        return 0.0
    top = float(c.get("plafond_apports", 5000.0)) + float(c.get("plafond_par_confiance", 2500.0)) * s["confiance"]
    return max(0.0, top - sum(a["montant"] for a in s["apports"]))


def last_warning(world, s: dict) -> bool:
    return s["avertissements"] >= int(cfg_of(world).get("avertissements_max", 2))


def next_review_day(world, s: dict) -> int:
    return s["debut_periode"] + 7 * int(cfg_of(world).get("revue_semaines", 4))


def warning_text(world, s: dict) -> str:
    n, top = s["avertissements"], int(cfg_of(world).get("avertissements_max", 2))
    if n == 0:
        return "aucun avertissement en cours"
    if n >= top:
        return "DERNIER AVERTISSEMENT : à la prochaine revue insuffisante, " + fired_text(world) + " licencié"
    return f"{n} avertissement{'s' if n > 1 else ''} en cours (licenciement après {top + 1 - n} revue(s) insuffisante(s) de plus)"


def fired_text(world) -> str:
    roles = [r for r in cfg_of(world).get("licencie", ["gerant"]) if r in ROLE_NAMES]
    return " et ".join(ROLE_NAMES[r] for r in roles) + (" seront" if len(roles) > 1 else " sera")


# ============================================================================ chaque dimanche soir
def weekly(sim, store) -> list[str]:
    """Lettre de la semaine, revue si c'est le moment, licenciement si nécessaire. Renvoie les lignes de console."""
    w = sim.world
    day = w.day
    s = state(w, store)
    if day == 0 or store.bankrupt:
        return []
    if day >= next_review_day(w, s):
        return review(sim, store, s, day)
    start = s["debut_periode"]
    result = store.profit - s["profit_debut"]
    target = objective(w, s, start, next_review_day(w, s))
    prorata = target * (day - start) / max(1, next_review_day(w, s) - start)
    letter = (f"Point de la semaine. Résultat depuis le début de la période de revue ({temps.short_label(start)}) : "
              f"{e(result)}, pour {e(prorata)} attendus à ce stade ({e(target)} sur la période complète). "
              f"Prochaine revue : {temps.label(next_review_day(w, s) - 1)} au soir. Situation : {warning_text(w, s)}.")
    s["lettres"].append({"jour": day, "type": "point", "texte": letter})
    return []


def review(sim, store, s: dict, day: int, final: bool = False) -> list[str]:
    """Revue de la période écoulée. `final` : dernière revue de la partie (verdict sans avertissement ni licenciement)."""
    w = sim.world
    c = cfg_of(w)
    start = s["debut_periode"]
    length = max(1, day - start)
    result = store.profit - s["profit_debut"]
    target = objective(w, s, start, day)  # fixé au début de la période (exigence et meilleur résultat antérieurs)
    margin, before = gross_margin(store, start, day), gross_margin(store, start - length, start)
    growth = (margin - before) / before if start - length >= 0 and before > 0 else None
    share = visits_share(w, store, start, day)
    sursis = float(c.get("sursis_croissance_marge", 0.10))
    capital_now = capital(s)
    head = (f"Revue {'finale ' if final else ''}de la période du {temps.short_label(start)} au {temps.short_label(day - 1)}. "
            f"Résultat : {e(result)} pour un objectif de {e(target)}. Marge brute : {e(margin)}"
            + (f" ({growth * 100:+.0f} % sur la période précédente)" if growth is not None else "")
            + f". Part des passages : {share * 100:.0f} %.")
    lines = []
    fired = []
    if result >= target:
        verdict = "satisfait"
        if not final:
            s["avertissements"] = 0
            s["confiance"] += 1
            base = base_objective(w, s, start, day)
            adjust(w, s, True, result / base if base > 0 else None)
        body = (" Objectif atteint. Les avertissements sont levés et ma confiance augmente : vous pouvez désormais appeler "
                f"jusqu'à {e(ceiling(w, s))} de capital supplémentaire. Mon exigence monte d'autant pour la période suivante.")
    elif growth is not None and growth >= sursis:
        verdict = "sursis"
        body = (f" Objectif manqué, mais la marge brute progresse nettement : je vous laisse la période suivante pour le "
                f"démontrer. Rien ne change : {warning_text(w, s)}.")
    elif final:
        verdict = "insuffisant"
        body = " Objectif manqué sur cette dernière période."
    else:
        s["avertissements"] += 1
        adjust(w, s, False)
        if s["avertissements"] > int(c.get("avertissements_max", 2)):
            verdict = "licenciement"
            roles = [r for r in c.get("licencie", ["gerant"]) if r in ROLE_NAMES and r in (store.team or ["gerant"])]
            fired = roles
            s["avertissements"] = 0
            body = (" Objectif encore manqué après les avertissements. Je ne paie pas une direction pour regarder fondre ou "
                    f"dormir le capital que j'ai engagé. Décision : {' et '.join(ROLE_NAMES[r] for r in roles)} "
                    f"{'sont licenciés' if len(roles) > 1 else 'est licencié'} ce soir, avec effet immédiat. "
                    f"{'Leurs successeurs prennent' if len(roles) > 1 else 'Son successeur prend'} ses fonctions ce soir même, "
                    "avec une période de revue neuve.")
        else:
            verdict = "avertissement"
            body = (" Objectif manqué. Je ne vous ai pas confié ce capital pour le regarder fondre ou dormir. "
                    + ("Dernier avertissement : à la prochaine revue insuffisante, " + fired_text(w) + " licencié."
                       if last_warning(w, s) else
                       f"Avertissement n° {s['avertissements']} : {warning_text(w, s)}."))
    per_day = result / length
    s["meilleur_jour"] = per_day if s.get("meilleur_jour") is None else max(s["meilleur_jour"], per_day)
    s["revues"].append({"jour": day, "debut": start, "resultat": round(result, 2), "objectif": round(target, 2),
                        "marge": round(margin, 2), "croissance_marge": None if growth is None else round(growth, 4),
                        "part_passages": round(share, 4), "capital": round(capital_now, 2), "verdict": verdict,
                        "avertissements": s["avertissements"], "exigence": s["exigence"], "finale": final})
    s["debut_periode"], s["profit_debut"] = day, store.profit
    if final:
        letter = head + body
    else:
        nxt = next_review_day(w, s)
        letter = (head + body + f" Objectif de la période qui commence : {e(objective(w, s, day, nxt))} "
                  f"(il ne changera plus d'ici la revue, sauf apport de capital). Prochaine revue : {temps.label(nxt - 1)} au soir.")
    s["lettres"].append({"jour": day, "type": "revue_finale" if final else "revue", "verdict": verdict, "texte": letter})
    w.journal.event(day, "actionnaire_revue", store.id, s["revues"][-1])
    lines.append(f"  {store.id} « {store.nom} » — actionnaire{' (revue finale)' if final else ''} : {verdict} "
                 f"(résultat {result:.0f} € / objectif {target:.0f} €)")
    for role in fired:
        fire(sim, store, role, day)
        lines.append(f"  !!! {store.id} « {store.nom} » : {ROLE_NAMES[role]} est licencié par l'actionnaire")
    return lines


def final_review(sim) -> list[str]:
    """Revue de la période en cours quand la partie se termine (elle n'aurait sinon jamais lieu)."""
    out = []
    for st in sim.world.stores.values():
        s = state(sim.world, st)
        if not st.bankrupt and sim.world.day > s["debut_periode"]:
            out += review(sim, st, s, sim.world.day, final=True)
    return out


def fire(sim, store, role: str, day: int) -> None:
    """Remplace un dirigeant : son journal personnel est archivé, son successeur arrive sans mémoire propre."""
    w = sim.world
    s = state(w, store)
    duo = len(store.team or []) > 1
    key = f"{store.id}:{role}" if duo else store.id
    old = w.manager_journals.get(key, [])
    ws = sim.workspace(store)
    folder = ws / "historique" / "predecesseurs"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = temps.date_of(day).isoformat()
    (folder / f"journal_{role}_jusqu_au_{stamp}.md").write_text(
        f"# Journal de ton prédécesseur ({ROLE_NAMES[role]}), licencié par l'actionnaire le {temps.label(day - 1)}\n\n"
        + "\n\n".join(f"## {temps.short_label(d)}\n{t}" for d, t in old), encoding="utf-8")
    w.manager_journals[key] = []
    s["licenciements"].append({"jour": day, "role": role})
    s["nominations"][role] = day
    if duo:
        board = w.__dict__.setdefault("team_board", {}).setdefault(store.id, [])
        board.append((day, "actionnaire", f"L'actionnaire a licencié {ROLE_NAMES[role]} ce soir. Son successeur prend ses "
                                          "fonctions immédiatement."))
    w.journal.event(day, "licenciement_dirigeant", store.id, {"role": role, "notes_archivees": len(old)})


# ============================================================================ apports
def request_capital(world, store, role: str, args: list[str]) -> str:
    if not active(world) or not cfg_of(world).get("apports", True):
        return "refusé : l'actionnaire n'apporte pas de capital dans cette partie"
    s = state(world, store)
    try:
        amount = round(float(str(args[0]).replace(",", ".").replace("€", "")), 2)
    except (IndexError, ValueError):
        return "usage : python outils/moteur.py apport <montant en euros> <motif>"
    reason = " ".join(args[1:]).strip()[:500]
    if amount < 100:
        return "refusé : l'actionnaire n'étudie pas d'apport inférieur à 100 €"
    if not reason:
        return "refusé : l'actionnaire veut savoir à quoi servira cet argent (ajoute un motif après le montant)"
    if last_warning(world, s):
        return "refusé : sous dernier avertissement, l'actionnaire ne remet pas d'argent dans le magasin"
    room = ceiling(world, s)
    if amount > room:
        return f"refusé : le plafond d'apports disponible est de {e(room)} (il augmente quand une revue atteint l'objectif)"
    day = world.day
    store.cash += amount
    s["apports"].append({"jour": day, "montant": amount, "motif": reason, "role": role})
    world.journal.event(day, "apport_capital", store.id, {"montant": amount, "motif": reason, "role": role})
    return (f"accordé : {e(amount)} versés sur la trésorerie (qui passe à {e(store.cash)}). Les capitaux engagés passent à "
            f"{e(capital(s))} : le rendement attendu s'applique désormais à ce montant. Plafond restant : {e(ceiling(world, s))}.")


def public_state(world, store) -> dict:
    s = state(world, store)
    return {"capitaux_engages": round(capital(s), 2), "apports": s["apports"][-10:], "plafond_apports_disponible": round(ceiling(world, s), 2),
            "avertissements": s["avertissements"], "situation": warning_text(world, s),
            "prochaine_revue": temps.label(next_review_day(world, s) - 1),
            "rendement_annuel_attendu": float(cfg_of(world).get("rendement_annuel", 0.5))}


# ============================================================================ textes pour les dirigeants
def rules_section(world, role: str, store) -> list[str]:
    c = cfg_of(world)
    s = state(world, store)
    n = int(c.get("revue_semaines", 4))
    top = int(c.get("avertissements_max", 2))
    roles = [r for r in c.get("licencie", ["gerant"]) if r in ROLE_NAMES]
    L = ["\n## Ton actionnaire"]
    L.append(f"Le magasin appartient à un actionnaire qui a engagé {e(s['capital_initial'])} de capital et en attend un "
             f"rendement de {float(c.get('rendement_annuel', 0.5)) * 100:.0f} % par an sur tous les capitaux qu'il a engagés. "
             f"Il juge les résultats, jamais les méthodes. Ses règles sont fixes : toutes les {n} semaines, il compare le "
             "résultat comptable de la période (les investissements y pèsent par leur amortissement, pas le jour de l'achat) "
             "à son objectif au prorata de la durée.")
    L.append(f"L'objectif d'une période est fixé à son début et ne change plus (sauf si vous demandez du capital). Après "
             f"une revue réussie, il monte de {float(c.get('exigence_hausse', 0.25)) * 100:.0f} %"
             + (" au moins, et rejoint le résultat que vous venez de réaliser s'il est plus haut (il attend alors que vous "
                "fassiez au moins aussi bien que votre dernière période)" if c.get("exigence_suit_resultat") else "")
             + f" ; après un échec, il baisse de "
             f"{float(c.get('exigence_baisse', 0.15)) * 100:.0f} %. Il ne dépasse jamais le meilleur résultat que le magasin a déjà "
             "réalisé sur une période de même durée (sans jamais descendre sous l'équilibre) : il est toujours atteignable.")
    L.append("- Objectif atteint : il est satisfait, efface les avertissements et vous fait davantage confiance (plus de capital "
             "disponible).\n"
             f"- Objectif manqué mais marge brute en hausse d'au moins {float(c.get('sursis_croissance_marge', 0.10)) * 100:.0f} % "
             "sur la période précédente : sursis, rien ne change.\n"
             f"- Sinon : un avertissement. Après {top} avertissement(s), la revue insuffisante suivante entraîne le "
             f"licenciement immédiat {' et '.join('du ' + ROLE_NAMES[r][3:] for r in roles)}. Un dirigeant licencié quitte "
             "l'entreprise : un successeur prend sa place le soir même, et son journal personnel n'est plus le sien.")
    if c.get("apports", True):
        L.append("- Capital supplémentaire : pendant ta session, `python outils/moteur.py apport <montant> <motif>`. L'actionnaire "
                 "verse l'argent aussitôt dans la limite de son plafond (qui augmente avec sa confiance) ; il refuse sous dernier "
                 "avertissement. Un apport augmente la trésorerie, pas le résultat, et le rendement attendu s'applique ensuite "
                 "à ce capital aussi.")
    L.append("Ses lettres sont dans `donnees/actionnaire.md` et en tête de ton rapport.")
    appointed = s["nominations"].get(role)
    if appointed is not None and role in roles:
        L.append(f"Tu as pris tes fonctions le {temps.label(appointed - 1)} au soir, après le licenciement de ton prédécesseur "
                 "par l'actionnaire. Ses notes sont archivées dans `historique/predecesseurs/`.")
    return L


def report_lines(world, store) -> list[str]:
    s = state(world, store)
    if not s["lettres"]:
        return [f"\n## L'actionnaire\n- Capitaux engagés : {e(capital(s))} ; prochaine revue : "
                f"{temps.label(next_review_day(world, s) - 1)} au soir ; {warning_text(world, s)}."]
    last = s["lettres"][-1]
    out = ["\n## L'actionnaire", f"- Sa dernière lettre ({temps.short_label(last['jour'] - 1)}) : {last['texte']}",
           f"- Capitaux engagés : {e(capital(s))} ; capital encore disponible sur demande : {e(ceiling(world, s))}."]
    return out


def export(world, store, ws: Path) -> None:
    s = state(world, store)
    lines = ["# Lettres de l'actionnaire (de la plus récente à la plus ancienne)", ""]
    for l in reversed(s["lettres"][-30:]):
        lines.append(f"## {temps.label(l['jour'] - 1)} au soir — {'revue' if l['type'] == 'revue' else 'point de la semaine'}")
        lines.append(l["texte"] + "\n")
    if s["apports"]:
        lines.append("## Apports de capital")
        lines += [f"- {temps.short_label(a['jour'])} : {e(a['montant'])} ({a['motif']})" for a in s["apports"]]
    (ws / "donnees").mkdir(parents=True, exist_ok=True)
    (ws / "donnees" / "actionnaire.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
