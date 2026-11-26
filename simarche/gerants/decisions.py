"""Format des décisions hebdomadaires des gérants et application par le moteur (avec les règles du jeu)."""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from .. import personnel, temps
from ..catalogue import EMPLACEMENTS_SECONDAIRES, NIVEAUX, PRODUCTS, RAYONS
from ..communication import distribute_flyer, moderate, run_ad, send_message
from ..enquetes import BON_OFFRES, validate_reward
from ..lois import check_promo_rate, clean_text, min_price
from ..magasin import Promotion
from ..population import QUARTIERS
from . import projets, sous_agents
from .automatisations import MAX_ACTIVE, validate

S, N, I, B = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}


def _arr(x):
    return {"type": "array", "items": x}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _enum(*v):
    return {"type": "string", "enum": list(v)}


OFFRES = ("remise_pct_produit", "remise_pct_panier", "remise_euros_panier", "produit_offert", "points_x2", "parrainage")

# Domaines de l'équipe de direction : chacun ne décide que sur ses leviers (les deux peuvent investir et programmer).
SHARED_KEYS = ("analyse", "objectifs", "hypotheses", "sources_utilisees", "chantier", "projets", "missions", "automatisations",
               "message_equipe", "journal")
ROLE_KEYS = {
    "gerant": ("prix", "prix_individuels", "personnel", "caisses", "rayons", "mises_en_avant", "stocks_cibles", "commandes", "assortiment", "horaires",
               "dates_courtes", "etiquettes_electroniques", "releve_prix", "message_concurrent"),
    "marketing": ("promotions", "coupons", "prospectus", "messages", "publicites", "fidelite", "enquete", "reponses_avis",
                  "slogan"),
}
ROLE_TITLES = {"gerant": "le gérant", "marketing": "la responsable marketing et clients"}


def role_keys(role: str | None, team: list[str] | None, strict: bool = True) -> set[str] | None:
    """Clés de décision permises à un rôle (None : tous les leviers — agent seul, programme ancien, ou équipe dont les
    domaines ne sont pas imposés : chacun peut tout décider et l'organisation vient des agents eux-mêmes)."""
    if not strict or not role or not team or len(team) <= 1 or role not in ROLE_KEYS:
        return None
    return set(ROLE_KEYS[role]) | set(SHARED_KEYS)


def store_keys(store, role: str | None) -> set[str] | None:
    return role_keys(role, getattr(store, "team", None), bool(getattr(store, "domaines_stricts", False)))


def owner_of(key: str, team: list[str]) -> str | None:
    for r in team:
        if key in ROLE_KEYS.get(r, ()):
            return r
    return None


def role_schema(slots: list[str], keys: set[str] | None) -> dict:
    full = decisions_schema(slots)
    if keys is None:
        return full
    props = {k: v for k, v in full["properties"].items() if k in keys}
    return {**full, "properties": props, "required": list(props)}


def _creneaux_schema(slots):
    return _arr(_obj({"jours": _enum("semaine", "samedi", "dimanche"), "creneau": _enum(*slots)}))


def decisions_schema(slots: list[str]) -> dict:
    return _obj({
        "analyse": S,
        "objectifs": S,
        "hypotheses": _arr(S),
        "sources_utilisees": _arr(S),
        "chantier": _obj({"realise": S, "suivant": S}),
        "prix": _arr(_obj({"produit": S, "prix": N})),
        "prix_individuels": _arr(_obj({"membre": S, "produit": S, "prix": N})),
        "promotions": _arr(_obj({"produits": _arr(S), "mecanique": _enum("pourcentage", "x_pour_y", "deuxieme_moitie"),
                                 "valeur": N, "y": I, "duree_jours": I, "affiche": B})),
        "coupons": _arr(_obj({"offre": _enum(*OFFRES), "valeur": N, "produit": S, "panier_min": N, "jours_validite": I,
                              "distribution": _enum("membres_liste", "tous_les_membres", "prospectus", "caisse"),
                              "canal": _enum("email", "sms", "carte"), "membres": _arr(S), "texte": S,
                              "declencheur_produit": S, "quantite_min": I, "budget": N, "temoin_pct": I})),
        "prospectus": _obj({"envoyer": B, "quartiers": _arr(_enum(*QUARTIERS)), "exemplaires": I, "texte": S,
                            "prix_annonces": _arr(_obj({"produit": S, "prix": N}))}),
        "messages": _arr(_obj({"canal": _enum("email", "sms"), "membres": _arr(S), "texte": S, "rappel_coupon": S})),
        "publicites": _arr(_obj({"media": _enum("radio", "affichage", "reseaux"), "budget": N, "texte": S,
                                 "quartiers": _arr(S), "age_min": I, "age_max": I})),
        "fidelite": _obj({"modifier": B, "actif": B, "points_par_euro": N, "valeur_point": N, "seuil_utilisation": I,
                          "bonus_inscription": I, "donnees_demandees": _arr(_enum(*CARD_FIELDS)), "questions_inscription": _arr(S),
                          "prix_membres": _arr(_obj({"produit": S, "prix": N}))}),
        "rayons": _arr(_obj({"produit": S, "rayon": I, "segment": I, "niveau": _enum(*NIVEAUX), "facings": I})),
        "mises_en_avant": _obj({"modifier": B, "entree": _arr(S), "tete_gondole_1": _arr(S), "tete_gondole_2": _arr(S), "caisse": _arr(S)}),
        "stocks_cibles": _arr(_obj({"produit": S, "stock_cible": I})),
        "commandes": _arr(_obj({"produit": S, "quantite": I, "fournisseur": _enum("grossiste", "local")})),
        "horaires": _arr(_obj({"jours": _enum("semaine", "samedi", "dimanche"), "creneau": _enum(*slots), "ouvert": B})),
        "assortiment": _obj({"ajouter": _arr(S), "retirer": _arr(S)}),
        "dates_courtes": _obj({"modifier": B, "actif": B, "jours_avant_peremption": I, "remise_pct": N}),
        "caisses": _arr(_obj({"jours": _enum("semaine", "samedi", "dimanche"), "creneau": _enum(*slots), "nombre": I})),
        "personnel": _obj({"recruter": _arr(_obj({"poste": _enum("caisse", "rayon"), "contrat": _enum("CDI", "CDD"),
                                                  "duree_semaines": I, "creneaux": _creneaux_schema(slots)})),
                           "modifier": _arr(_obj({"employe": S, "creneaux": _creneaux_schema(slots)})),
                           "licencier": _arr(S)}),
        "etiquettes_electroniques": B,
        "releve_prix": _arr(S),
        "enquete": _obj({"lancer": B, "questions": _arr(S), "cible": _enum("membres_email", "clients_en_caisse"),
                        "recompense_points": I, "bon": _obj({"offre": _enum(*BON_OFFRES), "valeur": N, "produit": S,
                                                           "panier_min": N, "jours_validite": I})}),
        "reponses_avis": _arr(_obj({"avis_id": S, "texte": S})),
        "slogan": _obj({"modifier": B, "texte": S}),
        "automatisations": _arr(_obj({"fichier": S, "actif": B})),
        "projets": _arr(_obj({"fichier": S, "budget_max": N, "actif": B})),
        "missions": _arr(_obj({"fichier": S, "budget_max": N})),
        "message_concurrent": S,
        "message_equipe": S,
        "journal": S,
    })


