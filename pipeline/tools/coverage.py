#!/usr/bin/env python3
"""Coverage of one campaign: replay every corpus on the coverage builds and report what was and was not reached.

  coverage.py <target work dir> <tag> <target.toml> [--jobs N] [--slices 4] [--llvm-bin DIR]
              [--phase all|replay|report] [--shard K/N]

The replay is the expensive part and splits over machines: `--phase replay --shard K/N` replays every N-th block of
inputs and leaves merged profiles in coverage/<tag>/.shards/K/; `--phase report` builds the report from all shards
(steps/coverage.sh submits them as Slurm jobs when SHARDS > 1). An input is replayed once per run: copies that the
instances of one fuzzer exchanged, and inputs equal to a seed, are dropped (a run's coverage includes its surface's
seeds, which every run starts from).

For both programs (bin/<side>_cov, clang source-based coverage): functions, lines, regions and branches, where a
branch is one outcome (true or false) of one condition, as llvm-cov counts them.

  runs      every runs/<tag>/<engine>.<surface>.<id>/corpus.tgz, plus the seed corpus of every surface ("seeds")
  slices    a run's corpus is cut by file time into `slices` parts (1/8, 1/4, 1/2 and all of the duration), which
            gives coverage over time per engine
  scope     [coverage] ignore drops files that are not the program (dependencies, tests); [[coverage.out_of_scope]]
            names program code the configured command line cannot reach (whole files by `match`, single functions
            by `function`, a regular expression on the demangled name), reported separately with its reason. A
            function listed there that was executed after all stays in the totals and is reported as a warning.
            [coverage] unreachable names a file of single branch outcomes that cannot occur (side, file, line,
            column, outcome, reason); they leave the branch totals, and one that does occur is reported as a warning.
            The report gives the totals with and without these exclusions.

Outputs in coverage/<tag>/:
  COVERAGE.md              the report
  coverage.json            every number of the report
  <side>.profdata          merged profile of everything
  files.<side>.tsv         per file: functions, lines, regions, branches (covered / total)
  functions.<side>.tsv     functions never entered (file, line, name, regions)
  branches.<side>.tsv      conditions with an outcome never taken (file, line, column, true count, false count)
  html/<side>/             llvm-cov's annotated source (line counts and branch counts)
"""
import argparse, collections, json, os, re, shutil, subprocess, sys, tarfile, tomllib
from concurrent.futures import ThreadPoolExecutor

ap = argparse.ArgumentParser()
ap.add_argument("work"); ap.add_argument("tag"); ap.add_argument("toml")
ap.add_argument("--jobs", type=int, default=os.cpu_count())
ap.add_argument("--slices", type=int, default=4)
ap.add_argument("--chunk", type=int, default=200)
ap.add_argument("--llvm-bin", default="")
ap.add_argument("--scratch", default=None)
ap.add_argument("--max-inputs", type=int, default=0, help="per run and slice: replay at most this many inputs (0 = all)")
ap.add_argument("--phase", default="all", choices=("all", "replay", "report"))
ap.add_argument("--shard", default="0/1")
a = ap.parse_args()
SHARD, NSHARD = (int(x) for x in a.shard.split("/"))
import hashlib
cfg = tomllib.load(open(a.toml, "rb"))
SIDES = [cfg["side"]["a"]["name"], cfg["side"]["b"]["name"]]
TITLE = {cfg["side"][s]["name"]: cfg["side"][s].get("title", cfg["side"][s]["name"]) for s in ("a", "b")}
ccfg = cfg.get("coverage", {})
IGNORE = ccfg.get("ignore", "")
OOS = [(re.compile(r["match"]), r.get("reason", "")) for r in ccfg.get("out_of_scope", []) if "function" not in r]
# single functions: a regular expression on the demangled name, optionally one on the file
# single branch outcomes: [coverage] unreachable = "<file in the target directory>", lines
#   side TAB file TAB line TAB column TAB true|false|both TAB reason
UNREACH = collections.defaultdict(dict)   # side -> (file, line, column) -> (outcomes, reason)
if ccfg.get("unreachable"):
    for l in open(os.path.join(os.path.dirname(os.path.abspath(a.toml)), ccfg["unreachable"])):
        p_ = l.rstrip("\n").split("\t")
        if len(p_) >= 6 and not l.startswith("#"):
            UNREACH[p_[0]][(p_[1], int(p_[2]), int(p_[3]))] = (p_[4], p_[5])
