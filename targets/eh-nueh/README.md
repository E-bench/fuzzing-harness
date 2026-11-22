# eh-nueh: ExpansionHunter v5.0.0 against NuExpansionHunter

| Side | Program | Source | Commit |
|---|---|---|---|
| a `eh` | ExpansionHunter v5.0.0 | https://github.com/Illumina/ExpansionHunter | `9f5db1d` (tag v5.0.0) |
| b `nueh` | NuExpansionHunter | https://github.com/BioCode-Commons/NuExpansionHunter | `96bdca4` (main, 2026-11-21) |

NuExpansionHunter states that for every input on which v5.0.0 has defined behaviour it writes the same VCF and JSON
bytes and a realigned BAM with the same header and the same multiset of decoded records
(`docs/07_Performance.md`). That is what is compared here.

## State

- **Differences.** Two campaigns were worked off with the difference loop (`docs/WORKFLOW.md`): `main2` (20 runs of
  37 minutes, 596 stored differences) and `smoke` (20 runs of 10 minutes, 739). Replayed on the reference builds
  with the three temporary patches and the two input constraints (below), none of `smoke` still differs. Of `main2`,
  11 inputs are still listed, all as a timeout of one program: heavy cases (path-aligner, hundreds of loci) that
  passed the 20-second limit while the machine was loaded. The one that did so in every replay takes 9.4 s in the
  original and 3.2 s in the rewrite on its own, with equal output; the other ten did not repeat. No difference in
  output has been traced to the rewrite on valid input.
- **Coverage** of the seed corpora plus the `smoke` campaign (five fuzzers, four surfaces, 200 CPUs for 10 minutes):

  | Program | Branches, reachable code | Branches, all code of streaming mode | Lines |
  |---|---|---|---|
  | ExpansionHunter v5.0.0 | 2,305 / 2,373 (97.1%) | 2,305 / 3,154 (73.1%) | 75.6% |
  | NuExpansionHunter | 4,750 / 5,107 (93.0%) | 4,750 / 6,378 (74.5%) | 76.9% |

  "Reachable" depends on the list of unreachable code ([`UNREACHABLE.md`](UNREACHABLE.md), which also says how far
  that list can be trusted). The seed corpora alone reach 94.3% and 87.4%; each fuzzer's corpora 95.0-96.4% and
  88.9-92.2%, honggfuzz highest in the rewrite.
- **Next:** another campaign with the present generator and patches (the three patches and the cap on a case's
  read bases are newer than the runs above), then the two loops again. Not covered at all: everything under "Valid
  inputs the generator does not produce".

## What is run

```
ExpansionHunter --reads reads.{bam,sam} --reference ref.fa --variant-catalog catalog.json --output-prefix r
                --analysis-mode streaming [options the adapter adds per case]
```

**Streaming mode only.** The mode is the last element of `[run] argv` in [`target.toml`](target.toml); nothing else
depends on it. (For seeking mode the adapter would also have to write an index for every reads file: it can, see
`bam::bai`, but only does so on request of a case.)

Compared: `r.vcf` (by column and sub-field), `r.json` (by key), `r_realigned.bam` (decoded by the adapter: header
plus sorted records), and the exit state. Not compared: the log; exit codes; anything when both programs fail.

Builds ([`build.sh`](build.sh)): each side's own CMake superbuild for the dependencies (htslib 1.10.2, Boost 1.73.0,
spdlog, abseil; system GCC 11), then `ehunter/` once per flavour. Build fixes are listed at the top of `build.sh`; no
product code is changed. NuExpansionHunter's optional libraries (libdeflate, ISA-L, jemalloc) are off.

## Surfaces

| Surface | The fuzz input is | Reaches |
|---|---|---|
| `locus` | a structured description: genome, catalog, sample, options (below) | routing, alignment, genotyping, output writers, options |
| `catalog` | `catalog.json` (after 2 option bytes); genome and reads from a base case | catalog parsing and validation |
| `sam` | the reads as SAM text; genome and catalog from a base case | record handling, pairing, text reader |
| `bam` | the reads as an uncompressed BAM stream, compressed into BGZF blocks by the adapter | binary record reader, block boundaries |

### `locus`

16 header bytes, 16 bytes per locus (1 to 4 loci), then up to 12 records of 8 bytes for single unusual reads
([`adapter.cpp`](adapter.cpp), `H_*`, `L_*`). Zero is the ordinary case everywhere.

- **Genome.** One contig per locus or all loci on one contig at a chosen spacing (4,000 bp down to 0, so that their
  read-extraction regions overlap); autosome, X or Y; `chr1` or `1` naming; flank lengths from 2,000 down to 0 bp;
  flanks with N runs, soft-masked, with IUPAC codes, or continuing the repeat.