def apply_price(world, store, pid, amount, slot=None):
    problems = []
    if not isinstance(pid, str) or pid not in PRODUCTS:
        return False, [f"prix : produit inconnu « {pid} » (identifiants dans donnees/produits.csv) — ligne ignorée, les autres "
                       "lignes de prix sont appliquées"]
    if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
        return False, [f"prix : montant invalide pour {pid} ({amount!r}) — ligne ignorée, les autres lignes de prix sont appliquées"]
    if slot and (not store.esl or slot not in temps.slots(world.cfg)):
        return False, [f"prix par créneau refusé pour {pid} : étiquettes électroniques et créneau valide nécessaires"]
    floor = min_price(pid, world.supplier, world.cfg["lois"])
    cap = round(PRODUCTS[pid].prix_conseille * 3, 2)
    if amount < floor:
        problems.append(f"{pid} : prix sous le seuil de revente à perte ({floor:.2f} €), corrigé")
    if amount > cap:
        problems.append(f"{pid} : prix plafonné à 3 fois le prix conseillé")
    price = round(max(floor, min(amount, cap)), 2)
    if slot:
        store.slot_prices.setdefault((world.day, slot), {})[pid] = price
    else:
        store.set_price(pid, price, world.day)
    return True, problems


def check_decision(d: dict, workspace: Path | None = None) -> tuple[dict, list[str]]:
    """Vérification à blanc d'un brouillon de décision (commande `verifier` du guichet) : rien n'est appliqué.

    Renvoie un résumé (nombre de lignes par champ) et la liste des lignes qui seraient refusées : produits inconnus,
    montants invalides, fichiers absents, champs inconnus. Les règles économiques (seuil de revente à perte, plafonds)
    restent appliquées au moment réel de la décision."""
    issues: list[str] = []
    if not isinstance(d, dict):
        return {}, ["la décision doit être un objet JSON"]
    base = empty_decisions()
    unknown = [k for k in d if k not in base]
    if unknown:
        issues.append("champs inconnus, ignorés : " + ", ".join(map(str, unknown[:10])))
    d = normalize({k: v for k, v in d.items() if k in base})

    def bad_pid(pid):
        return not isinstance(pid, str) or pid not in PRODUCTS

    for line in d["prix"]:
        pid, amount = (line or {}).get("produit"), (line or {}).get("prix")
        if bad_pid(pid):
            issues.append(f"prix : produit inconnu « {pid} »")
        elif not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
            issues.append(f"prix : montant invalide pour {pid} ({amount!r})")
    for line in d["prix_individuels"]:
        if bad_pid((line or {}).get("produit")):
            issues.append(f"prix_individuels : produit inconnu « {(line or {}).get('produit')} »")
        if not str((line or {}).get("membre") or "").strip():
            issues.append("prix_individuels : membre manquant")
    for field, key in (("stocks_cibles", "produit"), ("commandes", "produit"), ("rayons", "produit")):
        for line in d[field]:
            if bad_pid((line or {}).get(key)):
                issues.append(f"{field} : produit inconnu « {(line or {}).get(key)} »")
    for p in d["promotions"]:
        for pid in (p or {}).get("produits") or []:
            if bad_pid(pid):
                issues.append(f"promotions : produit inconnu « {pid} »")
    for c in d["coupons"]:
        pid = (c or {}).get("produit")
        if pid and bad_pid(pid):
            issues.append(f"coupons : produit inconnu « {pid} »")
    for side in ("ajouter", "retirer"):
        for pid in d["assortiment"].get(side) or []:
            if bad_pid(pid):
                issues.append(f"assortiment ({side}) : produit inconnu « {pid} »")
    for m in d["fidelite"].get("prix_membres") or []:
        if bad_pid((m or {}).get("produit")):
            issues.append(f"prix_membres : produit inconnu « {(m or {}).get('produit')} »")
    if workspace is not None:
        for field in ("automatisations", "projets", "missions"):
            for line in d[field]:
                f = str((line or {}).get("fichier") or "")
                if f and not (Path(workspace) / f).exists():
                    issues.append(f"{field} : fichier introuvable « {f} »")
    summary = {k: len(v) for k, v in d.items() if isinstance(v, list) and v}
    summary.update({k: True for k, v in d.items() if isinstance(v, dict) and (v.get("modifier") or v.get("lancer") or v.get("envoyer"))})
    return summary, issues


def empty_decisions() -> dict:
    return {"analyse": "", "objectifs": "", "hypotheses": [], "sources_utilisees": [], "chantier": {"realise": "", "suivant": ""}, "missions": [], "prix": [], "prix_individuels": [], "promotions": [], "coupons": [],
            "prospectus": {"envoyer": False, "quartiers": [], "exemplaires": 0, "texte": "", "prix_annonces": []},
            "messages": [], "publicites": [],
            "fidelite": {"modifier": False, "actif": False, "points_par_euro": 1, "valeur_point": 0.01, "seuil_utilisation": 500,
                         "bonus_inscription": 0, "donnees_demandees": [], "questions_inscription": [], "prix_membres": []},
            "rayons": [], "mises_en_avant": {"modifier": False, "entree": [], "tete_gondole_1": [], "tete_gondole_2": [], "caisse": []},
            "stocks_cibles": [], "commandes": [], "horaires": [], "assortiment": {"ajouter": [], "retirer": []},
            "dates_courtes": {"modifier": False, "actif": False, "jours_avant_peremption": 1, "remise_pct": 30},
            "caisses": [], "etiquettes_electroniques": False, "releve_prix": [],
            "enquete": {"lancer": False, "questions": [], "cible": "membres_email", "recompense_points": 0},
            "reponses_avis": [], "slogan": {"modifier": False, "texte": ""}, "automatisations": [],
            "personnel": {"recruter": [], "modifier": [], "licencier": []}, "projets": [],
            "message_concurrent": "", "message_equipe": "", "journal": ""}


def normalize(d: dict) -> dict:
    """Complète une décision incomplète avec les valeurs neutres."""
    base = empty_decisions()
    for k, v in (d or {}).items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            base[k] = {**base[k], **v}
        elif k in base:
            base[k] = v
    return base


