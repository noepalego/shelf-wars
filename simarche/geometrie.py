"""Géométrie commune aux visiteurs et à la maquette 3D. Distances en mètres.

Un client suit les allées des rayons dont il a besoin (et de ceux où il flâne). Ce qu'il remarque dépend de la
géométrie : distance à l'étagère, angle entre son regard et le produit (hauteur des yeux d'un adulte, d'un enfant),
largeur occupée (facings), obstacles (les gondoles masquent ce qui est derrière). Aucun bonus n'est attribué à un
niveau d'étagère : l'avantage de la hauteur des yeux découle de l'angle de vue.
"""
from __future__ import annotations

from functools import lru_cache
from math import atan2, exp, hypot

AISLES = tuple(((r % 3 - 1) * 3.6, (r // 3 - 0.5) * 5) for r in range(6))
OFFSETS = (-1.275, -0.425, 0.425, 1.275)
HEIGHTS = {"bas": 0.36, "mains": 0.92, "yeux": 1.52, "haut": 1.96}
FACE = 0.45                     # face avant des gondoles (côté x croissant)
ENTRANCE, CHECKOUT = (0, -5.9), (4.8, 5.55)
DISPLAYS = {"entree": (-1.2, -5.5), "tete_gondole_1": (-3.6, -4.55),
            "tete_gondole_2": (0, 4.55), "caisse": (4.8, 5.05)}
DISPLAY_HEIGHT = 1.1
DISPLAY_GAIN = 0.75             # un présentoir isole le produit, mais on ne s'y arrête pas toujours
GAZE = -0.20                    # en marchant, le regard est légèrement baissé (radians)
SPREAD = 0.52                   # dispersion angulaire de l'attention
MAX_VIEW = 4.0
CHILD_EYE = 1.0


def room_spec():
    return {"width": 13, "depth": 14, "aisles": [{"id": r, "x": x, "z": z} for r, (x, z) in enumerate(AISLES)],
            "segmentOffsets": OFFSETS, "levels": list(HEIGHTS.values()), "faceOffset": FACE,
            "gondola": {"halfWidth": 0.43, "halfLength": 1.7, "height": 2.2},
            "entrance": ENTRANCE, "checkout": CHECKOUT, "secondary": DISPLAYS, "displayHeight": DISPLAY_HEIGHT}


def eye_height(attrs: dict) -> float:
    """Hauteur des yeux d'un adulte (sans tirage aléatoire : la population reste identique pour une graine donnée)."""
    h = 1.53 if attrs.get("genre") == "F" else 1.64
    if attrs.get("age", 40) >= 75:
        h -= 0.05
    if attrs.get("mobilite_reduite"):
        h -= 0.08
    return round(h, 2)


@lru_cache(maxsize=64)
def visitor_path(aisles):
    """Parcours en allées : les changements de colonne se font dans les traverses libres."""
    points = [ENTRANCE]
    for row in range(2):
        row_aisles = [r for r in aisles if r // 3 == row]
        near, far = (-5.0, 0.0) if row == 0 else (0.0, 5.0)
        if not row_aisles:
            continue
        if len(points) == 1 and row == 0:  # depuis l'entrée, tout droit jusqu'à la traverse de devant
            points.append((ENTRANCE[0], near))
        elif points[-1][1] < near:  # rejoindre l'autre traverse par le côté de la salle
            points.extend([(5.35, points[-1][1]), (5.35, near)])
        corridor = near
        for r in row_aisles:
            x = AISLES[r][0] + 1.45
            points.append((x, corridor))
            corridor = far if corridor == near else near
            points.append((x, corridor))
    points.extend([(5.35, points[-1][1]), (5.35, CHECKOUT[1]), CHECKOUT])
    return tuple(p for i, p in enumerate(points) if i == 0 or p != points[i - 1])


def _blocked(ax, az, bx, bz):
    """Intersection du segment de visée avec le volume horizontal d'une gondole."""
    for cx, cz in AISLES:
        enter, leave = 0., 1.
        for start, delta, low, high in ((ax, bx - ax, cx - .43, cx + .43), (az, bz - az, cz - 1.7, cz + 1.7)):
            if abs(delta) < 1e-8:
                if not low <= start <= high:
                    break
            else:
                a, b = (low - start) / delta, (high - start) / delta
                enter, leave = max(enter, min(a, b)), min(leave, max(a, b))
                if enter > leave:
                    break
        else:
            return True
    return False


@lru_cache(maxsize=16000)
def closest_view(aisles, x, z, front_only):
    """Distance minimale entre le parcours et un point, si une ligne de vue dégagée existe (None sinon)."""
    path = visitor_path(aisles)
    distances = []
    for (ax, az), (bx, bz) in zip(path, path[1:]):
        dx, dz = bx - ax, bz - az
        along = max(0., min(1., ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz)))
        px, pz = ax + along * dx, az + along * dz
        if front_only and px <= x:
            continue
        distance = hypot(px - x, pz - z)
        if distance <= MAX_VIEW and not _blocked(px, pz, x, z):
            distances.append(max(0.4, distance))
    return min(distances, default=None)


def exposure(distance, height, eye, facings):
    """Visibilité d'un emplacement : angle par rapport au regard × surface apparente (plus de facings = plus large)."""
    if distance is None or eye is None:
        return 0.
    angle = atan2(height - eye, distance)
    angular = exp(-0.5 * ((angle - GAZE) / SPREAD) ** 2)
    apparent = (0.85 + 0.15 * facings) / max(distance, 1.0) ** 1.45
    return angular * apparent


def shelf_place(loc) -> tuple[float, float, float]:
    cx, cz = AISLES[loc["rayon"]]
    return cx + FACE, cz + OFFSETS[loc["segment"]], HEIGHTS[loc["niveau"]]


def visibility(loc, displays, aisles, eye, child_eye=None):
    """Ce qu'un passage révèle d'un produit : (étagère adulte, étagère enfant, présentoirs adulte, présentoirs enfant).

    Les deux premiers sont des intensités (≈1 à hauteur des yeux, à un mètre, sur un facing) ; les deux derniers des
    probabilités de remarquer le produit sur ses présentoirs (entrée, têtes de gondole, caisse)."""
    x, z, h = shelf_place(loc)
    f = loc.get("facings", 1)
    d = closest_view(aisles, x, z, True)
    shelf = exposure(d, h, eye, f)
    shelf_child = exposure(d, h, child_eye, f) if child_eye else 0.0
    seen = seen_child = 0.0
    for name in displays:
        dx, dz = DISPLAYS[name]
        dd = closest_view(aisles, dx, dz, False)
        e = min(0.95, DISPLAY_GAIN * exposure(dd, DISPLAY_HEIGHT, eye, 3))
        seen = 1 - (1 - seen) * (1 - e)
        if child_eye:
            ec = min(0.95, DISPLAY_GAIN * exposure(dd, DISPLAY_HEIGHT, child_eye, 3))
            seen_child = 1 - (1 - seen_child) * (1 - ec)
    return shelf, shelf_child, seen, seen_child


def product_exposure(loc, displays, aisles, eye, child_eye, child_interest):
    """Probabilité globale de remarquer un produit (rayon et présentoirs), pour l'analyse et la maquette."""
    shelf, shelf_child, seen, seen_child = visibility(loc, displays, aisles, eye, child_eye)
    missed = (1 - min(0.95, shelf)) * (1 - seen)
    if child_eye is not None:
        missed *= 1 - child_interest * (1 - (1 - min(0.95, shelf_child)) * (1 - seen_child))
    return 1 - missed
