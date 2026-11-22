#!/usr/bin/env python3
"""Check that a campaign and its post-processing produced everything, and that the numbers are plausible.

  check.py <target work dir> <tag> <target.toml> <engines dir>

Prints one line per check (ok / FAIL / warn) and exits 1 when a check fails. Run it on a short campaign after any
change to the pipeline, before spending CPU hours on a long one.
"""
import json, os, statistics, sys, tarfile, tomllib

work, tag, toml, engines_dir = sys.argv[1:5]
cfg = tomllib.load(open(toml, "rb"))
sides = [cfg["side"]["a"]["name"], cfg["side"]["b"]["name"]]
failed = 0


def check(ok, what, detail="", warn=False):
    global failed
    print(f"{'ok  ' if ok else 'warn' if warn else 'FAIL'}  {what}{': ' + detail if detail and not ok else ''}")
    if not ok and not warn:
        failed += 1


# ---- runs
runs_dir = os.path.join(work, "runs", tag)
runs = {}
for d in sorted(os.listdir(runs_dir)) if os.path.isdir(runs_dir) else []:
    p = os.path.join(runs_dir, d)
    if os.path.isfile(os.path.join(p, "meta.txt")):
        runs[d] = dict(l.strip().split("=", 1) for l in open(os.path.join(p, "meta.txt")) if "=" in l)
engines = sorted(f[:-3] for f in os.listdir(engines_dir) if f.endswith(".sh"))
surfaces = sorted(cfg["surface"])
have = {(m["engine"], m["surface"]) for m in runs.values()}
missing = [f"{e}.{s}" for e in engines for s in surfaces if (e, s) not in have]
check(bool(runs), "the campaign has runs", runs_dir)
check(not missing, "every fuzzer ran on every surface", "missing " + " ".join(missing), warn=True)
rate = {}
for d, m in runs.items():
    p = os.path.join(runs_dir, d)
    rows = [l.split("\t") for l in open(os.path.join(p, "stats.tsv")).read().splitlines()[1:]] if os.path.isfile(os.path.join(p, "stats.tsv")) else []
    rows = [r for r in rows if len(r) == 4 and all(x.strip().lstrip("-").isdigit() for x in r)]
    last = [int(x) for x in rows[-1]] if rows else [0, 0, 0, 0]
    problems = []
    if "end" not in m:
        problems.append("did not finish (no end time)")
    for f in ("find.tgz", "corpus.tgz", "harness.cfg"):
        if not os.path.isfile(os.path.join(p, f)) or os.path.getsize(os.path.join(p, f)) == 0:
            problems.append(f"no {f}")
    if not rows:
        problems.append("no progress rows")
    if last[1] <= 0:
        problems.append("the fuzzer reported no executions")
    if last[2] <= int(m.get("seeds", 0)) // 2:
        problems.append(f"corpus of {last[2]} files (seeds: {m.get('seeds')})")
    try:
        with tarfile.open(os.path.join(p, "corpus.tgz")) as t:
            n = sum(1 for x in t if x.isfile())
        if n < 10:
            problems.append(f"corpus archive holds {n} files")
    except (OSError, tarfile.TarError):
        pass
    check(not problems, f"run {d}", "; ".join(problems))
    if last[0] and int(m.get("cpus", 0)):
        rate[d] = (m["surface"], last[1] / (last[0] * int(m["cpus"])))
# a run that executes far more or far fewer cases per CPU second than the others on its surface is not doing the
# same work (inputs rejected before the programs run, or a harness that does not start)
for s in surfaces:
    v = [r for sf, r in rate.values() if sf == s]
    if len(v) >= 3:
        med = statistics.median(v)
        for d, (sf, r) in rate.items():
            if sf == s and med > 0 and not (med / 20 <= r <= med * 20):
                check(False, f"run {d} executes at a plausible rate", f"{r:.2f} cases per CPU second, median of the surface {med:.2f}", warn=True)

# ---- differences
ss = os.path.join(work, "sanitize", tag, "summary.json")
if os.path.isfile(ss):
    s = json.load(open(ss))
    check(s["replayed"] > 0, "the inputs were replayed on the sanitizer builds", str(s))
    print(f"      sanitizers: {s['replayed']} inputs replayed, kinds of report by program: {s['by_program']}")
else:
    print("skip  no sanitizer replay (sanitize/<tag>/summary.json): ./dfz sanitize")
sj = os.path.join(work, "recheck", tag, "summary.json")
check(os.path.isfile(sj), "the differences were rechecked (recheck/<tag>/summary.json)")
if os.path.isfile(sj):
    s = json.load(open(sj))
    check(s["stored"] == s["differs"] + s.get("fuzz_only", 0) + s["flaky"] + s.get("timeout", 0) + s.get("memory", 0) + s["gone"], "every stored signature has a status", str(s))
    rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(work, "recheck", tag, "remaining.tsv"))][1:]
    bad = [r[4] for r in rows if not os.path.isfile(os.path.join(work, "recheck", tag, r[4]))]
    check(not bad, f"every remaining difference has its input ({len(rows)} listed)", " ".join(bad[:5]))
    check(s["flaky"] <= max(5, s["stored"] // 20), "few differences are flaky", str(s), warn=True)
    print(f"      differences: {s['stored']} stored, {s['to_account_for']} to account for, {s.get('timeout_deferred', 0) + s.get('memory_deferred', 0)} put off (only the original passed a limit), {s['flaky']} flaky, {s['gone']} gone")

# ---- coverage
cj = os.path.join(work, "coverage", tag, "coverage.json")
check(os.path.isfile(cj), "the coverage report was written (coverage.json)")
if os.path.isfile(cj):
    c = json.load(open(cj))
    for s in sides:
        t = c["total"].get(s)
        check(bool(t) and t["branches"][1] > 0, f"coverage totals of {s}", "empty")
        if t and t["branches"][1]:
            print(f"      {s}: branches {t['branches'][0]}/{t['branches'][1]} ({100 * t['branches'][0] / t['branches'][1]:.1f}%) reachable"
                  + (f", {c['raw_total'][s]['branches'][0]}/{c['raw_total'][s]['branches'][1]} all code" if c.get("raw_total", {}).get(s) else ""))
        for e in sorted({m["engine"] for m in runs.values()}):
            x = c["engines"].get("engine", {}).get(e, {}).get(s)
            check(bool(x), f"coverage of {e}'s corpora in {s}", "no profile")
        w = c.get("warnings", {}).get(s, [])
        check(not w, f"no listed-unreachable code of {s} was executed", f"{len(w)} entries, e.g. {w[:2]}", warn=True)
    for f in ["COVERAGE.md"] + [f"{k}.{s}.tsv" for k in ("files", "functions", "branches") for s in sides] + [f"html/{s}/index.html" for s in sides]:
        check(os.path.isfile(os.path.join(work, "coverage", tag, f)), f"coverage/{tag}/{f}")
check(os.path.isfile(os.path.join(work, "report", tag, "REPORT.md")), "the campaign report was written")
print("FAILED" if failed else "all checks passed")
sys.exit(1 if failed else 0)
