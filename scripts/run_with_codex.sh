#!/usr/bin/env bash
# Launch the stock analysis system using Codex OAuth token.
# Token is read dynamically from ~/.codex/auth.json on each run.
#
# Usage:
#   ./scripts/run_with_codex.sh                    # default analysis
#   ./scripts/run_with_codex.sh --dry-run           # dry run
#   ./scripts/run_with_codex.sh --stocks 600519     # specific stocks
#   ./scripts/run_with_codex.sh --serve             # web server

set -euo pipefail

CODEX_AUTH="$HOME/.codex/auth.json"

if [[ ! -f "$CODEX_AUTH" ]]; then
  echo "❌ Codex auth not found. Run 'codex login' first."
  exit 1
fi

TOKEN=$(python3 -c "
import json, sys, time
auth = json.load(open('$CODEX_AUTH'))
token = auth.get('tokens', {}).get('access_token', '')
if not token:
    print('❌ No access_token in auth.json', file=sys.stderr)
    sys.exit(1)

# Decode JWT payload to check expiry (no verification needed)
import base64
payload = token.split('.')[1]
payload += '=' * (4 - len(payload) % 4)
claims = json.loads(base64.urlsafe_b64decode(payload))
exp = claims.get('exp', 0)
remaining = exp - time.time()
if remaining < 0:
    print('❌ Token expired. Run \"codex login\" to refresh.', file=sys.stderr)
    sys.exit(1)
days = remaining / 86400
if days < 1:
    print(f'⚠️  Token expires in {remaining/3600:.0f} hours — consider running \"codex login\"', file=sys.stderr)
elif days < 3:
    print(f'⚠️  Token expires in {days:.0f} days', file=sys.stderr)
print(token)
")

export OPENAI_API_KEY="$TOKEN"

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

if [[ -d ".venv" ]]; then
  source .venv/bin/activate
fi

exec python main.py "$@"
