"""Événements : météo, chocs économiques programmés et événements de vie des foyers."""
from __future__ import annotations

from . import temps
from .population import METIERS, daily_needs, refresh_economics
from .rng import clamp


def economic_events(world, day: int) -> None:
    """Applique les chocs de la configuration le lundi de la semaine indiquée (semaine 1 = première semaine)."""
    if temps.dow(day) != 0:
        return
    w = temps.week_of(day) + 1
    for ev in world.cfg.get("evenements", []):
        if int(ev.get("semaine", -1)) != w:
            continue
        if ev["type"] == "inflation":
            world.supplier.inflation(day, ev["categories"], float(ev["hausse"]))
            world.journal.event(day, "choc_inflation", "monde", ev)
            world.news.append((day, f"Les prix de gros augmentent de {float(ev['hausse']) * 100:.0f} % ({', '.join(ev['categories'])})."))
        elif ev["type"] == "penurie":
            world.supplier.shortage(day, ev["produits"], int(ev.get("jours", 7)))
            world.journal.event(day, "choc_penurie", "monde", ev)
        elif ev["type"] == "fermeture_employeur":
            # Choc de revenus : un employeur de la région ferme, une partie des actifs perd son emploi.
            rnd = world.rng.stream(f"choc_{w}")
            workers = sorted((c for c in world.active_customers()
                              if c.attrs["statut"] in ("cdi", "cdd", "interim")
                              and (not ev.get("quartiers") or c.attrs.get("quartier") in ev["quartiers"])), key=lambda c: c.id)
            hit = rnd.sample(workers, min(len(workers), max(1, round(len(workers) * float(ev.get("part", 0.3)))))) if workers else []
            for c in hit:
                apply_life_event(world, c, "perte_emploi", day, rnd)
            world.journal.event(day, "choc_emploi", "monde", {**ev, "foyers": [c.id for c in hit]})
            world.news.append((day, f"{ev.get('texte') or 'Une entreprise de la région ferme ses portes'} : "
                                    f"environ {len(hit)} foyers de la ville sont touchés par le chômage."))
        elif ev["type"] == "carburant":
            # Le plein coûte plus cher : l'hypermarché éloigné devient moins attractif pour les foyers motorisés.
            hyp = world.cfg.setdefault("hypermarche", {})
            old = float(hyp.get("carburant", 3.0))
            hyp["carburant"] = round(old * float(ev.get("facteur", 1.5)), 2)
            world.__dict__.setdefault("cfg_chocs", {})["carburant"] = hyp["carburant"]  # conservé à la reprise
            world.journal.event(day, "choc_carburant", "monde", {**ev, "avant": old, "apres": hyp["carburant"]})
            world.news.append((day, f"Le prix du carburant s'envole (+{(float(ev.get('facteur', 1.5)) - 1) * 100:.0f} %)."))


def _recompute(c) -> None:
    refresh_economics(c.attrs)
    c.daily = daily_needs(c.attrs)
    for n in c.daily:
        c.state["garde_manger"].setdefault(n, 0.0)


def life_events(world, day: int, rnd) -> None:
    for c in list(world.active_customers()):
        a = c.attrs
        works = a["statut"] in ("cdi", "cdd", "interim", "independant")
        events = []
        if works:
            events.append(("perte_emploi", 0.15 if a["statut"] in ("cdd", "interim") else 0.04))
            events.append(("augmentation", 0.10))
            if a["age"] >= 62:
                events.append(("retraite", 0.35))
        if a["statut"] == "chomage":
            events.append(("nouvel_emploi", 0.35))
        if a["type_foyer"] in ("couple", "famille") and 25 <= a["age"] <= 42:
            events.append(("naissance", 0.07))
        if any(k >= 16 for k in a["enfants_ages"]):
            events.append(("depart_enfant", 0.08))
        if a["regime"] == "omnivore":
            events.append(("nouveau_regime", 0.03))
        if a["voiture"]:
            events.append(("panne_voiture", 0.06))
        elif a["niveau_revenu"] > 0.3 and a["statut"] != "etudiant":
            events.append(("achat_voiture", 0.04))
        events.append(("demenagement", 0.04))
        if c.state.get("panne_jusqua") is not None and day >= c.state["panne_jusqua"]:
            a["voiture"] = True
            c.state["panne_jusqua"] = None
        for name, annual in events:
            if rnd.random() < annual / 365:
                apply_life_event(world, c, name, day, rnd)
                break


