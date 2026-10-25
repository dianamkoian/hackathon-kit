#!/usr/bin/env bash
# Everything that must work before you call the repo ready. Run it at hour 7:30.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== версия Python =="
python3 -c "
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f'нужен Python 3.10+, а здесь {sys.version.split()[0]}')
print('ok:', sys.version.split()[0])
"

echo
echo "== eval (mock backend) =="
python3 -m evals.run

echo
echo "== api imports and serves =="
python3 -c "
from fastapi.testclient import TestClient
from api.server import app
c = TestClient(app)
assert c.get('/healthz').json()['ok'], 'healthz failed'
samples = c.get('/samples').json()
assert samples, 'no samples'
body = c.post('/run', json={'text': samples[0]['text']}).json()
assert body['trace']['steps'], 'empty trace'
assert c.get('/').status_code == 200, 'demo page not served'
print('api ok:', body['status'], '|', len(body['trace']['steps']), 'trace steps')
"

echo
echo "== числа в README совпадают с прогоном =="
python3 scripts/check_readme_numbers.py

echo
echo "== no secrets committed =="
# Exclude this file: it contains the pattern it searches for, so without the
# exclusion the scanner always finds itself and reports a secret that is not there.
if git log -p --all -- ':(exclude)scripts/smoke.sh' 2>/dev/null \
   | grep -nE '(ANTHROPIC_API_KEY=.+|sk-ant-[A-Za-z0-9]|gh[pousr]_[A-Za-z0-9]{20,})' >/dev/null; then
  echo "FAIL: something key-shaped is in the git history. Rotate it, do not just delete it."
  exit 1
fi
echo "ok: nothing key-shaped in history"

echo
echo "== local models =="
python3 scripts/prefetch.py || true

echo
echo "smoke passed"
