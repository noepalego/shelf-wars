"""Génération de la population : ~130 attributs par foyer, biographie et réseau social."""
from __future__ import annotations

import copy
import math
import statistics

from .catalogue import NEEDS, PRODUCTS, is_allowed, products_for_need
from .rng import clamp

QUARTIERS = {
    "centre": {"nom": "centre-ville", "centre": (10, 10), "rayon": 2.5, "revenu": 0.5},
    "residentiel": {"nom": "quartier résidentiel", "centre": (5, 15), "rayon": 2.5, "revenu": 0.85},
    "populaire": {"nom": "quartier populaire", "centre": (15, 5), "rayon": 2.5, "revenu": 0.2},
    "peripherie": {"nom": "périphérie", "centre": (16, 16), "rayon": 3.0, "revenu": 0.45},
}
PARTS_QUARTIERS = {"centre": 0.27, "residentiel": 0.27, "populaire": 0.27, "peripherie": 0.19}
POSITIONS_MAGASINS = [(8, 12), (12, 8)]
POSITION_MARCHE = (10, 10)
KM_PAR_CASE = 0.2

TYPES_PAR_QUARTIER = {
    "centre": ["celibataire", "etudiant", "couple", "colocation", "celibataire", "famille", "couple", "monoparentale"],
    "residentiel": ["famille", "famille", "couple", "retraites", "famille", "couple", "famille_nombreuse", "retraites"],
    "populaire": ["famille_nombreuse", "monoparentale", "celibataire", "famille", "retraites", "monoparentale", "etudiant", "famille"],
    "peripherie": ["retraites", "retraites", "couple", "famille", "retraites", "celibataire"],
}

PRENOMS_F = ["Nadia", "Sophie", "Camille", "Fatou", "Léa", "Isabelle", "Chloé", "Yasmine", "Martine", "Julie", "Aïcha",
             "Monique", "Inès", "Émilie", "Sandrine", "Mei", "Laura", "Nathalie", "Sarah", "Jeanne", "Khadija", "Manon",
             "Valérie", "Amandine", "Lucie", "Odile", "Samira", "Pauline", "Céline", "Hélène", "Awa", "Elodie"]
PRENOMS_M = ["Karim", "Thomas", "Julien", "Mamadou", "Lucas", "Philippe", "Antoine", "Mehdi", "Bernard", "Nicolas",
             "Youssef", "Gérard", "Hugo", "Stéphane", "Wei", "Maxime", "Patrick", "Sofiane", "Louis", "Jean-Pierre",
             "Omar", "Théo", "Olivier", "David", "Moussa", "Christian", "Rachid", "Kévin", "Alain", "Paul", "Ibrahim", "Rémi"]
NOMS = ["Martin", "Haddad", "Bernard", "Diallo", "Petit", "Moreau", "Nguyen", "Benali", "Laurent", "Garcia", "Lefèvre",
        "Traoré", "Roux", "Fontaine", "Chevalier", "Mercier", "Bouaziz", "Dupont", "Lambert", "Faure", "Rousseau",
        "Blanc", "Guerin", "Kone", "Muller", "Henry", "Perrin", "Morel", "Girard", "Andre", "Chen", "Mendes", "Leroy"]

METIERS = {
    "modeste": [("caissier", "caissière", "decales"), ("aide-soignant", "aide-soignante", "nuit"),
                ("agent d'entretien", "agente d'entretien", "decales"), ("livreur", "livreuse", "decales"),
                ("ouvrier en usine", "ouvrière en usine", "nuit"), ("serveur", "serveuse", "decales"),
                ("aide à domicile", "aide à domicile", "jour"), ("magasinier", "magasinière", "jour"),
                ("vendeur", "vendeuse", "decales")],
    "moyen": [("infirmier", "infirmière", "nuit"), ("enseignant", "enseignante", "jour"),
              ("technicien", "technicienne", "jour"), ("comptable", "comptable", "jour"),
              ("secrétaire médical", "secrétaire médicale", "jour"), ("conducteur de bus", "conductrice de bus", "decales"),
              ("électricien", "électricienne", "jour"), ("boulanger", "boulangère", "decales"),
              ("employé de banque", "employée de banque", "jour")],
    "aise": [("ingénieur", "ingénieure", "jour"), ("médecin", "médecin", "decales"), ("avocat", "avocate", "jour"),
             ("cadre commercial", "cadre commerciale", "jour"), ("architecte", "architecte", "jour"),
             ("pharmacien", "pharmacienne", "jour"), ("chef de projet", "cheffe de projet", "jour"),
             ("dentiste", "dentiste", "jour")],
}

CATEGORIES_PREF = ["fruits_legumes", "frais", "viande", "epicerie", "sucre", "boissons", "surgeles", "bio_local"]

