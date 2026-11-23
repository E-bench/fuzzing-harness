# eh-nueh: ExpansionHunter v5.0.0 against NuExpansionHunter

| Side | Program | Source | Commit |
|---|---|---|---|
| a `eh` | ExpansionHunter v5.0.0 | https://github.com/Illumina/ExpansionHunter | `9f5db1d` (tag v5.0.0) |
| b `nueh` | NuExpansionHunter | https://github.com/BioCode-Commons/NuExpansionHunter | `7c738d5` (NuEH-v0.2, 2026-11-21) |

NuExpansionHunter states that for every input on which v5.0.0 has defined behaviour it writes the same VCF and JSON
bytes and a realigned BAM with the same header and the same multiset of decoded records
(`docs/07_Performance.md`). That is what is compared here.

## State

Last campaign: `main5`, five fuzzers on four surfaces, 40 runs of one hour (20 on 10 CPUs, 20 on 15), 509 CPU hours,
25.6 million test cases, 130,104 distinct kept inputs. Its results below are replays on the present builds (six
temporary patches of v5.0.0, three of the rewrite, below) with the present generator.

- **Differences.** The fuzzers stored 671 signatures. On the reference builds **none differs in an output file or
  in how a program ends**, apart from time limits:

  | Stored signatures | |
  |---|---|
  | 208 | no longer differ; 3 of them were a time limit that the rewrite passed while other replays ran, and not when replayed alone |
  | 462 | only v5.0.0 passed the 20-second limit; put off, not examined one by one |
  | 1 | only the rewrite passed the limit in the recheck, also in its replay at the end of the job (48.5 s on the clock and 361 s of CPU, against 3.7 s). Run directly the rewrite ends first in every setting tried: 0.3 to 0.4 s on the clock with 8 threads, the CPU time of v5.0.0 and equal output, in 300 runs on the recheck's machine and on two others. So the recheck's figure is not a property of the program; what in the recheck's job produces it was not found |

  One stored input did differ and became an input constraint: a negative `--region-extension-length`
  ([`constraints/negative-region-extension`](constraints/negative-region-extension/)). The earlier campaigns
  (`main2`, `smoke`, `main3`, `main4`; 596, 739, 68 and 469 stored signatures) ended the same way: no difference in
  output on valid input was traced to the rewrite. What they found is in the three tables below (bugs, input
  constraints, exemptions).
- **Memory errors and undefined behaviour.** 128,383 inputs of `main5` (seeds, corpora, stored inputs) were replayed
  on the sanitizer builds. One kind of report is left, in both programs, on 668 inputs: a crash inside htslib on
  reads files that are not valid ([`constraints/malformed-reads-file`](constraints/malformed-reads-file/)). The
  three defects in the programs' own code that these builds stopped at before (bugs, below) are patched. In code
  that only the rewrite has, the sanitizers reported two things, both on the seeds: a `memcpy` from a null pointer
  (bugs, below) and unaligned access through htslib's helpers (notes, below). 139 runs of v5.0.0 and 25 of the
  rewrite were ended at a limit and show nothing. The replay covers the inputs the fuzzers kept, not all they ran:
  undefined behaviour that depends on a value and not on the path taken can be missed.
- **Coverage** of the seed corpora plus the corpora of `main5`:

  | Program | Branches, all code of streaming mode | Branches, reachable code | Lines |
  |---|---|---|---|
  | ExpansionHunter v5.0.0 | 2,269 / 3,154 (71.9%) | 2,269 / 2,380 (95.3%) | 75.5% |
  | NuExpansionHunter | 4,671 / 6,380 (73.2%) | 4,671 / 5,120 (91.2%) | 77.6% |

  "Reachable" depends on the list of unreachable code ([`UNREACHABLE.md`](UNREACHABLE.md), which also says how far
  that list can be trusted); the first column does not. The seed corpora alone reach 2,225 and 4,482 branches; each
  fuzzer's corpora 2,252 to 2,263 and 4,598 to 4,660 (AFL++ highest on v5.0.0, honggfuzz on the rewrite). Two
  measurements of the same inputs differ by a few branches (up to 7 seen), because some depend on thread timing.
  997 replays of v5.0.0 and 235 of the rewrite passed a limit and add nothing; what those inputs reach is missing.
  **More fuzzing no longer adds branches:** `main4`, with half the CPU hours, reached 2,267 and 4,666. What is left
  is behind inputs the generator does not make ("Valid inputs the generator does not produce") and in the list of
  unreachable code.
