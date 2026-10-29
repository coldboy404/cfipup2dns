#!/bin/sh
# Only remove installations owned by the new native installer. Data kept by default.
set -eu
case "${1:-}" in
  -h|--help) printf '%s\n' 'Usage: bash uninstall.sh [--purge] (native only; keeps data by default)'; exit 0 ;;
  ''|--purge) ;;
  *) printf '%s\n' 'Unknown option; use --help' >&2; exit 2 ;;
esac
manager="${CFIP_INSTALL_ROOT:-}/opt/cfipup2dns/current/native/manage.py"
if [ ! -f "$manager" ]; then
  printf '%s\n' 'No managed native installation found; nothing removed.' >&2
  exit 1
fi
exec python3 "$manager" uninstall "$@"
