# ExpansionHunter v5.0.0 reads out of bounds when a record's mate has no contig

**Side at fault:** the original. NuExpansionHunter `96bdca4` guards the same query and lists the case as limit 6 of
its identity guarantee (`docs/07_Performance.md`, "Negative contig indices").

**What differs.** v5.0.0 crashes with SIGSEGV, or runs on with whatever the stray read returned; NuExpansionHunter
processes the file. Because the read is out of bounds, v5.0.0's behaviour varies from run to run.

**Trigger** (this directory, default options): one record whose mate contig is not a contig of the file, here
`RNEXT` = `P` in line 11 of `in/reads.sam` (htslib: "urecognized mate reference name; treated as unmapped", mate
contig index -1). `RNEXT` = `*` on a mapped record does the same.

```
EH=/path/to/ExpansionHunter-v5.0.0 NUEH=/path/to/NuExpansionHunter ./run.sh
eh exit=139     (SIGSEGV)
nueh exit=0
```

With that one field changed to `=`, v5.0.0 exits 0 on the same file.

**Cause.** The streaming loop asks the genome mask about the mate's position before it looks at any flag
(`sample/HtsStreamingSampleAnalysis.cpp`, `targetRegionMask.query(currentMateContigId(), currentMatePosition())`).
`GenomeMask::query` (`sample/GenomeMask.cpp:38-46`) only checks `contigId >= mask_.size()`; with `contigId` -1 it
reads `mask_[-1]`.

**Temporary patch** ([`../../patches/eh-0002-genome-mask-negative-contig.diff`](../../patches/eh-0002-genome-mask-negative-contig.diff)):
`query` returns false for a negative contig index, which is what NuExpansionHunter does. It is applied to the builds
that are fuzzed (`target.toml`, `[side.a] patches`); the witness above is against the unpatched program.

Files: `in/` (reference, catalog, reads as SAM text), `run.sh`, `case.bin` (the fuzz input, surface `sam`).
