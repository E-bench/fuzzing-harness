# Improving a target's generator

For whoever takes over a target, person or agent. It applies to every target; the examples are from
[`targets/eh-nueh`](../targets/eh-nueh/).

## What the generator is and why it decides the result

A fuzzer mutates bytes. The target's adapter (`dfz_decode` in `adapter.cpp`) turns those bytes into a complete test
case: input files and command-line options. For a **raw** surface this is trivial (the bytes are one input file). For
a **structured** surface the adapter contains a generator: a small program that reads the bytes as a description
(tables of choices, sizes, flags) and writes a consistent set of files.

The fuzzers can only reach code that some byte string makes the generator produce an input for. If the generator
cannot express a read longer than 300 bases, no fuzzer, feedback or CPU budget will ever run the code for long reads.
Raw surfaces do not have this limit, but they rarely get past the parsers: random bytes almost never form a genome,
a catalog and reads that agree with each other. So the depth of a campaign is set by the structured generator, and
enhancing the generator is the main lever for coverage and for finding differences.

"Enhancing the generator" means: adding input features (a new table entry, a new flag, a new record kind, a new file
variant, a new option) so that a part of either program that no input reached becomes reachable.

## The loop

```
coverage report ─▶ what is never reached? ─▶ which input property selects it? ─▶ add it to the generator
      ▲                                                                              │
      └──────────── seeds for it, rebuild, check it is reached, campaign ◀───────────┘
```

1. **Measure.** `./dfz coverage <target> TAG=<campaign>` writes `work/<target>/coverage/<campaign>/`:
   - `COVERAGE.md`: totals, by directory, by file (most missed branches first), by fuzzer and surface;
   - `functions.<side>.tsv`: functions never entered;
   - `branches.<side>.tsv`: conditions with an outcome never taken (`missing` = `true`, `false` or `both`);
   - `html/<side>/`: annotated source.
   A tag without runs works too and covers the seed corpora alone (`TAG=seeds`): minutes instead of a campaign.
2. **Pick targets.** Start with files that have many missed branches and belong to what the rewrite changed. Read the
   never-taken outcomes in the source (both sides: a branch missing in the rewrite only is where the rewrite has code
   of its own).
3. **Find the input property.** For each one, answer: which property of the input makes this condition true (or
   false)? A threshold (`threads >= 4`, `length >= 32`), a flag, a file shape (a record that crosses a block
   boundary), a combination (an option together with a field), an error path (what makes this `throw` fire)? Write
   down exact constants. If nothing in the input can select it (compile-time switch, another run mode, code without
   a caller, a defensive check), record it as unreachable with that reason: a whole file or function under
   `[[coverage.out_of_scope]]`, a single branch outcome in the target's `unreachable` list
   ([`WORKFLOW.md`](WORKFLOW.md), "The coverage loop"). If an input could select it but the generator is limited not
   to produce that input, leave it in the totals and record the limit (below, "Record every limit on valid input").
