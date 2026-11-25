"""Hasard reproductible : un flux indépendant par sous-système."""
from __future__ import annotations

import hashlib
import random


class RNG:
    def __init__(self, seed: int):
        self.seed = int(seed)
        self._streams: dict[str, random.Random] = {}

    def stream(self, name: str) -> random.Random:
        r = self._streams.get(name)
        if r is None:
            h = int(hashlib.sha256(f"{self.seed}:{name}".encode()).hexdigest()[:16], 16)
            r = random.Random(h)
            self._streams[name] = r
        return r


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x
