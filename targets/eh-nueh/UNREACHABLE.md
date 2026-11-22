# Code that the configured command line cannot reach

[`unreachable.tsv`](unreachable.tsv) lists branch outcomes of ExpansionHunter v5.0.0 (`eh`) and NuExpansionHunter
(`nueh`) that cannot occur when the programs are run as this target runs them: streaming mode, the release build
(no `EH_VERIFY` switch, optional libraries off), a FASTA reference, BAM or SAM reads, a local output file. The coverage
report takes them out of the "reachable" totals and shows the totals without the exclusion next to them.

**Status of the list.** It comes from reading both source trees against the list of branches the seed corpus did not
reach; the reasons below name the callers that were checked. The entries were not tested one by one. The coverage
step checks them on every run in one direction: an outcome listed here that does get executed is reported as a
warning in `COVERAGE.md` and counted as reachable. Entries marked *host* depend on the machine (CPU features, CPU
count), not on the input.

How far to trust it: of the 1,428 outcomes first listed, 137 (9.6%) have since been executed and removed: 47 when
146 seeds for newly found input features were added, 90 more in a ten-minute campaign of five fuzzers. 1,291 remain.
So the list still contains wrong entries, most likely among the "defensive check" and *host* reasons, and the
"reachable" coverage figure is an upper estimate; the figure for all code, shown next to it, does not depend on the
list.

Facts most verdicts rest on:

- Streaming never passes a genome-wide depth: both programs call `analyze(sex, boost::none, ...)`
  (eh `sample/HtsStreamingSampleAnalysis.cpp:187`, nueh `:95`).
- nueh's streaming mode always converts the catalog to its compact form (`app/ExpansionHunter.cpp:185-192`), so the
  code for the owned (unconverted) catalog, the class `LocusAnalyzer` and the owned genome mask run only in seeking
  mode.
- All `EH_VERIFY_*` switches are at their defaults in a release build (`core/VerifyPolicy.hh`): `kFastPaths`,
  `kRowStop`, `kFusedRows`, `kGraphCache`, `kFoldSkip` are true; `kScalarOnly`, `kNoAvx2`, `kNoAvx512`,
  `kBudgetSearch*`, `kReferenceLayout`, `kShadow` are false.
- Alignment scores are always 5/-4/-8/-2, k-mer lengths always 14 (alignment) and 10 (orientation); the aligner is
  always instantiated as `BaseMatchingDagAligner<true, false>`.
- Graph nodes contain only `ACGTBDHKMNSRVWY` and are never empty (`io/GraphBlueprint.cpp`).

## graph-tools (`thirdparty/graph-tools-master-f421f4c`)

