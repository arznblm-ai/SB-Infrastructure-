#!/usr/bin/env python3
"""python3 contact_sheet.py <папка_карусели>
<папка>/png/NN.png -> <папка>/contact.png: лента слайдов в ряд, высота превью 540."""
import os, sys, glob
from PIL import Image

TH, PAD = 540, 24
BG = (24, 24, 26)


def main():
    if len(sys.argv) < 2: sys.exit(__doc__)
    d = os.path.abspath(sys.argv[1])
    pngs = sorted(glob.glob(os.path.join(d, "png", "[0-9][0-9].png")))
    if not pngs: sys.exit("нет png/NN.png в " + d + " (сначала render.py)")
    ims = []
    for p in pngs:
        im = Image.open(p).convert("RGB")
        ims.append(im.resize((round(im.width * TH / im.height), TH), Image.LANCZOS))
    sheet = Image.new("RGB", (PAD + sum(i.width + PAD for i in ims), TH + 2 * PAD), BG)
    x = PAD
    for i in ims:
        sheet.paste(i, (x, PAD)); x += i.width + PAD
    out = os.path.join(d, "contact.png")
    sheet.save(out)
    print(out, sheet.size, len(ims), "слайдов")


if __name__ == "__main__":
    main()
