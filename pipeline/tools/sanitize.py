#!/usr/bin/env python3
"""Replay a campaign's inputs on builds that stop at memory errors and undefined behaviour, and list what they report.

  sanitize.py <target work dir> <tag> <target.toml> [--jobs N] [--chunk 64] [--phase all|replay|merge] [--shard K/N]

A comparison of outputs cannot show a program that reads out of bounds and prints the right answer anyway. The builds
bin/<side>_san (AddressSanitizer, UndefinedBehaviorSanitizer, libstdc++ assertions: steps/build.sh, flavour san) stop
at such an error and say where. Replayed are the seed corpora, the corpora of runs/<tag> and the inputs its fuzzers
stored; a tag without runs covers the seeds alone.

  replay (splits over machines: --shard K/N takes every N-th chunk of inputs)
    each program that ends by a signal is run again on that input alone and its log is read:
      asan:<error>        AddressSanitizer (heap-buffer-overflow, use-after-free, ...)
      ubsan:<message>     UndefinedBehaviorSanitizer (numbers in the message masked)
      assertion:<text>    an assertion, of the C++ library (a container used out of range) or of the program
      abort               std::terminate, abort()
      signal<N>           anything else
    and the place: the first stack frame in the program's own source.
  merge (one process)
    sanitize/<tag>/findings.tsv     one row per program, kind and place: number of inputs, surfaces, the report, an input
    sanitize/<tag>/reports/<id>.txt the sanitizer's report for that input
    sanitize/<tag>/inputs/<id>.bin  the input (./dfz replay or export shows it on the reference builds)
    sanitize/<tag>/summary.json     inputs replayed, findings per program, runs ended at the time or memory limit
                                    (5 and 4 times the configured ones; such a run shows nothing about the program)

Rows are by place because the place is the defect: every input that stops there shows the same one. A finding is a
bug of that program whether or not the outputs differ; it is handled like any other (docs/WORKFLOW.md).
"""
import argparse, collections, hashlib, json, os, re, shutil, subprocess, sys, tomllib
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import archives

ap = argparse.ArgumentParser()
ap.add_argument("work"); ap.add_argument("tag"); ap.add_argument("toml")
ap.add_argument("--jobs", type=int, default=os.cpu_count())
ap.add_argument("--chunk", type=int, default=64)
ap.add_argument("--scratch", default=None)
ap.add_argument("--phase", default="all", choices=("all", "replay", "merge"))
ap.add_argument("--shard", default="0/1")
a = ap.parse_args()
SHARD, NSHARD = (int(x) for x in a.shard.split("/"))
cfg = tomllib.load(open(a.toml, "rb"))
SIDES = [cfg["side"]["a"]["name"], cfg["side"]["b"]["name"]]
runs_dir = os.path.join(a.work, "runs", a.tag)
out = os.path.join(a.work, "sanitize", a.tag)
scratch = a.scratch or os.path.join(os.environ.get("TMPDIR", "/tmp"), f"dfz_san_{os.getpid()}")
# stop at the first error with a stack trace, plus the target's own sanitizer options (env.sh puts both together)
CHILD_ENV = os.environ.get("DFZ_SAN_CHILD_ENV") or ("ASAN_OPTIONS=abort_on_error=1:handle_abort=1:symbolize=1;"
                                                    "UBSAN_OPTIONS=halt_on_error=1:abort_on_error=1:print_stacktrace=1")
