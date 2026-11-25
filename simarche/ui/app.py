"""Tableau de bord Streamlit : python -m simarche tableau"""
from __future__ import annotations

import asyncio
import gzip
import json
import pickle
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simarche import temps  # noqa: E402
from simarche.catalogue import NIVEAUX, PRODUCTS, RAYONS  # noqa: E402
from simarche.population import ATTRIBUTE_FAMILIES, POSITION_MARCHE, POSITIONS_MAGASINS, QUARTIERS  # noqa: E402

RUNS = ROOT / "runs"
st.set_page_config(page_title="Simarché", layout="wide")
COLORS = {"A": "#2a6fdb", "B": "#e0703a", "hyper": "#7d8a99", "marche": "#3aa35b", "maison": "#c9ced6"}


def runs() -> list[Path]:
    return sorted((d for d in RUNS.glob("*") if (d / "journal.sqlite").exists() and not d.name.startswith("_")),
                  key=lambda d: d.stat().st_mtime, reverse=True)


@st.cache_data(ttl=20)
def q(run: str, sql: str, params: tuple = ()) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{Path(run) / 'journal.sqlite'}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


@st.cache_data(ttl=60)
def config(run: str) -> dict:
    return yaml.safe_load((Path(run) / "config.yaml").read_text(encoding="utf-8"))


@st.cache_data(ttl=60)
def population(run: str) -> dict:
    p = Path(run) / "population.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@st.cache_resource(ttl=60)
def world(run: str):
    p = Path(run) / "etat" / "dernier.pkl.gz"
    if not p.exists():
        return None
    with gzip.open(p, "rb") as f:
        return pickle.load(f)


