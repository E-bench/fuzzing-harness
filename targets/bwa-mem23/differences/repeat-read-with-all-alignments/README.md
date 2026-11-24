# With `-a` the same decision shows up as a record count: 2 against 35

**Side at fault:** the rewrite, as in
[`../repeat-read-unmapped-vs-mapped`](../repeat-read-unmapped-vs-mapped/); this is the same decision seen through
`-a` (report all alignments).

The reference is repeat-rich and one single-end read lies inside the repeat. bwa-mem2 v2.2.1 reports it unmapped;
bwa-mem3 `--compat=bwa-mem2` aligns it, and with `-a` it then lists every one of the alignments it found:

```
bwa-mem2 v2.2.1            2 records    (the read unmapped, the mate as usual)
bwa-mem3 --compat=bwa-mem2 35 records
```

`run.sh` reproduces this with `-a`. The read is 30 bp of `AGA` repeats; the first differing record:

```
mem2: q1  4  *  0  0  *  0  0  AGAAGAAGAAGAAGAAGAAGAAGAAGAAGA  ...  AS:i:0
mem3: q1  0  chr2  1925  0  30M  0  0  AGAAGAAGAAGAAGAAGAAGAAGAAGAAGA  ...  MAPQ 0
```

This is why the campaigns report two shapes of difference (`unmapped-vs-mapped` and `record-count`) for one cause:
without `-a` the difference is one record against one record, with `-a` it is many.