# Valeurs neutres des éléments de liste qu'un programme peut omettre (ctx.decider accepte des décisions partielles).
ITEM_DEFAULTS = {
    "promotions": {"mecanique": "pourcentage", "y": 0, "duree_jours": 7, "affiche": True},
    "coupons": {"produit": "", "panier_min": 0, "jours_validite": 7, "distribution": "membres_liste", "canal": "carte",
                "membres": [], "texte": "", "declencheur_produit": "", "quantite_min": 0, "budget": 0, "temoin_pct": 0},
    "messages": {"membres": [], "rappel_coupon": ""},
    "publicites": {"quartiers": [], "age_min": 15, "age_max": 99, "texte": ""},
    "rayons": {"facings": 1},
    "caisses": {"jours": "semaine"},
    "commandes": {"fournisseur": "grossiste"},
    "projets": {"budget_max": 0, "actif": True},
}
OBJECT_DEFAULTS = {
    "personnel": {"recruter": [], "modifier": [], "licencier": []},
    "prospectus": {"envoyer": True, "quartiers": [], "texte": "", "prix_annonces": []},
    "enquete": {"lancer": True, "cible": "clients_en_caisse", "recompense_points": 0},
    "dates_courtes": {"modifier": True},
    "fidelite": {"modifier": True},
    "mises_en_avant": {"modifier": True},
    "slogan": {"modifier": True},
}


def _conform(value, schema):
    """Garde ce qui a le bon type ; renvoie None sinon (les clés inconnues sont ignorées)."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return None
        out = {}
        for k, sub in schema["properties"].items():
            if k in value:
                c = _conform(value[k], sub)
                if c is not None:
                    out[k] = c
        return out
    if t == "array":
        if not isinstance(value, (list, tuple)):
            return None
        return [c for c in (_conform(v, schema["items"]) for v in value) if c is not None]
    if t == "string":
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return None
        value = str(value)
        return value if "enum" not in schema or value in schema["enum"] else None
    if t in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) == float("inf"):
            return None
        return int(value) if t == "integer" else float(value)
    if t == "boolean":
        return value if isinstance(value, bool) else None
    return None


def sanitize_partial(d, slots: list[str]) -> tuple[dict, list[str]]:
    """Décision partielle d'un programme → décision bien typée (éléments incomplets écartés, avec la raison)."""
    schema = decisions_schema(slots)
    problems: list[str] = []
    if not isinstance(d, dict):
        return {}, ["la décision doit être un dictionnaire"]
    unknown = [k for k in d if k not in schema["properties"]]
    if unknown:
        problems.append("clés inconnues ignorées : " + ", ".join(map(str, unknown[:8])))
    out = {}
    for key, sub in schema["properties"].items():
        if key not in d or key == "analyse":
            continue
        value = d[key]
        if sub["type"] == "array" and sub["items"].get("type") == "object":
            if not isinstance(value, (list, tuple)):
                problems.append(f"{key} : liste attendue")
                continue
            items = []
            required = sub["items"]["required"]
            for raw in value:
                item = _conform({**ITEM_DEFAULTS.get(key, {}), **raw} if isinstance(raw, dict) else raw, sub["items"])
                missing = [r for r in required if item is None or r not in item]
                if missing:
                    problems.append(f"{key} : élément ignoré (champ manquant ou invalide : {', '.join(missing[:4])})")
                    continue
                items.append(item)
            out[key] = items
        elif sub["type"] == "object":
            item = _conform({**OBJECT_DEFAULTS.get(key, {}), **value} if isinstance(value, dict) else value, sub)
            if item is None:
                problems.append(f"{key} : dictionnaire attendu")
                continue
            if key == "enquete" and "bon" in item and not all(k in item["bon"] for k in sub["properties"]["bon"]["required"]):
                bon = item.pop("bon")
                if bon.get("offre") not in (None, "aucun"):
                    problems.append("enquete : bon incomplet (offre, valeur, produit, panier_min, jours_validite)")
                    continue
            out[key] = item
        else:
            c = _conform(value, sub)
            if c is None:
                problems.append(f"{key} : type invalide")
                continue
            out[key] = c
    return out, problems


def merge_partials(items: list[dict]) -> dict:
    """Fusionne les décisions partielles d'une même exécution : listes mises bout à bout, réglages : le dernier l'emporte."""
    out: dict = {}
    for d in items:
        for k, v in d.items():
            if isinstance(v, list):
                out[k] = (out.get(k) or []) + v
            elif k == "assortiment" and isinstance(v, dict):
                cur = out.setdefault(k, {"ajouter": [], "retirer": []})
                for kk in ("ajouter", "retirer"):
                    cur[kk] = cur.get(kk, []) + list(v.get(kk, []) or [])
            elif isinstance(v, dict):
                out[k] = {**(out.get(k) or {}), **v}
            elif k == "journal":
                out[k] = (str(out.get(k, "")) + " " + str(v)).strip()
            elif k == "etiquettes_electroniques":
                out[k] = bool(out.get(k)) or bool(v)
            else:
                out[k] = v
    return out


CARD_FIELDS = ("age", "quartier", "foyer", "email", "telephone", "profession", "revenus", "vehicule", "animaux", "regime",
               "connu_par")
MAX_QUESTIONS = 5  # questions d'un questionnaire (et questions libres de la fiche d'inscription)
MAX_COUPONS = 20  # coupons aux conditions distinctes par décision (un coupon peut viser autant de membres que voulu)


def _capped(items: list, n: int, label: str, ko: list) -> list:
    """Les `n` premiers éléments ; ceux au-delà sont signalés, jamais écartés en silence."""
    if len(items) > n:
        ko.append(f"{label} : {len(items) - n} élément(s) au-delà de la limite technique de {n} par décision, ignorés")
    return items[:n]


FRIDGE_NEEDS = {"lait", "laitiers", "oeufs", "proteines", "plats"}


def _fridge_ok(pid: str, aisle: int) -> bool:
    p = PRODUCTS[pid]
    if p.frais and p.besoin in FRIDGE_NEEDS:
        return aisle in (1, 4)
    return True