FOOS = [(re.compile(r["function"]), re.compile(r.get("match", "")), r.get("reason", "")) for r in ccfg.get("out_of_scope", []) if "function" in r]
runs_dir = os.path.join(a.work, "runs", a.tag)
out = os.path.join(a.work, "coverage", a.tag)
scratch = a.scratch or os.path.join(os.environ.get("TMPDIR", "/tmp"), f"dfz_cov_{os.getpid()}")
shm = f"/dev/shm/dfz_cov_{os.getpid()}" if os.access("/dev/shm", os.W_OK) else os.path.join(scratch, "work")
shard_dir = os.path.join(out, ".shards", str(SHARD))
if a.phase == "all":
    shutil.rmtree(out, ignore_errors=True)
os.makedirs(out, exist_ok=True)
os.makedirs(scratch, exist_ok=True)
# work on private copies of the binaries, the configuration and the data: a rebuild or a deployment during the run
# must not change what is being run
local = os.path.join(scratch, "local")
shutil.rmtree(local, ignore_errors=True)
os.makedirs(local)
for f in ["dfz_tool"] + [s + "_cov" for s in SIDES]:
    shutil.copy2(os.path.join(a.work, "bin", f), os.path.join(local, f))
shutil.copy2(os.path.join(a.work, "harness.cfg"), os.path.join(local, "harness.cfg"))
if a.phase != "report":
    shutil.copytree(os.path.join(a.work, "data"), os.path.join(local, "data"), symlinks=True)
tool = os.path.join(local, "dfz_tool")
BIN = {s: os.path.join(local, s + "_cov") for s in SIDES}
llvm = lambda t: os.path.join(a.llvm_bin, t) if a.llvm_bin else t


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha1(f.read()).digest()


def merge(dst, srcs):
    srcs = [s for s in srcs if os.path.exists(s)]
    if not srcs:
        return False
    lst = dst + ".list"
    with open(lst, "w") as f:
        for s in srcs:
            if os.path.isdir(s):
                for n in sorted(os.listdir(s)):
                    f.write(os.path.join(s, n) + "\n")
            else:
                f.write(s + "\n")
    r = sh([llvm("llvm-profdata"), "merge", "-sparse", "-o", dst, "-f", lst])
    os.remove(lst)
    if r.returncode:
        print("llvm-profdata:", r.stderr[:500], file=sys.stderr)
    return r.returncode == 0 and os.path.exists(dst)


