#!/usr/bin/env python3
"""python3 check_brackets.py <папка_карусели>
Проверка посадки фирменных скобок (система «Розенблюм») в slides/NN.html.
Пара скобок = <img src=".../br-NN-L.png"> и <img src=".../br-NN-R.png">.
Цель (то, что в скобках):
  - инлайн-скобки (L и R соседи в одном родителе): элементы между ними;
  - абсолютные скобки (.bk): элемент с атрибутом data-bk-target, иначе ближайший блок,
    центрированный между скобками; не нашелся -> «цель не найдена, пометь data-bk-target».
Края цели: по пикселям (кадр с целью минус кадр без нее), т.е. по краям букв и рамкам картинок;
для справки печатается и замер DOM (Range.getBoundingClientRect для текста, рамка для блоков).
Выход по слайду: gapL, gapR, центр цели по вертикали, центр скобок, отступы скобок от краев.
Зазоры gapL/gapR по пикселям. Центр цели по вертикали для текста = (верх краски + базовая линия) / 2,
базовая линия = нижняя строка пикселей с заполненностью >= 25% от максимальной строки цели
(у многострочных это база последней строки); для img и блоков без текста (.cell) — рамка элемента.
Справочно печатаются центр по строкам DOM и по всей краске.
FAIL: |gapL-gapR| > 2, |центр цели - центр скобок| > 2, скобка ближе 24 px к краю слайда.
Слайды без скобок пропускаются. Код выхода 1, если есть FAIL."""
import sys, os, glob, io
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

W, H, TOL, EDGE = 1080, 1350, 2, 24

JS = r"""() => {
const box = b => ({l:b.left, r:b.right, t:b.top, b:b.bottom});
const rect = e => box(e.getBoundingClientRect());
const isText = e => e.tagName !== 'IMG' && e.textContent.trim() &&
  [...e.querySelectorAll('*')].every(c => ['BR','B','I','EM','STRONG','SPAN'].includes(c.tagName) && !c.querySelector('img'));
const ink = e => { const rg = document.createRange(); rg.selectNodeContents(e); return box(rg.getBoundingClientRect()); };
const meas = e => isText(e) ? ink(e) : rect(e);
// для вертикального центра: текстовая цель = есть текст, нет картинок, не .cell (у них рамка)
const hasText = e => e.tagName !== 'IMG' && !!e.textContent.trim() && !e.querySelector('img') && !e.classList.contains('cell') && !e.querySelector('.cell');
const union = a => ({l:Math.min(...a.map(x=>x.l)), r:Math.max(...a.map(x=>x.r)), t:Math.min(...a.map(x=>x.t)), b:Math.max(...a.map(x=>x.b))});
const imgs = [...document.querySelectorAll('img')];
const nn = (e, side) => { const m = (e.getAttribute('src')||'').match(new RegExp('br-(\\d+)-' + side + '\\.png')); return m ? m[1] : null; };
const Ls = imgs.filter(e => nn(e,'L')), Rs = imgs.filter(e => nn(e,'R'));
const out = [];
Ls.forEach((L, i) => {
  const R = Rs.find(r => nn(r,'R') === nn(L,'L') && !out.some(o => o._r === r)) || Rs[i];
  if (!R) { out.push({err:'нет правой скобки к br-' + nn(L,'L') + '-L'}); return; }
  const bl = rect(L), br = rect(R);
  let T = null, how = '', txt = false;
  if (L.parentElement === R.parentElement && getComputedStyle(L).position !== 'absolute') {
    const mid = []; let e = L.nextElementSibling;
    while (e && e !== R) { mid.push(e); e = e.nextElementSibling; }
    if (mid.length) { T = union(mid.map(meas)); how = 'инлайн'; txt = mid.every(hasText); mid.forEach(m => m.setAttribute('data-bk-measure', out.length)); }
  }
  if (!T) {
    const marked = document.querySelector('[data-bk-target]');
    if (marked) { T = meas(marked); how = 'data-bk-target'; txt = hasText(marked); marked.setAttribute('data-bk-measure', out.length); }
    else {
      const cx = (bl.r + br.l) / 2, cy = ((bl.t+bl.b)/2 + (br.t+br.b)/2) / 2;
      let best = null, bd = 1e9;
      for (const e of document.body.querySelectorAll('*')) {
        if (e === L || e === R || e.contains(L) || e.contains(R)) continue;
        const r = meas(e); if (r.r - r.l < 4 || r.b - r.t < 4) continue;
        if (r.l < bl.l || r.r > br.r) continue;
        const dx = Math.abs((r.l + r.r)/2 - cx), dy = Math.abs((r.t + r.b)/2 - cy);
        if (dx > 6 || dy > 40) continue;
        const d = dx + dy - (r.r - r.l) * (r.b - r.t) / 1e6;
        if (d < bd) { bd = d; best = e; }
      }
      if (best) { best.setAttribute('data-bk-measure', out.length); T = meas(best); txt = hasText(best); how = 'авто: ' + best.tagName.toLowerCase() + (best.className ? '.' + String(best.className).trim().replace(/\s+/g,'.') : ''); }
    }
  }
  const o = {_r:R, nn:nn(L,'L'), how, txt, L:bl, R:br};
  if (T) o.T = T;
  out.push(o);
});
return out.map(o => { delete o._r; return o; });
}"""


