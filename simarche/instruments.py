"""Instruments d'observation inventés par les magasins : capacités génériques composables (caché aux agents).

Un instrument est décrit par une spécification que l'arbitre construit à partir de l'invention d'un agent :

    {"flux": [{"evenement": "consultation", "lieu": "propre_magasin", "couverture": 0.8, "rayons": [],
               "champs": [{"nom": "age_estime", "champ": "age", "precision": "moyenne"}, ...],
               "agregation": None | {"periode": "creneau"|"jour", "cles": [...], "mesures": [{"nom", "fonction", "champ"}]}}],
     "fiabilite": 0.97, "retention_jours": 28, "effets": [...], "visible_clients": "..."}

Le moteur transforme ensuite chaque événement réel du registre (registre.py) en observation selon ces capacités :
ce que l'instrument peut voir (type d'événement, lieu, rayons), sa couverture, ses pannes, le bruit imposé par la
physique de la mesure (un âge estimé à l'œil n'est jamais exact), les données manquantes, l'agrégation et la durée de
conservation. Les données internes des habitants (budget, préférences, intentions, souvenirs, opinions) ne sont
observables par aucun instrument : seulement à travers ce qu'ils acceptent de déclarer.

Aucune classe n'est écrite à la main pour une invention : toute invention est une composition de ces capacités.
Ce qui ne peut pas être représenté est signalé à l'agent au lieu d'être simulé.
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict

# -------------------------------------------------------------------- lieux (relatifs au magasin propriétaire)
LIEUX = {
    "propre_magasin": "à l'intérieur du magasin du demandeur",
    "abords_propre_magasin": "sur la voie publique devant le magasin du demandeur",
    "concurrent": "à l'intérieur du magasin concurrent (seulement ce qu'y verrait une personne présente sur place)",
    "abords_concurrent": "sur la voie publique devant le magasin concurrent",
    "ville": "dans les rues de la ville (trajets des habitants entre chez eux et un commerce)",
    "canaux_propres": "les messages (e-mails, SMS) envoyés par le demandeur",
}
FACTEUR_LIEU = {"propre_magasin": 1.0, "abords_propre_magasin": 1.5, "concurrent": 4.0, "abords_concurrent": 2.0,
                "ville": 25.0, "canaux_propres": 0.5}

# -------------------------------------------------------------------- champs observables
# classe : public (fait physique net), visuel (estimation à l'apparence, toujours bruitée), identite (reconnaître une
# même personne), suivi (relier les gestes d'une même visite), transaction (caisse du demandeur seulement),
# declaratif (seulement si la personne accepte de répondre), interne (jamais observable).
CHAMPS = {
    "age": ("visuel", "âge apparent de la personne"),
    "genre": ("visuel", "genre apparent (F/M)"),
    "taille_groupe": ("visuel", "nombre de personnes venues ensemble"),
    "enfants_presents": ("visuel", "présence d'enfants avec la personne"),
    "humeur_apparente": ("visuel", "humeur apparente de 0 (sombre) à 10 (radieuse)"),
    "mode_deplacement": ("visuel", "à pied, vélo, voiture, bus"),
    "identifiant": ("identite", "identifiant stable : la même personne garde le même identifiant d'une observation à l'autre"),
    "visite": ("suivi", "identifiant de la visite en cours : relie les gestes d'une même personne pendant une visite"),
    "produit": ("public", "identifiant du produit concerné"),
    "rayon": ("public", "numéro du rayon"),
    "niveau": ("public", "hauteur sur l'étagère"),
    "prix_affiche": ("public", "prix affiché au moment de l'événement"),
    "promo_affichee": ("public", "promotion affichée (texte) ou vide"),
    "quantite": ("public", "nombre d'articles"),
    "rayons": ("public", "rayons traversés dans l'ordre"),
    "duree_min": ("mesure", "durée en minutes"),
    "cause": ("public", "rupture (rayon vide), introuvable (cherché sans succès) ou renonce (vu mais non pris)"),
    "attente_min": ("mesure", "attente en caisse en minutes"),
    "caisses_ouvertes": ("public", "nombre de caisses ouvertes"),
    "en_rayon": ("mesure", "quantité visible en rayon"),
    "achat_effectue": ("public", "la personne repart avec des achats (vrai/faux)"),
    "destination": ("public", "commerce où se rend la personne"),
    "origine_quartier": ("public", "quartier d'où part le trajet"),
    "ticket": ("transaction", "numéro du ticket de caisse"),
    "total": ("transaction", "montant payé"),
    "articles": ("transaction", "lignes du ticket (produit, quantité, prix, promo)"),
    "coupons": ("transaction", "bons utilisés"),
    "membre": ("transaction", "numéro de carte de fidélité présenté (vide sinon)"),
    "canal": ("public", "e-mail ou SMS"),
    "campagne": ("public", "texte de la campagne (début)"),
    "membre_destinataire": ("transaction", "numéro de membre du destinataire"),
    "satisfaction": ("declaratif", "note de 1 à 5 donnée volontairement"),
    "besoin": ("declaratif", "besoin que la personne dit ne pas avoir satisfait"),
    # Jamais observables : seulement à travers ce que la personne choisit de déclarer (questionnaires, carte…)
    "budget": ("interne", "budget de la personne"),
    "sensibilite_prix": ("interne", "sensibilité de la personne aux prix"),
    "preferences": ("interne", "goûts et préférences"),
    "intention": ("interne", "intentions et projets"),
    "souvenirs": ("interne", "souvenirs et expériences passées"),
    "opinion": ("interne", "opinion sur les magasins"),
    "quartier_residence": ("interne", "adresse ou quartier de résidence"),
}
PERSONNE = ("age", "genre", "taille_groupe", "enfants_presents", "humeur_apparente", "identifiant")

# -------------------------------------------------------------------- événements de l'univers
# type : (où il se produit, description, champs propres)
EVENEMENTS = {
    "passage": (("abords",), "une personne passe devant un magasin", ("mode_deplacement",)),
    "entree": (("magasin",), "une personne entre dans un magasin", ("mode_deplacement", "visite")),
    "deplacement": (("magasin",), "parcours d'une personne dans le magasin", ("rayons", "duree_min", "visite")),
    "consultation": (("magasin",), "un produit regardé en rayon", ("produit", "rayon", "niveau", "prix_affiche", "promo_affichee", "visite")),
    "prise": (("magasin",), "un article pris en rayon pour l'acheter", ("produit", "quantite", "rayon", "prix_affiche", "promo_affichee", "visite")),
    "remise_en_rayon": (("magasin",), "un article pris puis reposé avant la caisse", ("produit", "quantite", "rayon", "visite")),
    "abandon": (("magasin",), "un produit cherché ou regardé que la personne ne prend pas", ("produit", "cause", "besoin", "visite")),
    "rupture_constatee": (("magasin",), "une personne trouve un rayon vide", ("produit", "rayon", "visite")),
    "attente_caisse": (("magasin",), "passage en caisse", ("attente_min", "caisses_ouvertes", "visite")),
    "achat": (("magasin",), "paiement en caisse", ("ticket", "total", "articles", "coupons", "membre", "quantite", "visite")),
    "sortie": (("magasin",), "une personne sort du magasin", ("duree_min", "achat_effectue", "satisfaction", "visite")),
    "etat_rayon": (("magasin",), "état d'un produit en rayon au début d'un créneau", ("produit", "rayon", "prix_affiche", "promo_affichee", "en_rayon")),
    "trajet_ville": (("ville",), "un habitant se rend dans un commerce", ("origine_quartier", "destination", "mode_deplacement")),
    "message_ouvert": (("canal",), "un destinataire ouvre un message du magasin", ("canal", "campagne", "membre_destinataire")),
    "conversation": (("domicile",), "deux habitants parlent des magasins (vie privée, chez eux)", ()),
    "changement_opinion": (("domicile",), "un habitant change d'avis sur un magasin (pensée intime)", ()),
}
PERSON_EVENTS = {"passage", "entree", "deplacement", "consultation", "prise", "remise_en_rayon", "abandon",
                 "rupture_constatee", "attente_caisse", "achat", "sortie", "trajet_ville"}
# Ce qu'une personne présente dans le magasin concurrent peut voir : pas sa caisse, ni son stock en réserve.
CONCURRENT_OK = {"entree", "deplacement", "consultation", "prise", "remise_en_rayon", "rupture_constatee", "attente_caisse",
                 "sortie", "etat_rayon", "abandon"}

PRECISIONS = ("exacte", "bonne", "moyenne", "faible")
FONCTIONS = ("compte", "somme", "moyenne", "max", "min", "distincts")
MANQUANT = {"exacte": 0.0, "bonne": 0.01, "moyenne": 0.04, "faible": 0.10}
BRUIT = {  # bruit minimal imposé par la nature de la mesure
    "age": {"exacte": 3, "bonne": 3, "moyenne": 6, "faible": 10},
    "genre": {"exacte": 0.02, "bonne": 0.02, "moyenne": 0.08, "faible": 0.2},
    "humeur_apparente": {"exacte": 1.2, "bonne": 1.2, "moyenne": 2.0, "faible": 3.0},
    "taille_groupe": {"exacte": 0.03, "bonne": 0.03, "moyenne": 0.1, "faible": 0.25},
    "enfants_presents": {"exacte": 0.03, "bonne": 0.03, "moyenne": 0.1, "faible": 0.25},
    "mode_deplacement": {"exacte": 0.05, "bonne": 0.05, "moyenne": 0.15, "faible": 0.3},
    "identifiant": {"exacte": 0.02, "bonne": 0.02, "moyenne": 0.08, "faible": 0.2},
    "visite": {"exacte": 0.0, "bonne": 0.01, "moyenne": 0.05, "faible": 0.15},
    "mesure": {"exacte": 0.0, "bonne": 0.05, "moyenne": 0.15, "faible": 0.3},
}
COUT_CLASSE = {"public": 4, "mesure": 6, "visuel": 18, "identite": 90, "suivi": 30, "transaction": 4, "declaratif": 12}
FACTEUR_PRECISION = {"exacte": 2.0, "bonne": 1.5, "moyenne": 1.0, "faible": 0.6}
MAX_LIGNES = 50000


def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(x) or math.isinf(x):
        return default
    return max(lo, min(hi, x))


def _slug(s: str, n: int = 40) -> str:
    out = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(s or "").lower()).strip("_")
    return out[:n]


def lieu_relatif(owner: str, lieu: str, stores) -> str | None:
    """Lieu d'un événement vu depuis le magasin `owner` (None : inobservable pour lui)."""
    if lieu in stores:
        return "propre_magasin" if lieu == owner else "concurrent"
    kind, _, sid = lieu.partition(":")
    if kind == "abords" and sid in stores:
        return "abords_propre_magasin" if sid == owner else "abords_concurrent"
    if kind == "canal":
        return "canaux_propres" if sid == owner else None
    if lieu == "ville":
        return "ville"
    return None


