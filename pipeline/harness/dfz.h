// Differential test core: run two programs on the same generated test case and compare what they write.
//
// One fuzz input is decoded by the target's adapter into a test case (input files plus command-line options), both
// programs are run on it as child processes, their outputs are compared, and every difference is reduced to a short
// signature (a set of "atoms"). Nothing here knows a particular target: the command line, the outputs and their
// comparators come from the target's configuration (target.toml -> harness.cfg), the decoding from its adapter.
//
// The core is engine independent: fuzz_entry.cpp exposes LLVMFuzzerTestOneInput and feeds the children's coverage maps
// back to whichever fuzzer drives it; tool.cpp replays and exports single cases outside a fuzzer.
//
// Environment:
//   DFZ_CFG       harness.cfg of the target (written by pipeline/cfg.py)
//   DFZ_SURFACE   which of the target's surfaces this process fuzzes
//   DFZ_A, DFZ_B  the two binaries (a: the original, b: the rewrite)
//   DFZ_A_MAP, DFZ_B_MAP   sizes of their AFL++ coverage maps (0 or unset: not instrumented)
//   DFZ_WORK      scratch directory (node local; a per-process subdirectory is used)
//   DFZ_FIND      where inputs that show a difference are stored ("" = do not store)
//   DFZ_DATA      the target's fixed data directory ({data} in the command line)
//   DFZ_CHILD_ENV extra environment of the children, entries separated by ';', {side} is replaced by the side's name
//   DFZ_TIMEOUT_MS, DFZ_MEMORY_MB, DFZ_CAP (stored inputs per signature), DFZ_VERBOSE, DFZ_KEEP (keep the case directory)
#pragma once
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <dirent.h>
#include <fcntl.h>
#include <map>
#include <memory>
#include <regex>
#include <set>
#include <spawn.h>
#include <string>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>
#ifdef __linux__
#include <sys/ipc.h>
#include <sys/shm.h>
#endif

extern char **environ;

namespace dfz {
using std::map;
using std::set;
using std::string;
using std::vector;

// ------------------------------------------------------------------ small utilities
inline string envs(const char *k, const char *d = "") { const char *v = getenv(k); return v ? v : d; }
inline bool readFile(const string &p, string &out) {
    FILE *f = fopen(p.c_str(), "rb");
    if (!f) return false;
    out.clear();
    char buf[65536];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) out.append(buf, n);
    fclose(f);
    return true;
}
inline void writeFile(const string &p, const string &s) {
    FILE *f = fopen(p.c_str(), "wb");
    if (!f) return;
    fwrite(s.data(), 1, s.size(), f);
    fclose(f);
}
inline vector<string> split(const string &s, char d) {
    vector<string> r;
    size_t a = 0;
    for (;;) {
        size_t b = s.find(d, a);
        if (b == string::npos) { r.push_back(s.substr(a)); break; }
        r.push_back(s.substr(a, b - a));
        a = b + 1;
    }
    return r;
}
inline vector<string> lines(const string &s) {
    vector<string> r = split(s, '\n');
    if (!r.empty() && r.back().empty()) r.pop_back();
    return r;
}
inline void rmTree(const string &p) {
    DIR *d = opendir(p.c_str());
    if (!d) { unlink(p.c_str()); return; }
    while (dirent *e = readdir(d)) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        string q = p + "/" + e->d_name;
        struct stat st;
        if (lstat(q.c_str(), &st) == 0 && S_ISDIR(st.st_mode)) rmTree(q);
        else unlink(q.c_str());
    }
    closedir(d);
    rmdir(p.c_str());
}
inline void mkdirs(const string &p) {
    for (size_t i = 1; i <= p.size(); i++)
        if (i == p.size() || p[i] == '/') mkdir(p.substr(0, i).c_str(), 0700);
}
inline uint64_t fnv(const string &s, uint64_t h = 1469598103934665603ULL) {
    for (unsigned char c : s) { h ^= c; h *= 1099511628211ULL; }
    return h;
}
inline string hex16(uint64_t h) {
    char b[17];
    snprintf(b, sizeof b, "%016llx", (unsigned long long)h);
    return b;
}
inline string replaceAll(string s, const string &a, const string &b) {
    for (size_t i = 0; (i = s.find(a, i)) != string::npos; i += b.size()) s.replace(i, a.size(), b);
    return s;
}
inline string clip(const string &s, size_t n = 300) { return s.size() <= n ? s : s.substr(0, n) + "..."; }

