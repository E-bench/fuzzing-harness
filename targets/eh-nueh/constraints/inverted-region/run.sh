#!/usr/bin/env bash
# usage: EH=/path/to/ExpansionHunter-v5.0.0 NUEH=/path/to/NuExpansionHunter ./run.sh
# Runs both programs on the same reads with the TargetRegion written end-start (catalog.json) and start-end
# (catalog_forward.json, control), default options, streaming mode.
cd "$(dirname "$0")"; rm -rf out; mkdir -p out/eh out/nueh
for c in catalog catalog_forward; do
  "$EH" --reads in/reads.bam --reference in/ref.fa --variant-catalog in/$c.json --output-prefix out/eh/$c --analysis-mode streaming > out/eh/$c.log 2>&1; echo "$c eh exit=$?"
  "$NUEH" --reads in/reads.bam --reference in/ref.fa --variant-catalog in/$c.json --output-prefix out/nueh/$c --analysis-mode streaming > out/nueh/$c.log 2>&1; echo "$c nueh exit=$?"
  echo "  eh  : $(grep -v '^#' out/eh/$c.vcf | cut -f 7,10)"
  echo "  nueh: $(grep -v '^#' out/nueh/$c.vcf | cut -f 7,10)"
  for f in vcf json; do cmp -s out/eh/$c.$f out/nueh/$c.$f && echo "  $f: identical" || echo "  $f: DIFFERENT"; done
done
