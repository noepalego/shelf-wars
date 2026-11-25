"""Graphiques du README, tirés du journal d'une partie (SVG autonomes, thème clair et sombre).

Usage : python docs/make_charts.py runs/<partie>
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).parent / "img"
W, H = 760, 340
L, R, T, B = 64, 120, 56, 44   # marges (R : place des étiquettes directes)

STYLE = """<style>
.s{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--grid:#e6e5e0;--axis:#b9b7ae;
--a:#2a78d6;--b:#eb6834;--c:#1baf7a;--d:#eda100;--n:#8a887f}
@media (prefers-color-scheme:dark){.s{--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2e2e2c;--axis:#55544f;
--a:#3987e5;--b:#d95926;--c:#199e70;--d:#c98500;--n:#8f8d84}}
text{font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;fill:var(--ink2);font-size:12px}
.t{fill:var(--ink);font-size:15px;font-weight:600}.st{font-size:12px}.lab{font-size:12px;font-weight:600;fill:var(--ink)}
.g{stroke:var(--grid);stroke-width:1}.ax{stroke:var(--axis);stroke-width:1}
</style>"""


def svg(title: str, sub: str, body: str, h: int = H) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" class="s" role="img" '
            f'aria-label="{title}">{STYLE}<rect width="100%" height="100%" rx="10" fill="var(--bg)"/>'
            f'<text x="{L}" y="26" class="t">{title}</text><text x="{L}" y="44" class="st">{sub}</text>{body}</svg>')


def scale(lo, hi, a, b):
    return lambda v: a + (v - lo) / (hi - lo) * (b - a) if hi != lo else a


def axes_y(lo, hi, ticks, fy, fmt):
    out = []
    for v in ticks:
        y = fy(v)
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="g"/>'
                   f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end">{fmt(v)}</text>')
    return "".join(out)


def line_chart(name, title, sub, series, x_lab, y_fmt, ticks, lo, hi, x_ticks, labels):
    xs = [x for s in series for x, _ in s["pts"]]
    fx = scale(min(xs), max(xs), L, W - R)
    fy = scale(lo, hi, H - B, T + 10)
    body = axes_y(lo, hi, ticks, fy, y_fmt)
    body += f'<line x1="{L}" x2="{W - R}" y1="{H - B}" y2="{H - B}" class="ax"/>'
    for x, lab in x_ticks:
        body += f'<text x="{fx(x):.1f}" y="{H - B + 18}" text-anchor="middle">{lab}</text>'
    body += f'<text x="{W - R}" y="{H - 8}" text-anchor="end">{x_lab}</text>'
    for s in series:
        d = " ".join(f"{'M' if i == 0 else 'L'}{fx(x):.1f},{fy(y):.1f}" for i, (x, y) in enumerate(s["pts"]))
        dash = ' stroke-dasharray="5 4"' if s.get("dash") else ""
        body += f'<path d="{d}" fill="none" stroke="var(--{s["c"]})" stroke-width="2" stroke-linejoin="round"{dash}/>'
        x, y = s["pts"][-1]
        body += f'<circle cx="{fx(x):.1f}" cy="{fy(y):.1f}" r="4" fill="var(--{s["c"]})" stroke="var(--bg)" stroke-width="2"/>'
    for s, (txt, dy) in zip(series, labels):
        x, y = s["pts"][-1]
        body += f'<text x="{fx(x) + 10:.1f}" y="{fy(y) + 4 + dy:.1f}" class="lab">{txt}</text>'
    (OUT / name).write_text(svg(title, sub, body), encoding="utf-8")


def main(run: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(f"file:{run / 'journal.sqlite'}?mode=ro", uri=True)
    rows = db.execute("SELECT jour, lieu, ca, marge, visites, resultat_cumule, indice_prix FROM metriques ORDER BY jour").fetchall()
    by = defaultdict(dict)
    for j, lieu, ca, marge, vis, res, idx in rows:
        by[lieu][j] = {"ca": ca, "marge": marge, "visites": vis, "res": res, "idx": idx}
    days = sorted(by["A"])
    weeks = [(f"S{i + 1}", i * 7) for i in range(8)]
    xt = [(d, lab) for lab, d in weeks]

    # 1. Bénéfice cumulé
    a = [(d, by["A"][d]["res"]) for d in days]
    b = [(d, by["B"][d]["res"]) for d in days]
    line_chart("benefice_cumule.svg", "Bénéfice net cumulé", "8 semaines, 91 foyers simulés — partie dynamique_8s (Claude Sonnet 5.5)",
               [{"pts": a, "c": "a"}, {"pts": b, "c": "b"}], "jour simulé", lambda v: f"{v:,.0f} €".replace(",", " "),
               [0, 1000, 2000, 3000], -200, 3400, xt,
               [(f"A  {a[-1][1]:,.0f} €".replace(",", " "), 6), (f"B  {b[-1][1]:,.0f} €".replace(",", " "), -4)])

    # 2. Indice de prix hebdomadaire
    def weekly(lieu, key, agg="mean"):
        out = []
        for i in range(8):
            vals = [by[lieu][d][key] for d in range(i * 7, i * 7 + 7) if d in by[lieu]]
            out.append(sum(vals) / len(vals) if agg == "mean" else sum(vals))
        return out
    ia, ib = weekly("A", "idx"), weekly("B", "idx")
    pa = [(i + 1, v) for i, v in enumerate(ia)]
    pb = [(i + 1, v) for i, v in enumerate(ib)]
    ref = [(1, 1.0), (8, 1.0)]
    line_chart("indice_prix.svg", "Indice de prix (1,00 = prix conseillés)",
               "Moyenne hebdomadaire. Pointillé : prix conseillés. 40 % des changements de B imités par A sous 7 jours",
               [{"pts": pa, "c": "a"}, {"pts": pb, "c": "b"}, {"pts": ref, "c": "n", "dash": True}], "semaine",
               lambda v: f"{v:.2f}", [1.0, 1.02, 1.04, 1.06], 0.99, 1.07, [(i, f"S{i}") for i in range(1, 9)],
               [(f"A  {ia[-1]:.2f}", 10), (f"B  {ib[-1]:.2f}", 0), ("", 0)])

    # 3. Parts des passages, barres empilées par semaine
    places = [("A", "a", "Le Panier Malin (A)"), ("B", "b", "Chez Marcel (B)"), ("hyper", "c", "Hypermarché (hors ville)")]
    vis = {p: weekly(p, "visites", "sum") for p, _, _ in places}
    h3 = 360
    fy = scale(0, 1, h3 - B, T + 34)
    bw = (W - L - R) / 8 * 0.62
    body = axes_y(0, 1, [0, .25, .5, .75, 1], fy, lambda v: f"{v * 100:.0f} %")
    for i in range(8):
        tot = sum(vis[p][i] for p, _, _ in places) or 1
        x = L + (i + 0.5) * (W - L - R) / 8 - bw / 2
        acc = 0.0
        for p, c, _ in places:
            share = vis[p][i] / tot
            if share <= 0:
                continue
            y0, y1 = fy(acc), fy(acc + share)
            body += (f'<rect x="{x:.1f}" y="{y1 + 1:.1f}" width="{bw:.1f}" height="{max(0, y0 - y1 - 2):.1f}" rx="2" '
                     f'fill="var(--{c})"><title>S{i + 1} — {p} : {share * 100:.0f} %</title></rect>')
            if share > 0.12:
                body += f'<text x="{x + bw / 2:.1f}" y="{(y0 + y1) / 2 + 4:.1f}" text-anchor="middle" style="fill:#fff;font-weight:600">{share * 100:.0f}</text>'
            acc += share
        body += f'<text x="{x + bw / 2:.1f}" y="{h3 - B + 18}" text-anchor="middle">S{i + 1}</text>'
    lx = L
    for p, c, lab in places:  # légende en une ligne
        body += f'<rect x="{lx}" y="{T - 2}" width="12" height="12" rx="3" fill="var(--{c})"/><text x="{lx + 18}" y="{T + 8}">{lab}</text>'
        lx += 34 + len(lab) * 7
    (OUT / "parts_passages.svg").write_text(svg("Où vont les habitants ?", "Part des passages par semaine (%). Semaine 7 : A, le plus cher, perd la moitié de ses clients", body, h3),
                                            encoding="utf-8")

    # 4. Actionnaire : objectif vs résultat à la revue du 31/01
    import json
    revs = {}
    for sid, data in db.execute("SELECT acteur, donnees FROM evenements WHERE type = 'actionnaire_revue'"):
        revs[sid] = json.loads(data)
    if revs:
        h4 = 250
        body = ""
        fx = scale(0, 2600, L + 110, W - R)
        y = T + 24
        for sid, c, nom in (("A", "a", "Le Panier Malin"), ("B", "b", "Chez Marcel")):
            r = revs.get(sid)
            if not r:
                continue
            body += f'<text x="{L}" y="{y + 15}" class="lab">{nom}</text>'
            body += (f'<rect x="{L + 110}" y="{y}" width="{fx(r["resultat"]) - L - 110:.1f}" height="22" rx="4" fill="var(--{c})"/>'
                     f'<text x="{fx(r["resultat"]) + 8:.1f}" y="{y + 15}" class="lab">{r["resultat"]:,.0f} €  ({r["resultat"] / r["objectif"] * 100:.0f} % de l\'objectif)</text>'.replace(",", " "))
            ox = fx(r["objectif"])
            body += f'<line x1="{ox:.1f}" x2="{ox:.1f}" y1="{y - 6}" y2="{y + 28}" stroke="var(--ink)" stroke-width="2"/>'
            y += 60
        body += f'<text x="{fx(898):.1f}" y="{y + 2}" text-anchor="middle">objectif de l\'actionnaire : 898 €</text>'
        (OUT / "actionnaire.svg").write_text(svg("Revue de l'actionnaire, 31 janvier",
                                                 "Résultat sur 4 semaines face à l'objectif (130 %/an sur 9 000 €) : la pression était trop faible", body, h4),
                                             encoding="utf-8")
    print("graphiques écrits dans", OUT)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
