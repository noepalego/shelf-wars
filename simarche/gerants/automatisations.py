"""Bac à sable pour le code écrit par les dirigeants et leurs sous-agents.

Déclencheurs : chaque matin, à chaque créneau, à chaque passage en caisse, à chaque observation d'un instrument, à
chaque changement de prix, à chaque événement du magasin, chaque dimanche, et à une période choisie par le programme.

Défense en profondeur : analyse du code (sous-ensemble de Python sans import, sans accès aux attributs
internes), fonctions de base restreintes, exécution dans un processus séparé, délai maximal par appel (le temps de
calcul s'achète comme un équipement). Le code ne voit qu'une copie des données de son magasin (et des informations
publiques), jamais l'état caché de la ville. Il ne fait que proposer des actions ; le moteur les valide ensuite avec
les mêmes règles et les mêmes coûts que les décisions des sessions.
"""
from __future__ import annotations

import ast
import hashlib
import math
import multiprocessing as mp
import random
import statistics
import traceback
import types

HOOKS = ("chaque_matin", "chaque_creneau", "a_chaque_ticket", "a_chaque_observation", "a_chaque_changement_prix",
         "a_chaque_evenement", "chaque_dimanche", "periodique")
HOOK_ARITY = {"chaque_matin": 1, "chaque_creneau": 1, "chaque_dimanche": 1, "periodique": 1, "a_chaque_ticket": 2,
              "a_chaque_observation": 3, "a_chaque_changement_prix": 2, "a_chaque_evenement": 2}
MAX_CODE = 60000
MAX_ACTIVE = 200  # protection technique seulement
MAX_ACTIONS = 20000  # actions par exécution : protection technique, toute action au-delà est signalée
DELAI_BASE_S = 3.0

ALLOWED_NODES = {
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Assign, ast.AugAssign, ast.AnnAssign,
    ast.For, ast.While, ast.If, ast.IfExp, ast.Compare, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name,
    ast.Load, ast.Store, ast.Del, ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set, ast.ListComp, ast.DictComp,
    ast.SetComp, ast.GeneratorExp, ast.comprehension, ast.Subscript, ast.Slice, ast.Attribute, ast.Expr, ast.Pass,
    ast.Break, ast.Continue, ast.keyword, ast.Lambda, ast.JoinedStr, ast.FormattedValue, ast.Try, ast.ExceptHandler,
    ast.Assert, ast.Delete, ast.Starred,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.BitAnd, ast.BitOr,
}
FORBIDDEN_NAMES = {"eval", "exec", "compile", "open", "globals", "locals", "vars", "getattr", "setattr", "delattr", "type",
                   "object", "super", "memoryview", "breakpoint", "input", "help", "exit", "quit", "classmethod",
                   "staticmethod", "property", "dir", "id", "bytearray", "bytes", "iter", "next", "frozenset", "issubclass"}
FORBIDDEN_ATTRS = {"format", "format_map", "mro", "gi_frame", "gi_code", "f_globals", "f_locals", "f_builtins", "f_back",
                   "f_code", "tb_frame", "tb_next", "cr_frame", "cr_code", "ag_frame", "ag_code", "co_code",
                   "sys", "os", "modules", "builtins", "subprocess", "system", "popen", "spawn"}

# Fonctions statistiques exposées sans le module lui-même (qui donnerait accès à d'autres modules).
SAFE_STATS = types.SimpleNamespace(mean=statistics.mean, fmean=statistics.fmean, median=statistics.median,
                                   stdev=statistics.stdev, pstdev=statistics.pstdev, variance=statistics.variance,
                                   quantiles=statistics.quantiles, correlation=statistics.correlation,
                                   linear_regression=statistics.linear_regression)


def validate(code: str) -> list[str]:
    errors = []
    if len(code) > MAX_CODE:
        return [f"fichier trop long ({len(code)} caractères, maximum {MAX_CODE})"]
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"erreur de syntaxe ligne {e.lineno} : {e.msg}"]
    for node in ast.walk(tree):
        if type(node) not in ALLOWED_NODES:
            errors.append(f"construction interdite : {type(node).__name__} (ligne {getattr(node, 'lineno', '?')})")
        elif isinstance(node, ast.Name) and (node.id.startswith("_") or node.id in FORBIDDEN_NAMES):
            errors.append(f"nom interdit : {node.id} (ligne {node.lineno})")
        elif isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in FORBIDDEN_ATTRS):
            errors.append(f"attribut interdit : .{node.attr} (ligne {node.lineno})")
        elif isinstance(node, ast.FunctionDef) and node.name.startswith("_"):
            errors.append(f"nom de fonction interdit : {node.name}")
    if not errors:
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        if not names & set(HOOKS):
            errors.append("aucune fonction reconnue : définis au moins une de " + ", ".join(HOOKS))
        for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in HOOK_ARITY):
            positional = len(fn.args.posonlyargs + fn.args.args) - len(fn.args.defaults)
            if positional != HOOK_ARITY[fn.name] or fn.args.kwonlyargs:
                errors.append(f"{fn.name} reçoit {HOOK_ARITY[fn.name]} argument(s) positionnel(s)")
    return errors[:10]


