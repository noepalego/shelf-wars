"""Magasin : prix, stocks par lots, rayons, promotions, coupons, fidélité, caisse et comptabilité."""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field

from . import systemes, temps
from .catalogue import ASSORTIMENT_INITIAL, NIVEAUX, PRODUCTS, RAYONS

NEVER = 10 ** 9


def _dd_dict():
    return {}


# Fabriques nommées (et non des lambdas) pour que l'état reste sauvegardable avec pickle.
def _dd_float():
    return defaultdict(float)


def _dd_int():
    return defaultdict(int)


def _sales_entry():
    return [0, 0.0, 0.0]


def _dd_sales():
    return defaultdict(_sales_entry)


def _waste_entry():
    return [0, 0.0]


def _dd_waste():
    return defaultdict(_waste_entry)


@dataclass
class Lot:
    qty: int
    expiry: int        # dernier jour de vente possible
    unit_cost: float
    reserve: bool = False  # livré mais pas encore mis en rayon (invisible et invendable)


@dataclass
class Promotion:
    id: str
    produits: list
    mecanique: str     # pourcentage | x_pour_y | deuxieme_moitie
    valeur: float      # % de remise, ou X pour « X pour Y »
    y: int             # Y pour « X pour Y »
    debut: int
    fin: int
    affiche: bool

    def label(self) -> str:
        if self.mecanique == "pourcentage":
            return f"PROMO -{self.valeur:.0f} %"
        if self.mecanique == "x_pour_y":
            return f"PROMO {int(self.valeur)} pour le prix de {self.y}"
        return "PROMO 2e à -50 %"

    def total(self, unit: float, qty: int) -> float:
        if self.mecanique == "pourcentage":
            return unit * qty * (1 - self.valeur / 100)
        if self.mecanique == "x_pour_y":
            x, y = int(self.valeur), self.y
            free = (qty // x) * (x - y) if x > y > 0 else 0
            return unit * (qty - free)
        return unit * (qty - qty // 2 * 0.5)

    def discount_rate(self) -> float:
        if self.mecanique == "pourcentage":
            return self.valeur / 100
        if self.mecanique == "x_pour_y":
            return (self.valeur - self.y) / self.valeur if self.valeur else 0.0
        return 0.25


@dataclass
class Coupon:
    id: str
    magasin: str
    offre: str         # remise_pct_produit | remise_pct_panier | remise_euros_panier | produit_offert | points_x2 | parrainage | filleul
    valeur: float
    produit: str | None
    panier_min: float
    debut: int
    fin: int
    detenteur: str | None
    canal: str         # prospectus | email | sms | carte | caisse | bouche_a_oreille
    texte: str
    campagne: str = ""
    parrain: str | None = None
    utilise: bool = False
    jour_utilisation: int | None = None
    declencheur_produit: str | None = None
    quantite_min: int = 0

    def describe(self) -> str:
        p = PRODUCTS[self.produit].nom if self.produit in PRODUCTS else ""
        if self.offre == "remise_pct_produit":
            s = f"-{self.valeur:.0f} % sur {p}"
        elif self.offre == "remise_pct_panier":
            s = f"-{self.valeur:.0f} % sur le panier"
        elif self.offre in ("remise_euros_panier", "filleul"):
            s = f"-{self.valeur:.2f} € sur le panier".replace(".", ",")
        elif self.offre == "produit_offert":
            s = f"{p} offert"
        elif self.offre == "points_x2":
            s = "points de fidélité doublés"
        else:
            s = f"parrainage : {self.valeur:.0f} € pour vous et pour la personne parrainée"
        if self.panier_min > 0:
            s += f" dès {self.panier_min:.0f} € d'achat"
        if self.declencheur_produit:
            s += f" avec {self.quantite_min or 1} × {PRODUCTS[self.declencheur_produit].nom}"
        return s


def payment_of(customer, cfg: dict) -> tuple[str, str]:
    """Moyen de paiement d'un foyer, tel que la caisse le voit : « especes », ou « carte » avec les 4 derniers chiffres
    de sa carte bancaire (toujours les mêmes pour ce foyer). C'est ce qu'un vrai commerçant lit sur son terminal : rien
    sur la personne, mais de quoi reconnaître un même payeur d'un passage à l'autre, sans carte de fidélité."""
    if not (cfg.get("donnees") or {}).get("moyen_paiement", True):
        return "", ""
    a = customer.attrs
    h = hashlib.sha256(f"paiement|{customer.id}".encode()).hexdigest()
    cash = int(h[:8], 16) / 16 ** 8 < 0.10 + 0.30 * (a.get("age", 40) >= 70) + 0.10 * (a.get("niveau_revenu", 0.5) < 0.25)
    return ("especes", "") if cash else ("carte", f"•••• {int(h[8:16], 16) % 10000:04d}")


@dataclass
class Member:
    id: str
    client: str
    jour_inscription: int
    donnees: dict
    consent_email: bool
    consent_sms: bool
    points: float = 0.0
    visites: int = 0
    total_depense: float = 0.0
    derniere_visite: int = -1
    desabonne_email: bool = False
    desabonne_sms: bool = False
    notes: str = ""
    tags: list = field(default_factory=list)


def default_planogram() -> dict:
    plan = {}
    by_aisle = defaultdict(list)
    for pid, p in PRODUCTS.items():
        by_aisle[p.rayon].append(pid)
    order = {"mdd": 0, "marque": 1, "premium": 2, "bio": 3}
    for aisle, pids in by_aisle.items():
        pids.sort(key=lambda x: (PRODUCTS[x].besoin, order[PRODUCTS[x].gamme], x))
        for i, pid in enumerate(pids):
            seg, lvl = divmod(i, 4)
            plan[pid] = {"rayon": aisle, "segment": seg, "niveau": ["yeux", "mains", "haut", "bas"][lvl], "facings": 1}
    return plan


class Store:
    def __init__(self, sid: str, name: str, cfg: dict, supplier, day0: int = 0):
        eco = cfg["economie"]
        self.id = sid
        self.nom = name
        self.slogan = ""
        self.cfg = cfg
        self.cash = float(eco["tresorerie_initiale"])
        self.cash0 = self.cash
        self.profit = 0.0
        self.bankrupt = False
        self.esl = False
        self.listed = {pid: pid in ASSORTIMENT_INITIAL for pid in PRODUCTS}
        self.prices = {pid: p.prix_conseille for pid, p in PRODUCTS.items()}
        self.member_prices: dict[str, float] = {}
        self.lots: dict[str, list[Lot]] = {pid: [] for pid in PRODUCTS}
        self.on_order: list[tuple[int, str, int, float]] = []
        self.targets = {pid: self._initial_target(pid) for pid in PRODUCTS}
        self.planogram = default_planogram()
        self.secondary: dict[str, list[str]] = {"entree": [], "tete_gondole_1": [], "tete_gondole_2": [], "caisse": []}
        self.layout_version: dict[int, int] = {r: 0 for r in RAYONS}  # change quand on déplace une catégorie
        self.promotions: list[Promotion] = []
        self.coupons: dict[str, Coupon] = {}
        self.coupon_seq = 0
        self.promo_seq = 0
        self.campaigns: list[dict] = []
        self.bounce_back: list[dict] = []   # coupons distribués en caisse pendant une période
        self.loyalty = {"actif": False, "points_par_euro": 1.0, "valeur_point": 0.01, "bonus_inscription": 0,
                        "donnees_demandees": ["age", "quartier"], "seuil_utilisation": 500}
        self.members: dict[str, Member] = {}
        self.member_seq = 0
        self.checkouts = {(dt, s): (2 if dt == "samedi" and s in ("matin", "soir") else 1)
                          for dt in ("semaine", "samedi", "dimanche") for s in ("matin", "midi", "soir", "jour")}
        self.short_date_rule: dict | None = None
        self.slot_prices: dict[tuple[int, str], dict[str, float]] = {}
        self.automations: dict[str, dict] = {}
        self.automation_memory: dict = {}
        self.price_survey: dict = {}
        self.pending_survey_request: list[str] = []
        self.survey: dict | None = None
        self.survey_answers: list[dict] = []
        self.inbox_from_competitor: list[str] = []
        self.report_notes: list[str] = []
        self.rejected: list[str] = []
        self.ledger: dict[int, dict[str, float]] = defaultdict(_dd_float)
        self.sales: dict[int, dict[str, list]] = defaultdict(_dd_sales)  # quantité, chiffre d'affaires, coût
        self.visits: dict[int, dict[str, int]] = defaultdict(_dd_int)
        self.visitors: dict[int, set] = defaultdict(set)
        self.stockouts: dict[int, set] = defaultdict(set)
        self.waste: dict[int, dict[str, list]] = defaultdict(_dd_waste)
        self.price_history: dict[str, list] = {pid: [(day0, self.prices[pid])] for pid in PRODUCTS}
        self.tickets: list[dict] = []
        self.ticket_seq = 0
        self.wait_minutes: dict[int, list] = defaultdict(list)
        self.recent_share = 0.5
        self.supplier = supplier
        self._init_extensions()

    # Attributs ajoutés après les premières versions : aussi posés à la reprise d'une ancienne sauvegarde.
    def _init_extensions(self) -> None:
        d = self.__dict__
        d.setdefault("team", ["gerant"])               # rôles de l'équipe de direction (voir config gerants.equipe)
        d.setdefault("staff", [])                      # salariés (voir personnel.py)
        d.setdefault("staff_seq", 0)
        d.setdefault("day_plan", {})
        d.setdefault("work_log", defaultdict(_dd_dict))
        d.setdefault("shelving_log", defaultdict(_dd_dict))
        d.setdefault("systems", {})                    # équipements et services installés (projets)
        d.setdefault("system_data", {})
        d.setdefault("system_consents", {})
        d.setdefault("slot_waits", defaultdict(list))
        d.setdefault("devis", {})                      # devis de l'arbitre (inventions, missions de sous-agents)
        d.setdefault("devis_seq", 0)
        d.setdefault("missions", {})                   # missions confiées à des sous-agents
        d.setdefault("mission_seq", 0)
        d.setdefault("missions_a_executer", [])
        d.setdefault("program_outputs", {})            # tables publiées par les programmes (ctx.publier)
        d.setdefault("automation_versions", {})        # toutes les versions de chaque programme
        d.setdefault("price_changes", [])              # changements de prix en attente des programmes
        d.setdefault("evenements_internes", [])        # événements du magasin en attente des programmes
        d.setdefault("domaines_stricts", False)        # chaque dirigeant limité à son domaine (configuration)
        d.setdefault("individual_prices", {})          # prix individuels : membre → {produit: prix}

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._init_extensions()

    # ------------------------------------------------------------------ équipements
    def effect(self, kind: str) -> float:
        return systemes.effect_value(self.systems, kind)

    def attraction_for(self, attrs: dict) -> float:
        return systemes.attraction(self.systems, attrs)

    def is_open(self, day: int, slot: str) -> bool:
        date = temps.date_of(day)
        if (date.month, date.day) in ((12, 25), (1, 1), (5, 1)):
            return False
        return getattr(self, "hours", {}).get((temps.day_type(day), slot), temps.store_open(day, slot))

    def init_stock(self, daily_demand: dict[str, float], day0: int = 0) -> None:
        """Stocks cibles de départ calés sur la demande attendue (et sur la durée de conservation)."""
        for pid, p in PRODUCTS.items():
            d = daily_demand.get(pid, 0.0)
            if p.conservation_j <= 14:
                self.targets[pid] = max(2, math.ceil(d * min(p.conservation_j + 0.5, 5) * 1.3) + 1)
            else:
                self.targets[pid] = max(3, math.ceil(d * 10) + 2)
            if self.listed[pid]:
                self._add_lot(pid, self.targets[pid], day0, self.supplier.prices[pid])
        self.inv0 = self.inventory_value()

    # ------------------------------------------------------------------ stock
    def _initial_target(self, pid: str) -> int:
        p = PRODUCTS[pid]
        base = {"pain": 22, "lait": 30, "fruits": 14, "legumes": 12, "proteines": 10, "laitiers": 10, "oeufs": 10,
                "feculents": 12, "petit_dej": 8, "snacks": 8, "boissons": 12, "hygiene": 8, "entretien": 5,
                "bebe": 4, "animaux": 3, "plats": 6, "epicerie": 6}.get(p.besoin, 8)
        if p.gamme in ("premium", "bio"):
            base = max(3, base // 2)
        return base

    def _add_lot(self, pid: str, qty: int, day: int, unit_cost: float, reserve: bool = False) -> None:
        if qty <= 0:
            return
        p = PRODUCTS[pid]
        expiry = day + p.conservation_j if p.conservation_j < 5000 else NEVER
        if p.conservation_j <= 14:
            expiry += int(self.effect("conservation_frais"))
        self.lots[pid].append(Lot(int(qty), expiry, round(unit_cost, 4), reserve))
        self.lots[pid].sort(key=lambda l: l.expiry)

    def available(self, pid: str, day: int) -> int:
        """Stock total (rayon + réserve)."""
        return sum(l.qty for l in self.lots[pid] if l.expiry >= day)

    def on_shelf(self, pid: str, day: int) -> int:
        """Ce que les clients trouvent en rayon."""
        return sum(l.qty for l in self.lots[pid] if l.expiry >= day and not l.reserve)

    def in_reserve(self, pid: str, day: int) -> int:
        return sum(l.qty for l in self.lots[pid] if l.expiry >= day and l.reserve)

    def shelve(self, day: int, capacity: int) -> int:
        """Met en rayon jusqu'à `capacity` articles de la réserve : rayons vides d'abord, puis les plus périssables."""
        if capacity <= 0:
            return 0
        waiting = [(pid, l) for pid, lots in self.lots.items() for l in lots if l.reserve and l.qty > 0 and l.expiry >= day]
        if not waiting:
            return 0
        waiting.sort(key=lambda x: (self.on_shelf(x[0], day) > 0, x[1].expiry))
        moved = 0
        for pid, lot in waiting:
            if moved >= capacity:
                break
            k = min(lot.qty, capacity - moved)
            if k == lot.qty:
                lot.reserve = False
            else:
                lot.qty -= k
                self.lots[pid].append(Lot(k, lot.expiry, lot.unit_cost, False))
                self.lots[pid].sort(key=lambda l: l.expiry)
            moved += k
        log = self.shelving_log[day]
        log["mis_en_rayon"] = log.get("mis_en_rayon", 0) + moved
        return moved

    def short_date_qty(self, pid: str, day: int) -> int:
        r = self.short_date_rule
        if not r:
            return 0
        lim = day + int(r["jours_avant_peremption"])
        return sum(l.qty for l in self.lots[pid] if day <= l.expiry <= lim and not l.reserve)

    def take(self, pid: str, qty: int, day: int) -> tuple[int, float]:
        """Retire du rayon (premiers périmés d'abord). Renvoie (quantité prise, coût)."""
        taken, cost = 0, 0.0
        for lot in self.lots[pid]:
            if lot.expiry < day or lot.qty <= 0 or lot.reserve:
                continue
            k = min(lot.qty, qty - taken)
            lot.qty -= k
            taken += k
            cost += k * lot.unit_cost
            if taken >= qty:
                break
        self.lots[pid] = [l for l in self.lots[pid] if l.qty > 0]
        return taken, cost

    def inventory_value(self) -> float:
        return sum(l.qty * l.unit_cost for lots in self.lots.values() for l in lots)

    def expire(self, day: int) -> None:
        for pid, lots in self.lots.items():
            keep = []
            for l in lots:
                if l.expiry < day:
                    w = self.waste[day][pid]
                    w[0] += l.qty
                    w[1] += l.qty * l.unit_cost
                    self.profit -= l.qty * l.unit_cost
                    self.ledger[day]["demarque"] += l.qty * l.unit_cost
                else:
                    keep.append(l)
            self.lots[pid] = keep

    def receive(self, day: int) -> list[str]:
        notes = []
        arrived = [o for o in self.on_order if o[0] <= day]
        self.on_order = [o for o in self.on_order if o[0] > day]
        for _, pid, qty, unit in arrived:
            self._add_lot(pid, qty, day, unit, reserve=True)
            self.pay(day, "achats_marchandises", qty * unit, cash_only=True)
        return notes

    def cold_space(self, day: int) -> int | None:
        capacity = self.cfg.get("marche_local", {}).get("capacite_froid_par_foyer")
        if not capacity:
            return None
        booked = sum(self.available(p, day + 1) for p, product in PRODUCTS.items() if product.frais)
        booked += sum(q for _, p, q, _ in self.on_order if PRODUCTS[p].frais)
        households = self.cfg["foyers"] * (1 + int((self.cfg.get("clients") or {}).get("variantes_par_foyer", 0) or 0))
        return max(0, households * capacity - booked)

    def order(self, pid: str, qty: int, day: int, supplier, source="grossiste") -> tuple[bool, str]:
        if qty <= 0:
            return False, "quantité nulle"
        world_settings = self.cfg.get("marche_local", {})
        space = self.cold_space(day) if PRODUCTS[pid].frais else None
        if space is not None and qty > space:
            return False, f"capacité de stockage du frais insuffisante ({space} articles libres)"
        if source == "local":
            offer = supplier.local_offer(pid) if world_settings.get("fournisseur_local") else None
            if offer is None:
                return False, "produit indisponible chez le fournisseur local"
            orders = getattr(self, "_local_orders", (day, 0))
            ordered = orders[1] if orders[0] == day else 0
            if qty + ordered > offer["maximum_jour"]:
                return False, "capacité de livraison locale dépassée"
            self._local_orders = (day, ordered + qty)
            unit, delay = offer["prix"], offer["delai"]
        elif source == "grossiste":
            minimum = supplier.wholesale_minimum(pid) if world_settings.get("fournisseur_local") else 1
            if qty < minimum:
                return False, f"lot minimum du grossiste : {minimum}"
            if not supplier.can_deliver(pid, day):
                return False, f"{PRODUCTS[pid].nom} : rupture chez le grossiste"
            unit = supplier.unit_cost(pid, qty)
            delay = 1 if PRODUCTS[pid].frais else 2
        else:
            return False, "fournisseur inconnu"
        self.on_order.append((day + delay, pid, int(qty), unit))
        return True, ""

    def replenish(self, day: int, supplier) -> None:
        """Réassort automatique du soir vers les stocks cibles."""
        for pid, listed in self.listed.items():
            if not listed:
                continue
            pos = self.available(pid, day + 1) + sum(q for _, p, q, _ in self.on_order if p == pid)
            need = self.targets.get(pid, 0) - pos
            if need > 0:
                minimum = supplier.wholesale_minimum(pid) if self.cfg.get("marche_local", {}).get("fournisseur_local") else 1
                self.order(pid, max(need, minimum), day, supplier)

    # ------------------------------------------------------------------ prix
    def base_price(self, pid: str, day: int, slot: str) -> float:
        over = self.slot_prices.get((day, slot), {})
        return over.get(pid, self.prices[pid])

    def promo_for(self, pid: str, day: int) -> Promotion | None:
        best = None
        for pr in self.promotions:
            if pr.debut <= day <= pr.fin and pid in pr.produits:
                if best is None or pr.discount_rate() > best.discount_rate():
                    best = pr
        return best

    def shelf_offer(self, pid: str, day: int, slot: str, member) -> dict:
        """Offre vue par un client. `member` : fiche de membre (ou vrai/faux). Un membre identifié voit son prix
        individuel s'il en a un (plus haut ou plus bas que le prix en rayon), sinon le prix membre s'il est plus bas."""
        price = self.base_price(pid, day, slot)
        card = bool(member) and self.loyalty["actif"]
        mp = self.member_prices.get(pid) if card else None
        personal = self.individual_prices.get(getattr(member, "id", None), {}).get(pid) if card else None
        promo = self.promo_for(pid, day)
        if personal is not None:
            price, mp = personal, None
        offer = {"prix": round(price, 2), "prix_membre": round(mp, 2) if mp is not None and mp < price else None,
                 "promo": promo.label() if promo else None, "affiche": bool(promo and promo.affiche),
                 "mecanique_promo": promo.mecanique if promo else None,
                 "valeur_promo": promo.valeur if promo else 0, "y_promo": promo.y if promo else 0}
        if self.short_date_rule and self.short_date_qty(pid, day) > 0:
            offer["date_courte"] = round(price * (1 - self.short_date_rule["remise_pct"] / 100), 2)
            offer["qte_date_courte"] = self.short_date_qty(pid, day)
        return offer

    def set_price(self, pid: str, price: float, day: int, charge_label: bool = True) -> None:
        price = round(price, 2)
        if abs(price - self.prices[pid]) < 0.005:
            return
        old = self.prices[pid]
        self.prices[pid] = price
        self.price_history[pid].append((day, price))
        self.price_changes.append({"jour": day, "produit": pid, "ancien_prix": old, "nouveau_prix": price})
        del self.price_changes[:-200]
        if charge_label and not self.esl:
            self.pay(day, "etiquettes", self.cfg["economie"]["cout_etiquette"])

    # ------------------------------------------------------------------ argent
    def pay(self, day: int, kind: str, amount: float, cash_only: bool = False) -> None:
        if amount <= 0:
            return
        self.cash -= amount
        self.ledger[day][kind] += amount
        if not cash_only:
            self.profit -= amount

    def invest(self, day: int, kind: str, cost: float):
        life = self.cfg["economie"].get("duree_amortissement_jours", 0)
        self.pay(day, kind, cost, cash_only=life > 0)
        if life > 0 and cost > 0:
            self.ledger[day]["acquisitions_immobilisees"] += cost
            self.assets = getattr(self, "assets", [])
            self.assets.append({"debut": day, "cout": cost, "duree": life, "amorti": 0.0})

    def net_assets(self):
        return round(sum(asset["cout"] - asset["amorti"] for asset in getattr(self, "assets", [])), 2)

    def daily_costs(self, day: int) -> None:
        eco = self.cfg["economie"]
        if self.bankrupt:
            return
        for asset in getattr(self, "assets", []):
            elapsed = min(asset["duree"], day - asset["debut"] + 1)
            amortized = round(asset["cout"] * elapsed / asset["duree"], 2)
            charge = round(amortized - asset["amorti"], 2)
            if charge > 0:
                asset["amorti"] = amortized
                self.profit -= charge
                self.ledger[day]["amortissements"] += charge
        self.pay(day, "charges_fixes", eco["charges_fixes_jour"])
        added = sum(self.is_open(day, s) and not temps.store_open(day, s) for s in temps.slots(self.cfg))
        self.pay(day, "horaires_etendus", added * eco.get("cout_ouverture_hors_horaire", 0))
        plan = self.day_plan if self.day_plan.get("jour") == day else None
        if plan is None:  # sans planning du jour (anciens appels) : chaque caisse en plus est tenue par un intérimaire
            dt = temps.day_type(day)
            extra = sum(max(0, self.checkouts.get((dt, s), 1) - 1) for s in temps.slots(self.cfg) if self.is_open(day, s))
        else:
            extra = plan["creneaux_interim"]
            self.pay(day, "salaires", plan["salaires"])
        self.pay(day, "caisses_supplementaires", extra * eco["cout_caisse_supplementaire"])
        if self.loyalty["actif"]:
            self.pay(day, "programme_fidelite", eco["cout_programme_fidelite_semaine"] / 7)
        weekly = sum(float(s.get("cout_hebdomadaire", 0)) for s in systemes.active(self.systems))
        if weekly:
            self.pay(day, "systemes", weekly / 7)
        cogs = self.ledger.get(day, {}).get("cout_des_ventes", 0.0)
        if cogs > 0:
            presence = 0.8 if plan and plan.get("rayon_present") else 1.0
            self.pay(day, "demarque_inconnue", cogs * eco.get("demarque_inconnue", 0.0) * presence * self.effect("vol"))
        if self.cash < 0:
            self.pay(day, "agios", -self.cash * eco["taux_decouvert_jour"])

    # ------------------------------------------------------------------ fidélité
    def _declares(self, customer, champ: str, gene: float) -> bool:
        """Le foyer remplit-il ce champ de la fiche ? Plus il protège ses données et plus la question est indiscrète,
        plus il laisse la case vide. Tirage stable (même foyer, même champ, même magasin)."""
        u = int(hashlib.sha256(f"{self.id}|{customer.id}|{champ}".encode()).hexdigest()[:8], 16) / 16 ** 8
        return u >= gene * customer.attrs.get("souci_vie_privee", 0.3)

    def known_by(self, customer) -> str:
        """Réponse du foyer à « comment nous avez-vous connus ? », d'après ce qu'il a réellement reçu ou entendu."""
        seen = [m for m in getattr(customer, "inbox", []) if m.get("magasin") == self.id]
        for canal, label in (("bouche_a_oreille", "par un proche"), ("prospectus", "par un prospectus"),
                             ("publicité", "par une publicité"), ("email", "par un e-mail"), ("sms", "par un SMS")):
            if any(str(m.get("canal", "")).startswith(canal) for m in seen):
                return label
        if any(self.id == m.get("magasin") and "bouche_a_oreille" in m.get("tags", []) for m in getattr(customer, "memory", [])):
            return "par un proche"
        return "proche de chez moi ou sur mon trajet"

    def join(self, customer, day: int) -> Member:
        self.member_seq += 1
        a = customer.attrs
        data = {}
        for k in self.loyalty["donnees_demandees"]:
            if k == "age":
                data["tranche_age"] = f"{(a['age'] // 10) * 10}-{(a['age'] // 10) * 10 + 9} ans"
            elif k == "quartier":
                data["quartier"] = a["quartier"]
            elif k == "foyer":
                data["taille_foyer"] = a["taille_foyer"]
                data["enfants"] = a["nb_enfants"]
            elif k == "email":
                data["email"] = f"{a['prenom'].lower()}.{customer.id.lower()}@exemple.fr"
            elif k == "telephone":
                data["telephone"] = "06 •• •• •• " + customer.id[-2:]
            # Champs facultatifs : déclarés par le foyer, ou laissés vides (« non communiqué ») selon sa discrétion.
            elif k == "profession":
                data["profession"] = a.get("metier", "") if self._declares(customer, k, 0.5) else "non communiqué"
            elif k == "revenus":
                lo = int(a.get("revenu_mensuel", 0) // 500 * 500)
                data["revenus_mensuels"] = (f"{lo}-{lo + 499} €" if self._declares(customer, k, 1.2) else "non communiqué")
            elif k == "vehicule":
                data["vehicule"] = ("oui" if a.get("voiture") else "non") if self._declares(customer, k, 0.3) else "non communiqué"
            elif k == "animaux":
                data["animaux"] = a.get("animal", "aucun") if self._declares(customer, k, 0.3) else "non communiqué"
            elif k == "regime":
                data["regime_alimentaire"] = a.get("regime", "") if self._declares(customer, k, 0.5) else "non communiqué"
            elif k == "connu_par":
                data["connu_par"] = self.known_by(customer)
        consent_email = "email" in self.loyalty["donnees_demandees"] and customer.state.get("_consent_email", False)
        consent_sms = "telephone" in self.loyalty["donnees_demandees"] and customer.state.get("_consent_sms", False)
        m = Member(id=f"{self.id}-{self.member_seq:04d}", client=customer.id, jour_inscription=day, donnees=data,
                   consent_email=consent_email, consent_sms=consent_sms, points=float(self.loyalty["bonus_inscription"]))
        self.members[customer.id] = m
        return m

    def member_of(self, cid: str) -> Member | None:
        return self.members.get(cid)

    def member_by_id(self, mid: str) -> Member | None:
        for m in self.members.values():
            if m.id == mid:
                return m
        return None

    # ------------------------------------------------------------------ coupons
    def new_coupon(self, holder: str | None, offre: str, valeur: float, produit, panier_min: float, debut: int,
                   fin: int, canal: str, texte: str, campagne: str = "", parrain: str | None = None, *, declencheur_produit=None, quantite_min=0) -> Coupon:
        self.coupon_seq += 1
        c = Coupon(id=f"{self.id}-K{self.coupon_seq:05d}", magasin=self.id, offre=offre, valeur=float(valeur),
                   produit=produit, panier_min=float(panier_min or 0), debut=debut, fin=fin, detenteur=holder,
                   canal=canal, texte=texte, campagne=campagne, parrain=parrain, declencheur_produit=declencheur_produit, quantite_min=quantite_min)
        self.coupons[c.id] = c
        return c

    # ------------------------------------------------------------------ caisse
    def wait_time(self, day: int, slot: str, rnd) -> float:
        base = {"matin": 2.0, "midi": 4.0, "soir": 6.0, "jour": 4.0}[slot]
        dt = temps.day_type(day)
        if dt == "samedi":
            base *= 1.6
        elif dt == "dimanche":
            base *= 1.3
        plan = self.day_plan if self.day_plan.get("jour") == day else {}
        n = max(1, plan.get("caisses", {}).get(slot) or self.checkouts.get((dt, slot), 1))
        w = base * (0.6 + self.recent_share) / n * self.effect("vitesse_caisse")
        wait = max(0.0, round(rnd.gauss(w, w * 0.3), 1))
        self.slot_waits[(day, slot)].append(wait)
        return wait

    def quote_lines(self, items, day, slot, member):
        lines, requested, short_requested = [], defaultdict(int), defaultdict(int)
        for item in sorted(items, key=lambda item: not item.get("date_courte", False)):
            pid = item["produit"]
            if not self.listed.get(pid) or item["quantite"] <= 0:
                continue
            offer = self.shelf_offer(pid, day, slot, member)
            qty = min(item["quantite"], self.on_shelf(pid, day) - requested[pid])
            short = item.get("date_courte", False) and "date_courte" in offer
            unit = offer["prix_membre"] or offer["prix"]
            if short:
                qty = min(qty, offer["qte_date_courte"] - short_requested[pid])
                unit = offer["date_courte"]
            if qty <= 0:
                continue
            requested[pid] += qty
            if short:
                short_requested[pid] += qty
            promo = None if short else self.promo_for(pid, day)
            total = promo.total(unit, qty) if promo else unit * qty
            lines.append({"produit": pid, "quantite": qty, "prix_unitaire": round(unit, 2), "total": round(total, 2),
                          "promo": promo.label() if promo else None, "date_courte": short,
                          "cherche": item.get("cherche", True)})
        return lines

    def quote_discounts(self, lines, wallet, member):
        subtotal = round(sum(line["total"] for line in lines), 2)
        used, coupon_disc, double_points = [], 0.0, False
        committed = defaultdict(float)
        for coupon in wallet if lines else ():
            if len(used) >= 2:
                break
            if subtotal < coupon.panier_min:
                continue
            trigger = coupon.declencheur_produit
            if trigger and sum(line["quantite"] for line in lines if line["produit"] == trigger) < (coupon.quantite_min or 1):
                continue
            product_lines = [line for line in lines if line["produit"] == coupon.produit]
            if coupon.offre == "remise_pct_produit" and product_lines:
                discount = sum(line["total"] for line in product_lines) * coupon.valeur / 100
            elif coupon.offre == "remise_pct_panier":
                discount = (subtotal - coupon_disc) * coupon.valeur / 100
            elif coupon.offre in ("remise_euros_panier", "filleul"):
                discount = coupon.valeur
            elif coupon.offre == "produit_offert" and product_lines:
                discount = min(product_lines[0]["total"], product_lines[0]["prix_unitaire"])
            elif coupon.offre == "points_x2" and member:
                double_points = True
                discount = 0.0
            else:
                continue
            discount = round(min(discount, subtotal - coupon_disc), 2)
            trial = getattr(self, "coupon_trials", {}).get(coupon.campagne)
            if trial and trial["budget"] is not None:
                remaining = trial["budget"] - trial["depense"] - committed[coupon.campagne]
                if discount > remaining + 1e-9:
                    continue
            committed[coupon.campagne] += discount
            used.append((coupon, discount))
            coupon_disc += discount
        coupon_disc = round(coupon_disc, 2)
        after = subtotal - coupon_disc
        points_disc = 0.0
        if member and self.loyalty["actif"] and lines and member.points >= self.loyalty["seuil_utilisation"]:
            points_disc = round(min(after * 0.5, member.points * self.loyalty["valeur_point"]), 2)
        return subtotal, used, coupon_disc, points_disc, double_points

    def checkout(self, customer, items: list[dict], day: int, slot: str, rnd, *, budget=None) -> dict:
        member = self.member_of(customer.id)
        wallet = customer.valid_coupons(self, day)
        wallet = [c for c in wallet if c.canal != "prospectus" or rnd.random() <= 0.55 + 0.4 * customer.attrs["conscienciosite"]]
        lines = self.quote_lines(items, day, slot, member)
        wanted, found = defaultdict(int), defaultdict(int)
        for item in items:
            wanted[item["produit"]] += item["quantite"]
        for line in lines:
            found[line["produit"]] += line["quantite"]
        missing = [pid for pid, qty in wanted.items() if found[pid] < qty]
        self.stockouts[day].update(missing)
        subtotal, applied, coupon_disc, points_disc, double_points = self.quote_discounts(lines, wallet, member)
        while budget is not None and lines and round(subtotal - coupon_disc - points_disc, 2) > budget:
            idx = min(range(len(lines)), key=lambda i: (lines[i]["cherche"], -lines[i]["prix_unitaire"]))
            line = lines[idx]
            line["quantite"] -= 1
            if line["quantite"]:
                promo = None if line["date_courte"] else self.promo_for(line["produit"], day)
                unit, qty = line["prix_unitaire"], line["quantite"]
                line["total"] = round(promo.total(unit, qty) if promo else unit * qty, 2)
            else:
                lines.pop(idx)
            subtotal, applied, coupon_disc, points_disc, double_points = self.quote_discounts(lines, wallet, member)
        used = []
        for coupon, discount in applied:
            trial = getattr(self, "coupon_trials", {}).get(coupon.campagne)
            if trial:
                trial["depense"] += discount
            coupon.utilise, coupon.jour_utilisation = True, day
            used.append(coupon.id)
        cost_total = 0.0
        for line in lines:
            _, cost = self.take(line["produit"], line["quantite"], day)
            line["cout"] = round(cost, 4)
            line.pop("cherche")
            cost_total += cost
        total = round(max(0.0, subtotal - coupon_disc - points_disc), 2)
        points_earned = 0.0
        if member and self.loyalty["actif"] and lines:
            if self.loyalty["valeur_point"]:
                member.points = max(0.0, member.points - points_disc / self.loyalty["valeur_point"])
            points_earned = math.floor(total * self.loyalty["points_par_euro"] * (2 if double_points else 1))
            member.points += points_earned
            member.visites += 1
            member.total_depense += total
            member.derniere_visite = day
            hist = member.__dict__.setdefault("historique", [])  # (jour, créneau, montant) : 120 derniers passages
            hist.append((day, slot, round(total, 2)))
            del hist[:-120]
        # Comptabilité
        if lines and member:
            for trial in getattr(self, "coupon_trials", {}).values():
                group = trial["membres"].get(member.id)
                if group and trial["debut"] <= day <= trial["fin"]:
                    result = trial["resultats"][group]
                    result["foyers"].add(member.id)
                    result["visites"] += 1
                    result["ca"] += total
                    result["marge"] += total - cost_total
        if lines:
            self.cash += total
            self.profit += total - cost_total
            self.ledger[day]["ventes"] += total
            self.ledger[day]["cout_des_ventes"] += cost_total
            self.ledger[day]["remises_coupons"] += coupon_disc
            self.ledger[day]["remises_points"] += points_disc
            ratio = total / subtotal if subtotal > 0 else 0.0
            for l in lines:
                s = self.sales[day][l["produit"]]
                s[0] += l["quantite"]
                s[1] += l["total"] * ratio
                s[2] += l["cout"]
        self.ticket_seq += 1
        paiement, carte = payment_of(customer, self.cfg) if lines else ("", "")
        ticket = {"id": f"{self.id}-T{self.ticket_seq:06d}", "jour": day, "creneau": slot, "magasin": self.id,
                  "paiement": paiement, "carte_bancaire": carte,
                  "client": customer.id, "membre": member.id if member else None, "lignes": lines,
                  "sous_total": subtotal, "remise_coupons": coupon_disc, "remise_points": round(points_disc, 2),
                  "total": total, "coupons": used, "points_gagnes": points_earned, "manquants": missing,
                  "cout": round(cost_total, 2)}
        self.visits[day][slot] += 1
        self.visitors[day].add(customer.id)
        self.tickets.append(ticket)
        if len(self.tickets) > 9000:  # environ 6 semaines au moins pour une ville de 90 foyers
            self.tickets = self.tickets[-7000:]
        # Coupons distribués en caisse (bounce-back)
        for bb in self.bounce_back:
            if bb["debut"] <= day <= bb["fin"] and lines:
                c = self.new_coupon(customer.id, bb["offre"], bb["valeur"], bb.get("produit"), bb.get("panier_min", 0),
                                    day + 1, day + 1 + bb["jours_validite"], "caisse", bb.get("texte", ""), bb.get("campagne", ""),
                                    declencheur_produit=bb.get("declencheur_produit"), quantite_min=bb.get("quantite_min", 0))
                customer.receive_coupon(c)
        return ticket

    # ------------------------------------------------------------------ indicateurs
    def campaign_results(self):
        rows = []
        for campaign, trial in getattr(self, "coupon_trials", {}).items():
            for group, result in trial["resultats"].items():
                rows.append({"campagne": campaign, "groupe": group, "debut": trial["debut"], "fin": trial["fin"],
                             "foyers_assignes": sum(g == group for g in trial["membres"].values()),
                             "foyers_acheteurs": len(result["foyers"]), "visites": result["visites"],
                             "ca": round(result["ca"], 2), "marge": round(result["marge"], 2),
                             "budget": trial["budget"], "depense": round(trial["depense"], 2) if group == "traite" else 0})
        return rows

    def price_index(self, day: int, refs: list[str]) -> float:
        vals = [self.prices[p] / PRODUCTS[p].prix_conseille for p in refs if self.listed.get(p)]
        return round(sum(vals) / len(vals), 4) if vals else 1.0

    def margin_rate(self) -> float:
        rates = []
        for pid, listed in self.listed.items():
            if listed:
                cost = self.supplier.prices[pid]
                rates.append((self.prices[pid] - cost) / self.prices[pid])
        return round(sum(rates) / len(rates), 4) if rates else 0.0

    def week_totals(self, week: int) -> dict:
        days = range(week * 7, week * 7 + 7)
        tot = defaultdict(float)
        for d in days:
            for k, v in self.ledger.get(d, {}).items():
                tot[k] += v
        tot["visites"] = sum(sum(self.visits.get(d, {}).values()) for d in days)
        tot["clients_uniques"] = len(set().union(*[self.visitors.get(d, set()) for d in days]))
        return dict(tot)

    def layout_description(self, pid: str) -> str:
        loc = self.planogram.get(pid)
        if not loc:
            return "emplacement inconnu"
        extra = [k for k, v in self.secondary.items() if pid in v]
        lvl = {"bas": "tout en bas", "mains": "à hauteur des mains", "yeux": "à hauteur des yeux", "haut": "en haut"}[loc["niveau"]]
        desc = f"rayon « {RAYONS[loc['rayon']]} », {lvl}"
        if loc.get("facings", 1) >= 3:
            desc += ", large présence en rayon"
        names = {"entree": "à l'entrée", "tete_gondole_1": "en tête de gondole", "tete_gondole_2": "en tête de gondole", "caisse": "près des caisses"}
        for e in extra:
            desc += f", aussi {names[e]}"
        return desc

    def niveau_index(self, pid: str) -> int:
        loc = self.planogram.get(pid)
        return NIVEAUX.index(loc["niveau"]) if loc else 0
