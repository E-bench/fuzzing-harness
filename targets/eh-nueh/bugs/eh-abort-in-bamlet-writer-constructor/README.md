# ExpansionHunter v5.0.0 aborts when the alignment writer cannot be set up

**Side at fault:** the original. NuExpansionHunter `96bdca4` reports the error and exits 1.

**What differs.** v5.0.0 dies with SIGABRT (`terminate called without an active exception`) and prints no reason;
NuExpansionHunter prints `Invalid region string: chr1:-1-0` and exits with status 1. Neither writes VCF or JSON.

**Trigger** (this directory): a locus that starts at the first base of its contig, with a region extension shorter
than the default, here `--region-extension-length 1`:

```json
{ "LocusId": "L0", "LocusStructure": "(CNG)*", "ReferenceRegion": "chr1:0-15", "VariantType": "RareRepeat", ... }
```

The left flank of the locus would start at position -1. (v5.0.0 means to accept extensions of 500 to 1500 only, but
the check never rejects anything: [`../../README.md`](../../README.md), notes.)

```
EH=/path/to/ExpansionHunter-v5.0.0 NUEH=/path/to/NuExpansionHunter ./run.sh
eh exit=134     (SIGABRT)   terminate called without an active exception
nueh exit=1                 Invalid region string: chr1:-1-0
```

**Cause.** `BamletWriter`'s constructor starts the thread that writes the alignments in its initialiser list and
then does work that can throw:

```cpp
// ehunter/io/BamletWriter.cpp:66-79
    , writeThread_(&BamletWriter::writeHtsAlignments, this)
{
    for (const auto& locusSpec : regionCatalog)
    {
        graphReferenceMappings_.emplace(locusSpec.locusId(), generateMapping(contigInfo, locusSpec));   // can throw
    }

    writeHeader();                                                                                      // can throw
}
```

When it throws, the members are destroyed, and destroying a `std::thread` that was not joined calls
`std::terminate`. The destructor, which ends and joins the thread, does not run for an object whose constructor
did not finish. Here `generateMapping` throws from `ReferenceInterval::parseRegion("chr1:-1-0")`; any other exception
of these lines (`Failed to write header`) takes the same path. The sanitizer build names the same place
(`BamletWriter.cpp:79`, `std::thread::~thread()`).

**Temporary patch** ([`../../patches/eh-0004-bamlet-writer-constructor.diff`](../../patches/eh-0004-bamlet-writer-constructor.diff)):
the constructor catches the exception, ends and joins the thread and rethrows. With it v5.0.0 reports the error and
exits 1, as NuExpansionHunter does. The witness above is against the unpatched program.

Files: `in/` (reference, catalog, reads), `run.sh`, `observed/` (both programs' logs on the unpatched reference
builds), `case.bin` (the fuzz input the files were decoded from), `finding.txt`.
