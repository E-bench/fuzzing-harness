# The difference classes, deduplicated

4,622 stored differences from three campaigns, classified by the shape of the SAM difference:

| shape | cases | what it is |
|---|---|---|
| `unmapped-vs-mapped` | 2,771 | the original leaves the read unmapped, the rewrite aligns it |
| `record-count` | 1,698 | one side emits a different number of records (`-a`, `-z` fan-out) |
| `fields` | 149 | both align the read, but FLAG/POS/CIGAR/MAPQ/tags differ |
| `flag` | 4 | both align, different strand/position |

Only the first two were understood when this directory was written; the other two were surfaced by a campaign over a
wider option space (34 option sets). They are not one cause: see `../attribution.txt` for the sampled attribution, with
**bwa 0.7.19 as the referee** (same input and options on all three programs):

| attribution | sampled cases |
|---|---|
| the original deviates (bwa == rewrite) | 65 |
| the rewrite deviates (bwa == original) | 6 |
| all three differ | 8 |

and by option set:

| option set | shape | attribution |
|---|---|---|
| `-z 50` | record-count | 58 original deviates, 5 all differ |
| `-A 2 -B 6` | fields | 7 original deviates, 3 all differ |
| `-k 19` | fields | 2 rewrite deviates |
| `-L 1`, `-I 400`, `-h 5,1`, `-P` | fields | 1 rewrite deviates each |

So the picture is two-sided: under custom scoring and `-z` the original is the one that departs from bwa, while for a
few option paths the rewrite departs from both bwa and the original.

# Undefined behaviour

None found. The rewrite's ASan+UBSan build (`USE_MIMALLOC=0`, clang 20) replayed all 3,206 stored differences and
several thousand generated cases with **0 sanitizer reports**; the original's official binary is clean under valgrind
on a 40-case sample. A valgrind invalid free seen at exit in a mimalloc-linked build of the rewrite does not appear in
the same source built with `USE_MIMALLOC=0`, so it is a build-integration artifact, not a program bug.
