#!/usr/bin/env bash
# usage: MEM2=/path/to/bwa-mem2 MEM3=/path/to/bwa-mem3 ./run.sh
# The reference index must be built by bwa-mem2 v2.2.1 (bwa-mem3's index is not readable by it).
cd "$(dirname "$0")"; rm -f ref.fa.*
"$MEM2" index ref.fa
"$MEM2" mem -t 1 -K 100000000 ref.fa reads.fq 2>/dev/null | grep -v '^@' > mem2.sam
"$MEM3" mem --compat=bwa-mem2 -t 1 -K 100000000 ref.fa reads.fq 2>/dev/null | grep -v '^@' > mem3.sam
diff mem2.sam mem3.sam && echo IDENTICAL || echo DIFFERENT