4. **Add it to the generator.** See the rules below.
5. **Add seeds.** One hand-made seed per new feature (`make_data.py` or the target's equivalent), so that every
   fuzzer starts with an input that already exercises it. A feature only reachable through a rare byte combination
   stays unreached without a seed.
6. **Check before spending CPU hours.** `./dfz build <target> STEPS="harness data"`, replay the new seeds
   (`./dfz replay ...`): do both programs run the case as intended (not an early error)? Then `TAG=seeds` coverage
   again: are the branches from step 2 now reached?
7. **Run a campaign** under a new tag, then `post` (recheck, coverage, report); compare `COVERAGE.md` with the previous
   tag's. Repeat.

Reading the source systematically pays off before the first campaign too: an inventory of "which input property
selects which path", with exact thresholds, for the code the rewrite touched. For eh-nueh such an inventory led to
thread counts 4 and 8 (the rewrite switches its reader at 4), reads of 1,400 and 3,300 bases (beyond 16-bit
alignment scores), an index file next to the reads, and a second repeat type per locus.

## Rules for the generator

- **Zero is the ordinary case.** Every field must mean "default" when it is zero. A reader of an input then sees at
  a glance what is not default in it, a seed for one feature is "all zero except this field", and tags (which name
  the non-default options) stay few. New table entries go at the end of a table, never at index 0.
- **Keep the layout stable.** Corpora, stored findings and witnesses are byte strings in the current layout. Use
  reserved bytes and unused bits for new features, append to tables, and do not renumber or reorder: an old `case.bin`
  must still decode to the same case. If the layout must change, do it under a new surface name.
- **Valid by construction, with deliberate exceptions.** The default path must produce inputs that both programs
  accept and process deeply (the files agree with each other: coordinates, names, lengths). Invalid inputs are
  separate, explicit choices (a "malformed" selector with one value per defect), not accidents: one defect per case
  keeps error-path findings interpretable.
- **One knob per property, orthogonal knobs.** A byte that changes three things at once makes inputs unreadable and
  hides which property mattered. Independent random streams per part (per locus, per haplotype), seeded
  from the input, so that changing one part does not reshuffle the others.
- **Deterministic.** The same bytes give the same case, always. No clocks, no global random state, no dependence on
  file-system order.
- **Hit both sides of every threshold.** Tables should contain the boundary values of the conditions found in step 3
  (`limit - 1`, `limit`, `limit + 1`), not a smooth range. Sizes in the tables of eh-nueh are chosen this way (read
  length 150/151, quality 19/20, extension 999/1000/1001).
- **Tag causes.** Add a tag for each non-default option and for input properties an exemption rule refers to
  (`c.tags`). Do not tag data (sizes, positions): tags multiply signatures.
- **Mind the cost per case, and bound it by work, not by count.** Throughput is cases per second, and a few heavy
  cases dominate both fuzzing and the coverage replay, which runs every kept input once more. Keep the default case
  small (it is run millions of times) and cap the total work a case can ask for in the unit the programs' cost
  follows. eh-nueh first capped a case at 600 read pairs whatever the read length; a case with 506 reads of 1,400
  bases then took 10 seconds in each program instead of under 0.1, and the replay jobs that drew such cases ran
  several times longer than the others. The cap is now 600 pairs and 120,000 read bases. To find such cases:
  `./dfz replay` prints each program's time per case, and a campaign's `stats.tsv` shows a run whose cases per CPU
  second fall far below the others. Make expensive shapes (very long reads, many threads, large catalogs) explicit
  choices, never the default.
- **Record every limit on valid input.** Whenever the generator leaves out inputs that the programs accept (a cap
  on size for cost, a table with a few values, a format it cannot write, an option range), write it into the
  target's README under "Valid inputs the generator does not produce": the limit, why it is there, and what stays
  unexercised because of it. This is different from an input constraint (below), which removes inputs the original
  does not define: a limit on valid input is a gap in the testing, and a reader must be able to see every gap
  without reading the adapter.
- **Rewrite-only options.** Options that only the rewrite has go to the rewrite's command line only (`c.argsB`); the
  original runs without them and the outputs must still be equal. If such an option needs a preparing run, use
  `c.preB`.
- **Do not steer around findings on valid input.** The generator describes the input space; it must not special-case
  inputs because they produced a difference. A difference on valid input is a bug in one of the programs: it is
  recorded and hidden by a temporary patch, so that fuzzing goes on behind it ([`WORKFLOW.md`](WORKFLOW.md)); the
  input shape stays in the generator.
- **Do not generate differences that exist by design.** An invalid value for an option that only the rewrite has
  always makes the programs differ; cases like that tell nothing and bury the rest. Leave them out, or behind an
  option that is off by default (eh-nueh: `rewrite_option_errors`).
- **Do take implicit input constraints out.** See the next section.

## Implicit input constraints

Programs accept more than they define. An original that does not reject an input may still have no defined behaviour
for it: an `assert` that release builds compile out, a range check that is written but wrong, a documented
precondition that nothing enforces. On such inputs the original's output is an accident of its implementation, the
rewrite cannot be expected to reproduce it, and the fuzzers find these inputs quickly and fill the results with them.

When the root cause of a difference turns out to be such an input:

1. **Record the constraint** in the target's README ("Input constraints"): the constraint in one line, the evidence
   that the original does not define the input (an assert, a check, documentation, or the original contradicting
   itself), and the difference it caused. `targets/<target>/constraints/` holds exactly these: differences between
   the two programs that come from violating an implicit constraint. An input shape that is merely unusual, or on
   which the programs agree, does not go there. Keep one small witness with a `run.sh` under
   `targets/<target>/constraints/<name>/`, including a control input that differs only by satisfying the constraint
   (both programs must agree on the control: that is what shows the constraint is the cause).
2. **Stop generating it.** Give the generator an `[adapter]` option for the shape, off by default, and make the raw
   surfaces reject it in `dfz_decode` (return false) where a cheap check can tell. The constraint is then explicit,
   visible in `target.toml` and reversible, instead of a silent gap in the generator.
3. **Tag it when it is on.** With the option switched on, tag the cases and add an `[[exempt]]` rule for the tag, so
   that a campaign that deliberately includes the shape still separates these cases from the rest.

What is not an input constraint: an input the original handles in a defined way, however unusual (an empty catalog,
an unsorted file, an option at the edge of its range); and an input the original rejects with an error (the rewrite
must reject it too). When in doubt, the test is the evidence: without an assert, a check, a documented precondition
or the original giving different results for the same input (as v5.0.0 does on an invalid reads file, depending on
its thread count), it is a finding, not a constraint.

eh-nueh has two: regions written end-start, and reads files that are not valid SAM or BAM
([`targets/eh-nueh/README.md`](../targets/eh-nueh/README.md), "Input constraints").
