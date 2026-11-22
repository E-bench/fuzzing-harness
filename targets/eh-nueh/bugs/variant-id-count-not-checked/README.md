# Both programs read beyond the list of variant ids when a locus has fewer ids than variants

**Side at fault:** both: the code is the original's, and NuExpansionHunter `96bdca4` has it unchanged. Found by the
sanitizer builds, not by a difference: on the input that showed it the two programs fail alike.

**What happens.** A locus with two variants and one `VariantId` (`in/catalog.json`):

```json
{ "LocusId": "L0", "LocusStructure": "CAACAG(CAG)*CAACAG(AT)*CAACAG",
  "ReferenceRegion": ["chr1:1306-1336", "chr1:1342-1354"], "VariantType": ["RareRepeat", "Repeat"],
  "VariantId": ["LL0_v1"] }
```

While the catalog is loaded, the id of the second variant is taken from a vector with one element:

```cpp
// ehunter/io/LocusSpecDecoding.cpp:311-314 (the same lines in both programs)
const GenomicRegion& referenceRegion = userDescription.referenceRegions.at(variantIndex);
VariantTypeFromUser variantDescription = userDescription.variantTypesFromUser.at(variantIndex);
const string& variantId = userDescription.variantIds[variantIndex];          // no check
```

The two lines before it use `.at()`, which turns a short list into an error; this one reads whatever follows the
vector's storage and uses it as a string. What the programs then do depends on that memory: on the reference builds
both were seen to exit with `Error loading locus L0: basic_string::_M_construct null not valid` and to die with
SIGSEGV, on the same file. A string that happens to look valid would be written to the outputs as the variant's id.

```
std::vector<std::string>::operator[](unsigned long) const: Assertion '__n < this->size()' failed
    #5 ehunter::decodeLocusSpecification(...) ehunter/io/LocusSpecDecoding.cpp:314:43
    #6 ehunter::loadLocusCatalogFromDisk(...) ehunter/io/CatalogLoading.cpp:224:40
```

(`sanitizer-eh.txt`: the full report of the original; the rewrite's names the same line.)

**Reproduce.** `EH=... NUEH=... ./run.sh` shows the failure of the day; a build with `-D_GLIBCXX_ASSERTIONS` or
`-fsanitize=address` stops at the line above every time.

**Temporary patches** ([`../../patches/eh-0005-variant-id-count.diff`](../../patches/eh-0005-variant-id-count.diff),
[`../../patches/nueh-0002-variant-id-count.diff`](../../patches/nueh-0002-variant-id-count.diff)): `.at()` like the
neighbouring lines, in both programs, so that both report an error and exit 1.

**It can also end without an error, with an id that is not in the catalog.** With exactly one id too few, the
element read lies in memory the vector has reserved but not filled. On the unpatched reference builds
(`./run-one-short.sh`, a reads file without records, eight runs each, all alike):

| Catalog (`in-one-short/`) | ExpansionHunter v5.0.0 | NuExpansionHunter |
|---|---|---|
| 4 variants, 3 ids | exit 0; the fourth variant has the id `""` (`VARID=;` in the VCF, a key `""` under `Variants` in the JSON) | exit 1, `Error loading locus L0: basic_string::_M_create` |
| 6 variants, 5 ids | exit 0; the sixth variant has the id `""` | the same |
| 4 variants, 4 ids; 6 variants, 6 ids | exit 0, the ids of the catalog | the same |

So the two programs can differ on such a catalog, by what the memory behind the list holds in each, and neither
reports the mistake in the catalog. With two ids too few both ended with the error quoted above.

Files: `in/`, `run.sh`, `observed/` (logs on the unpatched reference builds), `case.bin` (the fuzz input, surface
`catalog`), `finding.txt`, `sanitizer-eh.txt`; `in-one-short/`, `run-one-short.sh` (the catalogs of the table).
