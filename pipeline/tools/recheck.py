#!/usr/bin/env python3
"""Replay every difference a campaign stored and list the inputs on which the two programs still differ.

  recheck.py <target work dir> <tag> <target.toml> [--jobs N] [--repeat 3] [--phase all|replay|merge] [--shard K/N]

Everything runs on the reference builds (bin/<side>_ref), never on the instrumented ones, under the current
configuration: the adapter's input constraints, the [[exempt]] rules, the comparators. A side with temporary patches
([side.<x>] patches in target.toml) runs in its patched reference build (bin/<side>_refp), so what is listed is what
still differs with the known bugs patched away.

  replay (splits over machines: --shard K/N takes the signatures whose hash is K modulo N)
    unpack runs/<tag>/*/find.tgz; replay up to two stored inputs per signature `repeat` times:
      differs      a difference in the same outputs every time (the signature shown is the replayed one)
      flaky        only in some replays
      gone         in none: removed by a constraint, an exemption's cause, a normalisation or a patch, or it never
                   existed on the reference builds
  merge (one process)
    recheck/<tag>/remaining.tsv   the inputs that still differ, not exempt first: status, signature, exempt reason,
                                  which fuzzers stored it, the input (recheck/<tag>/inputs/<name>.bin)
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
ap.add_argument("--scratch", default=None)
ap.add_argument("--phase", default="all", choices=("all", "replay", "merge"))
ap.add_argument("--shard", default="0/1")
a = ap.parse_args()
SHARD, NSHARD = (int(x) for x in a.shard.split("/"))
cfg = tomllib.load(open(a.toml, "rb"))
A, B = cfg["side"]["a"]["name"], cfg["side"]["b"]["name"]
runs = os.path.join(a.work, "runs", a.tag)
out = os.path.join(a.work, "recheck", a.tag)
scratch = a.scratch or os.path.join(os.environ.get("TMPDIR", "/tmp"), f"dfz_recheck_{os.getpid()}")
RULES = [(re.compile(r["match"]), r.get("reason", "")) for r in cfg.get("exempt", [])]
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
    for k in ("DFZ_A_MAP", "DFZ_B_MAP", "DFZ_FIND", "DFZ_NOFEEDBACK"):
        env0.pop(k, None)
    slots = list(range(a.jobs))
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

    def one(item):
        sig, s = item
        slot = slots.pop()
        try:
            surface, _, atoms = split_sig(sig)
            env = dict(env0, DFZ_SURFACE=s["surface"], DFZ_WORK=os.path.join(scratch, "work", str(slot)))
            best = (0, None, "")
            for inp in s["inputs"][:2]:
                same, seen = 0, collections.Counter()
                for _ in range(a.repeat):
                    lines = subprocess.run([os.path.join(local, "dfz_tool"), "sig", inp], env=env, capture_output=True, text=True, errors="replace").stdout.splitlines()
                    got = [l.split("\t", 1)[1] for l in lines if "\t" in l and not l.endswith("\t-") and not l.endswith("\tINVALID")]
                    alike = [g for g in got if split_sig(g)[2] == atoms] or [g for g in got if kind(split_sig(g)[2]) == kind(atoms)]
                    same += bool(alike)
                    seen.update(alike[:1])
                if same > best[0]:
                    best = (same, inp, seen.most_common(1)[0][0])
                if same == a.repeat:
                    break
            same, inp, now = best
            return sig, ("differs" if same == a.repeat else "flaky" if same else "gone"), inp, now
        finally:
            slots.append(slot)

    with ThreadPoolExecutor(a.jobs) as ex:
        for sig, status, inp, now in ex.map(one, sigs.items()):
            s = sigs[sig]
            s["status"], s["now"] = status, now
            if inp and status != "gone":
                s["input"] = hashlib.sha1(sig.encode()).hexdigest()[:16] + ".bin"
                shutil.copy(inp, os.path.join(shard_dir, "inputs", s["input"]))
            del s["inputs"]
    json.dump({"total": total, "sigs": sigs}, open(os.path.join(shard_dir, "result.json"), "w"))
    print(f"shard {SHARD}/{NSHARD}: {len(sigs)} of {total} stored signatures:", dict(collections.Counter(s["status"] for s in sigs.values())), flush=True)


def merge_phase():
    sd = os.path.join(out, ".shards")
    shards = sorted(d for d in os.listdir(sd) if os.path.isfile(os.path.join(sd, d, "result.json")))
    if not shards:
        sys.exit("no replay results under " + sd)
    for f in ("inputs", "remaining.tsv", "summary.json"):
        p = os.path.join(out, f)
        shutil.rmtree(p) if os.path.isdir(p) else os.path.exists(p) and os.remove(p)
    os.makedirs(os.path.join(out, "inputs"))
    rows, count = [], collections.Counter()
    for d in shards:
        for sig, s in json.load(open(os.path.join(sd, d, "result.json")))["sigs"].items():
            count[s["status"]] += 1
            if s["status"] == "gone":
                continue
            now = s["now"] or sig
            exempt = next((reason for rx, reason in RULES if rx.search(now) or rx.search(sig)), "")
            shutil.copy(os.path.join(sd, d, "inputs", s["input"]), os.path.join(out, "inputs", s["input"]))
            rows.append((bool(exempt), s["status"] != "differs", now, s["status"], exempt, " ".join(f"{e}:{t}" for e, t in sorted(s["engines"].items())), s["input"], s["surface"]))
            count["exempt" if exempt else "to account for"] += s["status"] == "differs"
    rows.sort()
    with open(os.path.join(out, "remaining.tsv"), "w") as f:
        f.write("status\tsurface\tsignature\texempt\tfuzzers(first_seconds)\tinput\n")
        for _, _, now, status, exempt, engines, inp, surface in rows:
            f.write("\t".join([status, surface, now, exempt, engines, "inputs/" + inp]) + "\n")
    summary = {"stored": sum(count[k] for k in ("differs", "flaky", "gone")), "differs": count["differs"], "flaky": count["flaky"], "gone": count["gone"],
               "to_account_for": count["to account for"], "exempt": count["exempt"], "patched": PATCHED}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    shutil.rmtree(sd, ignore_errors=True)
    print(f"stored {summary['stored']}: still differ {summary['differs']} ({summary['to_account_for']} to account for, {summary['exempt']} exempt), "
          f"flaky {summary['flaky']}, gone {summary['gone']}" + (f"; patched builds of {', '.join(PATCHED)}" if PATCHED else "") + f" -> {out}/remaining.tsv")


if a.phase == "all":
    shutil.rmtree(out, ignore_errors=True)
os.makedirs(out, exist_ok=True)
os.makedirs(scratch, exist_ok=True)
if a.phase in ("all", "replay"):
    replay_phase()
if a.phase in ("all", "merge"):
    merge_phase()
shutil.rmtree(scratch, ignore_errors=True)