def compatible(evenement: str, lieu: str) -> bool:
    ou = EVENEMENTS[evenement][0]
    if lieu in ("propre_magasin", "concurrent"):
        return "magasin" in ou and (lieu == "propre_magasin" or evenement in CONCURRENT_OK)
    if lieu in ("abords_propre_magasin", "abords_concurrent"):
        return "abords" in ou
    if lieu == "ville":
        return "ville" in ou
    if lieu == "canaux_propres":
        return "canal" in ou
    return False


def champs_de(evenement: str) -> set[str]:
    own = set(EVENEMENTS[evenement][2])
    return own | (set(PERSONNE) if evenement in PERSON_EVENTS else set())


# -------------------------------------------------------------------- validation d'une spécification
def valider(spec: dict) -> tuple[dict, list[str]]:
    """Spécification proposée par l'arbitre → spécification exécutable et liste de ce qui ne peut pas être représenté."""
    spec = spec if isinstance(spec, dict) else {}
    out_flux, problems, used = [], [], set()
    for f in (spec.get("flux") or [])[:40]:
        if not isinstance(f, dict):
            continue
        ev, lieu = f.get("evenement"), f.get("lieu")
        if ev not in EVENEMENTS:
            problems.append(f"phénomène non représentable par le moteur : « {ev} »")
            continue
        if lieu not in LIEUX:
            problems.append(f"lieu non représentable : « {lieu} »")
            continue
        if not EVENEMENTS[ev][2] and ev not in PERSON_EVENTS:
            problems.append(f"« {EVENEMENTS[ev][1]} » se passe dans la vie privée des habitants : aucun dispositif ne peut l'observer")
            continue
        if not compatible(ev, lieu):
            problems.append(f"« {EVENEMENTS[ev][1]} » n'est pas observable {LIEUX[lieu]}")
            continue
        allowed = champs_de(ev)
        champs, seen = [], set()
        for c in (f.get("champs") or [])[:30]:
            if not isinstance(c, dict):
                continue
            cid = c.get("champ")
            if cid not in CHAMPS:
                problems.append(f"mesure non représentable par le moteur : « {cid} »")
                continue
            cls = CHAMPS[cid][0]
            if cls == "interne":
                problems.append(f"« {CHAMPS[cid][1]} » est une donnée intime des habitants : aucun instrument ne la mesure "
                                "(seulement ce qu'ils acceptent de déclarer)")
                continue
            if cid not in allowed:
                problems.append(f"« {CHAMPS[cid][1]} » n'est pas observable sur « {EVENEMENTS[ev][1]} »")
                continue
            if cls == "transaction" and lieu != "propre_magasin" and not (cid == "membre_destinataire" and lieu == "canaux_propres"):
                problems.append(f"« {CHAMPS[cid][1]} » n'est connu que de la caisse du magasin qui encaisse")
                continue
            nom = _slug(c.get("nom") or cid) or cid
            if nom in seen or nom in ("jour", "creneau", "heure", "evenement", "ref", "lieu"):
                nom = f"{nom}_{len(seen)}"
            seen.add(nom)
            prec = c.get("precision") if c.get("precision") in PRECISIONS else "moyenne"
            if cls == "visuel" and prec == "exacte":
                prec = "bonne"  # une estimation à l'apparence n'est jamais exacte
            champs.append({"nom": nom, "champ": cid, "precision": prec})
        agg = f.get("agregation") if isinstance(f.get("agregation"), dict) else None
        if agg and agg.get("active", True):
            keys = [k for k in (agg.get("cles") or []) if k in seen or k in ("lieu",)][:6]
            mesures = []
            for m in (agg.get("mesures") or [])[:10]:
                if not isinstance(m, dict) or m.get("fonction") not in FONCTIONS:
                    continue
                champ = m.get("champ") or ""
                if m["fonction"] != "compte" and champ not in seen:
                    continue
                mesures.append({"nom": _slug(m.get("nom") or f"{m['fonction']}_{champ}") or m["fonction"],
                                "fonction": m["fonction"], "champ": champ})
            agg = {"periode": agg.get("periode") if agg.get("periode") in ("creneau", "jour") else "creneau",
                   "cles": keys, "mesures": mesures or [{"nom": "nombre", "fonction": "compte", "champ": ""}]}
        else:
            agg = None
        rayons = sorted({int(r) for r in (f.get("rayons") or []) if isinstance(r, (int, float)) and 0 <= int(r) <= 5})
        out_flux.append({"evenement": ev, "lieu": lieu, "couverture": round(_num(f.get("couverture"), 0.01, 1.0, 1.0), 3),
                         "rayons": rayons, "champs": champs, "agregation": agg})
        used.add(ev)
    clean = {"flux": out_flux, "fiabilite": round(_num(spec.get("fiabilite"), 0.5, 0.999, 0.97), 3),
             "retention_jours": int(_num(spec.get("retention_jours"), 1, 365, 28))}
    return clean, problems[:20]


