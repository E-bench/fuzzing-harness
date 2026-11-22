#!/usr/bin/env bash
# usage: EH=/path/to/eh NUEH=/path/to/nueh ./run.sh   (writes out/eh/ and out/nueh/)
cd "$(dirname "$0")"; rm -rf out; mkdir -p out/eh out/nueh
"$EH" '--reads' 'in/reads.bam' '--reference' 'in/ref.fa' '--variant-catalog' 'in/catalog.json' '--output-prefix' 'out/eh/r' '--analysis-mode' 'streaming' '--sex' 'male' '--aligner' 'path-aligner' '--threads' '6' '--region-extension-length' '0' '--log-level' 'trace' '--disable-bamlet-output' > out/eh.log 2>&1; echo "eh exit=$?"
"$NUEH" '--reads' 'in/reads.bam' '--reference' 'in/ref.fa' '--variant-catalog' 'in/catalog.json' '--output-prefix' 'out/nueh/r' '--analysis-mode' 'streaming' '--sex' 'male' '--aligner' 'path-aligner' '--threads' '6' '--region-extension-length' '0' '--log-level' 'trace' '--disable-bamlet-output' '--catalog-cache' 'out/nueh/catalog.cache' > out/nueh.log 2>&1; echo "nueh exit=$?"
for f in r.vcf r.json r_realigned.bam; do
  if [ ! -e out/eh/$f ] && [ ! -e out/nueh/$f ]; then echo "$f: written by neither"
  elif cmp -s out/eh/$f out/nueh/$f; then echo "$f: identical"; else echo "$f: DIFFERENT"; fi
done
