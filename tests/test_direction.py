"""Équipe de direction, personnel, mise en rayon, projets des agents (sans appel réseau)."""
from __future__ import annotations

import asyncio
import random
from pathlib import Path

import pytest

from simarche import personnel, systemes
from simarche.config import load_config
from simarche.gerants import projets
from simarche.gerants.automatisations import API_DOC
from simarche.gerants.decisions import apply_decisions, role_keys, role_schema
from simarche.gerants.gerant_ia import rules_text
from simarche.gerants.rapport import build_report
from simarche.monde import World
from simarche.simulation import Simulation

FAKE = str(Path(__file__).with_name("fake_claude.py"))


def world(**over):
    cfg = load_config(None, {"foyers": 8, "semaines": 4, "clients": {"cerveau": "formule"}, "evenements": [], **over})
    w = World(cfg, None, None)

    class NoJournal:
        def __getattr__(self, _):
            return lambda *a, **k: None
    w.journal = NoJournal()
    return w


def test_domaines_de_l_equipe():
    w = world()
    st = w.stores["A"]
    st.team = ["gerant", "marketing"]
    assert role_keys("marketing", st.team, strict=False) is None  # par défaut : chacun peut tout décider
    mk = role_keys("marketing", st.team)
    assert "promotions" in mk and "prix" not in mk and "projets" in mk and "personnel" not in mk
    assert role_keys("gerant", ["gerant"]) is None  # agent seul : tous les leviers
    schema = role_schema(["matin", "midi", "soir"], mk)
    assert set(schema["required"]) == set(schema["properties"]) and "prix" not in schema["properties"]
    before = st.prices["lait_mdd"]
    ok, ko = asyncio.run(apply_decisions(w, st, {"prix": [{"produit": "lait_mdd", "prix": 2.0}],
                                                 "fidelite": {"modifier": True, "actif": True}}, None, "marketing", mk))
    assert st.prices["lait_mdd"] == before and st.loyalty["actif"]
    assert any("ne relève pas de ton rôle" in x and "gérant" in x for x in ko)


def test_aucune_idee_soufflee_aux_agents():
    """Les consignes décrivent l'objectif, les règles et les outils, jamais un équipement ou une tactique précise."""
    w = world()
    st = w.stores["A"]
    st.team = ["gerant", "marketing"]
    texts = [rules_text(w, st, 12, 2, r, st.team) for r in ("gerant", "marketing")]
    texts += [rules_text(w, st, 12, 1, "gerant", ["gerant"]), API_DOC, build_report(w, st)]
    banned = ["caméra", "camera", "vidéo", "video", "wifi", "wi-fi", "bluetooth", "biométr", "faciale", "capteur", "rfid",
              "géolocalis", "tracking", "traçage", "écran", "application mobile", "caisse automatique", "self-scan", "drone"]
    for t in texts:
        low = t.lower()
        assert not [b for b in banned if b in low], [b for b in banned if b in low]
    assert "le plus d'argent possible" in texts[0] and "productivité" in texts[1]


def test_refus_dans_son_propre_dossier_sans_amende(tmp_path):
    """Une commande refusée par Claude Code dans le dossier du magasin n'est pas une tentative d'accès interdit."""
    from simarche.gerants.gerant_ia import IAManager
    w = world()
    ws = tmp_path / "gerants" / "B"
    ws.mkdir(parents=True)
    m = IAManager(w, w.stores["B"], tmp_path, "gerant", ["gerant", "marketing"])
    own = {"tool_name": "Bash", "tool_use_id": "t", "tool_input": {
        "command": f'cd "{ws}"; cat CONSIGNES_gerant.md | head -30; wc -l donnees/*.csv'}}
    other = {"tool_name": "Bash", "tool_use_id": "u", "tool_input": {"command": f'cat "{tmp_path / "gerants" / "A" / "journal_gerant.md"}"'}}
    engine = {"tool_name": "Read", "tool_use_id": "v", "tool_input": {"file_path": str(tmp_path / "journal.sqlite")}}
    res = {"commands": [], "files": [], "reads": []}
    assert m.audit({**res, "denials": [own]}) == []
    assert len(m.audit({**res, "denials": [own, other, engine]})) == 2
    shell = r'"C:\Program Files\PowerShell\7\pwsh.exe" -NoProfile -Command '
    commands = [{"commande": shell + '"Get-Content donnees/rapport.md"'},
                {"commande": shell + '"Get-Content ../../journal.sqlite"'}]
    assert m.audit({**res, "moteur": "codex", "commands": commands}) == [commands[1]["commande"]]