def cout_plancher(spec: dict) -> tuple[float, float]:
    """Prix minimal (installation, par semaine) d'une spécification : le moteur ne laisse pas sous-évaluer un dispositif."""
    inst, hebdo = 40.0, 0.0
    for f in spec.get("flux", []):
        lf = FACTEUR_LIEU[f["lieu"]]
        cov = 0.3 + 0.7 * f["couverture"]
        inst += 20 * lf * cov
        for c in f["champs"]:
            cls = CHAMPS[c["champ"]][0]
            inst += COUT_CLASSE.get(cls, 5) * FACTEUR_PRECISION[c["precision"]] * min(lf, 6) * cov
        if f["lieu"] in ("concurrent", "abords_concurrent", "ville"):
            hebdo += 6 * lf * cov  # présence ou réseau à entretenir hors de chez soi
    inst *= 1 + max(0, spec.get("retention_jours", 28) - 28) / 365
    hebdo += 0.015 * inst
    return round(inst, 2), round(hebdo, 2)


# -------------------------------------------------------------------- production des observations
def pseudonyme(owner: str, client: str, sel: str = "") -> str:
    return "P" + hashlib.sha256(f"{owner}|{sel}|{client}".encode()).hexdigest()[:7].upper()


def _valeur(owner: str, ev: dict, champ: str):
    v = ev["verite"]
    if champ in ("age", "genre", "taille_groupe", "enfants_presents", "mode_deplacement"):
        return v.get(champ) if champ != "mode_deplacement" else (ev["donnees"].get("mode_deplacement") or v.get(champ))
    if champ == "humeur_apparente":
        h = v.get("humeur")
        return None if h is None else h * 10
    if champ == "identifiant":
        return pseudonyme(owner, ev["client"]) if ev.get("client") else None
    if champ == "visite":
        vid = ev["donnees"].get("visite")
        return pseudonyme(owner, vid, "visite")[:6] if vid else None
    return ev["donnees"].get(champ)


