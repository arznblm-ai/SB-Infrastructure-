"""Provider-neutral text-only model calls; domain state and actions stay outside the model."""
import json
import os
import subprocess
from dataclasses import dataclass

@dataclass(frozen=True)
class Result:
    text: str = ''
    error: str | None = None
    diagnostic: str = ''

def command(engine, executable=None, model=None):
    if engine == 'codex':
        cmd = [executable or '/root/.local/bin/codex', '-a', 'never', 'exec',
               '--ignore-user-config', '--disable', 'apps', '--disable', 'shell_tool',
               '--disable', 'unified_exec', '-c', 'web_search="disabled"',
               '--sandbox', 'read-only', '--skip-git-repo-check', '--ephemeral']
        if model:
            cmd += ['--model', model]
        return cmd + ['-']
    if engine == 'claude':
        cmd = [executable or '/root/.local/bin/claude', '-p', '--output-format', 'json',
               '--tools', '', '--no-session-persistence']
        if model:
            cmd += ['--model', model]
        return cmd
    raise ValueError('unsupported engine: ' + str(engine))

def run(prompt, workspace, engine='codex', executable=None, model=None, timeout=1200):
    """Input-only invocation; no tool use, application data writes or provider fallbacks."""
    args = command(engine, executable, model)
    names = ('HOME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'CODEX_HOME', 'XDG_CONFIG_HOME')
    env = {k: os.environ[k] for k in names if k in os.environ}
    if engine == 'claude' and 'CLAUDE_CODE_OAUTH_TOKEN' in os.environ:
        env['CLAUDE_CODE_OAUTH_TOKEN'] = os.environ['CLAUDE_CODE_OAUTH_TOKEN']
    try:
        proc = subprocess.run(args, cwd=workspace, input=prompt, text=True,
                              capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return Result(error='timeout')
    except OSError as exc:
        return Result(error='unavailable', diagnostic=type(exc).__name__)
    if proc.returncode:
        return Result(error='provider_error', diagnostic='exit=%d' % proc.returncode)
    if engine == 'claude':
        try:
            obj = json.loads(proc.stdout)
            if obj.get('is_error'):
                return Result(error='provider_error')
            return Result(text=str(obj.get('result') or '').strip())
        except (ValueError, AttributeError):
            return Result(error='invalid_response')
    text = proc.stdout.strip()
    return Result(text=text) if text else Result(error='empty')
