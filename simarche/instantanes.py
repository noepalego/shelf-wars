"""États des magasins à chaque instant (après chaque créneau et à la clôture) pour la visite 3D hors connexion.

Pendant la partie : `record_scene` écrit un état compact par instant dans le journal (table scene3d) ; le code des
programmes des gérants n'y figure qu'une fois (table scene_programmes). Après la partie : `export_replays` produit
`sites/observatoire/dist/instantanes.js`, lu par `magasins.html` (aucun serveur nécessaire).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import pickle
import sqlite3
import zlib
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import temps
from .catalogue import NIVEAUX, PRODUCTS, RAYONS
from .config import load_config


RUNS = Path(__file__).resolve().parent.parent / "runs"
PRODUCT_IDS = tuple(PRODUCTS)
PRODUCT_INDEX = {p: i for i, p in enumerate(PRODUCT_IDS)}
SLOT_ORDER = {"matin": 0, "midi": 1, "soir": 2, "jour": 2, "cloture": 3}


def _cents(amount):
    return round(amount * 100)


def _digest(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _store_frame(store, day, slot):
    active = tuple(p for p in PRODUCT_IDS if store.listed.get(p))
    layout, prices, stock, specials = [], [], [], {}
    for p in active:
        loc = store.planogram[p]
        layout.append([PRODUCT_INDEX[p], loc["rayon"], loc["segment"], NIVEAUX.index(loc["niveau"]), loc.get("facings", 1)])
        offer = store.shelf_offer(p, day, slot, True)  # à la clôture : tarif de base (aucun prix de créneau)
        prices.append(_cents(offer["prix"]))
        stock.append(store.on_shelf(p, day) if hasattr(store, "on_shelf") else store.available(p, day))
        extra = {}
        if offer["prix_membre"] is not None:
            extra["member"] = _cents(offer["prix_membre"])
        if offer.get("date_courte") is not None:
            extra["short"] = [_cents(offer["date_courte"]), offer["qte_date_courte"]]
        if offer["promo"]:
            extra["promo"] = offer["promo"]
        if extra:
            specials[str(PRODUCT_INDEX[p])] = extra
    displays = {k: [PRODUCT_INDEX[p] for p in v if p in active] for k, v in store.secondary.items() if v}
    programs = [{"name": name, "version": meta["version"], "day": meta["depuis"], "active": bool(meta["actif"]),
                 "sha": _digest(meta["code"])} for name, meta in store.automations.items()]
    checkout_slot = slot if slot != "cloture" else ("soir" if store.cfg.get("creneaux", True) else "jour")
    trips = list(getattr(store, "scene_trips", []) or []) if slot != "cloture" else []
    visits = sum(store.visits.get(day, {}).values()) if slot == "cloture" else store.visits.get(day, {}).get(slot, 0)
    plan = getattr(store, "day_plan", {}) or {}
    plan = plan if plan.get("jour") == day else {}
    if slot == "cloture":
        seen, staff = set(), []
        for people in plan.get("noms", {}).values():
            for x in people:
                if x["name"] not in seen:
                    seen.add(x["name"])
                    staff.append(x)
    else:
        staff = list(plan.get("noms", {}).get(slot, []))
    systems = [{"name": n, "state": s.get("etat"), "visible": s.get("visible_clients", ""), "what": s.get("installation", "")[:160],
                "by": s.get("propose_par", "")} for n, s in getattr(store, "systems", {}).items() if s.get("etat") == "actif"]
    registers = plan.get("caisses", {}).get(checkout_slot) or store.checkouts.get((temps.day_type(day), checkout_slot), 1)
    return {"layout": layout, "displays": displays, "prices": prices, "stock": stock, "offers": specials,
            "profit": _cents(store.profit), "cash": _cents(store.cash), "closed": store.bankrupt,
            "registers": registers, "visits": visits,
            "members": len(store.members), "programs": programs, "trips": trips, "slogan": store.slogan,
            "esl": store.esl, "staff": staff, "systems": systems}


def record_scene(world, day: int, slot: str) -> None:
    """Capture l'état de chaque commerce (après les passages en caisse du créneau, ou à la clôture)."""
    db = world.journal.db
    db.execute("CREATE TABLE IF NOT EXISTS scene3d (jour INT, creneau TEXT, etat BLOB, PRIMARY KEY(jour,creneau))")
    db.execute("CREATE TABLE IF NOT EXISTS scene_programmes (sha TEXT PRIMARY KEY, magasin TEXT, fichier TEXT, version INT, "
               "jour INT, code TEXT)")
    stores = {}
    for sid, st in world.stores.items():
        stores[sid] = _store_frame(st, day, slot)
        for name, meta in st.automations.items():
            db.execute("INSERT OR IGNORE INTO scene_programmes VALUES (?,?,?,?,?,?)",
                       (_digest(meta["code"]), sid, name, meta["version"], meta["depuis"], meta["code"]))
    weather = {"weather": world.weather, "heat": world.heat}
    blob = zlib.compress(json.dumps({"stores": stores, **weather}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
    db.execute("INSERT OR REPLACE INTO scene3d VALUES (?,?,?)", (day, slot, blob))


def _local(path: Path, root: Path):
    path = path.resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise ValueError("L'export accepte uniquement les parties locales du dossier runs.")
    return path


def _registry(values, key, value):
    if key not in values:
        values[key] = (len(values), value)
    return values[key][0]


def _run_data(run_dir, runs_root: Path = RUNS):
    run_dir = _local(Path(run_dir), runs_root)
    config = load_config(_local(run_dir / "config.yaml", run_dir))
    scripts, layouts, offers = {}, {}, {}
    frames = {}
    revisions = []
    codes = {}

    provenance = {}
    for file in sorted((run_dir / "gerants").glob("*/auteur/S*/version.json")):
        meta = json.loads(_local(file, run_dir).read_text(encoding="utf-8"))
        provenance[file.parents[2].name, meta.get("sha256")] = {k: meta[k] for k in ("source", "moteur", "modele", "note", "semaine")
                                                                if meta.get(k) is not None}

    def script(sid, name, version, day, sha):
        return _registry(scripts, (sid, name, sha), {"store": sid, "name": name, "version": version, "day": day,
                                                     "code": codes.get(sha, ""), **provenance.get((sid, sha), {})})

    def add(day, slot, stores, weather=None):
        for sid, st in stores.items():
            geometry = [st["layout"], st.pop("displays")]
            st["layout"] = _registry(layouts, json.dumps(geometry, sort_keys=True), geometry)
            st["offers"] = _registry(offers, json.dumps(st["offers"], sort_keys=True), st["offers"])
            progs = []
            for p in st.get("programs", []):
                sha = p.get("sha") or (_digest(p["code"]) if p.get("code") else "")
                if p.get("code"):
                    codes.setdefault(sha, p["code"])
                progs.append([script(sid, p["name"], p["version"], p["day"], sha), p["active"]])
            st["programs"] = progs
        frames[day, slot] = {"day": day, "date": temps.date_of(day).isoformat(), "label": temps.short_label(day),
                             "slot": slot, "stores": stores, **({"weather": weather} if weather else {})}

    db_path = run_dir / "journal.sqlite"
    if db_path.exists():
        uri = _local(db_path, run_dir).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "scene_programmes" in tables:
                codes.update(dict(db.execute("SELECT sha, code FROM scene_programmes")))
            for day, sid, raw in db.execute("SELECT jour,acteur,donnees FROM evenements WHERE type='automatisation' ORDER BY jour,id"):
                p = json.loads(raw)
                if isinstance(p.get("code"), str):
                    sha = _digest(p["code"])
                    codes.setdefault(sha, p["code"])
                    revisions.append({"day": day, "store": sid, "script": script(sid, p["fichier"], p["version"], day, sha)})
            if "scene3d" in tables:
                for day, slot, blob in db.execute("SELECT jour,creneau,etat FROM scene3d ORDER BY jour"):
                    data = json.loads(zlib.decompress(blob))
                    if "stores" in data:
                        weather = {"weather": data["weather"], "heat": data.get("heat", False)} if data.get("weather") else None
                        add(day, slot, data["stores"], weather)
                    else:  # format des premières versions : {magasin: état}
                        add(day, slot, data)

    if not frames:  # parties sans enregistrement : un instant par sauvegarde hebdomadaire
        checkpoints = sorted((run_dir / "etat").glob("semaine_*.pkl.gz"))
        latest = run_dir / "etat" / "dernier.pkl.gz"
        if latest.exists():
            checkpoints.append(latest)
        for checkpoint in checkpoints:
            # Fichiers générés localement par Simulation.save, sans chemin entré par l'utilisateur de la page.
            with gzip.open(_local(checkpoint, run_dir), "rb") as handle:
                world = pickle.load(handle)
            day = world.day - 1
            if day >= 0 and (day, "cloture") not in frames:
                for st in world.stores.values():
                    for meta in st.automations.values():
                        codes.setdefault(_digest(meta["code"]), meta["code"])
                add(day, "cloture", {sid: _store_frame(st, day, "cloture") for sid, st in world.stores.items()})
    if not frames:
        raise ValueError(f"Aucun instant enregistré dans {run_dir.name}.")
    for (sid, name, sha), (i, value) in scripts.items():
        value["code"] = value["code"] or codes.get(sha, "")
    ordered = sorted(frames, key=lambda k: (k[0], SLOT_ORDER.get(k[1], 9)))
    previous = {}
    for stamp in ordered:
        for sid, st in frames[stamp]["stores"].items():
            before = previous.get(sid, {})
            previous[sid] = dict(st)
            if before.get("layout") != st["layout"]:
                continue
            for key in ("prices", "stock"):
                changes = []
                for i, (old, new) in enumerate(zip(before[key], st[key])):
                    if old != new:
                        changes.extend((i, new))
                if len(changes) < len(st[key]):
                    del st[key]
                    if changes:
                        st[key + "Patch"] = changes
    shops = [{"id": m["id"], "name": m["nom"], "manager": m.get("gerant", "ia")} for m in config["magasins"]]

    def values(registry):
        return [v for _, v in registry.values()]
    from .config import uses_ia
    ia = config.get("ia") if uses_ia(config) else None
    models = {p: config["modeles"].get(ia, {}).get(p, {}).get("modele") for p in ("gerant", "marketing", "arbitre", "client")} if ia else {}
    efforts = {p: config["modeles"].get(ia, {}).get(p, {}).get("effort") for p in ("gerant", "marketing")} if ia else {}
    return {"id": run_dir.name, "name": config["nom"], "seed": config["graine"], "shops": shops,
            "ia": ia, "models": models, "efforts": efforts, "team": config["gerants"].get("equipe", ["gerant"]),
            "brain": config["clients"]["cerveau"], "weeks": config["semaines"],
            "layouts": values(layouts), "offers": values(offers), "programs": values(scripts),
            "revisions": revisions, "frames": [frames[k] for k in ordered]}


def runs_with_scenes(root: Path = RUNS) -> list[Path]:
    out = []
    for d in sorted(Path(root).glob("*")):
        db = d / "journal.sqlite"
        if not d.is_dir() or d.name.startswith("_") or not (d / "config.yaml").exists() or not db.exists():
            continue
        try:
            with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='scene3d'").fetchone() and \
                        con.execute("SELECT 1 FROM scene3d LIMIT 1").fetchone():
                    out.append(d)
        except sqlite3.Error:
            continue
    return out


def export_replays(run_dirs: Iterable[Path], destination: Path, runs_root: Path = RUNS) -> dict:
    """Exporte les parties choisies dans un script JS autonome, lisible en file://."""
    games = [_run_data(p, runs_root) for p in dict.fromkeys(Path(p).resolve() for p in run_dirs)]
    if not games:
        raise ValueError("Choisissez au moins une partie.")
    from .geometrie import room_spec
    data = {"version": 2, "room": room_spec(), "aisles": RAYONS, "levels": NIVEAUX,
            "products": [{"id": p.id, "name": p.nom, "need": p.besoin, "range": p.gamme, "fresh": p.frais,
                          "list": p.prix_conseille} for p in PRODUCTS.values()],
            "games": games}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=destination.parent, delete=False, encoding="utf-8") as handle:
        tmp = Path(handle.name)
        try:
            handle.write("window.SIMARCHE_SCENES=")
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write(";\n")
        except BaseException:
            handle.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)
    return data
