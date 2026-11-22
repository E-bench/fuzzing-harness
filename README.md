# Differential fuzzing pipeline

Runs an original program and a rewrite of it on the same generated inputs, with several fuzzers, and reports where
their outputs differ and how much of both programs the inputs reached.

- **Any pair of programs.** The pipeline knows nothing about a particular program. A target is a directory under
  [`targets/`](targets/) with a configuration file, a build script and a small C++ adapter that turns fuzz bytes into
  input files and options.
- **Any fuzzer.** A fuzzer is one file under [`pipeline/engines/`](pipeline/engines/). AFL++, AFL++ without feedback
  (black-box baseline), LibAFL, libFuzzer and honggfuzz are there. All drive the same harness and are compared on the
  same footing: differences found, time to find them, coverage of both programs.
- **Coverage is reported.** Both programs are also built with source-based coverage; every corpus is replayed on them
  and the report lists functions, lines and branches reached, per program, directory, file, fuzzer and over time, and
  what was never reached.

First target: [`targets/eh-nueh`](targets/eh-nueh/) (ExpansionHunter v5.0.0 against NuExpansionHunter).

Documents: [`docs/WORKFLOW.md`](docs/WORKFLOW.md) (how to work on a target: the coverage loop and the difference
loop; read this first), [`docs/GENERATORS.md`](docs/GENERATORS.md) (improving a target's input generator, input
constraints), [`targets/README.md`](targets/README.md) (what a target consists of, how to add one),
[`pipeline/engines/README.md`](pipeline/engines/README.md) (how to add a fuzzer).

## How it works

```
fuzzer ──bytes──▶ harness (pipeline/harness/dfz.h)
                    1. the target's adapter decodes the bytes into input files and options
                    2. both programs run on them (two child processes, same command line)
                    3. their exit states and output files are compared ─▶ atoms ─▶ signature
                    4. the input is stored under <find>/<hash of signature>/
                    5. feedback to the fuzzer: edge coverage of both children + one virtual edge per signature
```

- The children are built with AFL++'s compiler wrapper and write their edge counters into private shared-memory maps;
  the harness copies both maps into whatever feedback the fuzzer reads ([`fuzz_entry.cpp`](pipeline/harness/fuzz_entry.cpp)).
  So every fuzzer is guided by the coverage of both programs, and an input that shows a new kind of difference is kept
  even when it reaches no new code.
- A **signature** is `surface [tags] atoms`. Atoms say where the outputs differ, never what the values are:
  `exit:eh=sig6,nueh=err`, `r.vcf:c10.3` (column 10, third sub-field), `r.json:Genotype`, `r_realigned.bam:lines_a>b`.
  Tags are labels the adapter attaches to a case (for example its non-default options).
- Differences are found on the instrumented builds and **rechecked on reference builds** (the documented compiler, no
  instrumentation). They are worked off one at a time: one input is traced to its cause and settled as an input
  constraint, an exemption, a normalisation or a bug with a temporary patch; the recheck then drops every stored
  input with the same cause. This repeats until none is left ([`docs/WORKFLOW.md`](docs/WORKFLOW.md)).

## Layout

```
dfz                      entry point: ./dfz <command> <target> [NAME=value ...]
deploy.sh                copy this checkout to the machine that builds and fuzzes
pipeline/
  env.sh, cfg.py         common environment; target.toml -> shell variables and the harness's configuration
  harness/               dfz.h (runner, comparators, signatures, adapter interface), fuzz_entry.cpp, tool.cpp, covshim.c
  engines/               one file per fuzzer (engines/README.md: how to add one)
  steps/                 tools.sh build.sh campaign.sh submit.sh stop.sh recheck.sh coverage.sh report.sh post.sh check.sh
  tools/                 recheck.py coverage.py report.py check.py
targets/<target>/        target.toml, build script, adapter.cpp, seeds (targets/README.md: the contract)
docs/                    WORKFLOW.md (the two loops), GENERATORS.md (the generator, input constraints)
work/                    everything generated (git-ignored): fuzzers, sources, builds, seeds, runs, rechecks, coverage, reports
```

## Use

```bash
./dfz tools                                   # fetch and build the fuzzers into work/tools
./dfz build eh-nueh                           # fetch, deps, ref, refp, fuzz, cov, harness, data
./dfz submit eh-nueh TAG=main DUR=3600        # Slurm: every fuzzer x every surface, sized to the CPU budget
./dfz post eh-nueh TAG=main WAIT=1            # recheck of the differences, coverage, report, check (in parallel jobs)
./dfz coverage eh-nueh TAG=seeds              # coverage of the seed corpora alone (minutes; the generator loop)
./dfz recheck eh-nueh TAG=main                # what still differs under the current constraints, exemptions, patches
./dfz stop eh-nueh TAG=main                   # end a campaign early, keeping its results
./dfz fuzz eh-nueh ENGINE=afl SURFACE=locus TAG=local DUR=600   # one fuzzer on this machine
./dfz replay eh-nueh SURFACE=locus work/eh-nueh/recheck/main/inputs/<name>.bin   # one input: what differs
```

Results of a campaign `<tag>` of target `<t>`, all under `work/<t>/`:

| Path | Content |
|---|---|
| `runs/<tag>/<engine>.<surface>.<id>/` | `meta.txt`, `stats.tsv` (progress), `find.tgz` (stored inputs), `corpus.tgz` |
| `recheck/<tag>/remaining.tsv`, `inputs/` | the stored inputs on which the programs still differ on the reference builds, with their signatures and which fuzzer stored them when |
| `recheck/<tag>/summary.json` | the counts: stored, still differing (on the reference builds, only on the instrumented builds; to account for, exempt), flaky, gone |
| `coverage/<tag>/COVERAGE.md` | the coverage report (all code and reachable code); next to it per-file tables, never-entered functions, one-sided conditions, annotated HTML |
| `report/<tag>/REPORT.md` | the campaign report |

Requirements: x86-64 Linux, clang/LLVM 20 (compiler-rt with libFuzzer and the profile runtime, `llvm-cov`,
`llvm-profdata`), gcc, python 3.12 or later, git, curl; rustup `stable` for LibAFL. Machine values (modules, Slurm
account, CPU budget, CPUs per job, memory per CPU) go into `env.local.sh`, the remote copy's address into
`remote.local.sh` (both git-ignored).

## Comparison rules

Set per target in `target.toml`; the defaults:

- Exit states are compared by class: success, error exit, signal, timeout. Exit codes are not compared unless
  `exit = "code"`. A failed run's error message goes into the signature with numbers, quoted text and paths masked.
- Output files are compared only when both programs succeed. Each file has a comparator: `bytes`, `lines`, `tsv`
  (names the differing column), `json` (names the differing key). The adapter can rewrite a file before comparison
  (a binary format to canonical text).
- Differences a rewrite documents as intended are listed as `[[exempt]]` rules; matching cases are marked, not
  dropped.