def replay_phase():
    """the runs and their inputs, cut into time slices; replay this shard's part; leave profiles in shard_dir"""
    seeds_dir = os.path.join(a.work, "seeds")
    seed_hash = {}
    runs = []   # {name, engine, surface, slices: [[files]]}
    for surface in sorted(os.listdir(seeds_dir)):
        files = sorted(os.path.join(seeds_dir, surface, f) for f in os.listdir(os.path.join(seeds_dir, surface)))
        seed_hash[surface] = {sha(f) for f in files}
        runs.append({"name": f"seeds.{surface}", "engine": "seeds", "surface": surface, "slices": [files], "dur": 0, "cpus": 0, "dropped": 0})
    for run in sorted(os.listdir(runs_dir)) if os.path.isdir(runs_dir) else []:
        tgz = os.path.join(runs_dir, run, "corpus.tgz")
        meta = os.path.join(runs_dir, run, "meta.txt")
        if not os.path.isfile(tgz) or not os.path.isfile(meta):
            continue
        m = dict(l.strip().split("=", 1) for l in open(meta) if "=" in l)
        dest = os.path.join(scratch, "corpus", run)
        with tarfile.open(tgz) as t:
            t.extractall(dest, filter="data")
        files = []
        for root, _, names in os.walk(dest):
            if os.path.basename(root).startswith("."):
                continue
            for n in names:
                p = os.path.join(root, n)
                if n.startswith("log_") or n in ("fuzzer_stats", "plot_data") or "/.state" in p or n.startswith(".") or n.endswith(".csv"):
                    continue
                files.append((os.path.getmtime(p) - int(m["start"]), os.path.relpath(p, dest), p))
        files.sort()
        dur = int(m["dur"])
        bounds = [dur * 2 ** -(a.slices - 1 - k) for k in range(a.slices)]
        bounds[-1] = float("inf")
        sl = [[] for _ in bounds]
        seen = set(seed_hash.get(m["surface"], ()))
        dropped = 0
        for t, _, p in files:
            h = sha(p)
            if h in seen:
                dropped += 1
                continue
            seen.add(h)
            sl[next(k for k, b in enumerate(bounds) if t <= b)].append(p)
        runs.append({"name": run, "engine": m["engine"], "surface": m["surface"], "slices": sl, "dur": dur, "cpus": int(m.get("cpus", 0)), "dropped": dropped})
    print(f"runs: {len(runs)}, inputs: {sum(len(s) for r in runs for s in r['slices'])} (+ {sum(r['dropped'] for r in runs)} duplicates dropped)", flush=True)
    tasks = []
    for r in runs:
        for k, files in enumerate(r["slices"]):
            if a.max_inputs and len(files) > a.max_inputs:
                step = len(files) / a.max_inputs
                files = [files[int(i * step)] for i in range(a.max_inputs)]
            for i in range(0, len(files), a.chunk):
                tasks.append((r, k, files[i:i + a.chunk], len(tasks)))
    tasks = [x for x in tasks if x[3] % NSHARD == SHARD]

    def replay(task):
        r, k, files, tid = task
        d = os.path.join(scratch, "in", str(tid))
        os.makedirs(d)
        for i, f in enumerate(files):
            os.symlink(f, os.path.join(d, f"{i:06d}"))
        prof = os.path.join(scratch, "prof", f"{r['name']}__{k}")
        env = dict(os.environ, DFZ_A=BIN[SIDES[0]], DFZ_B=BIN[SIDES[1]], DFZ_DATA=os.path.join(local, "data"),
                   DFZ_CFG=os.path.join(local, "harness.cfg"), DFZ_SURFACE=r["surface"], DFZ_WORK=os.path.join(shm, str(tid)),
                   DFZ_CHILD_ENV=f"LLVM_PROFILE_FILE={prof}/{{side}}/%8m.profraw", DFZ_TIMEOUT_MS=str(2 * int(cfg["run"].get("timeout_ms", 20000))))   # coverage builds are slower
        for v in ("DFZ_A_MAP", "DFZ_B_MAP", "DFZ_FIND", "DFZ_NOFEEDBACK"):
            env.pop(v, None)
        return sh([tool, "run", d], env=env).stdout.strip()

    n = 0
    with ThreadPoolExecutor(a.jobs) as ex:
        for res in ex.map(replay, tasks):
            m = re.search(r"inputs=(\d+)", res)
            n += int(m.group(1)) if m else 0
    shutil.rmtree(shm, ignore_errors=True)
    print(f"shard {SHARD}/{NSHARD}: replayed {n} inputs", flush=True)
    # this shard's profiles: one per run, slice and side
    shutil.rmtree(shard_dir, ignore_errors=True)
    os.makedirs(shard_dir)
    pr = os.path.join(scratch, "prof")
    todo = [(os.path.join(shard_dir, f"{d}.{side}.profdata"), [os.path.join(pr, d, side)])
            for d in (sorted(os.listdir(pr)) if os.path.isdir(pr) else []) for side in SIDES]
    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(lambda x: merge(*x), todo))
    json.dump([{**r, "slices": [len(s) for s in r["slices"]]} for r in runs], open(os.path.join(shard_dir, "runs.json"), "w"))
    shutil.rmtree(os.path.join(scratch, "prof"), ignore_errors=True)
    shutil.rmtree(os.path.join(scratch, "corpus"), ignore_errors=True)
    shutil.rmtree(os.path.join(scratch, "in"), ignore_errors=True)


if a.phase in ("all", "replay"):
    replay_phase()
if a.phase == "replay":
    shutil.rmtree(scratch, ignore_errors=True)
    sys.exit(0)
shards = sorted(d for d in os.listdir(os.path.join(out, ".shards")) if os.path.isfile(os.path.join(out, ".shards", d, "runs.json")))
if not shards:
    sys.exit("no replay results under " + os.path.join(out, ".shards"))
runs = json.load(open(os.path.join(out, ".shards", shards[0], "runs.json")))
for r in runs:
    r["slices"] = [[None] * n for n in r["slices"]]   # the report needs the counts only
print(f"report from {len(shards)} shard(s), {len(runs)} runs", flush=True)


def slice_profiles(run, k, side):
    return [os.path.join(out, ".shards", s, f"{run}__{k}.{side}.profdata") for s in shards]


