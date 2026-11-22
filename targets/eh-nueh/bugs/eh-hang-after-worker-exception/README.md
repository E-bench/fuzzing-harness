# ExpansionHunter v5.0.0 hangs after an error in a read-processing worker

**Side at fault:** the original. NuExpansionHunter `96bdca4` reports the same error and exits 1.

**What differs.** v5.0.0 logs the worker's exception and never returns (stopped after 170 seconds in our runs, at
`--threads` 1, 2 and 4); NuExpansionHunter exits with status 1 in a tenth of a second.

```
EH=/path/to/ExpansionHunter-v5.0.0 NUEH=/path/to/NuExpansionHunter ./run.sh
eh --threads 1 exit=124      (still running after 30 s)
nueh --threads 1 exit=1
eh --threads 4 exit=124
nueh --threads 4 exit=1
[Exception caught in thread 0 while processing read pair queue for locus: `L0` current readPair: `irr0_3`: Locus L0 is not supposed to have offtarget read pairs]
[Locus L0 is not supposed to have offtarget read pairs]
```

**Trigger** (this directory): two things together.

1. An error while a worker processes a locus's read pairs. Here: locus `L0` is a plain `Repeat` that lists
   `OfftargetRegions`, and the reads contain pairs in those regions ("Locus L0 is not supposed to have offtarget
   read pairs"; both programs raise it).
2. A read pair that is near more loci than `--threads` + 5. Here: 900 further loci 40 bases apart on `chr8`, and
   read pairs among them.

**Cause.** Each locus has a queue of read pairs; at most `threads + 5` queues may be active
(`sample/HtsStreamingSampleAnalysis.cpp`, `maxActiveLocusAnalyzerQueues`), and the reading thread waits in
`HtsStreamingReadPairQueue::insertReadPair` (`sample/HtsStreamingReadPairQueue.cpp:34-40`) when that many are.
A queue becomes inactive only when a worker empties it (`getNextReadPair`). After an exception,
`processLocusAnalyzerQueue` leaves its queue active (it rethrows from the `catch`), and every later call returns at
once because the error flag is set, again without touching its queue. The reading thread would notice the flag at
its next record, but it is still handing the current pair to its loci: once `threads + 5` queues are stuck active,
it waits forever.

**Temporary patch** ([`../../patches/eh-0003-release-queue-after-exception.diff`](../../patches/eh-0003-release-queue-after-exception.diff)):
after an exception, a worker empties and releases its queue instead of processing it. With it v5.0.0 reports the error
and exits 1 on this input, as NuExpansionHunter does. It is applied to the builds that are fuzzed (`target.toml`,
`[side.a] patches`); the witness above is against the unpatched program.

Files: `in/` (reference, catalog with 901 loci, reads), `run.sh`, `case.bin` (the fuzz input, surface `locus`).