ATTRIBUTE_FAMILIES = {
    "Identité": ["prenom", "nom", "genre", "age", "type_foyer", "situation", "nb_adultes", "enfants_ages", "nb_enfants",
                 "nb_bebes", "taille_foyer", "etudes", "culture_culinaire", "regime", "allergies", "animal"],
    "Travail et emploi du temps": ["metier", "statut", "niveau_metier", "horaires", "jours_repos", "teletravail_jours",
                                   "temps_trajet_travail_min", "temps_libre_h_semaine", "pression_temporelle",
                                   "flexibilite_horaire", "stabilite_emploi", "quartier_travail"],
    "Budget": ["revenu_mensuel", "revenu_par_uc", "niveau_revenu", "jour_paie", "epargne", "credits_mensuels", "loyer",
               "aides_sociales", "budget_courses_mensuel", "part_budget_alimentaire", "reste_a_vivre",
               "depense_hebdo_estimee", "capacite_achat_gros", "stress_financier_base"],
    "Logement et mobilité": ["quartier", "x", "y", "type_logement", "taille_frigo", "congelateur", "capacite_stockage",
                             "voiture", "velo", "abonnement_bus", "tolerance_trajet_min", "mobilite_reduite"],
    "Personnalité": ["ouverture", "conscienciosite", "extraversion", "agreabilite", "nevrosisme", "impulsivite",
                     "aversion_perte", "besoin_variete", "maximiseur", "rancune", "confiance_generale",
                     "influencabilite", "optimisme", "patience", "humour"],
    "Profil d'acheteur": ["sensibilite_prix", "gout_promos", "fidelite_enseigne", "fidelite_marques", "attrait_mdd",
                          "exigence_qualite", "sensibilite_bio", "sensibilite_local", "sensibilite_sante",
                          "attrait_nouveaute", "fait_liste", "achats_impulsion", "tolerance_attente",
                          "tolerance_rupture", "sens_equite_prix", "receptivite_pub", "scepticisme_marketing",
                          "receptivite_coupons", "canal_prefere", "souci_vie_privee", "stop_pub", "lit_prospectus",
                          "achete_alcool"],
    "Habitudes et préférences": ["frequence_courses_semaine", "jours_courses", "creneau_prefere", "panier_type_euros",
                                 "aime_flaner", "courses_avec_enfants", "cuisine_maison", "gourmandise",
                                 "produits_favoris", "produits_detestes", "gouts", "vacances_semaines"] +
                                [f"pref_{c}" for c in CATEGORIES_PREF],
    "Social": ["bavardage", "leader_opinion", "lit_avis", "ecrit_avis", "reseaux_sociaux",
               "confiance_bouche_a_oreille", "associations", "manie"],
}


def _n(rnd, mu=0.5, sd=0.15) -> float:
    return clamp(rnd.gauss(mu, sd))


def _pos(quartier: str, rnd) -> tuple[int, int]:
    cx, cy = QUARTIERS[quartier]["centre"]
    r = QUARTIERS[quartier]["rayon"]
    ang = rnd.random() * 2 * math.pi
    d = r * math.sqrt(rnd.random())
    return int(round(clamp(cx + d * math.cos(ang), 0, 19))), int(round(clamp(cy + d * math.sin(ang), 0, 19)))


def allocate(n: int) -> dict[str, int]:
    counts = {q: int(n * p) for q, p in PARTS_QUARTIERS.items()}
    order = sorted(PARTS_QUARTIERS, key=lambda q: -(n * PARTS_QUARTIERS[q] - counts[q]))
    i = 0
    while sum(counts.values()) < n:
        counts[order[i % len(order)]] += 1
        i += 1
    return counts


def household_composition(t: str, rnd) -> tuple[int, int, list[int]]:
    """Renvoie (âge de la personne qui fait les courses, nb d'adultes, âges des enfants)."""
    if t == "etudiant":
        return rnd.randint(19, 25), 1, []
    if t == "colocation":
        return rnd.randint(22, 32), 3, []
    if t == "celibataire":
        return rnd.randint(23, 58), 1, []
    if t == "couple":
        return rnd.randint(25, 62), 2, []
    if t == "retraites":
        return rnd.randint(63, 86), rnd.choice([1, 2, 2]), []
    age = rnd.randint(27, 50)
    if t == "famille":
        k = rnd.choice([1, 2, 2])
    elif t == "famille_nombreuse":
        k = rnd.choice([3, 3, 4])
    else:  # monoparentale
        k = rnd.choice([1, 2, 2, 3])
    young = age < 36
    kids = []
    for _ in range(k):
        hi = max(0, min(17, age - 19))
        a = rnd.randint(0, min(hi, 8)) if young else rnd.randint(0, hi)
        kids.append(a)
    return age, (1 if t == "monoparentale" else 2), sorted(kids)


def adult_equivalent(attrs: dict, need_id: str) -> float:
    need = NEEDS[need_id]
    kids = [a for a in attrs["enfants_ages"] if a >= 3]
    babies = attrs["nb_bebes"]
    adults = attrs["nb_adultes"] * (0.85 if attrs["type_foyer"] == "colocation" else 1.0)
    return adults + len(kids) * need.facteur_enfant + babies * 0.3


def daily_needs(attrs: dict) -> dict[str, float]:
    """Portions consommées par jour, par besoin (hors saison)."""
    out = {}
    cm = attrs["cuisine_maison"]
    for nid, need in NEEDS.items():
        if nid == "bebe":
            v = attrs["nb_bebes"] * 7.0
        elif nid == "animaux":
            v = {"chat": 1.0, "chien": 1.5}.get(attrs["animal"], 0.0)
        else:
            v = need.conso * adult_equivalent(attrs, nid)
            if nid == "plats":
                v *= max(0.1, 1.6 - 1.4 * cm)
            if nid in ("proteines", "feculents", "legumes"):
                v *= 0.75 + 0.35 * cm
            if nid in ("fruits", "legumes"):
                v *= 0.7 + 0.6 * attrs["sensibilite_sante"]
            if nid == "snacks":
                v *= 0.5 + attrs["gourmandise"]
            if nid == "petit_dej" and attrs["age"] > 65:
                v *= 0.8
        if nid == "lait" and "lactose" in attrs["allergies"]:
            v = 0.0
        out[nid] = round(v, 3)
    return out


def cost_per_portion(need_id: str, attrs: dict) -> float:
    prods = [p for p in products_for_need(need_id) if is_allowed(p, attrs)]
    if not prods:
        return 0.0
    return statistics.median(p.prix_conseille / p.portions for p in prods)


