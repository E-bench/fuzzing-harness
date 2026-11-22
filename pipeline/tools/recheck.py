#!/usr/bin/env python3
"""Replay every difference a campaign stored and list the inputs on which the two programs still differ.

  recheck.py <target work dir> <tag> <target.toml> [--jobs N] [--repeat 3] [--slow-factor 10]
             [--phase all|replay|merge] [--shard K/N]

Everything runs on the reference builds (bin/<side>_ref), never on the instrumented ones, under the current
configuration: the adapter (its input constraints, its comparison) and the comparators. A side with temporary patches
([side.<x>] patches in target.toml) runs in its patched reference build (bin/<side>_refp), so what is listed is what
still differs with the known bugs patched away.

  replay (splits over machines: --shard K/N takes the signatures whose hash is K modulo N)
    unpack runs/<tag>/*/find.tgz; replay up to two stored inputs per signature `repeat` times:
      differs      a difference in the same outputs every time (the signature shown is the replayed one)
      flaky        only in some replays
      fuzz-only    not on the reference builds every time, but every time on the instrumented builds the fuzzers ran
                   (bin/<side>_fuzz, or _fuzzsan for a campaign run with sanitizers): the difference depends on the
                   build (compiler, flags), which is a finding to look at, not noise
      report       the same, where the signature is a program's death with a report of a sanitizer or of a failed
                   assertion (atoms `report_<side>:[...]`): a memory error or undefined behaviour in that program,
                   whether or not the other program has it too. To account for, like a difference.
      timeout:<side>       the difference is that this program passed the time limit and the other did not. A time
                   limit is the harness's doing, so these are a class of their own, looked at after the differences
                   in output; they are not dropped. (A program that ends in an error early while the other is still
                   computing towards the same error looks like a difference at a short limit.) Such an input is
                   replayed once more with `slow-factor` times the limit, and the seconds each program took are
                   recorded: both end and agree -> `timeout:<side>`; the program passes that limit too ->
                   `timeout-long:<side>` (a very slow case or a hang); another difference shows -> `differs` with
                   that difference.
                   The side matters. The original passing the limit where the rewrite does not is put off
                   (`deferred` in the counts). The rewrite passing it where the original does not counts with the
                   differences to account for: a rewrite must not be that much slower than what it replaces, and
                   may be hanging.
                   Because of that, an input on which the rewrite passed the limit is replayed again at the end,
                   with nothing else running in the job: wall time under the load of the other replays is not a
                   program's speed. No difference then -> `gone` (counted as `gone_timeout_under_load`); otherwise
                   the status and the seconds are those of the replay alone (marked "alone").
                   The seconds come with the CPU time of each program: a program that took long on the clock and
                   little CPU time was kept waiting, by the machine or by itself; one that is slower took more of both.
      memory:<side>        the difference is that this program passed the memory limit ([run] memory_mb) and the other
                   did not: the harness's other limit, with the same rule for the side
      gone         in none: removed by a constraint, an exemption, a normalisation or a patch, or an effect
                   of load or of undefined behaviour that does not repeat
  merge (one process)
    recheck/<tag>/remaining.tsv   the inputs that still differ: status, signature,
                                  which fuzzers stored it, the input (recheck/<tag>/inputs/<name>.bin), the
                                  seconds of both programs at the long limit; timeouts of the original are last
    recheck/<tag>/summary.json    the counts

Nothing is grouped here. The way on is to take one input of remaining.tsv, find the cause, act on it
(docs/WORKFLOW.md) and run this again. To look at one: `./dfz replay` or `./dfz export`.
"""
import argparse, collections, hashlib, json, os, re, shutil, subprocess, sys, tarfile, tomllib
from concurrent.futures import ThreadPoolExecutor

