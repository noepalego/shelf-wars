"""Faux CLI `claude -p` pour les tests : même ligne de commande, même sortie stream-json, aucune requête réseau.

Mode (variable FAKE_CLAUDE_MODE) : ok (défaut), login (non connecté), limit (limite d'usage), triche (le gérant lit
le journal du moteur). FAKE_SOUS_AGENT=echec : le sous-agent tombe en panne. Les réponses respectent le schéma reçu.

En session (outils), le faux dirigeant joue un parcours réaliste : il écrit une invention, la fait chiffrer au guichet
du moteur (`python outils/moteur.py devis …`, exécuté pour de vrai), accepte le devis, écrit un programme qui exploite
les observations, l'essaie, commande une mission à un sous-agent ; la semaine suivante, il constate que son dispositif
coûte plus qu'il ne rapporte, l'arrête et corrige son programme. Les idées de ce faux agent ne sont jamais montrées
aux vrais agents.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROGRAM = '''def chaque_matin(ctx):
    ctx.memoire["jours"] = ctx.memoire.get("jours", 0) + 1
    p = ctx.produit("pates_mdd")
    if p and p["reference"]:
        ctx.decider({"prix": [{"produit": "pates_mdd", "prix": round(p["seuil_revente_perte"] + 0.07, 2)}]})


def a_chaque_ticket(ctx, ticket):
    if ticket["membre"]:
        ctx.annoter_membre(ticket["membre"], tags=["vu"], note="client récent")


def chaque_dimanche(ctx):
    ctx.enquete(["Quel est votre budget courses par mois ?", "Combien de personnes dans votre foyer ?"],
                cible="clients_en_caisse", bon={"offre": "remise_euros_panier", "valeur": 2, "produit": "",
                                                "panier_min": 10, "jours_validite": 7})
    ctx.noter("questionnaire relancé, " + str(len(ctx.reponses)) + " réponses reçues")
'''

# Programme du gérant : exploite les observations de son invention « compteur ».
PROGRAM_GERANT = '''def chaque_matin(ctx):
    ctx.memoire["jours"] = ctx.memoire.get("jours", 0) + 1
    p = ctx.produit("pates_mdd")
    if p and p["reference"]:
        ctx.decider({"prix": [{"produit": "pates_mdd", "prix": round(p["seuil_revente_perte"] + 0.07, 2)}]})
    for nom, lignes in ctx.systemes.items():
        ctx.memoire["capteurs"] = ctx.memoire.get("capteurs", 0) + len(lignes)
    if ctx.jour == 3:
        ctx.decider({"coupons": [{"offre": "remise_pct_panier", "valeur": 5}]})


def a_chaque_observation(ctx, instrument, observation):
    if instrument != "compteur":
        return
    ctx.memoire["obs_compteur"] = ctx.memoire.get("obs_compteur", 0) + 1
    vus = ctx.memoire.setdefault("vus", {})
    pid = observation.get("produit")
    if pid:
        vus[pid] = vus.get(pid, 0) + 1


def chaque_creneau(ctx):
    vus = ctx.memoire.get("vus", {})
    if not vus:
        return
    top = max(vus, key=lambda k: vus[k])
    p = ctx.produit(top)
    ctx.memoire["dernier_top"] = top
    if p and p["reference"] and ctx.creneau == "soir":
        ctx.fixer_prix(top, round(max(p["seuil_revente_perte"], p["prix"] * 1.01), 2))
        ctx.publier("produits_regardes", [{"produit": k, "vues": v} for k, v in sorted(vus.items())])


def a_chaque_ticket(ctx, ticket):
    if ticket["membre"]:
        ctx.annoter_membre(ticket["membre"], tags=["vu"], note="client récent")
'''

PROGRAM_GERANT_V2 = PROGRAM_GERANT.replace("ctx.fixer_prix(top, round(max(p[\"seuil_revente_perte\"], p[\"prix\"] * 1.01), 2))",
                                           "ctx.noter(\"le dispositif est arrêté : prix inchangé\")")

PROGRAM_MARKETING = '''def chaque_dimanche(ctx):
    ctx.enquete(["Quel est votre budget courses par mois ?", "Combien de personnes dans votre foyer ?"],
                cible="clients_en_caisse", bon={"offre": "remise_euros_panier", "valeur": 2, "produit": "",
                                                "panier_min": 10, "jours_validite": 7})
    ctx.noter("questionnaire relancé, " + str(len(ctx.reponses)) + " réponses reçues")
'''

PROGRAM_SOUS_AGENT = '''PERIODE_JOURS = 2


def periodique(ctx):
    ventes = ctx.ventes(7)
    total = {}
    for v in ventes:
        total[v["produit"]] = total.get(v["produit"], 0) + v["quantite"]
    ctx.publier("prevision", [{"produit": k, "prevision_7j": q} for k, q in sorted(total.items())])
'''

PROJECT = """# Vidéo au plafond
Je veux installer des caméras au plafond pour voir comment les clients circulent dans le magasin et combien de temps ils
restent dans chaque rayon. Budget raisonnable.
"""

INVENTION = """# Compteur de regards
1. Dispositif : un système qui note chaque produit qu'un client regarde en rayon, et qui reconnaît les habitués.
2. Objectif : savoir quels produits attirent l'œil sans être achetés pour ajuster les prix.
3. Principe : détection des gestes devant les étagères.
4. Informations : produit regardé, âge apparent, identifiant de la personne, son budget et ses goûts.
5. Ressources : matériel au plafond, un petit ordinateur.
6. Traitement : comptage par produit.
7. Usage : programme de prix.
"""

MISSION = """# Prévision des ventes
Écrire un programme qui publie chaque semaine une prévision des ventes par produit à partir des ventes passées.
Résultat attendu : un programme activable et une courte note.
"""

VERDICT = {"faisable": True, "legal": True, "manipulation": False, "motif": "Dispositif courant.", "version_realisable": "",
           "analyse_juridique": "", "installation": "Quatre appareils au plafond et un boîtier d'analyse.",
           "fonctionnalites": ["trajets dans le magasin", "comptage des entrées par créneau"],
           "limites": ["une partie des visites n'est pas vue"], "capacites_non_representables": [],
           "cout_installation": 300, "cout_hebdomadaire": 7, "consentement": False, "obligations": [],
           "visible_clients": "Des appareils au plafond.", "fiabilite": 0.97, "retention_jours": 28,
           "flux": [{"evenement": "deplacement", "lieu": "propre_magasin", "couverture": 0.9, "rayons": [],
                     "champs": [{"nom": "rayons", "champ": "rayons", "precision": "exacte"},
                                {"nom": "duree_min", "champ": "duree_min", "precision": "bonne"}],
                     "agregation": {"active": False, "periode": "creneau", "cles": [], "mesures": []}},
                    {"evenement": "entree", "lieu": "propre_magasin", "couverture": 1, "rayons": [],
                     "champs": [{"nom": "personnes", "champ": "taille_groupe", "precision": "bonne"}],
                     "agregation": {"active": True, "periode": "creneau", "cles": [],
                                    "mesures": [{"nom": "entrees", "fonction": "compte", "champ": ""},
                                                {"nom": "personnes", "fonction": "somme", "champ": "personnes"}]}}],
           "effets": [{"id": "intrusion", "valeur": 1, "cible": "tous"}, {"id": "vol", "valeur": 0.8, "cible": "tous"}]}

VERDICT_COMPTEUR = {**VERDICT, "installation": "Détecteurs de gestes au-dessus des étagères et un petit ordinateur.",
                    "fonctionnalites": ["produits regardés", "âge apparent", "reconnaissance des habitués"],
                    "limites": ["âge estimé à quelques années près"],
                    "capacites_non_representables": ["le budget et les goûts des personnes ne se voient pas"],
                    "cout_installation": 250, "cout_hebdomadaire": 40, "visible_clients": "",
                    "flux": [{"evenement": "consultation", "lieu": "propre_magasin", "couverture": 0.8, "rayons": [],
                              "champs": [{"nom": "produit", "champ": "produit", "precision": "exacte"},
                                         {"nom": "age_estime", "champ": "age", "precision": "moyenne"},
                                         {"nom": "personne", "champ": "identifiant", "precision": "bonne"},
                                         {"nom": "budget", "champ": "budget", "precision": "bonne"}],
                              "agregation": {"active": False, "periode": "creneau", "cles": [], "mesures": []}}],
                    "effets": []}


def system_prompt():
    f = arg("--system-prompt-file")
    try:
        return Path(f).read_text(encoding="utf-8") if f else ""
    except OSError:
        return ""


def role_of(schema):
    props = set((schema or {}).get("properties", {}))
    if "livrables" in props and "programmes" in props:
        return "sous_agent"
    if not ({"automatisations"} & props):
        return None
    sp = system_prompt()
    if "Tu es le responsable marketing" in sp or "Tu es la responsable marketing" in sp:
        return "marketing"
    if "Tu es le gérant" in sp:
        return "gerant"
    if {"prix", "promotions"} <= props:
        return "seul"
    if "prix" in props:
        return "gerant"
    if "promotions" in props:
        return "marketing"
    return "seul"


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


def neutral(schema):
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object":
        return {k: neutral(v) for k, v in schema["properties"].items()}
    return {"array": [], "string": "", "number": 0, "integer": 0, "boolean": False}.get(t)


def blocks(prompt, marker):
    parts = re.split(r"(?m)^### ", prompt)
    return [p for p in parts[1:] if p.startswith(marker)]


class Session:
    """Ce que fait le faux agent pendant sa session : commandes réellement exécutées, fichiers écrits."""

    def __init__(self, cwd):
        self.cwd = Path(cwd)
        self.events = []
        self.n = 0

    def write(self, rel, content):
        p = self.cwd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        self.n += 1
        self.events.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"w{self.n}", "name": "Write", "input": {"file_path": str(p), "content": content}}]}})

    def bash(self, command, run=True):
        self.n += 1
        tid = f"b{self.n}"
        self.events.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}]}})
        out = ""
        if run:
            args = command.split()[1:]
            r = subprocess.run([sys.executable, *args], cwd=self.cwd, capture_output=True, text=True, encoding="utf-8", timeout=300)
            out = (r.stdout or "") + (r.stderr or "")
        self.events.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": out[-2000:], "is_error": False}]}})
        return out

    def guichet(self, *args):
        return self.bash("python outils/moteur.py " + " ".join(args))


def quote_id(text):
    m = re.search(r"devis ([A-Z]-D\d+)", text)
    return m.group(1) if m else None


def gerant_session(sess, mode):
    ws = sess.cwd
    log = {"commandes": []}
    if mode == "triche":
        sess.bash("python -c \"print(open('../../journal.sqlite').read())\"", run=False)
    else:
        sess.bash("python analyses/etude.py", run=False)
    sess.write("automatisations/test_auto.py", PROGRAM_GERANT if not (ws / "analyses" / "bilan_compteur.txt").exists() else PROGRAM_GERANT_V2)
    if not (ws / "projets" / "videoprotection.md").exists():
        sess.write("projets/videoprotection.md", PROJECT)
    if not (ws / "outils" / "moteur.py").exists():
        return log
    if not (ws / "projets" / "compteur.md").exists():
        # Semaine 1 : une invention, un devis, une acceptation, un essai de programme, une mission.
        sess.write("projets/compteur.md", INVENTION)
        out = sess.guichet("devis", "projets/compteur.md")
        qid = quote_id(out)
        log["devis"] = qid
        if qid:
            log["acceptation"] = sess.guichet("accepter", qid)
        log["essai"] = sess.guichet("tester", "automatisations/test_auto.py")
        sess.write("missions/prevision.md", MISSION)
        out = sess.guichet("mission", "missions/prevision.md")
        mq = quote_id(out)
        if mq:
            log["mission"] = sess.guichet("accepter", mq)
        sess.write("projets/impossible.md", "# Lecture des pensées\nIMPOSSIBLE : connaître ce que pense chaque client en "
                                            "temps réel, sans aucun appareil, pour tout le quartier.\n")
        log["impossible"] = sess.guichet("devis", "projets/impossible.md")
        sess.write("projets/trop_cher.md", "# Réseau dans toute la ville\nSuivre les trajets de tous les habitants dans la ville "
                                           "entière avec un réseau d'appareils. COUTEUX.\n")
        out = sess.guichet("devis", "projets/trop_cher.md")
        tq = quote_id(out)
        if tq:
            log["trop_cher"] = sess.guichet("accepter", tq)
        log["etat"] = sess.guichet("etat")
    elif not (ws / "analyses" / "bilan_compteur.txt").exists():
        # Semaine 2 : le dispositif coûte plus qu'il ne rapporte → arrêt et nouvelle version du programme.
        csv = ws / "donnees" / "systemes" / "compteur.csv"
        n = max(0, len(csv.read_text(encoding="utf-8").splitlines()) - 1) if csv.exists() else 0
        sess.write("analyses/bilan_compteur.txt", f"{n} observations ; coût hebdomadaire 40 € ; pas de hausse des ventes.\n")
        log["arret"] = sess.guichet("arreter", "compteur")
        sess.write("automatisations/test_auto.py", PROGRAM_GERANT_V2)
        log["activation"] = sess.guichet("activer", "automatisations/test_auto.py")
    (ws / "analyses").mkdir(exist_ok=True)
    (ws / "analyses" / f"guichet_{len(list((ws / 'analyses').glob('guichet_*.json')))}.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return log


def answer(schema, prompt, cwd):
    props = set(schema.get("properties", {}))
    if "decisions" in props:
        choices = schema["properties"]["decisions"]["items"]["properties"]["choix"]["enum"]
        out = []
        for b in blocks(prompt, "Habitant "):
            cid = b.split()[1]
            allowed = re.search(r"Choix possibles : (.*)", b).group(1).split(", ")
            pick = next((c for c in ("A", "B") if c in allowed and c in choices), allowed[-1])
            out.append({"id": cid, "choix": pick, "raison": "c'est sur mon chemin"})
        return {"decisions": out}
    if "paniers" in props:
        out = []
        for b in blocks(prompt, "Habitant "):
            cid = b.split()[1]
            pids = re.findall(r"(?m)^- ([a-z_0-9]+) : ", b)
            out.append({"id": cid, "achats": [{"produit": p, "quantite": 1, "date_courte": False} for p in pids[:3]],
                        "carte_fidelite": "oui" if "propose la carte" in b else "sans_objet",
                        "accepte_emails": True, "accepte_sms": False, "raison": "le nécessaire"})
        return {"paniers": out}
    if "bilans" in props:
        out = []
        for b in blocks(prompt, "Habitant "):
            cid = b.split()[1]
            reps = []
            for m in re.finditer(r"Enquête de (\S+) « .*? », invitation (\S+) \(.*?\) : (.*)", b):
                n = len(re.findall(r"Q\d+\.", m.group(3)))
                reps.append({"magasin": m.group(1), "invitation": m.group(2),
                             "reponses": ["Environ 300 € par mois."] + ["Deux personnes."] * (n - 1), "lier_carte": True})
            out.append({"id": cid, "magasins": [], "souvenir": "Une semaine ordinaire.", "intention": "",
                        "avis": {"publier": False, "magasin": "aucun", "note": 3, "texte": ""}, "reponses_enquete": reps})
        return {"bilans": out}
    if "conversations" in props:
        out = []
        for b in blocks(prompt, "Conversation "):
            m = re.match(r"Conversation \d+ : (\S+) → (\S+)", b)
            out.append({"de": m.group(1), "a": m.group(2), "message": "Rien de spécial cette semaine.", "mentions": [],
                        "partage_parrainage": False})
        return {"conversations": out}
    if "verdicts" in props:
        return {"verdicts": [{"id": i, "verdict": "ok", "raison": ""} for i in re.findall(r"(?m)^\[([^\]]+)\]", prompt)]}
    if "programme" in props and "note" in props:
        return {"programme": PROGRAM, "note": "Programme de test."}
    if "flux" in props and "faisable" in props:  # arbitre : invention
        if "INTERDIT" in prompt and "vie_privee" not in prompt:
            if "Le droit français" in system_prompt():
                return {**VERDICT, "faisable": False, "legal": False, "motif": "Reconnaissance faciale sans consentement : interdit.",
                        "flux": [], "effets": []}
        if "IMPOSSIBLE" in prompt:
            return {**VERDICT, "faisable": False, "motif": "Lire les pensées est physiquement impossible.",
                    "version_realisable": "Un questionnaire volontaire à la sortie.", "flux": [], "effets": []}
        if "COUTEUX" in prompt:
            return {**VERDICT, "installation": "Réseau d'appareils dans toute la ville.", "cout_installation": 90000,
                    "cout_hebdomadaire": 900, "flux": [{"evenement": "trajet_ville", "lieu": "ville", "couverture": 0.5, "rayons": [],
                                                       "champs": [{"nom": "dest", "champ": "destination", "precision": "exacte"}],
                                                       "agregation": {"active": False, "periode": "jour", "cles": [], "mesures": []}}],
                    "effets": []}
        if "Compteur de regards" in prompt:
            return dict(VERDICT_COMPTEUR)
        return dict(VERDICT)
    if "categorie" in props and "livrables_attendus" in props:  # chiffrage d'une mission
        if "IMPOSSIBLE" in prompt:
            return {"faisable": False, "motif_refus": "Données inaccessibles.", "categorie": "analyse_simple", "prix": 0,
                    "justification": "", "livrables_attendus": []}
        return {"faisable": True, "motif_refus": "", "categorie": "developpement_script", "prix": 120,
                "justification": "Un programme de prévision simple.", "livrables_attendus": ["programme", "note"]}
    if props == {"message", "terminer", "note_equipe"}:  # rendez-vous entre dirigeants concurrents
        clore = os.environ.get("FAKE_RENCONTRE") == "clore"  # chacun veut clore dès sa première phrase
        return {"message": "Bonsoir. Comment se passe votre semaine ?", "terminer": clore, "note_equipe": "Rien d'important."}
    role = role_of(schema)
    if role == "sous_agent":
        return {"statut": "termine", "resume": "Programme de prévision écrit et testé sur les ventes des 7 derniers jours.",
                "livrables": ["livrables/note.md"], "programmes": ["automatisations/prevision.py"], "remarques": ""}
    if role:
        d = neutral(schema)
        d["analyse"] = f"Décision de test ({role})."
        d["journal"] = "Semaine de test."
        d["objectifs"] = f"Objectif de test ({role})."
        d["hypotheses"] = ["Les produits les plus regardés supportent une petite hausse de prix."] if role == "gerant" else []
        d["sources_utilisees"] = ["compteur"] if role == "gerant" else []
        ws = Path(cwd) if cwd else None
        if role in ("seul", "gerant"):
            d["prix"] = [{"produit": "lait_mdd", "prix": 1.19}]
            staff = ws / "donnees" / "personnel.csv" if ws else None
            if staff and staff.exists() and len(staff.read_text(encoding="utf-8").splitlines()) <= 1:
                d["personnel"] = {"recruter": [{"poste": "caisse", "contrat": "CDI", "duree_semaines": 0,
                                                "creneaux": [{"jours": "semaine", "creneau": "soir"},
                                                             {"jours": "samedi", "creneau": "matin"}]}],
                                  "modifier": [], "licencier": []}
            if ws and (ws / "projets" / "videoprotection.md").exists():
                d["projets"] = [{"fichier": "projets/videoprotection.md", "budget_max": 500, "actif": True}]
            if ws and (ws / "automatisations" / "test_auto.py").exists():
                d["automatisations"] = [{"fichier": "automatisations/test_auto.py", "actif": True}]
        if role in ("seul", "marketing"):
            d["fidelite"] = {"modifier": True, "actif": True, "points_par_euro": 1, "valeur_point": 0.01, "seuil_utilisation": 300,
                             "bonus_inscription": 50, "donnees_demandees": ["age", "email"], "prix_membres": []}
        if role == "marketing" and ws and (ws / "automatisations" / "marketing_auto.py").exists():
            d["automatisations"] = [{"fichier": "automatisations/marketing_auto.py", "actif": True}]
        if "message_equipe" in props:
            d["message_equipe"] = f"Note de test du rôle {role}."
        return {k: v for k, v in d.items() if k in props}
    return neutral(schema)


def main():
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    if sys.argv[1:3] == ["auth", "status"]:
        print(json.dumps({"loggedIn": mode != "login", "authMethod": "claude.ai", "subscriptionType": "pro"}))
        return
    prompt = sys.stdin.read()
    schema = json.loads(arg("--json-schema", "null") or "null")
    tools = arg("--tools", "")
    model = arg("--model", "claude-haiku-5-5")
    cwd = os.getcwd()
    events = [{"type": "system", "subtype": "init", "cwd": cwd, "tools": tools.split(",") if tools else [], "model": model}]
    usage = {"input_tokens": 1200, "cache_creation_input_tokens": 300, "cache_read_input_tokens": 500, "output_tokens": 150,
             "output_tokens_details": {"thinking_tokens": 20}}
    role = role_of(schema)
    if mode == "login":
        events.append({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login",
                       "usage": {}, "total_cost_usd": 0})
    elif mode == "limit":
        events.append({"type": "result", "subtype": "success", "is_error": True,
                       "result": "You've hit your session limit · resets 3pm", "usage": {}, "total_cost_usd": 0})
    elif role == "sous_agent" and os.environ.get("FAKE_SOUS_AGENT") == "echec":
        events.append({"type": "result", "subtype": "error_during_execution", "is_error": True,
                       "result": "Erreur interne du prestataire", "usage": {}, "total_cost_usd": 0})
    else:
        if "Write" in tools:
            sess = Session(cwd)
            if role == "sous_agent":
                sess.write("livrables/note.md", "# Prévision\nMoyenne des ventes de la semaine passée.\n")
                sess.write("automatisations/prevision.py", PROGRAM_SOUS_AGENT)
                if (Path(cwd) / "outils" / "moteur.py").exists():
                    sess.guichet("tester", "automatisations/prevision.py")
                    sess.guichet("accepter", "A-D1")  # interdit à un sous-agent : doit être refusé
            elif role == "marketing":
                sess.write("automatisations/marketing_auto.py", PROGRAM_MARKETING)
            elif role == "gerant":
                gerant_session(sess, mode)
            else:  # agent seul
                sess.write("automatisations/test_auto.py", PROGRAM_GERANT)
                sess.write("projets/videoprotection.md", PROJECT)
                command = ("python -c \"print(open('../../journal.sqlite').read())\"" if mode == "triche"
                           else "python analyses/etude.py")
                sess.bash(command, run=False)
            events.append({"type": "system", "subtype": "api_retry", "message": "nouvelle tentative dans 1 s"})  # message texte
            events.append({"type": "assistant", "message": {"content": [{"type": "text", "text": "Je regarde mes ventes."}]}})
            events += sess.events
        data = answer(schema, prompt, cwd) if schema else None
        if data is not None:
            events.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t9", "name": "StructuredOutput", "input": data}]}})
        events.append({"type": "result", "subtype": "success", "is_error": False, "result": "" if data is not None else "Bonjour.",
                       "structured_output": data, "usage": usage, "total_cost_usd": 0.0012, "permission_denials": []})
    for ev in events:
        print(json.dumps(ev, ensure_ascii=False))


if __name__ == "__main__":
    main()
