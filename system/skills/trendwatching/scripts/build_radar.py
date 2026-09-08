#!/usr/bin/env python3
"""Собирает самодостаточный HTML-радар трендов из trends.json.

    python3 build_radar.py trends.json -o radar.html
    python3 build_radar.py trends.json -o radar.html --lang en

Кольца — требуемое решение (от центра: масштабировать → опцион → проверить →
мониторить), секторы — домены STEEP+, размер точки — влияние, цвет —
уверенность. Фильтры и карточка по клику. Без внешних зависимостей.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trendlib as T  # noqa: E402

UI = {
    "kicker":      ("Тренд-радар · волна", "Trend radar · wave"),
    "n_trends":    ("Трендов", "Trends"),
    "n_signals":   ("Сигналов", "Signals"),
    "horizon":     ("Горизонт", "Horizon"),
    "date":        ("Дата", "Date"),
    "f_horizon":   ("Горизонт", "Horizon"),
    "f_domain":    ("Домен", "Domain"),
    "f_conf":      ("Уверенность", "Confidence"),
    "f_score":     ("Доказательность", "Evidence"),
    "all":         ("все", "all"),
    "any":         ("любая", "any"),
    "leg_high":    ("уверенность высокая", "confidence high"),
    "leg_med":     ("средняя", "medium"),
    "leg_low":     ("низкая", "low"),
    "leg_size":    ("размер точки — влияние на бизнес (1–5)", "dot size — business impact (1–5)"),
    "leg_rings":   ("кольца — требуемое решение", "rings — required decision"),
    "footer":      ("Радар — индекс, а не аналитика: он кодирует четыре измерения и всегда читается вместе "
                    "с карточками. Тренды со статусом «игнорировать» на радар не выносятся, но остаются в отчёте.",
                    "The radar is an index, not the analysis: it encodes four dimensions and is always read "
                    "together with the cards. Trends marked ignore are kept in the report but left off the radar."),
    "k_decision":  ("Решение", "Decision"),
    "k_stage":     ("Стадия", "Stage"),
    "k_score":     ("Score / coverage", "Score / coverage"),
    "k_conf":      ("Уверенность", "Confidence"),
    "k_status":    ("Статус", "Status"),
    "k_horizon":   ("Горизонт", "Horizon"),
    "k_pi":        ("Вероятность × влияние", "Probability × impact"),
    "k_indep":     ("Независимых сигналов", "Independent signals"),
    "k_owner":     ("Владелец · пересмотр", "Owner · next review"),
    "s_drivers":   ("Драйверы", "Drivers"),
    "s_barriers":  ("Барьеры", "Barriers"),
    "s_counter":   ("Контртренд", "Counter-trend"),
    "s_disconf":   ("Опровергающие свидетельства", "Disconfirming evidence"),
    "s_cost":      ("Цена принятия", "Cost of adoption"),
    "s_ind":       ("Индикаторы", "Indicators"),
    "s_delta":     ("Изменение с прошлой волны", "Change since previous wave"),
    "s_gaps":      ("Пробелы в оценке (N/D)", "Assessment gaps (N/D)"),
    "s_signals":   ("Сигналы", "Signals"),
    "leading":     ("ведущий", "leading"),
    "lagging":     ("отстающий", "lagging"),
    "threshold":   ("порог", "threshold"),
    "source":      ("источник", "source"),
    "cls":         ("класс", "class"),
    "no_disconf":  ("Не найдены. Это почти всегда признак недостаточного поиска, а не их отсутствия.",
                    "None found. Almost always a sign of insufficient search rather than their absence."),
    "no_ind":      ("Индикаторы не заданы — тренд нельзя ни подтвердить, ни опровергнуть.",
                    "No indicators set — the trend can be neither confirmed nor refuted."),
    "no_signals":  ("Сигналы не привязаны.", "No signals linked."),
    "empty":       ("В trends.json нет трендов для радара.", "No trends to display on the radar."),
}


def prepare(data, lang, weight_table):
    index = T.signals_index(data)
    sectors = list(T.STEEP.keys())
    items = []
    for tr in data["trends"]:
        c = T.compute(tr, weight_table)
        conf = T.confidence_hint(tr, c, index)
        status, action = T.status_for(c["score"], lang)
        decision = tr.get("decision") or "monitor"
        if decision == "ignore":
            continue  # игнорируемые на радар не выносятся, но остаются в отчёте
        steep = tr.get("steep") or ["B"]
        signals = []
        for sid in tr.get("signal_ids", []):
            s = index.get(sid)
            if s:
                signals.append({"id": sid, "date": s.get("date_event", ""),
                                "fact": s.get("headline_fact", ""), "source": s.get("source", ""),
                                "url": s.get("url"), "reliability": s.get("reliability", ""),
                                "tier": T.tier(s)})
        items.append({
            "id": tr["id"], "name": tr["name"], "statement": tr.get("statement", ""),
            "sector": steep[0] if steep[0] in sectors else "B", "sectors": steep,
            "ring": decision, "decision": T.L(T.DECISIONS.get(decision, ("—", "—")), lang),
            "score": c["score"], "coverage": c["coverage"],
            "confidence": conf, "confidence_ru": T.L(T.CONFIDENCE[conf], lang),
            "status": status, "action": action,
            "stage": T.L(T.STAGES[tr.get("stage", 0)], lang),
            "horizon": tr.get("horizon") or "—",
            "impact": tr.get("impact") or 3, "probability": tr.get("probability"),
            "impact_object": tr.get("impact_object") or "", "urgency": tr.get("urgency"),
            "counter_trend": tr.get("counter_trend", ""), "disconfirming": tr.get("disconfirming", ""),
            "adoption_cost": tr.get("adoption_cost", ""),
            "drivers": tr.get("drivers", []), "barriers": tr.get("barriers", []),
            "indicators": tr.get("indicators", []),
            "owner": tr.get("owner") or "—", "next_review": tr.get("next_review") or "—",
            "independent": T.independent_count(tr, index),
            "gaps": [T.L(g, lang) for g in c["gaps"]],
            "delta": tr.get("delta") or "", "signals": signals,
        })
    return items


HTML = """<!doctype html>
<html lang="__LANG__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{
  --paper:#EAEEE9; --surface:#F7F9F5; --surface-2:#E1E7DE;
  --ink:#141A17; --ink-soft:#3A453F; --muted:#66716A;
  --line:#C8D1C6; --line-soft:#D9E0D6;
  --accent:#1E3FCB; --brass:#9A7413;
  --c-high:#1E6F4E; --c-med:#9A7413; --c-low:#A8401A;
  --grid:rgba(20,26,23,.10);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --paper:#0E1310; --surface:#161D19; --surface-2:#1E2621;
    --ink:#E6EBE3; --ink-soft:#C0C9BE; --muted:#8B968C;
    --line:#2C362F; --line-soft:#232C26;
    --accent:#8AA0FF; --brass:#DEB851;
    --c-high:#5FBF92; --c-med:#DEB851; --c-low:#E08055;
    --grid:rgba(230,235,227,.12);
  }
}
:root[data-theme="dark"]{
  --paper:#0E1310; --surface:#161D19; --surface-2:#1E2621;
  --ink:#E6EBE3; --ink-soft:#C0C9BE; --muted:#8B968C;
  --line:#2C362F; --line-soft:#232C26;
  --accent:#8AA0FF; --brass:#DEB851;
  --c-high:#5FBF92; --c-med:#DEB851; --c-low:#E08055;
  --grid:rgba(230,235,227,.12);
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:"IBM Plex Sans",system-ui,-apple-system,sans-serif;font-size:15px;line-height:1.55;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1320px;margin:0 auto;padding:36px 24px 80px}
header{border-bottom:1px solid var(--line);padding-bottom:22px;margin-bottom:26px}
.eyebrow{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--muted)}
h1{font-size:clamp(24px,3.4vw,34px);margin:10px 0 6px;font-weight:600;letter-spacing:-.01em;text-wrap:balance}
.sub{color:var(--muted);font-size:14px;max-width:70ch;margin:0}
.metaline{margin-top:14px;display:flex;flex-wrap:wrap;gap:8px 24px;
  font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--muted)}