ap = argparse.ArgumentParser()
ap.add_argument("work"); ap.add_argument("tag"); ap.add_argument("toml")
ap.add_argument("--jobs", type=int, default=os.cpu_count())
ap.add_argument("--repeat", type=int, default=3)
ap.add_argument("--slow-factor", type=int, default=10)
ap.add_argument("--scratch", default=None)
ap.add_argument("--phase", default="all", choices=("all", "replay", "merge"))
ap.add_argument("--shard", default="0/1")
a = ap.parse_args()
SHARD, NSHARD = (int(x) for x in a.shard.split("/"))
cfg = tomllib.load(open(a.toml, "rb"))
# As many replays at once as the job's memory allows when each of them reaches the memory limit of a program run:
# otherwise the machine ends processes of the job, and their results look like behaviour of the programs.
REPLAYS = a.jobs
SAN_SLOWER, SAN_LARGER = int(cfg["run"].get("san_slower", 5)), int(cfg["run"].get("san_larger", 4))
_limit, _job = (SAN_LARGER if cfg["run"].get("sanitizers") else 1) * int(cfg["run"].get("memory_mb", 0)), int(os.environ.get("SLURM_MEM_PER_NODE") or 0)
if _limit and _job and _job // _limit < a.jobs:
    REPLAYS = max(1, _job // _limit)
    print(f"{REPLAYS} replays at once instead of {a.jobs}: the job has {_job} MB and a program run may use {_limit} MB", flush=True)
A, B = cfg["side"]["a"]["name"], cfg["side"]["b"]["name"]
runs = os.path.join(a.work, "runs", a.tag)
out = os.path.join(a.work, "recheck", a.tag)
scratch = a.scratch or os.path.join(os.environ.get("TMPDIR", "/tmp"), f"dfz_recheck_{os.getpid()}")
LONG_MS = a.slow_factor * int(cfg["run"].get("timeout_ms", 20000))
TIMEOUT = re.compile(r"=timeout\b")   # the exit atom of a run that passed the time limit
TIMEOUT_SIDE = re.compile(r"([^\s,:=]+)=timeout\b")
MEMORY = re.compile(r"=memory\b")     # the exit atom of a run that passed the memory limit
MEMORY_SIDE = re.compile(r"([^\s,:=]+)=memory\b")
PATCHED = [s for s in (A, B) if os.path.exists(os.path.join(a.work, "bin", s + "_refp"))]


def split_sig(sig):
    """'surface [tags] atoms' -> (surface, tags, atoms)"""
    m = re.match(r"(\S+) \[(.*?)\] (.*)$", sig, re.S)
    return (m.group(1), m.group(2), m.group(3)) if m else ("?", "", sig)


def replay_phase():
    # private copies of the binaries, the configuration and the data: a rebuild or a deployment during the run must
    # not change what is being run
    local = os.path.join(scratch, "local")
    shutil.rmtree(local, ignore_errors=True)
    os.makedirs(local)
    shutil.copy2(os.path.join(a.work, "bin", "dfz_tool"), os.path.join(local, "dfz_tool"))
    for s in (A, B):
        shutil.copy2(os.path.join(a.work, "bin", s + ("_refp" if s in PATCHED else "_ref")), os.path.join(local, s))
    shutil.copy2(os.path.join(a.work, "harness.cfg"), os.path.join(local, "harness.cfg"))
    shutil.copytree(os.path.join(a.work, "data"), os.path.join(local, "data"), symlinks=True, ignore=shutil.ignore_patterns(".*"))
    env0 = dict(os.environ, DFZ_A=os.path.join(local, A), DFZ_B=os.path.join(local, B), DFZ_DATA=os.path.join(local, "data"),
                DFZ_CFG=os.path.join(local, "harness.cfg"))
    # the instrumented builds the fuzzers ran, for what does not show on the reference builds: those with sanitizers
    # (fuzzsan) when a run of the campaign used them (meta.txt), with their options and scaled limits
    def meta(run):
        p = os.path.join(runs, run, "meta.txt")
        return dict(l.strip().split("=", 1) for l in open(p) if "=" in l) if os.path.isfile(p) else {}
    sanitized = any(meta(run).get("sanitizers") == "1" for run in os.listdir(runs))
    flavour = "_fuzzsan" if sanitized else "_fuzz"
    fuzz = all(os.path.exists(os.path.join(a.work, "bin", s + flavour)) for s in (A, B))
    if fuzz:
        for s in (A, B):
            shutil.copy2(os.path.join(a.work, "bin", s + flavour), os.path.join(local, s + "_fuzz"))
    fuzz_env = {}
    if sanitized:
        fuzz_env = {"DFZ_CHILD_ENV": os.environ.get("DFZ_SAN_CHILD_ENV", ""), "DFZ_TIMEOUT_MS": str(SAN_SLOWER * int(cfg["run"].get("timeout_ms", 20000)))}
        if int(cfg["run"].get("memory_mb", 0)):
            fuzz_env["DFZ_MEMORY_MB"] = str(SAN_LARGER * int(cfg["run"]["memory_mb"]))
    for k in ("DFZ_A_MAP", "DFZ_B_MAP", "DFZ_FIND", "DFZ_NOFEEDBACK"):
        env0.pop(k, None)
    slots = list(range(REPLAYS))
    shard_dir = os.path.join(out, ".shards", str(SHARD))
    shutil.rmtree(shard_dir, ignore_errors=True)
    os.makedirs(shard_dir + "/inputs")
    raw = os.path.join(scratch, "raw")
    sigs, total = {}, 0   # stored signature -> {engines: {engine: first seconds}, inputs, surface}
    for run in sorted(os.listdir(runs)):
        tgz = os.path.join(runs, run, "find.tgz")
        if not os.path.isfile(tgz):
            continue
        engine, surface = run.split(".")[0], run.split(".")[1]
        with tarfile.open(tgz) as t:
            t.extractall(os.path.join(raw, run), filter="data")
        fd = os.path.join(raw, run, "find")
        for h in sorted(os.listdir(fd)) if os.path.isdir(fd) else []:
            d = os.path.join(fd, h)
            try:
                sig = open(os.path.join(d, "sig.txt"), errors="replace").read().split("\n", 1)[0]
            except OSError:
                continue
            total += 1
            if int(hashlib.sha1(sig.encode()).hexdigest()[:8], 16) % NSHARD != SHARD:
                continue
            try:
                found = int(open(os.path.join(d, "FOUND")).read().strip())
            except (OSError, ValueError):
                found = -1
            s = sigs.setdefault(sig, {"engines": {}, "inputs": [], "surface": surface})
            s["engines"][engine] = found if engine not in s["engines"] or s["engines"][engine] < 0 else s["engines"][engine] if found < 0 else min(found, s["engines"][engine])
            s["inputs"] += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".bin")]

    def kind(atoms):   # which outputs a finding is about: the atoms' names up to the first colon
        return frozenset(x.split(":", 1)[0] for x in atoms.split())

    def long_replay(inp, env):   # one replay with the long limit: the differences, and both programs' seconds
        o = subprocess.run([os.path.join(local, "dfz_tool"), "replay", inp], env=dict(env, DFZ_TIMEOUT_MS=str(LONG_MS)), capture_output=True, text=True, errors="replace").stdout
        ms = re.findall(r"^  -> \S+ \(exit -?\d+, (\d+) ms, \d+ MB, (\d+) ms of CPU", o, re.M)
        return re.findall(r"^FINDING (.*)$", o, re.M), ", ".join(f"{n} {int(m) / 1000:.1f} s ({int(c) / 1000:.1f} s of CPU)" for n, (m, c) in zip((A, B), ms))

    def differences(inp, env):   # the signatures of the differences one replay shows
        lines = subprocess.run([os.path.join(local, "dfz_tool"), "sig", inp], env=env, capture_output=True, text=True, errors="replace").stdout.splitlines()
        return [l.split("\t", 1)[1] for l in lines if "\t" in l and not l.endswith("\t-") and not l.endswith("\tINVALID")]

    def at_long_limit(inp, env, side):   # (status, signature or None, seconds) of an input on which `side` passed the time limit
        got, took = long_replay(inp, env)
        if not got:
            return "timeout:" + side, None, took
        if not all(TIMEOUT.search(g) for g in got):
            return "differs", next(g for g in got if not TIMEOUT.search(g)), took   # what the limit was hiding
        return "timeout-long:" + "+".join(TIMEOUT_SIDE.findall(got[0])), got[0], took

    def one(item):
        sig, s = item
        slot = slots.pop()
        try:
            surface, _, atoms = split_sig(sig)
            env = dict(env0, DFZ_SURFACE=s["surface"], DFZ_WORK=os.path.join(scratch, "work", str(slot)))

            def replays(inp, env):   # in how many of `repeat` replays the difference shows, and as which signature
                same, seen = 0, collections.Counter()
                for _ in range(a.repeat):
                    got = differences(inp, env)
                    alike = [g for g in got if split_sig(g)[2] == atoms] or [g for g in got if kind(split_sig(g)[2]) == kind(atoms)]
                    same += bool(alike)
                    seen.update(alike[:1])
                return same, (seen.most_common(1)[0][0] if seen else "")

            best = (0, None, "")
            for inp in s["inputs"][:2]:
                same, now = replays(inp, env)
                if same > best[0]:
                    best = (same, inp, now)
                if same == a.repeat:
                    break
            same, inp, now = best
            status = "differs" if same == a.repeat else "flaky" if same else "gone"
            if status != "differs" and fuzz and s["inputs"]:
                fenv = dict(env, DFZ_A=os.path.join(local, A + "_fuzz"), DFZ_B=os.path.join(local, B + "_fuzz"), **fuzz_env)
                fsame, fnow = replays(inp or s["inputs"][0], fenv)
                if fsame == a.repeat:
                    status, inp, now, env = "fuzz-only", inp or s["inputs"][0], fnow, fenv
                    if all(k.startswith("report_") for k in kind(split_sig(fnow)[2])):
                        status = "report"   # a sanitizer's or an assertion's report, not a difference between the programs
            took = ""
            if status != "gone" and a.slow_factor > 1 and TIMEOUT.search(now or sig):
                # a passed time limit is the harness's doing, not behaviour of a program: once more with a long limit
                status, shown, took = at_long_limit(inp, env, "+".join(TIMEOUT_SIDE.findall(now or sig)))
                now = shown or now
            if status in ("differs", "fuzz-only") and MEMORY.search(now):
                status = "memory:" + "+".join(MEMORY_SIDE.findall(now))   # the other limit of the harness
            return sig, status, inp, now, took, env["DFZ_A"] != env0["DFZ_A"]
        finally:
            slots.append(slot)

    with ThreadPoolExecutor(REPLAYS) as ex:
        for sig, status, inp, now, took, on_fuzz_builds in ex.map(one, sigs.items()):
            s = sigs[sig]
            s["status"], s["now"], s["took"], s["fuzz_builds"] = status, now, took, on_fuzz_builds
            if inp and status != "gone":
                s["input"] = hashlib.sha1(sig.encode()).hexdigest()[:16] + ".bin"
                shutil.copy(inp, os.path.join(shard_dir, "inputs", s["input"]))
            del s["inputs"]
    # A time limit that the rewrite passed is decided again with nothing else running in this job. Wall time under the
    # load of the other replays is not a program's speed, and a program that spreads its work over threads loses more
    # of it to that load than one that does not.
    for sig, s in sigs.items():
        if not s["status"].startswith("timeout") or B not in s["status"].split(":", 1)[1].split("+"):
            continue
        inp = os.path.join(shard_dir, "inputs", s["input"])
        env = dict(env0, DFZ_SURFACE=s["surface"], DFZ_WORK=os.path.join(scratch, "work", "alone"))
        if s["fuzz_builds"]:
            env.update(DFZ_A=os.path.join(local, A + "_fuzz"), DFZ_B=os.path.join(local, B + "_fuzz"), **fuzz_env)
        got = differences(inp, env)   # at the configured limit
        if not got:
            s["status"], s["under_load"] = "gone", True
        elif not any(TIMEOUT.search(g) for g in got):
            s["status"], s["now"], s["took"] = "differs", got[0], ""
        else:
            s["status"], shown, s["took"] = at_long_limit(inp, env, "+".join(TIMEOUT_SIDE.findall(got[0])))
            s["now"], s["took"] = shown or got[0], s["took"] + " (alone)"
    json.dump({"total": total, "sigs": sigs}, open(os.path.join(shard_dir, "result.json"), "w"))
    open(os.path.join(shard_dir, "of"), "w").write(str(NSHARD))
    print(f"shard {SHARD}/{NSHARD}: {len(sigs)} of {total} stored signatures:", dict(collections.Counter(s["status"] for s in sigs.values())), flush=True)


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


