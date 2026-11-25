"use strict";

function exportCsv(data, view, state) {
  const keys=["week","profit","visits","waste","revenue","grossMargin","labelCost","coupons","issued","arm","closed","treated","control","returnedTreated","returnedControl"];
  const lines=[["experience","scenario","graine","magasin","politique","semaine","profit_cumule_eur","visites","perimes_eur","ca_eur","marge_brute_eur","frais_etiquettes_eur","bons_utilises","bons_emis","tactique","ferme","affectations_offre","affectations_temoin","retours_offre","retours_temoin"]];
  for(const [type,list] of [["comparaison",data.runs],["duel",data.duels]]){
    if(view==="duels"&&type!=="duel" || view==="observatoire"&&type!=="comparaison") continue;
    for(const r of list){
      if(view!=="methode"&&(r.scenario!==state.scenario || view==="duels"&&r.seed!==state.seed)) continue;
      for(const w of r.weeks.slice(0,view==="methode"?data.meta.weeks:state.week))
        lines.push([type,r.scenario,r.seed,r.side,r.policy||"adaptatif",...keys.map(k=>w[k]??"")]);
    }
  }
  return "\ufeff"+lines.map(row=>row.map(v=>typeof v==="number"?String(v).replace(".",","):v).join(";")).join("\r\n");
}

