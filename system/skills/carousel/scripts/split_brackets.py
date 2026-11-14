#!/usr/bin/env python3
"""python3 split_brackets.py [--src DIR] [--out DIR] [--no-preview]
Режет PNG-пары фирменных скобок «[ ]» (файлы «LOGO – 01 – NN.png», прозрачный фон) на левую и правую:
  разрез по самому широкому полностью прозрачному вертикальному промежутку около середины,
  каждая половина обрезается по альфе вплотную (альфа > ALPHA).
Пишет <out>/br-NN-L.png, <out>/br-NN-R.png, копию оригинала <out>/src/NN.png и превью <out>/index.png.
Оригиналы в --src не трогаются. Если промежутка нет (скобки соприкасаются), материал пропускается."""
import argparse, glob, os, re, shutil, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = "/Users/anton/AI AGENT FOLDER/Second Brain/tasks/Portal AI Portfolio/Assets/Portal Branding/FINAL BRANDING/BRACKETS"
OUT = os.path.join(SKILL, "design-system-rozenblum", "brackets")
ALPHA = 8
MID_ZONE = (0.2, 0.8)  # промежуток ищем в этой доле ширины


def tight(im, a):
    ys, xs = np.where(a > ALPHA)
    return im.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))


def split(path):
    im = Image.open(path).convert("RGBA")
    a = np.array(im)[:, :, 3]
    empty = ~(a > ALPHA).any(axis=0)
    w = a.shape[1]
    lo, hi = int(w * MID_ZONE[0]), int(w * MID_ZONE[1])
    best, x = None, lo
    while x < hi:
        if empty[x]:
            s = x
            while x < w and empty[x]: x += 1
            # промежуток должен быть между двумя непустыми частями
            if s > 0 and x < w and (best is None or x - s > best[1] - best[0]): best = (s, x)
        x += 1
    if best is None: return None
    cut = (best[0] + best[1]) // 2
    L = tight(im.crop((0, 0, cut, im.height)), a[:, :cut])
    R = tight(im.crop((cut, 0, w, im.height)), a[:, cut:])
    return L, R, best[1] - best[0]


def preview(pairs, out):
    cw, ch, pad = 360, 300, 20
    cols = 4
    rows = (len(pairs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cw, rows * ch), "#888888")
    try: font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
    except Exception: font = ImageFont.load_default()
    for k, (nn, L, R) in enumerate(pairs):
        x0, y0 = (k % cols) * cw, (k // cols) * ch
        cell = Image.new("RGB", (cw, ch))
        d = ImageDraw.Draw(cell)
        d.rectangle((0, 0, cw // 2, ch), fill="#1E1E1E"); d.rectangle((cw // 2, 0, cw, ch), fill="#FDFDFD")
        # пара целиком по центру ячейки: левая скобка на темном, правая на светлом
        h = ch - 70
        sl, sr = h / L.height, h / R.height
        s = min(sl, sr, (cw // 2 - pad * 2) / max(L.width, R.width))
        l2 = L.resize((max(1, int(L.width * s)), max(1, int(L.height * s))), Image.LANCZOS)
        r2 = R.resize((max(1, int(R.width * s)), max(1, int(R.height * s))), Image.LANCZOS)
        cell.paste(l2, (cw // 2 - pad - l2.width, 50), l2)
        cell.paste(r2, (cw // 2 + pad, 50), r2)
        d.rectangle((0, 0, 56, 34), fill="#E0301E"); d.text((8, 6), nn, fill="white", font=font)
        sheet.paste(cell, (x0, y0))
        ImageDraw.Draw(sheet).rectangle((x0, y0, x0 + cw - 1, y0 + ch - 1), outline="#888888", width=2)
    sheet.save(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=SRC); ap.add_argument("--out", default=OUT)
    ap.add_argument("--no-preview", action="store_true")
    o = ap.parse_args()
    files = sorted(glob.glob(os.path.join(o.src, "*.png")))
    os.makedirs(os.path.join(o.out, "src"), exist_ok=True)
    pairs, bad = [], []
    print(f"{'NN':<4}{'L':>12}{'R':>12}{'gap':>6}")
    for f in files:
        m = re.search(r"(\d+)\.png$", os.path.basename(f))
        if not m: continue
        nn = m.group(1)
        r = split(f)
        if r is None:
            bad.append(nn); print(f"{nn:<4}  нет прозрачного промежутка, пропущен"); continue
        L, R, gap = r
        L.save(os.path.join(o.out, f"br-{nn}-L.png")); R.save(os.path.join(o.out, f"br-{nn}-R.png"))
        shutil.copy(f, os.path.join(o.out, "src", f"{nn}.png"))
        pairs.append((nn, L, R))
        print(f"{nn:<4}{L.width:>5}x{L.height:<6}{R.width:>5}x{R.height:<6}{gap:>4}")
    if pairs and not o.no_preview:
        preview(pairs, os.path.join(o.out, "index.png")); print("превью:", os.path.join(o.out, "index.png"))
    if bad: print("ПРОПУЩЕНЫ (скобки соприкасаются):", ", ".join(bad)); sys.exit(2 if not pairs else 0)


if __name__ == "__main__":
    main()