.metaline b{color:var(--ink);font-weight:500}
.controls{display:flex;flex-wrap:wrap;gap:18px;margin-bottom:22px;align-items:flex-end}
.ctl{display:flex;flex-direction:column;gap:5px}
.ctl label{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted)}
select{background:var(--surface);color:var(--ink);border:1px solid var(--line);
  border-radius:3px;padding:6px 9px;font:inherit;font-size:13px;min-width:170px}
select:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.layout{display:grid;gap:26px;grid-template-columns:1fr}
@media(min-width:1000px){.layout{grid-template-columns:minmax(0,1fr) 380px}}
.radarbox{background:var(--surface);border:1px solid var(--line);padding:14px;overflow-x:auto}
svg{display:block;width:100%;height:auto;max-width:760px;margin:0 auto}
.blip{cursor:pointer;transition:opacity .15s}
.blip:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.blip.dim{opacity:.14;pointer-events:none}
.blip circle{stroke:var(--paper);stroke-width:1.5}
.bliptext{pointer-events:none;transition:opacity .15s}
.bliptext.dim{opacity:.14}
.bliptext text{font-family:"IBM Plex Mono",monospace;font-size:9px;fill:var(--ink);
  paint-order:stroke;stroke:var(--surface);stroke-width:3.5px;stroke-linejoin:round}