def _bruiter(owner: str, champ: str, value, prec: str, rnd, ev: dict):
    if value is None:
        return None
    if rnd.random() < MANQUANT[prec]:
        return None
    cls = CHAMPS[champ][0]
    if champ == "age":
        return max(10, int(round(value + rnd.gauss(0, BRUIT["age"][prec]))))
    if champ == "genre":
        return ("M" if value == "F" else "F") if rnd.random() < BRUIT["genre"][prec] else value
    if champ == "humeur_apparente":
        return max(0, min(10, int(round(value + rnd.gauss(0, BRUIT["humeur_apparente"][prec])))))
    if champ == "taille_groupe":
        return max(1, value + rnd.choice((-1, 1))) if rnd.random() < BRUIT["taille_groupe"][prec] else value
    if champ == "enfants_presents":
        return (not value) if rnd.random() < BRUIT["enfants_presents"][prec] else value
    if champ == "mode_deplacement":
        return rnd.choice(["pied", "velo", "voiture", "bus"]) if rnd.random() < BRUIT["mode_deplacement"][prec] else value
    if champ == "identifiant":
        if rnd.random() < BRUIT["identifiant"][prec]:
            return None if rnd.random() < 0.5 else pseudonyme(owner, f"faux{rnd.randrange(10 ** 6)}")
        return value
    if champ == "visite":
        return None if rnd.random() < BRUIT["visite"][prec] else value
    if cls == "mesure" and isinstance(value, (int, float)):
        s = BRUIT["mesure"][prec]
        x = value * (1 + rnd.gauss(0, s)) if s else value
        return max(0, int(round(x))) if isinstance(value, int) else round(max(0.0, x), 1)
    if cls == "declaratif":
        return value if rnd.random() < float(ev["verite"].get("repond", 0.25)) else None
    if isinstance(value, (list, tuple)):
        return "-".join(str(x) for x in value) if champ == "rayons" else value
    return value


