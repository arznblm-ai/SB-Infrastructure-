# Google Drive для Крис — ранбук

### [[2026-09-10]]

Разовая настройка руками Антона. Аккаунт студии — **admin@portalcg.xyz**. Скоуп — `https://www.googleapis.com/auth/drive`.

## 1. OAuth client (Google Cloud Console)

1. Проект Google Cloud под аккаунтом admin@portalcg.xyz → **APIs & Services → Library → Google Drive API → Enable**.
2. **OAuth consent screen**: тип Internal (домен portalcg.xyz), добавить скоуп `.../auth/drive`.
   Если Internal недоступен (аккаунт не в Google Workspace) — бери **External**, оставь в режиме
   Testing и добавь `admin@portalcg.xyz` в **Test users**, иначе Google не пустит на consent.
3. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app**.
4. Скачать JSON и положить на VPS:
   ```bash
   scp client_secret_*.json root@163.5.29.10:/root/.config/kris/google-client.json
   ssh root@163.5.29.10 'chmod 600 /root/.config/kris/google-client.json'
   ```

## 2. Авторизация (основной путь — с мака)

Google свернул OOB-редирект (`urn:ietf:wg:oauth:2.0:oob`) для новых OAuth-клиентов, поэтому ручной flow на VPS может вернуть `invalid_request`. Основной способ — авторизоваться **на маке**, где есть браузер, и перенести готовый токен на сервер.

```bash
# 1. зависимости на маке (если ещё не стоят)
pip3 install google-auth google-auth-oauthlib google-api-python-client

# 2. локальный flow: поднимет http://localhost:<свободный порт> и откроет браузер
KRIS_GOOGLE_CLIENT=~/Downloads/client_secret_*.json \
KRIS_GOOGLE_TOKEN=/tmp/google-token.json \
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Kris/Scripts/gdrive.py" --auth --local
```

В браузере войти **под admin@portalcg.xyz** и разрешить доступ. Токен ляжет в `/tmp/google-token.json` (chmod 600).

```bash
# 3. перенести токен на VPS
scp /tmp/google-token.json root@163.5.29.10:/root/.config/kris/google-token.json
ssh root@163.5.29.10 'chmod 600 /root/.config/kris/google-token.json'

# 4. проверить, что сервер видит аккаунт студии
ssh root@163.5.29.10 'cd /opt/kris && .venv/bin/python gdrive.py --whoami'
# ожидаемо: admin@portalcg.xyz (Portal CG)
```

Порт локального сервера можно зафиксировать: `--auth --local --port 8765` (тогда этот `http://localhost:8765/` должен быть в Authorized redirect URIs, если клиент не Desktop-типа).

### 2b. Запасной путь — ручной flow на VPS (OOB)

Работает только для старых OAuth-клиентов, где OOB ещё разрешён.

```bash
ssh root@163.5.29.10
cd /opt/kris && .venv/bin/python gdrive.py --auth
```

Скрипт напечатает URL → открыть под admin@portalcg.xyz → скопировать код → вставить в терминал. Токен сохранится в `/root/.config/kris/google-token.json`.

Если Google ответит `invalid_request` / ошибкой про `redirect_uri` — скрипт выйдет с кодом 2 и сам напомнит про `--auth --local` на маке. Это не баг, а свёрнутый OOB: возвращайся к основному пути выше.

## 3. Проверка

```bash
ssh root@163.5.29.10 'cd /opt/kris && .venv/bin/python gdrive.py --whoami'
# ожидаемо: admin@portalcg.xyz (Portal CG)
```

## 4. Переменные окружения

В `/root/.config/kris/.env` (chmod 600):

```
KRIS_GOOGLE_TOKEN=/root/.config/kris/google-token.json
KRIS_GOOGLE_CLIENT=/root/.config/kris/google-client.json
KRIS_DRIVE_FOLDER=ESTIMATES
```

## 5. Зависимости

В venv `/opt/kris/.venv` должны стоять: `google-auth`, `google-auth-oauthlib`, `google-api-python-client`.

## Прочие команды

```bash
.venv/bin/python gdrive.py --upload /path/смета.xlsx --folder ESTIMATES   # → id + ссылка
.venv/bin/python gdrive.py --update <FILE_ID> /path/смета_v2.xlsx         # новая версия, ссылка та же
.venv/bin/python gdrive.py --export <FILE_ID> application/pdf out.pdf     # экспорт Google-документа
```

Если токен отозвали — любая команда скажет «запусти gdrive.py --auth», шаг 2 повторить (основным путём, с мака).