.ringlabel{font-family:"IBM Plex Mono",monospace;font-size:9.5px;fill:var(--muted);letter-spacing:.08em;
  paint-order:stroke;stroke:var(--surface);stroke-width:4px;stroke-linejoin:round}
.seclabel{font-family:"IBM Plex Mono",monospace;font-size:10px;fill:var(--muted);letter-spacing:.06em}
.legend{display:flex;flex-wrap:wrap;gap:10px 22px;margin-top:14px;padding-top:14px;
  border-top:1px solid var(--line-soft);font-size:12px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.panel{background:var(--surface);border:1px solid var(--line);padding:20px;
  position:sticky;top:24px;align-self:start;max-height:calc(100vh - 48px);overflow-y:auto}
.panel h2{margin:0 0 4px;font-size:19px;font-weight:600;line-height:1.25;text-wrap:balance}
.panel .stmt{color:var(--ink-soft);font-size:14px;margin:0 0 14px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;font-size:13px;margin-bottom:14px}
.kv dt{color:var(--muted);font-family:"IBM Plex Mono",monospace;font-size:10.5px;
  letter-spacing:.06em;text-transform:uppercase;padding-top:3px}
.kv dd{margin:0}
.sect{margin-top:14px}
.sect h3{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--muted);margin:0 0 5px;font-weight:500}
.sect p{margin:0 0 6px;font-size:13.5px}
.sect ul{margin:0;padding-left:17px;font-size:13.5px}
.sect li{margin-bottom:3px}
.sig{border-top:1px solid var(--line-soft);padding:8px 0;font-size:12.5px}
.sig .h{display:flex;gap:8px;align-items:baseline}
.sig .id{font-family:"IBM Plex Mono",monospace;font-size:10.5px;color:var(--brass)}
.sig .src{color:var(--muted);font-size:11.5px}
a{color:var(--accent);text-underline-offset:2px}
.empty{color:var(--muted);font-size:13.5px}
.pill{display:inline-block;font-family:"IBM Plex Mono",monospace;font-size:10.5px;
  padding:2px 8px;border-radius:100px;background:var(--surface-2);color:var(--ink-soft)}
.warn{color:var(--c-low);font-size:12.5px;margin-top:8px}
footer{margin-top:34px;border-top:1px solid var(--line);padding-top:16px;
  font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--muted)}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow" id="kicker"></div>
  <h1>__TITLE__</h1>
  <p class="sub">__SUBTITLE__</p>
  <div class="metaline" id="metaline"></div>
</header>

<div class="controls">
  <div class="ctl"><label for="f-horizon" id="l-horizon"></label>
    <select id="f-horizon"></select></div>
  <div class="ctl"><label for="f-sector" id="l-sector"></label>
    <select id="f-sector"></select></div>
  <div class="ctl"><label for="f-conf" id="l-conf"></label>
    <select id="f-conf"></select></div>
  <div class="ctl"><label for="f-score" id="l-score"></label>
    <select id="f-score"></select></div>
</div>

<div class="layout">
  <div class="radarbox"><div id="radar"></div>
    <div class="legend" id="legend"></div>
  </div>
  <aside class="panel" id="panel"></aside>
</div>

<footer id="footer"></footer>
</div>

<script>
const DATA = __DATA__;
const SECTORS = __SECTORS__;
const RINGS = __RINGS__;
const UI = __UI__;
const META = __META__;
const CONF_COLOR = {high:"var(--c-high)", medium:"var(--c-med)", low:"var(--c-low)"};

const SIZE = 760, CX = SIZE/2, CY = SIZE/2, R_MAX = 300, R_MIN = 62;
const ringCount = RINGS.length;
const bandW = (R_MAX - R_MIN) / ringCount;

