"""Le monde : magasins, clients, grossiste, météo, avis et informations publiques."""
from __future__ import annotations

import math
from copy import deepcopy

from . import temps
from .catalogue import PANIER_REFERENCE, is_allowed, products_for_need
from .clients import Customer
from .fournisseur import Supplier
from .magasin import Store
from .population import generate_population, make_variant, new_household
from .rng import RNG


GAMME_POIDS = {"mdd": 1.0, "marque": 1.1, "premium": 0.5, "bio": 0.5}


def expected_demand(customers) -> dict[str, float]:
    """Unités vendues par jour attendues pour chaque produit (toutes boutiques confondues)."""
    demand: dict[str, float] = {}
    for c in customers:
        for need, rate in c.daily.items():
            prods = [p for p in products_for_need(need) if is_allowed(p, c.attrs)]
            if rate <= 0 or not prods:
                continue
            ws = {p.id: GAMME_POIDS[p.gamme] * math.exp(c.attrs.get("gouts", {}).get(p.id, 0.0)) for p in prods}
            tot = sum(ws.values())
            for p in prods:
                demand[p.id] = demand.get(p.id, 0.0) + rate * ws[p.id] / tot / p.portions
    return demand


class World:
    def __init__(self, cfg: dict, journal=None, ia=None):
        self.cfg = cfg
        self.journal = journal
        self.ia = ia          # passerelle vers les modèles (None en mode sans IA)
        self.rng = RNG(cfg["graine"])
        self.day = 0
        self.supplier = Supplier()
        self.stores: dict[str, Store] = {m["id"]: Store(m["id"], m["nom"], cfg, self.supplier) for m in cfg["magasins"]}
        people = generate_population(int(cfg["foyers"]), self.rng.stream("population"))
        init = self.rng.stream("clients_init")
        self.customers: dict[str, Customer] = {a["id"]: Customer(a, list(self.stores), init) for a in people}
        self.next_customer = len(people) + 1
        # Foyers variantes : chaque foyer piloté par le modèle en entraîne k autres, sans appel supplémentaire au modèle.
        # Tirages séparés : les foyers-sources sont exactement les mêmes qu'avec k = 0.
        self.variantes: dict[str, list[str]] = {}
        k = int((cfg.get("clients") or {}).get("variantes_par_foyer", 0) or 0)
        if k > 0:
            vrnd = self.rng.stream("variantes")
            spread = float(cfg["clients"].get("ecart_variantes", 0.08))
            for a in people:
                for j in range(k):
                    va = make_variant(a, f"{a['id']}{chr(ord('b') + j)}", vrnd, spread)
                    self.customers[va["id"]] = Customer(va, list(self.stores), vrnd)
                    self.variantes.setdefault(a["id"], []).append(va["id"])
        if (cfg.get("clients") or {}).get("garde_manger_initial", "equilibre") == "equilibre":
            # Départ en régime de croisière : les grosses courses remettent les placards à leur capacité, si bien qu'en
            # régime établi ils sont pleins à 95-100 %. Partir à 30-80 % remplissait tous les placards la première
            # semaine (pic de ventes), puis l'épicerie « s'effondrait » en semaines 2 et 3 : une illusion qui faussait les
            # premiers tests de prix des dirigeants (partie dynamique_claude_7s).
            prnd = self.rng.stream("garde_manger_initial")
            for c in self.customers.values():
                for need, rate in c.daily.items():
                    if rate > 0:
                        c.state["garde_manger"][need] = round(c.capacity(need) * prnd.uniform(0.8, 1.0), 2)
        demand = expected_demand(self.customers.values())
        for st in self.stores.values():
            st.init_stock({k: v / len(self.stores) for k, v in demand.items()})
        self.weather, self.heat = "soleil", False
        self.reviews: list[dict] = []
        self.review_seq = 0
        self.news: list[tuple[int, str]] = []
        self.public_flyers: list[dict] = []
        self.public_ads: list[dict] = []
        self.refs = list(PANIER_REFERENCE)
        self.pending_price_surveys: list[tuple[str, list[str]]] = []
        self.manager_journals: dict[str, list[tuple[int, str]]] = {s: [] for s in self.stores}
        self.week_started: set[int] = set()
        self.moderation_memo: dict = {}
        self.finished = False

    # -------------------------------------------------------------- persistance
    def __getstate__(self):
        d = dict(self.__dict__)
        d["journal"] = None
        d["ia"] = None
        d.pop("codex", None)
        return d

    def __setstate__(self, d):
        d.pop("codex", None)  # sauvegardes antérieures au moteur Claude
        d.setdefault("ia", None)
        d.setdefault("moderation_memo", {})
        d.setdefault("variantes", {})
        self.__dict__.update(d)

    # -------------------------------------------------------------- utilitaires
    def active_customers(self) -> list[Customer]:
        return [c for c in self.customers.values() if c.active]

    def place_name(self, place: str) -> str:
        if place in self.stores:
            return self.stores[place].nom
        return {"hyper": "hypermarché", "marche": "marché du samedi", "maison": "maison"}.get(place, place)

    def weather_text(self) -> str:
        return {"soleil": "beau temps", "nuageux": "nuageux", "pluie": "pluie"}[self.weather] + (" (canicule)" if self.heat else "")

    def public_information(self, sid: str) -> dict:
        frozen = getattr(self, "_competition_view", None)
        if frozen is not None:
            return frozen[sid]
        store = self.stores[sid]
        return {"id": sid, "nom": store.nom, "slogan": store.slogan,
                "horaires": [[s for s in temps.slots(self.cfg) if store.is_open(d, s)] for d in range(self.day, self.day + 7)],
                "prospectus": [f for f in self.public_flyers if f["magasin"] == sid],
                "publicites": [a for a in self.public_ads if a["magasin"] == sid],
                "avis": self.reviews}

    def freeze_competition(self):
        self._competition_view = deepcopy({sid: self.public_information(sid) for sid in self.stores})
        self._pending_competitor_messages = []

    def deliver_competitor_message(self, sid: str, text: str):
        queue = getattr(self, "_pending_competitor_messages", None)
        if queue is None:
            self.stores[sid].inbox_from_competitor.append(text)
        else:
            queue.append((sid, text))

    def publish_competition(self):
        for sid, text in self._pending_competitor_messages:
            self.stores[sid].inbox_from_competitor.append(text)
        self._pending_competitor_messages = None
        self._competition_view = None

    def resolve_store(self, value: str) -> str | None:
        if not isinstance(value, str):
            return None
        label = value.strip().casefold()
        ids = [sid for sid in self.stores if sid.casefold() == label]
        names = [sid for sid, store in self.stores.items() if store.nom.casefold() == label]
        return ids[0] if ids else names[0] if len(names) == 1 else None

    def other_store(self, sid: str) -> Store:
        return [s for k, s in self.stores.items() if k != sid][0]

    def add_new_household(self, day: int) -> None:
        cid = f"C{self.next_customer:02d}"
        self.next_customer += 1
        a = new_household(cid, self.rng.stream("arrivees"), [c.attrs for c in self.customers.values()])
        c = Customer(a, list(self.stores), self.rng.stream("arrivees"), day)
        for sid in self.stores:
            c.beliefs[sid]["connait"] = False
            c.beliefs[sid]["habitude"] = 0.0
        c.remember(day, "Nouvel arrivant dans la ville : il faut trouver ses magasins.", 8, None, ("vie",))
        self.customers[cid] = c
        if self.journal:
            self.journal.event(day, "arrivee", cid, {"texte": "Nouveau foyer dans la ville", "persona": a["prenom"]})

    def daily_weather(self) -> None:
        self.weather, self.heat = temps.next_weather(self.weather, self.day, self.rng.stream("meteo"))