def weekly_cost_estimate(attrs: dict) -> float:
    dn = daily_needs(attrs)
    return sum(dn[n] * 7 * cost_per_portion(n, attrs) for n in dn)


def _traits(attrs: dict, rnd) -> None:
    il = attrs["niveau_revenu"]
    o, c, e, a, n = (attrs[k] for k in ("ouverture", "conscienciosite", "extraversion", "agreabilite", "nevrosisme"))
    old = attrs["age"] > 60
    young = attrs["age"] < 35
    g = lambda mu, sd=0.12: clamp(rnd.gauss(mu, sd))
    attrs["sensibilite_prix"] = g(0.8 - 0.55 * il + 0.1 * (attrs["nb_enfants"] > 2))
    attrs["gout_promos"] = g(0.25 + 0.45 * attrs["sensibilite_prix"] + 0.15 * c)
    attrs["fidelite_enseigne"] = g(0.45 + 0.2 * (1 - o) + 0.15 * old)
    attrs["exigence_qualite"] = g(0.25 + 0.45 * il + 0.15 * o)
    attrs["fidelite_marques"] = g(0.45 + 0.2 * (1 - o) - 0.2 * attrs["sensibilite_prix"])
    attrs["attrait_mdd"] = g(0.25 + 0.55 * attrs["sensibilite_prix"] - 0.2 * attrs["exigence_qualite"])
    attrs["sensibilite_bio"] = g(0.05 + 0.35 * o + 0.35 * il * (not old), 0.15)
    attrs["sensibilite_local"] = g(0.2 + 0.3 * old + 0.2 * o)
    attrs["sensibilite_sante"] = g(0.3 + 0.25 * il + 0.15 * c + 0.1 * old)
    attrs["attrait_nouveaute"] = g(0.15 + 0.5 * o + 0.1 * young)
    attrs["fait_liste"] = g(0.25 + 0.6 * c)
    attrs["achats_impulsion"] = g(0.15 + 0.5 * attrs["impulsivite"] + 0.2 * (1 - c) - 0.2 * attrs["sensibilite_prix"])
    attrs["tolerance_attente"] = g(0.2 + 0.6 * attrs["patience"])
    attrs["tolerance_rupture"] = g(0.25 + 0.5 * a)
    attrs["sens_equite_prix"] = g(0.35 + 0.3 * n + 0.2 * attrs["sensibilite_prix"])
    attrs["scepticisme_marketing"] = g(0.2 + 0.3 * (attrs["etudes"] in ("bac+3", "bac+5")) + 0.25 * (1 - a))
    attrs["receptivite_pub"] = g(0.7 - 0.6 * attrs["scepticisme_marketing"] + 0.15 * e)
    attrs["receptivite_coupons"] = g(0.15 + 0.6 * attrs["gout_promos"] + 0.15 * c)
    attrs["souci_vie_privee"] = g(0.25 + 0.3 * (attrs["etudes"] in ("bac+3", "bac+5")) + 0.2 * n)
    attrs["stop_pub"] = rnd.random() < 0.08 + 0.35 * attrs["scepticisme_marketing"]
    attrs["lit_prospectus"] = g(0.15 + 0.55 * attrs["gout_promos"] - 0.25 * attrs["scepticisme_marketing"] + 0.2 * old)
    if old:
        attrs["canal_prefere"] = rnd.choice(["papier", "papier", "email", "sms"])
    elif young:
        attrs["canal_prefere"] = rnd.choice(["sms", "email", "email", "papier"])
    else:
        attrs["canal_prefere"] = rnd.choice(["email", "email", "sms", "papier"])


def _schedule(attrs: dict, rnd) -> None:
    st = attrs["statut"]
    h = attrs["horaires"]
    kids = attrs["nb_enfants"]
    works = st in ("cdi", "cdd", "interim", "independant")
    base = 0.15 if st == "retraite" else 0.35 if st in ("etudiant", "chomage", "parent_au_foyer") else 0.55
    base += 0.15 * min(kids, 3) / 3 + (0.1 if h in ("nuit", "decales") else 0)
    attrs["pression_temporelle"] = clamp(rnd.gauss(base, 0.1))
    attrs["temps_libre_h_semaine"] = round(max(4, 60 * (1 - attrs["pression_temporelle"]) + rnd.gauss(0, 4)))
    attrs["flexibilite_horaire"] = clamp(rnd.gauss(0.8 if not works else 0.35 if h == "jour" else 0.5, 0.12))
    attrs["teletravail_jours"] = rnd.choice([0, 1, 2]) if (works and attrs["niveau_metier"] != "modeste" and h == "jour") else 0
    attrs["temps_trajet_travail_min"] = rnd.choice([10, 15, 20, 30, 40]) if works else 0
    attrs["jours_repos"] = [5, 6] if h == "jour" or not works else rnd.sample(range(7), 2)
    if not works:
        attrs["creneau_prefere"] = rnd.choice(["matin", "matin", "midi"])
    elif h == "nuit":
        attrs["creneau_prefere"] = "matin"
    elif h == "decales":
        attrs["creneau_prefere"] = rnd.choice(["matin", "midi", "soir"])
    else:
        attrs["creneau_prefere"] = "soir"
    freq = 3 if st == "retraite" else 2 if (kids or attrs["nb_adultes"] >= 2) else rnd.choice([1, 2])
    if attrs["type_foyer"] == "famille_nombreuse":
        freq = 3
    attrs["frequence_courses_semaine"] = freq
    rest = attrs["jours_repos"]
    days = set()
    if works:
        days.add(rnd.choice(rest))
    while len(days) < freq:
        days.add(rnd.randrange(6))  # pas le dimanche en priorité
    attrs["jours_courses"] = sorted(days)