# ---- profiles: per run and slice (cumulative), per engine, total
def summary(side, profdata):
    cmd = [llvm("llvm-cov"), "export", BIN[side], f"-instr-profile={profdata}", "-summary-only"]
    if IGNORE:
        cmd.append(f"-ignore-filename-regex={IGNORE}")
    r = sh(cmd)
    if r.returncode:
        print("llvm-cov:", r.stderr[:500], file=sys.stderr)
        return None
    return json.loads(r.stdout)["data"][0]


KINDS = ("functions", "lines", "regions", "branches")
pd = os.path.join(scratch, "profdata")
os.makedirs(pd)
result = {"sides": SIDES, "titles": TITLE, "runs": [], "engines": {}, "total": {}, "files": {}, "scope": {}}


def totals_in_scope(data):
    """sum the file summaries: (in scope, out of scope by reason)"""
    ins = {k: [0, 0] for k in KINDS}
    oos = collections.defaultdict(lambda: {k: [0, 0] for k in KINDS})
    for f in data["files"]:
        reason = next((why for rx, why in OOS if rx.search(f["filename"])), None)
        tgt = ins if reason is None else oos[reason]
        for k in KINDS:
            tgt[k][0] += f["summary"][k]["covered"]
            tgt[k][1] += f["summary"][k]["count"]
    return ins, {k: v for k, v in oos.items()}


def one(job):
    key, side, srcs = job
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", "_".join(str(x) for x in key))   # no commas: llvm-profdata reads "weight,file"
    dst = os.path.join(pd, f"{name}.{side}.profdata")
    if not merge(dst, srcs):
        return key, side, None, dst
    s = summary(side, dst)
    return key, side, (totals_in_scope(s)[0] if s else None), dst


jobs = []
for r in runs:
    for side in SIDES:
        for k in range(len(r["slices"])):
            srcs = [p for j in range(k + 1) for p in slice_profiles(r["name"], j, side)]
            if r["engine"] != "seeds":   # every run starts from its surface's seeds
                srcs += slice_profiles("seeds." + r["surface"], 0, side)
            jobs.append(((r["name"], k), side, srcs))
table = {}
with ThreadPoolExecutor(a.jobs) as ex:
    for key, side, tot, dst in ex.map(one, jobs):
        table[(key, side)] = (tot, dst)
for r in runs:
    last = len(r["slices"]) - 1
    result["runs"].append({"name": r["name"], "engine": r["engine"], "surface": r["surface"], "dur": r["dur"], "cpus": r["cpus"],
                           "inputs": [len(s) for s in r["slices"]],
                           "slices": {side: [table[((r["name"], k), side)][0] for k in range(last + 1)] for side in SIDES}})
# per engine and surface (all its runs), per engine, per surface, total
groups = collections.defaultdict(list)
for r in runs:
    last = len(r["slices"]) - 1
    for g in (("engine", r["engine"]), ("surface", r["surface"]), ("engine_surface", r["engine"] + " " + r["surface"]), ("total", "all")):
        groups[g].append(r["name"] + f"|{last}")
    if r["engine"] != "seeds":
        groups[("total", "fuzzers only")].append(r["name"] + f"|{last}")
jobs = []
for g, members in groups.items():
    for side in SIDES:
        srcs = [table[((m.split("|")[0], int(m.split("|")[1])), side)][1] for m in members]
        jobs.append((("G",) + g, side, srcs))
gt = {}
with ThreadPoolExecutor(a.jobs) as ex:
    for key, side, tot, dst in ex.map(one, jobs):
        gt[(key, side)] = (tot, dst)
for g in groups:
    result["engines"].setdefault(g[0], {})[g[1]] = {side: gt[(("G",) + g, side)][0] for side in SIDES}

