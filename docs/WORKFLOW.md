# Working on a target

How the pipeline is meant to be used on one pair of programs. The work is iterative at every level: campaigns are
repeated, and between two campaigns two loops work off what the last one showed.

## Order of work

1. **Build and check.** `./dfz build <target>`, then a ten-minute campaign with every fuzzer and its post-processing:

   ```bash
   ./dfz submit <target> TAG=smoke DUR=600
   ./dfz post <target> TAG=smoke WAIT=1     # recheck, coverage, sanitizers, report, check
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
./dfz replay <target> SURFACE=<s> work/<target>/recheck/<campaign>/inputs/<name>.bin        # what differs, seconds, memory
./dfz export <target> SURFACE=<s> work/<target>/recheck/<campaign>/inputs/<name>.bin <dir>  # input files, run.sh, outputs
./dfz decode <target> SURFACE=<s> work/<target>/recheck/<campaign>/inputs/<name>.bin <dir>  # input files and options only
```

What finds a cause: the programs' own logs (`replay` shows the last lines of both), the note and the tags the
adapter attaches to the case, and control experiments on the exported files (change one thing, run both programs
again). Inputs are not shrunk first: on eh-nueh every cause so far was found this way, also for inputs of several
hundred kilobytes.


| Cause | Action | Where |
|---|---|---|
| the input is outside what the original defines (an assert compiled out, an unenforced precondition) | **input constraint**: record it with its evidence and a witness, stop generating the shape | target README, `constraints/<name>/`, an `[adapter]` option ([`GENERATORS.md`](GENERATORS.md), "Implicit input constraints") |
| the rewrite documents the difference as intended | **exemption**, in code: remove the finding when the case has the property and the outcome is exactly the documented one. Never by a property of the input alone, which would excuse every difference on such inputs | `dfz_compare` in the adapter ([`targets/README.md`](../targets/README.md), "Exemptions are code"); if the difference is one of form, a normalisation instead |
| the outputs mean the same and differ in form (order, compression, formatting) | **normalisation**: compare the canonical form | `dfz_normalize` in the adapter, or another comparator in `[run] output` |
| a bug, in either program | **record it and patch it temporarily**: a witness against the unpatched builds, then a patch that hides the bug so that fuzzing continues behind it | `targets/<target>/bugs/<name>/` (inputs, `run.sh`, what differs and why), `[side.<x>] patches` in `target.toml`, `./dfz build <target> STEPS="refp fuzz cov"` |

Then `recheck` again: every input with that cause is gone at once, however its signature looked. Repeat until
`to account for` is zero, then run the next campaign: a patched bug often hid others.

Notes:

- **Counting.** The number of distinct bugs is the number of patches it took, not the number of inputs or
  signatures. A patch that removes many of them made them one bug.
- **Patches are temporary and minimal.** They exist to unmask what is behind a bug; they are not proposed fixes. The
  unpatched reference builds (`bin/<side>_ref`) are kept, and a bug's witness is always shown against them.