// ------------------------------------------------------------------ configuration
struct OutputSpec { string file, cmp, opt; };
struct Config {
    string surface, binA, binB, nameA = "a", nameB = "b", work, find, data, childEnv;
    size_t mapA = 0, mapB = 0;
    int timeoutMs = 20000, cap = 4;
    long memoryMb = 0;         // limit of a program run's resident memory (0: none)
    bool verbose = false, keep = false;
    vector<string> argv, argvA, argvB, env, errorPrefix;
    vector<OutputSpec> outputs;
    string exitMode = "class", errorLine = "last", errorStrip;
    std::multimap<string, string> kv; // every line of harness.cfg
};
inline Config &cfg() {
    static Config c;
    static bool init = false;
    if (init) return c;
    init = true;
    string text;
    if (!readFile(envs("DFZ_CFG"), text)) { fprintf(stderr, "dfz: cannot read DFZ_CFG=%s\n", envs("DFZ_CFG").c_str()); exit(2); }
    for (const string &l : lines(text)) {
        size_t t = l.find('\t');
        if (t == string::npos) continue;
        string k = l.substr(0, t), v = l.substr(t + 1);
        c.kv.insert({k, v});
        if (k == "argv") c.argv.push_back(v);
        else if (k == "argv_a") c.argvA.push_back(v);
        else if (k == "argv_b") c.argvB.push_back(v);
        else if (k == "env") c.env.push_back(v);
        else if (k == "name.a") c.nameA = v;
        else if (k == "name.b") c.nameB = v;
        else if (k == "timeout_ms") c.timeoutMs = atoi(v.c_str());
        else if (k == "exit") c.exitMode = v;
        else if (k == "error_line") c.errorLine = v;
        else if (k == "error_strip") c.errorStrip = v;
        else if (k == "error_prefix") c.errorPrefix.push_back(v);
        else if (k == "output") {
            vector<string> f = split(v, '|');
            f.resize(3);
            c.outputs.push_back({f[0], f[1].empty() ? "bytes" : f[1], f[2]});
        }
    }
    c.surface = envs("DFZ_SURFACE");
    c.binA = envs("DFZ_A");
    c.binB = envs("DFZ_B");
    c.mapA = strtoul(envs("DFZ_A_MAP", "0").c_str(), 0, 10);
    c.mapB = strtoul(envs("DFZ_B_MAP", "0").c_str(), 0, 10);
    c.work = envs("DFZ_WORK", "/tmp/dfz_work") + "/" + std::to_string(getpid());
    c.find = envs("DFZ_FIND");
    c.data = envs("DFZ_DATA");
    c.childEnv = envs("DFZ_CHILD_ENV");
    if (getenv("DFZ_TIMEOUT_MS")) c.timeoutMs = atoi(getenv("DFZ_TIMEOUT_MS"));
    c.cap = atoi(envs("DFZ_CAP", "4").c_str());
    c.verbose = !envs("DFZ_VERBOSE").empty();
    c.keep = !envs("DFZ_KEEP").empty();
    // memory_mb: limit of the resident memory of one program run; runProg ends a run that passes it (class
    // `memory`). An input that makes a program allocate without bound then has the same result everywhere, instead
    // of meeting the machine's out-of-memory killer, which ends whichever process of the job is largest.
    auto mm = c.kv.find("memory_mb");
    c.memoryMb = mm == c.kv.end() ? 0 : atol(mm->second.c_str());
    if (getenv("DFZ_MEMORY_MB")) c.memoryMb = atol(getenv("DFZ_MEMORY_MB"));
    return c;
}
// options of the target's [adapter] table and of the current [surface.<name>] table
inline string opt(const string &key, const string &def = "") {
    auto it = cfg().kv.find(key);
    return it == cfg().kv.end() ? def : it->second;
}
inline vector<string> opts(const string &key) {
    vector<string> r;
    auto range = cfg().kv.equal_range(key);
    for (auto it = range.first; it != range.second; ++it) r.push_back(it->second);
    return r;
}
inline string adapterOpt(const string &k, const string &def = "") { return opt("adapter." + k, def); }
inline string surfaceOpt(const string &k, const string &def = "") { return opt("surface." + cfg().surface + "." + k, def); }

// ------------------------------------------------------------------ the adapter interface
// A test case, filled by the adapter's decode().
struct Case {
    string surface;               // the surface being fuzzed
    string in;                    // the case's input directory ({in}); the adapter writes its files there
    map<string, string> vars;     // extra placeholders for the configured command line, e.g. {"reads_ext", ".bam"}
    vector<string> args;          // options appended to the command line of both programs
    vector<string> argsA, argsB;  // options appended for one program only
    vector<vector<string>> preA, preB; // extra runs of one program before the compared run: the full command line plus
                                  // these options (e.g. a run that prepares a cache the compared run then reads)
    set<string> tags;             // short labels that become part of the signature (e.g. the non-default options)
    string note;                  // free text for people (shown by `dfz_tool replay`)
    void put(const string &name, const string &content) const { writeFile(in + "/" + name, content); }
};
struct Run {
    string cls;                // ok | err | sig<N> | timeout | memory | spawnfail (timeout, memory: ended by the harness)
    int code = 0;              // exit code when cls is ok or err
    string msg;                // normalised error line of a failed run
    string out;                // this side's output directory
    string log;                // file with stdout and stderr
    vector<unsigned char> cov; // coverage map of the child (empty when not instrumented)
    long ms = 0;
    long peakMb = 0;           // largest resident memory seen while it ran (sampled)
    long cpuMs = 0;            // CPU time of the program, all its threads (user + system); `ms` is wall time
};
struct Finding {
    string atoms;  // sorted, space separated
    string detail; // excerpt for people
};
struct Outcome {
    Run a, b;
    vector<Finding> findings;
};
} // namespace dfz

