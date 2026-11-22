#!/usr/bin/env bash
# usage: EH=/path/to/ExpansionHunter-v5.0.0 NUEH=/path/to/NuExpansionHunter ./run.sh
# The same SAM file (no @SQ lines in its header) at different thread settings.
cd "$(dirname "$0")"; rm -rf out; mkdir -p out
run() { local name=$1 bin=$2; shift 2
  "$bin" --reads in/reads.sam --reference in/ref.fa --variant-catalog in/catalog.json --output-prefix out/r \
         --analysis-mode streaming "$@" > out/log 2>&1; echo "$name $* -> exit=$?"; }
run eh "$EH" --threads 1; run eh "$EH" --threads 2; run eh "$EH" --threads 8
run nueh "$NUEH" --threads 1; run nueh "$NUEH" --threads 8
run nueh "$NUEH" --threads 8 --decode-threads 0; run nueh "$NUEH" --threads 1 --decode-threads 4
