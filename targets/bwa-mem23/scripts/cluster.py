#!/usr/bin/env python3
"""Cluster the bwa-mem2 vs bwa-mem3 compat findings by trigger features."""
import glob, os, re, sys, json
from collections import Counter, defaultdict

BASE = "/oscar/scratch/zliu328/dfz/bwa/findings2"
BS = set("ACGT")

def load_fa(p):
    seqs = []; name=None; buf=[]
    for line in open(p):
        if line.startswith(">"):
            if name: seqs.append("".join(buf))
            name=line[1:].strip(); buf=[]
        else: buf.append(line.strip())
    if name: seqs.append("".join(buf))
    return seqs

def max_tandem(seq):
    """for unit length 1..6: max copies of a tandem repeat"""
    best = {}
    for u in range(1,7):
        mx = 0
        for i in range(0, len(seq)-u*2):
            unit = seq[i:i+u]
            if set(unit) - BS: continue
            copies = 1
            j = i+u
            while j+u <= len(seq) and seq[j:j+u] == unit:
                copies += 1; j += u
            mx = max(mx, copies)
        best[u] = mx
    return best

def classify(d):
    A=[l for l in open(d+"a.sam") if not l.startswith("@")]
    Bb=[l for l in open(d+"b.sam") if not l.startswith("@")]
    if len(A)!=len(Bb): return "record-count", None
    for x,y in zip(A,Bb):
        if x==y: continue
        fa=int(x.split("\t")[1]); fb=int(y.split("\t")[1])
        ua=bool(fa&4); ub=bool(fb&4)
        if ua and not ub: return "unmapped-vs-mapped", "B"
        if ub and not ua: return "mapped-vs-unmapped", "A"
        if fa!=fb: return "flag", None
        return "fields", None
    return "none", None

def feat(d):
    refs = load_fa(d+"ref.fa")
    ref = refs[0] if refs else ""
    n = len(ref) or 1
    tand = max_tandem(ref)
    reads = [l.strip() for i,l in enumerate(open(d+"reads.fq")) if i%4==1]
    rlens = [len(r) for r in reads] or [0]
    readsN = sum(1 for r in reads if "N" in r)
    # read self-repetition: max copies of unit 1..6 inside a read
    rrep = 0
    for r in reads[:20]:
        for u in range(1,5):
            mx=1; i=0
            while i+u <= len(r):
                j=i+u; c=1
                while j+u<=len(r) and r[j:j+u]==r[i:i+u]: c+=1; j+=u
                mx=max(mx,c); i+=1
            rrep=max(rrep,mx)
    return dict(
        ref_len=len(ref), N_frac=round(ref.count("N")/n,3),
        hump=max(len(x) for x in re.split(r"[^ACGT]+", ref)) if ref else 0,
        rep1=tand[1], rep2=tand[2], rep3=tand[3], rep4=tand[4], rep6=tand[6],
        rep_tot=sum(tand[u]*u for u in tand),
        n_reads=len(reads), rlen=round(sum(rlens)/len(rlens),1),
        readsN=readsN, read_rep=rrep,
    )

rows=[]
for d in sorted(glob.glob(BASE+"/*/")):
    if not os.path.exists(d+"a.sam"): continue
    cls, side = classify(d)
    f = feat(d); f["cluster"]=cls; f["side"]=side; f["dir"]=os.path.basename(d.rstrip("/"))
    rows.append(f)

json.dump(rows, open("/oscar/scratch/zliu328/dfz/bwa/cluster-features.json","w"))
print("cases:", len(rows), "classes:", Counter(r["cluster"] for r in rows))
for cls in ("unmapped-vs-mapped","record-count","fields","flag"):
    rs=[r for r in rows if r["cluster"]==cls]
    if not rs: continue
    print(f"\n=== {cls} (n={len(rs)}) ===")
    for k in ("ref_len","N_frac","hump","rep2","rep4","rep6","rep_tot","rlen","read_rep","n_reads"):
        vals=sorted(r[k] for r in rs)
        print(f"  {k:10s} min={vals[0]:>8} med={vals[len(vals)//2]:>8} max={vals[-1]:>8}")
    # 关键阈值测试：重复区（rep2>=50 或 rep4>=50 或 rep6>=50）
    for thr in (20,50,100,200):
        hit=sum(1 for r in rs if max(r["rep1"],r["rep2"],r["rep3"],r["rep4"],r["rep6"])>=thr)
        print(f"  tandem(any unit)>= {thr}: {hit}/{len(rs)}")