# ---- the total in detail
for side in SIDES:
    prof = gt[(("G", "total", "all"), side)][1]
    if not os.path.exists(prof):
        continue
    shutil.copy(prof, os.path.join(out, f"{side}.profdata"))
    s = summary(side, prof)
    ins, oos = totals_in_scope(s)
    result["total"][side] = ins
    result["scope"][side] = oos
    # common prefix of the file names, for short paths
    names = [f["filename"] for f in s["files"]]
    prefix = os.path.commonpath(names) if len(names) > 1 else os.path.dirname(names[0]) if names else ""
    short = lambda p: os.path.relpath(p, prefix) if prefix else p
    files = []
    with open(os.path.join(out, f"files.{side}.tsv"), "w") as f:
        f.write("file\tscope\t" + "\t".join(f"{k}_covered\t{k}_total" for k in KINDS) + "\n")
        for x in sorted(s["files"], key=lambda x: x["filename"]):
            reason = next((why for rx, why in OOS if rx.search(x["filename"])), "")
            row = {"file": short(x["filename"]), "scope": reason, **{k: [x["summary"][k]["covered"], x["summary"][k]["count"]] for k in KINDS}}
            files.append(row)
            f.write(row["file"] + "\t" + (reason or "in") + "\t" + "\t".join(f"{row[k][0]}\t{row[k][1]}" for k in KINDS) + "\n")
    # ---- functions configured as out of scope: llvm-cov's own per-function numbers, taken out of the file's totals
    excl = collections.defaultdict(lambda: {k: 0 for k in KINDS})      # file -> totals to remove
    by_reason = collections.defaultdict(lambda: {k: [0, 0] for k in KINDS})
    warn = []
    if FOOS:
        srcs = [x["filename"] for x in s["files"] if not any(rx.search(x["filename"]) for rx, _ in OOS)]
        cmd = [llvm("llvm-cov"), "report", BIN[side], f"-instr-profile={prof}", "-show-functions", "-show-branch-summary"] + srcs
        rows, cur = [], None
        for line in sh(cmd).stdout.splitlines():
            m = re.match(r"File '(.*)':$", line)
            if m:
                cur = m.group(1)
                continue
            p_ = line.split()
            # Name Regions Miss Cover Lines Miss Cover Branches Miss Cover
            if cur and len(p_) == 10 and p_[0] != "TOTAL" and p_[1].isdigit() and p_[4].isdigit() and p_[7].isdigit():
                rows.append((cur, p_[0], int(p_[1]), int(p_[2]), int(p_[4]), int(p_[5]), int(p_[7]), int(p_[8])))
        names_ = sh([llvm("llvm-cxxfilt")], input="\n".join(r[1].split(":")[-1] for r in rows)).stdout.splitlines()
        seen_fn = set()
        ran = {(fn, mangled.split(":")[-1]) for fn, mangled, reg, regmiss, lin, linmiss, br, brmiss in rows if linmiss < lin}
        for (fn, mangled, reg, regmiss, lin, linmiss, br, brmiss), dem in zip(rows, names_):
            hit = next((why for frx, mrx, why in FOOS if frx.search(dem) and mrx.search(fn)), None)
            mangled = mangled.split(":")[-1]          # copies of one definition in several translation units
            if hit is None or (fn, mangled) in seen_fn:
                continue
            seen_fn.add((fn, mangled))
            if (fn, mangled) in ran:
                warn.append(f"{short(fn)}: {dem} is configured out of scope but was executed")
                continue
            e = excl[fn]
            e["functions"] += 1; e["lines"] += lin; e["regions"] += reg; e["branches"] += br
            b = by_reason[hit]
            for k, v in (("functions", 1), ("lines", lin), ("regions", reg), ("branches", br)):
                b[k][1] += v
    for row, x in zip(files, sorted(s["files"], key=lambda x: x["filename"])):
        e = excl.get(x["filename"])
        if e and not row["scope"]:
            for k in KINDS:
                row[k][1] = max(row[k][0], row[k][1] - e[k])
    result.setdefault("excluded_functions", {})[side] = {k: sum(e[k] for e in excl.values()) for k in KINDS}
    result.setdefault("warnings", {})[side] = warn
    for why, b in by_reason.items():
        result["scope"][side][why] = b
    with open(os.path.join(out, f"files.{side}.tsv"), "w") as f:
        f.write("file\tscope\t" + "\t".join(f"{k}_covered\t{k}_total" for k in KINDS) + "\n")
        for row in files:
            f.write(row["file"] + "\t" + (row["scope"] or "in") + "\t" + "\t".join(f"{row[k][0]}\t{row[k][1]}" for k in KINDS) + "\n")
    result["files"][side] = files
    # full export: functions never entered, conditions with an outcome never taken
    cmd = [llvm("llvm-cov"), "export", BIN[side], f"-instr-profile={prof}", "-skip-expansions"]
    if IGNORE:
        cmd.append(f"-ignore-filename-regex={IGNORE}")
    full = json.loads(sh(cmd).stdout)["data"][0]
    with open(os.path.join(out, f"functions.{side}.tsv"), "w") as f:
        f.write("file\tline\tregions\tfunction\tout_of_scope\n")
        seen = set()
        rows = []
        ign = re.compile(IGNORE) if IGNORE else None
        # One definition can have several records: template instantiations, and a copy of a static or inline
        # function per translation unit. It was never entered only if none of them was.
        entered = {(fn["filenames"][0], fn["regions"][0][0] if fn.get("regions") else 0)
                   for fn in full.get("functions", []) if fn["count"] and fn.get("filenames")}
        for fn in full.get("functions", []):
            if fn["count"] or not fn.get("filenames") or (ign and ign.search(fn["filenames"][0])):
                continue
            line = fn["regions"][0][0] if fn.get("regions") else 0
            key = (fn["filenames"][0], line)
            if key in seen or key in entered:
                continue
            seen.add(key)
            last = max((r[2] for r in fn.get("regions", [])), default=line)
            rows.append((short(fn["filenames"][0]), line, len(fn.get("regions", [])), fn["name"], last))
        rows.sort()
        dem = sh([llvm("llvm-cxxfilt")], input="\n".join(r[3].split(":")[-1] for r in rows)).stdout.splitlines()
        ranges = collections.defaultdict(list)   # file -> (first line, last line) of out-of-scope functions
        inscope = 0
        for row, d in zip(rows, dem + [""] * len(rows)):
            fscope = next((why for frx, mrx, why in FOOS if frx.search(d) and mrx.search(row[0])), "")
            scope = fscope or next((why for rx, why in OOS if rx.search("/" + row[0])), "")
            if fscope:
                ranges[row[0]].append((row[1], row[4]))
            inscope += not scope
            f.write("\t".join(map(str, row[:3])) + "\t" + (d or row[3]) + "\t" + scope + "\n")
        result.setdefault("uncovered_functions", {})[side] = inscope
    with open(os.path.join(out, f"branches.{side}.tsv"), "w") as f:
        f.write("file\tline\tcolumn\ttrue_count\tfalse_count\tmissing\tout_of_scope\n")
        n = collections.Counter()
        claims = UNREACH.get(side, {})
        bex = collections.Counter()          # file name -> branch outcomes taken out
        bwhy = collections.Counter()
        for x in full["files"]:
            sf = short(x["filename"])
            file_oos = any(rx.search(x["filename"]) for rx, _ in OOS)
            seen = {}
            for b in x.get("branches", []):
                key = (b[0], b[1])
                t, fl = seen.get(key, (0, 0))
                seen[key] = (t + b[4], fl + b[5])      # the same condition in several instantiations
            for (line, col), (t, fl) in sorted(seen.items()):
                claim = claims.get((sf, line, col))
                oos_ = file_oos or any(a_ <= line <= b_ for a_, b_ in ranges.get(sf, ()))
                mark = "yes" if oos_ else ""
                if claim and not oos_:
                    want = {"true": [t], "false": [fl], "both": [t, fl]}[claim[0]]
                    if any(want):   # an outcome listed as impossible was taken
                        warn.append(f"{sf}:{line}:{col}: outcome `{claim[0]}` is listed as unreachable but was taken")
                    else:
                        bex[x["filename"]] += len(want)
                        bwhy[claim[1]] += len(want)
                        mark = "unreachable"
                if t and fl:
                    continue
                missing = "both" if not t and not fl else "true" if not t else "false"
                n[missing] += not mark
                f.write(f"{sf}\t{line}\t{col}\t{t}\t{fl}\t{missing}\t{mark}\n")
        result.setdefault("partial_conditions", {})[side] = dict(n)
        # the unreachable outcomes leave the files' branch totals; the unadjusted totals are kept for the report
        result.setdefault("raw_total", {})[side] = {k: list(v) for k, v in result["total"][side].items()}
        for row, x in zip(files, sorted(s["files"], key=lambda x: x["filename"])):
            if bex.get(x["filename"]) and not row["scope"]:
                row["branches"][1] = max(row["branches"][0], row["branches"][1] - bex[x["filename"]])
        result["excluded_functions"][side]["branches"] += sum(bex.values())
        for why, cnt in bwhy.items():
            b_ = result["scope"][side].setdefault("unreachable: " + why, {k: [0, 0] for k in KINDS})
            b_["branches"][1] += cnt
        with open(os.path.join(out, f"files.{side}.tsv"), "w") as f2:
            f2.write("file\tscope\t" + "\t".join(f"{k}_covered\t{k}_total" for k in KINDS) + "\n")
            for row in files:
                f2.write(row["file"] + "\t" + (row["scope"] or "in") + "\t" + "\t".join(f"{row[k][0]}\t{row[k][1]}" for k in KINDS) + "\n")
    html = os.path.join(out, "html", side)
    cmd = [llvm("llvm-cov"), "show", BIN[side], f"-instr-profile={prof}", "-format=html", f"-output-dir={html}", "-show-branches=count",
           "-show-line-counts-or-regions", "-show-instantiations=false"]
    if IGNORE:
        cmd.append(f"-ignore-filename-regex={IGNORE}")
    sh(cmd)
