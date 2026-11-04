#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  printf '%s\n' 'Usage: bash Demo/run-demo.sh --udid SIMULATOR_UDID --app /path/to/Telegram.app' 'The full-client offline fixture is not yet available; this command fails without opening accounts.'
  exit 0
fi
udid=''
app=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --udid) [[ $# -ge 2 ]] || { echo 'Missing simulator UDID' >&2; exit 2; }; udid="$2"; shift 2 ;;
    --app) [[ $# -ge 2 ]] || { echo 'Missing app path' >&2; exit 2; }; app="$2"; shift 2 ;;
    *) echo 'Unknown option; use --help' >&2; exit 2 ;;
  esac
done
[[ -n "$udid" && -n "$app" ]] || { echo 'Supply --udid and --app; use --help' >&2; exit 2; }
[[ -d "$app" ]] || { echo 'App bundle does not exist' >&2; exit 2; }
echo 'Blocked: no validated full-client offline fixture or UI scenario is packaged. No simulator or account was changed.' >&2
exit 3