SLOWER = int(cfg["run"].get("san_slower", 5))   # the time limit is this many times the configured one
LARGER = int(cfg["run"].get("san_larger", 4))   # and the memory limit this many times (the sanitizers' own memory)
# As many replays at once as the job's memory allows when each of them reaches the memory limit of a program run:
# otherwise the machine ends processes of the job, and their results look like behaviour of the programs.
REPLAYS = a.jobs
_limit, _job = LARGER * int(cfg["run"].get("memory_mb", 0)), int(os.environ.get("SLURM_MEM_PER_NODE") or 0)
if _limit and _job and _job // _limit < a.jobs:
    REPLAYS = max(1, _job // _limit)
    print(f"{REPLAYS} replays at once instead of {a.jobs}: the job has {_job} MB and a program run may use {_limit} MB", flush=True)


def complete_shards(sd, marker):
    """the shard directories under sd, if they are the whole of one submission (each records how many shards it was one of)"""
    shards = sorted(d for d in os.listdir(sd) if os.path.isfile(os.path.join(sd, d, marker))) if os.path.isdir(sd) else []
    if not shards:
        sys.exit("no replay results under " + sd)
    of = {open(os.path.join(sd, d, "of")).read().strip() if os.path.isfile(os.path.join(sd, d, "of")) else "?" for d in shards}
    if len(of) != 1 or sorted(shards, key=int) != [str(k) for k in range(int(next(iter(of)) if next(iter(of)).isdigit() else -1))]:
        sys.exit(f"the shards under {sd} are not the whole of one submission (shards {', '.join(shards)}, each one of {', '.join(sorted(of))}): "
                 "a job failed or two submissions ran at once; run the step again")
    return shards


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def short(path, side):
    """a source path as the reader knows it: relative to the program's source tree"""
    for mark in (f"/build/{side}_san/", f"/src/{side}/"):
        if mark in path:
            return path.split(mark, 1)[1]
    return path


def read_report(log, side):
    """(kind, place, excerpt) of a log of a program that ended by a signal"""
    lines = log.splitlines()
    kind, place, start = None, "", 0
    for i, l in enumerate(lines):
        m = re.search(r"(\S+:\d+): .*Assertion [`'](.*)' failed", l)
        if m:
            kind, place, start = "assertion:" + m.group(2)[:100], m.group(1), i
            break
        m = re.search(r"(\S+:\d+:\d+): runtime error: (.*)", l)
        if m:
            kind, place, start = "ubsan:" + re.sub(r"-?\d+", "N", re.sub(r"0x[0-9a-f]+", "ADDRESS", m.group(2)))[:100], m.group(1), i
            break
        m = re.search(r"ERROR: AddressSanitizer: ([\w-]+)", l)
        if m:
            kind, start = ("abort" if m.group(1) == "ABRT" else "asan:" + m.group(1)), i
            break
    if kind is None:
        tail = [l for l in lines if l.strip()][-12:]
        return None, (re.sub(r"\d+", "N", tail[-1])[:120] if tail else ""), "\n".join(tail)
    # the first frame in the program's own source (not the compiler's runtime, the C++ library or a dependency)
    for fn, path, line in re.findall(r"#\d+ 0x[0-9a-f]+ in (.+?) (/\S+?):(\d+)", "\n".join(lines[start:])):
        if a.work in path and "/deps/" not in path:
            place = f"{short(path, side)}:{line} in {fn.split('(')[0][-80:]}"
            break
    else:
        place = short(place, side) if place else "(no frame in the program's source)"
    return kind, place, "\n".join(lines[start:start + 60])


def replay_phase():
    local = os.path.join(scratch, "local")
    shutil.rmtree(local, ignore_errors=True)
    os.makedirs(local)
    tool = os.path.join(local, "dfz_tool")
    shutil.copy2(os.path.join(a.work, "bin", "dfz_tool"), tool)
    for s in SIDES:
        shutil.copy2(os.path.join(a.work, "bin", s + "_san"), os.path.join(local, s))
    shutil.copy2(os.path.join(a.work, "harness.cfg"), os.path.join(local, "harness.cfg"))
    shutil.copytree(os.path.join(a.work, "data"), os.path.join(local, "data"), symlinks=True, ignore=shutil.ignore_patterns(".*"))
    # the inputs: seeds, corpora, stored findings; each content once per surface
    inputs = collections.defaultdict(dict)   # surface -> hash -> path
    seeds_dir = os.path.join(a.work, "seeds")
    for surface in sorted(os.listdir(seeds_dir)):
        for f in sorted(os.listdir(os.path.join(seeds_dir, surface))):
            p = os.path.join(seeds_dir, surface, f)
            inputs[surface].setdefault(sha(p), p)
    # the archives are read through, not unpacked: an input is (archive, member name) until this shard's part is known
    archived = []
    for run in sorted(os.listdir(runs_dir)) if os.path.isdir(runs_dir) else []:
        surface = run.split(".")[1] if run.count(".") >= 2 else None
        for name in ("corpus.tgz", "find.tgz"):
            tgz = os.path.join(runs_dir, run, name)
            if surface is not None and os.path.isfile(tgz):
                archived.append((surface, tgz))
    with ThreadPoolExecutor(a.jobs) as ex:
        listings = list(ex.map(lambda x: archives.listing(x[1], also=("sig.txt", "FOUND", "full")), archived))
    for (surface, tgz), listed in zip(archived, listings):
        for name, _, h in listed:
            inputs[surface].setdefault(h.hex(), (tgz, name))   # the same key as the seeds: the digest as text
    total = sum(len(v) for v in inputs.values())
    # chunks small enough that every CPU of every shard has work: a few slow inputs must not sit in one chunk
    chunk = max(1, min(a.chunk, total // (4 * NSHARD * REPLAYS) + 1))
    tasks = []
    for surface in sorted(inputs):
        files = [inputs[surface][h] for h in sorted(inputs[surface])]
        for i in range(0, len(files), chunk):
            tasks.append((surface, files[i:i + chunk], len(tasks)))
    tasks = [t for t in tasks if t[2] % NSHARD == SHARD]
    # written to the scratch directory: only what this shard replays
    need = collections.defaultdict(set)
    for _, files, _ in tasks:
        for f in files:
            if not isinstance(f, str):
                need[f[0]].add(f[1])
    unpacked = lambda tgz: os.path.join(scratch, "unpacked", os.path.basename(os.path.dirname(tgz)), os.path.basename(tgz))
    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(lambda tgz: archives.unpack(tgz, unpacked(tgz), need[tgz]), sorted(need)))
    shm = os.path.join("/dev/shm" if os.access("/dev/shm", os.W_OK) else scratch, f"dfz_san_{os.getpid()}")
    limit = str(SLOWER * int(cfg["run"].get("timeout_ms", 20000)))

    def replay(task):
        surface, files, tid = task
        files = [f if isinstance(f, str) else archives.path(unpacked(f[0]), f[1]) for f in files]
        d = os.path.join(scratch, "in", str(tid))
        os.makedirs(d)
        for i, f in enumerate(files):
            os.symlink(f, os.path.join(d, f"{i:06d}"))
        env = dict(os.environ, DFZ_A=os.path.join(local, SIDES[0]), DFZ_B=os.path.join(local, SIDES[1]), DFZ_DATA=os.path.join(local, "data"),
                   DFZ_CFG=os.path.join(local, "harness.cfg"), DFZ_SURFACE=surface, DFZ_WORK=os.path.join(shm, str(tid)),
                   DFZ_CHILD_ENV=CHILD_ENV, DFZ_TIMEOUT_MS=limit)
        if int(cfg["run"].get("memory_mb", 0)):
            env["DFZ_MEMORY_MB"] = str(LARGER * int(cfg["run"]["memory_mb"]))
        for v in ("DFZ_A_MAP", "DFZ_B_MAP", "DFZ_FIND", "DFZ_NOFEEDBACK", "DFZ_KEEP"):
            env.pop(v, None)
        res = sh([tool, "run", d], env=env).stdout
        m = re.search(r"inputs=(\d+) invalid=(\d+)", res)
        n = int(m.group(1)) - int(m.group(2)) if m else 0
        # runs the harness ended at a limit say nothing about the program: counted, not reported
        ended = collections.Counter()
        for classes, k in re.findall(r" (\S+=\S+,\S+=\S+):(\d+)", res):
            for side_cls in classes.split(","):
                side, cls = side_cls.split("=", 1)
                if cls in ("timeout", "memory"):
                    ended[f"{side} {cls}"] += int(k)
        found = []
        if re.search(r"=sig\d+", res):   # a program ended by a signal on some input of the chunk: each input alone
            for f in files:
                o = sh([tool, "replay", f], env=dict(env, DFZ_KEEP="1")).stdout
                cls = re.findall(r"^  -> (\S+) \(exit", o, re.M)
                case = re.search(r"^case directory: (\S+)", o, re.M)
                for k, side in enumerate(SIDES):
                    if len(cls) == 2 and cls[k].startswith("sig") and case:
                        try:
                            log = open(os.path.join(case.group(1), "ab"[k] + ".log"), errors="replace").read()
                        except OSError:
                            log = ""
                        kind, place, excerpt = read_report(log, side)
                        found.append({"side": side, "kind": kind or cls[k].replace("sig", "signal"), "place": place, "report": excerpt, "input": f, "surface": surface})
                if case:
                    shutil.rmtree(os.path.dirname(case.group(1)), ignore_errors=True)
        shutil.rmtree(d, ignore_errors=True)
        return n, found, ended

    shard_dir = os.path.join(out, ".shards", str(SHARD))
    shutil.rmtree(shard_dir, ignore_errors=True)
    os.makedirs(shard_dir + "/inputs")
    n, by, ended = 0, {}, collections.Counter()
    with ThreadPoolExecutor(REPLAYS) as ex:
        for k, found, e in ex.map(replay, tasks):
            n += k
            ended.update(e)
            for f in found:
                key = hashlib.sha1(f"{f['side']}|{f['kind']}|{f['place']}".encode()).hexdigest()[:12]
                e = by.setdefault(key, {"side": f["side"], "kind": f["kind"], "place": f["place"], "report": f["report"], "inputs": 0, "surfaces": {}, "surface": f["surface"]})
                e["inputs"] += 1
                e["surfaces"][f["surface"]] = e["surfaces"].get(f["surface"], 0) + 1
                if e["inputs"] == 1:
                    shutil.copy(f["input"], os.path.join(shard_dir, "inputs", key + ".bin"))
    json.dump({"total": total, "replayed": n, "ended": ended, "findings": by}, open(os.path.join(shard_dir, "result.json"), "w"))
    open(os.path.join(shard_dir, "of"), "w").write(str(NSHARD))
    shutil.rmtree(shm, ignore_errors=True)
    print(f"shard {SHARD}/{NSHARD}: {n} of {total} inputs replayed, {len(by)} kinds of report", flush=True)


def merge_phase():
    sd = os.path.join(out, ".shards")
    shards = complete_shards(sd, "result.json")
    for f in ("inputs", "reports", "findings.tsv", "summary.json"):
        p = os.path.join(out, f)
        shutil.rmtree(p) if os.path.isdir(p) else os.path.exists(p) and os.remove(p)
    os.makedirs(os.path.join(out, "inputs"))
    os.makedirs(os.path.join(out, "reports"))
    by, replayed, total, ended = {}, 0, 0, collections.Counter()
    for d in shards:
        r = json.load(open(os.path.join(sd, d, "result.json")))
        replayed += r["replayed"]
        ended.update(r.get("ended", {}))
        total = r["total"]
        for key, e in r["findings"].items():
            if key not in by:
                by[key] = e
                shutil.copy(os.path.join(sd, d, "inputs", key + ".bin"), os.path.join(out, "inputs", key + ".bin"))
                open(os.path.join(out, "reports", key + ".txt"), "w").write(f"{e['side']}  {e['kind']}  {e['place']}\nsurface {e['surface']}, input inputs/{key}.bin\n\n{e['report']}\n")
            else:
                by[key]["inputs"] += e["inputs"]
                for s, k in e["surfaces"].items():
                    by[key]["surfaces"][s] = by[key]["surfaces"].get(s, 0) + k
    rows = sorted(by.items(), key=lambda kv: (kv[1]["side"], -kv[1]["inputs"]))
    with open(os.path.join(out, "findings.tsv"), "w") as f:
        f.write("program\tkind\tplace\tinputs\tsurfaces\treport\tinput\tsurface_of_input\n")
        for key, e in rows:
            f.write("\t".join([e["side"], e["kind"], e["place"], str(e["inputs"]), " ".join(f"{s}:{k}" for s, k in sorted(e["surfaces"].items())),
                               f"reports/{key}.txt", f"inputs/{key}.bin", e["surface"]]) + "\n")
    summary = {"inputs": total, "replayed": replayed, "ended_at_a_limit": dict(sorted(ended.items())), "findings": len(by),
               "by_program": {s: sum(1 for e in by.values() if e["side"] == s) for s in SIDES}}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    shutil.rmtree(sd, ignore_errors=True)
    print(f"{replayed} of {total} inputs replayed on the sanitizer builds: " + ", ".join(f"{s} {k} kinds of report" for s, k in summary["by_program"].items())
          + (f"; runs ended at a limit: {dict(sorted(ended.items()))}" if ended else "") + f" -> {out}/findings.tsv")


if a.phase == "all":
    shutil.rmtree(out, ignore_errors=True)
os.makedirs(out, exist_ok=True)
os.makedirs(scratch, exist_ok=True)
if a.phase in ("all", "replay"):
    replay_phase()
if a.phase in ("all", "merge"):
    merge_phase()
shutil.rmtree(scratch, ignore_errors=True)