def merge_phase():
    sd = os.path.join(out, ".shards")
    shards = complete_shards(sd, "result.json")
    for f in ("inputs", "remaining.tsv", "summary.json"):
        p = os.path.join(out, f)
        shutil.rmtree(p) if os.path.isdir(p) else os.path.exists(p) and os.remove(p)
    os.makedirs(os.path.join(out, "inputs"))
    rows, count = [], collections.Counter()
    for d in shards:
        for sig, s in json.load(open(os.path.join(sd, d, "result.json")))["sigs"].items():
            if not s["status"].startswith(("timeout", "memory")):
                count[s["status"]] += 1
            count["under load"] += bool(s.get("under_load"))
            if s["status"] == "gone":
                continue
            now = s["now"] or sig
            shutil.copy(os.path.join(sd, d, "inputs", s["input"]), os.path.join(out, "inputs", s["input"]))
            st = s["status"]
            # the original alone passing a limit is put off; the rewrite passing it is to account for
            deferred = st.startswith(("timeout", "memory")) and st.split(":", 1)[1] == A
            order = (8 if st.startswith("timeout-long") else 9) if deferred else {"differs": 0, "fuzz-only": 1, "flaky": 5}.get(st, 1 if st == "report" else 2 if st.startswith("timeout-long") else 3)
            rows.append((order, now, st, " ".join(f"{e}:{t}" for e, t in sorted(s["engines"].items())), s["input"], s["surface"], s.get("took", "")))
            if st.startswith("timeout"):
                count["timeout"] += 1
                count["timeout long"] += st.startswith("timeout-long")
                count["timeout deferred"] += deferred
            if st.startswith("memory"):
                count["memory"] += 1
                count["memory deferred"] += deferred
            if st != "flaky" and not deferred:
                count["to account for"] += 1
    rows.sort()
    with open(os.path.join(out, "remaining.tsv"), "w") as f:
        f.write("status\tsurface\tsignature\tfuzzers(first_seconds)\tinput\tat_long_limit\n")
        for _, now, status, engines, inp, surface, took in rows:
            f.write("\t".join([status, surface, now, engines, "inputs/" + inp, took]) + "\n")
    summary = {"stored": sum(count[k] for k in ("differs", "fuzz-only", "report", "flaky", "timeout", "memory", "gone")), "differs": count["differs"], "fuzz_only": count["fuzz-only"],
               "report": count["report"],
               "flaky": count["flaky"], "timeout": count["timeout"], "timeout_long": count["timeout long"], "timeout_deferred": count["timeout deferred"],
               "memory": count["memory"], "memory_deferred": count["memory deferred"],
               "gone": count["gone"], "gone_timeout_under_load": count["under load"], "long_limit_ms": LONG_MS, "to_account_for": count["to account for"], "patched": PATCHED}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    shutil.rmtree(sd, ignore_errors=True)
    print(f"stored {summary['stored']}: {summary['to_account_for']} to account for, {summary['timeout_deferred'] + summary['memory_deferred']} put off (only the original passed a limit), "
          f"flaky {summary['flaky']}, gone {summary['gone']} ({summary['gone_timeout_under_load']} of them a time limit the rewrite passed only while other replays ran); of all: differ {summary['differs']}, only on the instrumented builds {summary['fuzz_only']}, sanitizer or assertion reports {summary['report']}, "
          f"timeouts {summary['timeout']} ({summary['timeout_long']} also at {LONG_MS // 1000} s), memory limit {summary['memory']}" + (f"; patched builds of {', '.join(PATCHED)}" if PATCHED else "") + f" -> {out}/remaining.tsv")

if a.phase == "all":
    shutil.rmtree(out, ignore_errors=True)
os.makedirs(out, exist_ok=True)
os.makedirs(scratch, exist_ok=True)
if a.phase in ("all", "replay"):
    replay_phase()
if a.phase in ("all", "merge"):
    merge_phase()
shutil.rmtree(scratch, ignore_errors=True)
