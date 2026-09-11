"""Тесты gdrive.py — без сети, service замокан вручную."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gdrive  # noqa: E402
from gdrive import XLSX_MIME, Drive, DriveError, guess_mime  # noqa: E402


# --- фейковый Drive API ------------------------------------------------


class FakeRequest:
    def __init__(self, owner, kind, kwargs, result):
        self.owner = owner
        self.kind = kind
        self.kwargs = kwargs
        self.result = result

    def execute(self):
        self.owner.calls.append((self.kind, self.kwargs))
        return self.result


class FakeFiles:
    def __init__(self, owner):
        self.owner = owner

    def list(self, **kw):
        return FakeRequest(self.owner, "list", kw, self.owner.list_result)

    def create(self, **kw):
        return FakeRequest(self.owner, "create", kw, self.owner.create_result)

    def update(self, **kw):
        return FakeRequest(self.owner, "update", kw, self.owner.update_result)


class FakeService:
    def __init__(self, list_result=None, create_result=None, update_result=None):
        self.calls = []
        self.list_result = list_result or {"files": []}
        self.create_result = create_result or {
            "id": "new-id",
            "name": "f",
            "webViewLink": "https://drive/new-id",
        }
        self.update_result = update_result or {
            "id": "same-id",
            "name": "f",
            "webViewLink": "https://drive/same-id",
        }

    def files(self):
        return FakeFiles(self)

    def kinds(self):
        return [kind for kind, _ in self.calls]


@pytest.fixture(autouse=True)
def no_media(monkeypatch):
    """MediaFileUpload заменён заглушкой — googleapiclient не нужен."""

    class FakeMedia:
        def __init__(self, path, mimetype=None, resumable=False):
            self.path = path
            self.mimetype = mimetype

    module = type(sys)("googleapiclient.http")
    module.MediaFileUpload = FakeMedia
    pkg = sys.modules.get("googleapiclient") or type(sys)("googleapiclient")
    monkeypatch.setitem(sys.modules, "googleapiclient", pkg)
    monkeypatch.setitem(sys.modules, "googleapiclient.http", module)
    yield


def make_drive(service, tmp_path):
    drive = Drive(token_path=tmp_path / "token.json", client_path=tmp_path / "cl.json")
    drive._service = service
    return drive


# --- is_configured -----------------------------------------------------


def test_is_configured_false_on_missing_path(tmp_path):
    assert Drive(token_path=tmp_path / "nope.json").is_configured() is False


def test_is_configured_false_on_broken_json(tmp_path):
    p = tmp_path / "token.json"
    p.write_text("{not json", encoding="utf-8")
    assert Drive(token_path=p).is_configured() is False


def test_is_configured_true(tmp_path):
    p = tmp_path / "token.json"
    p.write_text(json.dumps({"refresh_token": "x"}), encoding="utf-8")
    assert Drive(token_path=p).is_configured() is True


def test_credentials_without_token_raises_hint(tmp_path):
    drive = Drive(token_path=tmp_path / "nope.json")
    with pytest.raises(DriveError) as exc:
        drive._credentials()
    assert "--auth" in str(exc.value)


# --- sheet_url / mime --------------------------------------------------


def test_sheet_url():
    assert Drive.sheet_url("abc123") == "https://docs.google.com/spreadsheets/d/abc123/edit"


def test_guess_mime_xlsx(tmp_path):
    assert guess_mime(tmp_path / "смета.xlsx") == XLSX_MIME
    assert guess_mime(tmp_path / "note.txt") == "text/plain"
    assert guess_mime(tmp_path / "blob.weird") == "application/octet-stream"


# --- ensure_folder -----------------------------------------------------


def test_ensure_folder_existing_does_not_create(tmp_path):
    svc = FakeService(list_result={"files": [{"id": "folder-1", "name": "ESTIMATES"}]})
    drive = make_drive(svc, tmp_path)
    assert drive.ensure_folder("ESTIMATES") == "folder-1"
    assert svc.kinds() == ["list"]
    q = svc.calls[0][1]["q"]
    assert "trashed = false" in q and gdrive.FOLDER_MIME in q


def test_ensure_folder_creates_when_absent(tmp_path):
    svc = FakeService(create_result={"id": "folder-new"})
    drive = make_drive(svc, tmp_path)
    assert drive.ensure_folder("ESTIMATES", parent_id="root-1") == "folder-new"
    assert svc.kinds() == ["list", "create"]
    assert "'root-1' in parents" in svc.calls[0][1]["q"]
    body = svc.calls[1][1]["body"]
    assert body["mimeType"] == gdrive.FOLDER_MIME
    assert body["parents"] == ["root-1"]


def test_ensure_folder_escapes_quotes(tmp_path):
    svc = FakeService(list_result={"files": [{"id": "x"}]})
    make_drive(svc, tmp_path).ensure_folder("Kris's ESTIMATES")
    assert "\\'" in svc.calls[0][1]["q"]


# --- upload / update ---------------------------------------------------


def test_upload_xlsx_sets_mime_and_fields(tmp_path):
    f = tmp_path / "смета.xlsx"
    f.write_bytes(b"xx")
    svc = FakeService()
    res = make_drive(svc, tmp_path).upload(f, "folder-1")
    kind, kw = svc.calls[0]
    assert kind == "create"
    assert kw["media_body"].mimetype == XLSX_MIME
    assert "webViewLink" in kw["fields"]
    assert kw["body"] == {"name": "смета.xlsx", "parents": ["folder-1"]}
    assert res["webViewLink"] == "https://drive/new-id"


def test_upload_missing_file_raises(tmp_path):
    with pytest.raises(DriveError):
        make_drive(FakeService(), tmp_path).upload(tmp_path / "no.xlsx", "folder-1")


def test_update_version_keeps_file_id(tmp_path):
    f = tmp_path / "смета.xlsx"
    f.write_bytes(b"xx")
    svc = FakeService()
    res = make_drive(svc, tmp_path).update_version("same-id", f)
    kind, kw = svc.calls[0]
    assert kind == "update"
    assert kw["fileId"] == "same-id"
    assert kw["media_body"].mimetype == XLSX_MIME
    assert "webViewLink" in kw["fields"]
    assert res["id"] == "same-id"


# --- ретраи ------------------------------------------------------------


class FakeHttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = type("R", (), {"status": status})()


def test_retry_recovers_after_503(monkeypatch):
    monkeypatch.setattr(gdrive.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise FakeHttpError(503)
        return "ok"

    assert gdrive._retry(flaky, "тест") == "ok"
    assert calls["n"] == 3


def test_retry_gives_up_after_three(monkeypatch):
    monkeypatch.setattr(gdrive.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def always():
        calls["n"] += 1
        raise FakeHttpError(429)

    with pytest.raises(DriveError):
        gdrive._retry(always, "тест")
    assert calls["n"] == 3


def test_retry_does_not_retry_404(monkeypatch):
    monkeypatch.setattr(gdrive.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def missing():
        calls["n"] += 1
        raise FakeHttpError(404)

    with pytest.raises(FakeHttpError):
        gdrive._retry(missing, "тест")
    assert calls["n"] == 1


# --- from_env ----------------------------------------------------------


def test_from_env_reads_paths(monkeypatch):
    monkeypatch.setenv("KRIS_GOOGLE_TOKEN", "/tmp/t.json")
    monkeypatch.setenv("KRIS_GOOGLE_CLIENT", "/tmp/c.json")
    drive = Drive.from_env()
    assert drive.token_path == Path("/tmp/t.json")
    assert drive.client_path == Path("/tmp/c.json")


def test_from_env_defaults(monkeypatch):
    monkeypatch.delenv("KRIS_GOOGLE_TOKEN", raising=False)
    monkeypatch.delenv("KRIS_GOOGLE_CLIENT", raising=False)
    drive = Drive.from_env()
    assert str(drive.token_path) == gdrive.DEFAULT_TOKEN
    assert str(drive.client_path) == gdrive.DEFAULT_CLIENT


# --- авторизация: local server vs OOB ----------------------------------


@pytest.fixture
def fake_flow_module(monkeypatch):
    """Подменяет google_auth_oauthlib.flow.InstalledAppFlow заглушкой."""
    state = {"local_calls": [], "raise_on_auth_url": None}

    class FakeCreds:
        def to_json(self):
            return json.dumps({"refresh_token": "local-token"})

    class FakeFlow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes, redirect_uri=None):
            flow = cls()
            flow.redirect_uri = redirect_uri
            return flow

        def run_local_server(self, **kw):
            state["local_calls"].append(kw)
            return FakeCreds()

        def authorization_url(self, **kw):
            if state["raise_on_auth_url"]:
                raise state["raise_on_auth_url"]
            return ("https://accounts.google.com/o/oauth2/auth?x=1", "state")

        def fetch_token(self, code=None):  # pragma: no cover — не доходим
            raise AssertionError("не должно вызываться в этих тестах")

    module = type(sys)("google_auth_oauthlib.flow")
    module.InstalledAppFlow = FakeFlow
    pkg = sys.modules.get("google_auth_oauthlib") or type(sys)("google_auth_oauthlib")
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", pkg)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", module)
    return state


def _cli_drive(monkeypatch, tmp_path, service=None):
    """Drive.from_env → подставной Drive с путями в tmp и фейковым service."""
    drive = Drive(token_path=tmp_path / "token.json", client_path=tmp_path / "cl.json")
    drive.client_path.write_text("{}", encoding="utf-8")
    drive._service = service or FakeService()
    monkeypatch.setattr(gdrive.Drive, "from_env", classmethod(lambda cls: drive))
    monkeypatch.setattr(gdrive.Drive, "whoami", lambda self: {"emailAddress": "admin@portalcg.xyz"})
    return drive


def test_auth_local_uses_run_local_server(monkeypatch, tmp_path, fake_flow_module):
    drive = _cli_drive(monkeypatch, tmp_path)
    assert gdrive.main(["--auth", "--local"]) == 0
    assert len(fake_flow_module["local_calls"]) == 1
    kw = fake_flow_module["local_calls"][0]
    assert kw["port"] == 0 and kw["open_browser"] is True
    assert json.loads(drive.token_path.read_text(encoding="utf-8"))["refresh_token"] == "local-token"
    assert oct(drive.token_path.stat().st_mode)[-3:] == "600"


def test_auth_oob_failure_prints_local_hint(monkeypatch, tmp_path, fake_flow_module, capsys):
    _cli_drive(monkeypatch, tmp_path)
    fake_flow_module["raise_on_auth_url"] = ValueError(
        "invalid_request: redirect_uri urn:ietf:wg:oauth:2.0:oob is not supported"
    )
    assert gdrive.main(["--auth"]) == 2
    err = capsys.readouterr().err
    assert "--auth --local" in err
    assert not fake_flow_module["local_calls"]
