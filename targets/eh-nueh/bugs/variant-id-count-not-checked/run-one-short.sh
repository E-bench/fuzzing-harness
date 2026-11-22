#!/usr/bin/env bash
# usage: EH=/path/to/ExpansionHunter-v5.0.0 NUEH=/path/to/NuExpansionHunter ./run-one-short.sh
# One VariantId too few (4 variants with 3 ids, 6 with 5) and the complete lists as controls, on a reads file without
# records: the exit code and the variant ids each program writes.
cd "$(dirname "$0")"; rm -rf out-one-short; mkdir -p out-one-short
for c in 4v_4ids 4v_3ids 6v_6ids 6v_5ids; do n=${c%%v_*}
  for p in eh nueh; do [ $p = eh ] && b=$EH || b=$NUEH
    "$b" --reads in-one-short/reads$n.sam --reference in-one-short/ref$n.fa --variant-catalog in-one-short/cat_$c.json \
         --output-prefix out-one-short/${p}_$c --analysis-mode streaming > out-one-short/${p}_$c.log 2>&1
    echo "$c $p exit=$? VARID: $(grep -v '^#' out-one-short/${p}_$c.vcf 2>/dev/null | sed 's/.*VARID=\([^;]*\);.*/[\1]/' | tr '\n' ' ')"
  done
done
