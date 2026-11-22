"""Reading a run's archives (corpus.tgz, find.tgz) without unpacking them whole.

Every shard of a replay step reads every archive, and the scratch directory of a job is often memory (/tmp as tmpfs):
unpacking everything there costs each job the size of all corpora, which ends it at its memory limit once the corpora
are large. So an archive is read through once to list and hash its files, and only the files that a shard replays are
written out.
"""
import hashlib, os, tarfile


def skipped(name, also=()):
    """True for a file of an archive that is not an input: a fuzzer's statistics, state, logs and hidden files"""
    p = "/" + os.path.normpath(name)
    n = os.path.basename(p)
    return (os.path.basename(os.path.dirname(p)).startswith(".") or n.startswith(".") or n.startswith("log_") or n.endswith(".csv")
            or n in ("fuzzer_stats", "plot_data") or n in also or "/.state" in p)


def listing(tgz, also=()):
    """[(member name, modification time, sha1 digest)] of the inputs in an archive; nothing is written"""
    out = []
    with tarfile.open(tgz) as t:
        for m in t:
            if not m.isfile() or skipped(m.name, also):
                continue
            h, f = hashlib.sha1(), t.extractfile(m)
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
            out.append((m.name, m.mtime, h.digest()))
    return out


def unpack(tgz, dest, names):
    """write the named members of an archive under dest; the path of a member is path(dest, name)"""
    names = set(names)
    with tarfile.open(tgz) as t:
        for m in t:
            if m.name in names and m.isfile():
                t.extract(m, dest, filter="data")


def path(dest, name):
    return os.path.join(dest, os.path.normpath(name))