# the out-of-scope functions leave every total (they are never executed, so only the denominators change)
def adjust(tot, side):
    e = result.get("excluded_functions", {}).get(side)
    if tot and e:
        for k in KINDS:
            tot[k][1] = max(tot[k][0], tot[k][1] - e[k])


for side in SIDES:
    adjust(result["total"].get(side), side)
    for grp in result["engines"].values():
        for v in grp.values():
            adjust(v.get(side), side)
    for r in result["runs"]:
        for tot in r["slices"][side]:
            adjust(tot, side)
json.dump(result, open(os.path.join(out, "coverage.json"), "w"), indent=1)
shutil.rmtree(scratch, ignore_errors=True)
if result["total"]:
    shutil.rmtree(os.path.join(out, ".shards"), ignore_errors=True)


# ---- COVERAGE.md
def pct(c):
    return "-" if not c or not c[1] else f"{100.0 * c[0] / c[1]:.1f}%"


def cell(c):
    return "-" if not c else f"{c[0]:,} / {c[1]:,} ({pct(c)})"


L = [f"# Coverage: {cfg.get('title', cfg['name'])}, campaign `{a.tag}`", ""]
L += ["Both programs were rebuilt with clang's source-based coverage and every input the fuzzers kept (their corpora) plus",
      "the seed corpora were replayed on them. A branch is one outcome (true or false) of one condition, as `llvm-cov`",
      "counts it; a condition that occurs in several template instantiations is counted once per instantiation.",
      "A run's coverage includes the seeds of its surface (every run starts from them).", ""]