// The adapter defines dfz_decode; the other two hooks are optional (weak defaults below).
//   dfz_decode     turn fuzz bytes into a test case; false = not a valid input for this surface
//   dfz_normalize  rewrite an output file's content before it is compared (e.g. a binary format to canonical text);
//                  return false to leave it unchanged. `side` is 0 for a, 1 for b.
//   dfz_compare    the last word on what counts as a difference for this case: runs after the configured
//                  comparison with the case, both runs and the findings so far, and may add findings or remove
//                  one that the rewrite documents as intended (an exemption). A finding that is removed is neither
//                  stored nor fed back to the fuzzer
bool dfz_decode(const uint8_t *data, size_t size, dfz::Case &c);
bool dfz_normalize(const dfz::Case &c, const std::string &file, int side, std::string &content);
void dfz_compare(const dfz::Case &c, const dfz::Run &a, const dfz::Run &b, std::vector<dfz::Finding> &out);
#ifdef DFZ_MAIN
__attribute__((weak)) bool dfz_normalize(const dfz::Case &, const std::string &, int, std::string &) { return false; }
__attribute__((weak)) void dfz_compare(const dfz::Case &, const dfz::Run &, const dfz::Run &, std::vector<dfz::Finding> &) {}
#endif

namespace dfz {
// ------------------------------------------------------------------ running one program
struct ShmMap {
    int id = -1;
    unsigned char *p = nullptr;
    size_t size = 0;
    void ensure(size_t n) {
#ifdef __linux__
        if (p || !n) return;
        size = n;
        id = shmget(IPC_PRIVATE, n, IPC_CREAT | 0600);
        if (id < 0) return;
        void *a = shmat(id, nullptr, 0);
        // marked for removal at once: it lives while attached (Linux lets the child attach by id until then), so a
        // killed harness cannot leak a segment
        shmctl(id, IPC_RMID, nullptr);
        p = a == (void *)-1 ? nullptr : (unsigned char *)a;
#else
        (void)n;
#endif
    }
};
// Keep the template of a message only: quoted text, paths and numbers depend on the input.
inline string normMsg(string s) {
    const Config &C = cfg();
    if (!C.errorStrip.empty()) {
        static const std::regex re(C.errorStrip);
        s = std::regex_replace(s, re, "");
    }
    size_t q1 = s.find_first_of("'\"`"), q2 = s.find_last_of("'\"`");
    if (q1 != string::npos) s = s.substr(0, q1) + "\x01" + (q2 > q1 ? s.substr(q2 + 1) : "");
    string r;
    for (size_t i = 0; i < s.size() && r.size() < 80; i++) {
        unsigned char c = s[i];
        if (c == 1) r += '*';
        else if (c == '/' && i + 1 < s.size() && (isalnum((unsigned char)s[i + 1]) || s[i + 1] == '_')) {
            while (i + 1 < s.size() && !isspace((unsigned char)s[i + 1])) i++;
            r += '*';
        } else if (isdigit(c)) { if (r.empty() || r.back() != '#') r += '#'; }
        else if (c < 32 || c > 126) { if (r.empty() || r.back() != '?') r += '?'; }
        else if (c == ' ') r += '_';
        else r += (char)c;
    }
    return r;
}
inline Run runProg(const string &bin, const string &sideName, const vector<string> &args, const string &outDir, const string &logFile,
                   ShmMap *shm, bool clearMap = true) {
    const Config &C = cfg();
    Run r;
    r.out = outDir;
    r.log = logFile;
    vector<char *> argv;
    argv.push_back((char *)bin.c_str());
    for (auto &a : args) argv.push_back((char *)a.c_str());
    argv.push_back(nullptr);
    vector<string> envStore;
    for (char **e = environ; *e; e++) {
        // the fuzzer's own settings must not reach the programs under test
        if (!strncmp(*e, "__AFL_", 6) || !strncmp(*e, "AFL_", 4) || !strncmp(*e, "LD_PRELOAD=", 11) || !strncmp(*e, "ASAN_OPTIONS=", 13)) continue;
        envStore.push_back(*e);
    }
    for (const string &e : C.env) envStore.push_back(replaceAll(e, "{side}", sideName));
    if (!C.childEnv.empty())
        for (const string &e : split(C.childEnv, ';'))
            if (!e.empty()) envStore.push_back(replaceAll(e, "{side}", sideName));
    if (shm && shm->p) {
        if (clearMap) memset(shm->p, 0, shm->size);
        envStore.push_back("__AFL_SHM_ID=" + std::to_string(shm->id));
        envStore.push_back("AFL_MAP_SIZE=" + std::to_string(shm->size));
    }
    vector<char *> envp;
    for (auto &e : envStore) envp.push_back((char *)e.c_str());
    envp.push_back(nullptr);
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0);
    posix_spawn_file_actions_addopen(&fa, 1, logFile.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
    posix_spawn_file_actions_adddup2(&fa, 1, 2);
    posix_spawn_file_actions_addclose(&fa, 198); // the fuzzer's fork-server pipes must not reach the child
    posix_spawn_file_actions_addclose(&fa, 199);
    pid_t pid;
    int rc = posix_spawn(&pid, bin.c_str(), &fa, nullptr, argv.data(), envp.data());
    posix_spawn_file_actions_destroy(&fa);
    if (rc != 0) { r.cls = "spawnfail"; return r; }
    int status = 0;
    // Wall time depends on what else the machine runs; the CPU time says how much the program itself worked.
    struct rusage ru;
    auto reap = [&](int flags) {
        pid_t w = wait4(pid, &status, flags, &ru);
        if (w == pid) r.cpuMs = (ru.ru_utime.tv_sec + ru.ru_stime.tv_sec) * 1000 + (ru.ru_utime.tv_usec + ru.ru_stime.tv_usec) / 1000;
        return w;
    };
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    static const long pageKb = sysconf(_SC_PAGESIZE) / 1024;
    const string statm = "/proc/" + std::to_string(pid) + "/statm";
    for (;;) {
        pid_t w = reap(WNOHANG);
        clock_gettime(CLOCK_MONOTONIC, &t1);
        r.ms = (t1.tv_sec - t0.tv_sec) * 1000 + (t1.tv_nsec - t0.tv_nsec) / 1000000;
        if (w == pid) break;
        if (w < 0 && errno != EINTR) { r.cls = "waitfail"; return r; }
        // resident memory: the second number of /proc/<pid>/statm, in pages
        if (FILE *f = fopen(statm.c_str(), "r")) {
            long total = 0, resident = 0;
            if (fscanf(f, "%ld %ld", &total, &resident) == 2) r.peakMb = std::max(r.peakMb, resident * pageKb / 1024);
            fclose(f);
        }
        if (C.memoryMb > 0 && r.peakMb > C.memoryMb) {
            kill(pid, SIGKILL);
            reap(0);
            r.cls = "memory";
            return r;
        }
        if (r.ms > C.timeoutMs) {
            // SIGTERM first, so that a program can clean up; SIGKILL 300 ms later
            kill(pid, SIGTERM);
            for (int i = 0; i < 150 && reap(WNOHANG) != pid; i++) {
                struct timespec ts = {0, 2000000};
                nanosleep(&ts, nullptr);
                if (i == 149) { kill(pid, SIGKILL); reap(0); }
            }
            r.cls = "timeout";
            return r;
        }
        struct timespec ts = {0, r.ms < 50 ? 300000 : 2000000};
        nanosleep(&ts, nullptr);
    }
    if (WIFSIGNALED(status)) r.cls = "sig" + std::to_string(WTERMSIG(status));
    else { r.code = WEXITSTATUS(status); r.cls = r.code == 0 ? "ok" : "err"; }
    if (shm && shm->p) r.cov.assign(shm->p, shm->p + shm->size);
    if (r.cls != "ok" && C.errorLine != "none") {
        string o;
        readFile(logFile, o);
        vector<string> ls = lines(o);
        // With error_prefix: the first line that starts like an error report of the program (after error_strip).
        // Otherwise: the last non-empty line.
        if (!C.errorPrefix.empty()) {
            for (const string &l : ls) {
                string m = normMsg(l);
                bool hit = false;
                for (const string &p : C.errorPrefix) hit |= m.compare(0, p.size(), p) == 0;
                if (hit) { r.msg = m; break; }
            }
        } else
            for (size_t i = ls.size(); i-- > 0;)
                if (ls[i].find_first_not_of(" \t\r") != string::npos) { r.msg = normMsg(ls[i]); break; }
    }
    if (C.verbose) {
        size_t nz = 0;
        for (unsigned char b : r.cov) nz += b != 0;
        fprintf(stderr, "dfz: %s -> %s (%ld ms), coverage map %zu bytes, %zu non-zero\n", sideName.c_str(), r.cls.c_str(), r.ms, r.cov.size(), nz);
    }
    return r;
}

// ------------------------------------------------------------------ comparators
inline string joinAtoms(const set<string> &a) {
    string s;
    for (auto &x : a) s += (s.empty() ? "" : " ") + x;
    return s;
}
// bytes: equal or not
inline void cmpBytes(const string &tag, const string &a, const string &b, set<string> &atoms, string &detail) {
    if (a == b) return;
    atoms.insert(tag + ":bytes");
    size_t i = 0;
    while (i < a.size() && i < b.size() && a[i] == b[i]) i++;
    detail += tag + ": sizes " + std::to_string(a.size()) + " / " + std::to_string(b.size()) + ", first difference at byte " + std::to_string(i) + "\n";
}
// tsv: line by line, field by field. Atoms name the column (and, with sub-separators in `sub`, the position inside
// it), never the content. Lines starting with '#' count as header. sub == "\n" means: do not split lines (comparator
// "lines").
inline void cmpTsv(const string &tag, const string &a, const string &b, const string &sub, set<string> &atoms, string &detail) {
    if (a == b) return;
    vector<string> la = lines(a), lb = lines(b);
    bool whole = sub == "\n";
    if (la.size() != lb.size()) {
        atoms.insert(tag + (la.size() > lb.size() ? ":lines_a>b" : ":lines_a<b"));
        detail += tag + ": " + std::to_string(la.size()) + " / " + std::to_string(lb.size()) + " lines\n";
        // name what one side has and the other has not (as multisets)
        std::multiset<string> ma(la.begin(), la.end()), mb(lb.begin(), lb.end());
        int shown = 0;
        for (const string &l : la) { auto it = mb.find(l); if (it != mb.end()) mb.erase(it); else if (shown++ < 2) detail += "  only a: " + clip(l) + "\n"; }
        shown = 0;
        for (const string &l : lb) { auto it = ma.find(l); if (it != ma.end()) ma.erase(it); else if (shown++ < 2) detail += "  only b: " + clip(l) + "\n"; }
        return;
    }
    int shown = 0;
    for (size_t i = 0; i < la.size(); i++) {
        if (la[i] == lb[i]) continue;
        if (shown++ < 3) detail += tag + " line " + std::to_string(i + 1) + ":\n  a: " + clip(la[i]) + "\n  b: " + clip(lb[i]) + "\n";
        if (!la[i].empty() && la[i][0] == '#') { atoms.insert(tag + ":header"); continue; }
        if (whole) { atoms.insert(tag + ":line"); continue; }
        vector<string> fa = split(la[i], '\t'), fb = split(lb[i], '\t');
        if (fa.size() != fb.size()) { atoms.insert(tag + ":nfields"); continue; }
        for (size_t k = 0; k < fa.size(); k++) {
            if (fa[k] == fb[k]) continue;
            string col = tag + ":c" + std::to_string(k + 1);
            if (!sub.empty()) {
                // split on any of the sub-separators
                auto subsplit = [&](const string &s) {
                    vector<string> r(1);
                    for (char ch : s) { if (sub.find(ch) != string::npos) r.emplace_back(); else r.back().push_back(ch); }
                    return r;
                };
                vector<string> sa = subsplit(fa[k]), sb = subsplit(fb[k]);
                if (sa.size() == sb.size() && sa.size() > 1) {
                    for (size_t j = 0; j < sa.size(); j++)
                        if (sa[j] != sb[j]) atoms.insert(col + "." + std::to_string(j + 1));
                    continue;
                }
            }
            atoms.insert(col);
        }
    }
}
// json: structural comparison; an atom names the last object key on the path to a difference.
struct JVal {
    char t = 'n';                              // o a s x(number, true, false, null as text)
    string s;
    vector<std::pair<string, JVal>> kids;      // object members (key) or array elements (key empty)
};
struct JParser {
    const string &s;
    size_t i = 0;
    bool ok = true;
    int depth = 0;
    explicit JParser(const string &t) : s(t) {}
    void ws() { while (i < s.size() && isspace((unsigned char)s[i])) i++; }
    string str() {
        string r;
        i++;
        while (i < s.size() && s[i] != '"') {
            if (s[i] == '\\' && i + 1 < s.size()) r += s[i++];
            r += s[i++];
        }
        if (i >= s.size()) ok = false;
        i++;
        return r;
    }
    JVal val() {
        JVal v;
        ws();
        if (i >= s.size() || ++depth > 200) { ok = false; return v; }
        if (s[i] == '{') {
            v.t = 'o';
            i++;
            for (ws(); ok && i < s.size() && s[i] != '}'; ws()) {
                if (s[i] != '"') { ok = false; break; }
                string k = str();
                ws();
                if (i >= s.size() || s[i] != ':') { ok = false; break; }
                i++;
                v.kids.push_back({k, val()});
                ws();
                if (i < s.size() && s[i] == ',') i++;
            }
            if (i >= s.size()) ok = false;
            i++;
        } else if (s[i] == '[') {
            v.t = 'a';
            i++;
            for (ws(); ok && i < s.size() && s[i] != ']'; ws()) {
                v.kids.push_back({"", val()});
                ws();
                if (i < s.size() && s[i] == ',') i++;
            }
            if (i >= s.size()) ok = false;
            i++;
        } else if (s[i] == '"') {
            v.t = 's';
            v.s = str();
        } else {
            v.t = 'x';
            size_t j = i;
            while (i < s.size() && !isspace((unsigned char)s[i]) && !strchr(",]}", s[i])) i++;
            v.s = s.substr(j, i - j);
            if (v.s.empty()) ok = false;
        }
        depth--;
        return v;
    }
};
// Object keys can be data (identifiers used as keys): digit runs in a key are masked in atoms.
inline string maskKey(const string &k) {
    if (k.empty()) return "(root)";
    string r;
    for (char ch : k) {
        if (isdigit((unsigned char)ch)) { if (r.empty() || r.back() != '#') r += '#'; }
        else r += ch;
    }
    return r.size() > 40 ? r.substr(0, 40) : r;
}
inline void jsonDiff(const string &tag, const string &key, const string &path, const JVal &a, const JVal &b, set<string> &atoms, string &detail, int &shown) {
    auto note = [&](const string &what) {
        atoms.insert(tag + ":" + maskKey(key) + what);
        if (shown++ < 3) detail += tag + " " + path + what + ": a=" + clip(a.t == 's' || a.t == 'x' ? a.s : string(1, a.t), 120) + " b=" + clip(b.t == 's' || b.t == 'x' ? b.s : string(1, b.t), 120) + "\n";
    };
    if (a.t != b.t) { note(":type"); return; }
    if (a.t == 's' || a.t == 'x') { if (a.s != b.s) note(""); return; }
    if (a.t == 'a') {
        if (a.kids.size() != b.kids.size()) { note(":len"); return; }
        for (size_t i = 0; i < a.kids.size(); i++) jsonDiff(tag, key, path + "[" + std::to_string(i) + "]", a.kids[i].second, b.kids[i].second, atoms, detail, shown);
        return;
    }
    // objects: by key; member order is compared too
    map<string, const JVal *> mb;
    for (auto &k : b.kids) mb[k.first] = &k.second;
    set<string> seen;
    bool sameOrder = a.kids.size() == b.kids.size();
    for (size_t i = 0; i < a.kids.size(); i++) {
        auto &k = a.kids[i];
        seen.insert(k.first);
        if (sameOrder && b.kids[i].first != k.first) sameOrder = false;
        auto it = mb.find(k.first);
        if (it == mb.end()) { atoms.insert(tag + ":" + maskKey(k.first) + ":only_a"); if (shown++ < 3) detail += tag + " " + path + "." + k.first + ": only a\n"; continue; }
        jsonDiff(tag, k.first, path + "." + k.first, k.second, *it->second, atoms, detail, shown);
    }
    for (auto &k : b.kids)
        if (!seen.count(k.first)) { atoms.insert(tag + ":" + maskKey(k.first) + ":only_b"); if (shown++ < 3) detail += tag + " " + path + "." + k.first + ": only b\n"; }
    if (!sameOrder && a.kids.size() == b.kids.size() && seen.size() == mb.size()) atoms.insert(tag + ":" + maskKey(key) + ":order");
}
inline void cmpJson(const string &tag, const string &a, const string &b, set<string> &atoms, string &detail) {
    if (a == b) return;
    JParser pa(a), pb(b);
    JVal va = pa.val(), vb = pb.val();
    if (!pa.ok || !pb.ok) {
        if (pa.ok != pb.ok) { atoms.insert(tag + (pa.ok ? ":unparsable_b" : ":unparsable_a")); detail += tag + ": one side is not JSON\n"; }
        else cmpBytes(tag, a, b, atoms, detail);
        return;
    }
    int shown = 0;
    size_t before = atoms.size();
    jsonDiff(tag, "", "", va, vb, atoms, detail, shown);
    if (atoms.size() == before) atoms.insert(tag + ":format"); // same values, different text
}

// ------------------------------------------------------------------ run a case on both programs and compare
inline ShmMap &shmA() { static ShmMap m; return m; }
inline ShmMap &shmB() { static ShmMap m; return m; }
inline string caseDir() { return cfg().work + "/case"; }
inline vector<string> commandLine(const Case &c, int side) {
    const Config &C = cfg();
    vector<string> t = C.argv;
    for (auto &a : side ? C.argvB : C.argvA) t.push_back(a);
    for (auto &a : c.args) t.push_back(a);
    for (auto &a : side ? c.argsB : c.argsA) t.push_back(a);
    string out = caseDir() + (side ? "/b" : "/a");
    for (string &a : t) {
        a = replaceAll(a, "{in}", c.in);
        a = replaceAll(a, "{out}", out);
        a = replaceAll(a, "{data}", C.data);
        for (auto &v : c.vars) a = replaceAll(a, "{" + v.first + "}", v.second);
    }
    return t;
}
// What a sanitizer or the C++ library's assertions reported in the log of a program that died: kind and place,
// without directories, addresses and the numbers of the message ("" when the log has no such report, as on builds
// without sanitizers; a plain abort is not a report).
inline string deathReport(const string &logFile) {
    string o;
    if (!readFile(logFile, o)) return "";
    static const std::regex assertion("(\\S+):(\\d+): .*Assertion [`'](.*)' failed"), ubsan("(\\S+):(\\d+):\\d+: runtime error: (.*)"),
        asanKind("ERROR: AddressSanitizer: ([\\w-]+)"), asanPlace("SUMMARY: AddressSanitizer: \\S+ (\\S+?):(\\d+)"),
        address("0x[0-9a-fA-F]+"), number("-?\\d+");
    auto base = [](const string &path) { size_t s = path.rfind('/'); return s == string::npos ? path : path.substr(s + 1); };
    auto plain = [&](string s) {
        s = std::regex_replace(std::regex_replace(s, address, "ADDR"), number, "N");
        for (char &ch : s) if (ch == ' ' || ch == '\t') ch = '_';
        return s.substr(0, 100);
    };
    string kind;
    for (const string &l : lines(o)) {
        std::smatch m;
        if (kind.empty() && std::regex_search(l, m, assertion)) return "assertion_" + base(m[1]) + ":" + m[2].str() + "_" + plain(m[3]);
        if (kind.empty() && std::regex_search(l, m, ubsan)) return "ubsan_" + base(m[1]) + ":" + m[2].str() + "_" + plain(m[3]);
        if (kind.empty() && std::regex_search(l, m, asanKind)) { kind = m[1]; if (kind == "ABRT") return ""; continue; }
        if (!kind.empty() && std::regex_search(l, m, asanPlace)) return "asan_" + kind + "_" + base(m[1]) + ":" + m[2].str();
    }
    return kind.empty() ? "" : "asan_" + kind;
}
// Prepare an empty case directory; the adapter's decode() then fills c.in.
inline void newCase(Case &c) {
    const Config &C = cfg();
    string d = caseDir();
    rmTree(d);
    mkdirs(d + "/in");
    mkdir((d + "/a").c_str(), 0700);
    mkdir((d + "/b").c_str(), 0700);
    c.surface = C.surface;
    c.in = d + "/in";
}
inline void runCase(const Case &c, Outcome &o) {
    const Config &C = cfg();
    string d = caseDir();
    shmA().ensure(C.mapA);
    shmB().ensure(C.mapB);
    // extra runs first; their coverage is added to the compared run's map
    for (int side = 0; side < 2; side++) {
        const auto &pre = side ? c.preB : c.preA;
        ShmMap *shm = (side ? C.mapB : C.mapA) ? (side ? &shmB() : &shmA()) : nullptr;
        if (shm && shm->p) memset(shm->p, 0, shm->size);
        for (size_t i = 0; i < pre.size(); i++) {
            vector<string> args = commandLine(c, side);
            for (const string &a : pre[i]) args.push_back(a);
            runProg(side ? C.binB : C.binA, side ? C.nameB : C.nameA, args, d + (side ? "/b" : "/a"), d + (side ? "/b" : "/a") + ".pre" + std::to_string(i) + ".log", shm, false);
        }
    }
    o.a = runProg(C.binA, C.nameA, commandLine(c, 0), d + "/a", d + "/a.log", C.mapA ? &shmA() : nullptr, false);
    o.b = runProg(C.binB, C.nameB, commandLine(c, 1), d + "/b", d + "/b.log", C.mapB ? &shmB() : nullptr, false);
    bool sameExit = C.exitMode == "code" ? (o.a.cls == o.b.cls && o.a.code == o.b.code) : o.a.cls == o.b.cls;
    if (!sameExit) {
        Finding f;
        set<string> at;
        at.insert("exit:" + C.nameA + "=" + o.a.cls + "," + C.nameB + "=" + o.b.cls);
        if (!o.a.msg.empty()) at.insert("msg_" + C.nameA + ":" + o.a.msg);
        if (!o.b.msg.empty()) at.insert("msg_" + C.nameB + ":" + o.b.msg);
        f.atoms = joinAtoms(at);
        string la, lb;
        readFile(o.a.log, la);
        readFile(o.b.log, lb);
        auto tail = [](const string &s) { vector<string> l = lines(s); string r; for (size_t i = l.size() > 3 ? l.size() - 3 : 0; i < l.size(); i++) r += "    " + clip(l[i]) + "\n"; return r; };
        f.detail = C.nameA + ": " + o.a.cls + (o.a.cls == "err" ? " (exit " + std::to_string(o.a.code) + ")" : "") + "\n" + tail(la) + C.nameB + ": " + o.b.cls + (o.b.cls == "err" ? " (exit " + std::to_string(o.b.code) + ")" : "") + "\n" + tail(lb);
        o.findings.push_back(f);
    }
    // A program that dies with a report of a sanitizer or of a failed assertion is a finding by itself, also when the
    // other program ends the same way: a memory error or undefined behaviour is found where it happens, not only
    // where it changes an output.
    for (int side = 0; side < 2; side++) {
        const Run &r = side ? o.b : o.a;
        if (r.cls.compare(0, 3, "sig") != 0) continue;
        string report = deathReport(r.log);
        if (report.empty()) continue;
        Finding f;
        f.atoms = "report_" + (side ? C.nameB : C.nameA) + ":[" + report + "]";
        f.detail = (side ? C.nameB : C.nameA) + ": " + r.cls + ", " + report + "\n";
        o.findings.push_back(f);
    }
    // outputs are compared when both runs succeeded (with compare_on_error: also when both exited with an error;
    // what a failed run leaves behind is normally not part of a program's contract)
    if (o.a.cls == o.b.cls && (o.a.cls == "ok" || (o.a.cls == "err" && opt("compare_on_error") == "1"))) {
        vector<OutputSpec> outs = C.outputs;
        if (opt("compare_stdout") == "1") outs.push_back({"", "lines", ""});
        for (const OutputSpec &s : outs) {
            string fa, fb;
            bool ea = readFile(s.file.empty() ? o.a.log : o.a.out + "/" + s.file, fa), eb = readFile(s.file.empty() ? o.b.log : o.b.out + "/" + s.file, fb);
            string tag = s.file.empty() ? "stdout" : s.file;
            Finding f;
            set<string> at;
            if (!ea && !eb) continue;
            if (ea != eb) { at.insert(tag + (ea ? ":only_" + C.nameA : ":only_" + C.nameB)); f.detail = tag + ": written by one program only\n"; }
            else {
                if (fa != fb) { dfz_normalize(c, tag, 0, fa); dfz_normalize(c, tag, 1, fb); }
                if (s.cmp == "bytes") cmpBytes(tag, fa, fb, at, f.detail);
                else if (s.cmp == "lines") cmpTsv(tag, fa, fb, "\n", at, f.detail);
                else if (s.cmp == "tsv") cmpTsv(tag, fa, fb, s.opt, at, f.detail);
                else if (s.cmp == "json") cmpJson(tag, fa, fb, at, f.detail);
                else if (s.cmp != "none") cmpBytes(tag, fa, fb, at, f.detail);
            }
            if (at.empty()) continue;
            f.atoms = joinAtoms(at);
            o.findings.push_back(f);
        }
    }
    dfz_compare(c, o.a, o.b, o.findings);
}
inline string signature(const Case &c, const Finding &f) { return c.surface + " [" + joinAtoms(c.tags) + "] " + f.atoms; }

// Decode, run, compare. Returns false when the input is not a valid case.
inline bool runInput(const uint8_t *d, size_t n, Case &c, Outcome &o) {
    newCase(c);
    if (!dfz_decode(d, n, c)) return false;
    runCase(c, o);
    return true;
}

// Stored under <find>/<hash of signature>/: sig.txt (signature, note, detail) and up to DFZ_CAP inputs
// (<hash of input>.bin). `full` marks a directory that reached the cap, so later hits cost one stat().
inline void store(const Case &c, const Outcome &o, const uint8_t *d, size_t n) {
    const Config &C = cfg();
    if (C.find.empty()) return;
    // A campaign that is being stopped kills the programs under the harness; what a killed program leaves behind is
    // not a finding. The campaign creates this file before it sends any signal.
    static const char *stopFile = getenv("DFZ_STOP_FILE");
    struct stat stopped;
    if (stopFile && stat(stopFile, &stopped) == 0) return;
    for (const Finding &f : o.findings) {
        string sig = signature(c, f), dir = C.find + "/" + hex16(fnv(sig));
        struct stat st;
        if (stat((dir + "/full").c_str(), &st) == 0) continue;
        string file = dir + "/" + hex16(fnv(string((const char *)d, n))) + ".bin";
        if (stat(file.c_str(), &st) == 0) continue;
        mkdirs(dir);
        if (stat((dir + "/sig.txt").c_str(), &st) != 0) writeFile(dir + "/sig.txt", sig + "\n" + c.note + "\n" + f.detail);
        int count = 0;
        if (DIR *dd = opendir(dir.c_str())) {
            while (dirent *e = readdir(dd)) count += strstr(e->d_name, ".bin") != nullptr;
            closedir(dd);
        }
        if (count >= C.cap) { writeFile(dir + "/full", ""); continue; }
        writeFile(file, string((const char *)d, n));
    }
}
} // namespace dfz
