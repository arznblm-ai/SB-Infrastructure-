#!/usr/bin/env python3
"""Krisp MCP OAuth2 authorization flow with PKCE"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import ssl
import time
import urllib.parse
import urllib.request
import webbrowser

CLIENT_ID = os.environ.get("KRISP_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("KRISP_CLIENT_SECRET", "")
REDIRECT_URI = "http://localhost:3399/callback"
AUTH_ENDPOINT = "https://api.krisp.ai/platform/v1/oauth2/authorize"
TOKEN_ENDPOINT = "https://api.krisp.ai/platform/v1/oauth2/token"
SCOPES = "user::me::read user::meetings::read user::meetings:notes::read user::meetings:transcripts::read user::meetings::list user::meetings:metadata::read user::activities::list"
HTTP_TIMEOUT = 30
AUTH_WAIT_TIMEOUT = 300

# PKCE
code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
digest = hashlib.sha256(code_verifier.encode()).digest()
code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
state = secrets.token_urlsafe(16)

auth_code = None
auth_error = None


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        global auth_code, auth_error
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        returned_state = params.get("state", [""])[0]

        if returned_state != state:
            auth_error = "state_mismatch"
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("Ошибка авторизации: state не совпал.".encode("utf-8"))
        elif "error" in params:
            auth_error = params["error"][0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"Ошибка авторизации: {auth_error}".encode("utf-8"))
        elif "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>OK! Вернись в терминал.</h2><script>window.close()</script>".encode("utf-8"))
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Error: no code")


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise SystemExit("Set KRISP_CLIENT_ID and KRISP_CLIENT_SECRET before running this script.")

    # Build auth URL
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)

    server = http.server.HTTPServer(("localhost", 3399), CallbackHandler)
    server.timeout = 1

    print(f"\nОткрываю браузер для авторизации Krisp...")
    webbrowser.open(auth_url)
    print("Жди завершения авторизации в браузере...\n")

    deadline = time.time() + AUTH_WAIT_TIMEOUT
    while not auth_code and not auth_error and time.time() < deadline:
        server.handle_request()
    server.server_close()

    if auth_error:
        print(f"Ошибка: авторизация не завершилась ({auth_error})")
        return

    if not auth_code:
        print("Ошибка: код авторизации не получен")
        return

    print(f"Код получен, обмениваю на токен...")

    # Exchange code for token
    token_data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": code_verifier,
    }).encode()

    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    req = urllib.request.Request(TOKEN_ENDPOINT, data=token_data, method="POST")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    ctx = ssl.create_default_context()
    ctx.load_verify_locations("/etc/ssl/cert.pem")
    with urllib.request.urlopen(req, context=ctx, timeout=HTTP_TIMEOUT) as resp:
        token_response = json.loads(resp.read())

    print(f"\nТокен получен. Ответ содержит поля: {', '.join(sorted(token_response.keys()))}")

    # Save to ~/.claude/krisp_token.json
    token_path = os.path.expanduser("~/.claude/krisp_token.json")
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w") as f:
        json.dump(token_response, f, indent=2)
    print(f"\nТокен сохранён в {token_path}")

    # Update mcp.json with auth header
    mcp_path = os.path.expanduser("~/.claude/mcp.json")
    os.makedirs(os.path.dirname(mcp_path), exist_ok=True)
    with open(mcp_path) as f:
        mcp_config = json.load(f)

    access_token = token_response["access_token"]
    mcp_config["mcpServers"]["krisp"] = {
        "type": "http",
        "url": "https://mcp.krisp.ai/mcp",
        "headers": {
            "Authorization": f"Bearer {access_token}"
        }
    }

    with open(mcp_path, "w") as f:
        json.dump(mcp_config, f, indent=2)
    print(f"mcp.json обновлён с Bearer токеном.")


if __name__ == "__main__":
    main()
