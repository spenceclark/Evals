#!/usr/bin/env bash
# P9 on Promptfoo: the slice, k=3, three graders, then p9_results.jsonl from Promptfoo's own output file.
#   tools/promptfoo/run.sh [extra promptfoo eval args, e.g. --filter-pattern elig-01 --repeat 1]
set -u
cd "$(dirname "$0")"
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then # the user's key, read per command; never printed or written
  ANTHROPIC_API_KEY="$(powershell.exe -NoProfile -Command \
    "[Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY','User')" | tr -d '\r\n')"
  export ANTHROPIC_API_KEY
fi
# Promptfoo phones home by default: telemetry, update check, sharing, remote generation. All off; state stays here.
export PROMPTFOO_DISABLE_TELEMETRY=1 PROMPTFOO_DISABLE_UPDATE=1 PROMPTFOO_DISABLE_SHARING=1 \
  PROMPTFOO_DISABLE_REMOTE_GENERATION=1 PROMPTFOO_CONFIG_DIR="$PWD/.promptfoo"
# The test generator and the python assertion don't read the provider's pythonExecutable.
export PROMPTFOO_PYTHON="$PWD/.venv/Scripts/python.exe"
# Vessel, if it is recording, captures the agent's calls (supportdesk reads VESSEL_URL; session p9-promptfoo). Not the
# judge's: through any base URL but api.anthropic.com, Promptfoo 0.125.1 sends temperature: 0 to claude-haiku-5-5, which
# 400s (see P9.md, "Friction"). Set PF_JUDGE_LOG=1 to have the Anthropic JS SDK log the judge's requests instead.
if curl -s --max-time 2 http://127.0.0.1:4550/vessel/api/status | grep -q '"recording": *true'; then
  export VESSEL_URL=http://127.0.0.1:4550
fi
[ -z "${PF_JUDGE_LOG:-}" ] || export ANTHROPIC_LOG=debug
mkdir -p out
./node_modules/.bin/promptfoo eval -c p9.yaml --no-share --no-cache -o out/eval.json "$@"
code=$? # 100 when any assertion failed: expected, the rubric fails some replies
[ $code -eq 0 ] || [ $code -eq 100 ] || exit $code
.venv/Scripts/python.exe p9_export.py out/eval.json p9_results.jsonl