| Side | File | Functions / lines | Reason |
|---|---|---|---|
| both | `TracebackMatrix.cpp` 37-184, `.hh:54` | `decodeTracebackDirection`, string constructor, `==`, `<<` | only graph-tools' tests call them; `PinnedAligner.cpp` uses the (rows, columns) constructor |
| both | `PathOperations.cpp` (eh 104-708, nueh 109-715) | `extendPath`, `splitSequenceByPath`, `checkIfPathsAdjacent`, `checkPathPrefixSuffixOverlap`, `mergePaths`, `greedyMerge`, `exhaustiveMerge`, `intersectPaths` | callers are `PathFamily*.cpp` and `GraphJson.cpp`, which are not linked |
| both | `PathOperations.cpp` `checkIfBookended` / `concatenatePaths` (eh 745-783, nueh 752-790) | the neighbouring-node branch and the throw | the only caller (`GappedAligner.cpp`) passes the seed shrunk to length 0: the ends are on one node |
| nueh | `PathOperations.cpp` 212, 213, 239, 733 | `.at()` guards, `generateSubpathForEachNode` | defensive; runs only when sub-paths are invalid (`BamletWriter.cpp:614`) |
| both | `Path.cpp` 47-95, 117-259, 476 | validity checks, `encode`, `getDistanceFromPathStart`, `getNodeSeq`, `extend*ToIncludeNode` | paths are always valid; no linked caller |
| both | `GraphAlignmentOperations.cpp` (eh 41-248, nueh 48-326) | `checkConsistency`, `decodeGraphAlignment`, `splitGraphCigar`, `splitNodeCigar`, `getQuerySequencesForEachNode`, `prettyPrint` | callers are tests only |
| both | `GraphAlignment.cpp` 48, 81-111, 135, 174, 213 | `numMatches`, `getIndexesOfNode`, `<<`, shrink throws | `numMatches`' only caller has no caller |
| eh | `KmerIndex.cpp` 50, 205-247, 311-335 | copy/move, `==`, `encode`, `kmers`, `numUniqueKmers*` | callers are `KmerIndexOperations.cpp` (not linked) and tests |
| nueh | `KmerIndex.cpp` 1062-1243 | copy/move, `==`, `encode`, string queries, `kmers`, `numUnique*` | only `verification/` probes call them |
| nueh | `KmerIndex.cpp` 357, 367, 626:27, 663, 675-688, 728-739, 848-861, 935, 1026-1045, 1173-1186 | k-mer length guards, `kReferenceLayout`, checks of an index the program built itself | fixed k-mer lengths; self-built data |
| nueh | `KmerIndex.cpp` 962, 999, 973-974, 1010-1011 | scalar tail loops | the SSE2 loop already runs to the padded end (x86 build) |
| both | `LinearAlignment.cpp`, `Operation.cpp`, `OperationOperations.cpp`, `LinearAlignmentOperations.cpp` | missing-base cases, the CIGAR character switch, consistency failures, `prettyPrint`, `getSequencesForEachOperation` | string CIGARs are only `nM`, `1M`, `nS`; consistency checks are defensive |
| both | `SequenceOperations.cpp` 40-58, 119-174 | split helpers, `checkIfNucleotideReference*`, `expandReferenceSymbol` throw | no callers; every node symbol is in the expansion table |
| both | `KmerEncoding.hh` 38-80 | k-mer length checks, `decode`, `indexToBase` | fixed length; no callers |
| both | `GraphReferenceMapping.cpp` 40-140 | `parseRegion` errors, length mismatch, unmapped node | node regions have the node's length; names with `:` or `-` are rejected earlier (`core/GenomicRegion.cpp:156-160`) |
| both | `Graph.cpp` (eh 46, 54, 106, 111, 139-169; nueh 53, 81, 89, 184-189, 227-301, 376) | id checks, `assertEdgeExists`, label functions | labels come only from JSON graph loading, which is not linked |
| both | `TracebackRunner.cpp` 52, 93, 116-117 | prefix soft clip, `kStop` cases | `kStop` is set only at (0, 0) |
| both | `DagAlignerAffine.hh`, `AffineAlignMatrixVectorized.hh`, `Details.hh`, `PinnedDagAligner.hh`, `BaseMatching*.hh` | `softClip`, `clipFront`, `exploreAllPaths`, empty query or target, corner cells, sentinel edges, `NODE_START` cases | template constants and invariants of the one instantiation used |
| nueh | `AffineAlignMatrixVectorized.hh`, `DpKernels.hh`, `BaseMatchingKernels.hh` | `*_reference` kernels, SSE2 tier, AVX-512 tier, `kShadow` / `kScalarOnly` / `kNoAvx*` | not dispatched on an AVX2 machine without AVX-512 (*host*); verify switches |
| nueh | `PinnedDagAligner.hh`, `PinnedBudgetAlignMatrix.hh` (single lines, see the list) | `kBudgetSearchAlways`, depth 257, default scores, clock rollover, index guards | 256 query bases at most per piece; fixed scores; invariants |
| nueh | `GappedAligner.cpp` 113, 222, 157, 329-330, 396, 436, 456 | null facet, `kFastPaths`, size guards | the facet is null only in seeking mode (`HtsSeekingSampleAnalysis.cpp:290`) |
| eh | `GappedAligner.cpp` 82:33, 314, 334 | suffix trim on a raw seed of at least 14 bases; consistency checks | |

## `sample/`, `io/`, `app/`, `core/`

| Side | File | Functions / lines | Reason |
|---|---|---|---|
| both | `core/ReadPairs.cpp` | whole file | used only by seeking-mode code |
| both | `core/Read.cpp` 37-52, `Read.hh` | `operator==` | only tests call it |
| both | `core/LocusStats.cpp` 36-37, 71; `core/CountTable.cpp` `setCountOf`, `collapseTopElements`; `core/GenomicRegion.cpp` `overlaps`; `core/HtsHelpers.cpp` `decodeQuals`, `decodeBases`; `io/SampleStats.cpp` `extractReadLength` | | test-only or without caller |
| both | `core/Common.cpp` 37, 49, 64, 83-88 | invalid sex, switch defaults, a printer | rejected earlier (`io/ParameterLoading.cpp`); full enums; no caller |
| nueh | `core/HtsHelpers.cpp` 132, 177, 182, 194; `ReadDecodeKernels.hh`; `io/BamletWriter.cpp` 684-703 | `kNoAvx2`, `kScalarOnly`, `kShadow`, AVX2 check | verify switches; *host* |
| both | `io/ParameterLoading.cpp` (nueh 160-183, 238, 245, 281, 306, 310, 354, 416; eh 144-167, 179, 186, 222, 247, 251, 295) | output path, mode and quality cutoff checks, `assertIndexExists` | fixed by the harness's command line; seeking only |
| both | `app/ExpansionHunter.cpp` | write failures, the seeking branch, jemalloc, verify constants, switch default | I/O failure; other mode; build |
| nueh | `core/TaskExecutor.cpp` 79, 130-155, 234, 258, 312-343; 41-61, 107 | guards; CPU count and cache layout | both callers pass a worker and no completion; *host* |
| nueh | `core/WeightedPurityCalculator` `score()`; `sample/AnalyzerFinder.cpp` owned tree (199-250, 269-287) and 176-184, 204, 302, 402-409, 448; `sample/GenomeMask.*`, `GenomeQueryCollection.cpp`; `core/OrderedBlocks.hh:52` | | compact catalog always (see above); test-only |
| nueh | `io/BamletWriter.cpp` (79-124, 189, 239, 264-268, 285, 292, 299-306, 320, 340, 363, 387, 414-472, 503, 542-576, 728, 752-774); `io/BamRecordSink.cpp` 75, 127, 210-241, 259, 292; `io/PreparedCatalog.cpp` 59, 68, 85, 198, 346; `io/CatalogLoading.cpp` 262, 276, 295 | compression threads 0 in fast mode, open / write / allocation failures, names over 254 characters | a regular local output file; I/O failures an input cannot cause |
| both | `io/GraphBlueprint.cpp`, `io/LocusSpecDecoding.cpp:178`, `io/VcfHeader.cpp`, `io/VcfWriter.cpp` | empty tokens, switch defaults, non-FASTA reference | tokenizer invariants; full enums |
| eh | `io/JsonWriter.cpp:92` | a locus without variants | rejected when the catalog is loaded |
| both | `sample/HtsFileStreamer.cpp` | open, index, thread-pool and header failures; status checks | the same file was opened successfully just before (`io/SampleStats.cpp:92`) |
| nueh | `sample/StreamingPairSource.cpp` 146-161, `ParallelPairSource.cpp` 244, 295, `HtsStreamingSampleAnalysis.cpp` 350, 506, 616 | | `next()` is not called after the end; logic guards |
| eh | `sample/HtsStreamingSampleAnalysis.cpp` 92, 128, 173; `io/BamletWriter.cpp` 106, 213, 234, 239; `core/ReferenceContigInfo.cpp:99` | | `while (true)`; non-standard exceptions; I/O; no caller |

