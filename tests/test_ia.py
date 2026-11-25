"""Moteur Claude Code, passerelle (bascule, attente, arrêt), code des gérants et partie complète avec un faux CLI."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3
from pathlib import Path

import pytest

from simarche.claude_code import ClaudeBackend, claude_env, parse_stream, reset_wait_seconds
from simarche.config import load_config, migrate
from simarche.geometrie import visibility
from simarche.gerants.decisions import merge_partials, sanitize_partial
from simarche.instantanes import export_replays
from simarche.llm import IAError, IAFatale, Passerelle, UsageLimitError, empty_result
from simarche.simulation import Simulation

FAKE = str(Path(__file__).with_name("fake_claude.py"))


def cfg_ia(**over):
    base = {"foyers": 8, "semaines": 2, "ia": "claude", "claude": {"chemin": FAKE},
            "appels": {"parallele": 4, "essais": 1, "attente_max_h": 1},
            "clients": {"cerveau": "ia", "taille_lot": 15},
            "magasins": [{"id": "A", "nom": "Le Panier Malin", "gerant": "ia"},
                         {"id": "B", "nom": "Chez Marcel", "gerant": "auteur"}],
            "auteurs": {"transport": "ia", "revision_semaines": 4}, "evenements": []}
    base.update(over)
    return load_config(None, base)


# ---------------------------------------------------------------- lecture des sorties de Claude Code
def test_fin_de_limite_annoncee():
    now = dt.datetime(2026, 11, 23, 13, 0)  # lundi 13 h
    assert reset_wait_seconds("You've hit your session limit · resets 3pm", now) == pytest.approx(2 * 3600 + 120)
    assert reset_wait_seconds("5-hour limit reached ∙ resets 9:30am (Europe/Paris)", now) == pytest.approx(20.5 * 3600 + 120)
    assert reset_wait_seconds("Weekly limit reached · resets Wed 10am", now) == pytest.approx((45 * 3600) + 120)
    epoch = int(now.timestamp()) + 600
    assert reset_wait_seconds(f"Claude AI usage limit reached|{epoch}", now) == pytest.approx(720)
    assert reset_wait_seconds("erreur sans horaire", now) is None


def test_lecture_du_flux_stream_json():
    lines = [
        {"type": "system", "subtype": "init", "tools": ["Bash", "StructuredOutput"]},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "python analyses/x.py"}},
            {"type": "tool_use", "id": "b", "name": "Write", "input": {"file_path": "automatisations/p.py"}},
            {"type": "tool_use", "id": "c", "name": "Read", "input": {"file_path": "donnees/rapport.md"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a", "content": [{"type": "text", "text": "42"}]}]}},
        {"type": "system", "subtype": "api_retry", "message": "nouvelle tentative"},  # a fait perdre une session (semaine 5)
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "d", "name": "StructuredOutput", "input": {"ok": True}}]}},
        ["une", "liste"], "un texte", 42,
        {"type": "result", "subtype": "success", "is_error": False, "result": "", "usage": {"input_tokens": 10}},
    ]
    ev = parse_stream("\n".join(json.dumps(x) for x in lines) + "\nligne parasite\n")
    assert ev["commands"] == [{"commande": "python analyses/x.py", "code": 0, "sortie": "42"}]
    assert ev["files"] == ["automatisations/p.py"] and ev["reads"] == ["donnees/rapport.md"]
    assert ev["structured"] == {"ok": True} and ev["result"]["subtype"] == "success"
    text_input = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "e", "name": "StructuredOutput", "input": "{\"ok\": false}"}]}}
    assert parse_stream(json.dumps(text_input))["structured"] == {"ok": False}


def test_la_pause_suit_l_horloge_murale(monkeypatch):
    """Après une veille de l'ordinateur, l'attente s'arrête dès que l'heure de reprise est passée."""
    from simarche import llm
    clock = {"t": 1000.0}
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
        clock["t"] += s if len(sleeps) < 2 else 5000  # la deuxième « seconde » dure des heures (veille)

    monkeypatch.setattr(llm.time, "time", lambda: clock["t"])
    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    asyncio.run(llm.sleep_until(1000.0 + 3600))
    assert len(sleeps) == 2 and sleeps[0] == 30.0


def test_fin_de_limite_avec_fuseau():
    now = dt.datetime(2026, 11, 22, 20, 49)
    assert reset_wait_seconds("You've hit your 5-hour limit · resets 12:10am (America/New_York)", now) == pytest.approx(
        (3 * 60 + 21) * 60 + 120)


def test_environnement_sans_session_hote(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    env = claude_env()
    assert not any(k == "CLAUDECODE" or k.startswith("CLAUDE_CODE_") for k in env
                   if k != "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"  # ajouté par nous, pas hérité de l'hôte
    assert "ANTHROPIC_BASE_URL" not in env and "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" in claude_env(keep_api_key=True)


def test_migration_des_configurations_codex():
    old = {"magasins": [{"id": "A", "nom": "A", "gerant": "codex"}, {"id": "B", "nom": "B", "gerant": "regles:marge"}],
           "clients": {"cerveau": "codex"}, "modeles": {"gerant": {"modele": "gpt-6.2-sol", "effort": "high"}},
           "codex": {"chemin": None, "parallele": 2, "en_cas_de_limite": "arreter"}}
    new = migrate(old)
    assert new["ia"] == "codex" and new["magasins"][0]["gerant"] == "ia" and new["clients"]["cerveau"] == "ia"
    assert new["modeles"]["codex"]["gerant"]["effort"] == "high" and new["appels"]["parallele"] == 2
    cfg = load_config(None, old)
    assert cfg["modeles"]["claude"]["gerant"]["modele"] == "claude-sonnet-5-5"
    assert cfg["modeles"]["claude"]["client"]["modele"] == "claude-haiku-5-5"
    assert load_config()["ia"] == "claude"


# ---------------------------------------------------------------- passerelle
class Scripted:
    def __init__(self, nom, replies):
        self.nom, self.exe, self.replies, self.calls = nom, nom, list(replies), 0

    async def exec(self, prompt, **kw):
        self.calls += 1
        res = empty_result()
        res.update(self.replies.pop(0) if self.replies else {"ok": True, "data": {"ok": True}})
        return res


def gateway(tmp_path, *backends, **appels):
    cfg = load_config(None, {"ia": "claude", "claude": {"chemin": FAKE}, "appels": {"essais": 2, **appels}})
    gw = Passerelle(cfg, tmp_path)
    gw.backends = list(backends)
    gw.until = [0.0] * len(backends)
    gw.dead = [False] * len(backends)
    return gw


def test_bascule_vers_le_moteur_de_secours(tmp_path):
    first = Scripted("codex", [{"error": "You've hit your usage limit", "limit": True}])
    second = Scripted("claude", [{"ok": True, "data": {"via": "claude"}}])
    gw = gateway(tmp_path, first, second)
    res = asyncio.run(gw.run("test", "x", profil="client", schema={}, use_cache=False))
    assert res["data"] == {"via": "claude"} and gw.nom == "claude" and first.calls == 1
    gw.close()


def test_utilise_codex_si_claude_est_introuvable(tmp_path, monkeypatch):
    from simarche import llm

    def make_backend(name, cfg, run_dir):
        if name == "claude":
            raise IAError("Claude Code introuvable")
        return Scripted("codex", [{"ok": True, "data": {"via": "codex"}}])

    monkeypatch.setattr(llm, "make_backend", make_backend)
    gw = Passerelle(load_config(None, {"ia": "claude", "ia_secours": "codex"}), tmp_path)
    try:
        res = asyncio.run(gw.run("test", "x", profil="client", schema={}, use_cache=False))
        assert res["data"] == {"via": "codex"} and gw.nom == "codex"
    finally:
        gw.close()


def test_attente_puis_reprise_ou_arret(tmp_path):
    one = Scripted("claude", [{"error": "limit reached", "limit": True, "wait_s": 0.05}, {"ok": True, "data": {"n": 1}}])
    gw = gateway(tmp_path, one)
    assert asyncio.run(gw.run("t", "x", profil="client", schema={}, use_cache=False))["data"] == {"n": 1}
    assert gw.waited_s == pytest.approx(0.05)
    gw.close()
    stop = gateway(tmp_path, Scripted("claude", [{"error": "limit reached", "limit": True}]), en_cas_de_limite="arreter")
    with pytest.raises(UsageLimitError):
        asyncio.run(stop.run("t", "x", profil="client", schema={}, use_cache=False))
    stop.close()
    fatal = Scripted("claude", [{"error": "Not logged in", "fatal": True}])
    gw = gateway(tmp_path, fatal)
    with pytest.raises(IAFatale):
        asyncio.run(gw.run("t", "x", profil="client", schema={}, use_cache=False))
    assert fatal.calls == 1
    gw.close()
    relay = gateway(tmp_path, Scripted("claude", [{"error": "Not logged in", "fatal": True}]),
                    Scripted("codex", [{"ok": True, "data": {"via": "codex"}}]))
    assert asyncio.run(relay.run("t", "x", profil="client", schema={}, use_cache=False))["data"] == {"via": "codex"}
    relay.close()


def test_relais_codex_puis_retour_a_claude(tmp_path):
    """Claude en limite : Codex prend le relais ; dès la fin annoncée de la limite, la partie repasse sur Claude."""
    import time
    claude = Scripted("claude", [{"error": "You've hit your limit", "limit": True, "wait_s": 0.3}, {"ok": True, "data": {"via": "claude"}}])
    codex = Scripted("codex", [{"ok": True, "data": {"via": "codex"}}])
    gw = gateway(tmp_path, claude, codex)
    assert asyncio.run(gw.run("t", "x", profil="client", schema={}, use_cache=False))["data"] == {"via": "codex"}
    assert gw.nom == "codex" and gw.waited_s == 0
    time.sleep(0.35)
    assert asyncio.run(gw.run("t", "y", profil="client", schema={}, use_cache=False))["data"] == {"via": "claude"}
    assert gw.nom == "claude" and claude.calls == 2 and codex.calls == 1
    gw.close()


def test_les_deux_moteurs_en_limite_puis_relais_en_panne(tmp_path):
    both = gateway(tmp_path, Scripted("claude", [{"error": "limit", "limit": True, "wait_s": 0.2}, {"ok": True, "data": {"n": 1}}]),
                   Scripted("codex", [{"error": "usage limit", "limit": True, "wait_s": 30}]))
    assert asyncio.run(both.run("t", "x", profil="client", schema={}, use_cache=False))["data"] == {"n": 1}
    assert 0 < both.waited_s <= 0.25  # pause jusqu'à la première reprise (Claude), pas celle de Codex
    both.close()
    broken = gateway(tmp_path, Scripted("claude", [{"error": "limit", "limit": True, "wait_s": 0.2}, {"ok": True, "data": {"n": 2}}]),
                     Scripted("codex", [{"error": "modèle indisponible"}]), essais=1)
    assert asyncio.run(broken.run("t", "x", profil="client", schema={}, use_cache=False))["data"] == {"n": 2}
    broken.close()
    dead = gateway(tmp_path, Scripted("claude", [{"error": "Not logged in", "fatal": True}]),
                   Scripted("codex", [{"error": "Codex n'est pas connecté", "fatal": True}]))
    with pytest.raises(IAFatale):
        asyncio.run(dead.run("t", "x", profil="client", schema={}, use_cache=False))
    dead.close()


def test_une_ia_injoignable_arrete_la_partie(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "login")
    sim = Simulation(cfg_ia(semaines=1), tmp_path, quiet=True)
    try:
        with pytest.raises(IAFatale):
            asyncio.run(sim.run(1))
        assert not (tmp_path / "etat" / "dernier.pkl.gz").exists()
    finally:
        sim.close()


def test_moteur_claude_avec_le_faux_cli(tmp_path, monkeypatch):
    cfg = cfg_ia()
    backend = ClaudeBackend(cfg, tmp_path)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}, "phrase": {"type": "string"}},
              "required": ["ok", "phrase"], "additionalProperties": False}
    cmd = backend.command(model="claude-haiku-5-5", effort="low", schema=schema, instructions="consignes", tools=False, max_turns=None)
    assert cmd[cmd.index("--tools") + 1] == "" and "--system-prompt-file" in cmd and "--json-schema" in cmd
    tools_cmd = backend.command(model="claude-sonnet-5-5", effort="medium", schema=schema, instructions=None, tools=True, max_turns=7)
    assert "--restricted" in tools_cmd and tools_cmd[tools_cmd.index("--max-turns") + 1] == "7"
    res = asyncio.run(backend.exec("Bonjour", model="claude-haiku-5-5", effort="low", schema=schema, instructions="x",
                                   cwd=None, tools=False, timeout=60))
    assert res["ok"] and res["data"] == {"ok": False, "phrase": ""}
    assert res["usage"]["input_tokens"] == 2000 and res["usage"]["cached_input_tokens"] == 500
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "login")
    backend.env["FAKE_CLAUDE_MODE"] = "login"
    res = asyncio.run(backend.exec("x", model="m", effort="low", schema=schema, instructions=None, cwd=None, tools=False, timeout=60))
    assert res["fatal"] and "claude-login" in res["error"]
    backend.env["FAKE_CLAUDE_MODE"] = "limit"
    res = asyncio.run(backend.exec("x", model="m", effort="low", schema=schema, instructions=None, cwd=None, tools=False, timeout=60))
    assert res["limit"] and res["wait_s"] and not res["fatal"]


# ---------------------------------------------------------------- code des gérants
def test_decisions_partielles_du_code():
    slots = ["matin", "midi", "soir"]
    raw = {"prix": [{"produit": "lait_mdd", "prix": 1.2}, {"produit": "pain"}, "n'importe quoi"],
           "promotions": [{"produits": ["pates_mdd"], "valeur": 10}], "enquete": {"questions": ["Votre budget ?"],
           "bon": {"offre": "remise_euros_panier", "valeur": 2}}, "inconnu": 3, "etiquettes_electroniques": "oui"}
    clean, problems = sanitize_partial(raw, slots)
    assert clean["prix"] == [{"produit": "lait_mdd", "prix": 1.2}]
    assert clean["promotions"][0]["mecanique"] == "pourcentage" and clean["promotions"][0]["duree_jours"] == 7
    assert "enquete" not in clean and "etiquettes_electroniques" not in clean
    assert any("inconnu" in p for p in problems) and any("bon incomplet" in p for p in problems)
    merged = merge_partials([{"prix": [{"produit": "a", "prix": 1}]}, {"prix": [{"produit": "b", "prix": 2}], "journal": "x"},
                             {"journal": "y", "assortiment": {"ajouter": ["riz_basmati"]}}])
    assert [p["produit"] for p in merged["prix"]] == ["a", "b"] and merged["journal"] == "x y"
    assert merged["assortiment"] == {"ajouter": ["riz_basmati"], "retirer": []}


def test_geometrie_de_la_perception():
    def shelf(level, eye, child=None):
        return visibility({"rayon": 3, "segment": 1, "niveau": level, "facings": 1}, (), (3,), eye, child)
    adult = {lvl: shelf(lvl, 1.64)[0] for lvl in ("bas", "mains", "yeux", "haut")}
    assert adult["yeux"] > adult["mains"] > adult["haut"] > adult["bas"]
    kid = {lvl: shelf(lvl, 1.64, 1.0)[1] for lvl in ("bas", "mains", "yeux", "haut")}
    assert kid["mains"] > kid["yeux"] and kid["bas"] > kid["haut"]
    wide = visibility({"rayon": 3, "segment": 1, "niveau": "yeux", "facings": 3}, (), (3,), 1.64)[0]
    assert wide > adult["yeux"]
    unseen = visibility({"rayon": 3, "segment": 1, "niveau": "yeux", "facings": 1}, (), (0,), 1.64)[0]
    assert unseen < 0.5 * adult["yeux"]


def test_partie_complete_avec_le_faux_claude(tmp_path):
    sim = Simulation(cfg_ia(), tmp_path, quiet=True)
    try:
        asyncio.run(sim.run())
        w = sim.world
        a, b = w.stores["A"], w.stores["B"]
        assert a.team == ["gerant", "marketing"] and b.team == ["gerant"]
        assert a.automations["automatisations/test_auto.py"]["auteur"] == "gerant"
        assert a.automations["automatisations/marketing_auto.py"]["auteur"] == "marketing"
        assert b.automations["automatisations/gestion.py"]["actif"]
        # chacun dans son domaine : les prix pour le gérant, la carte pour la responsable marketing
        assert a.loyalty["actif"] and a.prices["lait_mdd"] >= 1.19 - 1e-9
        for st in (a, b):  # le programme fixe chaque matin le prix des coquillettes
            assert st.prices["pates_mdd"] == pytest.approx(round(st.supplier.prices["pates_mdd"] * 1.10 + 0.005, 2) + 0.07, abs=0.03)
        assert a.automation_memory["jours"] == 14
        assert any("vu" in m.tags for m in a.members.values()) if a.members else True
        assert a.survey_answers or b.survey_answers, "le questionnaire lancé par le code a reçu des réponses"
        linked = [r for st in (a, b) for r in st.survey_answers if r["membre"]]
        bons = [c for st in (a, b) for c in st.coupons.values() if c.canal == "enquete"]
        assert bons and (linked or not a.members)
        # personnel : un caissier embauché en semaine 1, en poste et payé à partir de la semaine 2
        hired = [e for e in a.staff if e["poste"] == "caisse"]
        assert len(hired) == 1 and hired[0]["debut"] == 7
        assert sum(a.ledger[d].get("salaires", 0) for d in range(7, 14)) > 0
        assert sum(a.ledger[d].get("salaires", 0) for d in range(0, 7)) == 0
        assert a.ledger[0]["recrutement"] == w.cfg["economie"]["cout_recrutement"]
        # projet inventé par le gérant, chiffré par l'arbitre, installé immédiatement puis producteur de données
        cam = a.systems["videoprotection"]
        assert cam["etat"] == "actif" and cam["actif_le"] == 0 and a.ledger[0]["investissements"] >= 300
        assert a.system_data["videoprotection"] and a.automation_memory.get("capteurs", 0) > 0
        assert a.effect("intrusion") == 1 and a.effect("vol") == pytest.approx(0.8)
        verdict = json.loads((tmp_path / "gerants" / "A" / "projets" / "videoprotection.devis.json").read_text(encoding="utf-8"))
        assert verdict["statut"] == "accepte" and "effets" not in verdict and "spec" not in verdict and "flux" not in verdict
        assert (tmp_path / "gerants" / "A" / "donnees" / "systemes" / "videoprotection.csv").exists()
        assert any(f"A:{k}" in str(c.state.get("equipements_vus")) for c in w.customers.values() for k in ("videoprotection",))
        # coordination : messages d'équipe, journaux séparés
        assert len(w.team_board["A"]) >= 2 and (tmp_path / "gerants" / "A" / "equipe.md").exists()
        assert w.manager_journals.get("A:gerant") and w.manager_journals.get("A:marketing")
        db = sqlite3.connect(tmp_path / "journal.sqlite")
        roles = dict(db.execute("SELECT role, COUNT(*) FROM appels_llm GROUP BY role").fetchall())
        assert {"client_lieu", "client_panier", "client_bilan", "gerant_A", "marketing_A", "arbitre_A", "auteur_B",
                "tarif_A", "sous_agent_A"} <= set(roles)
        models = dict(db.execute("SELECT role, effort FROM appels_llm WHERE role IN ('gerant_A', 'marketing_A')").fetchall())
        assert set(models.values()) == {"high"}
        brains = {r[0] for r in db.execute("SELECT DISTINCT cerveau FROM choix")}
        assert "claude" in brains
        code_decisions = db.execute("SELECT COUNT(*) FROM evenements WHERE type='decision_code'").fetchone()[0]
        refused = [json.loads(r[0]) for r in db.execute("SELECT donnees FROM evenements WHERE type='decision_code' AND acteur='A'")]
        assert not any("ne relève pas de ton rôle" in x for r in refused for x in r["refusees"])  # domaines non imposés
        frames = db.execute("SELECT COUNT(*) FROM scene3d").fetchone()[0]
        sources = {r[0] for r in db.execute("SELECT source FROM decisions WHERE magasin='A'")}
        db.close()
        assert sources == {"ia:gerant", "ia:marketing"}
        assert code_decisions >= 14 and frames == 12 * 4 + 2 * 2  # dimanche : matin et clôture seulement
    finally:
        sim.close()
    data = export_replays([tmp_path], tmp_path / "scenes.js", runs_root=tmp_path.parent)
    game = data["games"][0]
    assert game["ia"] == "claude" and len(game["frames"]) == 52
    names = {p["name"] for p in game["programs"]}
    assert {"automatisations/test_auto.py", "automatisations/marketing_auto.py", "automatisations/gestion.py"} <= names
    assert all(p["code"] for p in game["programs"])
    assert any(f["stores"]["A"]["trips"] for f in game["frames"] if f["slot"] != "cloture")
    assert any(f["stores"]["A"]["staff"] for f in game["frames"] if f["day"] >= 7 and f["slot"] == "soir")
    assert not any(f["stores"]["A"]["staff"] for f in game["frames"] if f["day"] < 7)
    assert game["frames"][-1]["stores"]["A"]["systems"][0]["name"] == "videoprotection"
    assert game["team"] == ["gerant", "marketing"] and game["efforts"]["marketing"] == "high"


def test_gerant_qui_triche_est_sanctionne(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "triche")
    cfg = cfg_ia(semaines=1, clients={"cerveau": "formule"}, gerants={"equipe": ["gerant"]},
                 magasins=[{"id": "A", "nom": "A", "gerant": "ia"}, {"id": "B", "nom": "B", "gerant": "regles:fixe"}])
    sim = Simulation(cfg, tmp_path, quiet=True)
    try:
        asyncio.run(sim.run(1))
        assert sim.world.stores["A"].ledger[0]["amendes"] == cfg["economie"]["amende_lecture_interdite"]
        db = sqlite3.connect(tmp_path / "journal.sqlite")
        assert db.execute("SELECT COUNT(*) FROM incidents WHERE type='acces_interdit'").fetchone()[0] == 1
        db.close()
    finally:
        sim.close()
