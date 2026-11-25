"""Effets cachés du moteur : ce qu'un dispositif, un service ou un aménagement installé par un magasin peut changer
dans la ville simulée (les observations sont produites par instruments.py à partir du registre d'événements ;
le catalogue de capteurs ci-dessous ne sert plus qu'à relire les anciennes sauvegardes).

Les agents ne voient jamais ce catalogue : ils décrivent librement leurs projets dans leur dossier, et un expert
indépendant (IA, voir gerants/projets.py) traduit chaque projet accepté dans ces termes, avec un coût, un délai et
un avis juridique. Le moteur applique ensuite les effets (bornés) et produit les données des capteurs à partir de
ce qui se passe vraiment dans le magasin.
"""
from __future__ import annotations

import hashlib
import math

# -------------------------------------------------------------------- capteurs (ce qui peut être observé)
CAPTEURS = {
    "comptage": ("creneau", "nombre d'entrées et de personnes (adultes et enfants) par créneau"),
    "parcours": ("visite", "trajet anonyme de chaque visiteur : rayons traversés dans l'ordre, durée de la visite, attente en caisse"),
    "profil_apparent": ("visite", "âge et sexe estimés à l'apparence (imprécis : ± 7 ans, 1 erreur de sexe sur 10), enfants présents"),
    "attention_produits": ("visite", "produits regardés ou pris en main sans être achetés, et produits pris, pour chaque visite"),
    "lien_ticket": ("visite", "rattache la visite observée à son ticket de caisse (donc à la carte de fidélité éventuelle)"),
    "reidentification": ("visite", "reconnaît une même personne d'une visite à l'autre (identifiant pseudonyme stable)"),
    "rue": ("creneau", "passants devant la vitrine du magasin par créneau"),
    "files_attente": ("creneau", "attente moyenne et maximale en caisse, nombre de caisses ouvertes"),
    "ruptures_rayon": ("creneau", "produits dont le rayon est vide au début du créneau"),
    "satisfaction_sortie": ("visite", "note de 1 à 5 laissée volontairement à la sortie (environ un client sur quatre)"),
}

# -------------------------------------------------------------------- effets (ce qui peut changer)
# id : (combinaison, minimum, maximum, neutre, description)
EFFETS = {
    "attractivite": ("somme", -0.6, 0.6, 0.0, "envie de choisir ce magasin pour ses courses (ciblable sur un public)"),
    "vitesse_caisse": ("produit", 0.4, 1.2, 1.0, "multiplie le temps d'attente en caisse"),
    "capacite_caisses": ("somme", 0, 2, 0, "caisses supplémentaires qui fonctionnent sans caissier"),
    "conservation_frais": ("somme", 0, 2, 0, "jours de conservation gagnés par les produits frais reçus"),
    "vol": ("produit", 0.2, 1.0, 1.0, "multiplie la démarque inconnue (vols, erreurs de caisse)"),
    "confort": ("somme", 0.0, 1.0, 0.0, "points de satisfaction (sur 10) gagnés à chaque visite"),
    "intrusion": ("max", 0, 3, 0, "sentiment d'être surveillé : 0 aucun, 1 discret et signalé, 2 visible, 3 oppressant "
                                  "(gêne les clients soucieux de leur vie privée)"),
    "recherche": ("produit", 0.4, 1.0, 1.0, "multiplie le temps perdu à chercher un produit en rayon"),
    "impulsion": ("produit", 1.0, 1.25, 1.0, "multiplie les achats d'impulsion"),
    "notoriete": ("somme", 0.0, 0.3, 0.0, "chance par semaine qu'un habitant qui ne connaît pas le magasin le découvre"),
    "capacite_rayon": ("somme", 0, 150, 0, "articles mis en rayon en plus chaque matin"),
    "etiquettes_electroniques": ("max", 0, 1, 0, "1 = étiquettes de prix électroniques (prix modifiables sans frais, et par créneau)"),
    "calcul": ("produit", 1.0, 20.0, 1.0, "multiplie le temps de calcul dont disposent les programmes du magasin à chaque "
                                         "exécution (ordinateur plus puissant, serveur, service de calcul)"),
}

