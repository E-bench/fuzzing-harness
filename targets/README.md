# Targets

A target is a directory `targets/<name>/` that describes one pair of programs: side `a` is the original, side `b` the
rewrite. The pipeline reads three things from it.

## 1. `target.toml`

| Table | Keys | Meaning |
|---|---|---|
| top | `name`, `title` | |
| `[side.a]`, `[side.b]` | `name`, `title`, `repo`, `commit` | where each program comes from; `name` is used in file names, signatures and reports |
| | `patches = ["patches/<file>.diff", ...]` | temporary patches of this side that hide known bugs (`docs/WORKFLOW.md`); applied to the `fuzz`, `cov` and `refp` builds, never to `ref` |
| `[build]` | `deps`, `program`, `data` | shell commands (below) |
| | `adapter`, `harness_libs` | the adapter source (default `adapter.cpp`) and extra link flags for the harness |
| | `san_flags` | extra compiler flags of the builds with sanitizers (`san`, `fuzzsan`), to leave a check out (`-fno-sanitize=alignment`). Which checks are off is the target's choice, here and in `asan_options` below; the reason belongs into the target's README |
| | `ref = { cc, cxx }` | the compiler of the reference builds |
| `[run]` | `argv`, `argv_a`, `argv_b` | the command line: common part, then per side |
| | `timeout_ms`, `memory_mb`, `env` | time limit per program run; limit of its resident memory (default 0 = none). The harness ends a run that passes one, and the run's exit class is `timeout` or `memory`. Set both: without a memory limit an input that makes a program allocate without bound meets the machine's out-of-memory killer, which ends any process of the job; extra environment |
| | `sanitizers` | `true`: campaigns fuzz on the builds with sanitizers (`fuzzsan`) unless told otherwise (`SAN=0`); default `false`, and `SAN=1` on a campaign's command line asks for it |
| | `san_slower`, `san_larger` | by how much the time and the memory limit are multiplied for a run on a build with sanitizers (defaults 5 and 4); measure them on the seeds |
| | `asan_options`, `ubsan_options` | runtime options of the sanitizers for this target, such as a check to leave out (`detect_leaks=0`). The pipeline itself only sets how a report ends a run |
| | `exit` | `"class"` (default) or `"code"` |
| | `error_line`, `error_strip`, `error_prefix` | which line of a failed run's output goes into the signature, and what to cut from it |
| | `compare_on_error` | compare output files also when both programs exit with an error (default: no) |
| | `output = [{ file, compare, options }]` | the output files and their comparators: `bytes`, `lines`, `tsv`, `json`, `none` |
| `[adapter]` | anything | options the adapter reads with `adapterOpt("key")` |
| `[surface.<name>]` | `max_len`, `min_len`, `dict`, anything | one kind of fuzz input; extra keys are read with `surfaceOpt("key")` |
| `[coverage]` | `ignore` | regular expression of source files that are not the programs under test |
| `[[coverage.out_of_scope]]` | `match`, `function`, `reason` | program code the configured command line cannot reach, with the reason: whole files (`match`, a regular expression on the path) or single functions (`function`, a regular expression on the demangled name, optionally restricted by `match`). Reported separately; a listed function that does get executed is reported as a warning and stays in the totals |
| | `unreachable` | a file in the target directory that lists single branch outcomes the configured command line cannot produce (side, file, line, column, `true`/`false`/`both`, reason); they leave the branch totals of reachable code, and a listed outcome that is executed is reported |

Placeholders in `[build]` commands: `{target}` (this directory), `{side}`, `{src}` (the checkout), `{deps}`, `{build}`
and `{out}` (build directory and output binary of one flavour), `{bin}`, `{data}`, `{seeds}`, `{jobs}`.
Placeholders in `argv`: `{in}` (the case's input directory), `{out}` (this side's output directory), `{data}`, and any
variable the adapter sets in `Case::vars`.

What a run mode is (for example which analysis mode of a program is tested) is decided here, in `argv` and
`[adapter]`: the pipeline has no notion of it.

## 2. The build commands

