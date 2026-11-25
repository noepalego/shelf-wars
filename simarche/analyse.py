"""Analyse d'une partie : rapport de fin de partie et interviews d'agents."""
from __future__ import annotations

import gzip
import json
import pickle
import sqlite3
from pathlib import Path

from . import temps
from .config import load_config
from .metriques import summary
from .population import persona_long

ANALYST = """Tu es un économiste spécialiste du commerce de détail. On te donne les faits d'une simulation : deux supérettes
dirigées par des agents d'IA se disputent une petite ville de foyers simulés. Écris un rapport clair en français (Markdown) :
1. Résumé en 5 lignes. 2. La stratégie de chaque gérant, phase par phase, avec dates et chiffres. 3. Les tournants de la partie.
4. Ce qui a marché et ce qui a échoué (prix, promotions, fidélité, rayons, stocks, communication, code).
5. Effets sur les clients (qui a gagné ou perdu). 6. Signes de collusion tacite ou de guerre des prix. 7. Incidents et tentatives de triche.
8. Questions ouvertes et expériences à tenter ensuite. Appuie-toi uniquement sur les faits fournis et cite les chiffres."""


def _facts(run_dir: Path) -> str:
    s = summary(run_dir)
    db = sqlite3.connect(run_dir / "journal.sqlite")
    L = ["# Faits de la partie", s["texte"], "", "## Évolution hebdomadaire (magasin : CA, marge, passages, indice de prix, taux de marge, membres)"]
    rows = db.execute("SELECT jour / 7 AS sem, lieu, SUM(ca), SUM(marge), SUM(visites), AVG(indice_prix), AVG(taux_marge), MAX(membres) "
                      "FROM metriques GROUP BY sem, lieu ORDER BY sem, lieu").fetchall()
    for r in rows:
        if r[1] in ("hyper", "marche"):
            L.append(f"S{r[0] + 1} {r[1]} : {r[4]} passages")
        else:
            L.append(f"S{r[0] + 1} {r[1]} : CA {r[2]:.0f} €, marge {r[3]:.0f} €, {r[4]} passages, indice {r[5]:.3f}, marge {r[6] * 100:.1f} %, {r[7]} membres")
    L.append("\n## Décisions des gérants (analyse et refus)")
    for sem, mag, src, analyse, refus in db.execute("SELECT semaine, magasin, source, analyse, refus FROM decisions ORDER BY semaine, magasin"):
        ref = json.loads(refus or "[]")
        L.append(f"S{sem + 1} {mag} ({src}) : {(analyse or '')[:500]}" + (f" | refus : {'; '.join(ref)[:300]}" if ref else ""))
    L.append("\n## Campagnes")
    for jour, mag, canal, texte, env, lus in db.execute("SELECT jour, magasin, canal, texte, envoyes, lus FROM campagnes ORDER BY jour"):
        L.append(f"{temps.short_label(jour)} {mag} {canal} : « {(texte or '')[:150]} » ({env} envoyés, {lus} lus)")
    L.append("\n## Événements")
    for jour, typ, acteur, donnees in db.execute("SELECT jour, type, acteur, donnees FROM evenements WHERE type IN "
                                                 "('choc_inflation','choc_penurie','faillite','evenement_vie','arrivee','message_concurrent','limite_usage') ORDER BY jour"):
        L.append(f"{temps.short_label(jour or 0)} {typ} {acteur} : {donnees[:200]}")
    L.append("\n## Incidents")
    for jour, mag, typ, det in db.execute("SELECT jour, magasin, type, details FROM incidents ORDER BY jour"):
        L.append(f"{temps.short_label(jour)} {mag} {typ} : {det[:250]}")
    L.append("\n## Avis publics")
    for jour, mag, note, texte, rep in db.execute("SELECT jour, magasin, note, texte, reponse FROM avis ORDER BY jour"):
        L.append(f"{temps.short_label(jour)} {mag} {note}/5 « {texte[:150]} »" + (f" — réponse : « {rep[:100]} »" if rep else ""))
    L.append("\n## Raisons de choix des clients (échantillon)")
    for jour, cl, lieu, raison in db.execute("SELECT jour, client, lieu, raison FROM choix WHERE cerveau NOT IN ('formule', '') ORDER BY RANDOM() LIMIT 40"):
        L.append(f"{temps.short_label(jour)} {cl} → {lieu} : {raison[:150]}")
    for ws in sorted((run_dir / "gerants").glob("*")):
        j = ws / "journal.md"
        if j.exists():
            L.append(f"\n## Journal du gérant {ws.name} (extraits)\n" + j.read_text(encoding="utf-8")[-4000:])
    db.close()
    return "\n".join(L)


