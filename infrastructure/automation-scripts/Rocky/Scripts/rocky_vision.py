"""Предразбор фото пачки одним мультимодальным запросом к Nous (до Hermes-прогона).

Зачем: агент Hermes смотрит фото инструментом vision_analyze — по вызову на картинку
(и часто по несколько на одну), каждый 15-70 с на бесплатной vision-модели. Пачка
уходила в 300-420 с. Здесь все фото уходят одним запросом, а Hermes получает уже
текстовые описания.

Запускается интерпретатором Hermes (`/usr/local/lib/hermes-agent/venv/bin/python`),
чтобы взять Nous OAuth тем же кодом, что и сам Hermes (refresh, lock стора).
Секреты не печатаются и не пишутся. Выход — одна JSON-строка в stdout:
{"ok": true, "text": "..."} или {"ok": false, "error": "..."}.

Использование: rocky_vision.py --model M [--timeout 40] IMG [IMG ...]
Окружение: HERMES_HOME (профиль бота), HERMES_SHARED_AUTH_DIR.
"""
import argparse
import base64
import io
import json
import sys

MAX_SIDE = 1024
JPEG_QUALITY = 85

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
    return text, (data.get("usage") or {})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--timeout", type=float, default=40.0)
    ap.add_argument("images", nargs="+")
    args = ap.parse_args(argv)
    try:
        text, usage = describe(args.images, args.model, args.timeout)
        out = {"ok": True, "text": text, "usage": usage}
    except Exception as exc:  # noqa: BLE001 - любой сбой = откат на старый путь
        out = {"ok": False, "error": ("%s: %s" % (type(exc).__name__, exc))[:400]}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