- `deps` runs once per side and builds what the program needs.
- `program` builds one **flavour** of one side into `{out}`. A flavour is only a compiler and flags, passed in the
  environment: `DFZ_CC`, `DFZ_CXX`, `DFZ_CFLAGS`, `DFZ_CXXFLAGS`, `DFZ_LDFLAGS` (and `DFZ_FLAVOUR`, its name), plus
  `DFZ_PATCHES`, the patch files to apply to the source first (empty for `ref`).

  | Flavour | Compiler | Purpose |
  |---|---|---|
  | `ref` | `[build] ref` | rechecking differences; the builds that witnesses refer to |
  | `refp` | as `ref`, with the side's patches (`DFZ_PATCHES`) | rechecking what still differs with the known bugs patched away |
  | `fuzz` | AFL++'s `afl-clang-fast(++)` | edge coverage for the fuzzers' feedback |
  | `fuzzsan` | the same with the flags of `san` | fuzzing with sanitizers (`SAN=1`): every executed input is checked. Built when `[run] sanitizers` is set, or with `STEPS=fuzzsan` |
  | `cov` | clang with `-fprofile-instr-generate -fcoverage-mapping -fprofile-update=atomic`, plus `covshim.o` at link time | the coverage report |
  | `san` | clang with `-fsanitize=address,undefined -g` and `-D_GLIBCXX_ASSERTIONS` | stopping at memory errors and undefined behaviour that leave the output unchanged (`./dfz sanitize`) |

  A program in another language needs the equivalents (a memory-safe language has no use for `san`): the equivalent of AFL++'s runtime with a trace-pc-guard pass
  for `fuzz` (Rust: `-C passes=sancov-module ... -C link-arg=afl-compiler-rt.o`), LLVM source-based coverage for `cov`.
- `data` writes the seed corpora `{seeds}/<surface>/` and whatever fixed data the adapter wants under `{data}`.

## 3. The adapter (`adapter.cpp`)

Includes [`dfz.h`](../pipeline/harness/dfz.h) and defines up to three functions:

```cpp
// required: fill the case from the fuzz bytes; false = not a valid input of this surface
bool dfz_decode(const uint8_t *data, size_t size, dfz::Case &c);
// optional: rewrite an output file before it is compared (binary format -> canonical text); false = unchanged
bool dfz_normalize(const dfz::Case &c, const std::string &file, int side, std::string &content);
// optional: runs after the configured comparison; may add findings, and remove one that is an intended difference
// of the rewrite (an exemption: see below)
void dfz_compare(const dfz::Case &c, const dfz::Run &a, const dfz::Run &b, std::vector<dfz::Finding> &out);
```

`dfz::Case` gives the adapter: `surface`; `in`, the directory to write input files into (`c.put(name, content)`);
`vars`, extra placeholders for `argv`; `args`, `argsA`, `argsB`, options appended for both or one program; `preA`,
`preB`, extra runs of one program before the compared run; `tags`, labels that become part of the signature; `note`,
a line for people.

Conventions that make the generic steps work well:

- **Zero means default.** An all-zero field selects the ordinary case, so that what is non-zero in an input is
  what is special about it.
- **Tags name causes, not data.** Tag a case with its non-default options and with the input properties that
  explain a difference.
- **Exemptions are code, and exact.** A difference that the rewrite documents as intended is removed in
  `dfz_compare`, by a condition that names both the property of the input (the adapter knows what it generated) and
  the outcome that is expected: "the option only the rewrite has is invalid, the original succeeded, the rewrite
  ended with an error". Anything else that differs on the same input still counts. Do not exempt by a property of
  the input alone: that excuses every difference on such inputs, whatever its cause. Where the intended difference
  is one of form (order, compression, a field's spelling), use `dfz_normalize` instead, which leaves the rest of
  the file compared.
- **Raw and structured surfaces.** A raw surface hands the fuzz bytes to the programs as one input file (parsers); a
  structured surface decodes them into a consistent set of files (the logic behind the parsers). Seeds of the raw
  surfaces can be produced from the structured one with `dfz_tool decode`.
- **Determinism.** The same bytes must give the same case: random choices come from generators seeded by the input.
- **Input constraints.** Input shapes for which the original has no defined behaviour are recorded in the target's
  README with their evidence and left out of the generator behind an `[adapter]` option
  ([`docs/GENERATORS.md`](../docs/GENERATORS.md), "Implicit input constraints").

## What else a target holds

Written while the target is worked on ([`docs/WORKFLOW.md`](../docs/WORKFLOW.md)):

| Path | Content |
|---|---|
| `bugs/<name>/` | one bug of either program: cause, a witness against the unpatched programs, `run.sh` |
| `patches/` | the temporary patches that hide those bugs in the fuzzed builds (`[side.<x>] patches`) |
| `constraints/<name>/` | one implicit input constraint whose violation made the programs differ: evidence, witness, control |
| the unreachable list and its evidence | what the configured command line cannot reach (`[coverage] unreachable`) |
| `README.md` | what is run and compared, the surfaces, and the sections "Bugs found", "Input constraints", "Valid inputs the generator does not produce", "Exemptions" (what `dfz_compare` removes, and why) |

## Adding a target

1. `targets/<name>/target.toml`, a build script for `deps` and `program`, `adapter.cpp`, a seed generator.
2. `./dfz build <name>`, then `./dfz replay <name> SURFACE=<s> work/<name>/seeds/<s>/<seed>`: the seeds should run on
   both programs without a difference (or with the differences you expect).
3. `./dfz submit <name> TAG=smoke DUR=600`, then `./dfz post <name> TAG=smoke WAIT=1`: every check must pass.
   From there on, the two loops of [`docs/WORKFLOW.md`](../docs/WORKFLOW.md).
