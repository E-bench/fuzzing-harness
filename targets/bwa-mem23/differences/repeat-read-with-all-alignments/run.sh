#!/usr/bin/env bash
# usage: MEM2=/path/to/bwa-mem2 MEM3=/path/to/bwa-mem3 ./run.sh
# -a: report all alignments. The rewrite aligns the read the original leaves unmapped, so it lists many more records.
cd "$(dirname "$0")"; rm -f ref.fa.*
"$MEM2" index ref.fa
"$MEM2" mem -a -t 1 -K 100000000 ref.fa reads.fq 2>/dev/null | grep -v '^@' > mem2.sam
"$MEM3" mem --compat=bwa-mem2 -a -t 1 -K 100000000 ref.fa reads.fq 2>/dev/null | grep -v '^@' > mem3.sam
echo "mem2: $(wc -l < mem2.sam) records, mem3: $(wc -l < mem3.sam) records"
diff mem2.sam mem3.sam | head