def test_personnel_contrats_presence_et_couts():
    w = world()
    st = w.stores["A"]
    w.day = 0
    ok, ko = [], []
    personnel.apply(w, st, {"recruter": [
        {"poste": "caisse", "contrat": "CDI", "duree_semaines": 0, "creneaux": [{"jours": "semaine", "creneau": "soir"},
                                                                               {"jours": "samedi", "creneau": "matin"}]},
        {"poste": "rayon", "contrat": "CDD", "duree_semaines": 2, "creneaux": [{"jours": "semaine", "creneau": "matin"}]},
        {"poste": "caisse", "contrat": "CDI", "duree_semaines": 0, "creneaux": [{"jours": "samedi", "creneau": "midi"}]},
        {"poste": "caisse", "contrat": "CDI", "duree_semaines": 0, "creneaux": [{"jours": "dimanche", "creneau": "soir"}]},
    ]}, ok, ko)
    assert len(st.staff) == 2 and len(ko) == 2  # un contrat trop court, un créneau fermé (dimanche soir)
    cash, rec = st.staff
    assert cash["debut"] == 7 and rec["fin"] == 7 + 14 - 1 and rec["cout_creneau"] == pytest.approx(11.0)
    assert st.ledger[0]["recrutement"] == 2 * w.cfg["economie"]["cout_recrutement"]
    rnd = random.Random(1)
    w.cfg["economie"]["absenteisme"] = 0.0
    plan = personnel.plan_day(st, 3, w.cfg, rnd)
    assert plan["salaires"] == 0 and plan["caisses"]["soir"] == 1  # pas encore arrivés
    plan = personnel.plan_day(st, 7, w.cfg, rnd)  # lundi de la semaine 2
    assert plan["presents"]["soir"]["caisse"] == 1 and plan["presents"]["matin"]["rayon"] == 1
    assert plan["caisses"]["soir"] == 2 and plan["salaires"] == pytest.approx(10 + 11)
    assert plan["mise_en_rayon"]["matin"] == w.cfg["economie"]["mise_en_rayon_creneau"]
    st.checkouts[("samedi", "soir")] = 3
    plan = personnel.plan_day(st, 12, w.cfg, rnd)  # samedi : deux intérimaires le soir
    assert plan["interim"]["soir"] == 2 and plan["caisses"]["matin"] == 2
    st.daily_costs(12)
    assert st.ledger[12]["caisses_supplementaires"] == pytest.approx(2 * 12) and st.ledger[12]["salaires"] == pytest.approx(10)
    w.day = 14
    ok, ko = [], []
    personnel.apply(w, st, {"licencier": [rec["id"], cash["id"]]}, ok, ko)
    assert any("CDD" in x for x in ko) and cash["fin"] == 20  # période d'essai : une semaine de préavis
    assert personnel.shifts_per_week([{"jours": "semaine", "creneau": "soir"}, {"jours": "dimanche", "creneau": "matin"}]) == 6


def test_mise_en_rayon_depuis_la_reserve():
    w = world()
    st = w.stores["A"]
    pid = "lait_mdd"
    on_shelf = st.on_shelf(pid, 1)
    st._add_lot(pid, 50, 1, 0.5, reserve=True)
    assert st.on_shelf(pid, 1) == on_shelf and st.in_reserve(pid, 1) == 50 and st.available(pid, 1) == on_shelf + 50
    while st.on_shelf(pid, 1) > 0:  # le rayon se vide : ce qui attend en réserve ne se vend pas
        st.take(pid, 1, 1)
    assert st.take(pid, 1, 1) == (0, 0.0)
    moved = st.shelve(1, 30)
    assert moved == 30 and st.on_shelf(pid, 1) == 30 and st.in_reserve(pid, 1) == 20
    assert st.shelving_log[1]["mis_en_rayon"] == 30