def shot(pg):
    return np.asarray(Image.open(io.BytesIO(pg.screenshot(clip={"x": 0, "y": 0, "width": W, "height": H}))).convert("RGB")).astype(int)


def pixel_ink(pg, i, base):
    """Края цели по пикселям: разница кадра с целью и без нее (visibility:hidden).
    Точнее Range при отрицательном letter-spacing и боковых выносах букв."""
    sel = f'[data-bk-measure="{i}"]'
    pg.evaluate("s => document.querySelectorAll(s).forEach(e => e.style.setProperty('visibility','hidden','important'))", sel)
    pg.wait_for_timeout(50)
    hid = shot(pg)
    pg.evaluate("s => document.querySelectorAll(s).forEach(e => e.style.removeProperty('visibility'))", sel)
    m = np.abs(base - hid).sum(axis=2) > 40
    ys, xs = np.where(m)
    if not len(xs): return None
    rows = m.sum(axis=1)
    # базовая линия: нижняя строка с заполненностью >= 25% от максимума (выносные р/у/д/запятая разрежены)
    base_y = int(np.where(rows >= 0.25 * rows.max())[0].max()) + 1
    return {"l": xs.min(), "r": xs.max() + 1, "t": ys.min(), "b": ys.max() + 1, "base": base_y}


def chrome_kw(p):
    if os.path.exists(p.chromium.executable_path): return {}
    c = sorted(glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-*/chrome-headless-shell")))
    return {"executable_path": c[-1]} if c else {}


def main():
    if len(sys.argv) != 2: sys.exit(__doc__)
    files = sorted(glob.glob(os.path.join(os.path.abspath(os.path.expanduser(sys.argv[1])), "slides", "*.html")))
    if not files: sys.exit("нет slides/*.html")
    fails = 0
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, **chrome_kw(p))
        try:
            pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            for f in files:
                pg.goto("file://" + f, wait_until="load"); pg.evaluate("document.fonts.ready.then(()=>true)"); pg.wait_for_timeout(300)
                name = os.path.splitext(os.path.basename(f))[0]
                res = pg.evaluate(JS)
                base = shot(pg) if any("T" in o for o in res) else None
                for i, o in enumerate(res):
                    if "T" in o:
                        o["rng"] = o["T"]; px = pixel_ink(pg, i, base)
                        if px: o["T"] = px
                    if "err" in o: print(f"{name}  FAIL  {o['err']}"); fails += 1; continue
                    L, R = o["L"], o["R"]
                    edges = [round(v) for v in (L["l"], L["t"], H - L["b"], W - R["r"], R["t"], H - R["b"])]
                    edge_s = f"края L(лев {edges[0]}, верх {edges[1]}, низ {edges[2]}) R(прав {edges[3]}, верх {edges[4]}, низ {edges[5]})"
                    cyB = round(((L["t"] + L["b"]) / 2 + (R["t"] + R["b"]) / 2) / 2)
                    if "T" not in o:
                        print(f"{name}  FAIL  br-{o['nn']}: цель не найдена, пометь data-bk-target | {edge_s}"); fails += 1; continue
                    T = o["T"]
                    gl, gr = round(T["l"] - L["r"]), round(R["l"] - T["r"])
                    cyD = round((o["rng"]["t"] + o["rng"]["b"]) / 2)  # по строкам DOM (справочно)
                    cyI = round((T["t"] + T["b"]) / 2)  # по всей краске (справочно)
                    if o.get("txt") and "base" in T:
                        cyT = round((T["t"] + T["base"]) / 2); cy_how = "верх-база"
                    else:
                        cyT = cyD; cy_how = "рамка"
                    why = []
                    if abs(gl - gr) > TOL: why.append(f"gap {gl}≠{gr}")
                    if abs(cyT - cyB) > TOL: why.append(f"центр {cyT}≠{cyB}")
                    if min(edges) < EDGE: why.append(f"край {min(edges)}<{EDGE}")
                    fails += bool(why)
                    print(f"{name}  {'FAIL' if why else 'ok  '}  br-{o['nn']} [{o['how']}] gapL {gl} gapR {gr} | "
                          f"центр цели {cyT} [{cy_how}] (DOM {cyD}, вся краска {cyI}) скобок {cyB} (L {round((L['t']+L['b'])/2)} R {round((R['t']+R['b'])/2)}) | {edge_s}"
                          + f" | Range: gapL {round(o['rng']['l'] - L['r'])} gapR {round(R['l'] - o['rng']['r'])}"
                          + (f" | {'; '.join(why)}" if why else ""))
        finally:
            b.close()
    print("ИТОГ:", "FAIL " + str(fails) if fails else "все пары ok")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