async def apply_decisions(world, store, d: dict, workspace: Path | None = None, role: str | None = None,
                          allowed: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Valide et applique une décision (session d'un agent ou `ctx.decider` de son code).

    Renvoie (actions acceptées, refus avec raisons). Sans `workspace` (décision venant du code), un programme
    peut en désactiver un autre mais pas en activer : l'activation passe par une session. `allowed` limite la décision
    au domaine de l'agent (équipe de direction) ; `role` est l'auteur (programmes, journal, messages d'équipe)."""
    eco = world.cfg["economie"]
    mode = world.cfg["lois"]
    day = world.day
    ok: list[str] = []
    ko: list[str] = []
    d = dict(d or {})
    if allowed is not None:
        team = getattr(store, "team", ["gerant"])
        for k in [k for k in d if k not in allowed]:
            v = d.pop(k)
            if v not in (None, "", [], {}, False) and not (isinstance(v, dict) and not any(
                    x not in (None, "", [], {}, False, 0) for x in v.values())):
                owner = owner_of(k, team)
                ko.append(f"« {k} » ne relève pas de ton rôle" + (f" : c'est le domaine de {ROLE_TITLES.get(owner, owner)}" if owner else ""))
    # Une décision partielle ne modifie que les réglages qu'elle nomme : le reste vient de l'état actuel du magasin.
    if isinstance(d.get("fidelite"), dict):
        L = store.loyalty
        current = {"modifier": False, "actif": L["actif"], "points_par_euro": L["points_par_euro"], "valeur_point": L["valeur_point"],
                   "seuil_utilisation": L["seuil_utilisation"], "bonus_inscription": L["bonus_inscription"],
                   "donnees_demandees": list(L["donnees_demandees"]),
                   "prix_membres": [{"produit": p, "prix": v} for p, v in store.member_prices.items()]}
        d["fidelite"] = {**current, **d["fidelite"]}
    if isinstance(d.get("mises_en_avant"), dict):
        d["mises_en_avant"] = {"modifier": False, **{k: list(v) for k, v in store.secondary.items()}, **d["mises_en_avant"]}
    if isinstance(d.get("dates_courtes"), dict) and store.short_date_rule:
        d["dates_courtes"] = {"modifier": False, "actif": True, **store.short_date_rule, **d["dates_courtes"]}
    d = normalize(d)

    def valid_pid(pid) -> bool:
        return isinstance(pid, str) and pid in PRODUCTS

    # 1. Étiquettes électroniques
    if d["etiquettes_electroniques"] and not store.esl:
        cost = round(eco["cout_etiquettes_electroniques"] * float(eco.get("echelle_investissements", 1.0)), 2)
        store.invest(day, "investissement_etiquettes", cost)
        store.esl = True
        ok.append(f"étiquettes électroniques installées ({cost:.0f} €)")

    # 2. Assortiment
    for pid in d["assortiment"]["ajouter"][:10]:
        if valid_pid(pid) and not store.listed[pid]:
            store.listed[pid] = True
            store.pay(day, "referencement", eco["cout_referencement"])
            store.targets[pid] = max(store.targets.get(pid, 0), 4)
            store.order(pid, store.targets[pid], day, world.supplier)
            ok.append(f"référencé : {pid}")
        elif not valid_pid(pid):
            ko.append(f"assortiment : produit inconnu « {pid} »")
    for pid in d["assortiment"]["retirer"][:10]:
        if valid_pid(pid) and store.listed[pid]:
            store.listed[pid] = False
            refund = sum(l.qty * l.unit_cost for l in store.lots[pid]) * 0.5
            loss = sum(l.qty * l.unit_cost for l in store.lots[pid]) - refund
            store.lots[pid] = []
            store.cash += refund
            store.profit -= loss
            store.ledger[day]["retours_fournisseur"] += loss
            for k in store.secondary:
                if pid in store.secondary[k]:
                    store.secondary[k].remove(pid)
            ok.append(f"déréférencé : {pid} (stock repris à 50 %)")

    # 3. Prix
    prices_changed = 0
    for change in _capped(d["prix"], 200, "prix", ko):
        changed, notes = apply_price(world, store, change.get("produit"), change.get("prix"))
        prices_changed += changed
        ko.extend(notes)
    if prices_changed:
        ok.append(f"{prices_changed} prix mis à jour")

    # 3 bis. Prix individuels : le prix qu'un membre identifié voit et paie (0 = retour au prix normal)
    set_n, cleared = 0, 0
    for line in _capped(d["prix_individuels"], 20000, "prix_individuels", ko):
        mid, pid, price = (line or {}).get("membre"), (line or {}).get("produit"), (line or {}).get("prix")
        m = store.member_by_id(str(mid or ""))
        if m is None:
            ko.append(f"prix individuel : membre inconnu « {mid} » — ligne ignorée")
            continue
        if not valid_pid(pid):
            ko.append(f"prix individuel : produit inconnu « {pid} » — ligne ignorée")
            continue
        if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            cleared += store.individual_prices.get(m.id, {}).pop(pid, None) is not None
            continue
        floor = min_price(pid, world.supplier, mode)
        cap = round(PRODUCTS[pid].prix_conseille * 3, 2)
        if price < floor:
            ko.append(f"prix individuel {pid} pour {m.id} sous le seuil de revente à perte ; fixé à {floor:.2f} €")
        store.individual_prices.setdefault(m.id, {})[pid] = round(max(floor, min(price, cap)), 2)
        set_n += 1
    store.individual_prices = {k: v for k, v in store.individual_prices.items() if v}
    if set_n or cleared:
        ok.append(f"prix individuels : {set_n} fixés, {cleared} retirés "
                  f"({sum(len(v) for v in store.individual_prices.values())} en vigueur pour {len(store.individual_prices)} membres)")

    # 4. Fidélité
    signup_questions = None
    f = d["fidelite"]
    if f["modifier"]:
        L = store.loyalty
        L["actif"] = bool(f["actif"])
        L["points_par_euro"] = max(0.0, min(10.0, float(f["points_par_euro"])))
        L["valeur_point"] = max(0.001, min(0.1, float(f["valeur_point"])))
        L["seuil_utilisation"] = max(50, min(5000, int(f["seuil_utilisation"])))
        L["bonus_inscription"] = max(0, min(1000, int(f["bonus_inscription"])))
        L["donnees_demandees"] = list(dict.fromkeys(x for x in f["donnees_demandees"] if x in CARD_FIELDS))
        if "questions_inscription" in f:  # modérées avec les autres textes (étape 10), appliquées ensuite
            signup_questions = [clean_text(q, 200) for q in f["questions_inscription"][:MAX_QUESTIONS]
                                if isinstance(q, str) and q.strip()]
        store.member_prices = {}
        for x in f["prix_membres"][:30]:
            pid, price = x.get("produit"), x.get("prix")
            if valid_pid(pid) and isinstance(price, (int, float)):
                floor = min_price(pid, world.supplier, mode)
                if price < floor:
                    ko.append(f"prix membre {pid} sous le seuil de revente à perte ; fixé à {floor:.2f} €")
                    price = floor
                store.member_prices[pid] = round(price, 2)
        ok.append("programme de fidélité " + ("actif" if L["actif"] else "désactivé"))

    # 5. Promotions
    active = [p for p in store.promotions if p.fin >= day]
    for x in d["promotions"][:10]:
        pids = [p for p in x.get("produits", []) if valid_pid(p) and store.listed[p]]
        if not pids:
            ko.append(f"promotion sans produit valide : {x.get('produits')}")
            continue
        mech, val, y = x.get("mecanique"), float(x.get("valeur", 0)), int(x.get("y", 0))
        days = max(1, min(14, int(x.get("duree_jours", 7))))
        if mech == "pourcentage":
            if not 1 <= val <= 70:
                ko.append(f"promotion : remise de {val} % hors limites (1 à 70 %)")
                continue
            rate = val / 100
        elif mech == "x_pour_y":
            if not (2 <= val <= 6 and 1 <= y < val):
                ko.append("promotion « X pour Y » : il faut 2 ≤ X ≤ 6 et 1 ≤ Y < X")
                continue
            rate = (val - y) / val
        else:
            rate = 0.25
        allowed = [p for p in pids if check_promo_rate(p, rate, mode) >= rate - 1e-9]
        refused = [p for p in pids if p not in allowed]
        if refused:
            ko.append(f"promotion refusée (plafond légal de 34 % sur l'alimentaire) pour : {', '.join(refused)}")
        if not allowed:
            continue
        if len(active) >= 12:
            ko.append("trop de promotions simultanées (12 maximum)")
            break
        store.promo_seq += 1
        promo = Promotion(f"{store.id}-P{store.promo_seq:04d}", allowed, mech, val, y, day, day + days - 1, bool(x.get("affiche")))
        store.promotions.append(promo)
        active.append(promo)
        ok.append(f"promotion {promo.label()} sur {', '.join(allowed)} pendant {days} jours")
    store.promotions = [p for p in store.promotions if p.fin >= day - 14]

    # 6. Rayons et mises en avant
    moved = 0
    for x in d["rayons"][:40]:
        pid = x.get("produit")
        if not valid_pid(pid):
            ko.append(f"rayons : produit inconnu « {pid} »")
            continue
        r, s, n, fa = int(x.get("rayon", -1)), int(x.get("segment", -1)), x.get("niveau"), int(x.get("facings", 1))
        if r not in RAYONS or not 0 <= s <= 3 or n not in NIVEAUX:
            ko.append(f"rayons : emplacement invalide pour {pid} ({r}, {s}, {n})")
            continue
        if not _fridge_ok(pid, r):
            ko.append(f"rayons : {pid} doit rester au froid (rayon 1 ou 4)")
            continue
        old = dict(store.planogram[pid])
        target = {"rayon": r, "segment": s, "niveau": n}
        occupant = next((q for q, loc in store.planogram.items() if q != pid and loc["rayon"] == r and loc["segment"] == s and loc["niveau"] == n), None)
        if occupant:
            if not _fridge_ok(occupant, old["rayon"]):
                ko.append(f"rayons : échange impossible, {occupant} doit rester au froid")
                continue
            store.planogram[occupant] = {**store.planogram[occupant], "rayon": old["rayon"], "segment": old["segment"], "niveau": old["niveau"]}
            moved += 1
        store.planogram[pid] = {**target, "facings": max(1, min(3, fa))}
        if (old["rayon"], old["segment"], old["niveau"]) != (r, s, n):
            moved += 1
    if moved:
        store.pay(day, "reamenagement", moved * eco["cout_deplacement_produit"])
        ok.append(f"{moved} produits déplacés en rayon")
    m = d["mises_en_avant"]
    if m["modifier"]:
        placed = 0
        for place, cap in EMPLACEMENTS_SECONDAIRES.items():
            pids = [p for p in m.get(place, []) if valid_pid(p) and store.listed[p]][:cap]
            placed += len(set(pids) - set(store.secondary[place]))
            store.secondary[place] = pids
        store.pay(day, "reamenagement", placed * eco["cout_deplacement_produit"])
        ok.append("mises en avant : " + "; ".join(f"{k} = {', '.join(v) or 'vide'}" for k, v in store.secondary.items()))

    # 7. Stocks et commandes
    for x in _capped(d["stocks_cibles"], 200, "stocks_cibles", ko):
        pid = x.get("produit")
        if valid_pid(pid):
            store.targets[pid] = max(0, min(300, int(x.get("stock_cible", 0))))
    if d["stocks_cibles"]:
        ok.append(f"{len(d['stocks_cibles'])} stocks cibles mis à jour")
    for x in d["commandes"][:40]:
        pid, q = x.get("produit"), int(x.get("quantite", 0))
        if not valid_pid(pid) or not store.listed.get(pid):
            ko.append(f"commande : produit non référencé « {pid} »")
            continue
        q = max(0, min(400, q))
        done, why = store.order(pid, q, day, world.supplier, x.get("fournisseur", "grossiste"))
        (ok if done else ko).append(f"commande {pid} x{q}" + ("" if done else f" refusée : {why}"))

    hours = d["horaires"][:9]
    if hours:
        store.hours = getattr(store, "hours", {})
        for change in hours:
            store.hours[change["jours"], change["creneau"]] = change["ouvert"]
        ok.append("horaires du magasin mis à jour")

    # 8. Dates courtes
    dc = d["dates_courtes"]
    if dc["modifier"]:
        if dc["actif"]:
            store.short_date_rule = {"jours_avant_peremption": max(0, min(3, int(dc["jours_avant_peremption"]))),
                                     "remise_pct": max(5.0, min(70.0, float(dc["remise_pct"])))}
            ok.append(f"dates courtes : -{store.short_date_rule['remise_pct']:.0f} % à J-{store.short_date_rule['jours_avant_peremption']}")
        else:
            store.short_date_rule = None
            ok.append("dates courtes désactivées")

    # 9. Caisses et personnel
    for x in d["caisses"][:12]:
        n = max(1, min(3, int(x.get("nombre", 1))))
        store.checkouts[(x.get("jours"), x.get("creneau"))] = n
    if d["caisses"]:
        ok.append("organisation des caisses mise à jour")
    personnel.apply(world, store, d["personnel"], ok, ko)

    # 10. Textes : modération groupée
    texts = []
    pr = d["prospectus"]
    if pr["envoyer"] and pr["texte"].strip():
        texts.append(("prospectus", clean_text(pr["texte"], 600)))
    for i, x in enumerate(d["messages"][:3]):
        texts.append((f"message{i}", clean_text(x.get("texte", ""), 160 if x.get("canal") == "sms" else 500)))
    for i, x in enumerate(d["coupons"][:MAX_COUPONS]):
        if x.get("texte"):
            texts.append((f"coupon{i}", clean_text(x["texte"], 200)))
    for i, x in enumerate(d["publicites"][:3]):
        texts.append((f"pub{i}", clean_text(x.get("texte", ""), 300)))
    if d["slogan"]["modifier"] and d["slogan"]["texte"].strip():
        texts.append(("slogan", clean_text(d["slogan"]["texte"], 90)))
    for i, x in enumerate(d["reponses_avis"][:5]):
        texts.append((f"avis{i}", clean_text(x.get("texte", ""), 300)))
    e = d["enquete"]
    questions = [clean_text(q, 200) for q in _capped(e["questions"], MAX_QUESTIONS, "enquête : questions", ko)
                 if isinstance(q, str) and q.strip()] if e["lancer"] else []
    texts.extend((f"question{i}", q) for i, q in enumerate(questions))
    texts.extend((f"inscription{i}", q) for i, q in enumerate(signup_questions or []))
    verdicts = await moderate(world, store, texts) if texts else {}

    def accepted(tid) -> bool:
        v = verdicts.get(tid)
        if v and not v[0]:
            fine = eco["amende_manipulation"]
            store.pay(day, "amendes", fine)
            ko.append(f"texte refusé par la modération ({tid}) : {v[1]} — amende de {fine:.0f} €")
            world.journal.incident(day, store.id, "moderation", {"texte": tid, "raison": v[1]})
            return False
        return True

    if signup_questions is not None:
        kept = [q for i, q in enumerate(signup_questions) if accepted(f"inscription{i}")]
        store.loyalty["questions_inscription"] = kept
        if kept:
            ok.append(f"fiche d'inscription : {len(kept)} question(s) libre(s), posée(s) à chaque nouveau membre")

    # 11. Coupons
    def amounts_match(text, offer, value, minimum, duration, expiration=None):
        def number(value):
            return float(value.replace(",", "."))
        threshold = re.search(r"(?:dès|à partir de)\s*(\d+(?:[.,]\d+)?)\s*€", text, re.I)
        if threshold and number(threshold[1]) != minimum:
            return False
        reward = re.search(r"(\d+(?:[.,]\d+)?)\s*€\s*(?:offerts?|de remise|de réduction)", text, re.I)
        if reward and offer in ("remise_euros_panier", "parrainage") and number(reward[1]) != value:
            return False
        days = re.search(r"valable\s+(\d+)\s+jours?", text, re.I)
        if days and int(days[1]) != duration:
            return False
        absolute_date = re.search(r"jusqu.au\s+(\d{1,2})\s+(" + "|".join(temps.MOIS) + ")", text, re.I)
        if absolute_date and expiration is not None:
            expires = temps.date_of(expiration)
            if int(absolute_date[1]) != expires.day or absolute_date[2].lower() != temps.MOIS[expires.month - 1]:
                return False
        percent = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
        return not percent or not offer.startswith("remise_pct") or number(percent[1]) == value

    flyer_coupons = []
    for i, x in enumerate(_capped(d["coupons"], MAX_COUPONS, "coupons", ko)):
        offre = x.get("offre")
        if offre not in OFFRES:
            continue
        prod = x.get("produit") if valid_pid(x.get("produit")) else None
        if offre in ("remise_pct_produit", "produit_offert") and prod is None:
            ko.append(f"coupon {offre} sans produit valide")
            continue
        val = float(x.get("valeur", 0))
        if offre in ("remise_pct_produit", "remise_pct_panier") and not 1 <= val <= 60:
            ko.append(f"coupon : remise de {val} % hors limites (1 à 60 %)")
            continue
        if offre in ("remise_euros_panier", "parrainage") and not 0.5 <= val <= 30:
            ko.append(f"coupon : valeur de {val} € hors limites (0,5 à 30 €)")
            continue
        if x.get("texte") and not accepted(f"coupon{i}"):
            continue
        threshold = max(0.0, float(x.get("panier_min", 0)))
        duration = max(1, min(30, int(x.get("jours_validite", 7))))
        expiration = None if x.get("distribution") == "caisse" else day + duration
        if not amounts_match(x.get("texte", ""), offre, val, threshold, duration, expiration):
            ko.append(f"coupon {i + 1} : texte incompatible avec le montant ou le seuil du bon")
            continue
        trigger = x.get("declencheur_produit") or None
        quantity = max(0, min(30, int(x.get("quantite_min", 0))))
        if (trigger is not None and trigger not in PRODUCTS) or (quantity and trigger is None):
            ko.append("coupon : condition de produit/quantité invalide")
            continue
        budget = max(0.0, float(x.get("budget", 0)))
        control = max(0, min(50, int(x.get("temoin_pct", 0))))
        if (control or budget) and x.get("distribution") not in ("tous_les_membres", "membres_liste"):
            ko.append("coupon : budget et temoin_pct seulement pour une campagne membres (distribution tous_les_membres ou "
                      f"membres_liste, pas {x.get('distribution')}) ; mets budget = 0 et temoin_pct = 0 sinon")
            continue
        if budget and offre in ("parrainage", "points_x2"):
            ko.append(f"coupon : pas de budget pour l'offre {offre} (aucune remise directe à plafonner) ; mets budget = 0 "
                      "(temoin_pct reste possible)")
            continue
        store.campaign_seq = getattr(store, "campaign_seq", 0) + 1
        tpl = {"offre": offre, "valeur": val, "produit": prod, "panier_min": threshold,
               "declencheur_produit": trigger, "quantite_min": quantity,
               "jours_validite": duration, "texte": clean_text(x.get("texte", ""), 200),
               "campagne": f"{store.id}-S{world.day // 7 + 1}-C{store.campaign_seq}"}
        dist = x.get("distribution")
        canal = x.get("canal", "carte")
        if dist == "prospectus":
            flyer_coupons.append(tpl)
            ok.append(f"coupon joint au prospectus : {offre}")
            continue
        if dist == "caisse":
            store.bounce_back.append({**tpl, "debut": day, "fin": day + 6})
            ok.append(f"coupon distribué en caisse cette semaine : {offre}")
            continue
        targets = list(store.members.values())
        if dist == "membres_liste":
            wanted = set(x.get("membres", []))
            targets = [mm for mm in targets if mm.id in wanted]
        if not targets:
            ko.append(f"coupon {offre} : aucun membre ciblé")
            continue
        if control and len(targets) < 2:
            ko.append("coupon : au moins deux membres éligibles pour un groupe témoin")
            continue
        if budget or control:
            channel_cost = eco["cout_email"] if canal == "email" else eco["cout_sms"] if canal == "sms" else 0.0
            if budget and len(targets) * channel_cost > budget:
                ko.append("coupon : enveloppe insuffisante pour l'envoi")
                continue
            campaign = tpl["campagne"]
            salt = f"{world.cfg['graine']}:{campaign}:"
            targets.sort(key=lambda member: hashlib.sha256((salt + member.id).encode()).digest())
            n_control = min(len(targets) - 1, max(1, round(len(targets) * control / 100))) if control else 0
            controls, targets = targets[:n_control], targets[n_control:]
            store.coupon_trials = getattr(store, "coupon_trials", {})
            store.coupon_trials[campaign] = {
                "debut": day, "fin": day + tpl["jours_validite"], "budget": budget or None, "depense": 0.0,
                "membres": {**{m.id: "temoin" for m in controls}, **{m.id: "traite" for m in targets}},
                "resultats": {g: {"foyers": set(), "visites": 0, "ca": 0.0, "marge": 0.0} for g in ("traite", "temoin")},
            }
        if canal in ("email", "sms"):
            msg = {"canal": canal, "texte": tpl["texte"] or "Un cadeau vous attend en magasin.", "membres": [mm.id for mm in targets], "coupons": [tpl]}
            sent, opened, unsub = send_message(world, store, msg)
            cost = sent * (eco["cout_email"] if canal == "email" else eco["cout_sms"])
            store.pay(day, "marketing", cost)
            trial = getattr(store, "coupon_trials", {}).get(tpl["campagne"])
            if trial:
                trial["depense"] = cost
                trial["resultats"]["traite"]["marge"] -= cost
            world.journal.campaign(day, store.id, f"coupon par {canal}", tpl["texte"], sent, opened, tpl)
            ok.append(f"coupon {offre} envoyé par {canal} à {sent} membres ({opened} ouverts)")
        else:
            for mm in targets:
                c = world.customers.get(mm.client)
                if c and c.active:
                    cp = store.new_coupon(c.id, offre, val, prod, tpl["panier_min"], day, day + tpl["jours_validite"], "carte", tpl["texte"], tpl["campagne"], declencheur_produit=trigger, quantite_min=quantity)
                    c.receive_coupon(cp)
            world.journal.campaign(day, store.id, "coupon sur la carte", tpl["texte"], len(targets), len(targets), tpl)
            ok.append(f"coupon {offre} chargé sur la carte de {len(targets)} membres")

    # 12. Prospectus
    if pr["envoyer"]:
        if pr["texte"].strip() and not accepted("prospectus"):
            pass
        else:
            ex = max(0, min(300, int(pr["exemplaires"])))
            quartiers = [q for q in pr["quartiers"] if q in QUARTIERS]
            prices = {}
            lie = []
            for x in pr["prix_annonces"][:8]:
                pid, price = x.get("produit"), x.get("prix")
                if valid_pid(pid) and isinstance(price, (int, float)):
                    real = store.prices[pid]
                    promo = store.promo_for(pid, day)
                    if promo and promo.mecanique == "pourcentage":
                        real = real * (1 - promo.valeur / 100)
                    prices[pid] = round(float(price), 2)
                    if price < real - 0.01:
                        lie.append(f"{pid} annoncé {price:.2f} € pour {real:.2f} € en rayon")
            if lie:
                ko.append(f"prospectus à corriger avant publication : {'; '.join(lie)}")
                world.journal.event(day, "brouillon_publicitaire_refuse", store.id, {"details": lie})
                pr["envoyer"] = False
            if pr['envoyer']:
                flyer = {"magasin": store.id, "texte": clean_text(pr["texte"], 600), "prix": prices, "quartiers": quartiers,
                         "exemplaires": ex, "debut": day, "fin": day + 6, "coupons": flyer_coupons}
                cost = eco["cout_prospectus_fixe"] + ex * eco["cout_prospectus_unite"]
                store.pay(day, "marketing", cost)
                delivered, read = distribute_flyer(world, store, flyer)
                world.public_flyers.append({k: v for k, v in flyer.items() if k != "coupons"})
                world.journal.campaign(day, store.id, "prospectus", flyer["texte"], delivered, read, {"prix": prices, "quartiers": quartiers})
                ok.append(f"prospectus : {delivered} boîtes aux lettres, {read} lecteurs probables (coût {cost:.2f} €)")

    # 13. Messages
    for i, x in enumerate(d["messages"][:3]):
        if not accepted(f"message{i}"):
            continue
        targets = x.get("membres") or None
        reminder = x.get("rappel_coupon")
        if reminder:
            cp = store.coupons.get(reminder)
            member = store.member_of(cp.detenteur) if cp else None
            if not (cp and member and not cp.utilise and cp.debut <= day <= cp.fin):
                ko.append("rappel : bon introuvable, utilisé ou hors validité")
                continue
            if (targets and set(targets) != {member.id}) or not amounts_match(
                    x.get("texte", ""), cp.offre, cp.valeur, cp.panier_min, cp.fin - day, cp.fin):
                ko.append("rappel : texte ou destinataire incompatible avec le bon")
                continue
            targets = [member.id]
        msg = {"canal": x.get("canal"), "texte": clean_text(x.get("texte", ""), 160 if x.get("canal") == "sms" else 500),
               "membres": targets, "coupons": []}
        sent, opened, unsub = send_message(world, store, msg)
        cost = sent * (eco["cout_email"] if msg["canal"] == "email" else eco["cout_sms"])
        store.pay(day, "marketing", cost)
        world.journal.campaign(day, store.id, msg["canal"], msg["texte"], sent, opened, {"desabonnements": unsub})
        ok.append(f"{msg['canal']} envoyé à {sent} membres ({unsub} désabonnements)")

    # 14. Publicités
    for i, x in enumerate(d["publicites"][:3]):
        if not accepted(f"pub{i}"):
            continue
        budget = max(0.0, min(500.0, float(x.get("budget", 0))))
        ad = {"media": x.get("media"), "budget": budget, "texte": clean_text(x.get("texte", ""), 300),
              "quartiers": [q for q in x.get("quartiers", []) if q in QUARTIERS], "age_min": int(x.get("age_min", 15)),
              "age_max": int(x.get("age_max", 99))}
        store.pay(day, "marketing", budget)
        reached, noticed = run_ad(world, store, ad)
        world.public_ads.append({"magasin": store.id, "jour": day, **ad})
        world.journal.campaign(day, store.id, f"publicité {ad['media']}", ad["texte"], reached, noticed, {"budget": budget})
        ok.append(f"publicité {ad['media']} ({budget:.0f} €) : {reached} personnes touchées")

    # 15. Relevé de prix, enquête, avis, slogan
    rp = [p for p in d["releve_prix"][:25] if valid_pid(p)]
    if rp:
        store.pay(day, "veille", eco["cout_releve_prix"])
        world.pending_price_surveys.append((store.id, rp))
        ok.append(f"relevé de prix commandé sur {len(rp)} produits (résultats dans le prochain rapport)")
    if e["lancer"]:
        try:
            reward = validate_reward(e.get("bon"))
            if not questions or e["cible"] not in ("membres_email", "clients_en_caisse"):
                raise ValueError("questions ou canal manquants")
            points = max(0, min(300, int(e.get("recompense_points", 0))))
        except (ValueError, TypeError, OverflowError) as error:
            ko.append(f"enquête refusée : {error}")
        else:
            if all(accepted(f"question{i}") for i in range(len(questions))):
                store.survey_seq = getattr(store, "survey_seq", 0) + 1
                survey = {"id": f"{store.id}-Q{store.survey_seq}", "questions": questions, "cible": e["cible"],
                          "recompense_points": points, "bon": reward, "jour": day}
                store.survey = survey
                if e["cible"] == "membres_email":
                    msg = {"canal": "email", "texte": "Questionnaire facultatif de " + store.nom + ".", "enquete": survey}
                    sent, opened, _ = send_message(world, store, msg)
                    store.pay(day, "marketing", sent * eco["cout_email"])
                    world.journal.campaign(day, store.id, "invitation enquête", msg["texte"], sent, opened,
                                           {"enquete": survey["id"]})
                ok.append(f"enquête {survey['id']} lancée ({len(questions)} questions)")
    for i, x in enumerate(d["reponses_avis"][:5]):
        r = next((r for r in world.reviews if r["id"] == x.get("avis_id") and r["magasin"] == store.id), None)
        if r and accepted(f"avis{i}"):
            r["reponse"] = clean_text(x.get("texte", ""), 300)
            world.journal.review(r)
            ok.append(f"réponse publiée à l'avis {r['id']}")
    if d["slogan"]["modifier"] and accepted("slogan"):
        store.slogan = clean_text(d["slogan"]["texte"], 90)
        ok.append(f"slogan : « {store.slogan} »")

    # 16. Automatisations (programmes écrits par les dirigeants ou livrés par leurs sous-agents)
    for x in d["automatisations"][:100]:
        name = str(x.get("fichier", "")).strip().replace("\\", "/")
        if not x.get("actif"):
            if name in store.automations:
                store.automations[name]["actif"] = False
                ok.append(f"programme suspendu : {name}")
            else:
                ko.append(f"programme inconnu : {name}")
            continue
        if workspace is None:
            ko.append("un programme s'active pendant une session (ou par le guichet), pas depuis un autre programme")
            continue
        path = (workspace / name).resolve()
        if not path.is_relative_to(workspace.resolve()) or path.suffix != ".py" or "outils" in path.relative_to(workspace.resolve()).parts[:1]:
            ko.append(f"programme refusé : {name} doit être un fichier .py de ton dossier")
            continue
        if not path.exists():
            ko.append(f"programme introuvable : {name}")
            continue
        code = path.read_text(encoding="utf-8", errors="replace")
        errs = validate(code)
        if errs:
            ko.append(f"programme {name} refusé : " + " ; ".join(errs[:4]))
            continue
        actives = [k for k, v in store.automations.items() if v["actif"] and k != name]
        if len(actives) >= MAX_ACTIVE:
            ko.append(f"programme {name} refusé : {MAX_ACTIVE} programmes actifs (limite technique)")
            continue
        prev = store.automations.get(name, {})
        if (prev.get("actif") and prev.get("auteur") not in (None, role) and getattr(store, "domaines_stricts", False)):
            ko.append(f"programme {name} refusé : ce programme est déjà actif au nom de {ROLE_TITLES.get(prev['auteur'], prev['auteur'])}")
            continue
        changed = prev.get("code") != code
        version = prev.get("version", 0) + (1 if changed else 0)
        store.automations[name] = {"actif": True, "code": code, "erreurs": 0, "version": max(1, version), "depuis": day,
                                   "auteur": role}
        if changed or not prev:
            store.automation_versions.setdefault(name, []).append({"version": max(1, version), "jour": day, "auteur": role, "code": code})
            world.journal.programme(store.id, name, max(1, version), day, role, "session", code)
            if workspace is not None:
                arch = workspace / "historique" / "programmes"
                arch.mkdir(parents=True, exist_ok=True)
                (arch / f"{name.replace('/', '__')}.v{max(1, version)}.py").write_text(code, encoding="utf-8")
        world.journal.event(day, "automatisation", store.id, {"fichier": name, "version": max(1, version), "code": code,
                                                              "auteur": role})
        ok.append(f"programme actif : {name} (version {max(1, version)})")

    # 16 bis. Projets (équipements, services, aménagements imaginés par l'agent)
    await projets.apply(world, store, d["projets"], workspace, role, ok, ko)

    # 16 ter. Missions confiées à des sous-agents (exécutées après les sessions de la semaine)
    todo = await sous_agents.apply(world, store, d.get("missions") or [], workspace, role, ok, ko)
    store.missions_a_executer.extend(todo)

    # 17. Message au concurrent
    msg = clean_text(d["message_concurrent"], 500)
    if msg:
        if world.cfg.get("discussion_gerants"):
            world.deliver_competitor_message(world.other_store(store.id).id, f"{temps.short_label(day)} : {msg}")
            world.journal.event(day, "message_concurrent", store.id, {"texte": msg})
            ok.append("message transmis au concurrent")
        else:
            from . import rencontre
            if rencontre.active(world):
                rencontre.post(world, store, role, msg)
                ok.append("mot écrit au concurrent : il lui sera remis au début du prochain rendez-vous du dimanche soir")
            else:
                ko.append("message au concurrent non transmis : les gérants ne peuvent pas communiquer dans cette partie")

    # 18. Message à l'équipe, journal
    me = clean_text(d.get("message_equipe", ""), 1500)
    if me and len(getattr(store, "team", [])) > 1:
        board = world.__dict__.setdefault("team_board", {}).setdefault(store.id, [])
        board.append((day, role or "?", me))
        del board[:-60]
        ok.append("message transmis à ton équipe")
    ch = d.get("chantier") if isinstance(d.get("chantier"), dict) else {}
    done, nxt = clean_text(str(ch.get("realise") or ""), 1200), clean_text(str(ch.get("suivant") or ""), 800)
    if done or nxt:
        log = store.__dict__.setdefault("chantiers", [])
        log.append({"jour": day, "role": role or "gerant", "realise": done, "suivant": nxt})
        del log[:-80]
    j = clean_text(d["journal"], 3000)
    if j:
        key = journal_key(store, role)
        world.manager_journals.setdefault(key, []).append((day, j))
    return ok, ko


def journal_key(store, role: str | None) -> str:
    """Journal propre à chaque agent d'une équipe ; un agent seul garde le journal du magasin."""
    return f"{store.id}:{role}" if role and len(getattr(store, "team", [])) > 1 else store.id


def run_price_surveys(world) -> None:
    """Exécute les relevés de prix commandés (le lundi, après les décisions des deux gérants)."""
    for sid, pids in world.pending_price_surveys:
        other = world.other_store(sid)
        res = {}
        for pid in pids:
            if other.listed.get(pid):
                promo = other.promo_for(pid, world.day)
                res[pid] = {"prix": other.prices[pid], "promo": promo.label() if promo else None,
                            "prix_membre": other.member_prices.get(pid)}
            else:
                res[pid] = {"prix": None, "promo": None, "prix_membre": None}
        world.stores[sid].price_survey = {"jour": world.day, "prix": res}
    world.pending_price_surveys = []
