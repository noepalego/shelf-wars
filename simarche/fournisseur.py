"""Grossiste commun aux deux magasins : prix d'achat évolutifs, remises de volume, pénuries."""
from __future__ import annotations

import math

from .catalogue import PRODUCTS


class Supplier:
    def __init__(self):
        self.base = {pid: p.prix_achat for pid, p in PRODUCTS.items()}
        self.prices = dict(self.base)
        self.shortages: dict[str, int] = {}   # produit -> dernier jour de pénurie
        self.announcements: list[tuple[int, str]] = []

    def wholesale_minimum(self, pid: str) -> int:
        return 4 if PRODUCTS[pid].frais else 1

    def local_offer(self, pid: str) -> dict | None:
        product = PRODUCTS[pid]
        if not product.frais:
            return None
        return {"prix": round(self.prices[pid] * 1.18, 4), "delai": 1, "minimum": 1, "maximum_jour": 35}

    def tick(self, day: int, rnd) -> None:
        for pid in self.prices:
            drift = math.exp(rnd.gauss(0, 0.004))
            target = self.base[pid]
            p = self.prices[pid] * drift
            p += 0.05 * (target - p)   # rappel vers le prix de base
            self.prices[pid] = round(max(0.05, p), 4)

    def inflation(self, day: int, needs: list[str], rate: float) -> list[str]:
        hit = []
        for pid, p in PRODUCTS.items():
            if p.besoin in needs:
                self.base[pid] = round(self.base[pid] * (1 + rate), 4)
                self.prices[pid] = round(self.prices[pid] * (1 + rate), 4)
                hit.append(pid)
        self.announcements.append((day, f"Hausse de {rate * 100:.0f} % des prix d'achat ({', '.join(needs)})."))
        return hit

    def shortage(self, day: int, pids: list[str], days: int) -> None:
        if days <= 0:
            return
        for pid in pids:
            self.shortages[pid] = max(self.shortages.get(pid, -1), day + days - 1)
        names = ", ".join(PRODUCTS[p].nom for p in pids if p in PRODUCTS)
        self.announcements.append((day, f"Pénurie chez le grossiste pendant {days} jours : {names}."))

    def can_deliver(self, pid: str, day: int) -> bool:
        return self.shortages.get(pid, -1) < day

    def unit_cost(self, pid: str, qty: int) -> float:
        p = self.prices[pid]
        if qty >= 200:
            p *= 0.94
        elif qty >= 50:
            p *= 0.97
        return round(p, 4)
