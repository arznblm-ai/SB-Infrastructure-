#!/usr/bin/env bash
# Install the provider-neutral task review; preserve timer, audit and data.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
remote='root@163.5.29.10'
key="$HOME/.ssh/id_ed25519_vps"
python3 -m py_compile "$script_dir/task_review.py" "$script_dir/model_engine.py"
(cd "$script_dir" && python3 -m pytest test_model_engine.py test_task_review.py -q)
ssh -i "$key" -o BatchMode=yes "$remote" 'mkdir -p /opt/asya/.staging-review && chmod 700 /opt/asya/.staging-review'
rsync -a -e "ssh -i $key -o BatchMode=yes" "$script_dir/task_review.py" "$script_dir/model_engine.py" "$remote:/opt/asya/.staging-review/"
ssh -i "$key" -o BatchMode=yes "$remote" 'set -eu; /root/tools/todoist-mcp/venv/bin/python -m py_compile /opt/asya/.staging-review/task_review.py /opt/asya/.staging-review/model_engine.py; test -e /opt/asya/task_review.py.bak-pre-provider-migration || cp -p /opt/asya/task_review.py /opt/asya/task_review.py.bak-pre-provider-migration; install -m 0644 /opt/asya/.staging-review/model_engine.py /opt/asya/model_engine.py; install -m 0644 /opt/asya/.staging-review/task_review.py /opt/asya/task_review.py; systemctl is-active asya-task-review.timer'
