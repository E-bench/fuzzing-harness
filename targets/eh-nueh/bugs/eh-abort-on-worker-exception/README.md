# ExpansionHunter v5.0.0 aborts when a worker thread reports an error

**Side at fault:** the original. NuExpansionHunter `96bdca4` handles the same error and exits 1.

**What differs.** On an input that makes a locus fail while its analyzer is built, or while it is genotyped,
v5.0.0 dies with SIGABRT (`terminate called after throwing an instance of 'std::logic_error'`); NuExpansionHunter
prints the same message and exits with status 1. Neither writes VCF or JSON.

**Trigger** (this directory, default options): a catalog locus with two `RareRepeat` variants,

```json
{ "LocusId": "L0", "LocusStructure": "(CAG)*CAACAG(AT)*",
  "ReferenceRegion": ["chr1:1300-1330", "chr1:1336-1348"], "VariantType": ["RareRepeat", "RareRepeat"] }
```

Other errors raised in the same two places take the same path: `Error rate must be positive and less than 1`,
`Likelihood Ratio threshold must be positive`, `Invalid SMN specification`.

```
EH=/path/to/ExpansionHunter-v5.0.0 NUEH=/path/to/NuExpansionHunter ./run.sh
eh exit=134     (SIGABRT)   Region L0 must not have more than one rare repeat
nueh exit=1                 Region L0 must not have more than one rare repeat
```

**Cause.** Both places run their work in `std::thread`s whose function catches the exception, stores it for the
main thread and then rethrows it:

- `locus/LocusAnalyzerUtil.cpp`, `initializeLocusAnalyzerThread`, lines 98 and 106 (`throw;`);
- `sample/HtsStreamingSampleAnalysis.cpp`, `analyzeLocus`, lines 196 and 204.

An exception that leaves a `std::thread`'s function calls `std::terminate`. The main thread's code that would
rethrow the stored exception (`LocusAnalyzerUtil.cpp:134`, `HtsStreamingSampleAnalysis.cpp:340`) runs right after
the threads are started, before they are joined, so it normally sees nothing yet; and the program is gone before
the join.

**Temporary patch** ([`../../patches/eh-0001-worker-thread-exceptions.diff`](../../patches/eh-0001-worker-thread-exceptions.diff)):
the thread functions no longer rethrow, and the main thread joins the threads first and then rethrows what they
stored. With it v5.0.0 reports the error and exits 1, as NuExpansionHunter does. The patch is applied to the builds
that are fuzzed (`target.toml`, `[side.a] patches`), so that differences behind this one can be seen; the witness
above is against the unpatched program.

Files: `in/` (reference, catalog, reads), `run.sh`, `observed/` (both programs' logs and outputs on the unpatched
reference builds), `case.bin` (the fuzz input the files were decoded from), `finding.txt`.
