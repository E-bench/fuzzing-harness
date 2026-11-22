# Both programs overflow an `int` in the genotype likelihood of very long alleles

**Side at fault:** both: the expression is the original's, and NuExpansionHunter `96bdca4` has it unchanged (in
another function). Found by the sanitizer builds, not by a difference: the outputs of the two programs agree.

**What happens.** The likelihood of a fragment under an allele of `alleleMotifCount` repeat units counts the
positions the fragment could come from:

```cpp
// v5.0.0: ehunter/genotyping/FragLogliks.cpp:67-68 (computeLoglik); NuExpansionHunter: lines 56-57 (forAllele)
const int numPossibleStarts = alleleMotifCount * motifLen_ + fragLen_ + 1;
const int numPossibleOrigins = numPossibleStarts * numPossibleStarts / 2;
```

The square does not fit into an `int` once `numPossibleStarts` reaches 46,341, that is, once the allele's length
plus the fragment length reaches 46,340 bases. Signed overflow is undefined behaviour. With the tested compilers the
product wraps around: `numPossibleOrigins` is then a wrong number, negative for the first such alleles, and its
logarithm, which enters the likelihood, is NaN.

```
ehunter/genotyping/FragLogliks.cpp:68:54: runtime error: signed integer overflow: 48493 * 48493 cannot be represented in type 'int'
    #0 ehunter::strgt::FragLogliks::computeLoglik(...) ehunter/genotyping/FragLogliks.cpp:68:54
    #1 ehunter::strgt::FragLogliks::getLoglik(int, int) ehunter/genotyping/FragLogliks.cpp:60:27
    #2 ehunter::strgt::getTopFragLogliks(...) ehunter/genotyping/StrGenotyper.cpp:92:50
    #3 ehunter::strgt::genotype(...) ehunter/genotyping/StrGenotyper.cpp:108:37
```

**Trigger** (this directory): a `(CAG)*` locus and a sample of 83 read pairs with `--threads 4`. Both programs
report a fragment length of 342, so the value 48,493 is a candidate allele of 16,050 units (48,150 bases) that the
genotyper scores. The candidate is the size it estimates for a long expansion
(`StrGenotyper.cpp:76-77`: read length + 2 x in-repeat reads x read length / depth, in repeat units), which is large
when a locus has many in-repeat reads and little depth. Alleles of that length also exist (the largest expansions
of 3- to 6-base motifs reach tens of kilobases).

**Reproduce.** `EH=... NUEH=... ./run.sh` exits 0 with equal VCF and JSON on the reference builds; a build with
`-fsanitize=undefined` stops at the line above (`sanitizer-eh.txt`, `sanitizer-nueh.txt`).

**Temporary patches** ([`../../patches/eh-0006-fragment-origins-overflow.diff`](../../patches/eh-0006-fragment-origins-overflow.diff),
[`../../patches/nueh-0003-fragment-origins-overflow.diff`](../../patches/nueh-0003-fragment-origins-overflow.diff)):
the product is computed with unsigned arithmetic and converted back, which gives the wrapped value without the
undefined behaviour. The patches keep what the programs print; they do not correct the likelihood. A correction
needs a 64-bit product in both programs.

**Does it change an output?** Not on the inputs tried. v5.0.0 was built once more with the product in 64 bits
(`static_cast<long long>(numPossibleStarts) * numPossibleStarts / 2`) and compared with v5.0.0 as it is fuzzed:

- on the witness both write the same VCF and JSON (genotype `8/8050`; the candidate of 16,050 units, whose
  likelihood is NaN in the wrapped arithmetic, does not win in the correct one either);
- on 8,189 inputs of the `locus` surface (all seeds, the corpora of honggfuzz and libFuzzer of campaign `main5`)
  no output differs. In 19 of these runs one of the two builds passed the time limit, in both directions.

How many of these inputs reach the overflow was not counted, so this bounds the effect on what the fuzzers found,
not in general. A wrapped product that is positive and small, or 0, would raise the likelihood of the candidate
(for 65,536 possible starts the product wraps to 0); no input with such a candidate was looked for.

Files: `in/`, `run.sh`, `observed/` (logs and outputs on the unpatched reference builds), `case.bin` (the fuzz
input, surface `locus`), `finding.txt`, `sanitizer-eh.txt`, `sanitizer-nueh.txt`.