def generate_attrs(cid: str, quartier: str, t: str, rnd) -> dict:
    a: dict = {"id": cid, "quartier": quartier, "type_foyer": t}
    a["genre"] = "F" if rnd.random() < 0.56 else "M"
    a["prenom"] = rnd.choice(PRENOMS_F if a["genre"] == "F" else PRENOMS_M)
    a["nom"] = rnd.choice(NOMS)
    age, adults, kids = household_composition(t, rnd)
    a["age"], a["nb_adultes"], a["enfants_ages"] = age, adults, kids
    a["nb_enfants"] = len(kids)
    a["nb_bebes"] = sum(1 for k in kids if k < 3)
    a["taille_foyer"] = adults + len(kids)
    a["situation"] = {"celibataire": "célibataire", "etudiant": "étudiant(e), vit seul(e)", "colocation": "en colocation à trois",
                      "couple": "en couple", "famille": "en couple avec enfants", "famille_nombreuse": "en couple, famille nombreuse",
                      "monoparentale": "parent solo", "retraites": "retraité(e)" + (" en couple" if adults == 2 else ", vit seul(e)")}[t]
    lvl = clamp(QUARTIERS[quartier]["revenu"] + rnd.gauss(0, 0.15))
    a["etudes"] = ("bac+5" if lvl > 0.75 else "bac+3" if lvl > 0.55 else "bac" if lvl > 0.3 else rnd.choice(["CAP", "brevet", "bac"]))
    a["culture_culinaire"] = rnd.choice(["française traditionnelle"] * 4 + ["maghrébine", "ouest-africaine", "asiatique", "italienne", "antillaise", "portugaise"])
    r = rnd.random()
    if a["culture_culinaire"] in ("maghrébine", "ouest-africaine") and r < 0.6:
        a["regime"] = "halal"
    elif r < 0.10:
        a["regime"] = "vegetarien"
    elif r < 0.14:
        a["regime"] = "sans_gluten"
    else:
        a["regime"] = "omnivore"
    a["allergies"] = (["lactose"] if rnd.random() < 0.05 else []) + (["arachide"] if rnd.random() < 0.03 else [])
    a["animal"] = rnd.choice(["aucun"] * 6 + ["chat", "chat", "chien"])

    # Travail
    if t == "retraites":
        a["statut"], a["niveau_metier"] = "retraite", ("moyen" if lvl > 0.4 else "modeste")
    elif t == "etudiant":
        a["statut"], a["niveau_metier"] = "etudiant", "modeste"
    else:
        r = rnd.random()
        a["statut"] = ("cdi" if r < 0.6 else "cdd" if r < 0.72 else "interim" if r < 0.77 else "independant" if r < 0.85
                       else "chomage" if r < 0.94 else ("parent_au_foyer" if kids else "chomage"))
        a["niveau_metier"] = "aise" if lvl > 0.7 else "moyen" if lvl > 0.38 else "modeste"
    if a["statut"] in ("retraite", "etudiant"):
        a["metier"] = ("retraité" if a["genre"] == "M" else "retraitée") if a["statut"] == "retraite" else ("étudiant" if a["genre"] == "M" else "étudiante")
        a["horaires"] = "libre"
    else:
        m, f, h = rnd.choice(METIERS[a["niveau_metier"]])
        title = m if a["genre"] == "M" else f
        if a["statut"] == "chomage":
            a["metier"], a["horaires"] = f"en recherche d'emploi (ancien métier : {title})", "libre"
        elif a["statut"] == "parent_au_foyer":
            a["metier"], a["horaires"] = "parent au foyer", "libre"
        else:
            a["metier"], a["horaires"] = title, h
    a["stabilite_emploi"] = {"cdi": 0.9, "independant": 0.6, "cdd": 0.4, "interim": 0.3, "chomage": 0.1,
                             "retraite": 1.0, "etudiant": 0.5, "parent_au_foyer": 0.7}[a["statut"]]
    a["quartier_travail"] = rnd.choice(list(QUARTIERS)) if a["statut"] in ("cdi", "cdd", "interim", "independant") else ""

    # Revenus
    def earner():
        if a["statut"] == "retraite":
            return rnd.uniform(1100, 1600) + 1000 * lvl
        if a["statut"] == "etudiant":
            return rnd.uniform(450, 900)
        if a["statut"] == "chomage":
            return rnd.uniform(950, 1400)
        if a["statut"] == "parent_au_foyer":
            return 0.0
        return {"modeste": rnd.uniform(1350, 1900), "moyen": rnd.uniform(1900, 2900), "aise": rnd.uniform(3200, 6000)}[a["niveau_metier"]]
    income = earner()
    if adults >= 2 and t != "colocation":
        if t == "retraites" or rnd.random() < 0.85:
            income += earner() if a["statut"] != "parent_au_foyer" else {"modeste": 1700, "moyen": 2500, "aise": 4200}[a["niveau_metier"]]
    if t == "colocation":
        income *= 1.0  # la personne gère la caisse commune : budget partagé ci-dessous
    aides = 0.0
    if income < 2200 and kids:
        aides = 140.0 * len(kids)
    a["aides_sociales"] = round(aides)
    a["revenu_mensuel"] = round(income + aides)
    uc = 1 + 0.5 * (adults - 1) + 0.3 * len(kids)
    a["revenu_par_uc"] = round(a["revenu_mensuel"] / uc)
    a["niveau_revenu"] = clamp((a["revenu_par_uc"] - 700) / 2800)
    a["jour_paie"] = rnd.choice([1, 1, 5, 10, 25, 28, 30])
    a["epargne"] = round(max(0, rnd.gauss(a["revenu_mensuel"] * (0.5 + 3 * a["niveau_revenu"]), 400)))
    a["credits_mensuels"] = round(max(0, rnd.gauss(150 + 300 * (t in ("famille", "famille_nombreuse", "couple")), 120)))
    a["loyer"] = round({"studio": 520, "appartement": 760, "maison": 980}.get("maison" if quartier in ("residentiel", "peripherie") else "appartement") * (0.8 + 0.4 * lvl))

    # Logement et mobilité
    a["type_logement"] = ("maison" if quartier in ("residentiel", "peripherie") and rnd.random() < 0.8
                          else "studio" if t in ("etudiant", "celibataire") and rnd.random() < 0.6 else "appartement")
    a["taille_frigo"] = {"studio": "petit", "appartement": rnd.choice(["petit", "moyen", "moyen"]), "maison": rnd.choice(["moyen", "grand", "grand"])}[a["type_logement"]]
    a["congelateur"] = a["type_logement"] == "maison" or rnd.random() < 0.3
    a["capacite_stockage"] = clamp({"petit": 0.25, "moyen": 0.5, "grand": 0.8}[a["taille_frigo"]] + 0.15 * a["congelateur"])
    p_car = {"centre": 0.35, "residentiel": 0.95, "populaire": 0.4, "peripherie": 0.95}[quartier]
    a["voiture"] = rnd.random() < p_car and a["statut"] != "etudiant"
    a["velo"] = rnd.random() < (0.5 if quartier == "centre" else 0.25) and age < 70
    a["abonnement_bus"] = (not a["voiture"]) and rnd.random() < 0.7
    a["mobilite_reduite"] = age > 75 and rnd.random() < 0.4
    a["x"], a["y"] = _pos(quartier, rnd)

    # Personnalité (Big Five + traits)
    for k in ("ouverture", "conscienciosite", "extraversion", "agreabilite", "nevrosisme"):
        a[k] = _n(rnd)
    a["impulsivite"] = clamp(rnd.gauss(0.55 - 0.4 * (a["conscienciosite"] - 0.5), 0.15))
    a["aversion_perte"] = _n(rnd, 0.55)
    a["besoin_variete"] = clamp(rnd.gauss(0.3 + 0.4 * a["ouverture"], 0.12))
    a["maximiseur"] = _n(rnd, 0.45)
    a["rancune"] = clamp(rnd.gauss(0.35 + 0.3 * a["nevrosisme"], 0.15))
    a["confiance_generale"] = clamp(rnd.gauss(0.3 + 0.4 * a["agreabilite"], 0.12))
    a["influencabilite"] = clamp(rnd.gauss(0.6 - 0.3 * a["conscienciosite"] + 0.1 * a["agreabilite"], 0.12))
    a["optimisme"] = clamp(rnd.gauss(0.6 - 0.3 * a["nevrosisme"], 0.12))
    a["patience"] = clamp(rnd.gauss(0.35 + 0.3 * a["agreabilite"] + 0.2 * (age > 60), 0.12))
    a["humour"] = rnd.choice(["pince-sans-rire", "jovial", "sérieux", "râleur", "bienveillant", "direct"])

    _schedule(a, rnd)
    _traits(a, rnd)

    # Habitudes et préférences
    a["aime_flaner"] = clamp(rnd.gauss(0.15 + 0.4 * a["ouverture"] - 0.35 * a["pression_temporelle"] + 0.2 * (age > 60), 0.12))
    young_kids = sum(1 for k in kids if 2 <= k <= 10)
    a["courses_avec_enfants"] = clamp(rnd.gauss(0.5, 0.15)) if young_kids else 0.0
    a["cuisine_maison"] = clamp(rnd.gauss(0.45 + 0.3 * (age > 50) + 0.2 * a["conscienciosite"] - 0.25 * a["pression_temporelle"], 0.12))
    a["gourmandise"] = _n(rnd, 0.5, 0.18)
    a["achete_alcool"] = a["regime"] != "halal" and age >= 20 and rnd.random() < 0.6
    for cat in CATEGORIES_PREF:
        a[f"pref_{cat}"] = _n(rnd, 0.5, 0.2)
    a["pref_bio_local"] = clamp(0.5 * a["pref_bio_local"] + 0.5 * max(a["sensibilite_bio"], a["sensibilite_local"]))
    allowed = [p for p in PRODUCTS.values() if is_allowed(p, a)]
    a["produits_favoris"] = [p.id for p in rnd.sample(allowed, k=min(3, len(allowed)))]
    a["produits_detestes"] = [p.id for p in rnd.sample(allowed, k=2) if p.id not in a["produits_favoris"]]
    a["gouts"] = product_tastes(a, rnd)
    if a["statut"] in ("cdi", "cdd", "interim", "independant", "etudiant") or kids:
        a["vacances_semaines"] = [] if rnd.random() < 0.3 else [rnd.randint(27, 32)] * 1
        if a["vacances_semaines"]:
            a["vacances_semaines"] = [a["vacances_semaines"][0], a["vacances_semaines"][0] + 1]
    else:
        a["vacances_semaines"] = [] if rnd.random() < 0.6 else [rnd.choice([23, 24, 36, 37])]

    # Social
    e = a["extraversion"]
    a["bavardage"] = clamp(rnd.gauss(0.15 + 0.6 * e, 0.12))
    a["leader_opinion"] = clamp(rnd.gauss(0.1 + 0.4 * e + 0.2 * a["ouverture"], 0.12))
    a["lit_avis"] = clamp(rnd.gauss(0.2 + 0.4 * (age < 45) + 0.2 * a["ouverture"], 0.12))
    a["ecrit_avis"] = clamp(rnd.gauss(0.05 + 0.3 * e + 0.2 * a["nevrosisme"], 0.1))
    a["reseaux_sociaux"] = clamp(rnd.gauss(0.85 - 0.012 * (age - 20), 0.12))
    a["confiance_bouche_a_oreille"] = clamp(rnd.gauss(0.35 + 0.3 * a["agreabilite"], 0.12))
    a["associations"] = rnd.random() < 0.15 + 0.3 * (age > 55)
    a["tolerance_trajet_min"] = round(8 + 14 * (1 - a["pression_temporelle"]) + 8 * a["voiture"])

    refresh_economics(a)
    a["manie"] = pick_quirk(a, rnd)
    return a