CIBLES = {
    "tous": "tout le monde",
    "familles": "foyers avec enfants",
    "seniors": "personnes de 65 ans et plus",
    "jeunes": "moins de 35 ans",
    "petits_budgets": "foyers très attentifs aux prix",
    "presses": "personnes pressées, qui détestent attendre",
    "soucieux_vie_privee": "personnes soucieuses de leur vie privée",
    "connectes": "personnes à l'aise avec le numérique (surtout moins de 45 ans)",
    "mobilite_reduite": "personnes à mobilité réduite",
}


def cible_ok(cible: str, a: dict) -> bool:
    if cible in (None, "", "tous"):
        return True
    if cible == "familles":
        return a.get("nb_enfants", 0) > 0
    if cible == "seniors":
        return a.get("age", 40) >= 65
    if cible == "jeunes":
        return a.get("age", 40) < 35
    if cible == "petits_budgets":
        return a.get("sensibilite_prix", 0.5) > 0.6
    if cible == "presses":
        return a.get("tolerance_attente", 0.5) < 0.35
    if cible == "soucieux_vie_privee":
        return a.get("souci_vie_privee", 0.3) > 0.6
    if cible == "connectes":
        return a.get("age", 40) < 45
    if cible == "mobilite_reduite":
        return bool(a.get("mobilite_reduite"))
    return False


def clamp_effect(kind: str, value: float) -> float:
    _, lo, hi, neutral, _ = EFFETS[kind]
    try:
        v = float(value)
    except (TypeError, ValueError):
        return neutral
    if math.isnan(v) or math.isinf(v):
        return neutral
    return max(lo, min(hi, v))


def active(systems: dict) -> list[dict]:
    return [s for s in (systems or {}).values() if s.get("etat") == "actif"]


def effect_value(systems: dict, kind: str) -> float:
    """Valeur combinée d'un effet sur l'ensemble des systèmes en service (bornée)."""
    comb, lo, hi, neutral, _ = EFFETS[kind]
    vals = [clamp_effect(kind, e.get("valeur")) for s in active(systems) for e in s.get("effets", [])
            if e.get("id") == kind and kind != "attractivite"]
    if not vals:
        return neutral
    if comb == "produit":
        out = 1.0
        for v in vals:
            out *= v
    elif comb == "max":
        out = max(vals)
    else:
        out = sum(vals)
    return max(lo, min(hi, out))


def attraction(systems: dict, attrs: dict) -> float:
    total = sum(clamp_effect("attractivite", e.get("valeur")) for s in active(systems) for e in s.get("effets", [])
                if e.get("id") == "attractivite" and cible_ok(e.get("cible", "tous"), attrs))
    return max(-0.8, min(0.8, total))


def visible_features(systems: dict) -> list[tuple[str, str]]:
    return [(name, s["visible_clients"]) for name, s in (systems or {}).items()
            if s.get("etat") == "actif" and s.get("visible_clients")]


# -------------------------------------------------------------------- données produites
def pseudonym(store_id: str, system: str, customer_id: str) -> str:
    return "V" + hashlib.sha256(f"{store_id}|{system}|{customer_id}".encode()).hexdigest()[:6].upper()


def consents(store, name: str, customer, rnd) -> bool:
    """Accord demandé une fois à chaque visiteur quand le système exige un consentement (selon sa personnalité)."""
    book = store.system_consents.setdefault(name, {})
    if customer.id not in book:
        a = customer.attrs
        p = 0.45 - 0.6 * a.get("souci_vie_privee", 0.3) + 0.25 * a.get("gout_promos", 0.5)
        book[customer.id] = rnd.random() < max(0.02, min(0.9, p))
    return book[customer.id]