class Ctx:
    """Objet passé au code du gérant : ses données (copie) et ses leviers."""

    def __init__(self, data: dict, actions: list, logs: list):
        self.jour = data.get("jour")
        self.jour_semaine = data.get("jour_semaine")
        self.semaine = data.get("semaine")
        self.date = data.get("date")
        self.creneau = data.get("creneau")
        self.creneaux = data.get("creneaux", [])
        self.meteo = data.get("meteo", "")
        self.magasin = data.get("magasin", {})
        self.tresorerie = data.get("tresorerie")
        self.resultat = data.get("resultat")
        self.etiquettes_electroniques = data.get("etiquettes_electroniques", False)
        self.fidelite = data.get("fidelite", {})
        self.produits = data.get("produits", [])
        self.ventes_par_jour = data.get("ventes", [])
        self.tickets_recents = data.get("tickets", [])
        self.membres = data.get("membres", [])
        self.prix_individuels = data.get("prix_individuels", {})  # membre -> {produit: prix} en vigueur
        self.reponses = data.get("reponses", [])
        self.coupons = data.get("coupons", [])
        self.comptes = data.get("compta", [])
        self.promotions = data.get("promotions", [])
        self.enquete_en_cours = data.get("enquete")
        self.prix_concurrent = data.get("prix_concurrent", {})
        self.releve_concurrent = data.get("releve_concurrent")
        self.avis = data.get("avis", [])
        self.concurrent = data.get("concurrent", {})
        self.experiences_coupons = data.get("experiences_coupons", [])
        self.horaires = data.get("horaires", [])
        self.offres_locales = data.get("offres_locales", {})
        self.places_frais_libres = data.get("places_frais_libres")
        self.actualites = data.get("actualites", [])
        self.rayons = data.get("rayons", {})
        self.personnel = data.get("personnel", [])
        self.systemes = data.get("systemes", {})
        self.instruments = data.get("instruments", {})
        self.tables = data.get("tables", {})
        self.equipe = data.get("equipe", [])
        self.memoire = data.get("memoire", {})
        self.stockage = self.memoire
        self.hasard = random.Random(data.get("graine", 0))
        self.actions = actions
        self.logs = logs

    # ---------------------------------------------------------------- lecture
    def ventes(self, jours=7):
        lim = (self.jour or 0) - int(jours)
        return [v for v in self.ventes_par_jour if v["jour"] >= lim]

    def tickets(self, jours=7):
        lim = (self.jour or 0) - int(jours)
        return [t for t in self.tickets_recents if t["jour"] >= lim]

    def compta(self, jours=7):
        lim = (self.jour or 0) - int(jours)
        return [c for c in self.comptes if c["jour"] >= lim]

    def produit(self, produit_id):
        for p in self.produits:
            if p["id"] == produit_id:
                return p
        return None

    def donnees(self, instrument, jours=7):
        """Observations d'un de tes instruments (liste vide s'il n'existe pas ou n'a encore rien vu)."""
        lim = (self.jour or 0) - int(jours)
        return [r for r in self.systemes.get(instrument, []) if r.get("jour", 0) >= lim]

    def table(self, nom):
        """Table publiée par un programme (`ctx.publier`)."""
        return list(self.tables.get(nom, []))

    def membre(self, membre_id):
        for m in self.membres:
            if m["membre"] == membre_id:
                return m
        return None

    # ---------------------------------------------------------------- actions
    def decider(self, decision):
        """Applique une décision partielle : mêmes clés que la décision hebdomadaire (voir la liste des leviers)."""
        if isinstance(decision, dict):
            self.actions.append({"type": "decider", "decision": decision})

    def prix_individuel(self, membre, produit, prix):
        """Prix vu et payé par ce membre quand il présente sa carte (0 : retour au prix normal)."""
        self.decider({"prix_individuels": [{"membre": str(membre), "produit": str(produit), "prix": float(prix)}]})

    def fixer_prix(self, produit, prix, creneau=None):
        self.actions.append({"type": "fixer_prix", "produit": str(produit), "prix": float(prix), "creneau": creneau})

    def emettre_coupon(self, membre, offre, valeur, produit=None, panier_min=0, jours_validite=7, texte=""):
        self.actions.append({"type": "emettre_coupon", "membre": str(membre), "offre": str(offre), "valeur": float(valeur),
                             "produit": produit, "panier_min": float(panier_min), "jours_validite": int(jours_validite),
                             "texte": str(texte)[:200]})

    def promotion(self, produits, mecanique="pourcentage", valeur=10, y=0, duree_jours=7, affiche=True):
        self.decider({"promotions": [{"produits": list(produits), "mecanique": mecanique, "valeur": valeur, "y": y,
                                      "duree_jours": duree_jours, "affiche": affiche}]})

    def enquete(self, questions, cible="clients_en_caisse", recompense_points=0, bon=None):
        e = {"lancer": True, "questions": list(questions), "cible": cible, "recompense_points": recompense_points}
        if bon:
            e["bon"] = bon
        self.decider({"enquete": e})

    def message(self, canal, texte, membres=None):
        self.decider({"messages": [{"canal": canal, "texte": texte, "membres": list(membres or [])}]})

    def placer(self, produit, rayon, segment, niveau, facings=1):
        self.decider({"rayons": [{"produit": produit, "rayon": rayon, "segment": segment, "niveau": niveau, "facings": facings}]})

    def stock_cible(self, produit, quantite):
        self.decider({"stocks_cibles": [{"produit": produit, "stock_cible": quantite}]})

    def commander(self, produit, quantite, fournisseur="grossiste"):
        self.decider({"commandes": [{"produit": produit, "quantite": quantite, "fournisseur": fournisseur}]})

    def annoter_membre(self, membre, tags=None, note=None):
        self.actions.append({"type": "annoter", "membre": str(membre), "tags": [str(t)[:40] for t in (tags or [])][:12],
                             "note": None if note is None else str(note)[:300]})

    def groupe(self, identifiant, experience, groupes=("temoin", "traite"), parts=None):
        """Répartition stable au hasard : le même identifiant (membre, produit…) tombe toujours dans le même groupe pour
        une même expérience, d'un jour à l'autre et d'un programme à l'autre. `parts` : poids des groupes (égaux sinon)."""
        groupes = [str(g) for g in groupes] or ["temoin", "traite"]
        poids = [max(0.0, float(x)) for x in (parts or [1] * len(groupes))][:len(groupes)]
        poids += [0.0] * (len(groupes) - len(poids))
        total = sum(poids) or 1.0
        u = int(hashlib.sha256(f"{experience}|{identifiant}".encode()).hexdigest()[:12], 16) / 16 ** 12
        acc = 0.0
        for g, w in zip(groupes, poids):
            acc += w / total
            if u < acc:
                return g
        return groupes[-1]

    def noter(self, texte):
        self.logs.append(str(texte)[:300])

    def publier(self, nom, lignes, remplacer=True):
        """Publie une table (liste de dictionnaires) dans donnees/programmes/<nom>.csv et ctx.table(nom)."""
        lignes = list(lignes)
        if len(lignes) > 5000:
            self.logs.append(f"table « {str(nom)[:60]} » : {len(lignes) - 5000} lignes au-delà de 5 000 non publiées (limite technique)")
        rows = [dict(l) for l in lignes[:5000] if isinstance(l, dict)]
        self.actions.append({"type": "publier", "nom": str(nom)[:60], "lignes": rows, "remplacer": bool(remplacer)})


