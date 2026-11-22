# NuExpansionHunter passes a null pointer to memcpy when the catalog is empty

**Side at fault:** the rewrite, `96bdca4`. Found by the sanitizer build, not by a difference: the outputs of the two
programs agree.

**What happens.** With a variant catalog that holds no locus (`[]`), `compactCatalog` copies zero loci:

```cpp
// ehunter/io/PreparedCatalog.cpp:340, 351
std::vector<MappedLocus> loci(count);                                    // count == 0: loci.data() is null
std::memcpy(storage.get() + lociSlice.offset, loci.data(), count * sizeof(MappedLocus));
```

`memcpy` must not be given a null pointer even for a length of zero (its arguments are declared `nonnull`); the call
is undefined behaviour. With the tested compilers it does nothing, and the program goes on to report the same error
as v5.0.0. A compiler may, however, conclude from the call that `loci.data()` is not null and remove later checks.

```
ehunter/io/PreparedCatalog.cpp:351:51: runtime error: null pointer passed as argument 2, which is declared to never be null
    #0 ehunter::compactCatalog(ehunter::RegionCatalog const&, unsigned int) ehunter/io/PreparedCatalog.cpp:351
    #1 main ehunter/app/ExpansionHunter.cpp:189
```

**Reproduce.** Build NuExpansionHunter with `-fsanitize=undefined` and run it in streaming mode on any reads and
reference with a catalog file that contains `[]`.

**Temporary patch** ([`../../patches/nueh-0001-empty-catalog-memcpy.diff`](../../patches/nueh-0001-empty-catalog-memcpy.diff)):
the copy is skipped when there is nothing to copy. It is applied to the builds that are fuzzed and to the sanitizer
build, so that reports behind this one can be seen.