L += ["## Totals", "", "| Program | Scope | Functions | Lines | Regions | Branches |", "|---|---|---|---|---|---|"]
for side in SIDES:
    t = result["total"].get(side)
    raw = result.get("raw_total", {}).get(side)
    if t:
        L.append(f"| {TITLE[side]} | reachable with the configured command line | " + " | ".join(cell(t[k]) for k in KINDS) + " |")
    if raw and raw != t:
        L.append(f"| {TITLE[side]} | all code of the configured mode, nothing taken out | " + " | ".join(cell(raw[k]) for k in KINDS) + " |")
L += ["", "\"Reachable\" leaves out the functions and branch outcomes that the target lists as impossible with this command line",
      "(`[[coverage.out_of_scope]]` functions and the `unreachable` list of `target.toml`, each with its reason; the lists and",
      "their evidence are part of the target). An entry of those lists that was executed is reported below as a warning and",
      "counted as reachable.", ""]
if any(result["scope"].get(s) for s in SIDES):
    L += ["Taken out of the reachable totals:", "", "| Program | Reason | Lines | Branches |", "|---|---|---|---|"]
    for side in SIDES:
        for reason, t in result["scope"].get(side, {}).items():
            L.append(f"| {TITLE[side]} | {reason} | {cell(t['lines'])} | {cell(t['branches'])} |")
    L.append("")
if IGNORE:
    L += [f"Files matching `{IGNORE}` are not program code under test (dependencies, unit tests) and are left out entirely.", ""]
for side in SIDES:
    for w in result.get("warnings", {}).get(side, []):
        L += [f"**Warning** ({TITLE[side]}): {w}.", ""]
pc = result.get("partial_conditions", {})
if pc:
    L += ["Conditions with an outcome never taken (`branches.<side>.tsv`):", "", "| Program | Never true | Never false | Never evaluated | Functions never entered |", "|---|---|---|---|---|"]
    for side in SIDES:
        n = pc.get(side, {})
        L.append(f"| {TITLE[side]} | {n.get('true', 0):,} | {n.get('false', 0):,} | {n.get('both', 0):,} | {result.get('uncovered_functions', {}).get(side, 0):,} |")
    L.append("")

L += ["## By directory", ""]
for side in SIDES:
    files = [f for f in result["files"].get(side, []) if not f["scope"]]
    if not files:
        continue
    agg = collections.defaultdict(lambda: {k: [0, 0] for k in KINDS})
    for f in files:
        d = os.path.dirname(f["file"]) or "."
        parts = d.split("/")
        d = "/".join(parts[:4]) if "thirdparty" in parts else "/".join(parts[:2])
        for k in KINDS:
            agg[d][k][0] += f[k][0]
            agg[d][k][1] += f[k][1]
    L += [f"**{TITLE[side]}**", "", "| Directory | Functions | Lines | Branches |", "|---|---|---|---|"]
    for d, t in sorted(agg.items()):
        L.append(f"| `{d}` | {cell(t['functions'])} | {cell(t['lines'])} | {cell(t['branches'])} |")
    L.append("")

