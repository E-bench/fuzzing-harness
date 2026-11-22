# Input constraint: a region is written `start-end` with `start <= end`

Applies to `ReferenceRegion`, `TargetRegion` and `OfftargetRegions` of a catalog.

**Why it is a constraint and not a finding.** ExpansionHunter v5.0.0 does not define what an inverted region means.
`GenomeMask::addRegion` starts with `assert(start <= stop)` (`sample/GenomeMask.cpp`), which release builds compile
out. What v5.0.0 then does is the product of two accidents: its interval tree swaps the two ends
(`thirdparty/intervaltree/IntervalTree.h`, the `Interval` constructor stores min and max), and its mask loop marks a
bin only when both ends fall into the same 1,024-base bin.

**What happens outside it.** The two programs differ, with default options:

| Catalog | ExpansionHunter v5.0.0 | NuExpansionHunter `96bdca4` |
|---|---|---|
| `TargetRegion` `chr1:2430-200` (`in/catalog.json`) | `LowDepth`, `1/1:INREPEAT/INREPEAT:0/0:0-714/0-714`, coverage 2.84, read length 150 | `LowDepth`, `./.`, coverage 0, read length 0 |
| `TargetRegion` `chr1:200-2430` (`in/catalog_forward.json`) | `PASS`, `0/0`, 10/10 repeats, coverage 19.2 | the same bytes |

The reads are the same in both rows (124 pairs around a `(CAG)*` locus at chr1:1300-1330 with 10 copies).

NuExpansionHunter's streaming mode always builds a compact catalog. `io/PreparedCatalog.cpp` (line 134) stores the
region's raw start and end as the interval that routing tests, while the tree key next to it gets its ends swapped;
`sample/AnalyzerFinder.cpp` (`queryPrepared`, lines 310-312, and the streaming sweep, lines 404-409) then asks for
`start <= readStart && readEnd <= end` with start > end, which no read satisfies. No read reaches the locus.

Neither output is right for the locus: v5.0.0's genotype comes from the few reads that fit the accidental window.

**In the generator.** `[adapter] inverted_regions = 0` in `target.toml` (the default) keeps the `locus` surface from
writing such regions and makes the `catalog` surface reject inputs that contain one. With `1` they are generated
again, tagged `inverted-region`, and the adapter (`dfz_compare`) then only reports a program that dies from a signal.

Run: `EH=... NUEH=... ./run.sh`.