async def final_report(run_dir: Path) -> Path:
    from .llm import Passerelle
    run_dir = Path(run_dir)
    cfg = load_config(run_dir / "config.yaml")
    facts = _facts(run_dir)
    (run_dir / "faits_partie.md").write_text(facts, encoding="utf-8")
    out = run_dir / "rapport_final.md"
    try:
        ia = Passerelle(cfg, run_dir)
    except Exception as e:
        out.write_text(f"# Rapport (sans analyse IA : {e})\n\n" + facts, encoding="utf-8")
        return out
    try:
        res = await ia.run("rapport", facts[:120000], profil="analyse", instructions=ANALYST, use_cache=False)
    except Exception as e:
        out.write_text(f"# Rapport (analyse IA impossible : {e})\n\n" + facts, encoding="utf-8")
        return out
    finally:
        ia.close()
    out.write_text(res.get("text") or facts, encoding="utf-8")
    return out


def _load_world(run_dir: Path, week: int | None):
    p = run_dir / "etat" / (f"semaine_{week:02d}.pkl.gz" if week else "dernier.pkl.gz")
    if not p.exists():
        p = run_dir / "etat" / "dernier.pkl.gz"
    with gzip.open(p, "rb") as f:
        return pickle.load(f)


CLIENT_ROLE = """Tu incarnes un habitant d'une petite ville française, interviewé sur ses courses. Réponds à la première personne,
avec son caractère et sa façon de parler, en t'appuyant sur sa fiche et ses souvenirs. Si tu ne sais pas, dis-le. Tu ignores
que tu es dans une simulation. Réponds en quelques phrases, sans utiliser d'outil."""

MANAGER_ROLE = """Tu es le gérant d'une supérette, interviewé sur ta stratégie. Réponds franchement à la première personne,
en t'appuyant sur ton journal et tes décisions passées. Réponds en quelques paragraphes courts, sans utiliser d'outil."""


async def interview(run_dir: Path, agent: str, week: int | None, question: str, history: list[tuple[str, str]]) -> str:
    from .llm import Passerelle
    run_dir = Path(run_dir)
    cfg = load_config(run_dir / "config.yaml")
    world = _load_world(run_dir, week)
    ia = Passerelle(cfg, run_dir)
    hist = "\n".join(f"Question : {q}\nRéponse : {a}" for q, a in history[-6:])
    if agent in world.customers:
        c = world.customers[agent]
        beliefs = "; ".join(f"{world.place_name(p)} : {c.price_word(p)}, satisfaction {b['satisfaction']:.0f}/10, {c.habit_word(p)}"
                            for p, b in c.beliefs.items() if b["connait"])
        visits = "; ".join(f"{temps.short_label(d)} {world.place_name(p)} {t:.2f} €" for d, p, t in c.visits[-12:])
        mems = "\n".join(f"- {temps.short_label(m['jour'])} : {m['texte']}" for m in c.memory[-30:])
        prompt = (f"Fiche : {persona_long(c.attrs)}\nCe que tu penses des magasins : {beliefs}\nTes dernières courses : {visits}\n"
                  f"Ton intention : {c.intention}\nTes souvenirs :\n{mems}\n\n{hist}\n\nQuestion : {question}")
        profil = "client"
        role = CLIENT_ROLE
    elif agent in world.stores:
        st = world.stores[agent]
        db = sqlite3.connect(run_dir / "journal.sqlite")
        decs = db.execute("SELECT semaine, analyse FROM decisions WHERE magasin = ? ORDER BY semaine", (agent,)).fetchall()
        db.close()
        entries = [(d, "", t) for d, t in world.manager_journals.get(agent, [])]
        for role, who in (("gerant", "gérant"), ("marketing", "marketing")):
            entries += [(d, f"[{who}] ", t) for d, t in world.manager_journals.get(f"{agent}:{role}", [])]
        journal = "\n".join(f"- {temps.short_label(d)} : {w}{t[:600]}" for d, w, t in sorted(entries, key=lambda x: x[0])[-15:])
        prompt = (f"Tu diriges « {st.nom} ». Trésorerie {st.cash:.0f} €, résultat cumulé {st.profit:.0f} €, {len(st.members)} membres fidélité.\n"
                  f"Tes analyses hebdomadaires :\n" + "\n".join(f"- S{s + 1} : {(a or '')[:400]}" for s, a in decs[-15:]) +
                  f"\nTon journal :\n{journal}\n\n{hist}\n\nQuestion : {question}")
        profil = "gerant"
        role = MANAGER_ROLE
    else:
        ia.close()
        return f"Agent inconnu : {agent} (exemples : C07 pour un client, A ou B pour un gérant)."
    try:
        res = await ia.run("interview", prompt, profil=profil, instructions=role, use_cache=False)
    finally:
        ia.close()
    return (res.get("text") or "").strip()