def refresh_economics(a: dict) -> None:
    """Recalcule revenus par unité de consommation et budget alimentaire (après un événement de vie)."""
    kids = a["enfants_ages"]
    a["nb_enfants"] = len(kids)
    a["nb_bebes"] = sum(1 for k in kids if k < 3)
    a["taille_foyer"] = a["nb_adultes"] + len(kids)
    uc = 1 + 0.5 * (a["nb_adultes"] - 1) + 0.3 * len(kids)
    a["revenu_par_uc"] = round(a["revenu_mensuel"] / uc)
    a["niveau_revenu"] = clamp((a["revenu_par_uc"] - 700) / 2800)
    weekly = weekly_cost_estimate(a)
    a["depense_hebdo_estimee"] = round(weekly, 2)
    share = clamp(0.21 - 0.11 * a["niveau_revenu"], 0.08, 0.25)
    monthly_need = weekly * 4.33
    a["budget_courses_mensuel"] = round(clamp(a["revenu_mensuel"] * share, monthly_need * 0.9, monthly_need * 1.9))
    a["part_budget_alimentaire"] = round(a["budget_courses_mensuel"] / max(1, a["revenu_mensuel"]), 3)
    a["reste_a_vivre"] = round(a["revenu_mensuel"] - a["loyer"] - a["credits_mensuels"] - a["budget_courses_mensuel"])
    a["stress_financier_base"] = clamp(0.6 - a["reste_a_vivre"] / 2500)
    a["capacite_achat_gros"] = clamp(0.5 * a["capacite_stockage"] + 0.5 * a["niveau_revenu"])
    a["panier_type_euros"] = round(weekly / max(1, a["frequence_courses_semaine"]), 2)