def observer(owner_id: str, name: str, system: dict, ev: dict, stores, rnd) -> list[dict]:
    """Observations qu'un instrument tire d'un événement réel (souvent aucune)."""
    spec = system.get("spec") or {}
    lieu = lieu_relatif(owner_id, ev["lieu"], stores)
    if lieu is None:
        return []
    out = []
    for i, f in enumerate(spec.get("flux", [])):
        if f["evenement"] != ev["type"] or f["lieu"] != lieu:
            continue
        if f["rayons"] and ev["donnees"].get("rayon") not in f["rayons"] and not (
                ev["type"] == "deplacement" and set(ev["donnees"].get("rayons") or []) & set(f["rayons"])):
            continue
        if rnd.random() > f["couverture"] * spec.get("fiabilite", 0.97):
            continue
        rec = {"jour": ev["jour"], "creneau": ev["creneau"], "heure": ev["heure"], "evenement": ev["type"], "ref": ev["id"]}
        if lieu in ("concurrent", "abords_concurrent"):
            rec["lieu"] = "concurrent"
        for c in f["champs"]:
            rec[c["nom"]] = _bruiter(owner_id, c["champ"], _valeur(owner_id, ev, c["champ"]), c["precision"], rnd, ev)
        out.append((i, rec))
    return out