L += ["## By fuzzer", "", "Branch coverage of each fuzzer's corpora (all its runs, every surface), of the seed corpora alone, and of everything.",
      "", "| Corpus | " + " | ".join(f"{TITLE[s]}: branches | {TITLE[s]}: lines" for s in SIDES) + " |", "|---|" + "---|" * (2 * len(SIDES))]
eng = result["engines"].get("engine", {})
order = ["seeds"] + sorted(e for e in eng if e != "seeds")
for e in order:
    if e in eng:
        L.append(f"| {e} | " + " | ".join(f"{cell(eng[e][s]['branches']) if eng[e][s] else '-'} | {pct(eng[e][s]['lines']) if eng[e][s] else '-'}" for s in SIDES) + " |")
for name in ("fuzzers only", "all"):
    t = result["engines"].get("total", {}).get(name)
    if t:
        L.append(f"| **{name}** | " + " | ".join(f"{cell(t[s]['branches']) if t[s] else '-'} | {pct(t[s]['lines']) if t[s] else '-'}" for s in SIDES) + " |")
L.append("")
es = result["engines"].get("engine_surface", {})
if es:
    surfaces = sorted({k.split(" ")[1] for k in es})
    for side in SIDES:
        L += [f"Branches reached in {TITLE[side]}, by fuzzer and surface:", "", "| Fuzzer | " + " | ".join(surfaces) + " |", "|---|" + "---|" * len(surfaces)]
        for e in order:
            L.append(f"| {e} | " + " | ".join(pct(es[f'{e} {sf}'][side]['branches']) if es.get(f"{e} {sf}") and es[f"{e} {sf}"][side] else "-" for sf in surfaces) + " |")
        L.append("")
fr = [r for r in result["runs"] if r["engine"] != "seeds" and len(r["inputs"]) > 1]
if fr:
    n = len(fr[0]["inputs"])
    heads = [f"1/{2 ** (n - 1 - k)}" if k < n - 1 else "end" for k in range(n)]
    L += ["### Coverage over time", "", "Branch coverage of one run's corpus as it was after the given fraction of the run (corpus file times).", "",
          "| Run | CPUs | Seconds | Inputs | " + " | ".join(f"{TITLE[s]} at {h}" for s in SIDES for h in heads) + " |", "|---|---|---|---|" + "---|" * (len(SIDES) * n)]
    for r in fr:
        L.append(f"| {r['engine']} {r['surface']} | {r['cpus']} | {r['dur']} | {sum(r['inputs']):,} | " +
                 " | ".join(pct(r["slices"][s][k]["branches"]) if r["slices"][s][k] else "-" for s in SIDES for k in range(n)) + " |")
    L.append("")

L += ["## By file", "", "Files of the reachable code, most missed branches first (`files.<side>.tsv` has all of them).", ""]
for side in SIDES:
    files = [f for f in result["files"].get(side, []) if not f["scope"] and f["branches"][1] + f["lines"][1] > 0]
    files.sort(key=lambda f: -(f["branches"][1] - f["branches"][0]))
    L += [f"**{TITLE[side]}**", "", "| File | Functions | Lines | Branches | Missed branches |", "|---|---|---|---|---|"]
    for f in files[:60]:
        L.append(f"| `{f['file']}` | {pct(f['functions'])} | {pct(f['lines'])} | {cell(f['branches'])} | {f['branches'][1] - f['branches'][0]:,} |")
    L.append("")
L += ["## Where to look", "", "- `html/<side>/index.html`: annotated source with line and branch counts.",
      "- `functions.<side>.tsv`: functions never entered. `branches.<side>.tsv`: conditions with an outcome never taken.",
      "- `coverage.json`: every number above, per run and time slice.", ""]
open(os.path.join(out, "COVERAGE.md"), "w").write("\n".join(L))
print(f"wrote {out}/COVERAGE.md")
for side in SIDES:
    t = result["total"].get(side)
    if t:
        print(f"  {side}: branches {cell(t['branches'])}, lines {cell(t['lines'])}, functions {cell(t['functions'])}")
