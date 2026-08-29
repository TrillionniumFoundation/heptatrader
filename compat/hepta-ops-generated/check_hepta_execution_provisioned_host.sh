#!/bin/sh
set -eu
unset BASH_ENV ENV CDPATH
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
case "$0" in
  */*) HEPTA_SHIM_DIR=${0%/*} ;;
  *) HEPTA_SHIM_DIR=. ;;
esac
CDPATH= cd -- "$HEPTA_SHIM_DIR/../.."
ROOT=$PWD
unset HEPTA_SHIM_DIR
exec /usr/bin/python3 "$ROOT/scripts/hepta_ops.py" --root "$ROOT" run --compat-wrapper check_hepta_execution_provisioned_host.sh execution.host.check -- "$@"