def _safe_builtins(logs: list) -> dict:
    def _print(*args, **kw):
        logs.append(" ".join(str(a) for a in args)[:300])
    return {"abs": abs, "all": all, "any": any, "bool": bool, "dict": dict, "enumerate": enumerate, "filter": filter,
            "float": float, "int": int, "len": len, "list": list, "map": map, "max": max, "min": min, "range": range,
            "reversed": reversed, "round": round, "set": set, "sorted": sorted, "str": str, "sum": sum, "tuple": tuple,
            "zip": zip, "isinstance": isinstance, "divmod": divmod, "print": _print, "Exception": Exception,
            "ValueError": ValueError, "KeyError": KeyError, "ZeroDivisionError": ZeroDivisionError, "TypeError": TypeError,
            "IndexError": IndexError, "True": True, "False": False, "None": None}


def _run_one(code: str, jobs: list, data: dict):
    actions, logs = [], []
    g = {"__builtins__": _safe_builtins(logs), "math": math, "statistics": SAFE_STATS}
    exec(compile(code, "<automatisation>", "exec"), g)  # noqa: S102 (code validé par validate())
    ctx = Ctx(data, actions, logs)
    ran = False
    for hook, args in jobs:
        fn = g.get(hook)
        if fn is None:
            continue
        if hook == "periodique":
            try:
                period = max(1, int(g.get("PERIODE_JOURS", 1)))
            except (TypeError, ValueError):
                period = 1
            if (data.get("jour") or 0) % period:
                continue
        ran = True
        fn(ctx, *args)
    if not ran:
        return None
    return {"actions": actions[:MAX_ACTIONS], "ignorees": max(0, len(actions) - MAX_ACTIONS), "logs": logs[:50] + ([f"… {len(logs) - 50} autres notes non transmises (50 par exécution)"] if len(logs) > 50 else []),
            "memoire": ctx.memoire}