- **Cost.** Test cases per CPU second on the `locus` surface, all fuzzers together and AFL++ alone:

  | Campaign | All fuzzers | AFL++ |
  |---|---|---|
  | `main3` | 1.78 | 2.29 |
  | `main4` (larger cases added) | 0.40 | 0.51 |
  | `main5` (the three limits of the generator) | 0.56 | 0.94 |

  On the other surfaces 17 to 19. The heaviest seed takes v5.0.0 5.2 s and 174 MB, the rewrite 0.6 s and 65 MB.
- **Next:** the inputs the generator does not make; the 455 time limits of v5.0.0; once coverage no longer rises,
  one longer campaign that fuzzes on the sanitizer builds.

### NuExpansionHunter v0.2 (`7c738d5`)

One campaign of the full matrix (seven fuzzers, four surfaces, 28 runs of one hour, 20.5 million test cases) on the
rewrite's `NuEH-v0.2` branch, with the patches below; the numbers of `main5` above are for `96bdca4`. **Based on
this fuzzing the two programs agree: no stored input differs in an output file or in how a program ends.** Of 683
signatures, 358 no longer differ, 257 are time limits that only v5.0.0 passed (put off), and the rest are limits
passed under the recheck's load that do not repeat when replayed alone on an idle node.

Two observations are not differences in output, and neither is undefined behaviour of the programs:

- **Resource use.** On a locus whose structure carries a long literal the rewrite peaks at 1,452 MiB against 923
  MiB for v5.0.0, in the same time and with identical `r.vcf` and `r.json`. The input is valid for both, so this is
  a difference in how much memory a run needs, not in what it computes; under the target's 1024 MiB run limit the
  rewrite passes the limit and the original does not, which is how the input was found.
- **An input outside the original's domain.** A short `--region-extension-length` with a locus at a contig start
  gives `chr1:-1-0`, which v5.0.0 refuses with `Invalid region string`; the rewrite, given a prepared catalog cache
  (`--prepare-catalog-cache` with `--catalog-cache`), writes `r.vcf` and `r.json` instead. The original defines no
  behaviour for a region before the contig, so this says nothing about the rewrite's fidelity.

The replay of 143,985 inputs on the sanitizer builds reports the htslib thread error of
[`constraints/malformed-reads-file`](constraints/malformed-reads-file/) on 2,474 of them, in both programs, and
nothing else.

Coverage: branches 2,271 / 2,380 (95.4%) for v5.0.0 and 4,857 / 5,415 (89.7%) for the rewrite; of the files the
branch changes, 2,804 / 3,013 lines (93.1%).

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
  read-extraction regions overlap); autosome, X or Y; `chr1` or `1` naming; flank lengths from 2,000 down to 0 bp, and
  10,000 together with a region extension of 10,000 (flank nodes beyond the range of 16-bit alignment scores);
  flanks with N runs, soft-masked, with IUPAC codes, or continuing the repeat; flanks that are a tandem of one
  37- or 41-base unit (few distinct k-mers), and a right flank that repeats the left one (a duplication).
