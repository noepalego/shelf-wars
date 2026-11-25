"use strict";
// Visite 3D des deux magasins à chaque instant enregistré (window.SIMARCHE_SCENES, produit par « python -m simarche scenes »).
// Rendu maison sur canvas 2D (aucune bibliothèque) : la page fonctionne hors connexion, ouverte directement depuis le disque.
(() => {
  const DATA = window.SIMARCHE_SCENES;
  const $ = id => document.getElementById(id);
  if (!DATA || !Array.isArray(DATA.games) || !DATA.games.length) {
    $("load-error").hidden = false;
    return;
  }
  $("studio-content").hidden = false;

  const ROOM = DATA.room, PRODUCTS = DATA.products, LEVELS = ROOM.levels;   // hauteurs des étagères (m)
  const AISLE_NAMES = DATA.aisles;
  const SHOP_COLORS = ["#2f6f9f", "#c0603a"];
  const NEED_COLORS = {pain: "#c98a3d", lait: "#a9c3e6", laitiers: "#efe2a8", oeufs: "#e3bb7d", proteines: "#c4574e",
    feculents: "#d8b86a", fruits: "#e8743c", legumes: "#5e9e4b", epicerie: "#a8763f", petit_dej: "#8a5a3b", snacks: "#d86aa0",
    boissons: "#3f82c4", hygiene: "#7ab8b4", entretien: "#6b7fd4", bebe: "#f2a7c3", animaux: "#8e7b5c", plats: "#b5623b"};
  const LEVEL_LABELS = {bas: "tout en bas", mains: "à hauteur des mains", yeux: "à hauteur des yeux", haut: "en haut"};
  const DISPLAY_LABELS = {entree: "entrée", tete_gondole_1: "tête de gondole 1", tete_gondole_2: "tête de gondole 2", caisse: "caisses"};
  const SLOT_LABELS = {matin: "matin", midi: "midi", soir: "soir", jour: "journée", cloture: "clôture"};
  const RANGE_LABELS = {mdd: "Marque distributeur", marque: "Marque", premium: "Premium", bio: "Bio"};
  const WEATHER = {soleil: "beau temps", nuageux: "nuageux", pluie: "pluie"};
  const euro = new Intl.NumberFormat("fr-FR", {style: "currency", currency: "EUR"});
  const euro0 = new Intl.NumberFormat("fr-FR", {style: "currency", currency: "EUR", maximumFractionDigits: 0});
  const GAP = ROOM.depth + 2.5;                       // les deux magasins sont posés l'un à côté de l'autre (axe z)
  const HOME = {both: {yaw: 1.42, pitch: 0.7, dist: 37}, one: {yaw: 1.22, pitch: 0.6, dist: 20}};
  const G = ROOM.gondola || {halfWidth: 0.43, halfLength: 1.7, height: 2.2};

  // ------------------------------------------------------------------ état
  const state = {game: 0, frame: 0, view: "both", shop: null, product: null, slot: "all",
    yaw: HOME.both.yaw, pitch: HOME.both.pitch, dist: HOME.both.dist, playing: false, timer: null, route: true};
  let game, resolved, visible = [], picks = [];

  function resolveGame(g) {
    // Reconstruit les prix et les stocks complets de chaque instant (l'export ne garde que les changements).
    const last = {};
    return g.frames.map(f => {
      const out = {};
      for (const [sid, st] of Object.entries(f.stores)) {
        const [rows, displays] = g.layouts[st.layout];
        const prev = last[sid];
        const take = key => {
          if (st[key]) return st[key].slice();
          const base = prev && prev.layout === st.layout ? prev[key].slice() : new Array(rows.length).fill(0);
          const patch = st[key + "Patch"] || [];
          for (let i = 0; i < patch.length; i += 2) base[patch[i]] = patch[i + 1];
          return base;
        };
        const full = {...st, rows, displays, prices: take("prices"), stock: take("stock"), offersMap: g.offers[st.offers] || {}};
        out[sid] = full;
        last[sid] = full;
      }
      return out;
    });
  }

  // ------------------------------------------------------------------ outils
  const make = (tag, parent, text, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    if (parent) parent.appendChild(el);
    return el;
  };
  const shade = (hex, k) => {
    const n = parseInt(hex.slice(1), 16);
    const c = [n >> 16 & 255, n >> 8 & 255, n & 255].map(v => Math.max(0, Math.min(255, Math.round(v * k))));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  };
  const mix = (hex, other, t) => {
    const a = parseInt(hex.slice(1), 16), b = parseInt(other.slice(1), 16);
    const c = [16, 8, 0].map(s => Math.round(((a >> s) & 255) * (1 - t) + ((b >> s) & 255) * t));
    return "#" + c.map(v => v.toString(16).padStart(2, "0")).join("");
  };
  const shopIndex = sid => game.shops.findIndex(s => s.id === sid);
  const shopName = sid => (game.shops.find(s => s.id === sid) || {}).name || sid;
  const frameLabel = f => `${f.label || f.date} · ${SLOT_LABELS[f.slot] || f.slot}`;

  // ------------------------------------------------------------------ caméra et projection
  const canvas = $("scene"), ctx = canvas.getContext("2d");
  let W = 0, H = 0, DPR = 1, eye, basis, focal;
  function resize() {
    const r = canvas.getBoundingClientRect();
    DPR = Math.min(2, window.devicePixelRatio || 1);
    W = Math.max(1, r.width); H = Math.max(1, r.height);
    canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
    draw();
  }
  function camera() {
    const target = [0, 0.7, 0];
    const cp = Math.cos(state.pitch);
    eye = [target[0] + state.dist * cp * Math.sin(state.yaw), target[1] + state.dist * Math.sin(state.pitch),
           target[2] - state.dist * cp * Math.cos(state.yaw)];
    const f = norm(sub(target, eye));
    const r = norm(cross(f, [0, 1, 0]));
    const u = cross(r, f);
    basis = {f, r, u};
    focal = Math.max(Math.min(W, H), 0.75 * Math.max(W, H)) * 1.05;
  }
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const norm = a => { const l = Math.hypot(...a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
  function project(p) {
    const v = sub(p, eye);
    const z = dot(v, basis.f);
    if (z < 0.2) return null;
    return [W / 2 + focal * dot(v, basis.r) / z, H / 2 - focal * dot(v, basis.u) / z, z];
  }
  const LIGHT = norm([0.35, 1, -0.45]);

  // ------------------------------------------------------------------ scène
  // Algorithme du peintre par groupes : les groupes (une gondole et ses produits, un présentoir, un mur…) sont triés
  // du plus loin au plus proche ; à l'intérieur d'un groupe, l'ordre des couches dépend du côté où se trouve la caméra.
  let groups = [], floorFaces = [], lines = [], labels = [];
  function newGroup(anchor) { const g = {anchor, layers: []}; groups.push(g); return g; }
  function layer(g) { const l = []; g.layers.push(l); return l; }
  function quad(target, points, normal, color, ref) {
    const center = points.reduce((a, p) => [a[0] + p[0] / 4, a[1] + p[1] / 4, a[2] + p[2] / 4], [0, 0, 0]);
    if (dot(normal, sub(eye, center)) <= 0) return;
    const light = 0.62 + 0.38 * Math.max(0, dot(normal, LIGHT));
    target.push({points, color: shade(color, light), ref, center});
  }
  function box(target, x0, y0, z0, x1, y1, z1, color, ref, top) {
    quad(target, [[x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1]], [0, 1, 0], top || color, ref);
    quad(target, [[x1, y0, z0], [x1, y0, z1], [x1, y1, z1], [x1, y1, z0]], [1, 0, 0], color, ref);
    quad(target, [[x0, y0, z1], [x0, y0, z0], [x0, y1, z0], [x0, y1, z1]], [-1, 0, 0], color, ref);
    quad(target, [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]], [0, 0, -1], color, ref);
    quad(target, [[x1, y0, z1], [x0, y0, z1], [x0, y1, z1], [x1, y1, z1]], [0, 0, 1], color, ref);
  }
  function solid(x0, y0, z0, x1, y1, z1, color, ref, top) {
    const g = newGroup([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2]);
    box(layer(g), x0, y0, z0, x1, y1, z1, color, ref, top);
    return g;
  }

  function buildStore(sid, st, oz) {
    const i = shopIndex(sid), tint = SHOP_COLORS[i % 2];
    const hw = ROOM.width / 2, hd = ROOM.depth / 2;
    floorFaces.push({points: [[-hw, 0, oz - hd], [hw, 0, oz - hd], [hw, 0, oz + hd], [-hw, 0, oz + hd]],
                     color: st.closed ? "#d9d6d2" : mix(tint, "#f4f2ea", 0.86)});
    // Murs bas sur trois côtés (le côté caméra reste ouvert), façade avec l'entrée.
    solid(-hw, 0, oz + hd - 0.12, hw, 0.9, oz + hd, "#d9dcd2");
    solid(-hw, 0, oz - hd, -hw + 0.12, 0.9, oz + hd, "#d9dcd2");
    const door = ROOM.entrance;
    solid(-hw, 0, oz - hd, door[0] - 1.1, 0.35, oz - hd + 0.12, "#cfd4c6");
    solid(door[0] + 1.1, 0, oz - hd, hw, 0.35, oz - hd + 0.12, "#cfd4c6");
    if (state.dist < 26) labels.push({p: [door[0], 0.05, oz - hd - 0.45], text: "entrée", cls: "small"});
    labels.push({p: [-hw, 1.6, oz], text: `${sid} · ${shopName(sid)}`, cls: "title", color: tint});
    if (st.closed) labels.push({p: [0, 2.7, oz], text: "FERMÉ (faillite)", cls: "title", color: "#7a3030"});
    // Gondoles : un dos plein, des tablettes ouvertes vers +x (côté allée où passent les clients).
    const back = G.halfWidth, front = ROOM.faceOffset;
    const shelves = {};
    for (const a of ROOM.aisles) {
      const cx = a.x, cz = oz + a.z;
      const g = newGroup([cx, G.height / 2, cz]);
      const spine = [], fill = [];
      box(spine, cx - back, 0, cz - G.halfLength, cx - 0.08, G.height, cz + G.halfLength, "#e4e1d8", null, "#f4f3ee");
      box(fill, cx - 0.08, 0, cz - G.halfLength, cx + front, 0.12, cz + G.halfLength, "#cfcabd");
      for (const off of ROOM.segmentOffsets) for (const h of LEVELS) {
        box(fill, cx - 0.08, h - 0.04, cz + off - 0.425, cx + front + 0.03, h, cz + off + 0.425, "#bdb8ac");
      }
      // Caméra côté allée : le dos d'abord, puis tablettes et produits ; sinon le dos les masque.
      g.layers = eye[0] > cx - 0.08 ? [spine, fill] : [fill, spine];
      shelves[a.id] = fill;
      if (state.dist < 26) labels.push({p: [cx, G.height + 0.3, cz], text: `${a.id} · ${AISLE_NAMES[a.id]}`, cls: "small"});
    }
    // Produits en rayon : largeur selon les facings, hauteur selon le stock.
    st.rows.forEach((row, k) => {
      const [pi, aisle, seg, lvl, facings] = row;
      const prod = PRODUCTS[pi], a = ROOM.aisles[aisle];
      const cx = a.x, cz = oz + a.z + ROOM.segmentOffsets[seg], y = LEVELS[lvl];
      const width = 0.2 + 0.2 * facings, stock = st.stock[k];
      const height = stock > 0 ? 0.12 + Math.min(1, stock / 18) * 0.3 : 0.04;
      const offer = st.offersMap[String(pi)] || {};
      const selected = state.product === pi && state.shop === sid;
      let color = NEED_COLORS[prod.need] || "#999999";
      if (stock <= 0) color = "#c9c6c0";
      const ref = {sid, pi, k}, target = shelves[aisle];
      box(target, cx - 0.02, y, cz - width / 2, cx + front - 0.02, y + height, cz + width / 2,
          selected ? "#ffe066" : color, ref, offer.promo ? "#f2c230" : undefined);
      if (offer.promo) box(target, cx + front - 0.02, y + 0.02, cz - 0.08, cx + front + 0.01, y + 0.14, cz + 0.08, "#e2a400", ref);
      if (offer.short) box(target, cx + front - 0.02, y + 0.02, cz + width / 2 - 0.1, cx + front + 0.01, y + 0.1, cz + width / 2, "#ef7a2f", ref);
      if (selected) labels.push({p: [cx + front + 0.35, y + height + 0.25, cz], text: euro.format(st.prices[k] / 100), cls: "price"});
    });
    // Présentoirs (entrée, têtes de gondole, caisses).
    const top = (ROOM.displayHeight || 1.1) - 0.25;
    for (const [name, pos] of Object.entries(ROOM.secondary)) {
      const [x, dz] = pos, z = oz + dz;
      const items = (st.displays || {})[name] || [];
      const g = solid(x - 0.45, 0, z - 0.35, x + 0.45, top, z + 0.35, items.length ? "#d8cdb6" : "#e6e2d8");
      const goods = layer(g);
      items.forEach((pi, j) => {
        const w = 0.8 / items.length;
        const k = st.rows.findIndex(r => r[0] === pi);
        const selected = state.product === pi && state.shop === sid;
        box(goods, x - 0.4 + j * w + 0.03, top, z - 0.25, x - 0.4 + (j + 1) * w - 0.03, top + 0.3, z + 0.25,
            selected ? "#ffe066" : (NEED_COLORS[PRODUCTS[pi].need] || "#999"), {sid, pi, k});
      });
    }
    // Caisses ouvertes à ce créneau.
    const [kx, kz] = ROOM.checkout;
    for (let r = 0; r < 3; r++) {
      const open = r < (st.registers || 1);
      solid(kx - 2.6 + r * 1.25, 0, oz + kz - 0.35, kx - 1.7 + r * 1.25, 0.95, oz + kz + 0.25, open ? "#4d6b57" : "#cfcfc8");
    }
    if (state.dist < 26) labels.push({p: [kx - 1.4, 1.3, oz + kz + 0.3], text: `${st.registers || 1} caisse(s) ouverte(s)`, cls: "small"});
    // Trajets des clients pendant ce créneau.
    if (state.route) {
      (st.trips || []).forEach((t, j) => {
        lines.push({points: t.path.map(([x, z]) => [x, 0.03, oz + z]), color: mix(tint, "#000000", 0.15 + 0.07 * (j % 4)),
                    width: j === 0 ? 3 : 1.6, dash: j === 0 ? [] : [5, 4]});
      });
    }
  }

  function buildScene() {
    groups = []; floorFaces = []; lines = []; labels = [];
    const fr = resolved[state.frame];
    const sids = game.shops.map(s => s.id).filter(sid => fr[sid] && (state.view === "both" || state.view === sid));
    sids.forEach((sid, n) => buildStore(sid, fr[sid], sids.length === 2 ? (n === 0 ? GAP / 2 : -GAP / 2) : 0));
  }

  function draw() {
    if (!W || !game) return;
    camera();
    buildScene();
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const poly = (pts, fill, stroke) => {
      const scr = pts.map(project);
      if (scr.some(p => !p)) return null;
      ctx.beginPath();
      scr.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.closePath();
      ctx.fillStyle = fill;
      ctx.fill();
      if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 0.6; ctx.stroke(); }
      return scr;
    };
    for (const f of floorFaces) poly(f.points, f.color, "#b8bdb0");
    // Quadrillage léger du sol.
    ctx.strokeStyle = "rgba(60,70,60,.07)"; ctx.lineWidth = 1;
    for (const f of floorFaces) {
      const [x0, , z0] = f.points[0], [x1, , z1] = f.points[2];
      for (let x = Math.ceil(x0); x < x1; x++) segment([x, 0.005, z0], [x, 0.005, z1]);
      for (let z = Math.ceil(z0); z < z1; z++) segment([x0, 0.005, z], [x1, 0.005, z]);
    }
    for (const l of lines) polyline(l);
    const depth = p => dot(sub(p, eye), basis.f);
    groups.forEach(g => { g.depth = depth(g.anchor); });
    groups.sort((a, b) => b.depth - a.depth);
    picks = [];
    for (const g of groups) {
      for (const list of g.layers) {
        list.forEach(f => { f.depth = depth(f.center); });
        list.sort((a, b) => b.depth - a.depth);
        for (const f of list) {
          const scr = poly(f.points, f.color, "rgba(40,40,40,.18)");
          if (scr && f.ref) picks.push({scr, ref: f.ref});
        }
      }
    }
    for (const l of labels) {
      const p = project(l.p);
      if (!p) continue;
      ctx.font = l.cls === "title" ? "600 15px 'DM Sans','Segoe UI',sans-serif" : l.cls === "price" ? "700 13px 'DM Sans','Segoe UI',sans-serif"
        : "11px 'DM Sans','Segoe UI',sans-serif";
      ctx.textAlign = "center";
      if (l.cls === "price") {
        const w = ctx.measureText(l.text).width + 12;
        ctx.fillStyle = "#23362e"; ctx.fillRect(p[0] - w / 2, p[1] - 15, w, 20);
        ctx.fillStyle = "#fff"; ctx.fillText(l.text, p[0], p[1]);
      } else {
        ctx.fillStyle = l.color || "rgba(40,50,42,.75)";
        ctx.fillText(l.text, p[0], p[1]);
      }
    }
  }
  function segment(a, b) {
    const p = project(a), q = project(b);
    if (!p || !q) return;
    ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.stroke();
  }
  function polyline(l) {
    const pts = l.points.map(project);
    if (pts.some(p => !p)) return;
    ctx.save();
    ctx.strokeStyle = l.color; ctx.lineWidth = l.width; ctx.setLineDash(l.dash); ctx.lineJoin = "round";
    ctx.beginPath();
    pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.stroke();
    const end = pts[pts.length - 1];
    ctx.setLineDash([]); ctx.fillStyle = l.color;
    ctx.beginPath(); ctx.arc(end[0], end[1], l.width + 2, 0, 2 * Math.PI); ctx.fill();
    ctx.restore();
  }
  function pick(x, y) {
    for (let i = picks.length - 1; i >= 0; i--) {
      const s = picks[i].scr;
      let inside = false;
      for (let a = 0, b = s.length - 1; a < s.length; b = a++) {
        if ((s[a][1] > y) !== (s[b][1] > y) && x < (s[b][0] - s[a][0]) * (y - s[a][1]) / (s[b][1] - s[a][1]) + s[a][0]) inside = !inside;
      }
      if (inside) return picks[i].ref;
    }
    return null;
  }

  // ------------------------------------------------------------------ interactions
  let drag = null;
  canvas.addEventListener("pointerdown", e => { drag = {x: e.clientX, y: e.clientY, yaw: state.yaw, pitch: state.pitch, moved: false}; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", e => {
    const r = canvas.getBoundingClientRect();
    if (drag) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      state.yaw = drag.yaw - dx * 0.008;
      state.pitch = Math.max(0.12, Math.min(1.45, drag.pitch + dy * 0.006));
      draw();
      return;
    }
    const ref = pick(e.clientX - r.left, e.clientY - r.top);
    const tip = $("tip");
    if (ref) {
      const st = resolved[state.frame][ref.sid];
      const k = ref.k >= 0 ? ref.k : st.rows.findIndex(rw => rw[0] === ref.pi);
      tip.hidden = false;
      tip.textContent = `${ref.sid} · ${PRODUCTS[ref.pi].name} — ${k >= 0 ? euro.format(st.prices[k] / 100) + ", stock " + st.stock[k] : ""}`;
    } else tip.hidden = true;
  });
  canvas.addEventListener("pointerup", e => {
    const r = canvas.getBoundingClientRect();
    if (drag && !drag.moved) {
      const ref = pick(e.clientX - r.left, e.clientY - r.top);
      if (ref) select(ref.sid, ref.pi);
    }
    drag = null;
  });
  canvas.addEventListener("wheel", e => { e.preventDefault(); zoom(e.deltaY > 0 ? 1.1 : 0.9); }, {passive: false});
  canvas.addEventListener("keydown", e => {
    const keys = {ArrowLeft: () => rotate(-0.12), ArrowRight: () => rotate(0.12), ArrowUp: () => tilt(0.08), ArrowDown: () => tilt(-0.08),
                  "+": () => zoom(0.9), "=": () => zoom(0.9), "-": () => zoom(1.1)};
    if (keys[e.key]) { e.preventDefault(); keys[e.key](); }
  });
  const rotate = d => { state.yaw += d; draw(); };
  const tilt = d => { state.pitch = Math.max(0.12, Math.min(1.45, state.pitch + d)); draw(); };
  const zoom = k => { state.dist = Math.max(5, Math.min(60, state.dist * k)); draw(); };
  $("left").onclick = () => rotate(-0.25);
  $("right").onclick = () => rotate(0.25);
  $("minus").onclick = () => zoom(1.15);
  $("plus").onclick = () => zoom(0.87);
  $("home").onclick = () => { Object.assign(state, HOME[state.view === "both" ? "both" : "one"]); draw(); };
  $("route").checked = state.route;
  $("route").onchange = e => { state.route = e.target.checked; draw(); };
  window.addEventListener("resize", resize);

  // ------------------------------------------------------------------ fiche produit
  function select(sid, pi) {
    state.shop = sid;
    state.product = pi;
    $("shop").value = sid;
    fillAisles();
    const st = resolved[state.frame][sid];
    const row = st.rows.find(r => r[0] === pi);
    $("aisle").value = row ? String(row[1]) : $("aisle").value;
    fillProducts();
    $("product").value = String(pi);
    refreshInspector();
    draw();
  }
  function fillShops() {
    const s = $("shop");
    s.innerHTML = "";
    for (const shop of game.shops) make("option", s, `${shop.id} · ${shop.name}`).value = shop.id;
    s.value = state.shop;
    s.onchange = () => { state.shop = s.value; fillAisles(); fillProducts(); pickFirst(); };
  }
  function fillAisles() {
    const s = $("aisle"), keep = s.value;
    s.innerHTML = "";
    for (const [id, name] of Object.entries(AISLE_NAMES)) make("option", s, `${id} · ${name}`).value = id;
    s.value = keep || "0";
    s.onchange = () => { fillProducts(); pickFirst(); };
  }
  function fillProducts() {
    const s = $("product"), st = resolved[state.frame][state.shop];
    s.innerHTML = "";
    const rows = st.rows.filter(r => String(r[1]) === $("aisle").value).sort((a, b) => a[2] - b[2] || b[3] - a[3]);
    for (const r of rows) make("option", s, `${PRODUCTS[r[0]].name} (segment ${r[2]}, ${LEVEL_LABELS[LEVELS_NAMES[r[3]]]})`).value = String(r[0]);
    s.onchange = () => { state.product = Number(s.value); refreshInspector(); draw(); };
  }
  const LEVELS_NAMES = DATA.levels || ["bas", "mains", "yeux", "haut"];   // noms des niveaux (export)
  function pickFirst() {
    const s = $("product");
    state.product = s.options.length ? Number(s.options[0].value) : null;
    if (state.product !== null) s.value = String(state.product);
    refreshInspector();
    draw();
  }
  function refreshInspector() {
    const pi = state.product, sid = state.shop;
    const st = resolved[state.frame][sid];
    const numbers = $("numbers"), loc = $("location");
    numbers.innerHTML = ""; loc.innerHTML = "";
    if (pi === null || pi === undefined) return;
    const prod = PRODUCTS[pi], k = st.rows.findIndex(r => r[0] === pi);
    $("item-swatch").style.background = NEED_COLORS[prod.need] || "#999";
    $("item-range").textContent = `${RANGE_LABELS[prod.range] || prod.range} · ${prod.need.replace("_", " ")}`;
    $("item-name").textContent = prod.name;
    const cell = (label, value) => { const d = make("div", numbers); make("span", d, label); make("strong", d, value); };
    if (k < 0) {
      cell("Prix affiché", "—"); cell("Stock", "non référencé");
      $("offer").textContent = `Ce produit n'est pas vendu par ${shopName(sid)} à cet instant.`;
    } else {
      cell("Prix affiché", euro.format(st.prices[k] / 100));
      cell("Stock en magasin", String(st.stock[k]));
      const other = game.shops.map(s => s.id).find(x => x !== sid);
      const ost = other && resolved[state.frame][other];
      const ok = ost ? ost.rows.findIndex(r => r[0] === pi) : -1;
      cell(`Chez ${other}`, ok >= 0 ? euro.format(ost.prices[ok] / 100) : "absent");
      cell("Prix conseillé", euro.format(prod.list));
      const offer = st.offersMap[String(pi)] || {};
      const bits = [];
      if (offer.promo) bits.push(offer.promo);
      if (offer.member !== undefined) bits.push(`prix membre ${euro.format(offer.member / 100)}`);
      if (offer.short) bits.push(`date courte ${euro.format(offer.short[0] / 100)} (${offer.short[1]} unités)`);
      $("offer").textContent = bits.length ? "Offre : " + bits.join(" · ") : "Aucune offre particulière à cet instant.";
      const [, aisle, seg, lvl, facings] = st.rows[k];
      const shows = Object.entries(st.displays || {}).filter(([, v]) => v.includes(pi)).map(([n]) => DISPLAY_LABELS[n] || n);
      for (const [dt, dd] of [["Rayon", `${aisle} · ${AISLE_NAMES[aisle]}`], ["Segment", String(seg)],
        ["Hauteur", LEVEL_LABELS[LEVELS_NAMES[lvl]]], ["Largeur", `${facings} facing(s)`], ["Mises en avant", shows.join(", ") || "aucune"]]) {
        make("dt", loc, dt); make("dd", loc, dd);
      }
    }
    priceChart();
  }

  // ------------------------------------------------------------------ graphiques
  function prepCanvas(c) {
    const r = c.getBoundingClientRect();
    c.width = Math.round(r.width * DPR); c.height = Math.round(r.height * DPR);
    const g = c.getContext("2d");
    g.setTransform(DPR, 0, 0, DPR, 0, 0);
    g.clearRect(0, 0, r.width, r.height);
    return [g, r.width, r.height];
  }
  function chart(c, series, cursor, fmt) {
    const [g, w, h] = prepCanvas(c);
    const vals = series.flatMap(s => s.values.filter(v => v !== null));
    if (!vals.length) return [null, null];
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    const n = resolved.length, X = i => 4 + (w - 8) * (n > 1 ? i / (n - 1) : 0.5), Y = v => h - 6 - (h - 14) * (v - lo) / (hi - lo);
    g.strokeStyle = "rgba(0,0,0,.08)"; g.beginPath(); g.moveTo(0, Y(lo)); g.lineTo(w, Y(lo)); g.stroke();
    for (const s of series) {
      g.strokeStyle = s.color; g.lineWidth = s.bold ? 2 : 1.2; g.beginPath();
      let started = false;
      s.values.forEach((v, i) => {
        if (v === null) { started = false; return; }
        if (!started) { g.moveTo(X(i), Y(v)); started = true; } else g.lineTo(X(i), Y(v));
      });
      g.stroke();
    }
    g.strokeStyle = "#5f4694"; g.lineWidth = 1; g.beginPath(); g.moveTo(X(cursor), 0); g.lineTo(X(cursor), h); g.stroke();
    return [lo, hi].map(fmt);
  }
  function priceChart() {
    const pi = state.product;
    const series = game.shops.map((s, i) => ({color: SHOP_COLORS[i % 2], bold: s.id === state.shop,
      values: resolved.map(fr => { const st = fr[s.id]; const k = st ? st.rows.findIndex(r => r[0] === pi) : -1; return k >= 0 ? st.prices[k] / 100 : null; })}));
    const [lo, hi] = chart($("price-chart"), series, state.frame, v => euro.format(v));
    $("price-range").textContent = lo ? `${lo} – ${hi} · ${game.shops.map((s, i) => s.id).join(" / ")}` : "";
  }
  function profitChart() {
    const series = game.shops.map((s, i) => ({color: SHOP_COLORS[i % 2], bold: true, values: resolved.map(fr => fr[s.id] ? fr[s.id].profit / 100 : null)}));
    chart($("profit-chart"), series, state.frame, v => euro0.format(v));
  }

  // ------------------------------------------------------------------ chronologie et comptes
  function filterFrames() {
    visible = resolved.map((_, i) => i).filter(i => state.slot === "all" || game.frames[i].slot === state.slot);
    const t = $("time");
    t.max = String(Math.max(0, visible.length - 1));
    let pos = visible.indexOf(state.frame);
    if (pos < 0) { pos = visible.findIndex(i => i >= state.frame); if (pos < 0) pos = visible.length - 1; state.frame = visible[pos] ?? 0; }
    t.value = String(pos);
  }
  function setFrame(i) {
    state.frame = i;
    const f = game.frames[i];
    $("time-name").textContent = frameLabel(f);
    const fr = resolved[i];
    const visits = game.shops.map(s => `${s.id} : ${fr[s.id] ? fr[s.id].visits : 0} passage(s)`).join(" · ");
    const weather = f.weather ? `${WEATHER[f.weather.weather] || f.weather.weather}${f.weather.heat ? " (canicule)" : ""} · ` : "";
    $("time-info").textContent = `${weather}${visits}${f.slot === "cloture" ? " dans la journée" : " pendant ce créneau"}`;
    $("stage-date").textContent = `${f.date} · ${SLOT_LABELS[f.slot] || f.slot}`;
    $("prev").disabled = visible.indexOf(i) <= 0;
    $("next").disabled = visible.indexOf(i) >= visible.length - 1;
    $("time").value = String(Math.max(0, visible.indexOf(i)));
    scoreboard();
    if (state.shop && !$("product").options.length) fillProducts();
    refreshInspector();
    profitChart();
    programs();
    draw();
  }
  function scoreboard() {
    const box = $("scoreboard"), fr = resolved[state.frame];
    box.innerHTML = "";
    game.shops.forEach((s, i) => {
      const st = fr[s.id];
      if (!st) return;
      const row = make("div", box, undefined, "score");
      const dot = make("i", row); dot.style.background = SHOP_COLORS[i % 2];
      make("span", row, `${s.id} · ${s.name}${st.closed ? " (fermé)" : ""}`);
      make("strong", row, `${euro0.format(st.profit / 100)}`);
      const line = text => { const el = make("small", box, text, "fine"); el.style.display = "block"; return el; };
      line(`trésorerie ${euro0.format(st.cash / 100)} · ${st.members || 0} membres fidélité · ${st.esl ? "étiquettes électroniques" : "étiquettes papier"}`
        + (st.slogan ? ` · « ${st.slogan} »` : ""));
      const staff = st.staff || [];
      const slot = game.frames[state.frame] ? game.frames[state.frame].slot : "";
      line(staff.length
        ? `${slot === "cloture" ? "personnel du jour" : "en poste"} : ${staff.map(p => `${p.name} (${p.role})`).join(", ")} · ${st.registers || 1} caisse(s)`
        : `pas de salarié ${slot === "cloture" ? "ce jour-là" : "sur ce créneau"} · ${st.registers || 1} caisse(s) ouverte(s)`);
      (st.systems || []).forEach(sys => line(
        `Équipement « ${sys.name} » (${sys.state === "actif" ? "en service" : "commandé"}${sys.by ? ", idée " + (sys.by === "marketing" ? "du marketing" : "du gérant") : ""}) : `
        + `${sys.what || ""}${sys.visible ? " Vu par les clients : " + sys.visible : ""}`));
    });
  }
  const managerLabel = m => m === "ia" ? ((game.team || []).length > 1 ? "direction IA : gérant + responsable marketing et clients (sessions + programmes)"
      : "gérant IA (sessions de réflexion + programmes)") : m === "auteur" ? "gérant auteur d'un programme"
    : m && m.startsWith("regles") ? `gérant à règles fixes (${m.split(":")[1] || "equilibre"})` : m;
  function programs() {
    const sid = state.shop, st = resolved[state.frame][sid], shop = game.shops.find(s => s.id === sid);
    const model = game.models && game.models.gerant ? ` · ${game.models.gerant}` : "";
    $("origin").textContent = `${sid} · ${managerLabel(shop.manager)}${shop.manager === "ia" || shop.manager === "auteur" ? model : ""}`;
    const sel = $("versions"), keep = sel.value;
    sel.innerHTML = "";
    const mine = game.programs.map((p, i) => [p, i]).filter(([p]) => p.store === sid);
    const active = new Set((st.programs || []).filter(([, on]) => on).map(([i]) => i));
    if (!mine.length) {
      $("version-label").hidden = true; $("source-panel").hidden = true;
      $("note").textContent = "Aucun programme écrit par ce gérant pour l'instant.";
      return;
    }
    $("version-label").hidden = false; $("source-panel").hidden = false;
    for (const [p, i] of mine) {
      make("option", sel, `${p.name} · v${p.version} (jour ${p.day})${active.has(i) ? " · actif" : ""}`).value = String(i);
    }
    const current = [...active][0];
    sel.value = mine.some(([, i]) => String(i) === keep) ? keep : String(current ?? mine[mine.length - 1][1]);
    const show = () => {
      const p = game.programs[Number(sel.value)];
      const prov = [p.moteur, p.modele].filter(Boolean).join(", ");
      $("note").textContent = (p.note ? `Note du gérant : ${p.note}` : "") + (prov ? ` (${prov})` : "");
      $("source").textContent = p.code || "(source indisponible)";
    };
    sel.onchange = show;
    show();
  }

  // ------------------------------------------------------------------ lecture
  $("prev").onclick = () => { const p = visible.indexOf(state.frame); if (p > 0) setFrame(visible[p - 1]); };
  $("next").onclick = () => { const p = visible.indexOf(state.frame); if (p < visible.length - 1) setFrame(visible[p + 1]); };
  $("time").oninput = e => setFrame(visible[Number(e.target.value)] ?? 0);
  $("play").onclick = () => {
    state.playing = !state.playing;
    $("play").textContent = state.playing ? "Pause" : "Lire";
    clearInterval(state.timer);
    if (state.playing) state.timer = setInterval(() => {
      const p = visible.indexOf(state.frame);
      if (p >= visible.length - 1) { $("play").click(); return; }
      setFrame(visible[p + 1]);
    }, 450);
  };
  const slotSel = $("slots");
  for (const [v, t] of [["all", "Tous les instants"], ["matin", "Matins"], ["midi", "Midis"], ["soir", "Soirs"], ["cloture", "Clôtures"]]) make("option", slotSel, t).value = v;
  slotSel.onchange = () => { state.slot = slotSel.value; filterFrames(); setFrame(state.frame); };

  function views() {
    const box = $("views");
    box.innerHTML = "";
    const opts = [...game.shops.map(s => [s.id, `${s.id} · ${s.name}`]), ["both", "Les deux"]];
    for (const [v, t] of opts) {
      const b = make("button", box, t);
      b.setAttribute("aria-pressed", String(state.view === v));
      b.onclick = () => {
        state.view = v;
        Object.assign(state, HOME[v === "both" ? "both" : "one"]);
        if (v !== "both") { state.shop = v; $("shop").value = v; fillProducts(); refreshInspector(); programs(); }
        views(); draw();
      };
    }
  }

  // ------------------------------------------------------------------ choix de la partie
  const gameSel = $("game");
  DATA.games.forEach((g, i) => {
    const who = g.ia ? ` · ${g.ia}` : " · sans IA";
    make("option", gameSel, `${g.name} (${g.id.slice(0, 15)})${who}`).value = String(i);
  });
  function loadGame(i) {
    state.game = i;
    game = DATA.games[i];
    resolved = resolveGame(game);
    state.shop = game.shops[0].id;
    state.frame = resolved.length - 1;
    fillShops(); fillAisles(); fillProducts(); pickFirst();
    views();
    filterFrames();
    setFrame(state.frame);
  }
  gameSel.value = String(DATA.games.length - 1);
  gameSel.onchange = () => loadGame(Number(gameSel.value));
  loadGame(DATA.games.length - 1);
  resize();
  if (typeof ResizeObserver === "function") new ResizeObserver(resize).observe($("stage"));
  window.simarcheInspect = () => ({groups: groups.length, picks: picks.length, eye, view: state.view, frame: state.frame,
    product: state.product, shop: state.shop,
    pick: picks.length ? picks[Math.floor(picks.length / 2)].scr.reduce((a, p) => [a[0] + p[0] / 4, a[1] + p[1] / 4], [0, 0]) : null,
    rows: game.shops.map(s => (resolved[state.frame][s.id] || {rows: []}).rows.length),
    layers: groups.map(g => g.layers.map(l => l.length).join("+")).slice(0, 12)});
})();