CULTURE_TASTES = {
    "française traditionnelle": {"baguette": 0.9, "camembert": 0.5, "vin": 0.3, "beurre": 0.3, "pommes": 0.2, "steak_hache": 0.2},
    "maghrébine": {"baguette": 0.5, "lentilles": 0.4, "poulet_mdd": 0.3, "tomates": 0.3, "huile": 0.2},
    "ouest-africaine": {"riz_mdd": 0.6, "poulet_mdd": 0.4, "bananes": 0.3, "huile": 0.3},
    "asiatique": {"riz_basmati": 0.6, "riz_mdd": 0.5, "tofu_bio": 0.4, "pain_mie_mdd": 0.2},
    "italienne": {"pates_marque": 0.7, "sauce_tomate_marque": 0.5, "tomates": 0.3, "baguette": 0.3},
    "antillaise": {"riz_mdd": 0.5, "bananes": 0.5, "poulet_mdd": 0.3, "jus_orange": 0.3},
    "portugaise": {"thon": 0.4, "poisson_pane": 0.3, "pdt": 0.4, "baguette": 0.4},
}


def product_tastes(a: dict, rnd) -> dict[str, float]:
    """Goût personnel et durable pour chaque produit (-1,5 à +1,5) : habitudes, culture, enfants, âge."""
    tastes = {}
    culture = CULTURE_TASTES.get(a["culture_culinaire"], {})
    kids = a["nb_enfants"] > 0
    for pid, p in PRODUCTS.items():
        t = rnd.gauss(0, 0.35) + culture.get(pid, 0.0)
        if kids and p.enfant >= 0.7:
            t += 0.35
        if a["age"] > 60 and pid in ("camembert", "the", "confiture", "pain_complet_bio", "baguette"):
            t += 0.2
        if a["age"] < 30 and pid in ("pizza", "soda", "pates_mdd", "cereales_marque"):
            t += 0.25
        if p.bio:
            t += 0.6 * (a["sensibilite_bio"] - 0.4)
        if pid in a["produits_favoris"]:
            t += 0.6
        if pid in a["produits_detestes"]:
            t -= 1.2
        tastes[pid] = round(max(-1.5, min(1.5, t)), 2)
    return tastes


def pick_quirk(a: dict, rnd) -> str:
    f = a["genre"] == "F"
    options = []
    if a["fait_liste"] > 0.75:
        options.append("fait ses courses avec une liste précise et s'y tient")
    if a["attrait_nouveaute"] > 0.65:
        options.append("adore découvrir les nouveautés en tête de gondole")
    if a["tolerance_attente"] < 0.3:
        options.append("ne supporte pas de faire la queue en caisse")
    if a["sensibilite_prix"] > 0.7 and a["conscienciosite"] > 0.55:
        options.append("garde ses tickets de caisse pour comparer les prix")
    if a["sensibilite_local"] > 0.65:
        options.append("achète local dès que possible")
    if a["achats_impulsion"] > 0.55:
        options.append("craque souvent pour une douceur en passant en caisse")
    if a["souci_vie_privee"] > 0.65:
        options.append(("méfiante" if f else "méfiant") + " envers les cartes de fidélité")
    if a["nb_enfants"] and a["courses_avec_enfants"] > 0.4:
        options.append("fait souvent les courses avec ses enfants, qui réclament des friandises")
    if a["fidelite_enseigne"] > 0.7 and a["age"] > 55:
        options.append("connaît les caissières par leur prénom")
    if a["reseaux_sociaux"] > 0.7 and a["gout_promos"] > 0.55:
        options.append("suit les bons plans sur les réseaux sociaux")
    if a["sensibilite_bio"] > 0.6 and a["nb_enfants"]:
        options.append("privilégie le bio pour ses enfants")
    if a["exigence_qualite"] > 0.7:
        options.append("lit attentivement les étiquettes et la provenance")
    if not options:
        options.append("aime discuter des bonnes affaires avec ses voisins")
    return rnd.choice(options)