- **Catalog.** 28 structure templates over 32 motifs (1 to 12 bases, some with IUPAC codes), repeats `*` and `+`,
  swaps, optional sequences, literals; `Repeat`, `RareRepeat`, `SmallVariant`, `SMN`; off-target regions; `TargetRegion`,
  `VariantId`, `ErrorRate`, `LikelihoodRatioThreshold`, `MinimalLocusCoverage`; string or array forms; 30 malformed
  variants (missing regions or types, reversed or out-of-range regions, unknown contigs, bad structure syntax,
  duplicate ids, misspelt keys; regions written end-start only with `inverted_regions = 1`); empty catalog; a
  single object instead of an array.
  Optionally 3 to 1,100 further loci without reads on a contig of their own (catalog sizes around the rewrite's batch
  limits of 256 and 1,024 loci; the limit at 4,096 is not reached: v5.0.0 needs seconds for such a catalog).
- **Sample.** Two haplotypes per locus, each with its own repeat sizes (reference ± a delta, or absolute up to 1,000
  copies), swap alleles and optional sequences; impure repeats. Read pairs are sampled from the haplotypes at a
  chosen depth, read length (10 to 3,300) and fragment length (at most 600 pairs and 120,000 read bases per case),
  with substitution, insertion, deletion and N errors and eight quality profiles (20 is the programs' cutoff);
  optionally each fragment draws its own repeat sizes around its haplotype's (stutter). In-repeat pairs placed in off-target regions; noise pairs;
  unmapped pairs at the end or inside the file.
- **Unusual records.** Unpaired, secondary, supplementary, duplicate and QC-fail flags; lone mates; a name seen three
  or four times; mates on another contig, unmapped, or without contig; no sequence; one base; soft clips and spliced
  CIGARs; positions at contig ends and beyond; lower-case, N-only, `=` bases; aux tags.
- **Reads file.** BAM with 65,280-byte blocks down to 1-byte blocks (records spanning blocks), stored or compressed,
  with or without the EOF block; SAM text; coordinate-sorted, shuffled, name-sorted or reversed; ten header variants
  (contig order, extra or missing contigs, lengths, naming convention, no `@SQ`: in SAM text only with
  `invalid_reads_files = 1`); optionally a `.bai` index that is
  correct, empty or stale.
- **Options.** `--sex`, `--aligner`, `--threads` 1/2/4/8/16, `--region-extension-length` (500 to 1500; with
  `extension_range = "any"` also 0, negative and very large values), `--log-level`, `--disable-bamlet-output`.
- **Command-line errors.** Fifteen, one per case: invalid values of `--sex`, `--aligner`, `--threads`,
  `--log-level`, `--region-extension-length`; missing reads, reference, catalog or FASTA index; an unknown option; an
  option given twice; `--help`, `--version`.
- **Options only the rewrite has** (`[adapter] rewrite_options`): `--decode-threads`, `--encode-threads`,
  `--bamlet-queue-mb`, and `--catalog-cache` (prepared by an extra run with `--prepare-catalog-cache`). The original
  runs without them; the outputs must still be equal.
  Invalid uses of these options (`rewrite_option_errors = 1`, off by default) make the programs differ by design and
  are only useful for the rewrite's own error paths.

Seeds ([`make_data.py`](make_data.py)): one hand-made input per template, motif, field, container, option, unusual
record and malformed variant for `locus`; their decoded catalogs and reads for the raw surfaces; dictionaries in
`dict/`.

## Bugs found, and their temporary patches

Each has a directory under [`bugs/`](bugs/) with the cause, a witness against the unpatched programs and `run.sh`.
The patches hide the bugs in the builds that are fuzzed, so that differences behind them show
(`target.toml`, `[side.<x>] patches`).

| Bug | Side | What happens | Patch |
|---|---|---|---|
| [`eh-abort-on-worker-exception`](bugs/eh-abort-on-worker-exception/) | original | an error raised while a locus is set up or genotyped (two `RareRepeat`s in one locus, an invalid `ErrorRate` or `LikelihoodRatioThreshold`, an invalid SMN definition) ends v5.0.0 with SIGABRT; the rewrite reports it and exits 1 | `patches/eh-0001-worker-thread-exceptions.diff` |
| [`eh-genome-mask-out-of-bounds`](bugs/eh-genome-mask-out-of-bounds/) | original | a record whose mate has no contig makes v5.0.0 read its genome mask at index -1: SIGSEGV or arbitrary behaviour; the rewrite guards the query (its documented limit 6) | `patches/eh-0002-genome-mask-negative-contig.diff` |
| [`eh-hang-after-worker-exception`](bugs/eh-hang-after-worker-exception/) | original | after an error in a read-processing worker, v5.0.0 never returns when one read pair is near more loci than `--threads` + 5; the rewrite reports the error and exits 1 | `patches/eh-0003-release-queue-after-exception.diff` |

## Input constraints

Shapes of input for which the original has no defined behaviour, although it does not reject them, and on which the
two programs were seen to differ. Such a difference says nothing about the rewrite's fidelity, and a fuzzer that
keeps producing the shape fills the results with it. Each constraint is recorded under [`constraints/`](constraints/)
with its evidence and the difference it caused, and the generator leaves the shape out unless an `[adapter]` option
switches it back on.

| Constraint | Evidence in v5.0.0 | Outside it | Option (default) |
|---|---|---|---|
| a region is written `start-end` with `start <= end` | `assert(start <= stop)` in `GenomeMask::addRegion`, compiled out in release builds | the programs differ with default options: [`constraints/inverted-region`](constraints/inverted-region/) | `inverted_regions = 0` |
| the reads file is valid SAM or BAM: its header declares the contigs (`@SQ`), read names have at most 254 characters | none in ExpansionHunter itself; htslib 1.10.2 reports the parse error only when it reads ahead with a thread pool, so v5.0.0 exits 0 with `--threads 1` and 1 with `--threads 2` on the same file | the rewrite behaves the same way, keyed on its decode pool; the programs differ only when `--decode-threads` is set apart from `--threads`: [`constraints/malformed-reads-file`](constraints/malformed-reads-file/) | `invalid_reads_files = 0` |

## Valid inputs the generator does not produce

Limits of the generator on inputs that both programs accept. Nothing in this table has been tested; each row says
why the limit is there.

| Limit | Why | Not exercised because of it |
|---|---|---|
| a case has at most 600 read pairs and 120,000 read bases | cost: a case with 506 reads of 1,400 bases took about 10 s in each program, an ordinary case takes under 0.1 s | deep coverage with long reads; the rewrite's paths for many records (batches of 16,384 records, the 2,000,000-byte store of unpaired mates, output over 1 MiB, the indexed range scan's 2 MiB split); its genotype grid, which needs about 2,000 fragments at ten candidate sizes (the per-fragment stutter of the sample is meant to reach it with more candidate sizes and fewer fragments; whether it does is in the coverage report) |
| reads are at most 3,300 bases | cost | the rewrite's size limits (65,535 motif copies in one alignment, 2^32 cells per locus) |
| at most 4 loci with reads, plus at most 1,100 loci without reads | cost: v5.0.0 needs about 1.4 ms per locus to set up | catalogs of 4,096 loci and more (parallel interval trees) and of 8,193 and more (index cache) in the rewrite |
| `--region-extension-length` between 500 and 1500 | a choice of range (`extension_range`; see "Notes on both programs") | flanks shorter than a read, empty flanks, negative lengths |
| `--threads` is 1, 2, 4, 8 or 16 | a table of five values | other counts; thread counts above the machine's CPUs |
| the reads file is BAM or SAM | the adapter writes these two formats itself (no htslib) | CRAM input |
| the genome is synthetic: up to 6 contigs of a few thousand random bases | cost and control over the sample | real genome content (long low-complexity stretches, segmental duplications), contigs of 2^31 bases and more |
| motifs come from a table of 32, structures from 28 templates | a structured input needs a finite vocabulary; the raw `catalog` surface is not limited this way | other motifs and structures, in combination with a matching sample |
| options only the rewrite has take valid values only | invalid values differ by design (`rewrite_option_errors`) | the rewrite's error paths for these options, tampered catalog caches |
| streaming mode | the configured command line (`[run] argv`) | seeking mode |

