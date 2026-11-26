"""Données remises au code d'un gérant : uniquement ce que son magasin observe, plus les informations publiques.

Jamais d'identifiant interne de client, jamais d'attribut caché, jamais de donnée du concurrent hors public. Un ticket sans
carte de fidélité porte seulement ce que lit un terminal de paiement : espèces, ou les 4 derniers chiffres d'une carte.
"""
from __future__ import annotations

from .. import personnel, temps
from ..catalogue import PRODUCTS, RAYONS
from ..enquetes import disclosed_answers
from ..lois import min_price
from . import projets

LEDGER_KEYS = ("ventes", "cout_des_ventes", "demarque", "demarque_inconnue", "charges_fixes", "salaires",
               "caisses_supplementaires", "recrutement", "indemnites", "marketing", "veille", "etiquettes", "systemes",
               "investissements", "investissement_etiquettes", "acquisitions_immobilisees", "amortissements", "horaires_etendus", "sous_agents", "remises_coupons", "remises_points", "amendes", "reamenagement", "referencement")


HISTORY_DAYS = 91   # ventes et comptes du jour remis aux programmes (13 semaines ; 28 jours avant)
TICKET_DAYS = 42    # tickets détaillés remis aux programmes (6 semaines ; 14 jours avant)


def _expiry_days(store, pid: str, day: int):
    lots = [l for l in store.lots[pid] if l.qty > 0 and l.expiry >= day]
    if not lots or PRODUCTS[pid].conservation_j >= 5000:
        return None
    return min(l.expiry for l in lots) - day


def products(world, store) -> list[dict]:
    day = world.day
    sup = world.supplier
    out = []
    for pid, p in PRODUCTS.items():
        loc = store.planogram[pid]
        promo = store.promo_for(pid, day)
        out.append({"id": pid, "nom": p.nom, "besoin": p.besoin, "gamme": p.gamme, "reference": bool(store.listed[pid]),
                    "prix": store.prices[pid], "prix_membre": store.member_prices.get(pid), "prix_achat": sup.prices[pid],
                    "prix_conseille": p.prix_conseille, "seuil_revente_perte": min_price(pid, sup, world.cfg["lois"]),
                    "stock": store.available(pid, day), "en_rayon": store.on_shelf(pid, day),
                    "en_reserve": store.in_reserve(pid, day), "stock_cible": store.targets.get(pid, 0),
                    "en_commande": sum(q for _, x, q, _ in store.on_order if x == pid),
                    "stock_date_courte": store.short_date_qty(pid, day), "peremption_jours": _expiry_days(store, pid, day),
                    "conservation_jours": p.conservation_j if p.conservation_j < 5000 else None,
                    "ventes_7j": sum(store.sales.get(d, {}).get(pid, [0])[0] for d in range(day - 7, day)),
                    "rayon": loc["rayon"], "segment": loc["segment"], "niveau": loc["niveau"], "facings": loc.get("facings", 1),
                    "mises_en_avant": [k for k, v in store.secondary.items() if pid in v],
                    "promo": promo.label() if promo else None, "rupture_fournisseur": not sup.can_deliver(pid, day)})
    return out


def ticket_view(t: dict) -> dict:
    return {"ticket": t["id"], "jour": t["jour"], "creneau": t["creneau"], "membre": t["membre"], "total": t["total"],
            "paiement": t.get("paiement", ""), "carte_bancaire": t.get("carte_bancaire", ""),
            "sous_total": t["sous_total"], "remise_coupons": t["remise_coupons"], "remise_points": t["remise_points"],
            "coupons": list(t["coupons"]),
            "articles": [{"produit": l["produit"], "quantite": l["quantite"], "prix_unitaire": l["prix_unitaire"],
                          "total": l["total"], "promo": l["promo"], "date_courte": l["date_courte"]} for l in t["lignes"]]}


def member_view(m) -> dict:
    return {"membre": m.id, "inscrit_jour": m.jour_inscription, "donnees": dict(m.donnees),
            "joignable_email": bool(m.consent_email and not m.desabonne_email),
            "joignable_sms": bool(m.consent_sms and not m.desabonne_sms), "points": round(m.points), "visites": m.visites,
            "total_depense": round(m.total_depense, 2), "derniere_visite": m.derniere_visite, "tags": list(m.tags), "notes": m.notes,
            "passages": [{"jour": d, "creneau": c, "montant": x} for d, c, x in m.__dict__.get("historique", [])]}


def instruments_view(store) -> dict:
    return {name: {"etat": s.get("etat"), "installation": s.get("installation", ""), "fonctionnalites": s.get("fonctionnalites", []),
                   "donnees": s.get("donnees", []), "cout_hebdomadaire": s.get("cout_hebdomadaire", 0),
                   "en_service_depuis": s.get("actif_le")} for name, s in store.systems.items()}


