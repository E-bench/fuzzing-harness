# Input constraint: the reads file is valid SAM or BAM

The header declares the contigs its records use (`@SQ` lines in SAM text), and read names have at most 254
characters (the limit of both formats).

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

A read name of 256 characters has the same effect ("query name too long").

**In the generator.** `[adapter] invalid_reads_files = 0` in `target.toml` (the default) keeps the `locus` surface
from writing SAM text without `@SQ` lines and read names over 254 characters. The raw `sam` and `bam` surfaces still
mutate the file freely; they do not pass `--decode-threads`, so the two programs read with the same pool setting.