- **Statuses.** The fuzzers run instrumented builds on loaded machines; the recheck replays on the reference
  builds. A stored difference that does not show there every time is replayed on the instrumented builds as well:
  - **fuzz-only**: every time there, not on the reference builds. The difference depends on the build (the compiler,
    its flags); it is listed with the ones to account for.
  - **timeout:\<side\>**: the difference is that this program passed the time limit and the other did not. A time
    limit is the harness's doing, so these are a class of their own. They are looked at after the differences in
    output, not dropped: one program can end in an error at once while the other is still computing towards the
    same error, which at a short limit looks like a difference; but a program can also hang. Each such input is
    replayed once more with ten times the limit (`--slow-factor`), and `remaining.tsv` shows the seconds both
    programs took. Both end and agree: `timeout:<side>`. Another difference shows: `differs`, with that difference.
    The program passes the long limit too: `timeout-long:<side>`, which is how a hang shows.
    **The side matters.** The original passing the limit where the rewrite does not is put off (`timeout_deferred`
    in the counts, last in the list). The rewrite passing it where the original does not counts with the differences
    to account for, and needs care: a rewrite, above all one made for speed, must not be that much slower than
    what it replaces. For that reason an input on which the rewrite passed the limit is replayed again at the end
    of the recheck, with nothing else running in the job: wall time under the load of the other replays is not a
    program's speed, and a program that spreads its work over threads loses more of it to that load. If the two
    programs agree then, the signature is `gone` and counted in `gone_timeout_under_load`; otherwise the status
    and the seconds in `remaining.tsv` are those of the replay alone. Next to each program's seconds on the clock
    stands its CPU time (also in the output of `./dfz replay`): long on the clock with little CPU time means the
    program was kept waiting, by other work on the machine or by itself, and is to be measured again on an idle
    machine; more of both means it is slower. Every timeout also costs the fuzzers two time
    limits per execution, a reason to look at what makes the case expensive ([`GENERATORS.md`](GENERATORS.md),
    "Mind the cost per case").
  - **memory:\<side\>**: the difference is that this program passed the memory limit (`[run] memory_mb`) and the
    other did not. The harness's other limit, with the same rule for the side: the original's are put off, the
    rewrite's count with the differences to account for.
  - **flaky**: only in some replays. Seen causes: undefined behaviour of a program (an out-of-bounds read whose
    result changes from run to run), and time limits passed by heavy cases on a loaded machine.
  - **gone**: in no replay. Removed by a change since the campaign (a constraint, a normalisation, a patch), or one of
    the flaky causes that did not happen again.

## Memory errors and undefined behaviour

The difference loop only sees what changes an output. A program that reads out of bounds, uses a container out of
range or overflows a signed integer can print the right answer anyway, on the tested machine, with the tested
compiler. So every input of a campaign (seeds, corpora, stored differences) is also replayed on sanitizer builds:

```bash
./dfz sanitize <target> TAG=<campaign> WAIT=1      # part of `post`; TAG=seeds for the seed corpora alone
```

(`./dfz replay <target> SURFACE=<s> BUILD=san <input>` runs one input on these builds.)

`work/<target>/sanitize/<campaign>/findings.tsv` has one row per program, kind of error and place in the source,
with the number of inputs that stop there, the sanitizer's report (`reports/`) and one input (`inputs/`). Rows are
by place because the place is the defect. Each row is a bug of that program and is handled like any other: a
directory under `targets/<target>/bugs/` with the report and a witness, and, if it stops inputs from getting
further, a temporary patch. A report on input that the original does not define (an input constraint) is recorded
with the constraint instead.

The sanitizer builds carry the temporary patches, like the fuzzed builds; a bug that is patched does not show again.
A defect that both programs share (the rewrite kept the original's code) shows here and nowhere else, because both
fail alike; it is patched in both programs the same way. The replay runs with five times the time limit and four
times the memory limit; runs that the harness ended at a limit are counted in `summary.json` and are not findings.

## Resources

`env.local.sh` sets `DFZ_CPU_BUDGET`, `DFZ_JOB_CPUS` and `DFZ_MEM_PER_CPU`. The steps split their work into as many
Slurm jobs as the CPU budget allows (`submit`, `recheck`, `coverage`, `post`); memory is requested per CPU,
because clusters cap memory per user too and a job that asks for more than it needs blocks other jobs of the same
user.

- **Set both limits of a program run** (`[run] timeout_ms`, `memory_mb`). The harness ends a run that passes one
  and gives it the class `timeout` or `memory`. Without a memory limit, an input that makes a program allocate
  without bound uses up the job's memory, and the machine then ends whichever process of the job is largest: seen
  as `sig9` in differences that do not repeat, as lost coverage, and as jobs that end early. Choose the limits from
  the largest case the generator makes on purpose: `./dfz run <target> SURFACE=<s> work/<target>/seeds/<s>` prints
  the largest memory and the longest time of both programs over the seeds.
- A step that is submitted again for the same campaign replaces its jobs that are still queued or running, and the
  merging job refuses results that are not the whole of one submission.
- A running campaign works on private copies of the binaries, the configuration and the seeds: rebuilding and
  deploying during a run is safe.
- `./dfz stop <target> TAG=<campaign>` ends a campaign early and keeps its results.
