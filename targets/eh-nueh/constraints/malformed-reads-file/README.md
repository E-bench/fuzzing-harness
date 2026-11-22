# Input constraint: the reads file is valid SAM or BAM

The header declares the contigs its records use (`@SQ` lines in SAM text), read names have at most 254
characters (the limit of both formats), and a record's CIGAR covers exactly its sequence.

**Why it is a constraint and not a finding.** On such a file ExpansionHunter v5.0.0 does not behave in one way: the
outcome depends on its own thread count.

```
EH=... NUEH=... ./run.sh          (in/reads.sam: a header with @HD only, then records on chr1)
eh --threads 1 -> exit=0
eh --threads 2 -> exit=1          [E::sam_parse1] no SQ lines present in the header / Failed to extract a record
eh --threads 8 -> exit=1
nueh --threads 1 -> exit=0
nueh --threads 8 -> exit=1
nueh --threads 8 --decode-threads 0 -> exit=0
nueh --threads 1 --decode-threads 4 -> exit=1
```

The first record of the file is unmapped, and both programs stop streaming at the first unmapped record, before the
line that htslib cannot parse. With a decompression thread pool htslib 1.10.2 parses ahead and reports the error on
the first read call. v5.0.0 creates that pool when `--threads` is above 1
(`sample/HtsStreamingSampleAnalysis.cpp`, `htsDecompressionThreads`); NuExpansionHunter creates it from
`--decode-threads`, whose default follows `--threads`. With default options the two programs agree at every thread
count; they differ only when `--decode-threads` is set apart from `--threads`, which is how the fuzzer found it.

A read name of 256 characters has the same effect ("query name too long"), and so has a record whose CIGAR
does not add up to its sequence length in SAM text ("CIGAR and query sequence are of different length"). The
generator wrote one by accident: a soft clip of 10 bases plus one aligned base, `10S1M`, on a read of 10 bases.

**In the generator.** `[adapter] invalid_reads_files = 0` in `target.toml` (the default) keeps the `locus` surface
from writing SAM text without `@SQ` lines, read names over 254 characters and soft clips longer than the read. The raw `sam` and `bam` surfaces still
mutate the file freely; they do not pass `--decode-threads`, so the two programs read with the same pool setting.

## A crash both programs share on such files

On a compressed file whose header htslib reads as SAM text and cannot parse (`Header includes @SQ line ... with no
LN: tag`), both programs die with SIGSEGV (seen with `--threads 4`). The stack is the same in both: the
destructor of `HtsFileStreamer` calls `hts_close`, and htslib's `sam_state_destroy` (`sam.c:2340`) joins a thread
that was joined before. AddressSanitizer reports it as "Joining already joined thread". 542 inputs of campaign
`main4` (surfaces `sam` and `bam`) end this way in each program, so the comparison shows nothing. The file is outside the
constraint above; the defect is in the htslib version both programs bundle.
