# bwa-mem3 `--compat=bwa-mem2` against bwa-mem2 v2.2.1

bwa-mem3 v0.18.0 promises "byte-for-byte identical alignment records to a specific upstream (bwa-mem2 v2.2.1),
`@PG` excluded and `-t`/`-K` matched". This target compares the two programs end to end on generated references
and reads, and records where they differ.

It is not yet a pipeline target: there is no `target.toml` or adapter, and the differences below were found with a
stand-alone differential loop (generate reference and reads, run both programs, drop the `@` header lines, compare
the remaining SAM records). Adding it to the pipeline needs the adapter and instrumented builds, see
[`docs/WORKFLOW.md`](../../docs/WORKFLOW.md).

## What is run and compared

| | |
|---|---|
| side a | bwa-mem2 **v2.2.1**, the official `x64-linux` prebuilt binary (dispatches to AVX-512 on these nodes) |
| side b | bwa-mem3 **v0.18.0**, built from source with clang 20.1.6 (needs `libdeflate` and, at run time, `libomp`) |
| index | built by **bwa-mem2 v2.2.1**; bwa-mem3's own index cannot be read by bwa-mem2, so it is never used |
| command line | `mem [-a] [-M] [-Y] [-P] [-L n] [-D x] [-w n] [-r x] [-c n] -t 1 -K 100000000 <ref> <reads>` |
| compared | every SAM record (header lines, including `@PG`, dropped); exit status |
| surface | a generated reference (tandem repeats of unit 1-6, N runs, homopolymers, GC bias; 0.6-20 kb) and generated reads (single and paired, 20-300 bp, inserts 50-2500, 0-10% substitution errors, indels, N in reads, 15 option sets) |

`-t 1` and `-K 100000000` are identical on both sides, which is what the compatibility claim asks for.

## What was found

**One cause, seen in two shapes.** When a **single-end read lies entirely inside a tandem repeat** the original
reports it unmapped and the rewrite aligns it, at MAPQ 0; with `-a` the rewrite then lists all the alignments it
found, which also changes the record count.

| campaign | cases that differ | `unmapped-vs-mapped` | `record-count` |
|---|---|---|---|
| `findings2`, 384 cores, 20 min | 1,015 | 817 | 198 |
| `findings3`, 384 cores, broader inputs | 2,175 | 1,786 | 389 |

No third kind of difference appeared in the 3,190 differing cases.

**Trigger**, from 312 controlled cases in [`trigger-matrix.csv`](trigger-matrix.csv) (generator:
`scripts/matrix.py`):

| factor | cases that differ |
|---|---|
| read entirely inside the tandem repeat | 64 of 78 |
| read crossing the repeat's edge into unique sequence | 0 of 78 |
| single-end | 64 of 107 |
| paired-end, both mates inside the repeat (that matrix's construction) | 0 of 107 |
| repeat copies 10 / 20 / 50 / 100 / 300 (inside + single subset) | 0/4, 1/10, 17/18, 22/22, 24/24 |

Read length (30-150bp) and repeat unit length (1-6) do not change the outcome; from about 100 copies on, every
such case differs.

The broader campaign also differs on 414 of its 2,175 cases that are paired-end (19%), so the paired-end 0 of 107
above is specific to the matrix's construction, where the whole insert lies inside the repeat: with one mate inside a
repeat and the pair reaching unique sequence, the same decision still shows up on that mate. The matrix's own paired
cases put both mates in the repeat; the pair is then consistent between the programs. This is the one place where
the trigger description above is narrower than the campaigns.

## The differing reads

Two artefacts in this directory hold every case the campaigns found:

- **`all-findings.tar.xz`** (21 MB, 4,622 cases) — for each case a directory
  `findings<N>/<worker>_<case>/` with `ref.fa`, `reads.fq`, `a.sam` (bwa-mem2 v2.2.1), `b.sam`
  (bwa-mem3 `--compat=bwa-mem2`, same options) and `diff.txt` (the options the case was found with and the first
  differing record). Unpack with `tar xf all-findings.tar.xz`.
- **`findings-manifest.tsv`** — one row per case: campaign, case id, difference class, options, single/paired,
  reference length, read count, and the sizes of the two input files, so the set can be filtered without unpacking.

## Differences

- [`differences/repeat-read-unmapped-vs-mapped`](differences/repeat-read-unmapped-vs-mapped/) — minimal witness: a
  1.2 kb reference of one tandem repeat and one 50 bp single-end read, `ref.fa` + `reads.fq` + `run.sh` + both
  programs' SAM output.
- [`differences/repeat-read-with-all-alignments`](differences/repeat-read-with-all-alignments/) — the same decision
  with `-a`: 2 records against 35.

## Reproducing

```sh
cd differences/repeat-read-unmapped-vs-mapped
MEM2=/path/to/bwa-mem2 MEM3=/path/to/bwa-mem3 ./run.sh     # prints DIFFERENT and the two records
```

## Ground truth, and the two defects

bwa 0.7.19 (built from source, its own index format) **maps** the witness read (`q0 0 chr1 421 0`). So bwa-mem2's
"unmapped" is a **bwa-mem2 deviation from bwa**, and the rewrite under `--compat=bwa-mem2` emits bwa's answer
instead of bwa-mem2's, which its own rule says it must not: `--compat=bwa-mem2` reproduces bwa-mem2, `--compat=bwa-mem`
reproduces bwa. One input class therefore exposes two defects:

- **bwa-mem2 v2.2.1** reports an exact-match read that lies entirely inside a tandem repeat as unmapped; bwa 0.7.19
  aligns it. (Its charter is bwa-identical output.)
- **bwa-mem3 `--compat=bwa-mem2`** reproduces bwa's mapped record here instead of bwa-mem2's unmapped one, so the
  compat target is not honoured on this input.

Both are deterministic: same output on every run, at every reference size tested (1.2 kb to 10 Mb), independent of
`-T` (1 to 60), `-c` (50 to 50,000), `-K` and of whether the repeat has 300 or 2,500,000 copies.

## What is not established

- **Whether bwa-mem2's unmapped decision is a bug or an intentional conservative choice** is for its maintainers to
  say; that it differs from bwa 0.7.19 is measured.
- **Real genomes.** The references are synthetic and repeat-rich; no WGS sample was run. Tandem repeats of this
  length are common in real genomes, but the effect on real data is unmeasured.
- **Threads and batching.** Only `-t 1`; `-K` was held at `100000000` on both sides.
- The campaigns were blind (no coverage feedback). A pipeline target with instrumented builds would explore more of
  the CLI and of the reference structure.