def apply_life_event(world, c, name: str, day: int, rnd) -> None:
    a = c.attrs
    f = a["genre"] == "F"
    text = ""
    if name == "perte_emploi":
        old = a["metier"]
        a["statut"], a["horaires"] = "chomage", "libre"
        a["metier"] = f"en recherche d'emploi (ancien métier : {old})"
        a["revenu_mensuel"] = round(a["revenu_mensuel"] * 0.62)
        a["pression_temporelle"] = clamp(a["pression_temporelle"] - 0.25)
        a["sensibilite_prix"] = clamp(a["sensibilite_prix"] + 0.15)
        a["gout_promos"] = clamp(a["gout_promos"] + 0.1)
        text = "Mauvaise nouvelle : perte d'emploi. Il va falloir surveiller chaque euro."
    elif name == "nouvel_emploi":
        lvl = a["niveau_metier"]
        m, fm, h = rnd.choice(METIERS[lvl])
        a["statut"], a["horaires"], a["metier"] = "cdi", h, (fm if f else m)
        a["revenu_mensuel"] = round(a["revenu_mensuel"] / 0.62 if a["revenu_mensuel"] < 2000 else a["revenu_mensuel"] * 1.3)
        a["pression_temporelle"] = clamp(a["pression_temporelle"] + 0.2)
        text = f"Nouveau travail : {a['metier']}. Moins de temps, un peu plus d'argent."
    elif name == "augmentation":
        a["revenu_mensuel"] = round(a["revenu_mensuel"] * 1.08)
        text = "Augmentation de salaire."
    elif name == "retraite":
        a["statut"], a["horaires"] = "retraite", "libre"
        a["metier"] = "retraitée" if f else "retraité"
        a["revenu_mensuel"] = round(a["revenu_mensuel"] * 0.75)
        a["pression_temporelle"] = 0.15
        text = "Départ à la retraite : enfin du temps."
    elif name == "naissance":
        a["enfants_ages"] = sorted(a["enfants_ages"] + [0])
        if a["type_foyer"] == "couple":
            a["type_foyer"], a["situation"] = "famille", "en couple avec enfants"
        a["pression_temporelle"] = clamp(a["pression_temporelle"] + 0.2)
        c.state["fatigue"] = clamp(c.state["fatigue"] + 0.3)
        text = "Naissance d'un bébé ! Couches et petits pots au programme."
    elif name == "depart_enfant":
        kids = sorted(a["enfants_ages"])
        kids.pop()
        a["enfants_ages"] = kids
        text = "L'aîné quitte la maison : le frigo se vide moins vite."
    elif name == "nouveau_regime":
        a["regime"] = rnd.choice(["vegetarien", "vegetarien", "sans_gluten"])
        text = f"Nouveau régime alimentaire : {a['regime'].replace('_', ' ')}."
    elif name == "panne_voiture":
        a["voiture"] = False
        c.state["panne_jusqua"] = day + 14
        text = "La voiture est au garage pour deux semaines."
    elif name == "achat_voiture":
        a["voiture"] = True
        a["abonnement_bus"] = False
        text = "Achat d'une voiture : tout devient accessible."
    elif name == "demenagement":
        c.active = False
        world.journal.event(day, "evenement_vie", c.id, {"type": name, "texte": "Le foyer quitte la ville."})
        world.add_new_household(day)
        return
    _recompute(c)
    c.remember(day, text, 10, None, ("vie",))
    world.journal.event(day, "evenement_vie", c.id, {"type": name, "texte": text})