function esc(s){return String(s).replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function short(s){return s.length>26 ? s.slice(0,25)+"…" : s}
function hash(str){let h=0;for(let i=0;i<str.length;i++){h=(h*31+str.charCodeAt(i))|0}return Math.abs(h)}

function chrome(){
  document.getElementById("kicker").textContent = `${UI.kicker} ${META.wave}`;
  document.getElementById("metaline").innerHTML =
    `<span>${UI.n_trends}: <b>${DATA.length}</b></span>` +
    `<span>${UI.n_signals}: <b>${META.signals}</b></span>` +
    `<span>${UI.horizon}: <b>${esc(META.horizon)}</b></span>` +
    `<span>${UI.date}: <b>${esc(META.date)}</b></span>`;
  document.getElementById("l-horizon").textContent = UI.f_horizon;
  document.getElementById("l-sector").textContent = UI.f_domain;
  document.getElementById("l-conf").textContent = UI.f_conf;
  document.getElementById("l-score").textContent = UI.f_score;
  document.getElementById("legend").innerHTML =
    `<span><i class="dot" style="background:var(--c-high)"></i>${UI.leg_high}</span>` +
    `<span><i class="dot" style="background:var(--c-med)"></i>${UI.leg_med}</span>` +
    `<span><i class="dot" style="background:var(--c-low)"></i>${UI.leg_low}</span>` +
    `<span>${UI.leg_size}</span><span>${UI.leg_rings}</span>`;
  document.getElementById("footer").textContent = UI.footer;

  const hSel = document.getElementById("f-horizon");
  hSel.innerHTML = `<option value="">${UI.all}</option>`;
  [...new Set(DATA.map(t=>t.horizon))].sort().forEach(v=>{
    const o=document.createElement("option");o.value=v;o.textContent=v;hSel.appendChild(o)});
  const sSel = document.getElementById("f-sector");
  sSel.innerHTML = `<option value="">${UI.all}</option>`;
  SECTORS.forEach(s=>{const o=document.createElement("option");o.value=s.key;o.textContent=s.label;
    sSel.appendChild(o)});
  document.getElementById("f-conf").innerHTML =
    `<option value="">${UI.any}</option><option value="high">${UI.leg_high.split(" ").pop()}</option>` +
    `<option value="medium">${UI.leg_med}</option><option value="low">${UI.leg_low}</option>`;
  document.getElementById("f-score").innerHTML =
    `<option value="0">${UI.any}</option><option value="40">40+</option><option value="55">55+</option>` +
    `<option value="70">70+</option><option value="85">85+</option>`;
}

function layout(){
  const bySlot = {};
  return DATA.map(t=>{
    const si = Math.max(0, SECTORS.findIndex(s=>s.key===t.sector));
    const ri = Math.max(0, RINGS.findIndex(r=>r.key===t.ring));
    const slot = si+"-"+ri;
    bySlot[slot] = (bySlot[slot]||0)+1;
    const n = bySlot[slot];
    const span = 2*Math.PI/SECTORS.length;
    const base = si*span - Math.PI/2;
    const jitterA = ((hash(t.id)%1000)/1000)*0.62 + 0.19;
    const drift = ((n-1)%5)*0.055 - 0.11;
    const angle = base + span*Math.min(0.93, Math.max(0.07, jitterA + drift));
    const jitterR = ((hash(t.id+"r")%1000)/1000)*0.56 + 0.22;
    // ri=0 (масштабировать) — ближе к центру: чем срочнее решение, тем ближе точка
    const radius = R_MIN + bandW*ri + bandW*jitterR;
    return Object.assign({}, t, {x: CX + radius*Math.cos(angle), y: CY + radius*Math.sin(angle)});
  });
}

function draw(items){
  const svg = [];
  svg.push(`<svg viewBox="0 0 ${SIZE} ${SIZE}" role="img" aria-label="${UI.kicker}">`);
  for(let i=0;i<=ringCount;i++){
    const r = R_MIN + bandW*i;
    svg.push(`<circle cx="${CX}" cy="${CY}" r="${r}" fill="none" stroke="var(--grid)" stroke-width="1"/>`);
  }
  for(let i=0;i<SECTORS.length;i++){
    const a = i*2*Math.PI/SECTORS.length - Math.PI/2;
    svg.push(`<line x1="${CX+R_MIN*Math.cos(a)}" y1="${CY+R_MIN*Math.sin(a)}" x2="${CX+R_MAX*Math.cos(a)}" y2="${CY+R_MAX*Math.sin(a)}" stroke="var(--grid)" stroke-width="1"/>`);
    const am = a + Math.PI/SECTORS.length;
    const lr = R_MAX + 26;
    svg.push(`<text class="seclabel" x="${CX+lr*Math.cos(am)}" y="${CY+lr*Math.sin(am)}" text-anchor="middle" dominant-baseline="middle">${esc(SECTORS[i].label)}</text>`);
  }
  for(let i=0;i<ringCount;i++){
    const r = R_MIN + bandW*i + bandW/2;
    svg.push(`<text class="ringlabel" x="${CX}" y="${CY-r}" text-anchor="middle" dominant-baseline="middle">${esc(RINGS[i].label)}</text>`);
  }
  const placed = items.map(t=>({t, r: 5 + (t.impact||3)*2.2, ly: t.y + 3})).sort((a,b)=> a.ly - b.ly);
  for(let i=1;i<placed.length;i++){
    const prev = placed[i-1], cur = placed[i];
    if(Math.abs(cur.ly - prev.ly) < 13 && Math.abs(cur.t.x - prev.t.x) < 200){ cur.ly = prev.ly + 13; }
  }
  // сначала все круги, затем все подписи — иначе соседний круг закрывает начало чужого текста
  placed.forEach(({t, r})=>{
    svg.push(`<g class="blip" data-id="${t.id}" tabindex="0" role="button" aria-label="${esc(t.name)}">`);
    svg.push(`<circle cx="${t.x.toFixed(1)}" cy="${t.y.toFixed(1)}" r="${r}" fill="${CONF_COLOR[t.confidence]||'var(--c-med)'}"/>`);
    svg.push(`</g>`);
  });
  // подпись уходит вправо, но если справа стоит чужой круг — отзеркаливаем влево
  const CH = 5.1; // средняя ширина символа моноширинного 9px
  placed.forEach(p=>{
    const label = short(p.t.name), w = label.length*CH;
    const hits = (dir)=> placed.some(o=>{
      if(o.t.id===p.t.id) return false;
      const x0 = dir>0 ? p.t.x+p.r+7 : p.t.x-p.r-7-w;
      const x1 = x0 + w;
      return o.t.x + o.r > x0 && o.t.x - o.r < x1 && Math.abs(o.t.y - p.ly) < 9;
    });
    p.side = (hits(1) && !hits(-1)) ? -1 : 1;
    p.w = w;
  });
  placed.forEach(({t, r, ly, side, w})=>{
    svg.push(`<g class="bliptext" data-id="${t.id}">`);
    const tx = side>0 ? t.x+r+7 : t.x-r-7;
    if(Math.abs(ly - (t.y+3)) > 2){
      svg.push(`<line x1="${(t.x+side*r).toFixed(1)}" y1="${t.y.toFixed(1)}" x2="${(t.x+side*(r+5)).toFixed(1)}" y2="${(ly-3).toFixed(1)}" stroke="var(--grid)" stroke-width="1"/>`);
    }
    svg.push(`<text x="${tx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="${side>0?'start':'end'}">${esc(short(t.name))}</text>`);
    svg.push(`</g>`);
  });
  svg.push(`</svg>`);
  document.getElementById("radar").innerHTML = svg.join("");
  document.querySelectorAll(".blip").forEach(el=>{
    el.addEventListener("click", ()=>select(el.dataset.id));
    el.addEventListener("keydown", e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();select(el.dataset.id)}});
  });
  applyFilters();
}

