#!/usr/bin/env python3
"""Read a target.toml.

  cfg.py shell   <target.toml>   shell assignments (CFG_*) for the step scripts
  cfg.py harness <target.toml>   the flat file the harness reads (one "key<TAB>value" per line; a list repeats its key)
  cfg.py json    <target.toml>   the whole file as JSON (for the Python tools)
"""
import json, shlex, sys, tomllib


def load(path):
    with open(path, "rb") as f:
        c = tomllib.load(f)
    for k in ("name", "side", "build", "run", "surface"):
        if k not in c:
            sys.exit(f"{path}: missing [{k}]")
    for s in ("a", "b"):
        if s not in c["side"] or "name" not in c["side"][s]:
            sys.exit(f"{path}: missing [side.{s}] name")
    return c


def shell(c):
    out = {}
    out["CFG_NAME"] = c["name"]
    for s in ("a", "b"):
        d = c["side"][s]
        S = s.upper()
        out[f"CFG_{S}_NAME"] = d["name"]
        out[f"CFG_{S}_REPO"] = d.get("repo", "")
        out[f"CFG_{S}_COMMIT"] = d.get("commit", "")
        out[f"CFG_{S}_TITLE"] = d.get("title", d["name"])
        out[f"CFG_{S}_PATCHES"] = " ".join(d.get("patches", []))
    b = c["build"]
    for k in ("deps", "program", "data"):
        out[f"CFG_BUILD_{k.upper()}"] = b.get(k, "")
    out["CFG_REF_CC"] = b.get("ref", {}).get("cc", "gcc")
    out["CFG_REF_CXX"] = b.get("ref", {}).get("cxx", "g++")
    out["CFG_HARNESS_LIBS"] = b.get("harness_libs", "")
    out["CFG_ADAPTER"] = b.get("adapter", "adapter.cpp")
    out["CFG_SURFACES"] = " ".join(c["surface"].keys())
    for name, s in c["surface"].items():
        n = name.upper().replace("-", "_")
        out[f"CFG_SURFACE_{n}_MAX_LEN"] = str(s.get("max_len", 4096))
        out[f"CFG_SURFACE_{n}_MIN_LEN"] = str(s.get("min_len", 0))
        out[f"CFG_SURFACE_{n}_DICT"] = s.get("dict", "")
    cov = c.get("coverage", {})
    out["CFG_COV_IGNORE"] = cov.get("ignore", "")
    out["CFG_TIMEOUT_MS"] = str(c["run"].get("timeout_ms", 20000))
    return "\n".join(f"export {k}={shlex.quote(v)}" for k, v in out.items())


def harness(c):
    lines = []

    def put(k, v):
        if isinstance(v, bool):
            v = "1" if v else "0"
        v = str(v)
        if "\n" in v or "\t" in v:
            sys.exit(f"harness value of {k} contains a tab or newline")
        lines.append(f"{k}\t{v}")

    r = c["run"]
    put("name.a", c["side"]["a"]["name"])
    put("name.b", c["side"]["b"]["name"])
    for k in ("argv", "argv_a", "argv_b"):
        for a in r.get(k, []):
            put(k, a)
    for k, v in r.get("env", {}).items():
        put("env", f"{k}={v}")
    put("timeout_ms", r.get("timeout_ms", 20000))
    put("memory_mb", r.get("memory_mb", 0))
    put("exit", r.get("exit", "class"))
    put("error_line", r.get("error_line", "last"))
    put("compare_on_error", r.get("compare_on_error", False))
    if r.get("error_strip"):
        put("error_strip", r["error_strip"])
    for p in r.get("error_prefix", []):
        put("error_prefix", p)
    for o in r.get("output", []):
        # file, comparator, options
        put("output", "|".join([o["file"], o.get("compare", "bytes"), o.get("options", "")]))
    put("compare_stdout", r.get("compare_stdout", False))
    for k, v in c.get("adapter", {}).items():
        if isinstance(v, list):
            for x in v:
                put(f"adapter.{k}", x)
        else:
            put(f"adapter.{k}", v)
    for name, s in c["surface"].items():
        put("surface", name)
        for k, v in s.items():
            if isinstance(v, list):
                for x in v:
                    put(f"surface.{name}.{k}", x)
            else:
                put(f"surface.{name}.{k}", v)
    return "\n".join(lines)


if __name__ == "__main__":
    mode, path = sys.argv[1], sys.argv[2]
    c = load(path)
    print({"shell": shell, "harness": harness, "json": lambda c: json.dumps(c, indent=1)}[mode](c))
