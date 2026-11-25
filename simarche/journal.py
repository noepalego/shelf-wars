"""Journal d'événements (SQLite) : tout ce qui se passe est enregistré pour le tableau de bord et l'analyse."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS evenements (id INTEGER PRIMARY KEY, jour INT, type TEXT, acteur TEXT, donnees TEXT);
CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY, jour INT, creneau TEXT, magasin TEXT, client TEXT, membre TEXT,
    total REAL, sous_total REAL, remise_coupons REAL, remise_points REAL, nb_articles INT, cout REAL, lignes TEXT);
CREATE TABLE IF NOT EXISTS appels_llm (id INTEGER PRIMARY KEY, jour INT, role TEXT, modele TEXT, effort TEXT, duree REAL,
    jetons_entree INT, jetons_caches INT, jetons_sortie INT, jetons_raisonnement INT, ok INT, erreur TEXT, cache INT, horodatage REAL,
    cout_usd REAL);
CREATE TABLE IF NOT EXISTS metriques (jour INT, lieu TEXT, ca REAL, marge REAL, visites INT, clients INT, tresorerie REAL,
    resultat_cumule REAL, indice_prix REAL, taux_marge REAL, promos INT, membres INT, demarque REAL, marketing REAL,
    PRIMARY KEY (jour, lieu));
CREATE TABLE IF NOT EXISTS decisions (semaine INT, magasin TEXT, source TEXT, donnees TEXT, refus TEXT, analyse TEXT, duree REAL);
CREATE TABLE IF NOT EXISTS clients_semaine (semaine INT, client TEXT, donnees TEXT, PRIMARY KEY (semaine, client));
CREATE TABLE IF NOT EXISTS choix (id INTEGER PRIMARY KEY, jour INT, creneau TEXT, client TEXT, lieu TEXT, trajet TEXT, raison TEXT, cerveau TEXT);
CREATE TABLE IF NOT EXISTS campagnes (id INTEGER PRIMARY KEY, jour INT, magasin TEXT, canal TEXT, texte TEXT, envoyes INT, lus INT, details TEXT);
CREATE TABLE IF NOT EXISTS avis (id TEXT PRIMARY KEY, jour INT, magasin TEXT, client TEXT, note INT, texte TEXT, reponse TEXT);
CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY, jour INT, de TEXT, a TEXT, message TEXT, cerveau TEXT);
CREATE TABLE IF NOT EXISTS incidents (id INTEGER PRIMARY KEY, jour INT, magasin TEXT, type TEXT, details TEXT);
CREATE TABLE IF NOT EXISTS prix (jour INT, magasin TEXT, produit TEXT, prix REAL, PRIMARY KEY (jour, magasin, produit));
CREATE TABLE IF NOT EXISTS devis (id TEXT PRIMARY KEY, magasin TEXT, type TEXT, nom TEXT, demandeur TEXT, jour INT,
    statut TEXT, montant REAL, hebdo REAL, details TEXT, maj REAL);
CREATE TABLE IF NOT EXISTS inventions (magasin TEXT, nom TEXT, createur TEXT, conception INT, installation INT, arret INT,
    etat TEXT, cout_installation REAL, cout_hebdo REAL, objectif TEXT, fonctionnement TEXT, details TEXT,
    PRIMARY KEY (magasin, nom, installation));
CREATE TABLE IF NOT EXISTS missions (id TEXT PRIMARY KEY, magasin TEXT, mandant TEXT, jour INT, statut TEXT, prix REAL,
    categorie TEXT, enonce TEXT, resultat TEXT, fichiers TEXT, programmes TEXT, maj REAL);
CREATE TABLE IF NOT EXISTS programmes (magasin TEXT, fichier TEXT, version INT, jour INT, auteur TEXT, origine TEXT,
    code TEXT, PRIMARY KEY (magasin, fichier, version));
CREATE TABLE IF NOT EXISTS direction (semaine INT, magasin TEXT, role TEXT, objectifs TEXT, hypotheses TEXT,
    sources TEXT, analyse TEXT, acceptees TEXT, refusees TEXT, PRIMARY KEY (semaine, magasin, role));
CREATE TABLE IF NOT EXISTS observations (jour INT, magasin TEXT, instrument TEXT, nombre INT, PRIMARY KEY (jour, magasin, instrument));
CREATE TABLE IF NOT EXISTS actions_programmes (jour INT, magasin TEXT, programme TEXT, nombre INT, PRIMARY KEY (jour, magasin, programme));
CREATE TABLE IF NOT EXISTS univers (id TEXT PRIMARY KEY, jour INT, creneau TEXT, type TEXT, lieu TEXT, client TEXT, donnees TEXT);
"""


