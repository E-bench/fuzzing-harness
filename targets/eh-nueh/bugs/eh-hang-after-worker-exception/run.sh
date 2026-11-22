#!/usr/bin/env bash
# usage: EH=/path/to/ExpansionHunter-v5.0.0 NUEH=/path/to/NuExpansionHunter ./run.sh
# The original does not return on this input; it is stopped after 30 seconds (exit status 124).
cd "$(dirname "$0")"; rm -rf out; mkdir -p out/eh out/nueh
for t in 1 4; do
  timeout 30 "$EH" --reads in/reads.bam --reference in/ref.fa --variant-catalog in/catalog.json --output-prefix out/eh/r \
          --analysis-mode streaming --aligner path-aligner --threads $t > out/eh.log 2>&1; echo "eh --threads $t exit=$?"
  timeout 30 "$NUEH" --reads in/reads.bam --reference in/ref.fa --variant-catalog in/catalog.json --output-prefix out/nueh/r \
          --analysis-mode streaming --aligner path-aligner --threads $t > out/nueh.log 2>&1; echo "nueh --threads $t exit=$?"
done
tail -1 out/eh.log | cut -c1-200; tail -1 out/nueh.log | cut -c1-200