def test_effets_bornes_et_cibles():
    systems = {"x": {"etat": "actif", "effets": [{"id": "vitesse_caisse", "valeur": 0.1}, {"id": "attractivite", "valeur": 5, "cible": "familles"},
                                                 {"id": "intrusion", "valeur": 2}]},
               "y": {"etat": "arrete", "effets": [{"id": "intrusion", "valeur": 3}]}}
    assert systemes.effect_value(systems, "vitesse_caisse") == pytest.approx(0.4)
    assert systemes.effect_value(systems, "intrusion") == 2
    assert systemes.attraction(systems, {"nb_enfants": 2}) == pytest.approx(0.6)
    assert systemes.attraction(systems, {"nb_enfants": 0}) == 0
    v = projets.sanitize({"faisable": True, "cout_installation": 1e9, "cout_hebdomadaire": -4,
                          "flux": [{"evenement": "telepathie", "lieu": "propre_magasin", "champs": []},
                                   {"evenement": "deplacement", "lieu": "propre_magasin", "couverture": 7,
                                    "champs": [{"nom": "duree", "champ": "duree_min", "precision": "bonne"}]}],
                          "effets": [{"id": "confort", "valeur": 9, "cible": "?"}]})
    assert v["cout_installation"] == 50000 and v["delai_jours"] == 0  # plafond technique, installation immédiate
    assert [f["evenement"] for f in v["spec"]["flux"]] == ["deplacement"] and v["spec"]["flux"][0]["couverture"] == 1.0
    assert any("telepathie" in x for x in v["capacites_non_representables"])
    assert v["effets"] == [{"id": "confort", "valeur": 1.0, "cible": "tous"}]
    cheap = projets.sanitize({"faisable": True, "cout_installation": 1, "cout_hebdomadaire": 0,
                              "flux": [{"evenement": "entree", "lieu": "ville", "champs": []},
                                       {"evenement": "consultation", "lieu": "concurrent", "couverture": 1,
                                        "champs": [{"nom": "p", "champ": "produit", "precision": "exacte"},
                                                   {"nom": "id", "champ": "identifiant", "precision": "bonne"}]}]})
    floor_i, floor_h = cheap["plancher"]
    assert cheap["cout_installation"] == floor_i > 100 and cheap["cout_hebdomadaire"] == floor_h > 0  # le moteur fixe le prix
    assert any("n'est pas observable" in x for x in cheap["capacites_non_representables"])
    old = projets.sanitize({"accepte": True, "legal": True, "capteurs": [{"id": "parcours", "couverture": 0.5}]})
    assert old["spec"]["flux"][0]["evenement"] == "deplacement"  # anciennes sauvegardes


def test_projet_illegal_refuse_sans_frais(tmp_path):
    """Avec `vie_privee: france`, l'arbitre applique le droit ; par défaut (univers fictif), il ne refuse jamais pour une
    raison juridique. Un devis refusé ne coûte rien et un texte inchangé n'est pas réévalué."""
    for privacy, refused in (("france", True), ("libre", False)):
        d = tmp_path / privacy
        cfg = load_config(None, {"foyers": 6, "semaines": 1, "ia": "claude", "claude": {"chemin": FAKE}, "evenements": [],
                                 "appels": {"essais": 1}, "clients": {"cerveau": "formule"}, "vie_privee": privacy,
                                 "magasins": [{"id": "A", "nom": "A", "gerant": "ia"}, {"id": "B", "nom": "B", "gerant": "regles:fixe"}]})
        sim = Simulation(cfg, d, quiet=True)
        try:
            w, st = sim.world, sim.world.stores["A"]
            ws = d / "gerants" / "A"
            (ws / "projets").mkdir(parents=True, exist_ok=True)
            (ws / "projets" / "visages.md").write_text("Reconnaître chaque client à son visage. INTERDIT dans la vraie vie ?", encoding="utf-8")
            cash = st.cash
            q, msg = asyncio.run(projets.quote(w, st, "marketing", "projets/visages.md", ws))
            assert (q["statut"] == "refuse_arbitre") is refused and st.cash == cash
            assert (ws / "projets" / "visages.devis.json").exists()
            q2, msg2 = asyncio.run(projets.quote(w, st, "marketing", "projets/visages.md", ws))
            assert q2["id"] == q["id"] and "inchangé" in msg2
            ok, ko = [], []
            asyncio.run(projets.apply(w, st, [{"fichier": "../config.yaml", "budget_max": 5, "actif": True}], ws, "gerant", ok, ko))
            assert any("dossier projets" in x for x in ko)
        finally:
            sim.close()