## Notes on both programs

- **`--region-extension-length` is never rejected.** v5.0.0 means to accept 500 to 1500 only
  (`io/ParameterLoading.cpp`, `assertValidity`, lines 208-216: "the range of allowed extensions is between 500 and
  1500"), but the condition reads `length < 500 && length > 1500`, which no value satisfies. NuExpansionHunter has
  the same condition (lines 269-270). On a plain case the two programs give the same output at 499, 50, 1, 0, -5,
  1501 and 100000, so this is not a difference between them. The generator stays inside 500-1500 by default
  (`[adapter] extension_range = "documented"`; `"any"` adds 0, 1, 50, 100, 300, -5, 2000 and 10000).

## Exemptions

Rules in `target.toml` mark cases that fall under a limit NuExpansionHunter documents
(`docs/07_Performance.md`, "Identity guarantee and its limits"). Record order and compression of the realigned BAM,
and the unused nibble of odd-length reads, never show up: the adapter compares decoded records as a multiset.

| Tag or pattern | Documented limit |
|---|---|
| `mate-without-contig` | 6: a record whose mate has no contig makes v5.0.0 index its genome mask out of bounds. With patch `eh-0002` in the fuzzed builds these cases no longer differ; the rule matters when the patches are off |
| `rewrite-option-error` | none: an invalid value for an option only the rewrite has; only generated with `rewrite_option_errors = 1` |
| `inverted-region` | none: an input constraint (above), only generated with `inverted_regions = 1` |