def build_social_graph(people: list[dict], rnd) -> None:
    ids = [p["id"] for p in people]
    links: dict[str, dict[str, str]] = {i: {} for i in ids}

    def add(a, b, rel):
        if a != b and b not in links[a] and len(links[a]) < 6 and len(links[b]) < 6:
            links[a][b] = rel
            links[b][a] = rel

    for p in people:
        same = sorted((q for q in people if q["quartier"] == p["quartier"] and q["id"] != p["id"]),
                      key=lambda q: (q["x"] - p["x"]) ** 2 + (q["y"] - p["y"]) ** 2)
        for q in same[:rnd.choice([1, 2, 2])]:
            add(p["id"], q["id"], "voisin")
        if p["quartier_travail"] and rnd.random() < 0.4:
            coll = [q for q in people if q["quartier_travail"] == p["quartier_travail"] and q["id"] != p["id"]]
            if coll:
                add(p["id"], rnd.choice(coll)["id"], "collègue")
        if rnd.random() < 0.25:
            add(p["id"], rnd.choice(ids), "famille")
        friends = [q for q in people if abs(q["age"] - p["age"]) <= 10 and q["id"] != p["id"]]
        for _ in range(rnd.choice([0, 1, 1, 2])):
            if friends:
                add(p["id"], rnd.choice(friends)["id"], "ami")
    for p in people:
        p["proches"] = [{"id": k, "relation": v} for k, v in links[p["id"]].items()]


def generate_population(n: int, rnd) -> list[dict]:
    counts = allocate(n)
    people = []
    i = 1
    for q, k in counts.items():
        types = TYPES_PAR_QUARTIER[q]
        for j in range(k):
            people.append(generate_attrs(f"C{i:02d}", q, types[j % len(types)], rnd))
            i += 1
    build_social_graph(people, rnd)
    return people


def new_household(cid: str, rnd, people: list[dict]) -> dict:
    q = rnd.choice(list(QUARTIERS))
    t = rnd.choice(TYPES_PAR_QUARTIER[q])
    a = generate_attrs(cid, q, t, rnd)
    neigh = sorted((p for p in people if p["quartier"] == q), key=lambda p: (p["x"] - a["x"]) ** 2 + (p["y"] - a["y"]) ** 2)
    a["proches"] = [{"id": p["id"], "relation": "voisin"} for p in neigh[:2]]
    for p in neigh[:2]:
        p.setdefault("proches", []).append({"id": cid, "relation": "voisin"})
    return a


VARIANT_TRAITS = ("sensibilite_prix", "gout_promos", "fidelite_enseigne", "exigence_qualite", "fidelite_marques", "attrait_mdd",
                  "sensibilite_bio", "sensibilite_local", "attrait_nouveaute", "achats_impulsion", "tolerance_attente",
                  "tolerance_rupture", "receptivite_pub", "receptivite_coupons", "souci_vie_privee", "lit_prospectus",
                  "pression_temporelle", "aime_flaner", "impulsivite", "patience", "influencabilite", "pref_bio_local")


def make_variant(src: dict, vid: str, rnd, spread: float = 0.08) -> dict:
    """Foyer « variante » d'un foyer piloté par le modèle : même type de foyer, même quartier, même culture, mais une
    autre adresse, d'autres revenus et des traits voisins. Il reprend les décisions de son foyer-source (voir
    simarche/variantes.py) sans appel au modèle, et s'en écarte quand sa propre situation l'impose."""
    a = copy.deepcopy(src)
    a["id"], a["source"] = vid, src["id"]
    a["prenom"] = rnd.choice(PRENOMS_F if a["genre"] == "F" else PRENOMS_M)
    a["nom"] = rnd.choice(NOMS)
    a["x"], a["y"] = _pos(a["quartier"], rnd)
    a["age"] = max(18, a["age"] + rnd.randint(-6, 6))
    a["revenu_mensuel"] = round(a["revenu_mensuel"] * rnd.uniform(0.85, 1.18))
    a["epargne"] = round(a["epargne"] * rnd.uniform(0.7, 1.3))
    a["jour_paie"] = rnd.choice([1, 1, 5, 10, 25, 28, 30])
    for k in VARIANT_TRAITS:
        if isinstance(a.get(k), (int, float)) and not isinstance(a.get(k), bool):
            a[k] = clamp(a[k] + rnd.gauss(0, spread))
    a["gouts"] = {p: round(v + rnd.gauss(0, 0.15), 3) for p, v in (a.get("gouts") or {}).items()}
    days = set()
    while len(days) < a["frequence_courses_semaine"]:
        days.add(rnd.randrange(6))
    a["jours_courses"] = sorted(days)
    a["proches"] = []  # le bouche-à-oreille passe par le foyer-source (voir simulation.end_of_week)
    refresh_economics(a)
    return a


