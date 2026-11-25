"""Gérants à règles fixes : sparring-partners gratuits pour tester le moteur et comparer avec l'IA."""
from __future__ import annotations

import math

from ..catalogue import PANIER_REFERENCE, PRODUCTS
from ..lois import min_price
from .decisions import empty_decisions


class RuleManager:
    kind = "regles"

    def __init__(self, world, store, style: str = "equilibre"):
        self.world = world
        self.store = store
        self.style = style or "equilibre"

    def _sales_last_week(self) -> dict[str, int]:
        day = self.world.day
        out: dict[str, int] = {}
        for d in range(day - 7, day):
            for pid, (q, _, _) in self.store.sales.get(d, {}).items():
                out[pid] = out.get(pid, 0) + q
        return out

    def _price(self, pid: str, markup: float) -> float:
        w = self.world
        return round(max(min_price(pid, w.supplier, w.cfg["lois"]), w.supplier.prices[pid] * markup), 2)

    async def decide(self) -> tuple[dict, dict]:
        w, s = self.world, self.store
        d = empty_decisions()
        week = w.day // 7
        if self.style == "fixe":  # ne change rien (utile pour le laboratoire)
            return d, {"duree": 0.0, "commandes": 0, "suspect": [], "erreur": None}
        sales = self._sales_last_week()
        listed = [p for p, ok in s.listed.items() if ok]
        rnd = w.rng.stream(f"regles_{s.id}")
        if self.style == "marge":
            markups = {p: 1.45 for p in listed}
        elif self.style == "hasard":
            markups = {p: rnd.uniform(1.2, 1.6) for p in listed}
        else:
            markups = {p: 1.38 for p in listed}
        if self.style == "moins_cher":
            d["releve_prix"] = PANIER_REFERENCE[:20]
            survey = (s.price_survey or {}).get("prix", {})
            for p in listed:
                v = survey.get(p, {}).get("prix") if survey else None
                if v:
                    markups[p] = max(1.12, (v * 0.97) / w.supplier.prices[p])
                else:
                    markups[p] = 1.30
        d["prix"] = [{"produit": p, "prix": self._price(p, markups[p])} for p in listed
                     if abs(self._price(p, markups[p]) - s.prices[p]) >= 0.05]
        targets = []
        for p in listed:
            if not week:
                continue
            daily = sales.get(p, 0) / 7
            cons = PRODUCTS[p].conservation_j
            t = math.ceil(daily * min(cons + 0.5, 5) * 1.3) + 1 if cons <= 14 else math.ceil(daily * 10) + 2
            targets.append({"produit": p, "stock_cible": max(2, t)})
        d["stocks_cibles"] = targets
        if self.style in ("equilibre", "moins_cher") and week == 0:
            d["fidelite"] = {"modifier": True, "actif": True, "points_par_euro": 1, "valeur_point": 0.01, "seuil_utilisation": 300,
                             "bonus_inscription": 50, "donnees_demandees": ["age", "quartier", "email"], "prix_membres": []}
            d["dates_courtes"] = {"modifier": True, "actif": True, "jours_avant_peremption": 1, "remise_pct": 30}
        if self.style == "equilibre":
            top = [p for p, _ in sorted(sales.items(), key=lambda kv: -kv[1]) if s.listed.get(p)][:3]
            if top:
                d["promotions"] = [{"produits": top, "mecanique": "pourcentage", "valeur": 15, "y": 0, "duree_jours": 7, "affiche": True}]
                if week % 2 == 0:
                    ann = []
                    for p in top:
                        new = next((x["prix"] for x in d["prix"] if x["produit"] == p), s.prices[p])
                        ann.append({"produit": p, "prix": round(new * 0.85 + 0.005, 2)})
                    d["prospectus"] = {"envoyer": True, "quartiers": [], "exemplaires": len(w.active_customers()),
                                       "texte": f"Cette semaine chez {s.nom} : -15 % sur vos produits préférés !",
                                       "prix_annonces": ann}
        if self.style == "hasard" and rnd.random() < 0.3 and listed:
            d["promotions"] = [{"produits": rnd.sample(listed, 2), "mecanique": "pourcentage", "valeur": rnd.choice([10, 20, 30]),
                                "y": 0, "duree_jours": 7, "affiche": rnd.random() < 0.5}]
        d["analyse"] = f"Gérant à règles ({self.style})."
        d["journal"] = f"Semaine {week + 1} : règles {self.style} appliquées."
        return d, {"duree": 0.0, "commandes": 0, "suspect": [], "erreur": None}
