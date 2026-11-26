"""Offres de questionnaire, décision du répondant et données déclarées au commerçant."""
from __future__ import annotations

import math
import re
import unicodedata

from .catalogue import PRODUCTS, is_allowed
from .lois import clean_text
from .magasin import Coupon
from .rng import clamp


BON_OFFRES = ("aucun", "remise_euros_panier", "remise_pct_panier", "remise_pct_produit", "produit_offert")


def validate_reward(raw):
    if raw is None or (isinstance(raw, dict) and raw.get("offre") == "aucun"):
        return None
    if not isinstance(raw, dict) or raw.get("offre") not in BON_OFFRES:
        raise ValueError("type de bon inconnu")
    offer = raw["offre"]
    pid = raw.get("produit") or None
    if offer in ("remise_pct_produit", "produit_offert") and (not isinstance(pid, str) or pid not in PRODUCTS):
        raise ValueError("produit du bon inconnu")
    try:
        amount = float(raw.get("valeur", 0))
        minimum = float(raw.get("panier_min", 0))
        days = raw.get("jours_validite", 7)
        if type(days) is not int or not 1 <= days <= 30 or not math.isfinite(minimum) or minimum < 0:
            raise ValueError
        if not math.isfinite(amount):
            raise ValueError
        if offer == "remise_euros_panier" and not 0.5 <= amount <= 30:
            raise ValueError
        if offer.startswith("remise_pct") and not 1 <= amount <= 60:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise ValueError("montant, seuil ou durée du bon invalide") from None
    return {"offre": offer, "valeur": amount, "produit": pid if offer in ("remise_pct_produit", "produit_offert") else None,
            "panier_min": minimum, "jours_validite": days}


def invite(customer, store, survey):
    """Un foyer garde la première invitation en attente et participe une fois par édition."""
    key = survey.get("id", f"{store.id}-J{survey['jour']}")
    seen = customer.state.setdefault("enquetes_vues", {})
    if store.id in customer.pending_surveys or seen.get(store.id) == key:
        return False
    m = store.member_of(customer.id) if store.loyalty["actif"] else None
    customer.pending_surveys[store.id] = {**survey, "id": key, "membre_propose": m.id if m else None,
                                          "valeur_point": store.loyalty["valeur_point"] if m else 0.0}
    seen[store.id] = key
    count(store, key, "invitations")
    return True


def count(store, campaign: str, what: str) -> None:
    """Participation à un questionnaire, telle que le magasin la constate (invités, réponses, refus de répondre)."""
    st = store.__dict__.setdefault("enquete_stats", {}).setdefault(campaign, {"invitations": 0, "reponses": 0, "refus": 0})
    st[what] = st.get(what, 0) + 1


def reward_text(survey):
    parts = []
    if survey.get("recompense_points") and survey.get("membre_propose"):
        parts.append(f"{survey['recompense_points']} points")
    bon = survey.get("bon")
    if bon:
        coupon = Coupon("", "", bon["offre"], bon["valeur"], bon["produit"], bon["panier_min"],
                        0, 0, None, "enquete", "")
        parts.append(f"{coupon.describe()}, valable {bon['jours_validite']} jours dès demain")
    return " et ".join(parts) or "aucune récompense"


def _question_text(question):
    plain = unicodedata.normalize("NFKD", question.lower())
    return "".join(c for c in plain if not unicodedata.combining(c))


def _answer(customer, sid, question):
    """Le mode formule sait répondre à des sujets limités. Le mode IA lit le texte libre."""
    text = _question_text(question)
    attrs = customer.attrs
    b = customer.beliefs[sid]
    if re.search(r"budget|depens|montant.{0,15}panier", text):
        monthly = bool(re.search(r"mensuel|mois", text))
        amount = attrs["budget_courses_mensuel"] if monthly else attrs["panier_type_euros"]
        return f"Environ {max(5, round(amount / 5) * 5)} € {'par mois' if monthly else 'par visite'}."
    if re.search(r"horaire|creneau|heure|moment de la journee", text):
        return "Je préfère faire mes courses le " + attrs["creneau_prefere"] + "."
    if re.search(r"frequen|combien de fois|rythme", text):
        return f"En général, {attrs['frequence_courses_semaine']} fois par semaine."
    if re.search(r"contact|canal|e-?mail|sms|prospectus", text):
        return "Je préfère le canal " + attrs["canal_prefere"] + "."
    if re.search(r"produit|rayon|article|marque|assortiment|acheter", text):
        names = [PRODUCTS[p].nom for p in attrs["produits_favoris"] if p in PRODUCTS]
        return "Parmi mes produits préférés, il y a " + " et ".join(names[:2]) + "." if names else None
    if re.search(r"prix|cher|promo|reduction", text):
        return "Je trouve ce magasin " + customer.price_word(sid) + "."
    if re.search(r"foyer|famille|personnes|enfants", text):
        return f"Nous sommes {attrs['taille_foyer']} au foyer, dont {attrs['nb_enfants']} enfant(s)."
    if re.search(r"qualite|satisf|avis|accueil|experience", text):
        return f"Je donnerais {round(b['satisfaction'] / 2)}/5 à mon expérience ici."
    return None