def _agreger(rows: list[dict], agg: dict) -> list[dict]:
    groups: dict = defaultdict(list)
    for r in rows:
        key = tuple([r.get("jour")] + ([r.get("creneau")] if agg["periode"] == "creneau" else []) +
                    [r.get(k) for k in agg["cles"]])
        groups[key].append(r)
    out = []
    for key, rs in groups.items():
        rec = {"jour": key[0]}
        rest = list(key[1:])
        if agg["periode"] == "creneau":
            rec["creneau"] = rest.pop(0)
        for k, v in zip(agg["cles"], rest):
            rec[k] = v
        for m in agg["mesures"]:
            vals = [r.get(m["champ"]) for r in rs]
            nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)] + \
                   [1.0 for v in vals if v is True] + [0.0 for v in vals if v is False]
            f = m["fonction"]
            if f == "compte":
                rec[m["nom"]] = len(rs)
            elif f == "distincts":
                rec[m["nom"]] = len({v for v in vals if v not in (None, "")})
            elif not nums:
                rec[m["nom"]] = None
            elif f == "somme":
                rec[m["nom"]] = round(sum(nums), 2)
            elif f == "moyenne":
                rec[m["nom"]] = round(sum(nums) / len(nums), 2)
            elif f == "max":
                rec[m["nom"]] = max(nums)
            else:
                rec[m["nom"]] = min(nums)
        out.append(rec)
    return out


