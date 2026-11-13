#!/usr/bin/env python3
"""Рисунки системы «Кобальт»: inline-SVG из design-system/doodles/ для вставки в слайд.

    from doodle import doodle
    doodle("blob-bean", "s6a", fill="var(--tint)", width=240, rotate=-6, cls="num-blob")

CLI:  python3 doodle.py <name> [--uid X] [--color C] [--fill F] [--accent A] [--width W] [--rotate R] [--cls "a b"]
      python3 doodle.py --list
uid обязателен и уникален в пределах слайда: им префиксуются id фильтров, иначе два рисунка делят один фильтр.
Класс 'stretch' растягивает рисунок без сохранения пропорций (рамки, подчерки). Позицию задавай в CSS слайда по cls."""
import os, re, sys

DOODLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "design-system", "doodles")


def names():
    return sorted(f[:-4] for f in os.listdir(DOODLES) if f.endswith(".svg"))


def doodle(name, uid, color=None, fill=None, accent=None, width=None, rotate=0, cls=''):
    path = os.path.join(DOODLES, name + ".svg")
    if not os.path.exists(path):
        raise ValueError(f"нет рисунка {name!r}; есть: {', '.join(names())}")
    s = open(path, encoding="utf-8").read()
    pre = f"dd{re.sub(r'[^A-Za-z0-9_-]', '', str(uid))}-"
    s = s.replace('id="d2-', f'id="{pre}').replace("url(#d2-", f"url(#{pre}")
    par = "none" if "stretch" in cls.split() else "xMidYMid meet"
    s = s.replace("<svg ", f'<svg preserveAspectRatio="{par}" ', 1)
    st = []
    if color: st.append(f"color:{color}")
    if fill: st.append(f"--doodle-fill:{fill}")
    if accent: st.append(f"--doodle-accent:{accent}")
    if width:
        st.append(f"width:{width}px")
        vb = re.search(r'viewBox="([\d.\s-]+)"', s)
        if vb:
            _, _, vw, vh = map(float, vb.group(1).split())
            st.append(f"height:{round(width * vh / vw)}px")
    if rotate: st.append(f"transform:rotate({rotate}deg)")
    style = f' style="{";".join(st)}"' if st else ""
    return f'<i class="{("dd " + cls).strip()}"{style}>{s}</i>'


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        sys.exit(__doc__)
    if argv[0] == "--list":
        print("\n".join(names())); return
    name, opts = argv[0], {}
    it = iter(argv[1:])
    for a in it:
        if not a.startswith("--"): sys.exit(f"лишний аргумент {a}")
        opts[a[2:]] = next(it)
    print(doodle(name, opts.get("uid", "x"), color=opts.get("color"), fill=opts.get("fill"),
                 accent=opts.get("accent"), width=int(opts["width"]) if "width" in opts else None,
                 rotate=float(opts.get("rotate", 0)), cls=opts.get("cls", "")))


if __name__ == "__main__":
    main(sys.argv[1:])
