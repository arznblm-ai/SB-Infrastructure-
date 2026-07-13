#!/usr/bin/env python3
"""Персональный астропрогноз по натальной карте Антона.

Считает натальную карту (Swiss Ephemeris, Moshier — файлы эфемерид не нужны)
и транзиты на заданный месяц: точные аспекты медленных планет и Марса к
натальным точкам, лунации, ретро-периоды Меркурия, положение транзитов в
натальных домах (Placidus).

Запуск (venv: ~/.astro-env, установка — см. CLAUDE.md проекта):
    ~/.astro-env/bin/python natal_forecast.py 2026 8            # рабочий слой
    ~/.astro-env/bin/python natal_forecast.py 2026 8 --love     # + Венера/Солнце (личная жизнь)
    ~/.astro-env/bin/python natal_forecast.py --natal           # только натальная карта

Скрипт печатает сырые транзиты; интерпретацию пишет агент (см. CLAUDE.md).
"""
import argparse
import swisseph as swe

# Натальные данные: 29.11.1997, 21:05 MSK (UTC+3) -> 18:05 UT, Москва.
# Время 21:05 подтверждено Антоном 2026-07-11 (ранее ошибочно считали 09:10).
JD_NATAL = swe.julday(1997, 11, 29, 18 + 5 / 60.0)
LAT, LON = 55.7558, 37.6173

FLG = swe.FLG_MOSEPH | swe.FLG_SPEED
SIGNS = ["Ari", "Tau", "Gem", "Can", "Leo", "Vir", "Lib", "Sco", "Sag", "Cap", "Aqu", "Pis"]
PLANETS = {
    swe.SUN: "Sun", swe.MOON: "Moon", swe.MERCURY: "Mercury", swe.VENUS: "Venus",
    swe.MARS: "Mars", swe.JUPITER: "Jupiter", swe.SATURN: "Saturn",
    swe.URANUS: "Uranus", swe.NEPTUNE: "Neptune", swe.PLUTO: "Pluto",
    swe.MEAN_NODE: "Node",
}
ASPECTS = {0: "conj", 60: "sextile", 90: "square", 120: "trine", 180: "opposition"}
# Транзитные планеты рабочего слоя; --love добавляет Венеру и Солнце.
TRANS_BASE = {swe.MARS: "Mars", swe.JUPITER: "Jupiter", swe.SATURN: "Saturn",
              swe.URANUS: "Uranus", swe.NEPTUNE: "Neptune", swe.PLUTO: "Pluto"}
LOVE_NATAL_POINTS = ("Sun", "Moon", "Venus", "Mars", "ASC", "MC")


