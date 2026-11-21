import subprocess
from types import SimpleNamespace
import model_engine as engine

def test_codex_disables_shell_apps_and_inherits_no_config():
    cmd = engine.command('codex')
    assert '--ignore-user-config' in cmd and '--ephemeral' in cmd
    assert cmd[cmd.index('--sandbox') + 1] == 'read-only'
    assert 'shell_tool' in cmd and 'unified_exec' in cmd and 'apps' in cmd
    assert 'web_search="disabled"' in cmd
    assert cmd[-1] == '-'

def test_provider_response_and_failure_are_separate(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='ok', stderr=''))
    assert engine.run('prompt', str(tmp_path)).text == 'ok'
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout='', stderr='secret'))
    result = engine.run('prompt', str(tmp_path))
    assert result.error == 'provider_error' and 'secret' not in result.diagnostic

def test_codex_does_not_receive_unrelated_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN', 'secret')
    def fake(*args, **kwargs):
        assert 'CLAUDE_CODE_OAUTH_TOKEN' not in kwargs['env']
        return SimpleNamespace(returncode=0, stdout='ok', stderr='')
    monkeypatch.setattr(subprocess, 'run', fake)
    assert engine.run('prompt', str(tmp_path)).text == 'ok'