function sect(title, body){return `<div class="sect"><h3>${title}</h3>${body}</div>`}
function list(arr){return `<ul>${arr.map(x=>`<li>${esc(x)}</li>`).join("")}</ul>`}

function select(id){
  const t = DATA.find(x=>x.id===id);
  if(!t) return;
  const rows = [
    [UI.k_decision, esc(t.decision)],
    [UI.k_stage, esc(t.stage)],
    [UI.k_score, `${t.score} · ${t.coverage}%`],
    [UI.k_conf, esc(t.confidence_ru)],
    [UI.k_status, esc(t.status)],
    [UI.k_horizon, esc(t.horizon)],
    [UI.k_pi, (t.probability&&t.impact) ? `${t.probability} × ${t.impact}` + (t.impact_object?` (${esc(t.impact_object)})`:"") : "—"],
    [UI.k_indep, t.independent],
    [UI.k_owner, `${esc(t.owner)} · ${esc(t.next_review)}`]
  ];
  let html = `<h2>${esc(t.name)}</h2><p class="stmt">${esc(t.statement)}</p><dl class="kv">`;
  rows.forEach(([k,v])=>{html += `<dt>${k}</dt><dd>${v}</dd>`});
  html += `</dl>`;
  if(t.drivers.length) html += sect(UI.s_drivers, list(t.drivers));
  if(t.barriers.length) html += sect(UI.s_barriers, list(t.barriers));
  if(t.counter_trend) html += sect(UI.s_counter, `<p>${esc(t.counter_trend)}</p>`);
  html += sect(UI.s_disconf, t.disconfirming ? `<p>${esc(t.disconfirming)}</p>`
    : `<p class="empty">${UI.no_disconf}</p>`);
  if(t.adoption_cost) html += sect(UI.s_cost, `<p>${esc(t.adoption_cost)}</p>`);
  if(t.indicators.length) html += sect(UI.s_ind,
    `<ul>${t.indicators.map(i=>`<li>${esc(i.name)} <span class="pill">${i.kind==="leading"?UI.leading:UI.lagging}</span>${i.threshold?` — ${UI.threshold}: ${esc(i.threshold)}`:""}</li>`).join("")}</ul>`);
  else html += `<p class="warn">${UI.no_ind}</p>`;
  if(t.delta) html += sect(UI.s_delta, `<p>${esc(t.delta)}</p>`);
  if(t.gaps.length) html += sect(UI.s_gaps, `<p class="empty">${t.gaps.map(esc).join(", ")}</p>`);
  html += sect(UI.s_signals, t.signals.length
    ? t.signals.map(s=>`<div class="sig"><div class="h"><span class="id">${esc(s.id)}</span><span>${esc(s.fact)}</span></div>
        <div class="src">${esc(s.date)} · ${esc(s.source)} · ${UI.cls} ${esc(s.reliability)} · ${esc(s.tier)}${s.url?` · <a href="${esc(s.url)}" target="_blank" rel="noopener">${UI.source}</a>`:""}</div></div>`).join("")
    : `<p class="empty">${UI.no_signals}</p>`);
  const p = document.getElementById("panel");
  p.innerHTML = html;
  p.scrollTop = 0;
}