class Atelier:
    """Production des observations de tous les instruments en service (rattaché au registre pendant la partie)."""

    def __init__(self, world, rnd):
        self.world = world
        self.rnd = rnd
        self.tampons: dict = defaultdict(list)  # (magasin, instrument, flux) → observations brutes à agréger
        self.nouvelles: dict[str, list] = defaultdict(list)  # magasin → (instrument, observation) pour les programmes

    def __call__(self, ev: dict) -> None:
        w = self.world
        for sid, st in w.stores.items():
            if st.bankrupt:
                continue
            for name, s in st.systems.items():
                if s.get("etat") != "actif" or not s.get("spec"):
                    continue
                for i, rec in observer(sid, name, s, ev, w.stores, self.rnd):
                    agg = s["spec"]["flux"][i]["agregation"]
                    if agg:
                        self.tampons[(sid, name, i)].append(rec)
                    else:
                        self.ranger(st, name, rec)

    def ranger(self, st, name: str, rec: dict) -> None:
        buf = st.system_data.setdefault(name, [])
        buf.append(rec)
        if len(buf) > MAX_LIGNES:
            del buf[:len(buf) - MAX_LIGNES]
        s = st.systems.get(name)
        if s is not None:
            s["observations"] = s.get("observations", 0) + 1
        self.nouvelles[st.id].append((name, rec))

    def fin_periode(self, periode: str) -> None:
        """Agrégation à la fin d'un créneau (et des flux journaliers à la fin du jour)."""
        for key in list(self.tampons):
            sid, name, i = key
            st = self.world.stores[sid]
            s = st.systems.get(name)
            if not s or not s.get("spec") or i >= len(s["spec"]["flux"]):
                del self.tampons[key]
                continue
            agg = s["spec"]["flux"][i]["agregation"]
            if agg and (agg["periode"] == periode or periode == "jour"):
                for rec in _agreger(self.tampons.pop(key), agg):
                    rec["evenement"] = s["spec"]["flux"][i]["evenement"]
                    self.ranger(st, name, rec)

    def prendre_nouvelles(self, sid: str) -> list:
        out, self.nouvelles[sid] = self.nouvelles[sid], []
        return out


def elaguer(store, day: int) -> None:
    for name, buf in store.system_data.items():
        keep = int(((store.systems.get(name) or {}).get("spec") or {}).get("retention_jours", 28))
        if buf and buf[0].get("jour", day) < day - keep:
            store.system_data[name] = [r for r in buf if r.get("jour", day) >= day - keep]


# -------------------------------------------------------------------- textes
def decrire(spec: dict) -> list[str]:
    """Ce que l'instrument fournira, en mots (montré à l'agent dans le devis)."""
    lines = []
    for f in spec.get("flux", []):
        cov = f"{f['couverture'] * 100:.0f} %"
        what = f"{EVENEMENTS[f['evenement']][1]} — {LIEUX[f['lieu']]} (part des cas observés : {cov}"
        what += f", rayons {f['rayons']})" if f["rayons"] else ")"
        cols = [f"{c['nom']} = {CHAMPS[c['champ']][1]} (précision {c['precision']})" for c in f["champs"]]
        if f["agregation"]:
            a = f["agregation"]
            meas = ", ".join(f"{m['nom']} = {m['fonction']}{' de ' + m['champ'] if m['champ'] else ''}" for m in a["mesures"])
            what += f" ; données regroupées par {a['periode']}" + (f" et {', '.join(a['cles'])}" if a["cles"] else "") + f" : {meas}"
        lines.append(what + (" ; colonnes : " + "; ".join(cols) if cols else ""))
    if spec.get("flux"):
        lines.append(f"fiabilité {spec.get('fiabilite', 0.97) * 100:.0f} % ; conservation {spec.get('retention_jours', 28)} jours")
    return lines


