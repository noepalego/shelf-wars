"""Registre d'événements de l'univers simulé (vérité du moteur).

Tout ce qui se passe réellement dans la ville est enregistré ici au moment où le moteur l'exécute : une personne
passe devant un magasin, entre, parcourt les rayons, regarde un produit, le prend, le repose, renonce à un besoin,
attend en caisse, paie, sort, se rend à l'hypermarché, ouvre un e-mail… Chaque événement porte la vérité cachée
(identité réelle, âge, humeur…) : elle reste dans le moteur. Les magasins n'en voient que ce que leurs instruments
savent mesurer (voir instruments.py), avec leur couverture, leur précision et leurs pannes.

Les événements découlent des décisions des habitants (pilotés par un modèle ou par la formule) une fois validées et
appliquées par le moteur : aucune observation n'est jamais inventée.
"""
from __future__ import annotations

from collections import Counter


class Registre:
    def __init__(self):
        self.seq = 0
        self.du_jour: list[dict] = []        # événements du jour (vérité ; jamais montrés aux agents)
        self.jour = None
        self.compteurs: Counter = Counter()  # nombre d'événements par type depuis le début
        self.ecouteur = None                 # fonction appelée à chaque événement (production des observations)

    def __getstate__(self):
        d = dict(self.__dict__)
        d["ecouteur"] = None
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)
        self.__dict__.setdefault("ecouteur", None)

    def nouveau_jour(self, jour: int) -> list[dict]:
        old, self.du_jour, self.jour = self.du_jour, [], jour
        return old

    def emettre(self, type_: str, lieu: str, jour: int, creneau: str | None, heure: str | None, *, client: str | None = None,
                verite: dict | None = None, donnees: dict | None = None) -> dict:
        self.seq += 1
        ev = {"id": f"E{self.seq}", "type": type_, "lieu": lieu, "jour": jour, "creneau": creneau, "heure": heure,
              "client": client, "verite": dict(verite or {}), "donnees": dict(donnees or {})}
        self.du_jour.append(ev)
        self.compteurs[type_] += 1
        if self.ecouteur is not None:
            self.ecouteur(ev)
        return ev


def heure_dans(creneau: str | None, rnd) -> str:
    lo, hi = {"matin": (8, 12), "midi": (12, 15), "soir": (15, 20), "jour": (8, 20)}.get(creneau or "jour", (8, 20))
    m = rnd.randrange(lo * 60, hi * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def verite_personne(c, *, enfants: bool = False, mode: str | None = None) -> dict:
    """Vérité physique d'une personne au moment de l'événement (jamais transmise telle quelle aux magasins)."""
    a, s = c.attrs, c.state
    groupe = 1 + (int(a.get("nb_enfants", 0)) if enfants else 0)
    return {"age": a.get("age"), "genre": a.get("genre"), "enfants_presents": bool(enfants), "taille_groupe": groupe,
            "mode_deplacement": mode or "", "humeur": round(float(s.get("humeur", 0.6)), 3), "quartier": a.get("quartier"),
            # Données internes : présentes pour l'audit du moteur, jamais observables par un instrument.
            "budget_restant": s.get("budget_restant"), "sensibilite_prix": a.get("sensibilite_prix"),
            "intention": getattr(c, "intention", "")}
