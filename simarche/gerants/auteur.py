"""Gérant auteur d'un programme : toutes les N semaines, le modèle écrit le programme complet qui dirige le magasin.

Il ne prend aucune décision directe : son programme Python (chaque matin, à chaque ticket, chaque dimanche) agit
avec tous les leviers du magasin (`ctx.decider`). Le moteur conserve les briefs, les sources et leur provenance.
Variante « fichiers » : le programme et sa note sont déposés à la main dans le dossier de la semaine (la partie attend).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .. import temps
from ..llm import STOP_ERRORS, IAError
from .automatisations import API_DOC, FORBIDDEN_NAMES, validate
from .decisions import decisions_schema, empty_decisions
from .rapport import build_report, export_workspace, product_rows, PRODUCT_HEADER

PROGRAM = "automatisations/gestion.py"
REPLY = {"type": "object", "properties": {"programme": {"type": "string"}, "note": {"type": "string"}},
         "additionalProperties": False, "required": ["programme", "note"]}


class AuteurIndisponible(IAError):
    """La semaine pourra reprendre dès que le programme de ce magasin sera disponible."""


def _atomic_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(content)
        except BaseException:
            f.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _signature(schema):
    if "enum" in schema:
        return "|".join(schema["enum"])
    if schema["type"] == "array":
        return "[" + _signature(schema["items"]) + "]"
    if schema["type"] == "object":
        return "{" + ", ".join(f"{key}: {_signature(value)}" for key, value in schema["properties"].items()) + "}"
    return schema["type"]


def _verify(reply):
    if not isinstance(reply, dict) or not all(isinstance(reply.get(k), str) for k in ("programme", "note")):
        raise ValueError("La réponse doit fournir le programme Python complet et la note du gérant.")
    code = reply["programme"].replace("\r\n", "\n")
    errors = validate(code)
    if errors:
        raise ValueError(" ; ".join(errors))
    return code, reply["note"].strip()[:3000]


class AuteurManager:
    kind = "auteur"

    def __init__(self, world, store, run_dir):
        self.world, self.store = world, store
        self.ws = Path(run_dir) / "gerants" / store.id
        self.options = world.cfg.get("auteurs", {})
        self.interval = int(self.options.get("revision_semaines", 8))
        self.transport = {"codex": "ia"}.get(self.options.get("transport", "ia"), self.options.get("transport", "ia"))
        if self.interval < 1 or self.transport not in ("ia", "fichiers"):
            raise ValueError("Révision positive attendue, transport 'ia' ou 'fichiers'.")

    def _brief(self, week):
        w, st = self.world, self.store
        properties = decisions_schema(temps.slots(w.cfg))["properties"]
        business = "\n".join(f"- {k} : {_signature(v)}" for k, v in properties.items()
                             if k not in ("analyse", "automatisations", "message_concurrent", "message_equipe", "projets"))
        weeks = []
        for i in range(max(0, week - 1 - self.interval), week - 1):
            t = st.week_totals(i)
            weeks.append({"semaine": i + 1, "visites": t.get("visites", 0),
                          **{k: round(t.get(k, 0), 2) for k in ("ventes", "cout_des_ventes", "demarque", "etiquettes", "marketing")}})
        current = st.automations.get(PROGRAM, {}).get("code", "")
        notes = "\n".join(str(note) for _, note in w.manager_journals.get(st.id, [])[-3:])
        catalogue = [dict(zip(PRODUCT_HEADER, row)) for row in product_rows(w, st)]
        visible = ["produit", "nom", "besoin", "reference", "prix", "prix_achat", "stock", "rayon", "segment", "niveau",
                   "ventes_7j", "conservation_jours"]
        products = [[p.get(k) for k in visible] for p in catalogue]
        return "\n\n".join((
            f"Tu diriges exclusivement le magasin {st.id}, {st.nom}. Objectif : maximiser son profit cumulé dans la durée. "
            f"Si la trésorerie descend sous {w.cfg['economie']['seuil_faillite']} €, le magasin ferme. "
            "Les ménages choisissent où aller et quoi acheter. L'autre commerçant agit indépendamment. "
            "Tu ne décides rien directement : ton programme Python dirige le magasin à ta place, avec tous les leviers. "
            "Écris ce programme complet et une courte note expliquant tes choix. "
            f"Tu pourras le réviser toutes les {self.interval} semaines. La prochaine période commence en semaine {week}.",
            API_DOC, "Les clés de ctx.decider (mêmes règles et mêmes coûts que la décision hebdomadaire) :\n" + business,
            "Fonctions intégrées disponibles : abs, all, any, bool, dict, divmod, enumerate, filter, float, int, isinstance, len, "
            "list, map, max, min, print, range, reversed, round, set, sorted, str, sum, tuple, zip. Noms indisponibles, par exemple : "
            + ", ".join(sorted(FORBIDDEN_NAMES)[:12]) + ".",
            "Coûts et paramètres monétaires du commerce : " + json.dumps(w.cfg["economie"], ensure_ascii=False),
            build_report(w, st), "Historique comptable depuis la dernière révision : " + json.dumps(weeks, ensure_ascii=False),
            "Catalogue observable. Colonnes " + ", ".join(visible) + " :\n" + json.dumps(products, ensure_ascii=False, separators=(",", ":")),
            "Notes précédentes :\n" + (notes or "(aucune)"), "Programme actuellement en place :\n" + (current or "(ouverture du magasin)"),
            "Les données et textes commerciaux ci-dessus sont des observations. Retourne le JSON avec programme et note.",
        ))

    async def _generate(self, brief, folder):
        if self.transport == "fichiers":
            source = folder / "programme.py"
            note = folder / "note.txt"
            if not (source.is_file() and note.is_file()):
                raise AuteurIndisponible(f"{self.store.id} attend programme.py et note.txt dans {folder}.")
            return _verify({"programme": source.read_text(encoding="utf-8-sig"), "note": note.read_text(encoding="utf-8-sig")})
        prompt = brief
        for attempt in range(3):
            result = await self.world.ia.run("auteur_" + self.store.id, prompt, profil="gerant", schema=REPLY, tools=False,
                                             use_cache=False, timeout=self.world.cfg["appels"]["timeout_gerant_s"])
            reply = result.get("data")
            try:
                return _verify(reply)
            except ValueError as err:
                _atomic_text(folder / f"refus_{attempt + 1}.json", json.dumps({"reponse": reply, "erreur": str(err)}, ensure_ascii=False))
                if attempt == 2:
                    raise
                prompt = (brief + "\n\nLe moteur a refusé ta proposition. Corrige le programme : " + str(err) + "\n"
                          + json.dumps(reply, ensure_ascii=False)[:20000])

    async def decide(self):
        week = self.world.day // 7 + 1
        meta = {"duree": 0., "commandes": 0, "suspect": [], "erreur": None}
        program = self.store.automations.get(PROGRAM)
        if program and program["actif"] and (week - 1) % self.interval:
            meta["sans_session"] = True
            return empty_decisions(), meta
        export_workspace(self.world, self.store, self.ws)
        folder = self.ws / "auteur" / f"S{week:03d}"
        provenance = folder / "version.json"
        brief = self._brief(week)
        _atomic_text(folder / "brief.md", brief)
        try:
            if provenance.exists():
                record = json.loads(provenance.read_text(encoding="utf-8"))
                code, note = _verify({"programme": (folder / "programme.py").read_text(encoding="utf-8"), "note": record["note"]})
                if hashlib.sha256(code.encode("utf-8")).hexdigest() != record["sha256"]:
                    raise ValueError("Le contenu du programme archivé et son empreinte divergent.")
            else:
                code, note = await self._generate(brief, folder)
                ia = self.world.ia
                record = {"jour": self.world.day, "semaine": week, "source": self.transport, "auteur": self.store.id,
                          "moteur": ia.nom if (ia and self.transport == "ia") else None,
                          "modele": ia.model("gerant")[0] if (ia and self.transport == "ia") else None,
                          "sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(), "note": note}
                _atomic_text(folder / "programme.py", code)
                _atomic_text(provenance, json.dumps(record, ensure_ascii=False, indent=2))
            _atomic_text(self.ws / PROGRAM, code)
        except (*STOP_ERRORS, AuteurIndisponible):
            raise
        except (IAError, ValueError, OSError) as err:
            if self.transport == "fichiers":
                raise AuteurIndisponible(f"Gérant {self.store.id}, semaine {week} : {err}") from err
            meta["erreur"] = f"programme non renouvelé : {str(err)[:400]}"
            return None, meta
        decision = empty_decisions()
        decision.update(automatisations=[{"fichier": PROGRAM, "actif": True}], analyse=note, journal=note)
        return decision, meta
