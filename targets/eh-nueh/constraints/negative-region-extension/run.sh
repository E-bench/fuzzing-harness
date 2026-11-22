#!/usr/bin/env bash
# usage: EH=/path/to/ExpansionHunter-v5.0.0 NUEH=/path/to/NuExpansionHunter ./run.sh
# Runs both programs on one locus at chr2:0-3 and a reads file without records, with --region-extension-length
# -5 (the region's end falls below 0), -2 and 0 (controls), streaming mode.
cd "$(dirname "$0")"; rm -rf out; mkdir -p out/eh out/nueh
for x in -5 -2 0; do
  "$EH" --reads in/reads.sam --reference in/ref.fa --variant-catalog in/catalog.json --output-prefix out/eh/r$x --analysis-mode streaming --region-extension-length $x > out/eh/r$x.log 2>&1; echo "extension $x: eh exit=$?"
  "$NUEH" --reads in/reads.sam --reference in/ref.fa --variant-catalog in/catalog.json --output-prefix out/nueh/r$x --analysis-mode streaming --region-extension-length $x > out/nueh/r$x.log 2>&1; echo "extension $x: nueh exit=$?"
  for f in vcf json; do
    if [ ! -e out/eh/r$x.$f ] && [ ! -e out/nueh/r$x.$f ]; then echo "  $f: written by neither"
    elif cmp -s out/eh/r$x.$f out/nueh/r$x.$f; then echo "  $f: identical"; else echo "  $f: DIFFERENT"; fi
  done
done
