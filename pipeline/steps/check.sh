#!/usr/bin/env bash
# Check a campaign's outputs (runs, differences, coverage, report):  DFZ_TARGET=<target> TAG=<campaign> pipeline/steps/check.sh
source "$(dirname "$0")/../env.sh"
python3 -I "$DFZ_ROOT/pipeline/tools/check.py" "$T" "${TAG:?}" "$TARGET_DIR/target.toml" "$DFZ_ROOT/pipeline/engines"
