"""Общий сборщик сметы xlsx из JSON поверх эталонного шаблона Антона.

Обобщает проектные скрипты (`build_estimate_sibur.py` и др.): шаблон уже чистый
(формулы починены, USD-колонка убрана, количества/дни пусты), поэтому сборщику
остаётся только заполнить строки, шапку и проценты, а затем закэшировать значения
формул инъекцией <v> в XML (на маке нет LibreOffice, openpyxl формулы не считает).

Использование:
    python3 Scripts/estimate_build.py <estimate.json> <out.xlsx> [--template PATH]

Схема входного JSON:
    {
      "client": "СИБУР",
      "project": "Мелиорация - продуктовый AI-ролик 60-75 сек",
      "production": null,
      "variant": "base",
      "fee_pct": 0.05,
      "tax_pct": 0.15,
      "lines": [{"role": "producer", "qty": 1, "days": 15}, ...],
      "unknown": [{"title": "Раскадровщик агентства", "unit": "per proj"}]
    }

Правила:
  - ставки модель не задаёт: `rate` во входе игнорируется; если у строки нет ставки
    в шаблоне — роль попадает в `missing_roles`, колонка G остаётся пустой;
  - неизвестная роль и элементы `unknown[]` пишутся в строку Miscellanea
    соответствующего листа подписью «<название> ?» и уходят в `missing_roles`;
  - колонка D (примечания) остаётся пустой; в C7 пишется только `project`.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import range_boundaries, get_column_letter

DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "memory" / "templates"
    / "{kris} {template} смета xlsx шаблон Антона (VTB 07.09) – 2026-09-10.xlsx"
)

# role -> (sheet, row, kind); kind: "day" = qty*rate*days, "proj" = qty*rate
ROLE_MAP = {
    "producer": ("PRE PRODUCTION", 8, "day"),
    "art_director": ("PRE PRODUCTION", 9, "day"),
    "cg_supervisor": ("PRE PRODUCTION", 10, "day"),
    "ai_artist": ("PRE PRODUCTION", 11, "day"),
    "tech_lead": ("PRE PRODUCTION", 12, "day"),
    "creatives": ("PRE PRODUCTION", 17, "proj"),
    "storyboard": ("PRE PRODUCTION", 18, "proj"),
    "assets": ("PRE PRODUCTION", 23, "proj"),
    "tests": ("PRE PRODUCTION", 24, "proj"),
    "designer_3d": ("PRODUCTION", 8, "day"),
    "modeller": ("PRODUCTION", 9, "day"),
    "sculptor": ("PRODUCTION", 10, "day"),
    "texture": ("PRODUCTION", 11, "day"),
    "simulation": ("PRODUCTION", 12, "day"),
    "rigger": ("PRODUCTION", 13, "day"),
    "animator": ("PRODUCTION", 14, "day"),
    "environment": ("PRODUCTION", 15, "day"),
    "editor": ("POST PRODUCTION", 8, "day"),
    "grading": ("POST PRODUCTION", 9, "day"),
    "stock_footage": ("POST PRODUCTION", 10, "proj"),
    "resizes": ("POST PRODUCTION", 11, "proj"),
    "designer_2d": ("POST PRODUCTION", 16, "day"),
    "vfx": ("POST PRODUCTION", 17, "day"),
    "composer": ("POST PRODUCTION", 18, "day"),
    "camera_tracking": ("POST PRODUCTION", 19, "day"),
    "cleanup": ("POST PRODUCTION", 20, "day"),
    "lighting": ("POST PRODUCTION", 21, "day"),
    "sound_producer": ("POST PRODUCTION", 26, "proj"),
    "voice_over": ("POST PRODUCTION", 27, "proj"),
    "audiostock": ("POST PRODUCTION", 28, "proj"),
}

# строки «Прочее» по листам (справочник); по умолчанию всё неизвестное идёт
# в POST!29 — «по смыслу» строку выбирает человек, модель не угадывает
MISC_ROWS = {
    "PRE PRODUCTION": 13,
    "PRODUCTION": 16,
    "POST PRODUCTION": 22,
}
MISC_FALLBACK = ("POST PRODUCTION", 29)

COL_TITLE, COL_NOTE, COL_QTY, COL_RATE, COL_DAYS = 3, 4, 5, 7, 8


# --------------------------------------------------------------------------- #
# кэш значений формул (перенос логики Alina/Scripts/inject_values.py)
# --------------------------------------------------------------------------- #
def inject_cached_values(src: Path, dst: Path) -> dict:
    """Посчитать значения формул книги и вписать <v> прямо в XML листов.

    Поддерживаются формулы шаблона: ссылки на листы, SUM(range), + - * / ( ), $.
    Возвращает {(sheet, coord): value}.
    """
    src, dst = Path(src), Path(dst)
    wb = load_workbook(src)
    memo: dict = {}

    def val(sheet: str, coord: str) -> float:
        key = (sheet, coord)
        if key in memo:
            return memo[key]
        memo[key] = 0.0  # защита от циклической ссылки
        v = wb[sheet][coord].value
        if isinstance(v, str) and v.startswith("="):
            r = evalf(sheet, v[1:])
        elif isinstance(v, (int, float)):
            r = float(v)
        else:
            r = 0.0
        memo[key] = r
        return r

    def rng_sum(sheet: str, rng: str) -> float:
        c1, r1, c2, r2 = range_boundaries(rng)
        s = 0.0
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                s += val(sheet, f"{get_column_letter(c)}{r}")
        return s

    def evalf(sheet: str, f: str) -> float:
        f = f.replace("$", "")

        def sumrep(m):
            sh = (m.group(1) or sheet).strip("'!")
            return repr(rng_sum(sh, m.group(2)))

        f = re.sub(
            r"SUM\(((?:'[^']+'!|[A-Za-z][A-Za-z ]*!)?)([A-Z]+\d+:[A-Z]+\d+)\)",
            sumrep, f)

        def refrep(m):
            sh = (m.group(1) or sheet).strip("'!")
            return repr(val(sh, m.group(2)))

        f = re.sub(r"((?:'[^']+'!|[A-Za-z][A-Za-z ]*!)?)([A-Z]{1,2}\d+)", refrep, f)
        return float(eval(f, {"__builtins__": {}}, {}))

    values: dict = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    values[(ws.title, c.coordinate)] = val(ws.title, c.coordinate)

    with zipfile.ZipFile(src) as z:
        wbxml = z.read("xl/workbook.xml").decode()
        rels = z.read("xl/_rels/workbook.xml.rels").decode()
        rid = {m.group(1): m.group(2) for m in re.finditer(
            r'<Relationship[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"', rels)}
        rid.update({m.group(2): m.group(1) for m in re.finditer(
            r'<Relationship[^>]*Target="([^"]+)"[^>]*Id="([^"]+)"', rels)})
        sheets = {}
        for m in re.finditer(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', wbxml):
            name = m.group(1).replace("&amp;", "&")
            target = rid[m.group(2)]
            sheets[name] = "xl/" + target if not target.startswith("/") else target[1:]

        dst.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
            for item in z.infolist():
                data = z.read(item.filename)
                for name, path in sheets.items():
                    if item.filename != path:
                        continue
                    x = data.decode()

                    def cellrep(m, _name=name):
                        coord, body = m.group(1), m.group(0)
                        if (_name, coord) in values and "<f>" in body:
                            v = f"<v>{values[(_name, coord)]!r}</v>"
                            if "<v/>" in body:
                                body = body.replace("<v/>", v)
                            elif "<v></v>" in body:
                                body = body.replace("<v></v>", v)
                            elif "<v>" not in body:
                                body = body.replace("</f>", "</f>" + v)
                        return body

                    data = re.sub(r'<c r="([A-Z]+\d+)"[^>]*>.*?</c>',
                                  cellrep, x, flags=re.S).encode()
                out.writestr(item, data)
    return values


# --------------------------------------------------------------------------- #
@dataclass
class BuildResult:
    total: float
    subtotal: float
    missing_roles: list
    out_path: Path
    lines_written: int


def _num(value, field: str, *, allow_float=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field}: ожидается число, получено {value!r}")
    if not allow_float and int(value) != value:
        raise ValueError(f"{field}: ожидается целое число, получено {value!r}")
    if value < 0:
        raise ValueError(f"{field}: значение не может быть отрицательным ({value})")
    return value


def _pct(value, field: str) -> float:
    v = _num(value, field)
    if v > 1:
        raise ValueError(f"{field}: доля должна быть в диапазоне [0, 1], получено {v}")
    return float(v)


def _unmerge_title(ws, row: int) -> None:
    """Снять слияние C:D в строке, чтобы длинная подпись не пряталась под примечание."""
    for rng in list(ws.merged_cells.ranges):
        if rng.min_row == row and rng.min_col == COL_TITLE and rng.max_col == COL_NOTE:
            ws.unmerge_cells(str(rng))


def build(estimate: dict, template: Path, out_xlsx: Path) -> BuildResult:
    if not isinstance(estimate, dict):
        raise ValueError("estimate: ожидается JSON-объект")
    template, out_xlsx = Path(template), Path(out_xlsx)
    if not template.exists():
        raise ValueError(f"шаблон не найден: {template}")

    client = estimate.get("client")
    project = estimate.get("project")
    if not client or not isinstance(client, str):
        raise ValueError("client: обязательное непустое строковое поле")
    if not project or not isinstance(project, str):
        raise ValueError("project: обязательное непустое строковое поле")
    production = estimate.get("production") or None
    if production is not None and not isinstance(production, str):
        raise ValueError("production: строка или null")

    fee_pct = _pct(estimate.get("fee_pct", 0.05), "fee_pct")
    tax_pct = _pct(estimate.get("tax_pct", 0.15), "tax_pct")

    lines = estimate.get("lines") or []
    unknown = estimate.get("unknown") or []
    if not isinstance(lines, list):
        raise ValueError("lines: ожидается список")
    if not isinstance(unknown, list):
        raise ValueError("unknown: ожидается список")

    wb = load_workbook(template)
    missing_roles: list = []
    written = 0
    misc_used: dict = {}   # sheet -> [подписи]
    misc_qty: dict = {}    # sheet -> суммарное кол-во

    def to_misc(title: str, qty, target=MISC_FALLBACK) -> None:
        sheet, row = target
        misc_used.setdefault((sheet, row), []).append(f"{title} ?")
        misc_qty[(sheet, row)] = misc_qty.get((sheet, row), 0) + (qty or 0)
        if title not in missing_roles:
            missing_roles.append(title)

    for i, ln in enumerate(lines):
        where = f"lines[{i}]"
        if not isinstance(ln, dict):
            raise ValueError(f"{where}: ожидается объект")
        role = ln.get("role")
        if not role or not isinstance(role, str):
            raise ValueError(f"{where}.role: обязательное непустое строковое поле")
        qty = _num(ln.get("qty", 1), f"{where}.qty", allow_float=False)
        days = ln.get("days")

        if role not in ROLE_MAP:
            to_misc(role, qty)
            continue

        sheet, row, kind = ROLE_MAP[role]
        ws = wb[sheet]
        if kind == "day":
            if days is None:
                raise ValueError(
                    f"{where}: роль '{role}' считается по дням — нужно поле days")
            days = _num(days, f"{where}.days")
        elif days is not None:
            days = _num(days, f"{where}.days")

        rate = ws.cell(row=row, column=COL_RATE).value
        formula = ws.cell(row=row, column=11).value or ""
        has_rate = rate not in (None, "")
        if not has_rate:
            # модель ставок не задаёт: строка остаётся без ставки, роль — в missing
            if role not in missing_roles:
                missing_roles.append(role)

        ws.cell(row=row, column=COL_QTY).value = qty
        if kind == "day":
            ws.cell(row=row, column=COL_DAYS).value = days
        else:
            # per-proj строки, чья формула умножает на «Дни», требуют H=1,
            # иначе итог обнулится (напр. POST!K11 = E*(G*H+I*J))
            if days is not None:
                ws.cell(row=row, column=COL_DAYS).value = days
            elif has_rate and "H" in str(formula):
                ws.cell(row=row, column=COL_DAYS).value = 1
        written += 1

    for i, item in enumerate(unknown):
        where = f"unknown[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where}: ожидается объект")
        title = item.get("title")
        if not title or not isinstance(title, str):
            raise ValueError(f"{where}.title: обязательное непустое строковое поле")
        qty = _num(item.get("qty", 1), f"{where}.qty", allow_float=False)
        to_misc(title, qty)

    for (sheet, row), titles in misc_used.items():
        ws = wb[sheet]
        _unmerge_title(ws, row)
        ws.cell(row=row, column=COL_TITLE).value = "; ".join(titles)
        ws.cell(row=row, column=COL_QTY).value = misc_qty.get((sheet, row)) or None
        written += 1

    m = wb["MAIN"]
    m["C6"] = client
    m["C7"] = project
    m["C8"] = production
    m["D22"] = fee_pct
    m["D23"] = tax_pct

    out_xlsx = Path(out_xlsx)
    out_xlsx.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="kris-estimate-"))
    try:
        tmp = tmp_dir / "raw.xlsx"
        wb.save(tmp)
        inject_cached_values(tmp, out_xlsx)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    done = load_workbook(out_xlsx, data_only=True)
    total = done["MAIN"]["E24"].value
    subtotal = done["MAIN"]["E21"].value
    return BuildResult(
        total=round(float(total or 0), 2),
        subtotal=round(float(subtotal or 0), 2),
        missing_roles=missing_roles,
        out_path=out_xlsx,
        lines_written=written,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Собрать смету xlsx из JSON по шаблону Антона")
    ap.add_argument("estimate_json")
    ap.add_argument("out_xlsx")
    ap.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    args = ap.parse_args(argv)
    try:
        data = json.loads(Path(args.estimate_json).read_text(encoding="utf-8"))
        res = build(data, Path(args.template), Path(args.out_xlsx))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"estimate.json: не разобран JSON — {e}", file=sys.stderr)
        return 1
    out = asdict(res)
    out["out_path"] = str(res.out_path)
    out.pop("lines_written", None)
    out["lines_written"] = res.lines_written
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
