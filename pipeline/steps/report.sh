#!/usr/bin/env bash
# The campaign report (fuzzer comparison, difference classes, coverage summary):
#   DFZ_TARGET=<target> TAG=<campaign> pipeline/steps/report.sh
source "$(dirname "$0")/../env.sh"
python3 -I "$DFZ_ROOT/pipeline/tools/report.py" "$T" "${TAG:?}" "$TARGET_DIR/target.toml"