def _worker(conn) -> None:
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            break
        if msg is None:
            break
        codes, jobs, data = msg
        out = {"actions": [], "logs": [], "erreurs": {}, "memoire": data.get("memoire", {})}
        for name, code in codes.items():
            data["memoire"] = out["memoire"]
            try:
                r = _run_one(code, jobs, data)
                if r:
                    out["actions"] += [dict(a, source=name) for a in r["actions"]]
                    if r.get("ignorees"):
                        out.setdefault("ignorees", {})[name] = r["ignorees"]
                    out["logs"] += [f"[{name}] {l}" for l in r["logs"]]
                    out["memoire"] = r["memoire"] if isinstance(r["memoire"], dict) else out["memoire"]
            except Exception as e:  # erreur du code de l'agent
                out["erreurs"][name] = f"{type(e).__name__} : {e} | {traceback.format_exc(limit=1).splitlines()[-1][:200]}"
        try:
            conn.send(out)
        except Exception:
            conn.send({"actions": [], "logs": [], "erreurs": {"*": "résultat non transmissible"}, "memoire": {}})


class AutomationRunner:
    def __init__(self, timeout: float = DELAI_BASE_S):
        self.timeout = timeout
        self.proc = None
        self.conn = None

    def _start(self) -> None:
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker, args=(child,), daemon=True)
        self.proc.start()
        self.conn = parent

    def call(self, codes: dict[str, str], hook: str | None, data: dict, ticket=None, *, jobs: list | None = None,
             factor: float = 1.0) -> dict:
        """Exécute `hook` (ou une liste de `jobs` : [(hook, arguments)]) pour chaque programme ; `factor` multiplie le
        temps de calcul (équipement acheté par le magasin)."""
        if jobs is None:
            jobs = [(hook, (ticket,) if hook == "a_chaque_ticket" else ())]
        if not codes or not jobs:
            return {"actions": [], "logs": [], "erreurs": {}, "memoire": data.get("memoire", {})}
        if self.proc is None or not self.proc.is_alive():
            self._start()
        self.conn.send((codes, jobs, data))
        budget = min(600.0, self.timeout * max(1.0, factor) * (1 + len(jobs) / 20) * max(1, len(codes)))
        if self.conn.poll(budget + 5):
            return self.conn.recv()
        self.proc.kill()
        self.proc = None
        return {"actions": [], "logs": [], "erreurs": {n: f"délai dépassé ({budget:.0f} s : boucle infinie ou calcul trop lourd ?)"
                                                       for n in codes}, "memoire": data.get("memoire", {})}

    def close(self) -> None:
        if self.proc is not None and self.proc.is_alive():
            try:
                self.conn.send(None)
                self.proc.join(2)
            except Exception:
                pass
            if self.proc.is_alive():
                self.proc.kill()
        self.proc = None