function applyFilters(){
  const h = document.getElementById("f-horizon").value;
  const s = document.getElementById("f-sector").value;
  const c = document.getElementById("f-conf").value;
  const m = parseFloat(document.getElementById("f-score").value);
  document.querySelectorAll(".blip, .bliptext").forEach(el=>{
    const t = DATA.find(x=>x.id===el.dataset.id);
    if(!t) return;
    const ok = (!h || t.horizon===h) && (!s || t.sectors.includes(s)) &&
               (!c || t.confidence===c) && (t.score>=m);
    el.classList.toggle("dim", !ok);
  });
}

chrome();
["f-horizon","f-sector","f-conf","f-score"].forEach(id=>
  document.getElementById(id).addEventListener("change", applyFilters));
draw(layout());
if(DATA.length) select(DATA.slice().sort((a,b)=>b.score-a.score)[0].id);
else document.getElementById("panel").innerHTML = `<p class="empty">${UI.empty}</p>`;
</script>
</body>
</html>
"""


def build(data, out_path, lang="ru"):
    _, weight_table = T.weights(data)
    items = prepare(data, lang, weight_table)
    meta = data["meta"]
    sectors = [{"key": k, "label": T.L(v, lang)} for k, v in T.STEEP.items()]
    rings = [{"key": k, "label": T.L(T.DECISIONS[k], lang)} for k in T.DECISION_RINGS]
    ui = {k: T.L(v, lang) for k, v in UI.items()}
    payload = {
        "wave": meta.get("wave", 1), "signals": len(data.get("signals", [])),
        "horizon": meta.get("horizon", "—"), "date": meta.get("date", "—"),
    }
    html = (HTML
            .replace("__DATA__", json.dumps(items, ensure_ascii=False))
            .replace("__SECTORS__", json.dumps(sectors, ensure_ascii=False))
            .replace("__RINGS__", json.dumps(rings, ensure_ascii=False))
            .replace("__UI__", json.dumps(ui, ensure_ascii=False))
            .replace("__META__", json.dumps(payload, ensure_ascii=False))
            .replace("__LANG__", lang)
            .replace("__TITLE__", meta.get("title", "Trend radar"))
            .replace("__SUBTITLE__", meta.get("object", "")))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="HTML-радар трендов из trends.json")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", default="radar.html")
    ap.add_argument("--lang", choices=["ru", "en"], default=None)
    args = ap.parse_args()
    data = T.load(args.input)
    path = build(data, args.output, T.lang_of(data, args.lang))
    print(f"Готово: {path}")


if __name__ == "__main__":
    main()