- **Catalog.** 28 structure templates over 32 motifs (1 to 12 bases, some with IUPAC codes), repeats `*` and `+`,
  swaps, optional sequences, literals of 1 to 26 bases and one of 2,100 (a graph node longer than the rewrite's
  compact k-mer index holds); the first locus's structure 4, 16 or 32 times over (up to 63 repeats in
  one locus, a graph of more than 64 nodes; optional sequences and swaps only in the first copy); `Repeat`, `RareRepeat`, `SmallVariant`, `SMN`; off-target regions; `TargetRegion`,
  `VariantId`, `ErrorRate`, `LikelihoodRatioThreshold`, `MinimalLocusCoverage`; string or array forms; 30 malformed
  variants (missing regions or types, reversed or out-of-range regions, unknown contigs, bad structure syntax,
  duplicate ids, misspelt keys; regions written end-start only with `inverted_regions = 1`); empty catalog; a
  single object instead of an array.
  Optionally 3 to 1,100 further loci without reads on a contig of their own, or twice as many (up to 2,200):
  catalog sizes around the rewrite's limits of 256 and 1,024 loci.
- **Sample.** Two haplotypes per locus, each with its own repeat sizes (reference ± a delta, or absolute up to 1,000
  copies), swap alleles and optional sequences; impure repeats. Read pairs are sampled from the haplotypes at a
  chosen depth, read length (10 to 3,300) and fragment length. A case has at most 600 pairs and 120,000 read bases
  by default; the input can choose a sample 4, 16 or 32 times as deep (up to 19,200 pairs),
  with substitution, insertion, deletion and N errors and eight quality profiles (20 is the programs' cutoff);
  optionally each fragment draws its own repeat sizes around its haplotype's (stutter). In-repeat pairs placed in off-target regions; noise pairs;
  unmapped pairs at the end or inside the file.
- **Unusual records** (up to 12 per case). Unpaired, secondary, supplementary, duplicate and QC-fail flags; lone mates; a name seen three
  or four times; mates on another contig, unmapped, or without contig; no sequence; one base; soft clips and spliced
  CIGARs; positions at contig ends and beyond; lower-case, N-only, `=` bases; aux tags.
- **Reads file.** BAM with 65,280-byte blocks down to 1-byte blocks (records spanning blocks), stored or compressed,
  with or without the EOF block; SAM text; coordinate-sorted, shuffled, name-sorted or reversed, or every pair's first record before all the partners; ten header variants
  (contig order, extra or missing contigs, lengths, naming convention, no `@SQ`: in SAM text only with
  `invalid_reads_files = 1`); optionally a `.bai` index that is
  correct, empty or stale.
- **Options.** `--sex`, `--aligner`, `--threads` (1 to 8, 12, 16, 32, 64), `--region-extension-length` (inside
  the documented range of 500 to 1500 and outside it: 0, 1, 5, 50, 100, 300, 2000, 10000; -5 only with
  `negative_extension = 1`), `--log-level`,
  `--disable-bamlet-output`.
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

Each has a directory under [`bugs/`](bugs/) with the cause and a witness against the unpatched programs.
The patches hide the bugs in the builds that are fuzzed, so that differences behind them show
(`target.toml`, `[side.<x>] patches`).

| Bug | Side | What happens | Patch |
|---|---|---|---|
| [`eh-abort-on-worker-exception`](bugs/eh-abort-on-worker-exception/) | original | an error raised while a locus is set up or genotyped (two `RareRepeat`s in one locus, an invalid `ErrorRate` or `LikelihoodRatioThreshold`, an invalid SMN definition) ends v5.0.0 with SIGABRT; the rewrite reports it and exits 1 | `patches/eh-0001-worker-thread-exceptions.diff` |
| [`eh-genome-mask-out-of-bounds`](bugs/eh-genome-mask-out-of-bounds/) | original | a record whose mate has no contig makes v5.0.0 read its genome mask at index -1: SIGSEGV or arbitrary behaviour; the rewrite guards the query (its documented limit 6) | `patches/eh-0002-genome-mask-negative-contig.diff` |
| [`nueh-memcpy-null-on-empty-catalog`](bugs/nueh-memcpy-null-on-empty-catalog/) | rewrite | with an empty catalog the rewrite calls `memcpy` with a null source pointer and length 0: undefined behaviour without a visible effect (found by the sanitizer build; the outputs agree) | `patches/nueh-0001-empty-catalog-memcpy.diff` |
| [`eh-abort-in-bamlet-writer-constructor`](bugs/eh-abort-in-bamlet-writer-constructor/) | original | an error while the alignment writer is set up (a locus at the start of a contig with a short region extension: `Invalid region string: chr1:-1-0`) ends v5.0.0 with SIGABRT, because the writer's thread is already running; the rewrite reports the error and exits 1 | `patches/eh-0004-bamlet-writer-constructor.diff` |
| [`variant-id-count-not-checked`](bugs/variant-id-count-not-checked/) | both (the original's code, unchanged in the rewrite) | a locus with fewer `VariantId` entries than variants makes both programs read beyond the list of ids: an error message, SIGSEGV, or exit 0 with an empty id in VCF and JSON; on one catalog v5.0.0 writes the empty id and the rewrite ends in an error (found by the sanitizer builds) | `patches/eh-0005-variant-id-count.diff`, `patches/nueh-0002-variant-id-count.diff` |
| [`fragment-origins-int-overflow`](bugs/fragment-origins-int-overflow/) | both (the original's expression, unchanged in the rewrite) | the genotype likelihood squares (allele length + fragment length) in an `int`, which overflows from 46,341 bases on: undefined behaviour, in practice a wrong and at first negative count whose logarithm is NaN; the outputs of the two programs agree (found by the sanitizer builds) | `patches/eh-0006-fragment-origins-overflow.diff`, `patches/nueh-0003-fragment-origins-overflow.diff` |
| [`nueh-locus-error-after-reading`](bugs/nueh-locus-error-after-reading/) | rewrite | a locus that both programs reject is reported by v5.0.0 before it reads a record and by the rewrite after it has processed the whole sample (0.01 to 0.06 s against 3.55 s on the witness, 5,879 records); the message and the exit status are the same | none (it would change the path every input takes) |
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
| `--region-extension-length` is not negative | the range check of `io/ParameterLoading.cpp` (500 to 1500), which rejects nothing as written; `assert(start <= stop)` and `assert(start >= 0)` in `GenomeMask::addRegion`, compiled out in release builds | a region whose end falls below 0: v5.0.0 ends with SIGSEGV, the rewrite with `std::bad_alloc` and exit 1: [`constraints/negative-region-extension`](constraints/negative-region-extension/) | `negative_extension = 0` |
| the reads file is valid SAM or BAM: its header declares the contigs (`@SQ`), read names have at most 254 characters, a CIGAR covers exactly its sequence | none in ExpansionHunter itself; htslib 1.10.2 reports the parse error only when it reads ahead with a thread pool, so v5.0.0 exits 0 with `--threads 1` and 1 with `--threads 2` on the same file | the rewrite behaves the same way, keyed on its decode pool; the programs differ only when `--decode-threads` is set apart from `--threads`: [`constraints/malformed-reads-file`](constraints/malformed-reads-file/) | `invalid_reads_files = 0` |

## Valid inputs the generator does not produce

Limits of the generator on inputs that both programs accept. Nothing in this table has been tested; each row says
why the limit is there.

| Limit | Why | Not exercised because of it |
|---|---|---|
| a case has at most 19,200 read pairs and 3,840,000 read bases (32 times the default of 600 and 120,000) | cost: a case of the default size takes about 0.1 s, one with 506 reads of 1,400 bases took about 10 s in each program | whatever needs a deeper sample; the larger sizes are choices of the input (`DEPTH_SCALE` in the adapter), so the fuzzers reach them, but far less often than small cases: each costs seconds |
| reads are at most 3,300 bases | cost: the aligners are quadratic in the read length; the rewrite's size limits lie at 65,535 motif copies in one alignment and 2^32 cells per locus, which a read of some 65,000 bases would need | those size limits |
| at most 4 loci with reads, plus at most 2,200 loci without reads | cost: v5.0.0 needs about 2.3 ms per locus to set up on one thread. Catalogs of 4,400 and 8,800 loci were generated for one campaign: they took v5.0.0 10.7 s and 19.8 s of the 20 s limit on an idle machine, so under load they were not compared at all, and the seeds of these sizes reached no branch that the other seeds do not (seed coverage with and without them: 2,224 branches in v5.0.0 both times, 4,473 against 4,472 to 4,473 in the rewrite, where two runs on the same seeds differ by up to 7) | larger catalogs, among them the rewrite's limits at 4,096 and 8,193 loci |
| `--threads` is one of 1 to 8, 12, 16, 32, 64 | a table | other counts |
| with `--aligner path-aligner` a structure is at most 4 times its template (16 and 32 times with the default aligner) | cost: the path aligner's time grows steeply with the number of repeats in a structure, in both programs alike. One case (a 12-base motif, 9,600 read pairs of which few reach the locus): 0.02 s at 1 time, 5.7 s and 3.6 s (v5.0.0, rewrite) at 4 times, more than 120 s in both at 16 and at 32 times; at 32 times with 600 pairs, 50 s in both. With the default aligner the 32-fold case takes 0.16 s and 0.04 s | the path aligner on loci with many repeats |
| a structure has at most 3 literals that are the degenerate base `N` (further ones are written as `A`) | cost: both programs need time and memory exponential in their number, like for the optional sequences below. Measured on one locus with default reads, `N` between the repeats: 3 literals, at most 0.6 s and 91 MB; 7 literals, 7 to 35 s and 0.7 to 3.9 GB in v5.0.0 and 5.6 to 21 s and 1.2 to more than 6 GB in the rewrite; 15 literals, both above 6 GB after 30 s. Other literals between up to 64 repeats: at most 4 s and 130 MB | loci with many degenerate bases outside their repeats |
| a structure has at most 3 optional sequences and swaps, also when it is a multiple of its template | cost: both programs need time and memory exponential in their number. With 32 optional sequences in one locus v5.0.0 was killed at 50 GB after 270 s while it set up the locus, and the rewrite had 30 GB and no result after 120 s | loci with many small variants |
| the reads file is BAM or SAM | the adapter writes these two formats itself (no htslib) | CRAM input |
| the genome is synthetic: up to 6 contigs of a few thousand bases, random or with tandem or duplicated flanks | cost and control over the sample | real genome content at scale, contigs of 2^31 bases and more |
| motifs come from a table of 32, structures from 28 templates | a structured input needs a finite vocabulary; the raw `catalog` surface is not limited this way | other motifs and structures, in combination with a matching sample |
| options only the rewrite has take valid values only | invalid values differ by design (`rewrite_option_errors`) | the rewrite's error paths for these options, tampered catalog caches |
| streaming mode | the configured command line (`[run] argv`) | seeking mode |

Not a limit of the generator, but of the machines: the rewrite chooses its SIMD kernels by CPU. On the machines
used (all with AVX2, some with AVX-512BW) the AVX2 kernels, the AVX-512 insertion scan and the SSE2 base-run kernel
run and are covered; the SSE2 tier of the DP row passes and the scalar fallbacks never run. They need a CPU without
AVX2, or one of the rewrite's verification builds (`EH_VERIFY=SIMD=SCALAR`, `SSE42`), which are other builds than
the one tested here.

## Notes on both programs

- **Unaligned integer access through htslib.** The sanitizer builds leave out the alignment check
  (`[build] san_flags`). With it, about 800 of 1,100 seed inputs stop in the rewrite at a 32-bit load or store at an
  unaligned address: `io/BamletWriter.cpp:731` (`u32_to_le`), `sample/HtsFileStreamer.cpp:122`, `:131`, `:182`
  (`le_to_u32`). These are htslib's helpers (`hts_endian.h`), which on x86 access the bytes through a type that
  htslib declares as unaligned (`typedef uint32_t uint32_u __attribute__((__aligned__(1)))`). GCC, the compiler
  both projects document, honours that declaration, so the access is defined there; clang's sanitizer reports it
  all the same ("requires 4 byte alignment"). No effect on x86 has been seen or is expected. v5.0.0 does not call
  these helpers from its own code. The check is left out because a build stops at its first report, and this one
  would hide everything behind it.
- **Leaks are not looked for.** The sanitizer runs have the leak check off (`[run] asan_options`): memory that a
  program still holds when it ends. No run with the check on has been made, so nothing is known about leaks in
  either program.
- **Memory on structures with many `N` literals.** On the loci of the limit above the rewrite needs 1.5 to 1.9 times
  the memory of v5.0.0 (7 literals: 1.2 to 4.2 GB against 0.7 to 2.2 GB). On every seed input it needs less (at
  most 59 MB against 172 MB).
- **The path aligner: the same work, spread over threads in the rewrite.** With `--aligner path-aligner` the
  rewrite needs the CPU time of v5.0.0 and divides it among its threads; v5.0.0 does not. One input of `main5`,
  alone on an idle machine, equal output:

  | `--threads` | v5.0.0: clock, CPU | Rewrite: clock, CPU |
  |---|---|---|
  | 1 | 4.3 s, 4.3 s | 4.0 s, 4.0 s |
  | 2 | 4.3 s, 4.3 s | 2.1 s, 4.0 s |
  | 4 | 4.3 s, 4.3 s | 1.1 s, 4.0 s |
  | 8 | 4.3 s, 4.3 s | 0.66 s, 4.0 s |

  On a busy machine the order turns: with 48 copies at once on 16 CPUs v5.0.0 took 12.1 s and the rewrite 15.3 s
  (medians). This is how the rewrite alone comes to pass a time limit in a replay under load; every such input of
  `main4` and `main5` that was measured alone ended with equal output; the rewrite was faster, or with one thread
  within 3% of v5.0.0 (2.23 s against 2.17 s). Over all seed inputs together the rewrite needs 34 s against 124 s.
- **`--region-extension-length` is never rejected.** v5.0.0 means to accept 500 to 1500 only
  (`io/ParameterLoading.cpp`, `assertValidity`, lines 208-216: "the range of allowed extensions is between 500 and
  1500"), but the condition reads `length < 500 && length > 1500`, which no value satisfies. NuExpansionHunter has
  the same condition (lines 269-270). On a plain case the two programs give the same output at 499, 50, 1, 0, -5,
  1501 and 100000, so this is not a difference between them. The generator uses values inside and outside the
  range, but none below 0: a negative length is an input constraint (above).

## Exemptions

Differences the adapter removes, in code (`dfz_compare` in [`adapter.cpp`](adapter.cpp)). Each is a condition on the
input and on the outcome; anything else that differs on such an input still counts.

| Input | Outcome that is removed | Why |
|---|---|---|
| an invalid value for an option only the rewrite has (tag `rewrite-option-error`; generated with `rewrite_option_errors = 1`) | the original succeeds, the rewrite ends with an error | the rewrite must reject the value, and the original never sees the option |
| a region written end-start (tag `inverted-region`; generated with `inverted_regions = 1`) | every difference except a program dying from a signal | an input constraint (above): the original's output is not defined |
| a reads file that is not valid, on the builds with sanitizers | both programs dying with AddressSanitizer's report "Joining already joined thread" | htslib's reader, the same in both ([`constraints/malformed-reads-file`](constraints/malformed-reads-file/)); one program alone dying that way still counts |

With the default options the first two inputs are not generated, so only the third rule removes anything, and only
in a campaign on the builds with sanitizers.

Not exemptions, but normalisation (`dfz_normalize`): record order and compression of the realigned BAM, and the
unused nibble of odd-length reads. The adapter compares decoded records as a multiset, so only these aspects are
left out.

There is no exemption for records whose mate has no contig (tag `mate-without-contig`; NuExpansionHunter's
documented limit 6, v5.0.0 indexes its genome mask out of bounds). Patch `eh-0002` makes these cases agree, and
excusing every difference on inputs with such a record would also hide differences with another cause in the same
file.
