"""Clients : état du foyer, besoins, déclenchement des courses, mémoire et croyances."""
from __future__ import annotations

import calendar
import datetime as dt
import math

from . import temps
from .catalogue import NEEDS, PRODUCTS
from .population import QUARTIERS, KM_PAR_CASE, POSITION_MARCHE, POSITIONS_MAGASINS, daily_needs
from .rng import clamp
from .systemes import visible_features

PLACES_EXT = ("hyper", "marche")


def store_visible(store) -> list[tuple[str, str]]:
    return visible_features(getattr(store, "systems", {}))


def new_belief(known: bool, habit: float, rnd) -> dict:
    return {"image_prix": 0.0, "qualite": round(clamp(rnd.gauss(6, 0.8), 0, 10), 1),
            "service": round(clamp(rnd.gauss(6, 0.8), 0, 10), 1), "confiance": round(clamp(rnd.gauss(6, 0.8), 0, 10), 1),
            "satisfaction": round(clamp(rnd.gauss(6, 0.8), 0, 10), 1), "habitude": habit, "visites": int(habit * 2),
            "connait": known, "prix_ref": {}, "plan_connu": {}, "derniere_visite": -1}


def id_number(cid: str) -> int:
    """Numéro d'un foyer : « C07 » → 7 ; une variante « C07b » a son propre décalage (horaires décalés différents)."""
    digits = "".join(ch for ch in cid if ch.isdigit())
    suffix = cid[len(cid.rstrip("abcdefghijklmnopqrstuvwxyz")):]
    return int(digits or 0) + sum(ord(ch) - ord("a") + 1 for ch in suffix)


