#!/bin/sh
set -eu
exec python3 /opt/cfipup2dns/cfip_runner.py "$@"