def week_col(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["semaine"] = df["jour"] // 7 + 1
    return df


# ------------------------------------------------------------------ barre latérale
st.sidebar.title("Simarché")
all_runs = runs()
if not all_runs:
    st.info("Aucune partie pour l'instant. Lance : `python -m simarche run --rapide --semaines 4`")
    st.stop()
run = str(st.sidebar.selectbox("Partie", all_runs, format_func=lambda p: Path(p).name))
cfg = config(run)
names = {m["id"]: m["nom"] for m in cfg["magasins"]}
status = Path(run) / "statut.json"
if status.exists():
    s = json.loads(status.read_text(encoding="utf-8"))
    st.sidebar.caption(f"Jour {s['jour']} / {s['semaines'] * 7} — {'terminée' if s['termine'] else 'en cours'} ({s['date']})")
if st.sidebar.button("Rafraîchir"):
    st.cache_data.clear()
    st.cache_resource.clear()
page = st.sidebar.radio("Page", ["Vue d'ensemble", "Clients", "Gérants", "Magasins", "Marketing", "Social", "Carte", "IA", "Rapport et interviews"])
stores = list(names)

met = q(run, "SELECT * FROM metriques ORDER BY jour")
if met.empty:
    st.warning("La partie n'a pas encore de journée simulée.")
    st.stop()
met = week_col(met)

# ------------------------------------------------------------------ pages
if page == "Vue d'ensemble":
    st.title("Vue d'ensemble")
    cols = st.columns(len(stores))
    total_visits = met["visites"].sum() or 1
    for col, sid in zip(cols, stores):
        m = met[met.lieu == sid]
        last = m.iloc[-1]
        col.subheader(f"{sid} — {names[sid]}")
        col.metric("Résultat cumulé", f"{last['resultat_cumule']:,.0f} €".replace(",", " "))
        col.metric("Trésorerie", f"{last['tresorerie']:,.0f} €".replace(",", " "))
        col.metric("Part des passages", f"{m['visites'].sum() / total_visits * 100:.0f} %")
        col.metric("Membres fidélité", int(last["membres"]))
    wk = met.groupby(["semaine", "lieu"], as_index=False).agg(visites=("visites", "sum"), ca=("ca", "sum"), marge=("marge", "sum"),
                                                              indice=("indice_prix", "mean"), taux=("taux_marge", "mean"),
                                                              treso=("tresorerie", "last"), res=("resultat_cumule", "last"))
    tot = wk.groupby("semaine")["visites"].transform("sum")
    wk["part"] = wk["visites"] / tot.where(tot > 0, 1)
    c1, c2 = st.columns(2)
    c1.plotly_chart(px.area(wk, x="semaine", y="part", color="lieu", color_discrete_map=COLORS, title="Part des passages par semaine"), use_container_width=True)
    c2.plotly_chart(px.line(wk[wk.lieu.isin(stores)], x="semaine", y="res", color="lieu", color_discrete_map=COLORS, title="Résultat cumulé (€)"), use_container_width=True)
    c3, c4 = st.columns(2)
    c3.plotly_chart(px.line(wk[wk.lieu.isin(stores)], x="semaine", y="indice", color="lieu", color_discrete_map=COLORS,
                            title="Indice de prix (1 = prix conseillés)"), use_container_width=True)
    c4.plotly_chart(px.line(wk[wk.lieu.isin(stores)], x="semaine", y="taux", color="lieu", color_discrete_map=COLORS, title="Taux de marge moyen"), use_container_width=True)
    c5, c6 = st.columns(2)
    c5.plotly_chart(px.bar(wk[wk.lieu.isin(stores)], x="semaine", y="ca", color="lieu", barmode="group", color_discrete_map=COLORS,
                           title="Chiffre d'affaires hebdomadaire (€)"), use_container_width=True)
    c6.plotly_chart(px.line(wk[wk.lieu.isin(stores)], x="semaine", y="treso", color="lieu", color_discrete_map=COLORS, title="Trésorerie (€)"), use_container_width=True)
    if "creneaux_travail" in met.columns and met["creneaux_travail"].fillna(0).sum() > 0:
        prod = met[met.lieu.isin(stores)].groupby(["semaine", "lieu"], as_index=False).agg(
            marge=("marge", "sum"), creneaux=("creneaux_travail", "sum"), salaires=("salaires", "sum"))
        prod["marge_par_creneau"] = prod["marge"] / prod["creneaux"].where(prod["creneaux"] > 0, 1)
        c7, c8 = st.columns(2)
        c7.plotly_chart(px.line(prod, x="semaine", y="marge_par_creneau", color="lieu", color_discrete_map=COLORS,
                                title="Productivité : marge brute par créneau de travail (€)"), use_container_width=True)
        c8.plotly_chart(px.bar(prod, x="semaine", y="salaires", color="lieu", barmode="group", color_discrete_map=COLORS,
                               title="Salaires et intérim (€ par semaine)"), use_container_width=True)
    ev = q(run, "SELECT jour, type, acteur, donnees FROM evenements WHERE type IN ('choc_inflation','choc_penurie','faillite','limite_usage',"
                "'message_concurrent','projet','systeme_en_service','projet_arrete','demission') ORDER BY jour")
    if not ev.empty:
        st.subheader("Événements marquants")
        ev["date"] = ev["jour"].apply(temps.short_label)
        st.dataframe(ev[["date", "type", "acteur", "donnees"]], use_container_width=True, hide_index=True)

elif page == "Clients":
    st.title("Clients")
    pop = population(run)
    ch = q(run, "SELECT jour, creneau, client, lieu, trajet, raison, cerveau FROM choix ORDER BY jour")
    if not ch.empty:
        ch = week_col(ch)
        maj = ch[ch.lieu != "maison"].groupby(["semaine", "client"])["lieu"].agg(lambda s: s.value_counts().index[0]).reset_index()
        weeks = sorted(maj.semaine.unique())
        if len(weeks) >= 2:
            w0, w1 = st.select_slider("Flux entre deux semaines", options=weeks, value=(weeks[0], weeks[-1]))
            a = maj[maj.semaine == w0].set_index("client")["lieu"]
            b = maj[maj.semaine == w1].set_index("client")["lieu"]
            common = a.index.intersection(b.index)
            flows = pd.DataFrame({"de": a[common].values, "vers": b[common].values}).value_counts().reset_index(name="n")
            labels = [f"{x} (S{w0})" for x in COLORS] + [f"{x} (S{w1})" for x in COLORS]
            idx = {l: i for i, l in enumerate(labels)}
            fig = go.Figure(go.Sankey(node={"label": labels, "color": list(COLORS.values()) * 2},
                                      link={"source": [idx[f"{r.de} (S{w0})"] for r in flows.itertuples()],
                                            "target": [idx[f"{r.vers} (S{w1})"] for r in flows.itertuples()],
                                            "value": flows["n"].tolist()}))
            fig.update_layout(title="Qui va où (magasin le plus fréquenté de chaque foyer)")
            st.plotly_chart(fig, use_container_width=True)
        if pop:
            seg = ch[ch.lieu != "maison"].copy()
            seg["quartier"] = seg["client"].map(lambda c: pop.get(c, {}).get("attributs", {}).get("quartier", "?"))
            st.plotly_chart(px.histogram(seg, x="quartier", color="lieu", barnorm="percent", color_discrete_map=COLORS,
                                         title="Où font leurs courses les habitants de chaque quartier (%)"), use_container_width=True)
    ids = sorted(pop) if pop else sorted(ch.client.unique()) if not ch.empty else []
    if ids:
        cid = st.selectbox("Foyer", ids, format_func=lambda c: f"{c} — {pop.get(c, {}).get('attributs', {}).get('prenom', '')}")
        info = pop.get(cid, {})
        st.markdown(f"**{info.get('persona', '')}**")
        cw = q(run, "SELECT semaine, donnees FROM clients_semaine WHERE client = ? ORDER BY semaine", (cid,))
        if not cw.empty:
            rows = []
            for r in cw.itertuples():
                d = json.loads(r.donnees)
                for place, b in d["croyances"].items():
                    if b.get("connait"):
                        rows.append({"semaine": r.semaine + 1, "lieu": place, "satisfaction": b["satisfaction"], "image_prix": b["image_prix"],
                                     "confiance": b["confiance"], "habitude": b["habitude"]})
            df = pd.DataFrame(rows)
            if not df.empty:
                c1, c2 = st.columns(2)
                c1.plotly_chart(px.line(df, x="semaine", y="satisfaction", color="lieu", color_discrete_map=COLORS, title="Satisfaction (0-10)"), use_container_width=True)
                c2.plotly_chart(px.line(df, x="semaine", y="image_prix", color="lieu", color_discrete_map=COLORS, title="Image prix (-2 bon marché, +2 cher)"), use_container_width=True)
            last = json.loads(cw.iloc[-1]["donnees"])
            st.markdown("**Souvenirs récents** : " + " ; ".join(last.get("souvenirs", [])))
            if last.get("intention"):
                st.markdown(f"**Intention** : {last['intention']}")
        mine = ch[ch.client == cid].copy() if not ch.empty else pd.DataFrame()
        if not mine.empty:
            mine["date"] = mine["jour"].apply(temps.short_label)
            st.subheader("Décisions et raisons")
            st.dataframe(mine[["date", "creneau", "trajet", "lieu", "raison", "cerveau"]].iloc[::-1], use_container_width=True, hide_index=True)
        tk = q(run, "SELECT jour, magasin, total, remise_coupons, nb_articles, lignes FROM tickets WHERE client = ? ORDER BY jour DESC LIMIT 40", (cid,))
        if not tk.empty:
            tk["date"] = tk["jour"].apply(temps.short_label)
            tk["articles"] = tk["lignes"].apply(lambda s: ", ".join(f"{l['produit']}×{l['quantite']}" for l in json.loads(s)))
            st.subheader("Tickets de caisse")
            st.dataframe(tk[["date", "magasin", "total", "remise_coupons", "nb_articles", "articles"]], use_container_width=True, hide_index=True)
        if info.get("attributs"):
            with st.expander(f"Les {sum(len(v) for v in ATTRIBUTE_FAMILIES.values())} attributs"):
                a = info["attributs"]
                for fam, keys in ATTRIBUTE_FAMILIES.items():
                    st.markdown(f"**{fam}**")
                    st.json({k: a.get(k) for k in keys if k != "gouts"}, expanded=False)

elif page == "Gérants":
    st.title("Gérants")
    sid = st.radio("Magasin", stores, horizontal=True, format_func=lambda s: f"{s} — {names[s]}")
    dec = q(run, "SELECT semaine, source, analyse, refus, duree, donnees FROM decisions WHERE magasin = ? ORDER BY semaine", (sid,))
    if dec.empty:
        st.info("Pas encore de décision.")
    else:
        dec["semaine"] = dec["semaine"] + 1
        st.dataframe(dec[["semaine", "source", "analyse", "refus", "duree"]].iloc[::-1], use_container_width=True, hide_index=True)
        wsel = st.selectbox("Détail de la décision", dec["semaine"].tolist()[::-1])
        st.json(json.loads(dec[dec.semaine == wsel].iloc[0]["donnees"] or "null"), expanded=False)
    ws = Path(run) / "gerants" / sid
    for jfile, title in (("journal.md", "Journal de stratégie"), ("journal_gerant.md", "Journal du gérant"),
                         ("journal_marketing.md", "Journal de la responsable marketing et clients"),
                         ("equipe.md", "Messages de l'équipe de direction")):
        if (ws / jfile).exists():
            with st.expander(title):
                st.markdown((ws / jfile).read_text(encoding="utf-8"))
    autos = sorted((ws / "automatisations").glob("*.py")) if (ws / "automatisations").exists() else []
    if autos:
        st.subheader("Code écrit par la direction")
        for f in autos:
            with st.expander(f.name):
                st.code(f.read_text(encoding="utf-8", errors="replace"), language="python")
    projects = sorted((ws / "projets").glob("*.md")) if (ws / "projets").exists() else []
    if projects:
        st.subheader("Inventions (et devis de l'arbitre)")
        for f in projects:
            verdict = f.with_name(f.stem + ".devis.json")
            if not verdict.exists():
                verdict = f.with_name(f.stem + ".verdict.json")
            v = json.loads(verdict.read_text(encoding="utf-8")) if verdict.exists() else {}
            with st.expander(f"{f.stem} — {v.get('statut', v.get('etat', 'pas encore évalué'))}"):
                st.markdown(f.read_text(encoding="utf-8", errors="replace"))
                if v:
                    st.json(v, expanded=False)
                data = ws / "donnees" / "systemes" / f"{f.stem}.csv"
                if data.exists():
                    st.dataframe(pd.read_csv(data, sep=";").tail(200), use_container_width=True, hide_index=True)
    index = ws / "sous_agents" / "INDEX.md"
    if index.exists():
        st.subheader("Missions des sous-agents")
        st.markdown(index.read_text(encoding="utf-8"))
    final = ws.parent.parent / "rapport_final.md"
    if final.exists():
        with st.expander("Rapport final de la partie"):
            st.markdown(final.read_text(encoding="utf-8"))
    staff = ws / "donnees" / "personnel.csv"
    if staff.exists():
        people = pd.read_csv(staff, sep=";")
        if not people.empty:
            st.subheader(f"Personnel ({int(people['en_poste'].sum())} en poste)")
            st.dataframe(people, use_container_width=True, hide_index=True)
    code_dec = q(run, "SELECT jour, donnees FROM evenements WHERE type = 'decision_code' AND acteur = ? ORDER BY id DESC LIMIT 300", (sid,))
    if not code_dec.empty:
        st.subheader("Ce que son code a décidé")
        rows = []
        for r in code_dec.itertuples():
            d = json.loads(r.donnees)
            rows.append({"date": temps.short_label(r.jour), "programmes": ", ".join(d.get("sources", [])),
                         "appliqué": " ; ".join(d.get("acceptees", [])), "refusé": " ; ".join(d.get("refusees", []))})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    surveys = ws / "donnees" / "enquetes.csv"
    if surveys.exists():
        answers = pd.read_csv(surveys, sep=";")
        if not answers.empty:
            st.subheader(f"Réponses à ses questionnaires ({len(answers)})")
            st.dataframe(answers.iloc[::-1], use_container_width=True, hide_index=True)
    hist = sorted((ws / "historique").glob("*_commandes.json")) if (ws / "historique").exists() else []
    if hist:
        with st.expander("Commandes exécutées pendant les sessions"):
            f = st.selectbox("Session", hist[::-1], format_func=lambda p: p.stem)
            for c in json.loads(f.read_text(encoding="utf-8")):
                st.code(f"$ {c.get('commande')}\n{c.get('sortie', '')}", language="powershell")
    inc = q(run, "SELECT jour, type, details FROM incidents WHERE magasin = ? ORDER BY jour", (sid,))
    if not inc.empty:
        st.subheader("Incidents")
        inc["date"] = inc["jour"].apply(temps.short_label)
        st.dataframe(inc[["date", "type", "details"]], use_container_width=True, hide_index=True)

elif page == "Magasins":
    st.title("Magasins")
    pr = q(run, "SELECT jour, magasin, produit, prix FROM prix")
    prods = st.multiselect("Produits", sorted(PRODUCTS), default=["baguette", "lait_mdd", "poulet_mdd", "pates_mdd"],
                           format_func=lambda p: PRODUCTS[p].nom)
    if prods and not pr.empty:
        sub = pr[pr.produit.isin(prods)]
        st.plotly_chart(px.line(sub, x="jour", y="prix", color="magasin", line_dash="produit", color_discrete_map=COLORS,
                                title="Prix affichés"), use_container_width=True)
    w = world(run)
    if w is not None:
        sid = st.radio("Plan du magasin", stores, horizontal=True)
        stt = w.stores[sid]
        sales: dict = {}
        for d, s in stt.sales.items():
            if d >= w.day - 28:
                for pid, v in s.items():
                    sales[pid] = sales.get(pid, 0) + v[0]
        grid = []
        for pid, loc in stt.planogram.items():
            if stt.listed[pid]:
                grid.append({"rayon": f"{loc['rayon']} {RAYONS[loc['rayon']][:18]}", "place": f"seg {loc['segment']} — {loc['niveau']}",
                             "ventes": sales.get(pid, 0), "produit": PRODUCTS[pid].nom, "niveau": NIVEAUX.index(loc["niveau"])})
        gdf = pd.DataFrame(grid).sort_values(["niveau"], ascending=False)
        st.plotly_chart(px.density_heatmap(gdf, x="rayon", y="place", z="ventes", histfunc="sum", hover_data=["produit"],
                                           title="Ventes des 4 dernières semaines par emplacement"), use_container_width=True)
        st.caption("Mises en avant : " + " | ".join(f"{k} : {', '.join(v) or '—'}" for k, v in stt.secondary.items()))
        waste = {}
        for d, s in stt.waste.items():
            for pid, v in s.items():
                waste[pid] = waste.get(pid, 0) + v[1]
        if waste:
            wd = pd.DataFrame([{"produit": PRODUCTS[p].nom, "cout": c} for p, c in waste.items()]).sort_values("cout", ascending=False).head(15)
            st.plotly_chart(px.bar(wd, x="produit", y="cout", title="Démarque (coût des produits périmés, €)"), use_container_width=True)

elif page == "Marketing":
    st.title("Marketing")
    camp = q(run, "SELECT jour, magasin, canal, texte, envoyes, lus FROM campagnes ORDER BY jour")
    if camp.empty:
        st.info("Aucune campagne pour l'instant.")
    else:
        camp["date"] = camp["jour"].apply(temps.short_label)
        st.plotly_chart(px.bar(week_col(camp), x="semaine", y="lus", color="magasin", pattern_shape="canal", color_discrete_map=COLORS,
                               title="Personnes touchées par semaine"), use_container_width=True)
        st.dataframe(camp[["date", "magasin", "canal", "texte", "envoyes", "lus"]].iloc[::-1], use_container_width=True, hide_index=True)
    tk = q(run, "SELECT jour, magasin, SUM(remise_coupons) AS coupons, SUM(remise_points) AS points FROM tickets GROUP BY jour, magasin")
    if not tk.empty:
        tk = week_col(tk).groupby(["semaine", "magasin"], as_index=False)[["coupons", "points"]].sum()
        st.plotly_chart(px.bar(tk, x="semaine", y=["coupons", "points"], facet_col="magasin", title="Remises accordées (€)"), use_container_width=True)
    av = q(run, "SELECT jour, magasin, note, texte, reponse FROM avis ORDER BY jour DESC")
    if not av.empty:
        st.subheader("Avis publics")
        av["date"] = av["jour"].apply(temps.short_label)
        st.dataframe(av[["date", "magasin", "note", "texte", "reponse"]], use_container_width=True, hide_index=True)

elif page == "Social":
    st.title("Bouche-à-oreille")
    pop = population(run)
    conv = q(run, "SELECT jour, de, a, message, cerveau FROM conversations ORDER BY jour DESC")
    if pop:
        xs, ys, texts = [], [], []
        for cid, p in pop.items():
            a = p["attributs"]
            xs.append(a["x"])
            ys.append(a["y"])
            texts.append(f"{cid} {a['prenom']}")
        fig = go.Figure()
        for cid, p in pop.items():
            for link in p["attributs"].get("proches", []):
                o = pop.get(link["id"])
                if o and cid < link["id"]:
                    fig.add_trace(go.Scatter(x=[p["attributs"]["x"], o["attributs"]["x"]], y=[p["attributs"]["y"], o["attributs"]["y"]],
                                             mode="lines", line={"width": 1, "color": "#bbb"}, hoverinfo="skip", showlegend=False))
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers+text", text=texts, textposition="top center", showlegend=False))
        fig.update_layout(title="Réseau de proches", height=600)
        st.plotly_chart(fig, use_container_width=True)
    if not conv.empty:
        conv["date"] = conv["jour"].apply(temps.short_label)
        st.dataframe(conv[["date", "de", "a", "message", "cerveau"]], use_container_width=True, hide_index=True)

elif page == "Carte":
    st.title("La ville")
    pop = population(run)
    ch = q(run, "SELECT jour, client, lieu FROM choix WHERE lieu != 'maison'")
    if pop and not ch.empty:
        ch = week_col(ch)
        weeks = sorted(ch.semaine.unique())
        wsel = st.slider("Semaine", int(weeks[0]), int(weeks[-1]), int(weeks[-1]))
        sub = ch[ch.semaine == wsel].groupby("client")["lieu"].agg(lambda s: s.value_counts().index[0])
        rows = [{"client": c, "x": p["attributs"]["x"], "y": p["attributs"]["y"], "lieu": sub.get(c, "aucune course"),
                 "quartier": p["attributs"]["quartier"], "qui": p["persona"][:80]} for c, p in pop.items()]
        df = pd.DataFrame(rows)
        fig = px.scatter(df, x="x", y="y", color="lieu", symbol="quartier", hover_data=["client", "qui"], color_discrete_map=COLORS,
                         title=f"Magasin le plus fréquenté par chaque foyer (semaine {wsel})")
        for i, (sx, sy) in enumerate(POSITIONS_MAGASINS):
            fig.add_trace(go.Scatter(x=[sx], y=[sy], mode="markers+text", marker={"size": 22, "symbol": "square", "color": COLORS[stores[i]]},
                                     text=[names[stores[i]]], textposition="bottom center", showlegend=False))
        fig.add_trace(go.Scatter(x=[POSITION_MARCHE[0]], y=[POSITION_MARCHE[1]], mode="markers+text", marker={"size": 16, "symbol": "star", "color": COLORS["marche"]},
                                 text=["marché"], textposition="bottom center", showlegend=False))
        fig.update_layout(height=650, xaxis_range=[-1, 20], yaxis_range=[-1, 20])
        st.plotly_chart(fig, use_container_width=True)

elif page == "IA":
    st.title("Usage des modèles (abonnement Claude ou ChatGPT)")
    llm = q(run, "SELECT * FROM appels_llm")
    if llm.empty:
        st.info("Aucun appel aux modèles dans cette partie (mode rapide).")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Appels", len(llm))
        c2.metric("Échecs", int((llm.ok == 0).sum()))
        c3.metric("Jetons en entrée", f"{llm.jetons_entree.sum() / 1e6:.2f} M")
        c4.metric("Attente cumulée", f"{llm.duree.sum() / 60:.0f} min")
        by = llm.groupby("role", as_index=False).agg(appels=("id", "count"), entree=("jetons_entree", "sum"), sortie=("jetons_sortie", "sum"),
                                                     duree_moy=("duree", "mean"), echecs=("ok", lambda s: int((s == 0).sum())))
        st.dataframe(by, use_container_width=True, hide_index=True)
        llm["semaine"] = llm["jour"].fillna(0).astype(int) // 7 + 1
        st.plotly_chart(px.bar(llm.groupby(["semaine", "role"], as_index=False).size(), x="semaine", y="size", color="role", title="Appels par semaine"),
                        use_container_width=True)
        err = llm[llm.ok == 0][["jour", "role", "erreur"]]
        if not err.empty:
            st.subheader("Erreurs")
            st.dataframe(err.tail(50), use_container_width=True, hide_index=True)

elif page == "Rapport et interviews":
    st.title("Rapport et interviews")
    rep = Path(run) / "rapport_final.md"
    if st.button("Écrire (ou réécrire) le rapport de fin de partie avec l'IA"):
        from simarche.analyse import final_report
        with st.spinner("L'IA analyse la partie…"):
            asyncio.run(final_report(Path(run)))
    if rep.exists():
        st.markdown(rep.read_text(encoding="utf-8"))
    st.subheader("Interviewer un agent")
    pop = population(run)
    agent = st.selectbox("Agent", stores + sorted(pop), format_func=lambda a: f"{a} — {names.get(a) or pop.get(a, {}).get('attributs', {}).get('prenom', '')}")
    question = st.text_input("Question", "Pourquoi as-tu choisi ce magasin ces dernières semaines ?")
    if st.button("Poser la question"):
        from simarche.analyse import interview
        with st.spinner("Réponse en cours…"):
            st.write(asyncio.run(interview(Path(run), agent, None, question, [])))
