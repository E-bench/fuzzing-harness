#!/usr/bin/env python3
"""The report of one campaign: what ran, how the fuzzers compare, which differences were confirmed, what was covered.

  report.py <target work dir> <tag> <target.toml>

Reads runs/<tag>/*/{meta.txt,stats.tsv}, recheck/<tag>/{summary.json,remaining.tsv} and coverage/<tag>/coverage.json
(the last two when they exist) and writes report/<tag>/REPORT.md.
"""
import collections, json, os, statistics, sys, tomllib

work, tag, toml = sys.argv[1:4]
cfg = tomllib.load(open(toml, "rb"))
A, B = cfg["side"]["a"], cfg["side"]["b"]
runs_dir = os.path.join(work, "runs", tag)
out = os.path.join(work, "report", tag)
os.makedirs(out, exist_ok=True)


def load(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None


runs = []
for run in sorted(os.listdir(runs_dir)):
    mp = os.path.join(runs_dir, run, "meta.txt")
    if not os.path.isfile(mp):
        continue
    m = dict(l.strip().split("=", 1) for l in open(mp) if "=" in l)
    rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(runs_dir, run, "stats.tsv"))][1:]
    rows = [r for r in rows if len(r) == 4 and all(x.strip().lstrip("-").isdigit() for x in r)]
    last = [int(x) for x in rows[-1]] if rows else [0, 0, 0, 0]
    runs.append({"name": run, "engine": m["engine"], "surface": m["surface"], "cpus": int(m.get("cpus", 0)), "dur": int(m.get("dur", 0)),
                 "host": m.get("host", ""), "elapsed": last[0], "execs": last[1], "corpus": last[2], "signatures": last[3],
                 "finished": "end" in m})
rck = load(os.path.join(work, "recheck", tag, "summary.json"))
remaining = []   # status, surface, signature, exempt, fuzzers, input
try:
    remaining = [l.rstrip("\n").split("\t") for l in open(os.path.join(work, "recheck", tag, "remaining.tsv"))][1:]
except OSError:
    pass
cov = load(os.path.join(work, "coverage", tag, "coverage.json"))
engines = sorted({r["engine"] for r in runs})
surfaces = sorted({r["surface"] for r in runs})
open_ = [r for r in remaining if r[0] == "differs" and not r[3]]


def found_by(r):
    return {x.split(":")[0]: int(x.split(":")[1]) for x in r[4].split()}


L = [f"# {cfg.get('title', cfg['name'])}: campaign `{tag}`", ""]
L += ["| | Program | Source | Commit |", "|---|---|---|---|",
      f"| a (original) | {A.get('title', A['name'])} | {A.get('repo', '')} | `{A.get('commit', '')}` |",
      f"| b (rewrite) | {B.get('title', B['name'])} | {B.get('repo', '')} | `{B.get('commit', '')}` |", ""]
cpu_h = sum(r["cpus"] * r["elapsed"] for r in runs) / 3600
L += [f"{len(runs)} runs ({len(engines)} fuzzers x {len(surfaces)} surfaces), {cpu_h:,.0f} CPU hours, {sum(r['execs'] for r in runs):,} test cases",
      "(one test case = both programs run on one generated input)."]
if rck:
    L += [f"The harness stored {rck['stored']:,} difference signatures. Replayed on the reference builds under the current configuration"
          + (f" (with the temporary patches of {', '.join(rck['patched'])})" if rck.get("patched") else "") + ",",
          f"**{rck['to_account_for']:,} still differ and are not accounted for**, {rck['exempt']:,} still differ and fall under an exemption,",
          f"{rck['flaky']:,} differ only in some replays and {rck['gone']:,} no longer differ."]
L.append("")

L += ["## Fuzzers", "", "| Fuzzer | Runs | CPU hours | Test cases | per CPU second | Corpus | Signatures stored |" + (" Open differences | Only this fuzzer | Median seconds to find |" if rck else "") +
      (f" Branches {A['name']} | Branches {B['name']} |" if cov else ""), "|---|---|---|---|---|---|---|" + ("---|---|---|" if rck else "") + ("---|---|" if cov else "")]
for e in engines:
    rs = [r for r in runs if r["engine"] == e]
    ch = sum(r["cpus"] * r["elapsed"] for r in rs)
    ex = sum(r["execs"] for r in rs)
    row = f"| {e} | {len(rs)} | {ch / 3600:,.1f} | {ex:,} | {ex / ch if ch else 0:.2f} | {sum(r['corpus'] for r in rs):,} | {sum(r['signatures'] for r in rs):,} |"
    if rck:
        mine = [found_by(r) for r in open_ if e in found_by(r)]
        only = [m for m in mine if len(m) == 1]
        times = [m[e] for m in mine if m[e] >= 0]
        row += f" {len(mine)} | {len(only)} | {statistics.median(times):,.0f} |" if times else f" {len(mine)} | {len(only)} | - |"
    if cov:
        for s in cov["sides"]:
            t = cov["engines"].get("engine", {}).get(e, {}).get(s)
            row += f" {100 * t['branches'][0] / t['branches'][1]:.1f}% |" if t and t["branches"][1] else " - |"
    L.append(row)
L.append("")
L += ["Executions and corpus sizes are the fuzzers' own counts (`execs` is not reported by every fuzzer in every mode).", ""]
if rck and surfaces:
    L += ["Open differences found, by fuzzer and surface:", "", "| Fuzzer | " + " | ".join(surfaces) + " |", "|---|" + "---|" * len(surfaces)]
    for e in engines:
        L.append(f"| {e} | " + " | ".join(str(sum(1 for r in open_ if r[1] == s and e in found_by(r))) for s in surfaces) + " |")
    L.append("")
if rck:
    L += ["## Differences", "",
          f"`recheck/{tag}/remaining.tsv` lists every stored input on which the programs still differ, with the input itself",
          f"(`recheck/{tag}/inputs/`). They are worked off one at a time: take one, find the cause, settle it as an input constraint,",
          "an exemption, a normalisation or a bug with a temporary patch, and recheck (`docs/WORKFLOW.md`). The first of them:", "",
          "| Surface | Signature | Found by | Input |", "|---|---|---|---|"]
    for r in open_[:25]:
        sig = r[2].replace("|", "\\|")
        L.append(f"| {r[1]} | `{sig[:230]}` | {', '.join(found_by(r))} | `{r[5]}` |")
    if len(open_) > 25:
        L.append(f"| | ... {len(open_) - 25:,} more | | |")
    L.append("")
if cov:
    L += ["## Coverage", "", "| Program | Functions | Lines | Branches |", "|---|---|---|---|"]
    for s in cov["sides"]:
        t = cov["total"].get(s)
        if t:
            f = lambda c: f"{c[0]:,} / {c[1]:,} ({100 * c[0] / c[1]:.1f}%)" if c[1] else "-"
            L.append(f"| {cov['titles'][s]} | {f(t['functions'])} | {f(t['lines'])} | {f(t['branches'])} |")
    L += ["", f"Details: `coverage/{tag}/COVERAGE.md` (by directory, file, fuzzer and over time; lists of what was never reached).", ""]
L += ["## Runs", "", "| Run | Host | CPUs | Seconds | Test cases | Corpus | Signatures |", "|---|---|---|---|---|---|---|"]
for r in runs:
    L.append(f"| {r['engine']} {r['surface']} | {r['host']} | {r['cpus']} | {r['elapsed']:,} | {r['execs']:,} | {r['corpus']:,} | {r['signatures']:,} |")
L.append("")
open(os.path.join(out, "REPORT.md"), "w").write("\n".join(L))
print(f"wrote {out}/REPORT.md")
