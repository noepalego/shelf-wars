"""Politiques expérimentales. Chaque commerçant utilise seulement ses propres observations."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import mean

from ..catalogue import PRODUCTS
from ..lois import min_price
from .decisions import empty_decisions


ARMS = ("temoin", "tarification", "relance", "rayon", "fraicheur")
POLICIES = (*ARMS, "adaptatif")


def sales_window(store, day, days=7):
    totals = Counter()
    for d in range(max(0, day - days), day):
        totals.update({p: entry[0] for p, entry in store.sales.get(d, {}).items()})
    return totals


def retention_results(store, start_day):
    cohorts = [x for x in getattr(store, "retention_trials", []) if x["day"] == start_day]
    visitors = {t["membre"] for t in store.tickets if start_day <= t["jour"] < start_day + 7 and t["lignes"]}
    out = {"treated": 0, "control": 0, "returnedTreated": 0, "returnedControl": 0}
    for trial in cohorts:
        for group, returned in (("treated", "returnedTreated"), ("control", "returnedControl")):
            out[group] += len(trial[group])
            out[returned] += len(visitors.intersection(trial[group]))
    return out


class ExperimentalManager:
    kind = "regles"

    def __init__(self, world, store, policy):
        self.world, self.store, self.policy = world, store, policy
        # Mémoire conservée dans le magasin pour les reprises de partie.
        if not hasattr(store, "strategy_history"):
            store.strategy_history = []
            store.retention_trials = []
            store.layout_before_trial = {}

    def choose_arm(self):
        st = self.store
        history = st.strategy_history
        if history:
            history[-1]["reward"] = round(st.profit - history[-1]["opening_profit"], 2)
        if self.policy != "adaptatif":
            return self.policy, "politique fixe"
        observed = defaultdict(list)
        for h in history:
            observed[h["arm"]].append(h["reward"])
        unseen = [arm for arm in ARMS if arm not in observed]
        rnd = self.world.rng.stream(f"exploration_{st.id}")
        if unseen:
            return rnd.choice(unseen), "premier essai"
        # Les dernières semaines pèsent davantage en cas de choc fournisseur.
        # L'incertitude donne une chance de réessayer une tactique peu testée.
        all_rewards = [h["reward"] for h in history]
        scale = max(10.0, mean(abs(x - mean(all_rewards)) for x in all_rewards))
        scores = {}
        for arm, rewards in observed.items():
            scores[arm] = mean(rewards[-3:]) + scale * math.sqrt(2 * math.log(len(history)) / len(rewards))
        return max(scores, key=scores.get), "bilan des profits et incertitude"

    def plan_stock(self, decisions, sales, arm):
        st, day = self.store, self.world.day
        waste = Counter()
        for d in range(day - 7, day):
            waste.update({p: row[0] for p, row in st.waste.get(d, {}).items()})
        for p, listed in st.listed.items():
            if not listed:
                continue
            life = PRODUCTS[p].conservation_j
            if day:
                daily = sales[p] / 7
                cover = min(life + 0.5, 5) * 1.3 if life <= 14 else 10
                buffer = 1 if life <= 14 else 2
                if arm == "fraicheur" and life <= 14:
                    lost_share = waste[p] / max(1, waste[p] + sales[p])
                    cover = min(life * 0.8, 4.0) * (1 - 0.6 * lost_share)
                decisions["stocks_cibles"].append({"produit": p, "stock_cible": max(2, math.ceil(daily * cover) + buffer)})
            cost = self.world.supplier.prices[p]
            if arm != "tarification":
                price = max(min_price(p, self.world.supplier, self.world.cfg["lois"]), round(1.38 * cost, 2))
                if abs(st.prices[p] - price) >= 0.05 or st.prices[p] < min_price(p, self.world.supplier, self.world.cfg["lois"]):
                    decisions["prix"].append({"produit": p, "prix": price})
        recent_loss = sum(waste.values()) / max(1, sum(sales.values()) + sum(waste.values()))
        decisions["dates_courtes"] = {"modifier": True, "actif": arm == "fraicheur",
                                      "jours_avant_peremption": 2, "remise_pct": 30 if recent_loss >= 0.06 else 18}

    def retention(self, decisions):
        st, day = self.store, self.world.day
        eligible = sorted((m for m in st.members.values() if m.visites and day - m.derniere_visite >= 7),
                          key=lambda m: m.id)
        if len(eligible) < 2:
            return
        # Échantillonnage avant attribution pour respecter les six offres hebdomadaires.
        rnd = self.world.rng.stream(f"cohortes_{st.id}")
        sample = rnd.sample(eligible, min(12, len(eligible)))
        treated, control = sample[::2], sample[1::2]
        st.retention_trials.append({"day": day, "treated": [m.id for m in treated], "control": [m.id for m in control]})
        for m in treated:
            threshold = max(3, round(0.65 * m.total_depense / m.visites))
            amount = round(max(1., min(4., threshold * 0.14)), 2)
            decisions["coupons"].append({"offre": "remise_euros_panier", "valeur": amount, "produit": "",
                                         "panier_min": threshold, "jours_validite": 6, "distribution": "membres_liste",
                                         "canal": "carte", "membres": [m.id],
                                         "texte": f"{amount:.2f} € sur vos prochaines courses dès {threshold} €."})

    def layout(self, decisions, sales):
        st, day = self.store, self.world.day
        # Un emplacement vedette par rayon, choisi selon la contribution aux marges.
        candidates = [p for p in sales if st.listed[p] and st.available(p, day) and sales[p] > 0]
        ranked = sorted(candidates, key=lambda p: (-sales[p] * (st.prices[p] - self.world.supplier.prices[p]), p))
        winners = {}
        for p in ranked:
            winners.setdefault(st.planogram[p]["rayon"], p)
        # Remettre d'abord les anciennes vedettes à leur emplacement. Chaque rayon
        # garde au plus un échange à inverser, même après une longue partie.
        for p, old in list(st.layout_before_trial.items()):
            if winners.get(old["rayon"]) != p:
                decisions["rayons"].append({"produit": p, **old})
                del st.layout_before_trial[p]
        for p in winners.values():
            if p in st.layout_before_trial:
                continue
            loc = dict(st.planogram[p])
            for restore in decisions["rayons"]:
                here = st.planogram[restore["produit"]]
                if (loc["rayon"], loc["segment"], loc["niveau"]) == (restore["rayon"], restore["segment"], restore["niveau"]):
                    loc.update(segment=here["segment"], niveau=here["niveau"])
                    break
            if loc["niveau"] != "yeux":
                decisions["rayons"].append({"produit": p, **loc, "niveau": "yeux"})
                st.layout_before_trial[p] = loc
        decisions["mises_en_avant"] = {"modifier": True, "entree": ranked[:1], "tete_gondole_1": ranked[1:2],
                                       "tete_gondole_2": ranked[2:3], "caisse": [p for p in ranked if PRODUCTS[p].impulsion][:2]}

    async def decide(self):
        st, day = self.store, self.world.day
        week = day // 7 + 1
        arm, reason = self.choose_arm()
        previous = st.strategy_history[-1]["arm"] if st.strategy_history else None
        st.strategy_history.append({"week": week, "arm": arm, "phase": reason, "opening_profit": st.profit})
        decisions = empty_decisions()
        if week == 1:
            decisions["fidelite"].update(modifier=True, actif=True, points_par_euro=1, valeur_point=0.01,
                                          seuil_utilisation=300, bonus_inscription=50, donnees_demandees=[])
        sales = sales_window(st, day)
        self.plan_stock(decisions, sales, arm)
        if arm == "relance":
            self.retention(decisions)
        if arm == "rayon":
            self.layout(decisions, sales)
        elif previous == "rayon":
            for p, loc in st.layout_before_trial.items():
                decisions["rayons"].append({"produit": p, **loc})
            st.layout_before_trial.clear()
            decisions["mises_en_avant"]["modifier"] = True
        decisions["analyse"] = f"Semaine {week} · {arm} · {reason}."
        decisions["journal"] = decisions["analyse"]
        return decisions, {"duree": 0.0, "commandes": 0, "suspect": [], "erreur": None}

    def open_day(self):
        st, day = self.store, self.world.day
        if st.bankrupt or not st.strategy_history or st.strategy_history[-1]["arm"] != "tarification":
            return
        sales = sales_window(st, day)
        for p, listed in st.listed.items():
            if not listed:
                continue
            target = max(1, st.targets[p])
            # Après la semaine de lancement, lisser avec la couverture issue des tickets.
            demand = sales[p] / min(7, max(1, day))
            desired = max(1., (target + demand * 4) / 2) if day >= 7 else target
            coverage = st.available(p, day) / desired
            markup = 1.24 if coverage > 1.25 else 1.56 if coverage < 0.65 else 1.38
            floor = min_price(p, self.world.supplier, self.world.cfg["lois"])
            price = max(floor, round(self.world.supplier.prices[p] * markup, 2))
            if abs(st.prices[p] - price) >= max(0.05, st.prices[p] * 0.035) or st.prices[p] < floor:
                st.set_price(p, price, day)