def visit_record(store, name: str, system: dict, fact: dict, customer, rnd) -> dict | None:
    """Ce qu'un système voit d'une visite (None s'il ne la voit pas)."""
    caps = {c["id"]: c for c in system.get("capteurs", []) if c.get("id") in CAPTEURS and CAPTEURS[c["id"]][0] == "visite"}
    if not caps:
        return None
    cover = max(c.get("couverture", 1.0) for c in caps.values())
    if rnd.random() > cover:
        return None
    rec = {"jour": fact["jour"], "creneau": fact["creneau"], "heure": fact["heure"]}
    if "reidentification" in caps:
        need_consent = bool(system.get("consentement"))
        if not need_consent or consents(store, name, customer, rnd):
            rec["visiteur"] = pseudonym(store.id, name, customer.id)
        else:
            rec["visiteur"] = ""
    if "parcours" in caps:
        rec.update({"rayons": "-".join(str(r) for r in fact["rayons"]), "duree_min": fact["duree_min"],
                    "attente_min": fact["attente_min"]})
    if "profil_apparent" in caps:
        age = int(round(fact["age"] + rnd.gauss(0, 7)))
        sexe = fact["sexe"] if rnd.random() < 0.9 else ("M" if fact["sexe"] == "F" else "F")
        rec.update({"age_estime": max(15, age), "sexe_estime": sexe, "enfants": int(fact["enfants"])})
    if "attention_produits" in caps:
        rec.update({"regardes_non_achetes": ",".join(fact["regardes"]), "pris": ",".join(fact["pris"])})
    if "lien_ticket" in caps:
        rec["ticket"] = fact["ticket"]
    if "satisfaction_sortie" in caps:
        if rnd.random() < 0.25:
            rec["note_sortie"] = fact["note"]
        elif len(caps) == 1:
            return None
    return rec


def slot_record(system: dict, fact: dict) -> dict | None:
    caps = {c["id"] for c in system.get("capteurs", []) if c.get("id") in CAPTEURS and CAPTEURS[c["id"]][0] == "creneau"}
    if not caps:
        return None
    rec = {"jour": fact["jour"], "creneau": fact["creneau"]}
    if "comptage" in caps:
        rec.update({"entrees": fact["entrees"], "personnes": fact["personnes"]})
    if "rue" in caps:
        rec["passants"] = fact["passants"]
    if "files_attente" in caps:
        rec.update({"attente_moyenne": fact["attente_moyenne"], "attente_max": fact["attente_max"],
                    "caisses_ouvertes": fact["caisses"]})
    if "ruptures_rayon" in caps:
        rec["rayons_vides"] = ",".join(fact["vides"])
    return rec


def record(store, name: str, rec: dict) -> None:
    buf = store.system_data.setdefault(name, [])
    buf.append(rec)
    if len(buf) > 4000:
        del buf[:len(buf) - 3000]


def prune(store, day: int, keep_days: int = 28) -> None:
    for name, buf in store.system_data.items():
        if buf and buf[0].get("jour", day) < day - keep_days:
            store.system_data[name] = [r for r in buf if r.get("jour", day) >= day - keep_days]


def catalogue_text() -> str:
    """Description du catalogue pour l'expert (jamais montrée aux agents)."""
    lines = ["Capteurs possibles (ce que le moteur sait mesurer ; niveau visite ou créneau) :"]
    lines += [f"- {k} ({lvl}) : {d}" for k, (lvl, d) in CAPTEURS.items()]
    lines.append("\nEffets possibles (bornes entre crochets ; neutre entre parenthèses) :")
    lines += [f"- {k} [{lo} ; {hi}] ({neutral}) : {d}" for k, (_, lo, hi, neutral, d) in EFFETS.items()]
    lines.append("\nCibles de l'effet attractivite : " + "; ".join(f"{k} = {v}" for k, v in CIBLES.items()))
    return "\n".join(lines)