def attribute_count(a: dict) -> int:
    return sum(len(v) for v in ATTRIBUTE_FAMILIES.values())


# ---------------------------------------------------------------- textes de persona

_TRAITS = [
    ("sensibilite_prix", ("très attentif aux prix", "très attentive aux prix"), ("peu regardant sur les prix", "peu regardante sur les prix")),
    ("gout_promos", ("chasseur de promotions", "chasseuse de promotions"), ("indifférent aux promotions", "indifférente aux promotions")),
    ("fidelite_enseigne", ("fidèle à ses magasins habituels",) * 2, ("change facilement de magasin",) * 2),
    ("fidelite_marques", ("attaché à ses marques", "attachée à ses marques"), None),
    ("attrait_mdd", ("achète volontiers les premiers prix",) * 2, ("évite les premiers prix",) * 2),
    ("exigence_qualite", ("exigeant sur la qualité", "exigeante sur la qualité"), None),
    ("sensibilite_bio", ("privilégie le bio",) * 2, None),
    ("sensibilite_local", ("aime acheter local",) * 2, None),
    ("achats_impulsion", ("craque facilement pour des achats imprévus",) * 2, ("n'achète que ce qui est prévu",) * 2),
    ("scepticisme_marketing", ("méfiant envers la publicité", "méfiante envers la publicité"), ("sensible à la publicité",) * 2),
    ("tolerance_attente", None, ("déteste attendre",) * 2),
    ("pression_temporelle", ("toujours pressé", "toujours pressée"), ("prend son temps",) * 2),
    ("souci_vie_privee", ("protège ses données personnelles",) * 2, None),
    ("attrait_nouveaute", ("curieux des nouveautés", "curieuse des nouveautés"), ("routinier", "routinière")),
    ("rancune", ("rancunier après une mauvaise expérience", "rancunière après une mauvaise expérience"), ("pardonne vite",) * 2),
    ("bavardage", ("bavard, parle de tout à ses proches", "bavarde, parle de tout à ses proches"), ("discret", "discrète")),
]


def income_phrase(a: dict) -> str:
    r = a["revenu_par_uc"]
    if r < 1100:
        return "revenus très modestes"
    if r < 1600:
        return "revenus modestes"
    if r < 2300:
        return "revenus moyens"
    if r < 3200:
        return "à l'aise financièrement"
    return "revenus élevés"


def salient_traits(a: dict, k: int = 4) -> list[str]:
    f = 1 if a["genre"] == "F" else 0
    scored = []
    for key, hi, lo in _TRAITS:
        v = a[key]
        if v >= 0.68 and hi:
            scored.append((v - 0.5, hi[f]))
        elif v <= 0.32 and lo:
            scored.append((0.5 - v, lo[f]))
    scored.sort(reverse=True)
    return [t for _, t in scored[:k]]


def household_phrase(a: dict) -> str:
    parts = [a["situation"]]
    kids = a["enfants_ages"]
    if kids:
        labels = ["bébé" if k == 0 else f"{k} an" + ("s" if k > 1 else "") for k in kids]
        ages = ", ".join(labels[:-1]) + (" et " if len(labels) > 1 else "") + labels[-1]
        parts.append(f"{len(kids)} enfant{'s' if len(kids) > 1 else ''} ({ages})")
    if a["animal"] != "aucun":
        parts.append(f"un {a['animal']}")
    return ", ".join(parts)


def mobility_phrase(a: dict) -> str:
    if a["voiture"]:
        return "a une voiture"
    bits = ["sans voiture"]
    if a["abonnement_bus"]:
        bits.append("prend le bus")
    if a["velo"]:
        bits.append("à vélo")
    if a["mobilite_reduite"]:
        bits.append("se déplace difficilement")
    return ", ".join(bits)


def persona_short(a: dict) -> str:
    regime = {"omnivore": "", "halal": " Mange halal.", "vegetarien": " Végétarien(ne).", "sans_gluten": " Sans gluten."}[a["regime"]]
    traits = salient_traits(a)
    likes = sorted(a.get("gouts", {}).items(), key=lambda kv: -kv[1])
    fav = [PRODUCTS[p].nom.split(" (")[0] for p, v in likes[:3] if v > 0.5 and is_allowed(PRODUCTS[p], a)]
    hate = [PRODUCTS[p].nom.split(" (")[0] for p, v in likes[::-1][:2] if v < -0.6]
    taste = (f" Aime : {', '.join(fav)}." if fav else "") + (f" N'aime pas : {', '.join(hate)}." if hate else "")
    return (f"{a['prenom']} {a['nom']}, {a['age']} ans, {a['metier']}, {household_phrase(a)}. "
            f"Habite le {QUARTIERS[a['quartier']]['nom']}, {mobility_phrase(a)}. {income_phrase(a).capitalize()}.{regime} "
            f"{'Caractère : ' + ', '.join(traits) + '. ' if traits else ''}Manie : {a['manie']}.{taste}")


def persona_long(a: dict) -> str:
    horaires = {"jour": "travaille en journée", "nuit": "travaille de nuit", "decales": "a des horaires décalés", "libre": "a des journées libres"}[a["horaires"]]
    return (persona_short(a) + f" {horaires.capitalize()} ; préfère faire les courses le {a['creneau_prefere']}. "
            f"Revenu du foyer : environ {a['revenu_mensuel']} € par mois, budget courses d'environ {a['budget_courses_mensuel']} € par mois. "
            f"Cuisine maison : {'beaucoup' if a['cuisine_maison'] > 0.6 else 'un peu' if a['cuisine_maison'] > 0.35 else 'rarement'}. "
            f"Tempérament {a['humour']}. Culture culinaire {a['culture_culinaire']}.")