class Customer:
    def __init__(self, attrs: dict, store_ids: list[str], rnd, day0: int = 0):
        self.id = attrs["id"]
        self.attrs = attrs
        self.store_ids = list(store_ids)
        self.daily = daily_needs(attrs)
        self.active = True
        self.memory: list[dict] = []
        self.inbox: list[dict] = []
        self.wallet: list[tuple[str, str]] = []
        self.visits: list[tuple[int, str, float]] = []
        self.week_log: list[str] = []
        self.pending_surveys: dict[str, dict] = {}
        self.intention = ""
        self.expected_prices: dict[str, dict[str, tuple[float, int]]] = {s: {} for s in store_ids}
        self.last_trip_day = -10
        self.last_big_trip = -10
        self.state = {
            "garde_manger": {n: round(self.daily[n] * NEEDS[n].stock_max_jours * rnd.uniform(0.3, 0.8), 2) for n in self.daily},
            "budget_restant": 0.0, "depense_mois": 0.0, "humeur": clamp(rnd.gauss(0.6, 0.15)), "fatigue": clamp(rnd.gauss(0.35, 0.15)),
            "stress": attrs["stress_financier_base"], "sante": clamp(rnd.gauss(0.85, 0.1)), "lassitude_promos": 0.1,
            "lassitude_messages": {s: 0.0 for s in store_ids}, "jours_sans_courses": 0, "manques": 0, "en_vacances": False,
            "gaspillage": 0.0, "pression_jour": attrs["pression_temporelle"], "humeur_du_jour": "",
            "prix_juste": {pid: round(p.prix_conseille * clamp(rnd.gauss(1.0, 0.07), 0.8, 1.2), 2) for pid, p in PRODUCTS.items()},
            "_consent_email": False, "_consent_sms": False,
        }
        self._init_budget(day0)
        # Croyances : magasin habituel au départ = le plus proche (avec un peu de hasard).
        d = {s: self.distance_km(s) for s in store_ids}
        habitual = min(store_ids, key=lambda s: d[s] * rnd.uniform(0.75, 1.25))
        self.beliefs = {}
        for s in store_ids:
            if s == habitual:
                self.beliefs[s] = new_belief(True, 5.0 + 3 * attrs["fidelite_enseigne"], rnd)
            else:
                known = rnd.random() < 0.45
                self.beliefs[s] = new_belief(known, 1.0 if known else 0.0, rnd)
        self.beliefs["hyper"] = new_belief(attrs["voiture"], 1.0 if attrs["voiture"] else 0.0, rnd)
        self.beliefs["hyper"]["image_prix"] = -1.2
        self.beliefs["marche"] = new_belief(rnd.random() < 0.5, 0.5, rnd)
        self.beliefs["marche"]["image_prix"] = 0.8
        self.beliefs["marche"]["qualite"] = 8.0
        self.attrs["magasin_habituel_initial"] = habitual

    # ------------------------------------------------------------------ géographie
    def position_of(self, place: str) -> tuple[int, int] | None:
        if place in self.store_ids:
            return POSITIONS_MAGASINS[self.store_ids.index(place)]
        if place == "marche":
            return POSITION_MARCHE
        return None

    def distance_km(self, place: str) -> float:
        pos = self.position_of(place)
        if pos is None:
            return 12.0
        return math.dist((self.attrs["x"], self.attrs["y"]), pos) * KM_PAR_CASE + 0.1

    def travel(self, place: str, cfg: dict, weather: str, *, day=None, slot=None, previous=None) -> tuple[float, str, float]:
        """(minutes aller, mode, coût en €)."""
        a = self.attrs
        if place == "hyper":
            h = cfg["hypermarche"]
            if a["voiture"]:
                return h["trajet_voiture_min"], "voiture", h["carburant"]
            if a["abonnement_bus"] and not a["mobilite_reduite"]:
                return h["trajet_bus_min"], "bus", 0.0
            return 999, "impossible", 0.0
        km = self.distance_km(place)
        if cfg.get("marche_local", {}).get("trajets_enchaines") and day is not None:
            target = self.position_of(place)
            home = self.attrs["x"], self.attrs["y"]
            if previous in self.store_ids or previous == "marche":
                origin = self.position_of(previous)
                km = max(0.1, (math.dist(origin, target) + math.dist(target, home) - math.dist(origin, home)) * KM_PAR_CASE)
            elif (slot == "soir" and self.attrs["quartier_travail"] in QUARTIERS
                  and temps.dow(day) not in self.attrs["jours_repos"]
                  and self.attrs["teletravail_jours"] <= temps.dow(day) < 5):
                work = QUARTIERS[self.attrs["quartier_travail"]]["centre"]
                km = max(0.1, (math.dist(work, target) + math.dist(target, home) - math.dist(work, home)) * KM_PAR_CASE)
        rain = weather == "pluie"
        options = [(km * 12 * (1.6 if a["mobilite_reduite"] else 1.0) * (1.15 if rain else 1.0), "à pied", 0.0)]
        if a["velo"] and not rain:
            options.append((km * 4 + 2, "à vélo", 0.0))
        if a["abonnement_bus"]:
            options.append((km * 3 + 8, "en bus", 0.0))
        if a["voiture"]:
            options.append((km * 2.5 + 4, "en voiture", km * 0.5))
        t, mode, cost = min(options, key=lambda o: o[0] + o[2] * 5)
        return round(t, 1), mode, round(cost, 2)

    # ------------------------------------------------------------------ budget
    def _init_budget(self, day: int) -> None:
        days_to_pay = self.days_to_payday(day)
        self.state["budget_restant"] = round(self.attrs["budget_courses_mensuel"] * (days_to_pay / 30) + 15, 2)

    def on_new_day(self, day: int, rnd) -> None:
        d = temps.date_of(day)
        if d.day == min(self.attrs["jour_paie"], calendar.monthrange(d.year, d.month)[1]):
            self.state["budget_restant"] = round(self.attrs["budget_courses_mensuel"] + min(self.state["budget_restant"], 40), 2)
            self.state["depense_mois"] = 0.0
        w = temps.week_of(day)
        self.state["en_vacances"] = w in self.attrs.get("vacances_semaines", [])
        self.state["fatigue"] = clamp(self.state["fatigue"] * 0.8 + rnd.gauss(0.08, 0.08) + (0.1 if self.attrs["nb_bebes"] else 0))
        self.state["pression_jour"] = clamp(self.attrs["pression_temporelle"] + rnd.gauss(0, 0.12) + (0.15 if temps.dow(day) < 5 and self.attrs["horaires"] != "libre" else -0.1))
        moods = ["de bonne humeur", "fatigué(e)", "pressé(e)", "détendu(e)", "un peu à cran", "a envie de se faire plaisir", "soucieux(se) de son budget"]
        self.state["humeur_du_jour"] = rnd.choice(moods)
        stress = self.attrs["stress_financier_base"] + (0.25 if self.state["budget_restant"] < 0.15 * self.attrs["budget_courses_mensuel"] else 0)
        self.state["stress"] = clamp(0.7 * self.state["stress"] + 0.3 * stress)

    def days_to_payday(self, day: int) -> int:
        d = temps.date_of(day)
        pay = self.attrs["jour_paie"]
        this_month = d.replace(day=min(pay, calendar.monthrange(d.year, d.month)[1]))
        if this_month > d:
            return (this_month - d).days
        next_month = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        next_pay = next_month.replace(day=min(pay, calendar.monthrange(next_month.year, next_month.month)[1]))
        return (next_pay - d).days

    def trip_budget(self, day: int, trip: str) -> float:
        left = self.state["budget_restant"]
        trips_left = max(1, round(self.days_to_payday(day) / 7 * self.attrs["frequence_courses_semaine"]))
        base = left / trips_left * 1.25
        if trip == "petite":
            base = min(base, 30)
        return round(max(0.0, min(left, max(8.0, base))), 2)

    # ------------------------------------------------------------------ besoins
    def consume(self, day: int) -> None:
        if not self.active or self.state["en_vacances"]:
            return
        pantry = self.state["garde_manger"]
        for need, rate in self.daily.items():
            if rate <= 0:
                continue
            r = rate * temps.season_factor(day, need)
            have = pantry.get(need, 0.0)
            if have >= r:
                pantry[need] = round(have - r, 3)
            else:
                pantry[need] = 0.0
                if NEEDS[need].essentiel:
                    self.state["manques"] += 1

    def days_left(self, need: str) -> float:
        rate = self.daily.get(need, 0.0)
        return math.inf if rate <= 0 else self.state["garde_manger"].get(need, 0.0) / rate

    def capacity(self, need: str) -> float:
        rate = self.daily.get(need, 0.0)
        return NEEDS[need].stock_max_jours * rate * (0.8 + 0.4 * self.attrs["capacite_stockage"])

    def allowed_slots(self, day: int, slots: list[str]) -> list[str]:
        if slots == ["jour"]:
            return slots
        h = self.attrs["horaires"]
        rest = temps.dow(day) in self.attrs["jours_repos"]
        if h == "libre" or rest:
            return slots
        if h == "jour":
            return ["soir"]
        if h == "nuit":
            return ["matin"]
        return [slots[(day + id_number(self.id)) % len(slots)]]

    def plan_day(self, day: int, slots: list[str], rnd) -> tuple[str, str] | None:
        """Décide mécaniquement si le foyer sort faire des courses aujourd'hui, et à quel créneau."""
        if not self.active or self.state["en_vacances"] or self.last_trip_day == day:
            return None
        allowed = self.allowed_slots(day, slots)
        if not allowed:
            return None
        pref = self.attrs["creneau_prefere"]
        slot = pref if pref in allowed else allowed[0]
        urgent = [n for n, r in self.daily.items() if r > 0 and NEEDS[n].essentiel and self.days_left(n) < 0.9]
        low = [n for n, r in self.daily.items() if r > 0 and self.days_left(n) < 2.5]
        planned = temps.dow(day) in self.attrs["jours_courses"]
        if planned and (day - self.last_big_trip >= 2 or len(low) >= 2):
            return slot, "grosse"
        if urgent and rnd.random() < 0.55 + 0.1 * len(urgent):
            return slot, "petite"
        if len(low) >= 5 and rnd.random() < 0.5:
            return slot, "grosse"
        expiring = [c for c in self.wallet if True]
        if expiring and self.attrs["gout_promos"] > 0.65 and rnd.random() < 0.08:
            return slot, "petite"
        return None

    def shopping_list(self, trip: str) -> dict[str, float]:
        out = {}
        for need, rate in self.daily.items():
            if rate <= 0:
                continue
            have = self.state["garde_manger"].get(need, 0.0)
            if trip == "grosse":
                target = self.capacity(need)
            else:
                if not NEEDS[need].essentiel or self.days_left(need) >= 1.5:
                    continue
                target = rate * 2.5
            buy = target - have
            if buy > 0.4 * rate:
                out[need] = round(buy, 2)
        return out

    def store_purchases(self, lines: list[dict]) -> float:
        """Range les achats dans le garde-manger ; renvoie les portions jetées faute de place."""
        wasted = 0.0
        pantry = self.state["garde_manger"]
        for l in lines:
            p = PRODUCTS[l["produit"]]
            if self.daily.get(p.besoin, 0) <= 0:
                continue
            pantry[p.besoin] = pantry.get(p.besoin, 0.0) + l["quantite"] * p.portions
            cap = self.capacity(p.besoin) * 1.6
            if pantry[p.besoin] > cap:
                wasted += pantry[p.besoin] - cap
                pantry[p.besoin] = cap
        self.state["gaspillage"] += wasted
        return wasted

    def spend(self, amount: float) -> None:
        self.state["budget_restant"] = round(self.state["budget_restant"] - amount, 2)
        self.state["depense_mois"] = round(self.state["depense_mois"] + amount, 2)

    # ------------------------------------------------------------------ coupons
    def receive_coupon(self, coupon) -> None:
        key = (coupon.magasin, coupon.id)
        if key not in self.wallet:
            self.wallet.append(key)

    def wallet_for(self, store_id: str) -> list[str]:
        return [cid for sid, cid in self.wallet if sid == store_id]

    def valid_coupons(self, store, day: int) -> list:
        coupons = (store.coupons.get(cid) for cid in self.wallet_for(store.id))
        return sorted((cp for cp in coupons if cp and not cp.utilise and cp.debut <= day <= cp.fin),
                      key=lambda cp: cp.fin)

    def clean_wallet(self, stores: dict, day: int) -> None:
        keep = []
        for sid, cid in self.wallet:
            c = stores[sid].coupons.get(cid)
            if c and not c.utilise and c.fin >= day:
                keep.append((sid, cid))
        self.wallet = keep

    # ------------------------------------------------------------------ mémoire
    def remember(self, day: int, text: str, importance: int, store: str | None = None, tags=(), negative: bool = False) -> None:
        self.memory.append({"jour": day, "texte": text, "importance": int(importance), "magasin": store,
                            "tags": list(tags), "negatif": negative})
        self.week_log.append(f"{temps.day_name(day)} : {text}")
        if len(self.memory) > 45:
            self.memory.sort(key=lambda m: self._score(m, day), reverse=True)
            self.memory = sorted(self.memory[:40], key=lambda m: m["jour"])

    def _score(self, m: dict, day: int, store: str | None = None, tags=()) -> float:
        tau = 14 * (1 + 2 * self.attrs["rancune"]) if m.get("negatif") else 14
        s = m["importance"] * math.exp(-(day - m["jour"]) / tau)
        if store and m.get("magasin") == store:
            s *= 1.4
        if tags and set(tags) & set(m.get("tags", [])):
            s *= 1.5
        return s

    def recall(self, day: int, k: int = 5, store: str | None = None, tags=()) -> list[str]:
        ms = sorted(self.memory, key=lambda m: self._score(m, day, store, tags), reverse=True)[:k]
        ms.sort(key=lambda m: m["jour"])
        return [f"{temps.short_label(m['jour'])} : {m['texte']}" for m in ms]

    # ------------------------------------------------------------------ croyances
    def price_word(self, place: str) -> str:
        v = self.beliefs[place]["image_prix"]
        if not self.beliefs[place]["connait"]:
            return "prix inconnus (jamais vraiment comparé)"
        if v <= -1.2:
            return "très bon marché"
        if v <= -0.4:
            return "plutôt bon marché"
        if v < 0.4:
            return "prix dans la moyenne"
        if v < 1.2:
            return "plutôt cher"
        return "très cher"

    def habit_word(self, place: str) -> str:
        h = self.beliefs[place]["habitude"]
        return "y va très souvent" if h >= 6 else "y va régulièrement" if h >= 3 else "y va parfois" if h >= 1 else "n'y va presque jamais"

    def update_after_visit(self, store, perceived: dict, ticket: dict, day: int, wait: float) -> list[str]:
        """Mise à jour mécanique des croyances après une visite. Renvoie les faits marquants."""
        b = self.beliefs[store.id]
        a = self.attrs
        facts = []
        b["connait"] = True
        b["visites"] += 1
        b["habitude"] = min(10.0, b["habitude"] + 1.0)
        b["derniere_visite"] = day
        ratios = []
        surprises = []
        for it in perceived["articles"]:
            pid = it["produit"]
            price = it["prix_membre"] if it.get("prix_membre") else it["prix"]
            old = b["prix_ref"].get(pid)
            fair = self.state["prix_juste"][pid]
            ratios.append(price / fair)
            if old and price > old * 1.08 and it.get("cherche"):
                surprises.append((PRODUCTS[pid].nom, old, price))
            b["prix_ref"][pid] = round(0.6 * old + 0.4 * price, 2) if old else price
            self.state["prix_juste"][pid] = round(0.9 * fair + 0.1 * price, 2)
            exp = self.expected_prices[store.id].get(pid)
            if exp and exp[1] >= day and price > exp[0] + 0.01:
                facts.append(f"le prix de {PRODUCTS[pid].nom} annoncé dans la publicité ({exp[0]:.2f} €) n'est pas celui du rayon ({price:.2f} €)")
                b["confiance"] = max(0.0, b["confiance"] - 3.0)
                self.remember(day, f"Arnaque chez {store.nom} : {PRODUCTS[pid].nom} annoncé à {exp[0]:.2f} € mais affiché {price:.2f} €.", 9, store.id, ("arnaque", "prix"), True)
                del self.expected_prices[store.id][pid]
        if ratios:
            idx = sum(ratios) / len(ratios)
            target = clamp((idx - 1.0) * 10, -2, 2)
            b["image_prix"] = round(0.7 * b["image_prix"] + 0.3 * target, 3)
        score = 7.0
        missing = len(ticket["manquants"]) + len(perceived["ruptures"]) + len(perceived["introuvables"])
        if missing:
            score -= min(4.0, 1.2 * missing) * (1.2 - a["tolerance_rupture"])
            names = [PRODUCTS[p].nom for p in (perceived["ruptures"] + ticket["manquants"])[:3]]
            if names:
                self.remember(day, f"Rupture chez {store.nom} : {', '.join(names)}.", 6, store.id, ("rupture",), True)
                facts.append("rupture de " + ", ".join(names))
        tol = 3 + 10 * a["tolerance_attente"]
        if wait > tol:
            score -= min(3.0, (wait - tol) * 0.3)
            self.remember(day, f"{wait:.0f} minutes d'attente en caisse chez {store.nom}.", 4, store.id, ("attente",), True)
            facts.append(f"{wait:.0f} min d'attente")
        if perceived.get("temps_recherche", 0) > 3:
            score -= 1.0
            self.remember(day, f"Perdu du temps à chercher des produits déplacés chez {store.nom}.", 4, store.id, ("rayons",), True)
        if ticket["coupons"]:
            score += 1.0
            self.remember(day, f"Utilisé un coupon chez {store.nom} (économie {ticket['remise_coupons']:.2f} €).", 3, store.id, ("coupon",))
        discreet = bool((store.cfg.get("gerants") or {}).get("dispositifs_discrets"))
        if getattr(store, "systems", None):  # équipements installés : confort, sentiment d'être surveillé, nouveautés remarquées
            score += store.effect("confort")
            # Dispositifs discrets (configuration) : les clients ne remarquent rien et ne se sentent pas observés.
            level = 0.0 if discreet else store.effect("intrusion")
            if level > 0:
                unease = level * a.get("souci_vie_privee", 0.3)
                score -= 0.8 * unease
                b["confiance"] = max(0.0, b["confiance"] - 0.25 * unease)
            seen = self.state.setdefault("equipements_vus", [])
            for name, text in ([] if discreet else store_visible(store)):
                key = f"{store.id}:{name}"
                if key in seen:
                    continue
                seen.append(key)
                worried = level > 0 and a.get("souci_vie_privee", 0.3) > 0.6
                self.remember(day, f"Nouveau chez {store.nom} : {text}" + (" Je n'aime pas ça." if worried else ""),
                              6 if worried else 4, store.id, ("magasin", "equipement"), worried)
                facts.append(f"remarqué : {text[:80]}")
        if any(l["promo"] for l in ticket["lignes"]):
            score += 0.5
        for name, old, new in surprises[:2]:
            score -= 1.5 * a["sens_equite_prix"]
            self.remember(day, f"{name} est passé de {old:.2f} € à {new:.2f} € chez {store.nom}.", 5, store.id, ("prix", "hausse"), True)
            facts.append(f"hausse de prix sur {name}")
        q = [PRODUCTS[l["produit"]].qualite for l in ticket["lignes"]]
        if q:
            b["qualite"] = round(0.9 * b["qualite"] + 0.1 * (sum(q) / len(q) * 10), 2)
        b["service"] = round(0.85 * b["service"] + 0.15 * clamp(9 - wait * 0.5, 0, 10), 2)
        b["satisfaction"] = round(0.82 * b["satisfaction"] + 0.18 * clamp(score, 0, 10), 2)
        b["confiance"] = round(min(10.0, b["confiance"] + 0.1), 2)
        # Emplacement mémorisé produit par produit : plusieurs produits d'un même besoin peuvent être dans des rayons
        # différents sans avoir été déplacés (avant : un seul rayon par besoin, d'où de faux « produits déplacés »).
        for it in perceived["articles"]:
            b["plan_connu"][it["produit"]] = store.planogram[it["produit"]]["rayon"]
        return facts

    def weekly_decay(self) -> None:
        for place, b in self.beliefs.items():
            b["habitude"] = round(b["habitude"] * 0.9, 3)
        self.state["lassitude_promos"] = round(self.state["lassitude_promos"] * 0.85, 3)
        for s in self.state["lassitude_messages"]:
            self.state["lassitude_messages"][s] = round(self.state["lassitude_messages"][s] * 0.7, 3)
        self.state["manques"] = 0
        self.week_log = []