def fmt(lon_deg):
    s = int(lon_deg // 30)
    d = lon_deg % 30
    return f"{int(d):2d}°{int(d % 1 * 60):02d}' {SIGNS[s]}"


def delta(a, b):
    return (a - b + 180) % 360 - 180


def compute_natal():
    natal = {}
    for p, name in PLANETS.items():
        pos, _ = swe.calc_ut(JD_NATAL, p, FLG)
        natal[name] = pos[0]
    cusps, ascmc = swe.houses(JD_NATAL, LAT, LON, b'P')
    natal["ASC"], natal["MC"] = ascmc[0], ascmc[1]
    return natal, cusps


def house_of(cusps, lon_pt):
    for i in range(12):
        a, b = cusps[i], cusps[(i + 1) % 12]
        if (a <= b and a <= lon_pt < b) or (a > b and (lon_pt >= a or lon_pt < b)):
            return i + 1
    return None


def print_natal(natal, cusps):
    print("=== NATAL 1997-11-29 21:05 MSK Moscow (Placidus) ===")
    for name, lon_pt in natal.items():
        h = "" if name in ("ASC", "MC") else f"  house {house_of(cusps, lon_pt)}"
        print(f"{name:8s} {fmt(lon_pt)}{h}")
    print("Cusps:", " | ".join(f"{i+1}:{fmt(c)}" for i, c in enumerate(cusps[:12])))


def scan_aspects(jd0, jd1, trans, natal, step=0.125):
    """Точные аспекты (пересечения нуля) транзитных планет к натальным точкам."""
    events = []
    for p, tname in trans.items():
        for nname, nlon in natal.items():
            for ang, aname in ASPECTS.items():
                for tgt in {(nlon + ang) % 360, (nlon - ang) % 360}:
                    prev, jd = None, jd0
                    while jd <= jd1:
                        pos, _ = swe.calc_ut(jd, p, FLG)
                        d = delta(pos[0], tgt)
                        if prev is not None and prev * d < 0 and abs(d - prev) < 20:
                            _, _, dd, _ = swe.revjul(jd)
                            events.append((jd, f"{tname} {aname} natal {nname}  ~ day {int(dd)}"))
                        prev, jd = d, jd + step
    # экзакт может повториться из-за ретро-петли — дубликаты в один день схлопываем
    seen, out = set(), []
    for jd, e in sorted(events):
        if e not in seen:
            seen.add(e)
            out.append((jd, e))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("year", type=int, nargs="?")
    ap.add_argument("month", type=int, nargs="?")
    ap.add_argument("--love", action="store_true",
                    help="добавить транзиты Венеры и Солнца к личным точкам")
    ap.add_argument("--natal", action="store_true", help="напечатать только натальную карту")
    args = ap.parse_args()

    natal, cusps = compute_natal()
    if args.natal or not (args.year and args.month):
        print_natal(natal, cusps)
        if not (args.year and args.month):
            return

    y, m = args.year, args.month
    y2, m2 = (y + 1, 1) if m == 12 else (y, m + 1)
    jd0, jd1 = swe.julday(y, m, 1, 0.0), swe.julday(y2, m2, 1, 0.0)

    print(f"\n=== Slow transits {y}-{m:02d} (1st -> last day) ===")
    for p, name in {**TRANS_BASE, swe.MEAN_NODE: "Node"}.items():
        p0, _ = swe.calc_ut(jd0, p, FLG)
        p1, _ = swe.calc_ut(jd1 - 0.01, p, FLG)
        print(f"{name:8s} {fmt(p0[0])} -> {fmt(p1[0])}  (natal houses {house_of(cusps, p0[0])}->{house_of(cusps, p1[0])})")

    print(f"\n=== Exact transit aspects {y}-{m:02d} ===")
    for _, e in scan_aspects(jd0, jd1, TRANS_BASE, natal):
        print(" ", e)

    if args.love:
        print(f"\n=== LOVE layer: Venus & Sun transits to personal points ===")
        love_natal = {k: natal[k] for k in LOVE_NATAL_POINTS}
        for _, e in scan_aspects(jd0, jd1, {swe.VENUS: "Venus", swe.SUN: "Sun"}, love_natal):
            print(" ", e)
        v0, _ = swe.calc_ut(jd0, swe.VENUS, FLG)
        v1, _ = swe.calc_ut(jd1 - 0.01, swe.VENUS, FLG)
        print(f"Venus    {fmt(v0[0])} -> {fmt(v1[0])}  (natal houses {house_of(cusps, v0[0])}->{house_of(cusps, v1[0])})")

    print(f"\n=== Lunations {y}-{m:02d} ===")
    jd, prev_e = jd0, None
    while jd <= jd1:
        s, _ = swe.calc_ut(jd, swe.SUN, FLG)
        mo, _ = swe.calc_ut(jd, swe.MOON, FLG)
        e = (mo[0] - s[0]) % 360
        if prev_e is not None:
            if prev_e > 350 and e < 10:
                _, _, dd, _ = swe.revjul(jd)
                print(f"  New Moon ~ day {int(dd)} at {fmt(s[0])} (natal house {house_of(cusps, s[0])})")
            if prev_e < 180 <= e:
                _, _, dd, _ = swe.revjul(jd)
                print(f"  Full Moon ~ day {int(dd)} at {fmt(mo[0])} (Moon in natal house {house_of(cusps, mo[0])})")
        prev_e, jd = e, jd + 0.125

    print(f"\n=== Mercury motion {y}-{m:02d} ===")
    jd, was = jd0, None
    while jd <= jd1:
        pos, _ = swe.calc_ut(jd, swe.MERCURY, FLG)
        st = "R" if pos[3] < 0 else "D"
        if st != was:
            _, _, dd, _ = swe.revjul(jd)
            verb = "retrograde since/turns retrograde" if st == "R" else "direct"
            print(f"  day {int(dd)}: Mercury {verb} at {fmt(pos[0])} (natal house {house_of(cusps, pos[0])})")
            was = st
        jd += 0.25


if __name__ == "__main__":
    main()