def _j(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(appels_llm)")}
        if "cout_usd" not in cols:  # journaux créés avant le moteur Claude
            self.db.execute("ALTER TABLE appels_llm ADD COLUMN cout_usd REAL")
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(metriques)")}
        for col, kind in (("salaires", "REAL"), ("creneaux_travail", "INT"), ("systemes", "INT")):
            if col not in cols:  # journaux créés avant le personnel et les équipements
                self.db.execute(f"ALTER TABLE metriques ADD COLUMN {col} {kind}")
        self.db.commit()

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def event(self, day, kind: str, actor: str, data) -> None:
        self.db.execute("INSERT INTO evenements (jour, type, acteur, donnees) VALUES (?, ?, ?, ?)", (day, kind, actor, _j(data)))

    def ticket(self, t: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (t["id"], t["jour"], t["creneau"], t["magasin"], t["client"], t["membre"], t["total"], t["sous_total"],
                         t["remise_coupons"], t["remise_points"], sum(l["quantite"] for l in t["lignes"]), t.get("cout", 0.0),
                         _j(t["lignes"])))

    def llm_call(self, day, role, model, effort, duration, tin, tcached, tout, treason, ok, error, cached, cost=None) -> None:
        self.db.execute("INSERT INTO appels_llm (jour, role, modele, effort, duree, jetons_entree, jetons_caches, jetons_sortie, "
                        "jetons_raisonnement, ok, erreur, cache, horodatage, cout_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (day, role, model, effort, duration, tin, tcached, tout, treason, int(ok), (error or "")[:500], int(cached),
                         time.time(), cost))
        self.db.commit()

    def metrics(self, day: int, place: str, m: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO metriques (jour, lieu, ca, marge, visites, clients, tresorerie, resultat_cumule, "
                        "indice_prix, taux_marge, promos, membres, demarque, marketing, salaires, creneaux_travail, systemes) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (day, place, m.get("ca", 0), m.get("marge", 0), m.get("visites", 0), m.get("clients", 0),
                         m.get("tresorerie", 0), m.get("resultat_cumule", 0), m.get("indice_prix", 1), m.get("taux_marge", 0),
                         m.get("promos", 0), m.get("membres", 0), m.get("demarque", 0), m.get("marketing", 0),
                         m.get("salaires", 0), m.get("creneaux_travail", 0), m.get("systemes", 0)))

    def decision(self, week: int, store: str, source: str, data, refused, analysis: str, duration: float) -> None:
        self.db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)", (week, store, source, _j(data), _j(refused), analysis, duration))

    def customer_week(self, week: int, cid: str, data) -> None:
        self.db.execute("INSERT OR REPLACE INTO clients_semaine VALUES (?,?,?)", (week, cid, _j(data)))

    def choice(self, day, slot, cid, place, trip, reason, brain) -> None:
        self.db.execute("INSERT INTO choix (jour, creneau, client, lieu, trajet, raison, cerveau) VALUES (?,?,?,?,?,?,?)",
                        (day, slot, cid, place, trip, reason, brain))

    def campaign(self, day, store, channel, text, sent, read, details) -> None:
        self.db.execute("INSERT INTO campagnes (jour, magasin, canal, texte, envoyes, lus, details) VALUES (?,?,?,?,?,?,?)",
                        (day, store, channel, text, sent, read, _j(details)))

    def review(self, r: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO avis VALUES (?,?,?,?,?,?,?)",
                        (r["id"], r["jour"], r["magasin"], r["client"], r["note"], r["texte"], r.get("reponse") or ""))

    def conversation(self, day, a, b, message, brain) -> None:
        self.db.execute("INSERT INTO conversations (jour, de, a, message, cerveau) VALUES (?,?,?,?,?)", (day, a, b, message, brain))

    def incident(self, day, store, kind, details) -> None:
        self.db.execute("INSERT INTO incidents (jour, magasin, type, details) VALUES (?,?,?,?)", (day, store, kind, _j(details)))

    # ---------------------------------------------------------------- journal d'expérimentation
    def devis(self, q: dict, store: str) -> None:
        v = q.get("verdict") or {}
        amount = v.get("cout_installation", q.get("prix"))
        details = {k: q.get(k) for k in ("fichier", "origine", "texte", "remplace", "categorie", "justification")}
        details["verdict"] = {k: v.get(k) for k in ("accepte", "motif", "version_realisable", "fonctionnalites", "limites",
                                                    "capacites_non_representables", "donnees", "installation", "plancher")}
        self.db.execute("INSERT OR REPLACE INTO devis VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (q["id"], store, q["type"], q["nom"], q.get("demandeur"), q["jour"], q["statut"], amount,
                         v.get("cout_hebdomadaire", 0.0), _j(details), time.time()))

    def invention(self, store: str, s: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO inventions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (store, s.get("nom"), s.get("propose_par"), s.get("propose_le"), s.get("installe_le"), s.get("arrete_le"),
                         s.get("etat"), s.get("cout_installation"), s.get("cout_hebdomadaire"), (s.get("texte") or "")[:3000],
                         s.get("installation"), _j({"spec": s.get("spec"), "effets": s.get("effets"), "devis": s.get("devis"),
                                                     "fonctionnalites": s.get("fonctionnalites"), "limites": s.get("limites"),
                                                     "observations": s.get("observations", 0)})))

    def mission(self, store: str, m: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO missions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (m["id"], store, m.get("mandant"), m.get("jour"), m.get("statut"), m.get("prix"), m.get("categorie"),
                         (m.get("enonce") or "")[:6000], _j(m.get("resultat")), _j(m.get("fichiers", [])),
                         _j(m.get("programmes", [])), time.time()))

    def programme(self, store: str, fichier: str, version: int, day: int, author, origin: str, code: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO programmes VALUES (?,?,?,?,?,?,?)",
                        (store, fichier, version, day, author, origin, code))

    def direction(self, week: int, store: str, role: str, data: dict, accepted, refused) -> None:
        d = data or {}
        self.db.execute("INSERT OR REPLACE INTO direction VALUES (?,?,?,?,?,?,?,?,?)",
                        (week, store, role, d.get("objectifs", ""), _j(d.get("hypotheses", [])), _j(d.get("sources_utilisees", [])),
                         d.get("analyse", ""), _j(accepted), _j(refused)))

    def observations(self, day: int, store: str, counts: dict) -> None:
        self.db.executemany("INSERT OR REPLACE INTO observations VALUES (?,?,?,?)",
                            [(day, store, k, n) for k, n in counts.items()])

    def program_actions(self, day: int, store: str, counts: dict) -> None:
        self.db.executemany("INSERT OR REPLACE INTO actions_programmes VALUES (?,?,?,?)",
                            [(day, store, k, n) for k, n in counts.items()])

    def universe(self, events: list[dict]) -> None:
        self.db.executemany("INSERT OR REPLACE INTO univers VALUES (?,?,?,?,?,?,?)",
                            [(e["id"], e["jour"], e["creneau"], e["type"], e["lieu"], e["client"],
                              _j({"donnees": e["donnees"], "verite": e["verite"]})) for e in events])

    def prices(self, day: int, store: str, prices: dict) -> None:
        self.db.executemany("INSERT OR REPLACE INTO prix VALUES (?,?,?,?)", [(day, store, p, v) for p, v in prices.items()])
