#!/usr/bin/env python3
"""python3 new_carousel.py <out_dir> <N>
Заготовка карусели «Кобальт» в любой папке (обычно ~/Desktop/Vibecode OUT/<дата>/carousel <тема>/):
  <out>/ds/base.css, <out>/ds/fit.js   копия дизайн-системы (обновляется при каждом запуске)
  <out>/assets/                        кадры и картинки слайдов
  <out>/slides/01..N.html              заготовки (существующие не перезаписываются)
Почему копия, а не ссылка в vault: карусель самодостаточна (переезд vault и WKWebView-фолбэк ей не мешают).
Рендер: python3 render.py <out> ; лента: python3 contact_sheet.py <out>"""
import os, sys, shutil

DS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "design-system")

MARK = ('<svg viewBox="0 0 40 40"><path d="M21 5c8 1 14 6 13 15s-7 15-15 14S5 28 6 19 13 4 21 5z" fill="currentColor"/>'
        '<path d="M34 6l3-3M37 12h3M30 2l1-2" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>')

HINT = """<!--
  Типы слайдов (эталон: system/skills/carousel/examples/test-ai-remake/slides/NN.html):
    обложка-хук 01 · кадр+подпись 02 · шаг 03 · промпт-карточка 04 · сетка 2x2 с выбором 05 · цифры на пятнах 06 · CTA 07
  Классы base.css: .serif (em = кобальт курсив), .l + data-fit на заголовке, .tag.tg, .lead, .txt, .hl (желтый маркер),
    .card / .card.accent / .img, .btn, .chip, .dd (рисунок).
  Рисунок: python3 scripts/doodle.py <name> --uid sNN1 [--fill "var(--tint)"] --cls "мой-класс" -> вставить SVG сюда,
    позицию задать в <style> по классу. Одна шутка на слайд.
  Поля >= 64px, основной текст >= 34px, без «ё» и длинных тире, предлог склеивать с словом через &nbsp;.
-->"""


def slide(i, n):
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Слайд {i:02d}</title>
<link rel="stylesheet" href="../ds/base.css"><script src="../ds/fit.js"></script>
<style>
.box{{position:absolute;left:var(--pad);right:var(--pad);top:{150 if i == 1 else 210}px;display:flex;flex-direction:column;gap:40px}}
h1{{font-size:140px}} h2{{font-size:110px}}
</style></head><body>
{HINT}
<div class="top"><span class="mark">{MARK}</span><span class="count">{i:02d}/{n:02d}</span></div>
{'' if i == 1 else '<span class="tag tg">Метка</span>'}
<div class="box">
  {'<h1 class="serif" data-fit="150"><span class="l">Заголовок</span><br><em class="l">вторая строка</em></h1>' if i == 1 else '<h2 class="serif" data-fit="120"><span class="l">Заголовок</span><br><em class="l">курсив кобальтом</em></h2>'}
  <p class="lead">Текст слайда, <span class="hl">выделение</span> маркером</p>
</div>
</body></html>
"""


def main():
    if len(sys.argv) != 3 or not sys.argv[2].isdigit(): sys.exit(__doc__)
    out, n = os.path.abspath(os.path.expanduser(sys.argv[1])), int(sys.argv[2])
    for d in ("ds", "assets", "slides"): os.makedirs(os.path.join(out, d), exist_ok=True)
    for f in ("base.css", "fit.js"): shutil.copy(os.path.join(DS, f), os.path.join(out, "ds", f))
    made = []
    for i in range(1, n + 1):
        p = os.path.join(out, "slides", f"{i:02d}.html")
        if os.path.exists(p): continue
        open(p, "w", encoding="utf-8").write(slide(i, n)); made.append(os.path.basename(p))
    print(out, "создано:", ", ".join(made) or "ничего (слайды уже есть)", "| ds/ обновлен")


if __name__ == "__main__":
    main()
