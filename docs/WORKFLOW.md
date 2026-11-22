# Working on a target

How the pipeline is meant to be used on one pair of programs. The work is iterative at every level: campaigns are
repeated, and between two campaigns two loops work off what the last one showed.

## Order of work

1. **Build and check.** `./dfz build <target>`, then a ten-minute campaign with every fuzzer and its post-processing:

   ```bash
   ./dfz submit <target> TAG=smoke DUR=600
   ./dfz post <target> TAG=smoke WAIT=1     # recheck, coverage, report, check
   ```

   `post` ends with `check` ([`pipeline/tools/check.py`](../pipeline/tools/check.py)): every fuzzer ran on every
   surface, archived its corpus and findings, executed at a plausible rate; the differences were replayed; the
   coverage report has totals for both programs and for every fuzzer. Nothing longer is worth running while a check
   fails.
2. **Coverage loop** (minutes per round, no campaign): raise what the generator reaches.
3. **Difference loop**: account for every difference the last campaign stored.
4. **The next campaign**, under a new tag, with the generator, constraints, exemptions and patches from 2 and 3;
   then `post`, and back to 2 and 3. A patch uncovers the differences that its bug was hiding, and a new input
   feature brings differences of its own, so they only show in a new campaign. Campaigns can grow longer from one
   round to the next. The work is done when a campaign of the intended length stores no difference that is not
   accounted for and adds no coverage.

Loops 2 and 3 interleave in practice: a generator change produces new differences, and a constraint changes what
the generator may produce. Start them as soon as the first campaign has stored something: neither needs a campaign
to have finished, and neither needs a long one.

## The coverage loop

```bash
./dfz coverage <target> TAG=seeds          # replays the seed corpora only: a few minutes
```

Read `work/<target>/coverage/seeds/COVERAGE.md` and the lists next to it, then for each piece of unreached code decide:

| Verdict | What to do |
|---|---|
| reachable: some input makes it run | add the input feature to the generator and a seed for it ([`GENERATORS.md`](GENERATORS.md)) |
| unreachable with this command line and build | record it with its reason: a whole file or function under `[[coverage.out_of_scope]]`, a single branch outcome in the target's `unreachable` list |
| reachable only with inputs the generator is limited not to produce (sizes, formats) or with timing | leave it in the totals; the limit and what it leaves unexercised go into the target's README, "Valid inputs the generator does not produce" |

The report gives two totals: all code of the configured mode, and the code reachable with the configured command line
(the first minus what the target lists as unreachable). Both are shown because the second depends on the list. The
list is checked on every run in one direction: a listed function or outcome that does get executed is reported as a
warning and counted as reachable again. The other direction (something listed that is reachable after all, with an
input nobody generated) is not checked by anything; entries need a reason that a reader can verify in the source.

Rebuild (`STEPS="harness data"`), run the coverage again, repeat. Stop when a round adds little; then let a short
campaign show what the fuzzers add to the seeds (`COVERAGE.md`, "By fuzzer").

## The difference loop

A campaign stores every input on which the two programs differ. The loop accounts for all of them:

```bash
./dfz recheck <target> TAG=<campaign> WAIT=1
```

replays every stored input on the reference builds under the current configuration and lists the inputs on which
the programs still differ: `work/<target>/recheck/<campaign>/remaining.tsv` (signature, which fuzzers stored it, the
input under `inputs/`) and the counts in `summary.json`. Nothing is grouped in advance: the next recheck removes
everything that shares a cause with the one input that was analysed, which is the only grouping that is right.

Take one input, find the cause, and act on it:

```bash
./dfz replay <target> SURFACE=<s> work/<target>/recheck/<campaign>/inputs/<name>.bin        # what differs
./dfz export <target> SURFACE=<s> work/<target>/recheck/<campaign>/inputs/<name>.bin <dir>  # input files, run.sh, outputs
```

What finds a cause: the programs' own logs (`replay` shows the last lines of both), the note and the tags the
adapter attaches to the case, and control experiments on the exported files (change one thing, run both programs
again). Inputs are not shrunk first: on eh-nueh every cause so far was found this way, also for inputs of several
hundred kilobytes.


| Cause | Action | Where |
|---|---|---|
| the input is outside what the original defines (an assert compiled out, an unenforced precondition) | **input constraint**: record it with its evidence and a witness, stop generating the shape | target README, `constraints/<name>/`, an `[adapter]` option ([`GENERATORS.md`](GENERATORS.md), "Implicit input constraints") |
| the rewrite documents the difference as intended | **exemption**: a tag on the cases, an `[[exempt]]` rule | `target.toml` |
| the outputs mean the same and differ in form (order, compression, formatting) | **normalisation**: compare the canonical form | `dfz_normalize` in the adapter, or another comparator in `[run] output` |
| a bug, in either program | **record it and patch it temporarily**: a witness against the unpatched builds, then a patch that hides the bug so that fuzzing continues behind it | `targets/<target>/bugs/<name>/` (inputs, `run.sh`, what differs and why), `[side.<x>] patches` in `target.toml`, `./dfz build <target> STEPS="refp fuzz cov"` |

Then `recheck` again: every input with that cause is gone at once, however its signature looked. Repeat until
`to account for` is zero, then run the next campaign: a patched bug often hid others.

Notes:

- **Counting.** The number of distinct bugs is the number of patches it took, not the number of inputs or
  signatures. A patch that removes many of them made them one bug.
- **Patches are temporary and minimal.** They exist to unmask what is behind a bug; they are not proposed fixes. The
  unpatched reference builds (`bin/<side>_ref`) are kept, and a bug's witness is always shown against them.
- **Flaky** differences (they do not reproduce on the reference builds every time) are in `remaining.tsv` with
  that status; they point at nondeterminism. **Gone** ones no longer differ: removed by a change, or an artefact of
  the instrumented builds.

## Resources

`env.local.sh` sets `DFZ_CPU_BUDGET`, `DFZ_JOB_CPUS` and `DFZ_MEM_PER_CPU`. The steps split their work into as many
Slurm jobs as the CPU budget allows (`submit`, `recheck`, `coverage`, `post`); memory is requested per CPU,
because clusters cap memory per user too and a job that asks for more than it needs blocks other jobs of the same
user.

- A running campaign works on private copies of the binaries, the configuration and the seeds: rebuilding and
  deploying during a run is safe.
- `./dfz stop <target> TAG=<campaign>` ends a campaign early and keeps its results.
