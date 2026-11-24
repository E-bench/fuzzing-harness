#!/usr/bin/env python3
"""Systematic trigger matrix: unit length x copies x read length x position x paired."""
import os, random, subprocess, tempfile
MEM2="/oscar/scratch/zliu328/dfz/bwa/bwa-mem2-2.2.1_x64-linux/bwa-mem2"
MEM3="/oscar/scratch/zliu328/dfz/bwa/bwa-mem3/bwa-mem3"
UNITS=["A","AC","ACG","ACGT","ACGTA","ACGTAC"]
def rc(s): return s.translate(str.maketrans("ACGTN","TGCAN"))[::-1]
def build(unit,copies,flanklen=1000):
    r=random.Random(3)
    f1="".join(r.choice("ACGT") for _ in range(flanklen))
    f2="".join(r.choice("ACGT") for _ in range(flanklen))
    rep=unit*copies
    return f1+rep+f2, len(f1), len(rep)
def run(bin,args,out):
    with open(out,"w") as f: subprocess.run([bin]+args,stdout=f,stderr=subprocess.DEVNULL)
def body(p): return [l for l in open(p) if not l.startswith("@")]
def classify(A,Bb):
    if len(A)!=len(Bb): return "count"
    for x,y in zip(A,Bb):
        if x==y: continue
        fa=int(x.split("\t")[1]); fb=int(y.split("\t")[1])
        ua=bool(fa&4); ub=bool(fb&4)
        if ua and not ub: return "unmapped-vs-mapped"
        if ub and not ua: return "mapped-vs-unmapped"
        return "fields"
    return "same"
print("unit,copies,rlen,pos,paired,class")
W=tempfile.mkdtemp(); os.chdir(W)
for unit in UNITS:
    for copies in (2,5,10,20,50,100,300):
        g,off,replen=build(unit,copies)
        open("ref.fa","w").write(">c\n"+"\n".join(g[i:i+60] for i in range(0,len(g),60))+"\n")
        os.system("rm -f ref.fa.*; %s index ref.fa >/dev/null 2>&1"%MEM2)
        for rlen in (30,50,100,150):
            if replen < rlen+10: continue
            for pos in ("inside","cross"):
                r=random.Random(7)
                if pos=="inside": st=off+replen//2-rlen//2
                else: st=max(0,off-rlen//2)
                rd=g[st:st+rlen]
                if len(rd)<rlen: continue
                for paired in (0,1):
                    if paired:
                        ins=max(rlen*2+5, 300)
                        st2=off+replen//2-ins//2
                        frag=g[max(0,st2):max(0,st2)+ins]
                        if len(frag)<2: continue
                        q1=frag[:rlen]; q2=rc(frag[-rlen:])
                        open("reads.fq","w").write("@p/1\n%s\n+\n%s\n@p/2\n%s\n+\n%s\n"%(q1,"I"*len(q1),q2,"I"*len(q2)))
                    else:
                        open("reads.fq","w").write("@r\n%s\n+\n%s\n"%(rd,"I"*len(rd)))
                    run(MEM2,["mem","-t","1","-K","100000000","ref.fa","reads.fq"],"a.sam")
                    run(MEM3,["mem","--compat=bwa-mem2","-t","1","-K","100000000","ref.fa","reads.fq"],"b.sam")
                    print("%s,%d,%d,%s,%d,%s"%(unit,copies,rlen,pos,paired,classify(body("a.sam"),body("b.sam"))),flush=True)
