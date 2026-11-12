"""Предразбор фото пачки одним мультимодальным запросом к Nous (до Hermes-прогона).

Зачем: агент Hermes смотрит фото инструментом vision_analyze — по вызову на картинку
(и часто по несколько на одну), каждый 15-70 с на бесплатной vision-модели. Пачка
уходила в 300-420 с. Здесь все фото уходят одним запросом, а Hermes получает уже
текстовые описания.

Запускается интерпретатором Hermes (`/usr/local/lib/hermes-agent/venv/bin/python`),
чтобы взять Nous OAuth тем же кодом, что и сам Hermes (refresh, lock стора).
Секреты не печатаются и не пишутся. Выход — одна JSON-строка в stdout:
{"ok": true, "text": "...", "usage": {...}, "cost": $|null} или {"ok": false, "error": "..."}.

Использование: rocky_vision.py --model M [--timeout 40] IMG [IMG ...]
Окружение: HERMES_HOME (профиль бота), HERMES_SHARED_AUTH_DIR.
"""
import argparse
import base64
import io
import json
import os
import sys
import time

MAX_SIDE = 1024
JPEG_QUALITY = 85
PRICE_CACHE = "vision_prices.json"  # в HERMES_HOME; цены каталога Nous за токен
PRICE_TTL = 24 * 3600

PROMPT = (
    "Ты помогаешь коучу по питанию. На входе %d фото по порядку (фото 1..%d). "
    "Для КАЖДОГО фото дай сжатый разбор на русском, 2-5 строк, без markdown-заголовков:\n"
    "фото N: что это (еда / упаковка / этикетка / весы / скриншот / другое); "
    "блюдо и все видимые компоненты с примерной граммовкой каждого; способ приготовления; "
    "посуда и масштаб (размер тарелки, приборы) как опора для граммовки; "
    "всё читаемое с упаковки, этикетки, экрана весов или скриншота — дословно, с цифрами "
    "(ккал, БЖУ на 100 г, вес нетто, показания весов). "
    "Не считай итоговые калории, не давай советов. Если что-то не видно или неуверенно — "
    "так и пиши, не выдумывай. Если на нескольких фото одно и то же блюдо (другой ракурс) — скажи."
)


def encode(path):
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=JPEG_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def model_price(creds, model, timeout):
    """(prompt, completion) $/токен из каталога Nous /models, кэш PRICE_TTL. Нет - None."""
    import httpx
    path = os.path.join(os.environ.get("HERMES_HOME") or ".", PRICE_CACHE)
    try:
        with open(path) as fh:
            cache = json.load(fh)
        if time.time() - float(cache.get("ts", 0)) < PRICE_TTL and model in cache.get("prices", {}):
            return tuple(cache["prices"][model])
    except (OSError, ValueError, TypeError):
        pass
    resp = httpx.get(str(creds["base_url"]).rstrip("/") + "/models", timeout=timeout,
                     headers={"Authorization": "Bearer " + creds["api_key"]})
    resp.raise_for_status()
    data = resp.json()
    prices = {}
    for m in (data.get("data", []) if isinstance(data, dict) else data):
        pr = (m or {}).get("pricing") or {}
        try:
            prices[m["id"]] = [float(pr["prompt"]), float(pr["completion"])]
        except (KeyError, TypeError, ValueError):
            continue
    try:
        with open(path, "w") as fh:
            json.dump({"ts": time.time(), "prices": prices}, fh)
    except OSError:
        pass
    return tuple(prices[model]) if model in prices else None


def usage_cost(creds, model, usage, timeout):
    """$ за запрос: usage x цена каталога; каталог недоступен - usage.cost; иначе None."""
    try:
        price = model_price(creds, model, timeout)
        if price:
            return round(int(usage.get("prompt_tokens") or 0) * price[0]
                         + int(usage.get("completion_tokens") or 0) * price[1], 6)
    except Exception:  # noqa: BLE001 - цена не критична для разбора
        pass
    cost = usage.get("cost")
    return float(cost) if isinstance(cost, (int, float)) else None


def describe(paths, model, timeout):
    from hermes_cli.auth import resolve_nous_runtime_credentials
    import httpx

    creds = resolve_nous_runtime_credentials(timeout_seconds=min(15.0, timeout))
    content = [{"type": "text", "text": PROMPT % (len(paths), len(paths))}]
    for i, p in enumerate(paths, 1):
        content.append({"type": "text", "text": "фото %d:" % i})
        content.append({"type": "image_url", "image_url": {"url": encode(p)}})
    body = {"model": model, "messages": [{"role": "user", "content": content}],
            "max_tokens": 1800, "temperature": 0.2}
    url = str(creds["base_url"]).rstrip("/") + "/chat/completions"
    resp = httpx.post(url, json=body, timeout=timeout,
                      headers={"Authorization": "Bearer " + creds["api_key"]})
    if resp.status_code != 200:
        raise RuntimeError("http %s: %s" % (resp.status_code, resp.text[:300]))
    data = resp.json()
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    text = text.strip()
    if not text:
        raise RuntimeError("пустой ответ")
    usage = data.get("usage") or {}
    return text, usage, usage_cost(creds, model, usage, min(15.0, timeout))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--timeout", type=float, default=40.0)
    ap.add_argument("images", nargs="+")
    args = ap.parse_args(argv)
    try:
        text, usage, cost = describe(args.images, args.model, args.timeout)
        out = {"ok": True, "text": text, "usage": usage, "cost": cost}
    except Exception as exc:  # noqa: BLE001 - любой сбой = откат на старый путь
        out = {"ok": False, "error": ("%s: %s" % (type(exc).__name__, exc))[:400]}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
