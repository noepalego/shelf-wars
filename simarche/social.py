"""Réseau social : choix des conversations de la semaine et effet du bouche-à-oreille."""
from __future__ import annotations

from .rng import clamp


def select_pairs(world, rnd, max_pairs: int = 24) -> list[dict]:
    pairs = []
    people = sorted(world.active_customers(), key=lambda c: c.id)
    rnd.shuffle(people)
    for c in people:
        if c.state["en_vacances"] or not c.attrs.get("proches") or c.attrs.get("source"):  # variantes : via leur source
            continue
        if rnd.random() >= 0.15 + 0.5 * c.attrs["bavardage"]:
            continue
        contacts = [(p["id"], p["relation"]) for p in c.attrs["proches"]
                    if p["id"] in world.customers and world.customers[p["id"]].active]
        if not contacts:
            continue
        oid, rel = rnd.choice(contacts)
        referral = None
        for sid, cid in c.wallet:
            cp = world.stores[sid].coupons.get(cid)
            if cp and cp.offre == "parrainage" and not cp.utilise and cp.fin >= world.day:
                referral = sid
                break
        pairs.append({"client": c, "autre": world.customers[oid], "relation": rel, "magasins": list(world.stores),
                      "jour": world.day, "parrainage": referral})
        if len(pairs) >= max_pairs:
            break
    return pairs


def apply_talk(world, pair: dict, res: dict) -> None:
    c, o = pair["client"], pair["autre"]
    day = world.day
    msg = (res.get("message") or "").strip()[:400]
    if not msg:
        return
    mentions = []
    for mention in res.get("mentions", []):
        sid = world.resolve_store(mention.get("magasin"))
        if sid is not None:
            mentions.append({**mention, "magasin": sid})
        else:
            world.journal.event(day, "retour_client_rejete", c.id, {"canal": "bouche_a_oreille", "motif": "magasin inconnu"})
    sentiment = 0
    for m in mentions:
        s = max(-1, min(1, int(m.get("sentiment", 0))))
        sentiment += s
        b = o.beliefs[m["magasin"]]
        infl = 0.6 * o.attrs["confiance_bouche_a_oreille"] * (0.5 + o.attrs["influencabilite"]) * (0.5 + c.attrs["leader_opinion"])
        b["satisfaction"] = round(clamp(b["satisfaction"] + s * infl, 0, 10), 2)
        b["image_prix"] = round(clamp(b["image_prix"] - s * 0.2 * infl, -2, 2), 3)
    store = mentions[0]["magasin"] if mentions else None
    o.inbox.append({"jour": day, "magasin": store, "canal": "bouche_a_oreille", "de": f"{c.attrs['prenom']} ({pair['relation']})",
                    "texte": msg, "prix": {}, "sentiment": sentiment})
    o.remember(day, f"{c.attrs['prenom']} m'a dit : « {msg[:160]} »", 3, store, ("bouche_a_oreille",), sentiment < 0)
    if res.get("partage_parrainage") and pair.get("parrainage"):
        st = world.stores[pair["parrainage"]]
        src = next((st.coupons[cid] for sid, cid in c.wallet if sid == st.id and st.coupons.get(cid) and st.coupons[cid].offre == "parrainage"), None)
        if src and not o.beliefs[st.id]["visites"]:
            cp = st.new_coupon(o.id, "filleul", src.valeur, None, src.panier_min, day, src.fin, "bouche_a_oreille",
                               f"Parrainé par {c.attrs['prenom']}", src.campagne, parrain=c.id)
            o.receive_coupon(cp)
            o.remember(day, f"{c.attrs['prenom']} m'a donné un code de parrainage pour {st.nom} ({src.valeur:.0f} € offerts).", 4, st.id, ("coupon",))
    world.journal.conversation(day, c.id, o.id, msg, res.get("cerveau", ""))
