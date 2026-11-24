# A read that lies entirely inside a tandem repeat: the original leaves it unmapped, the rewrite aligns it

**Side at fault:** the rewrite, `bwa-mem3 v0.18.0` with `--compat=bwa-mem2`. The two programs disagree on whether
the read is aligned at all, so the alignment records differ.

**What differs.** A reference of one tandem repeat and one single-end read that lies entirely inside it:

```
ref   = (ACGT) x 300                                      1200 bp, one contig
read  = ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTAC   50 bp, single-end
```

```
bwa-mem2 v2.2.1                      q0  4  *  0  0  *  *  0  0  ...            unmapped
bwa-mem3 --compat=bwa-mem2           q0  0  chr1  1  0  50M  *  0  0  ...  NM:i:0  MAPQ 0
```

The read matches the reference exactly; the original still reports it unmapped, the rewrite reports one alignment
at MAPQ 0. `run.sh` reproduces it; `mem2.sam` and `mem3.sam` are the output of the two programs.

**Trigger (measured, see `../../trigger-matrix.csv`).** 312 controlled cases (unit length 1 to 6, 2 to 300
copies, read length 30 to 150, read inside the repeat or crossing its edge, single and paired end):

| factor | result |
|---|---|
| read entirely inside the tandem repeat | 64 of 78 differ |
| read crossing the repeat's edge into unique sequence | 0 of 78 differ |
| single-end | 64 of 107 differ |
| paired-end | 0 of 107 differ |
| repeat copies 10 / 20 / 50 / 100 / 300 | 0% / 2% / 24% / 25% / 25% of all cases; 0/4, 1/10, 17/18, 22/22, 24/24 of the inside+single subset |

So the divergence needs a **single-end read entirely inside a tandem repeat of at least about 50 copies**; at 100
copies and above every such case differs. Read length and repeat unit length (1 to 6) do not change the outcome.

**Scale.** Two end-to-end campaigns (random references with tandem repeats, N runs, GC bias, single and paired
reads 20-300 bp, 0-10% errors, indels, N in reads, and 15 option sets) compared every record (header lines
dropped):

| campaign | cases that differ | of which this class | of which `-a` record counts |
|---|---|---|---|
| `findings2` | 1,015 | 817 | 198 |
| `findings3` | 2,078 (still growing) | 1,704 | 374 |

No third kind of difference appeared in 3,000+ cases: the `-a` record-count cases are this same decision, where the
rewrite emits the alignments it found and the original emits none (see
[`../repeat-read-with-all-alignments`](../repeat-read-with-all-alignments/)).

**What is not established.** Which of the two behaviours the compat mode is supposed to have. bwa-mem3 documents
"byte-for-byte identical alignment records ... `@PG` excluded and `-t`/`-K` matched"; this input satisfies that and
still differs. Both programs are run with the same index (built by bwa-mem2 v2.2.1), `-t 1` and `-K 100000000`.

**Caveats.** The references are synthetic and repeat-rich, chosen to exercise the aligner, not a genome. Only
single and paired small cases were compared, at `-t 1`; no real WGS sample was run. bwa-mem2 is the official
v2.2.1 prebuilt binary (it dispatches to AVX-512 on these nodes); bwa-mem3 v0.18.0 was built with clang 20.

Files: `ref.fa`, `reads.fq`, `run.sh`, `mem2.sam`, `mem3.sam`.