API_DOC = """# Programmer le magasin

Tu peux écrire autant de programmes Python que tu veux (fichiers `.py` dans `automatisations/`, ou livrés par un
sous-agent) : algorithmes, modèles, calculs, règles de décision… Le magasin les exécute tout seul, entre tes sessions,
tant qu'ils sont actifs. Écrire un programme ne coûte rien ; seul le matériel ou les services payants qu'il utiliserait
se paient (le temps de calcul de base est inclus : voir « Règles »).

Activation : champ `automatisations` de ta décision (`{fichier, actif}`), ou pendant ta session
`python outils/moteur.py activer automatisations/x.py` (et `desactiver`). Modifier le fichier puis le réactiver crée
une nouvelle version (toutes les versions sont archivées). Pour essayer un programme sur les données actuelles sans
rien appliquer : `python outils/moteur.py tester automatisations/x.py`.

## Déclencheurs (définis seulement ceux dont tu as besoin)

- `chaque_matin(ctx)` : chaque jour avant l'ouverture ;
- `chaque_creneau(ctx)` : à la fin de chaque créneau d'ouverture (`ctx.creneau`) ;
- `a_chaque_ticket(ctx, ticket)` : juste après chaque passage en caisse ;
- `a_chaque_observation(ctx, instrument, observation)` : à chaque nouvelle donnée d'un de tes instruments (appelé
  à la fin du créneau pour les observations de ce créneau) ;
- `a_chaque_changement_prix(ctx, changement)` : après un changement de prix chez toi `{jour, produit, ancien_prix, nouveau_prix}` ;
- `a_chaque_evenement(ctx, evenement)` : événements de ton magasin `{jour, type, ...}` (types : `nouveau_membre`,
  `rayon_vide`, `avis`, `reponse_enquete`, `instrument_installe`, `mission_terminee`) ;
- `chaque_dimanche(ctx)` : chaque dimanche soir après la fermeture ;
- `periodique(ctx)` : tous les `PERIODE_JOURS` jours (constante à définir en haut du fichier, 1 par défaut).

## Ce que `ctx` contient (copie des données de ton magasin et informations publiques)

- `ctx.jour`, `ctx.jour_semaine`, `ctx.semaine`, `ctx.date`, `ctx.creneau`, `ctx.creneaux`, `ctx.meteo`, `ctx.actualites`
- `ctx.tresorerie`, `ctx.resultat` (résultat cumulé), `ctx.etiquettes_electroniques`, `ctx.fidelite` (réglages de ta carte)
- `ctx.produits` : `{id, nom, besoin, gamme, reference, prix, prix_membre, prix_achat, prix_conseille, seuil_revente_perte,
  stock, en_rayon, en_reserve, stock_cible, en_commande, stock_date_courte, peremption_jours, conservation_jours,
  ventes_7j, rayon, segment, niveau, facings, mises_en_avant, promo, rupture_fournisseur}` ; `ctx.produit(id)`
- `ctx.instruments` : tes dispositifs `{nom: {etat, installation, fonctionnalites, donnees, cout_hebdomadaire}}`
- `ctx.donnees(nom, jours=7)` : observations d'un dispositif (colonnes selon ce qu'il mesure, valeurs parfois
  manquantes ou imprécises comme dans la réalité) ; `ctx.systemes` : toutes, `{nom: [observations récentes]}`
- `ctx.tables`, `ctx.table(nom)` : tables publiées par tes programmes
- `ctx.personnel` : salariés `{employe, prenom, poste, contrat, creneaux, creneaux_par_semaine, cout_creneau, debut, fin, depart, en_poste}`
- `ctx.equipe` : derniers messages de l'équipe de direction `{jour, role, texte}`
- `ctx.ventes(jours=7)` : `{jour, produit, quantite, ca, cout}` (91 jours au plus)
- `ctx.tickets(jours=7)` : `{ticket, jour, creneau, membre, total, sous_total, remise_coupons, remise_points, coupons,
  paiement (carte ou especes), carte_bancaire (4 derniers chiffres lus par le terminal, vide en espèces),
  articles: [{produit, quantite, prix_unitaire, total, promo, date_courte}]}` (42 jours au plus)
- `ctx.membres` : fichier de la carte `{membre, inscrit_jour, donnees, joignable_email, joignable_sms, points, visites,
  total_depense, derniere_visite, tags, notes, passages: [{jour, creneau, montant}] (120 derniers)}` ; `ctx.membre(id)`
- `ctx.reponses` : réponses aux questionnaires `{enquete, jour, questions, reponses, membre}`
- `ctx.coupons` : bons émis `{coupon, campagne, offre, valeur, produit, canal, debut, fin, membre, utilise, jour_utilisation}`
- `ctx.compta(jours=7)` : comptes du jour `{jour, ventes, cout_des_ventes, demarque, marketing, ..., passages}` (91 jours au plus)
- `ctx.promotions` (en cours), `ctx.enquete_en_cours`, `ctx.rayons` (noms des rayons)
- `ctx.prix_concurrent` : `{produit: prix}` de ton dernier relevé payé (`ctx.releve_concurrent` : détail et date)
- `ctx.experiences_coupons` (résultats par groupe), `ctx.horaires`, `ctx.offres_locales`
- `ctx.avis` (avis publics des deux magasins), `ctx.concurrent` (communication publique du concurrent)
- `ctx.memoire` (alias `ctx.stockage`) : dictionnaire conservé d'un appel à l'autre, partagé par les programmes du
  magasin (500 000 caractères au plus)
- `ctx.hasard` : générateur aléatoire (`ctx.hasard.random()`)

`ticket` : `{ticket, membre, fiche_membre, total, sous_total, remise_coupons, coupons, jour, creneau, paiement,
carte_bancaire, articles: [{produit, quantite, prix_unitaire, total, promo}]}`.

## Ce que ton code peut faire

- `ctx.decider({...})` : n'importe quelle partie de la décision de session, mêmes clés et mêmes règles (`prix`,
  `promotions`, `coupons`, `prospectus`, `messages`, `publicites`, `fidelite`, `rayons`, `mises_en_avant`,
  `stocks_cibles`, `commandes`, `assortiment`, `dates_courtes`, `caisses`, `personnel`, `etiquettes_electroniques`,
  `releve_prix`, `enquete`, `reponses_avis`, `slogan`, `journal`). Les champs absents ne changent rien.
  Raccourcis : `ctx.promotion(produits, mecanique, valeur, y=0, duree_jours=7, affiche=True)`,
  `ctx.enquete(questions, cible, recompense_points=0, bon=None)`, `ctx.message(canal, texte, membres=None)`,
  `ctx.placer(produit, rayon, segment, niveau, facings=1)`, `ctx.stock_cible(produit, quantite)`, `ctx.commander(produit, quantite, fournisseur="grossiste")`.
- `ctx.fixer_prix(produit, prix, creneau=None)` : sans créneau, nouveau prix (étiquette payante sans étiquettes
  électroniques) ; avec un créneau, prix valable pour ce créneau aujourd'hui seulement (étiquettes électroniques).
- `ctx.prix_individuel(membre, produit, prix)` : prix que ce membre voit et paie quand il présente sa carte, plus haut
  ou plus bas que le prix en rayon (0 : retour au prix normal) ; `ctx.prix_individuels` : ceux en vigueur.
- `ctx.emettre_coupon(membre, offre, valeur, produit=None, panier_min=0, jours_validite=7, texte="")` : bon chargé sur la
  carte d'un membre (`remise_pct_produit`, `remise_pct_panier`, `remise_euros_panier`, `produit_offert`, `points_x2`).
- `ctx.annoter_membre(membre, tags=None, note=None)` : étiquettes et note dans ta fiche client.
- `ctx.publier(nom, lignes, remplacer=True)` : publie une table (`donnees/programmes/<nom>.csv`, `ctx.table(nom)`).
- `ctx.noter(texte)` (ou `print`) : une ligne dans ton prochain rapport.
- `ctx.groupe(identifiant, experience, groupes=("temoin", "traite"), parts=None)` : répartition au hasard mais stable
  (le même identifiant reste dans le même groupe pour la même expérience, jour après jour).

Les dépenses passent par les mêmes règles et les mêmes coûts que tes décisions. Un programme ne peut ni acheter un
dispositif ni engager un sous-agent : cela se décide pendant une session.

## Règles

Limites techniques (protections, jamais des règles du jeu ; tout dépassement est signalé dans le rapport) : 20 000 actions
par exécution, 5 000 décisions partielles par jour pour l'ensemble des programmes, 20 000 prix individuels et 20 coupons
aux conditions différentes par décision (un même coupon peut viser autant de membres que voulu : les bons identiques
émis par `ctx.emettre_coupon` sont regroupés), 5 000 lignes par table publiée, 50 notes par exécution.

Python simplifié : pas d'`import` (`math` et `statistics` sont déjà disponibles), pas de classes, pas de noms
commençant par `_`, pas d'accès aux fichiers ni au réseau. Chaque exécution dispose d'environ 3 secondes de calcul
(plus si le magasin a acheté de la puissance de calcul). Un programme qui plante 3 fois de suite est suspendu
(l'erreur figure dans le rapport) ; tu peux le corriger et le réactiver.

Squelette :

```python
PERIODE_JOURS = 1

def chaque_matin(ctx):
    ctx.noter(f"{ctx.jour_semaine} : trésorerie {ctx.tresorerie:.0f} €")
```
"""