function init(data) {
  const {meta, runs, duels} = data;
  const get = id => document.getElementById(id);
  const colors = {temoin:"#696d68", tarification:"#ad5239", relance:"#267481", rayon:"#a16e18", fraicheur:"#398145", adaptatif:"#7951c6"};
  const labels = Object.fromEntries(meta.policies.map(p => [p.id, p.label]));
  const metrics = {profit:"Profit cumulé", visits:"Visites hebdomadaires", waste:"Périmés par semaine",
    grossMargin:"Marge brute hebdomadaire", labelCost:"Étiquettes par semaine", coupons:"Bons utilisés par semaine"};
  const currency = new Intl.NumberFormat("fr-FR", {style:"currency",currency:"EUR",maximumFractionDigits:0});
  const number = new Intl.NumberFormat("fr-FR",{maximumFractionDigits:1});
  const fmt = (v, metric) => ["profit","waste","grossMargin","labelCost"].includes(metric) ? currency.format(v) : number.format(v);
  const signed = (v, metric) => (v > 0.004 ? "+" : "") + fmt(v,metric);
  const avg = xs => xs.reduce((a,b) => a + b, 0) / xs.length;
  const sum = xs => xs.reduce((a,b) => a + b, 0);
  const state = {scenario:meta.scenarios[0].id, week:meta.weeks, metric:"profit", focus:"fraicheur", seed:meta.seeds[0]};
  const make = (tag, parent, content, cls) => {
    const element = document.createElement(tag);
    if (content !== undefined) element.textContent = content;
    if (cls) element.className = cls;
    parent.append(element);
    return element;
  };
  const reset = id => {const el = get(id); el.replaceChildren(); return el;};
  const filtered = () => runs.filter(r => r.scenario === state.scenario);
  const at = (r, week=state.week) => r.weeks[week-1];
  const meanAt = (rows, key, week) => avg(rows.map(r => at(r,week)[key]));
  const scenario = () => meta.scenarios.find(s => s.id === state.scenario);
  const validViews = ["observatoire","duels","methode"];
  const currentView = () => validViews.includes(location.hash.slice(1)) ? location.hash.slice(1) : "observatoire";
  function button(parent, title, action) {
    const el = make("button",parent,title);
    el.type = "button";
    el.addEventListener("click",action);
    return el;
  }
  function selectedWeek(value) {
    state.week = Math.max(1, Math.min(meta.weeks, Number(value)));
    render();
  }
  function comparisons(rows) {
    const baseline = new Map(rows.filter(r => r.policy === "temoin").map(r => [r.seed+"-"+r.side, r]));
    return meta.policies.map(p => {
      const selected = rows.filter(r => r.policy === p.id);
      const diff = (r, key) => at(r)[key] - at(baseline.get(r.seed+"-"+r.side))[key];
      const wins = meta.seeds.filter(seed => avg(selected.filter(r=>r.seed===seed).map(r=>diff(r,"profit"))) > 0).length;
      return {id:p.id, profit:meanAt(selected,"profit",state.week), delta:avg(selected.map(r=>diff(r,"profit"))), wins};
    }).sort((a,b)=>b.delta-a.delta);
  }
  function stat(parent, title, value, hint, icon) {
    const box = make("div",parent,undefined,"stat");
    make("span",box,icon,"glyph").setAttribute("aria-hidden","true");
    make("h3",box,title); make("strong",box,value); make("p",box,hint);
  }
  function seriesChart(id, series, key, events) {
    const holder = reset(id);
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS,"svg");
    const width = Math.max(300, Math.round(holder.clientWidth || 620)), height = width < 460 ? 225 : 250;
    const left = width < 460 ? 54 : 66, right=17, top=23, bottom=36, plotW=width-left-right;
    svg.setAttribute("viewBox","0 0 "+width+" "+height);
    svg.setAttribute("role","img");
    svg.setAttribute("aria-label",metrics[key]+", semaine "+state.week+". "+series.map(s => s.label+" "+fmt(s.values[state.week-1],key)).join(", "));
    const node = (tag, attrs, text) => {
      const n = document.createElementNS(NS,tag);
      for (const [k,v] of Object.entries(attrs)) n.setAttribute(k,String(v));
      if (text !== undefined) n.textContent=text;
      svg.append(n); return n;
    };
    const values = series.flatMap(s=>s.values);
    const low = Math.min(0,...values), high = Math.max(0,...values);
    const rough = Math.max(1,(high-low)/4);
    const unit = 10 ** Math.floor(Math.log10(rough));
    const tick = [1,2,2.5,5,10].find(t=>t*unit>=rough) * unit;
    const min = Math.floor(low/tick)*tick, max = Math.max(min+tick, Math.ceil(high/tick)*tick);
    const x = i => left+i*plotW/Math.max(1,meta.weeks-1);
    const y = v => height-bottom-(v-min)/(max-min)*(height-bottom-top);
    for(let val=min; val<=max+tick/100; val+=tick){
      node("line",{x1:left,x2:width-right,y1:y(val),y2:y(val),stroke:val===0?"#aaafa1":"#e3e6de","stroke-dasharray":val===0?"0":"3 4"});
      node("text",{x:left-9,y:y(val)+4,"text-anchor":"end",class:"axis"},fmt(val,key));
    }
    for(let i=0;i<meta.weeks;i++){
      if(i===0 || i===meta.weeks-1 || (i+1)%Math.max(2,Math.ceil(meta.weeks/(width/75)))===0)
        node("text",{x:x(i),y:height-10,"text-anchor":"middle",class:"axis"},"S"+(i+1));
    }
    for(const event of events.filter(e=>e.week<=meta.weeks)){
      node("line",{x1:x(event.week-1),x2:x(event.week-1),y1:top,y2:height-bottom,stroke:"#aa876b","stroke-dasharray":"3 3"});
      const label=node("text",{x:x(event.week-1),y:top-6,"text-anchor":"middle",class:"axis"},"◆");
      const title=document.createElementNS(NS,"title");title.textContent=event.label;label.append(title);
    }
    for(const s of [...series].sort((a,b)=>Number(a.focus)-Number(b.focus))){
      const points=s.values.map((v,i)=>x(i)+","+y(v)).join(" ");
      node("polyline",{points,fill:"none",stroke:s.color,"stroke-width":s.focus?3.5:1.9,opacity:s.focus?1:.72,"stroke-linejoin":"round","stroke-linecap":"round","stroke-dasharray":s.id==="temoin"?"6 4":"0"});
    }
    node("line",{x1:x(state.week-1),x2:x(state.week-1),y1:top,y2:height-bottom,stroke:"#888a84","stroke-dasharray":"2 4"});
    for(const s of series.filter(s=>s.focus)) node("circle",{cx:x(state.week-1),cy:y(s.values[state.week-1]),r:5,fill:s.color,stroke:"white","stroke-width":2});
    svg.addEventListener("click", e=>{
      const box=svg.getBoundingClientRect();
      selectedWeek(Math.round(((e.clientX-box.left)*width/box.width-left)/plotW*(meta.weeks-1))+1);
    });
    holder.append(svg);
  }
  function overview() {
    const rows=filtered(), ranking=comparisons(rows), winner=ranking.find(r=>r.id!=="temoin");
    const focus=ranking.find(r=>r.id===state.focus);
    const cards=reset("metrics");
    stat(cards,"Meilleur écart testé",signed(winner.delta,"profit"),labels[winner.id]+" face au témoin","↗");
    stat(cards,"Profit du magasin",fmt(focus.profit,"profit"),labels[focus.id]+" · moyenne cumulée","€");
    stat(cards,"Populations gagnantes",focus.wins+" / "+meta.seeds.length,labels[focus.id]+" devant le témoin","≋");
    const series=meta.policies.map(p=>{
      const selected=rows.filter(r=>r.policy===p.id);
      return {id:p.id,label:p.label,color:colors[p.id],focus:p.id===state.focus,
        values:Array.from({length:meta.weeks},(_,i)=>meanAt(selected,state.metric,i+1))};
    });
    const active=series.find(s=>s.focus);
    get("chart-note").textContent=active.label+" · semaine "+state.week+" · "+fmt(active.values[state.week-1],state.metric)+". Moyenne des deux emplacements et des "+meta.seeds.length+" populations.";
    seriesChart("plot",series,state.metric,scenario().events);
    const legend=reset("legend");
    for(const s of series){
      const b=button(legend,s.label,()=>{state.focus=s.id;render();});
      const dot=document.createElement("i");dot.style.setProperty("--c",s.color);b.prepend(dot);b.setAttribute("aria-pressed",s.focus);
    }
    const table=make("table",reset("chart-table"));
    const head=table.createTHead().insertRow();
    ["Semaine",...series.map(s=>s.label)].forEach(v=>make("th",head,v));
    const body=table.createTBody();
    for(let i=0;i<meta.weeks;i++){
      const row=body.insertRow();make("th",row,"S"+(i+1));
      series.forEach(s=>make("td",row,fmt(s.values[i],state.metric)));
    }
    get("rank-week").textContent="S"+String(state.week).padStart(2,"0");
    const rank=reset("rank"), scale=Math.max(1,...ranking.map(r=>Math.abs(r.delta)));
    for(const r of ranking){
      const b=button(rank,"",()=>{state.focus=r.id;render();});
      b.className="rank-row";b.style.setProperty("--c",colors[r.id]);b.setAttribute("aria-pressed",r.id===state.focus);
      const name=make("span",b);make("i",name,undefined,"rank-dot");name.append(labels[r.id]);make("b",b,signed(r.delta,"profit"));
      const bar=make("span",b,undefined,"rank-bar");bar.setAttribute("aria-hidden","true");make("i",bar).style.width=(Math.abs(r.delta)/scale*100)+"%";
    }
    get("rank-foot").textContent="La longueur indique l’ampleur de l’écart. Le signe indique un gain ou une perte.";
    const policyRows=rows.filter(r=>r.policy===state.focus), reference=rows.filter(r=>r.policy==="temoin");
    const through=(group,key)=>avg(group.map(r=>sum(r.weeks.slice(0,state.week).map(w=>w[key]))));
    const factors=reset("factors");
    for(const [key,label] of [["grossMargin","Marge brute"],["labelCost","Frais d’étiquetage"],["waste","Coût des périmés"],["visits","Visites"]]){
      const line=make("div",factors);
      make("span",line,label);make("strong",line,signed(through(policyRows,key)-through(reference,key),key));
    }
    get("factor-heading").textContent=labels[state.focus]+" face au témoin";
    get("factor-period").textContent="Écarts moyens par magasin, cumulés jusqu’en semaine "+state.week+". Une hausse des frais ou des pertes réduit le profit.";
    const cohort=reset("cohorts");
    const samples=rows.filter(r=>r.policy==="relance").flatMap(r=>r.weeks.slice(0,state.week));
    for(const [title,n,d] of [["Affectés à l’offre","returnedTreated","treated"],["Groupe témoin","returnedControl","control"]]){
      const total=sum(samples.map(r=>r[d])), visited=sum(samples.map(r=>r[n]));
      const row=make("div",cohort,undefined,"cohort-row");make("span",row,title);
      make("b",row,total?number.format(100*visited/total)+" % · "+visited+"/"+total:"Aucun membre éligible");
      const meter=make("meter",row);meter.min=0;meter.max=1;meter.value=total?visited/total:0;meter.setAttribute("aria-label",title);
    }
  }
  function duelView() {
    const rows=duels.filter(r=>r.scenario===state.scenario && r.seed===state.seed).sort((a,b)=>a.side.localeCompare(b.side));
    const scores=reset("duel-scores"), wrap=reset("duel-grid"), table=make("table",wrap);
    const head=table.createTHead().insertRow();make("th",head,"Magasin");
    for(let w=1;w<=meta.weeks;w++) make("th",head,"S"+w);
    const body=table.createTBody();
    rows.forEach((r,j)=>{
      const w=at(r), title=r.side==="A"?"Le Panier Malin":"Chez Marcel", box=make("div",scores);
      box.style.setProperty("--c",j?"#267481":"#7951c6");
      make("span",box,title);make("strong",box,fmt(w.profit,"profit"));make("small",box,"Semaine "+state.week+" · "+(labels[w.arm]||"Fermé"));
      const tr=body.insertRow();make("th",tr,title);
      for(const o of r.weeks){
        const cell=tr.insertCell(), b=button(cell,"",()=>selectedWeek(o.week));
        b.setAttribute("aria-pressed",o.week===state.week);
        b.title=title+" · S"+o.week+" · "+(labels[o.arm]||"Fermé");b.setAttribute("aria-label",b.title);
        make("i",b,undefined,"swatch").style.setProperty("--c",colors[o.arm]||"#999");
      }
    });
    const key=make("div",wrap,undefined,"legend");
    meta.policies.filter(p=>p.id!=="adaptatif").forEach(p=>{const item=make("span",key);make("i",item,undefined,"swatch").style.setProperty("--c",colors[p.id]);item.append(p.label);});
    seriesChart("duel-plot",rows.map((r,j)=>({id:r.side,label:r.side,color:j?"#267481":"#7951c6",focus:true,values:r.weeks.map(w=>w.profit)})),"profit",scenario().events);
    const exploratory = meta.policies.length-1;
    const choices=duels.filter(r=>r.scenario===state.scenario).flatMap(r=>r.weeks.slice(exploratory,state.week)).filter(w=>w.arm);
    get("duel-finding").textContent=choices.length
      ? "Après les "+exploratory+" semaines d’essai, les gérants de cette conjoncture ont repris la tarification dynamique "+choices.filter(w=>w.arm==="tarification").length+" fois sur "+choices.length+" décisions, toutes populations confondues, jusqu’à la semaine "+state.week+"."
      : "Durant les cinq semaines d’essai, chaque gérant teste les cinq tactiques dans un ordre tiré au sort.";
  }
  function protocol() {
    const container=reset("protocol");
    make("p",container,meta.simulations+" parties de "+meta.weeks+" semaines, "+meta.households+" foyers au départ et "+meta.seeds.length+" populations (graines "+meta.seeds.join(", ")+"). Les clients utilisent des formules comportementales. Les tactiques sont programmées et le gérant adaptatif apprend quand les employer.");
    make("p",container,meta.benchmarkSimulations+" parties comparent chaque politique à un adversaire témoin. Le profit inclut le coût des marchandises, les pertes et les frais. Chaque écart est calculé pour la même population, au même emplacement et à la même semaine. Les deux emplacements sont moyennés avant de compter les populations gagnantes.");
    make("p",container,meta.duelSimulations+" parties opposent deux gérants adaptatifs. Leurs choix peuvent avoir des effets différés sur les stocks et les habitudes. Les résultats décrivent ces populations simulées et ces paramètres.");
    make("p",container,"Dans le scénario de choc : "+meta.scenarios.flatMap(s=>s.events).map(e=>"S"+e.week+" — "+e.label).join(" · ")+".");
  }
  function render() {
    const view=currentView();
    validViews.forEach(v=>get(v).hidden=v!==view);
    document.querySelector(".controls").hidden=view==="methode";
    document.querySelectorAll("nav a").forEach(a=>{if(a.hash==="#"+view)a.setAttribute("aria-current","page");else a.removeAttribute("aria-current");});
    get("week").value=state.week;get("week-output").textContent="S"+String(state.week).padStart(2,"0")+" / "+meta.weeks;
    Array.from(get("scenarios").children).forEach(b=>b.setAttribute("aria-pressed",b.dataset.id===state.scenario));
    if(view==="observatoire") overview(); else if(view==="duels") duelView(); else protocol();
  }
  get("export").addEventListener("click",()=>{
    const view=currentView();
    const url=URL.createObjectURL(new Blob([exportCsv(data,view,state)],{type:"text/csv;charset=utf-8"}));
    const a=document.createElement("a");a.href=url;a.download="simarche-"+view+".csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  for(const s of meta.scenarios){const b=button(get("scenarios"),s.id==="choc"?"Choc fournisseur":s.label,()=>{state.scenario=s.id;render();});b.dataset.id=s.id;}
  for(const [id,name] of Object.entries(metrics))get("metric").add(new Option(name,id));
  meta.seeds.forEach(seed=>get("seed").add(new Option("Population "+seed,String(seed))));
  get("seed").addEventListener("change",e=>{state.seed=Number(e.target.value);render();});
  get("metric").addEventListener("change",e=>{state.metric=e.target.value;render();});
  get("week").max=meta.weeks;get("week").addEventListener("input",e=>selectedWeek(e.target.value));
  window.addEventListener("hashchange",render);
  document.querySelectorAll("header a[href^='#']").forEach(link=>link.addEventListener("click",e=>{
    if(e.metaKey||e.ctrlKey||e.shiftKey||e.altKey) return;
    e.preventDefault();
    if(location.hash!==link.hash) {
      try { history.pushState(null,"",link.hash); }
      catch { location.hash=link.hash; }
    }
    render(); window.scrollTo(0,0);
  }));
  let timer;window.addEventListener("resize",()=>{clearTimeout(timer);timer=setTimeout(render,120);});
  get("app").hidden=false;render();
}
if(typeof window!=="undefined"){
  if(window.SIMARCHE?.runs?.length) init(window.SIMARCHE);
  else {document.getElementById("error").hidden=false;document.getElementById("export").disabled=true;}
}