def snapshot(world, store, light: bool = False) -> dict:
    """Copie des données du magasin. `light` : version allégée pour le code appelé à chaque ticket."""
    day = world.day
    base = {"jour": day, "jour_semaine": temps.day_name(day), "semaine": day // 7 + 1, "date": temps.date_of(day).isoformat(),
            "creneaux": temps.slots(world.cfg), "meteo": world.weather_text(),
            "magasin": {"id": store.id, "nom": store.nom, "slogan": store.slogan},
            "tresorerie": round(store.cash, 2), "resultat": round(store.profit, 2), "etiquettes_electroniques": store.esl,
            "fidelite": {**store.loyalty, "prix_membres": dict(store.member_prices)}, "memoire": store.automation_memory,
            "graine": day * 7919 + ord(store.id[0]), "produits": products(world, store), "rayons": {str(k): v for k, v in RAYONS.items()},
            "personnel": personnel.rows(store, day), "instruments": instruments_view(store)}
    if light:
        base["systemes"] = projets.program_view(store, day, 1)
        return base
    by_member = {m.client: m.id for m in store.members.values()}
    ventes = [{"jour": d, "produit": pid, "quantite": v[0], "ca": round(v[1], 2), "cout": round(v[2], 2)}
              for d in range(max(0, day - HISTORY_DAYS), day) for pid, v in store.sales.get(d, {}).items()]
    tickets = [ticket_view(t) for t in store.tickets if t["jour"] >= day - TICKET_DAYS]
    compta = []
    for d in range(max(0, day - HISTORY_DAYS), day):
        led = store.ledger.get(d, {})
        wl = store.work_log.get(d, {})
        compta.append({"jour": d, **{k: round(led.get(k, 0.0), 2) for k in LEDGER_KEYS},
                       "passages": sum(store.visits.get(d, {}).values()), "creneaux_salaries": wl.get("creneaux_salaries", 0),
                       "creneaux_interim": wl.get("creneaux_interim", 0), "creneaux_base": wl.get("creneaux_base", 0)})
    coupons = [{"coupon": c.id, "campagne": c.campagne, "offre": c.offre, "valeur": c.valeur, "produit": c.produit, "canal": c.canal,
                "debut": c.debut, "fin": c.fin, "membre": by_member.get(c.detenteur), "utilise": c.utilise,
                "jour_utilisation": c.jour_utilisation} for c in list(store.coupons.values())[-300:]]
    survey = store.price_survey or {}
    other = world.other_store(store.id)
    public_other = world.public_information(other.id)
    news = [n for d, n in world.news if d >= day - 14] + [a for d, a in world.supplier.announcements if d >= day - 14]
    base.update({
        "places_frais_libres": store.cold_space(day),
        "horaires": [{"jour": d, "creneaux": [s for s in temps.slots(world.cfg) if store.is_open(d, s)]}
                     for d in range(day, day + 7)],
        "offres_locales": {pid: offer for pid in PRODUCTS if (offer := world.supplier.local_offer(pid))}
                          if world.cfg.get("marche_local", {}).get("fournisseur_local") else {},
        "ventes": ventes, "tickets": tickets, "compta": compta, "coupons": coupons,
        "membres": [member_view(m) for m in store.members.values()],
        "prix_individuels": {k: dict(v) for k, v in store.individual_prices.items()},
        "reponses": disclosed_answers(store),
        "experiences_coupons": store.campaign_results(),
        "promotions": [{"id": p.id, "produits": list(p.produits), "mecanique": p.mecanique, "valeur": p.valeur, "y": p.y,
                        "debut": p.debut, "fin": p.fin, "affiche": p.affiche} for p in store.promotions if p.fin >= day],
        "enquete": dict(store.survey) if store.survey else None,
        "prix_concurrent": {p: v["prix"] for p, v in survey.get("prix", {}).items() if v.get("prix")},
        "releve_concurrent": survey or None,
        "avis": [{"avis": r["id"], "jour": r["jour"], "magasin": r["magasin"], "auteur": r["auteur"], "note": r["note"],
                  "texte": r["texte"], "reponse": r.get("reponse")} for r in public_other["avis"] if r["jour"] >= day - 60],
        "concurrent": {"id": other.id, "nom": other.nom, "slogan": public_other["slogan"], "horaires": public_other["horaires"],
                       "prospectus": public_other["prospectus"][-3:],
                       "publicites": public_other["publicites"][-3:]},
        "actualites": news,
        "systemes": projets.program_view(store, day, 28),
        "tables": {k: list(v)[-2000:] for k, v in store.program_outputs.items()},
        "equipe": [{"jour": d, "role": r, "texte": t} for d, r, t in getattr(world, "team_board", {}).get(store.id, [])[-10:]],
    })
    return base