## `genotyping/`, `locus/`, `alignment/`

| Side | File | Functions / lines | Reason |
|---|---|---|---|
| both | `alignment/AlignmentFilters.cpp` | every `checkIf*` | no caller outside the file |
| both | `alignment/OperationsOnAlignments.cpp` | `extendWithSoftclip`, `getNumNonrepeatMatches*`, `countFullOverlaps` | called only by `SoftclippingAligner` (tests only) and tests |
| nueh | same file 170-182, 195, 227, 230 | | `kFoldSkip` / `kShadow`; the caller returns earlier (`locus/LocusAligner.cpp:110`) |
| both | `genotyping/AlignMatrix.cpp` | `getMaxMotifCount`, `<<`, index out of range | tests and verification only; rows are below `numReads()` |
| nueh | `genotyping/AlignMatrix.cpp` 265, 268; `AlignMatrixFiltering.cpp` 80-86 | odd row count | rows are added in pairs |
| both | `genotyping/StrAlign.cpp`, `StrAlign.hh` | `<<`, `<`; eh 167, 173, 266, 273; nueh 172, 178, 297, 304 | no production caller; implied by earlier branches |
| both | `genotyping/RepeatGenotype.cpp` 36-52; `SmallVariantGenotyper.cpp` 36, 42, 56, 82; `SmallVariantGenotype.hh:51`; `AlleleChecker.cpp` 56, 81; `FragLogliks.cpp` (eh 85, nueh 86) | | one or two sizes, short <= long; ploidy 1 or 2; counts are not negative; full enums |
| both | `alignment/ClassifierOfAlignmentsToVariant.cpp`, `GraphVariantAlignmentStats.cpp` | `encode`, empty or non-consecutive node checks | nodes come from blueprint features |
| both | `alignment/OrientationPredictor.cpp` | `<<`; nueh 81, 95, 252, 256 | no caller; earlier branch took every base; fixed k-mer length; `kScalarOnly`; *host* |
| both | `locus/LocusAnalyzer.cpp` (eh 69, 91, 158; nueh 87, 144, 209), `LocusSpecification.cpp` `requiresGenomeWideDepth` | third variant type, off-target without mate, genome-wide depth | two-valued enum; dispatch is built for on-target bundles; depth is never passed |
| nueh | `locus/LocusAnalyzer.cpp` 75, 89, 116, 133; `VariantAnalyzer.hh`; `VariantSpecification.hh`; `IrrPairFinder.hh:47`; `LocusSpecification.hh` 88, 99, 202, 238-293; `LocusSpecification.cpp` 102, 104, 165 | | paths of the unconverted catalog and of the seeking-only `LocusAnalyzer`; ids come from the catalog itself |
| both | `locus/VariantSpecification.cpp`, `VariantFindings.cpp:42`, `LocusAnalyzerUtil.cpp:73` (eh), `LocusSpecification.cpp:76` (eh) | | full enums; no caller; `while (true)`; lookups use the analyzers' own ids |
| nueh | `genotyping/TwoAlleleStrGenotyper.cpp` 304, 305, 338 | | the scoring call cannot throw; the caller guards zero fragments |

## Reachable, but not produced by the generator

Kept in the totals. The generator's limits on valid input, and what each leaves unexercised, are listed in
[`README.md`](README.md), "Valid inputs the generator does not produce". Besides those: branches that depend on
thread timing.