def ontologie_texte() -> str:
    """Ce que le moteur sait représenter (pour l'arbitre ; jamais montré aux agents)."""
    L = ["PHÉNOMÈNES QUE LE MOTEUR SIMULE (evenement : où ; description ; champs propres) :"]
    for k, (ou, d, ch) in EVENEMENTS.items():
        extra = " + champs de personne" if k in PERSON_EVENTS else ""
        L.append(f"- {k} : {'/'.join(ou)} ; {d} ; {', '.join(ch) or '—'}{extra}")
    L.append(f"Champs de personne : {', '.join(PERSONNE)}.")
    L.append("\nLIEUX d'observation (relatifs au demandeur) : " + "; ".join(f"{k} = {v}" for k, v in LIEUX.items()))
    L.append("Dans le magasin concurrent, seuls ces phénomènes sont visibles d'une personne présente : " + ", ".join(sorted(CONCURRENT_OK)))
    L.append("\nCHAMPS (classe : description). public = fait net ; mesure = grandeur mesurée (bruit selon la précision) ; "
             "visuel = estimation à l'apparence (jamais exacte) ; identite = reconnaître une même personne d'une fois sur "
             "l'autre ; suivi = relier les gestes d'une même visite ; transaction = connu de la caisse du demandeur seulement ; "
             "declaratif = seulement si la personne accepte de répondre ; interne = jamais observable :")
    L += [f"- {k} ({cls}) : {d}" for k, (cls, d) in CHAMPS.items()]
    L.append(f"\nPrécisions : {', '.join(PRECISIONS)}. Agrégation possible par creneau ou jour, fonctions : {', '.join(FONCTIONS)}.")
    return "\n".join(L)


# -------------------------------------------------------------------- anciens systèmes (sauvegardes antérieures)
LEGACY = {
    "comptage": {"evenement": "entree", "lieu": "propre_magasin", "champs": [{"nom": "personnes", "champ": "taille_groupe", "precision": "bonne"}],
                 "agregation": {"periode": "creneau", "cles": [], "mesures": [{"nom": "entrees", "fonction": "compte", "champ": ""},
                                                                             {"nom": "personnes", "fonction": "somme", "champ": "personnes"}]}},
    "parcours": {"evenement": "deplacement", "lieu": "propre_magasin", "champs": [{"nom": "rayons", "champ": "rayons", "precision": "exacte"},
                                                                               {"nom": "duree_min", "champ": "duree_min", "precision": "bonne"}]},
    "profil_apparent": {"evenement": "entree", "lieu": "propre_magasin", "champs": [{"nom": "age_estime", "champ": "age", "precision": "moyenne"},
                                                                                {"nom": "sexe_estime", "champ": "genre", "precision": "moyenne"},
                                                                                {"nom": "enfants", "champ": "enfants_presents", "precision": "bonne"}]},
    "attention_produits": {"evenement": "consultation", "lieu": "propre_magasin", "champs": [{"nom": "produit", "champ": "produit", "precision": "exacte"}]},
    "lien_ticket": {"evenement": "achat", "lieu": "propre_magasin", "champs": [{"nom": "ticket", "champ": "ticket", "precision": "exacte"}]},
    "reidentification": {"evenement": "entree", "lieu": "propre_magasin", "champs": [{"nom": "visiteur", "champ": "identifiant", "precision": "bonne"}]},
    "rue": {"evenement": "passage", "lieu": "abords_propre_magasin", "champs": [],
            "agregation": {"periode": "creneau", "cles": [], "mesures": [{"nom": "passants", "fonction": "compte", "champ": ""}]}},
    "files_attente": {"evenement": "attente_caisse", "lieu": "propre_magasin", "champs": [{"nom": "attente", "champ": "attente_min", "precision": "bonne"}],
                      "agregation": {"periode": "creneau", "cles": [], "mesures": [{"nom": "attente_moyenne", "fonction": "moyenne", "champ": "attente"},
                                                                                  {"nom": "attente_max", "fonction": "max", "champ": "attente"}]}},
    "ruptures_rayon": {"evenement": "rupture_constatee", "lieu": "propre_magasin", "champs": [{"nom": "produit", "champ": "produit", "precision": "exacte"}]},
    "satisfaction_sortie": {"evenement": "sortie", "lieu": "propre_magasin", "champs": [{"nom": "note_sortie", "champ": "satisfaction", "precision": "exacte"}]},
}


def spec_legacy(capteurs: list[dict]) -> dict:
    flux = []
    for c in capteurs or []:
        base = LEGACY.get(c.get("id"))
        if base:
            flux.append({**base, "couverture": c.get("couverture", 1.0), "rayons": []})
    return valider({"flux": flux})[0]