def formula_response(customer, sid, survey, rnd):
    a = customer.attrs
    answers = [_answer(customer, sid, q) for q in survey["questions"]]
    if not answers or any(answer is None for answer in answers):
        return None
    bon = survey.get("bon")
    reward = survey.get("recompense_points", 0) * survey.get("valeur_point", 0)
    if bon:
        basket = min(a["panier_type_euros"], max(0., customer.state["budget_restant"]))
        eligible = basket >= bon["panier_min"]
        estimate = min(basket, bon["valeur"])
        if bon["produit"]:
            p = PRODUCTS[bon["produit"]]
            eligible = eligible and is_allowed(p, a) and customer.daily.get(p.besoin, 0) > 0
            unit = customer.state["prix_juste"][p.id]
            estimate = unit if bon["offre"] == "produit_offert" else unit * bon["valeur"] / 100
        elif bon["offre"] == "remise_pct_panier":
            estimate = basket * bon["valeur"] / 100
        if eligible:
            chance_to_visit = min(1., bon["jours_validite"] * a["frequence_courses_semaine"] / 7)
            reward += estimate * chance_to_visit
    trust = customer.beliefs[sid]["confiance"] / 10
    privacy = a["souci_vie_privee"]
    time_cost = len(answers) * (0.04 + 0.14 * customer.state["pression_jour"])
    personal = any(re.search(r"budget|famille|foyer|enfant", _question_text(q)) for q in survey["questions"])
    gain = (1 - math.exp(-reward / 3)) * (0.35 + 0.5 * a["receptivite_coupons"])
    probability = clamp(0.28 + 0.3 * trust + gain - time_cost - privacy * (0.15 + 0.25 * personal))
    if rnd.random() >= probability:
        return None
    link = bool(survey.get("membre_propose")) and rnd.random() < clamp(trust * (1 - privacy))
    return {"magasin": sid, "invitation": invitation_id(sid, survey), "reponses": answers, "lier_carte": link}


def invitation_id(sid, survey):
    return survey.get("id", f"{sid}-J{survey['jour']}")


def submit(world, customer, response):
    sid = world.resolve_store(response.get("magasin"))
    survey = customer.pending_surveys.get(sid)
    if not survey or sid not in world.stores:
        return False
    # Une réponse à une autre campagne que celle en attente porterait sur d'autres questions : refusée.
    if response.get("invitation", invitation_id(sid, survey)) != invitation_id(sid, survey):
        return False
    # Une question laissée sans réponse (ou une réponse en trop) ne fait plus perdre tout le questionnaire : un quart des
    # réponses était rejeté dans la partie dynamique_claude_7s. Les réponses manquantes sont marquées, jamais inventées.
    answers = response.get("reponses")
    if isinstance(answers, str):
        answers = [answers]
    if not isinstance(answers, list):
        return False
    n = len(survey["questions"])
    answers = [a if isinstance(a, str) and a.strip() else "" for a in answers[:n]]
    if not any(answers):
        return False
    answers = [clean_text(a, 300) if a else "(sans réponse)" for a in answers] + ["(sans réponse)"] * (n - len(answers))
    st, day = world.stores[sid], world.day
    member = st.member_of(customer.id)
    # Une ancienne invitation sans proposition explicite de rapprochement reste anonyme.
    identify = bool(member and member.id == survey.get("membre_propose")
                    and (response.get("lier_carte") is True or survey.get("fiche_inscription")))
    campaign = invitation_id(sid, survey)
    st.survey_answers.append({"enquete": campaign, "jour": day, "questions": list(survey["questions"]),
                              "reponses": answers, "membre": member.id if identify else None})
    if member and (survey.get("membre_propose") == member.id or "membre_propose" not in survey):
        member.points += survey.get("recompense_points", 0)
    bon = survey.get("bon")
    if bon:
        coupon = st.new_coupon(customer.id if identify else None, bon["offre"], bon["valeur"], bon["produit"],
                               bon["panier_min"], day + 1, day + bon["jours_validite"], "enquete", "", campaign)
        customer.receive_coupon(coupon)
    del customer.pending_surveys[sid]
    count(st, campaign, "reponses")
    world.journal.event(day, "reponse_enquete", sid, {"enquete": campaign, "liee": identify, "bon_emis": bool(bon)})
    return True


def declined(world, customer, response) -> bool:
    """Le foyer renvoie l'invitation sans aucune réponse : il décline le questionnaire (ce n'est pas une erreur).
    L'invitation est retirée, comme en vrai un client qui dit non au questionnaire ; rien n'est transmis au magasin."""
    sid = world.resolve_store(response.get("magasin"))
    survey = customer.pending_surveys.get(sid)
    if not survey or response.get("invitation", invitation_id(sid, survey)) != invitation_id(sid, survey):
        return False
    answers = response.get("reponses")
    if answers in (None, "", []) or (isinstance(answers, list) and not any(isinstance(a, str) and a.strip() for a in answers)):
        del customer.pending_surveys[sid]
        if sid in world.stores:
            count(world.stores[sid], invitation_id(sid, survey), "refus")
        world.journal.event(world.day, "enquete_declinee", sid, {"enquete": invitation_id(sid, survey), "client": customer.id})
        return True
    return False


def disclosed_answers(store):
    """Projection rétrocompatible. Les anciennes réponses n'enregistraient pas le consentement."""
    return [{"enquete": row.get("enquete"), "jour": row.get("jour"), "questions": list(row.get("questions", [])),
             "reponses": list(row["reponses"]), "membre": row.get("membre") if "enquete" in row else None}
            for row in store.survey_answers]
