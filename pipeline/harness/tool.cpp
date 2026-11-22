// The harness outside a fuzzer (same decoding, running and comparing as fuzz_entry.cpp). Environment as in dfz.h.
//
//   dfz_tool sig <input>...           one line per finding: <input> TAB <signature>   ("-" when both programs agree)
//   dfz_tool replay <input>           the case, both exit states, every finding with an excerpt
//   dfz_tool run <file or dir>...     run every input (coverage replay); prints counts
//   dfz_tool export <input> <dir>     write the decoded case: <dir>/in/, both command lines, observed outputs, run.sh
//   dfz_tool decode <input> <dir>     write the decoded input files only (runs nothing; needs no binaries)
#define DFZ_MAIN
#include "dfz.h"
using namespace dfz;

static bool loadInput(const string &p, string &d) {
    if (!readFile(p, d)) { fprintf(stderr, "cannot read %s\n", p.c_str()); return false; }
    return true;
}
static void listInputs(const string &p, vector<string> &out) {
    struct stat st;
    if (stat(p.c_str(), &st) != 0) return;
    if (!S_ISDIR(st.st_mode)) { out.push_back(p); return; }
    vector<string> names;
    if (DIR *d = opendir(p.c_str())) {
        while (dirent *e = readdir(d))
            if (e->d_name[0] != '.') names.push_back(e->d_name);
        closedir(d);
    }
    std::sort(names.begin(), names.end());
    for (auto &n : names) listInputs(p + "/" + n, out);
}
static void copyTree(const string &from, const string &to) {
    mkdirs(to);
    if (DIR *d = opendir(from.c_str())) {
        while (dirent *e = readdir(d)) {
            if (e->d_name[0] == '.') continue;
            string s;
            if (readFile(from + "/" + e->d_name, s)) writeFile(to + "/" + e->d_name, s);
        }
        closedir(d);
    }
}
static string shq(const string &s) { return "'" + replaceAll(s, "'", "'\\''") + "'"; }

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: dfz_tool sig|replay|run|export|decode ... (see tool.cpp)\n"); return 2; }
    string cmd = argv[1];
    const Config &C = cfg();
    int rc = 0;
    if (cmd == "sig") {
        for (int i = 2; i < argc; i++) {
            string d;
            if (!loadInput(argv[i], d)) continue;
            Case c;
            Outcome o;
            if (!runInput((const uint8_t *)d.data(), d.size(), c, o)) { printf("%s\tINVALID\n", argv[i]); continue; }
            if (o.findings.empty()) printf("%s\t-\n", argv[i]);
            for (const Finding &f : o.findings) printf("%s\t%s\n", argv[i], signature(c, f).c_str());
            fflush(stdout);
        }
    } else if (cmd == "replay") {
        string d;
        if (!loadInput(argv[2], d)) return 1;
        Case c;
        Outcome o;
        if (!runInput((const uint8_t *)d.data(), d.size(), c, o)) { printf("INVALID input for surface %s\n", C.surface.c_str()); return 1; }
        printf("surface: %s\ntags: %s\nnote: %s\n", c.surface.c_str(), joinAtoms(c.tags).c_str(), c.note.c_str());
        for (int s = 0; s < 2; s++) {
            printf("%s:", (s ? C.nameB : C.nameA).c_str());
            for (auto &a : commandLine(c, s)) printf(" %s", a.c_str());
            const Run &r = s ? o.b : o.a;
            printf("\n  -> %s (exit %d, %ld ms, %ld MB, %ld ms of CPU) %s\n", r.cls.c_str(), r.code, r.ms, r.peakMb, r.cpuMs, r.msg.c_str());
        }
        if (o.findings.empty()) printf("no difference\n");
        for (const Finding &f : o.findings) printf("FINDING %s\n%s", signature(c, f).c_str(), f.detail.c_str());
        printf("case directory: %s%s\n", caseDir().c_str(), C.keep ? "" : " (removed; set DFZ_KEEP=1 to keep it)");
        rc = o.findings.empty() ? 0 : 1;
    } else if (cmd == "run") {
        vector<string> in;
        for (int i = 2; i < argc; i++) listInputs(argv[i], in);
        size_t invalid = 0, differ = 0;
        long peak[2] = {0, 0}, slow[2] = {0, 0};
        map<string, size_t> cls;
        for (auto &p : in) {
            string d;
            if (!loadInput(p, d)) continue;
            Case c;
            Outcome o;
            if (!runInput((const uint8_t *)d.data(), d.size(), c, o)) { invalid++; continue; }
            differ += !o.findings.empty();
            cls[C.nameA + "=" + o.a.cls + "," + C.nameB + "=" + o.b.cls]++;
            peak[0] = std::max(peak[0], o.a.peakMb), peak[1] = std::max(peak[1], o.b.peakMb);
            slow[0] = std::max(slow[0], o.a.ms), slow[1] = std::max(slow[1], o.b.ms);
        }
        printf("inputs=%zu invalid=%zu with_difference=%zu peak_mb=%ld,%ld longest_ms=%ld,%ld", in.size(), invalid, differ, peak[0], peak[1], slow[0], slow[1]);
        for (auto &k : cls) printf(" %s:%zu", k.first.c_str(), k.second);
        printf("\n");
    } else if (cmd == "decode" && argc >= 4) {
        string d;
        if (!loadInput(argv[2], d)) return 1;
        Case c;
        newCase(c);
        if (!dfz_decode((const uint8_t *)d.data(), d.size(), c)) { fprintf(stderr, "INVALID input\n"); rmTree(C.work); return 1; }
        copyTree(c.in, argv[3]);
        string info = "tags: " + joinAtoms(c.tags) + "\nnote: " + c.note + "\nargs:";
        for (auto &a : c.args) info += " " + a;
        printf("%s\n", info.c_str());
    } else if (cmd == "export" && argc >= 4) {
        string d, dir = argv[3];
        if (!loadInput(argv[2], d)) return 1;
        Case c;
        Outcome o;
        if (!runInput((const uint8_t *)d.data(), d.size(), c, o)) { fprintf(stderr, "INVALID input\n"); return 1; }
        mkdirs(dir);
        copyTree(c.in, dir + "/in");
        copyTree(o.a.out, dir + "/observed/" + C.nameA);
        copyTree(o.b.out, dir + "/observed/" + C.nameB);
        string la, lb;
        readFile(o.a.log, la);
        readFile(o.b.log, lb);
        writeFile(dir + "/observed/" + C.nameA + ".log", la);
        writeFile(dir + "/observed/" + C.nameB + ".log", lb);
        writeFile(dir + "/case.bin", d);
        // run.sh: the two command lines with the case directory replaced by relative paths; the binaries come from
        // environment variables named after the sides (upper case)
        string up[2] = {C.nameA, C.nameB};
        for (string &u : up) for (char &ch : u) ch = toupper((unsigned char)ch);
        string sh = "#!/usr/bin/env bash\n# usage: " + up[0] + "=/path/to/" + C.nameA + " " + up[1] + "=/path/to/" + C.nameB + " ./run.sh   (writes out/" + C.nameA + "/ and out/" + C.nameB + "/)\n"
                    "cd \"$(dirname \"$0\")\"; rm -rf out; mkdir -p out/" + C.nameA + " out/" + C.nameB + "\n";
        for (int s = 0; s < 2; s++) {
            const string &nm = s ? C.nameB : C.nameA;
            sh += "\"$" + up[s] + "\"";
            for (string a : commandLine(c, s)) {
                a = replaceAll(a, c.in, "in");
                a = replaceAll(a, caseDir() + (s ? "/b" : "/a"), "out/" + nm);
                sh += " " + shq(a);
            }
            sh += " > out/" + nm + ".log 2>&1; echo \"" + nm + " exit=$?\"\n";
        }
        string names;
        for (const OutputSpec &s : C.outputs) names += " " + s.file;
        sh += "for f in" + names + "; do\n  if [ ! -e out/" + C.nameA + "/$f ] && [ ! -e out/" + C.nameB + "/$f ]; then echo \"$f: written by neither\"\n"
              "  elif cmp -s out/" + C.nameA + "/$f out/" + C.nameB + "/$f; then echo \"$f: identical\"; else echo \"$f: DIFFERENT\"; fi\ndone\n";
        writeFile(dir + "/run.sh", sh);
        chmod((dir + "/run.sh").c_str(), 0755);
        string info = "surface: " + c.surface + "\ntags: " + joinAtoms(c.tags) + "\nnote: " + c.note + "\n" + C.nameA + ": " + o.a.cls + " " + o.a.msg + "\n" + C.nameB + ": " + o.b.cls + " " + o.b.msg + "\n";
        for (const Finding &f : o.findings) info += "FINDING " + signature(c, f) + "\n" + f.detail;
        writeFile(dir + "/finding.txt", info);
        printf("%s", info.c_str());
    } else {
        fprintf(stderr, "unknown command\n");
        return 2;
    }
    if (!C.keep) rmTree(C.work);
    return rc;
}
