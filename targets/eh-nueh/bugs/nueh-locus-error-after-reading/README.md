# NuExpansionHunter reports an invalid locus only after it has read the sample

**Side at fault:** the rewrite, `96bdca4`. A difference in when an error is reported, not in what is reported.

**What differs.** A catalog with a locus that both programs reject (`L1` has two `RareRepeat` variants) and reads
for another locus:

```json
{ "LocusId": "L1", "LocusStructure": "(CAG)*CAACAG(AT)*",
  "ReferenceRegion": ["2:1300-1330", "2:1336-1348"], "VariantType": ["RareRepeat", "RareRepeat"], ... }
```

Both end with `Region L1 must not have more than one rare repeat`. v5.0.0 does so before it reads a record, the
rewrite after it has read and aligned all of them:

```
v5.0.0 (with the patch of eh-abort-on-worker-exception)   0.01 to 0.06 s   exit 1
NuExpansionHunter                                          3.55 s           exit 1      (three runs each, idle machine)

nueh.log:  [Initializing all loci]
           [Streaming reads]
           [Analyzing read evidence]
           [Region L1 must not have more than one rare repeat]
```

The time is that of processing the sample: 5,879 records here. Under a campaign's load the same input took the
rewrite 24 s, which is how it was found (the rewrite passed the 20-second limit, the original did not). How much
of a real sample is processed before the error was not measured.

**Cause.** v5.0.0 builds the analyzers of all loci in its step "Initializing all loci", and the constructor checks
the locus. The rewrite builds a locus's analyzer when it first needs it (`sample/HtsStreamingSampleAnalysis.cpp`,
`StreamingAnalyzers::get`, `std::call_once`), at the latest in `finish`. In the witness the error is printed after
the step "Analyzing read evidence" has begun, that is, after all records were read. The other checks of that
constructor are made at the same place.

**Reproduce.** `EH=... NUEH=... ./run.sh` and compare the times; the unpatched v5.0.0 ends with SIGABRT instead of
exit 1 on this input ([`../eh-abort-on-worker-exception`](../eh-abort-on-worker-exception/)), equally fast.

**No patch.** Checking every locus at the start would change the path that the rewrite takes on every input, and
with it what is tested. Inputs of this kind stay in the recheck's list as time limits passed by the rewrite
(`timeout:nueh`, the original ending in this error).

Files: `in/` (reference, catalog, reads), `run.sh`, `observed/` (both programs' logs on the unpatched reference
builds), `case.bin` (the fuzz input, surface `locus`), `finding.txt`.
