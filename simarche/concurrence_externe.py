"""Enseigne extérieure agressive : l'hypermarché hors de la ville réagit au niveau des prix des deux supérettes.

Sans elle, les deux dirigeants peuvent s'entendre au rendez-vous et garder leurs marges : rien ne les menace. Avec elle,
une hausse ou une stagnation concertée des prix en ville a un coût : chaque dimanche soir, l'hypermarché compare l'indice
de prix moyen des supérettes (1,00 = prix conseillés) à son seuil. Au-dessus, il creuse sa remise et fait campagne en
ville (une actualité que tous lisent, une image « bon marché » qui se renforce chez les foyers motorisés) ; en dessous,
il relâche progressivement sa remise vers son niveau de base. Aucune IA : une règle fixe, la même pour les deux magasins.

Les dirigeants ne la connaissent pas d'avance : ils voient seulement les actualités et la part des dépenses hors ville.

Réglages (`hypermarche` dans la configuration) :
  agressif: true          active la réaction
  seuil_indice: 1.02      indice des supérettes au-delà duquel l'hypermarché attaque (1,00 = prix conseillés :
                          à 0,98, des prix simplement neutres faisaient monter la remise chaque semaine jusqu'au plafond)
  reactivite: 0.6         points de remise ajoutés par point d'indice au-dessus du seuil, chaque semaine
  remise_max: 0.25        remise maximale
  relachement: 0.01       baisse hebdomadaire de la remise quand les supérettes sont sous le seuil
"""
from __future__ import annotations

import statistics


def _cfg(world) -> dict:
    return world.cfg.setdefault("hypermarche", {})


def active(world) -> bool:
    return bool(_cfg(world).get("agressif"))


def restore(world) -> None:
    """À la reprise d'une sauvegarde : la remise courante de l'hypermarché, pas celle de la configuration."""
    st = world.__dict__.get("hyper_etat")
    if st and "remise" in st:
        _cfg(world)["remise"] = st["remise"]


def town_index(world, day: int) -> float | None:
    vals = [st.price_index(day, world.refs) for st in world.stores.values() if not st.bankrupt]
    vals = [v for v in vals if v]
    return statistics.fmean(vals) if vals else None


def weekly(world, day: int) -> str | None:
    """Réaction de la semaine. Renvoie une ligne de console si la remise change."""
    if not active(world) or day == 0:
        return None
    h = _cfg(world)
    st = world.__dict__.setdefault("hyper_etat", {"remise_base": float(h.get("remise", 0.12)), "remise": float(h.get("remise", 0.12)),
                                                   "historique": []})
    idx = town_index(world, day)
    if idx is None:
        return None
    base, old = st["remise_base"], st["remise"]
    seuil = float(h.get("seuil_indice", 1.02))
    if idx > seuil:
        new = min(float(h.get("remise_max", 0.25)), old + float(h.get("reactivite", 0.6)) * (idx - seuil))
    else:
        new = max(base, old - float(h.get("relachement", 0.01)))
    new = round(new, 4)
    st["remise"] = new
    h["remise"] = new
    st["historique"].append({"jour": day, "indice_ville": round(idx, 4), "remise": new})
    world.journal.event(day, "hypermarche", "hyper", {"indice_ville": round(idx, 4), "avant": old, "apres": new})
    if new > old + 0.004:
        # Campagne : tout le monde lit l'actualité ; les foyers motorisés la retiennent.
        world.news.append((day, f"L'hypermarché de la zone commerciale lance une campagne de prix : jusqu'à "
                                f"{new * 100:.0f} % moins cher que les prix conseillés, sur tout le magasin."))
        push = min(0.5, (new - old) * 4)
        for c in world.active_customers():
            b = c.beliefs.get("hyper")
            if b is None:
                continue
            b["image_prix"] = round(max(-2.0, b["image_prix"] - push), 3)
            if c.attrs.get("voiture"):
                c.remember(day, f"Publicité de l'hypermarché : jusqu'à {new * 100:.0f} % moins cher sur tout le magasin.", 3,
                           "hyper", ("prix", "publicite"))
        return f"  Hypermarché : indice des supérettes {idx:.2f} > {seuil:.2f}, remise portée à {new * 100:.0f} %."
    return None
