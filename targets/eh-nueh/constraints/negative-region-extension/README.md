# Input constraint: `--region-extension-length` is not negative

**Why it is a constraint and not a finding.** ExpansionHunter v5.0.0 does not define what a negative extension
means. It means to accept 500 to 1500 only (`io/ParameterLoading.cpp`, lines 208-216), but the condition reads
`length < 500 && length > 1500` and rejects nothing; NuExpansionHunter `96bdca4` has the same condition. With a
negative length `GenomicRegion::extend` (`core/GenomicRegion.cpp:134-139`) moves both ends of a region inwards, so
a region shorter than twice the amount gets its start after its end, and a region near the start of a contig gets
an end below 0. `GenomeMask::addRegion` starts with `assert(start <= stop)` and `assert(start >= 0)`
(`sample/GenomeMask.cpp:50-51`), which release builds compile out. The first of these is the constraint
[`../inverted-region`](../inverted-region/), reached here through an option instead of the catalog.

**What happens outside it.** One locus at `chr2:0-3` (`in/catalog.json`) and a reads file without records:

| `--region-extension-length` | Region after the extension | ExpansionHunter v5.0.0 | NuExpansionHunter `96bdca4` |
|---|---|---|---|
| -5 | 5 to -2 | SIGSEGV after `[Initializing all loci]` | exit 1, `[std::bad_alloc]` |
| -2 | 2 to 1 | exit 0 | exit 0, the same bytes |
| 0 | 0 to 3 | exit 0 | exit 0, the same bytes |

Both programs fail at -5, in different ways, and in the same step: marking the region in the mask that routes
reads.

- v5.0.0, `GenomeMask::addRegion` (`sample/GenomeMask.cpp:59-66`): the bin of position -2 is -1, as `size_t` the
  largest value; the mask is resized to that plus one, which is 0; the loop then sets bit 0 of an empty
  `vector<bool>`. UBSan: `store to null pointer of type '_Bit_type'` at line 66.
- NuExpansionHunter, `freezeIntervals` (`io/PreparedCatalog.cpp:160-166`): the same bin, as `uint64_t`, sizes a
  vector of 2^48 words. The allocation fails and the program reports `std::bad_alloc`. ASan: the requested
  allocation size exceeds the maximum, at line 166.

Neither is a check of the input. Found by honggfuzz on the `locus` surface in campaign `main5` (one stored
signature); the plain cases tried before had no region that close to the start of a contig, and there the two
programs gave the same output at -5.

**In the generator.** `[adapter] negative_extension = 0` in `target.toml` (the default) writes 5 where the option
table has -5. With `1` the value is -5 again and the case gets the tag `ext-5`; nothing is removed from the
comparison then.

Run: `EH=... NUEH=... ./run.sh`.
