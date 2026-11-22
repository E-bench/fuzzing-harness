// Adapter of the eh-nueh target: turns fuzz bytes into an ExpansionHunter test case (reference FASTA, variant
// catalog, reads file, options) and makes the realigned BAMs comparable. Everything specific to ExpansionHunter is in
// this directory; the pipeline only sees the hooks of dfz.h. The analysis mode is not chosen here: it is part of the
// command line in target.toml.
//
// Surfaces (DFZ_SURFACE):
//   locus    structured: a small genome with 1-4 loci, a catalog describing them, a simulated sample, options
//   catalog  the bytes are catalog.json; genome and reads come from a fixed base case ({data}/base/<n>)
//   sam      the bytes are the reads file as SAM text; genome and catalog from a base case
//   bam      the bytes are an uncompressed BAM stream, wrapped into BGZF blocks here; genome and catalog from a base case
//
// Layout of a `locus` input (a zero byte always means "the default"):
//   header  16 bytes   H_*   container, options, number of loci, read length, fragment length, depth, errors, ...
//   loci    16 bytes each, 1-4 of them   L_*   structure, motifs, reference sizes, catalog fields, sample alleles
//   extras   8 bytes each, up to 12      X_*   single unusual records (unpaired, secondary, unmapped, odd flags, ...)
#include "dfz.h"
#include "bam.h"

using namespace dfz;

namespace {
struct Rng {
    uint64_t s;
    explicit Rng(uint64_t seed) : s(seed * 0x9E3779B97F4A7C15ULL + 0x1234567) { next(); next(); }
    uint64_t next() { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return s; }
    uint32_t below(uint32_t n) { return n ? (uint32_t)((next() >> 20) % n) : 0; }
    char base() { return "ACGT"[next() >> 33 & 3]; }
};
char comp(char c) {
    switch (c) { case 'A': return 'T'; case 'C': return 'G'; case 'G': return 'C'; case 'T': return 'A';
                 case 'a': return 't'; case 'c': return 'g'; case 'g': return 'c'; case 't': return 'a'; default: return 'N'; }
}
string revcomp(const string &s) {
    string r(s.rbegin(), s.rend());
    for (char &c : r) c = comp(c);
    return r;
}
string rep(const string &u, int n) { string s; for (int i = 0; i < n; i++) s += u; return s; }
string randomSeq(uint64_t seed, int n) { Rng r(seed); string s(n, 'A'); for (char &c : s) c = r.base(); return s; }

// ---------------------------------------------------------------- tables
const char *MOTIFS[] = {"CAG", "CCG", "CGG", "GAA", "CTG", "A", "C", "AT", "GC", "AAG", "TTTA", "ATTCT", "AAAAG", "GGCCCC",
                        "CCCCGCCCCGCG", "CAGCAA", "CNG", "RCG", "GCN", "T", "AC", "AGC", "TGGAA", "AAAAT", "CCTG", "GGC",
                        "ATXN", "cag", "CA", "TTC", "GGGGCC", "AARRG"};
const int NMOTIF = sizeof MOTIFS / sizeof *MOTIFS;
const char *LITERALS[] = {"CAACAG", "CTGT", "A", "ACGTAC", "TTGACCAT", "GG", "CAGCAG", "N", "ACGTACGTACGTAGCTAGCTAGGATC"};
const int NLIT = sizeof LITERALS / sizeof *LITERALS;
const char *SWAPS[][2] = {{"C", "T"}, {"CAGT", "CGTTG"}, {"CAG", "CAT"}, {"A", "ACGT"}, {"GATTACA", "G"}, {"AC", "GT"}, {"CAG", "CAG"}, {"C", "N"}};
const int NSWAP = sizeof SWAPS / sizeof *SWAPS;
const char *OPTIONALS[] = {"CTGGC", "A", "CAG", "ACGTACGTAC", "GGCCCC", "N"};
const int NOPT = sizeof OPTIONALS / sizeof *OPTIONALS;
// Structure templates. R r  repeat * of motif slot 0/1/2 (motif index = L_MOTIF + slot)   P p q  the same with +
//                      L    literal   S  swap (a|b)   O  optional (x)?
const char *TEMPLATES[] = {"R", "P", "RLr", "S", "O", "PLS", "RLOLr", "Rr", "SLS", "RLrLp", "LR", "RL", "OLR", "SR", "RS",
                           "RLS", "PLp", "RLRLR", "OO", "SLO", "RrR", "LRLrL", "PS", "RO",
                           "RLQ", "RQ", "RQQ", "RLrLRLrLRLrLRLr"};   // Q: a further repeat with the first repeat's motif
const int NTEMPL = sizeof TEMPLATES / sizeof *TEMPLATES;
const int READLEN[] = {150, 100, 125, 75, 50, 151, 250, 36, 30, 20, 101, 3300, 1400, 200, 300, 10}; // 1400, 3300: beyond 16-bit DP scores
const int FRAGLEN[] = {350, 300, 400, 450, 250, 200, 500, 600, 160, 1000, 150, 100, 320, 380, 800, 50};
const int COVER[] = {20, 15, 30, 10, 5, 3, 1, 0, 40, 25, 12, 8, 2, 50, 60, 18};
const int EXTLEN[] = {-1, 1000, 500, 1500, 0, 100, 2000, 50, 999, 1001, 300, -5, 10000, 1, 750, 1250};
const int THREADS[] = {1, 2, 4, 8}; // the rewrite switches its reader at 4
const int SPACING[] = {4000, 2500, 1500, 600, 300, 100, 40, 0};
const int LEFTFLANK[] = {1300, 1000, 999, 1001, 600, 300, 100, 20, 5, 0, 2000, 1500, 150, 1100, 50, 1};
const int BLOCK[] = {65280, 4096, 1024, 256, 100, 37, 16, 1};
// extra loci without reads, on a contig of their own: catalog sizes around the rewrite's batch limits (256 loci per
// decoding grain, 1,024 for parallel output). The original needs about 1.4 ms per locus, so the next limit (4,096,
// parallel interval trees) would cost seconds per case and is left out.
const int EXTRA_LOCI[] = {0, 3, 40, 255, 256, 257, 600, 1023, 1024, 1100, 513, 300, 1025, 100, 700, 900};
// sample allele sizes: a delta to the reference size (below 32) or an absolute size
int alleleSize(int code, int ref) {
    static const int ABS[] = {0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 100, 120, 150, 200, 250, 300, 400, 500, 700, 1000};
    static const int DELTA[] = {0, 1, -1, 2, -2, 3, -3, 4, 5, -5, 6, 7, 8, 10, -10, 12, 15, 20, -20, 25, 30, 40, 50, 60, 80, 100, 150, 200, -4, -6, -8, -15};
    int v = code & 0x80 ? ABS[code & 31] : ref + DELTA[code & 31];
    return v < 0 ? 0 : v;
}

// ---------------------------------------------------------------- the model of one locus
struct Feature {
    char kind;              // R P (repeats) L S O
    string a, b;            // motif / literal / swap alternatives / optional sequence
    int refCopies = 0;      // repeats: copies in the reference; S: 0 or 1 = which alternative, 2 = neither; O: 1 present, 0 absent
    int hap[2] = {0, 0};    // the sample: copies / alternative / presence, per haplotype
    int refStart = 0, refEnd = 0; // reference interval (0-based, half open) on the contig
};
struct Locus {
    string id, contig;
    int contigIndex = 0;
    vector<Feature> feats;
    int start = 0, end = 0;                 // reference interval of the whole structure
    vector<std::pair<string, std::pair<int, int>>> offtargets; // contig, [start, end)
    string json;
};
struct Contig { string name, seq; };

// IUPAC codes in a motif stand for sets of bases in the catalog; the genome and the reads carry one concrete base
string concrete(const string &s, Rng &r) {
    string o;
    for (char ch : s) {
        switch (toupper((unsigned char)ch)) {
        case 'N': o += r.base(); break;
        case 'R': o += "AG"[r.below(2)]; break;
        case 'Y': o += "CT"[r.below(2)]; break;
        case 'X': o += 'A'; break;
        default: o += (char)toupper((unsigned char)ch);
        }
    }
    return o;
}
string featureSeq(const Feature &f, int copiesOrChoice, Rng &r, int impure) {
    switch (f.kind) {
    case 'R': case 'P': {
        string s;
        for (int i = 0; i < copiesOrChoice; i++) {
            string u = concrete(f.a, r);
            if (impure && r.below(100) < (uint32_t)impure) u[r.below(u.size())] = r.base();
            s += u;
        }
        return s;
    }
    case 'L': return concrete(f.a, r);
    case 'S': return copiesOrChoice == 0 ? concrete(f.a, r) : copiesOrChoice == 1 ? concrete(f.b, r) : randomSeq(r.next(), f.a.size());
    case 'O': return copiesOrChoice ? concrete(f.a, r) : "";
    }
    return "";
}
string regionStr(const string &contig, int a, int b) { return contig + ":" + std::to_string(a) + "-" + std::to_string(b); }
string jstr(const string &s) { return "\"" + s + "\""; }

enum { H_CONTAINER, H_OPTIONS, H_LAYOUT, H_READLEN, H_SEED, H_DEPTH, H_ERRORS, H_NUOPT, H_NOISE, H_UNMAPPED, H_HEADER, H_EXT, H_QUAL, H_FRAG, H_NU2, H_RES, HDR };
enum { L_TEMPLATE, L_MOTIF, L_REF1, L_REF2, L_TYPE, L_FIELDS, L_PLACE, L_HAP1, L_HAP2, L_HAPX, L_IMPURE, L_SMALL, L_COVER, L_IRR, L_MALFORM, L_MISC, LREC };
const size_t XREC = 8, MAXEXTRA = 12;

struct Sim {
    const uint8_t *d;
    size_t n;
    Case &c;
    vector<Contig> contigs;
    vector<Locus> loci;
    vector<bam::Rec> recs;
    int readLen = 150, fragLen = 350;
    bool chrPrefix = true;
    bool allowInverted = adapterOpt("inverted_regions", "0") == "1";
    int nExtra = 0;
    Sim(const uint8_t *data, size_t size, Case &cs) : d(data), n(size), c(cs) {}
    uint8_t H(int i) const { return d[i]; }

    int contigId(const string &name) {
        for (size_t i = 0; i < contigs.size(); i++) if (contigs[i].name == name) return i;
        return -1;
    }
    // ---- genome and catalog
    void buildGenome() {
        int nloci = 1 + (H(H_LAYOUT) & 3);
        while (nloci > 1 && HDR + (size_t)nloci * LREC > n) nloci--;
        chrPrefix = !(H(H_LAYOUT) & 0x40);
        bool oneContig = H(H_LAYOUT) & 0x04;
        int spacing = SPACING[(H(H_LAYOUT) >> 3) & 7];
        for (int k = 0; k < nloci; k++) {
            const uint8_t *l = d + HDR + k * LREC;
            Locus L;
            L.id = (l[L_MALFORM] & 31) == 7 ? "L0" : "L" + std::to_string(k);
            // contig: the first locus's choice decides when all loci share a contig
            int kind = (oneContig ? d[HDR + L_PLACE] : l[L_PLACE]) & 3; // 0, 3 autosome; 1 X; 2 Y
            string base = kind == 1 ? "X" : kind == 2 ? "Y" : oneContig ? "1" : std::to_string(1 + k);
            L.contig = (chrPrefix ? "chr" : "") + base;
            if ((H(H_LAYOUT) & 0x80) && kind != 1 && kind != 2) L.contig = "scaffold" + base;   // a name of another shape
            int ci = contigId(L.contig);
            if (ci < 0) { contigs.push_back({L.contig, ""}); ci = contigs.size() - 1; }
            L.contigIndex = ci;
            Contig &C = contigs[ci];
            // left flank (unless the contig already has sequence: then the spacing to the previous locus)
            int left = C.seq.empty() ? LEFTFLANK[(l[L_PLACE] >> 2) & 15] : spacing;
            C.seq += randomSeq(1000 + ci * 131 + C.seq.size(), left);
            // features
            // (templates added after the first 24 sit at the top of the byte's range, so that older inputs keep their meaning)
            const char *t = TEMPLATES[l[L_TEMPLATE] >= 256 - (NTEMPL - 24) ? 24 + (l[L_TEMPLATE] - (256 - (NTEMPL - 24))) : l[L_TEMPLATE] % 24];
            Rng rr(7 + k);
            for (const char *p = t; *p; p++) {
                Feature f;
                f.kind = *p == 'r' || *p == 'p' ? (*p == 'r' ? 'R' : 'P') : *p == 'Q' ? 'R' : *p;
                int slot = *p == 'r' || *p == 'p' ? 1 : 0;
                for (const Feature &g : L.feats) if ((g.kind == 'R' || g.kind == 'P') && isupper((unsigned char)*p) && (*p == 'R' || *p == 'P')) slot = 2; // a second upper-case repeat
                if (*p == 'Q') slot = 3;
                switch (f.kind) {
                case 'R': case 'P':
                    f.a = MOTIFS[(l[L_MOTIF] + (slot == 3 ? 0 : slot) * 7) % NMOTIF];
                    f.refCopies = slot == 0 ? l[L_REF1] % 48 : l[L_REF2] % 48;
                    if (slot == 0 && l[L_REF1] == 0) f.refCopies = 10; // default
                    if (slot != 0 && l[L_REF2] == 0) f.refCopies = 6;
                    f.hap[0] = alleleSize(l[L_HAP1] + (slot ? l[L_HAPX] : 0), f.refCopies);
                    f.hap[1] = alleleSize(l[L_HAP2] + (slot ? l[L_HAPX] >> 4 : 0), f.refCopies);
                    break;
                case 'L': f.a = LITERALS[(l[L_SMALL] >> 4) % NLIT]; break;
                case 'S': {
                    int s = (l[L_SMALL] + L.feats.size()) % NSWAP;
                    f.a = SWAPS[s][0]; f.b = SWAPS[s][1];
                    f.refCopies = (l[L_MISC] >> 0) & 3; if (f.refCopies == 3) f.refCopies = 0;
                    f.hap[0] = (l[L_MISC] >> 2) & 1; f.hap[1] = (l[L_MISC] >> 3) & 1;
                    break;
                }
                case 'O':
                    f.a = OPTIONALS[(l[L_SMALL] + L.feats.size()) % NOPT];
                    f.refCopies = !((l[L_MISC] >> 4) & 1);
                    f.hap[0] = !((l[L_MISC] >> 5) & 1); f.hap[1] = !((l[L_MISC] >> 6) & 1);
                    break;
                }
                L.feats.push_back(f);
            }
            L.start = C.seq.size();
            for (Feature &f : L.feats) {
                f.refStart = C.seq.size();
                string s = featureSeq(f, f.refCopies, rr, 0);
                if ((l[L_MALFORM] & 31) == 6 && (f.kind == 'R' || f.kind == 'P') && !s.empty()) s[s.size() / 2] = comp(s[s.size() / 2]); // reference disagrees with the motif
                C.seq += s;
                f.refEnd = C.seq.size();
            }
            L.end = C.seq.size();
            loci.push_back(L);
        }
        // right flanks, then an extra contig that holds off-target regions and noise
        for (size_t ci = 0; ci < contigs.size(); ci++) {
            int right = 1300;
            for (size_t k = 0; k < loci.size(); k++) if (loci[k].contigIndex == (int)ci) right = LEFTFLANK[(d[HDR + k * LREC + L_FIELDS] >> 4) & 15];
            contigs[ci].seq += randomSeq(5000 + ci * 17, right);
        }
        nExtra = EXTRA_LOCI[H(H_NOISE) >> 4];
        if (nExtra) contigs.push_back({string(chrPrefix ? "chr" : "") + "8", randomSeq(8888, 2600 + 40 * nExtra)});
        contigs.push_back({string(chrPrefix ? "chr" : "") + "9", randomSeq(9999, 3000)});
        // flank styles: N runs, soft-masked or IUPAC reference
        for (size_t k = 0; k < loci.size(); k++) {
            const uint8_t *l = d + HDR + k * LREC;
            string &s = contigs[loci[k].contigIndex].seq;
            int style = l[L_PLACE] >> 6;
            int m = l[L_MALFORM] & 31;
            auto setAt = [&](int pos, char ch) { if (pos >= 0 && pos < (int)s.size()) s[pos] = ch; };
            if (m == 8) for (int i = 0; i < 5; i++) setAt(loci[k].start - 10 - i, 'N');
            if (m == 9) for (int i = 0; i < 6; i++) setAt(loci[k].end + 10 + i, 'N');
            if (style == 1) for (int i = std::max(0, loci[k].start - 200); i < std::min<int>(s.size(), loci[k].end + 200); i++) s[i] = tolower((unsigned char)s[i]);
            if (style == 2) { setAt(loci[k].start - 30, 'R'); setAt(loci[k].end + 30, 'Y'); }
            if (style == 3) { // the flank continues with motif copies that the catalog region does not cover
                const Feature *f = nullptr;
                for (const Feature &g : loci[k].feats) if (g.kind == 'R' || g.kind == 'P') { f = &g; break; }
                if (f) { Rng r2(3); string u = rep(concrete(f->a, r2), 4); for (size_t i = 0; i < u.size(); i++) setAt(loci[k].start - u.size() + i, u[i]); }
            }
        }
    }
    void buildCatalog() {
        string out = "[";
        for (size_t k = 0; k < loci.size(); k++) {
            Locus &L = loci[k];
            const uint8_t *l = d + HDR + k * LREC;
            int m = l[L_MALFORM] & 31, type = l[L_TYPE] & 3, fields = l[L_FIELDS] & 15;
            // input constraint (README.md): a region is written start-end with start <= end
            if ((m == 3 || m == 22 || m == 23) && !allowInverted) m = 0;
            if (m == 3 || m == 22 || m == 23) c.tags.insert("inverted-region");
            string structure;
            vector<string> regions, types, ids;
            int vi = 0;
            for (const Feature &f : L.feats) {
                if (f.kind == 'L') { structure += f.a; continue; }
                int a = f.refStart, b = f.refEnd;
                if (m == 3) std::swap(a, b);                       // start > end
                if (m == 4) b += 100000;                           // beyond the contig
                if (m == 10) { a += 1; }                           // shifted by one base
                if (m == 11 && vi == 0) { a -= 3; b += 3; }        // region wider than the structure
                string cn = m == 5 ? "chrUn" : L.contig;
                if (m == 24) cn = cn.compare(0, 3, "chr") == 0 ? cn.substr(3) : "chr" + cn;   // the other naming convention
                if (m == 26 && vi == 1) { a += 600; b += 600; }    // regions more than 500 apart
                string rs = regionStr(cn, a, b);
                if (m == 27) rs += "abc";
                if (m == 28) rs = cn + ":+" + std::to_string(a) + "-" + std::to_string(b);
                if (m == 29) rs = cn + ":" + std::to_string(a) + "-4294967296";
                if (m == 30) rs = cn + ":" + std::to_string(a);
                regions.push_back(rs);
                if (f.kind == 'R' || f.kind == 'P') {
                    structure += "(" + f.a + ")" + (f.kind == 'R' ? "*" : "+");
                    // the first repeat takes the locus's type; later ones are plain repeats unless asked otherwise
                    // (a locus with two RareRepeats is an error case)
                    int ty = vi == 0 || (l[L_IMPURE] & 0x80) ? type : 0;
                    types.push_back(ty == 1 ? "RareRepeat" : ty == 2 ? "SmallVariant" : ty == 3 ? "SMN" : "Repeat");
                } else if (f.kind == 'S') {
                    structure += "(" + f.a + "|" + f.b + ")";
                    types.push_back(l[L_TYPE] & 0x40 ? "SMN" : l[L_TYPE] & 0x80 ? "Repeat" : "SmallVariant");
                } else {
                    structure += "(" + f.a + ")?";
                    types.push_back(l[L_TYPE] & 0x80 ? "RareRepeat" : "SmallVariant");
                }
                ids.push_back(L.id + "_v" + std::to_string(m == 25 ? 0 : vi));
                vi++;
            }
            if (m == 1 && !regions.empty()) regions.pop_back();
            if (m == 2 && !types.empty()) types.pop_back();
            if (m == 12) structure = "(" + structure + ")";
            if (m == 13) structure += "(";
            if (m == 14) structure = "";
            if (m == 15) for (char &ch : structure) ch = tolower((unsigned char)ch);
            if (m == 16) structure += "ACGT";                      // trailing literal
            if (m == 17) types.assign(types.size(), "Bogus");
            if (m == 18) structure = replaceAll(structure, ")*", "){2,5}");
            if (m == 19) structure = replaceAll(structure, "|", "||");
            auto arr = [&](const vector<string> &v, bool forceArray) {
                if (v.size() == 1 && !forceArray) return jstr(v[0]);
                string s = "[";
                for (size_t i = 0; i < v.size(); i++) s += (i ? ", " : "") + jstr(v[i]);
                return s + "]";
            };
            string j = "\n{\n    \"LocusId\": " + jstr(L.id) + ",\n    \"LocusStructure\": " + jstr(structure) + ",\n";
            j += "    \"ReferenceRegion\": " + arr(regions, l[L_TYPE] & 4) + ",\n    \"VariantType\": " + arr(types, l[L_TYPE] & 4);
            if (l[L_TYPE] & 8) j += ",\n    \"VariantId\": " + arr(ids, l[L_TYPE] & 4);
            // off-target regions (on the extra contig, where the simulated in-repeat pairs are placed)
            int nOff = (l[L_TYPE] >> 4) & 3;
            if (m == 22 && !nOff) nOff = 1;
            if (nOff) {
                vector<string> offs;
                const string &ex = contigs.back().name;
                for (int i = 0; i < nOff; i++) {
                    int a = 500 + 700 * i + 300 * (int)k, b = a + (i == 2 ? 2 : 60);
                    L.offtargets.push_back({ex, {a, b}});
                    if (m == 22) { a -= 100; b += 1500; std::swap(a, b); }   // written end-start, and wide
                    offs.push_back(regionStr(ex, a, b));
                }
                j += ",\n    \"OfftargetRegions\": " + arr(offs, true);
            }
            if ((fields & 1) || m == 23) {
                int a = std::max(0, L.start - 200), b = L.end + 200;
                if (m == 23) std::swap(a, b);
                j += ",\n    \"TargetRegion\": " + arr({regionStr(L.contig, a, b)}, fields & 8);
            }
            static const char *ERR[] = {"0.02", "0", "0.5", "1", "0.001", "-0.1", "1e-9", "0.2"};
            static const char *LRT[] = {"10000", "1", "0", "1e9", "100", "-1", "10", "0.5"};
            static const char *MLC[] = {"10", "0", "1", "5", "20", "100", "1e6", "-1"};
            if (fields & 2) j += string(",\n    \"ErrorRate\": ") + ERR[l[L_MISC] & 7];
            if (fields & 4) j += string(",\n    \"LikelihoodRatioThreshold\": ") + LRT[(l[L_MISC] >> 3) & 7];
            if (l[L_MALFORM] & 0x80) j += string(",\n    \"MinimalLocusCoverage\": ") + MLC[((l[L_MALFORM] >> 5) & 3) | ((l[L_COVER] >> 4) & 1) << 2];
            j += "\n}";
            if (m == 20) j = replaceAll(j, "\"LocusId\"", "\"Locusid\"");
            if (m == 21) j = replaceAll(j, "\"VariantType\"", "\"VariantTypes\"");
            L.json = j;
            out += (k ? "," : "") + j;
        }
        if (nExtra) {
            const string &cn = contigs[contigs.size() - 2].name;
            static const char *U[] = {"CAG", "A", "AT", "GGCCCC"};
            for (int i = 0; i < nExtra; i++) {
                int a = 1300 + 40 * i;
                out += ",\n{\"LocusId\": \"E" + std::to_string(i) + "\", \"LocusStructure\": \"(" + U[i & 3] + ")*\", \"ReferenceRegion\": \"" +
                       regionStr(cn, a, a + 12) + "\", \"VariantType\": \"Repeat\"}";
            }
        }
        out += "\n]\n";
        int g = H(H_HEADER) >> 5;
        if (g == 1) out = "[]\n";                                  // empty catalog
        if (g == 2 && loci.size() == 1) out = loci[0].json + "\n"; // a single object instead of an array
        c.put("catalog.json", out);
    }
    void writeGenome() {
        string fa, fai;
        int width = (H(H_HEADER) & 0x10) ? 0 : 60;
        for (const Contig &C : contigs) {
            fa += ">" + C.name + "\n";
            size_t off = fa.size(), lw = width ? width : C.seq.size();
            for (size_t i = 0; i < C.seq.size(); i += lw) fa += C.seq.substr(i, lw) + "\n";
            fai += C.name + "\t" + std::to_string(C.seq.size()) + "\t" + std::to_string(off) + "\t" + std::to_string(std::min(lw, C.seq.size())) + "\t" + std::to_string(std::min(lw, C.seq.size()) + 1) + "\n";
        }
        c.put("ref.fa", fa);
        c.put("ref.fa.fai", fai);
    }
    // ---- the sample
    string qualString(int len, Rng &r, int profile) {
        string q(len, 'I');
        switch (profile & 7) {
        case 1: for (char &ch : q) ch = '#'; break;                               // all Q2
        case 2: for (int i = len * 2 / 3; i < len; i++) q[i] = '#'; break;         // bad tail
        case 3: for (char &ch : q) ch = (char)(33 + r.below(42)); break;          // random
        case 4: for (char &ch : q) ch = '5'; break;                               // Q20, the cutoff
        case 5: for (char &ch : q) ch = '4'; break;                               // Q19
        case 6: for (int i = 0; i < len; i += 3) q[i] = '+'; break;               // every third base low
        case 7: q.clear(); break;                                                 // missing
        }
        return q;
    }
    void mutate(string &s, Rng &r, int errCode) {
        static const int RATE[] = {0, 1, 5, 10, 30, 100, 200, 500}; // substitutions per 1000 bases
        int rate = RATE[errCode & 7];
        if (rate) for (char &ch : s) if (r.below(1000) < (uint32_t)rate) ch = r.base();
        if (errCode & 8 && s.size() > 20) { size_t p = 5 + r.below(s.size() - 10); s.erase(p, 1 + r.below(3)); }   // a deletion
        if (errCode & 16 && s.size() > 20) { size_t p = 5 + r.below(s.size() - 10); s.insert(p, string(1 + r.below(3), r.base())); } // an insertion
        if (errCode & 32 && !s.empty()) s[r.below(s.size())] = 'N';
    }
    void addPair(const string &name, int ref1, int pos1, string s1, int ref2, int pos2, string s2, Rng &r, int flagsExtra1 = 0, int flagsExtra2 = 0) {
        // s1 is the forward read of the fragment, s2 the reverse one (already reverse-complemented)
        int err = H(H_ERRORS), qp = H(H_QUAL);
        mutate(s1, r, err);
        mutate(s2, r, err);
        bool firstIsForward = r.below(2);
        bam::Rec a, b;
        a.name = b.name = name;
        a.flag = 0x1 | 0x2 | 0x20 | (firstIsForward ? 0x40 : 0x80) | flagsExtra1;
        b.flag = 0x1 | 0x2 | 0x10 | (firstIsForward ? 0x80 : 0x40) | flagsExtra2;
        a.ref = ref1; a.pos = pos1; a.mref = ref2; a.mpos = pos2;
        b.ref = ref2; b.pos = pos2; b.mref = ref1; b.mpos = pos1;
        a.mapq = b.mapq = 60;
        a.seq = s1; b.seq = s2;
        a.cigar = std::to_string(s1.size()) + "M"; b.cigar = std::to_string(s2.size()) + "M";
        a.tlen = ref1 == ref2 ? pos2 + (int)s2.size() - pos1 : 0; b.tlen = -a.tlen;
        a.qual = qualString(s1.size(), r, qp); b.qual = qualString(s2.size(), r, qp >> 3);
        if (s1.empty()) a.cigar = "*";
        if (s2.empty()) b.cigar = "*";
        recs.push_back(a);
        recs.push_back(b);
    }
    void simulate() {
        readLen = READLEN[H(H_READLEN) & 15];
        fragLen = FRAGLEN[H(H_FRAG) & 15];
        int cover = COVER[H(H_DEPTH) & 15];
        // the cost of a case: at most 600 read pairs and 120,000 read bases (long reads are quadratic in the aligners)
        size_t budget = std::min<size_t>(600, 120000 / (2 * std::max(1, readLen)) + 1);
        for (size_t k = 0; k < loci.size(); k++) {
            const Locus &L = loci[k];
            const uint8_t *l = d + HDR + k * LREC;
            const string &ref = contigs[L.contigIndex].seq;
            int lcov = l[L_COVER] ? COVER[l[L_COVER] & 15] : cover;
            int nhap = (l[L_COVER] & 0x80) ? 1 : 2;
            for (int h = 0; h < nhap; h++) {
                Rng r(H(H_SEED) * 1000003ULL + k * 101 + h);
                // haplotype = left context + the allele's structure + right context
                int ctx = std::max(readLen, fragLen) + 100;
                int ls = std::max(0, L.start - ctx), re = std::min<int>(ref.size(), L.end + ctx);
                string left = ref.substr(ls, L.start - ls), right = ref.substr(L.end, re - L.end), mid;
                for (auto &ch : left) ch = toupper((unsigned char)ch);
                for (auto &ch : right) ch = toupper((unsigned char)ch);
                for (const Feature &f : L.feats) mid += featureSeq(f, f.kind == 'L' ? 0 : f.hap[h], r, l[L_IMPURE] & 127);
                string hap = left + mid + right;
                for (char &ch : hap) if (!strchr("ACGT", ch)) ch = ch == 'N' ? 'N' : r.base();
                int refMid = L.end - L.start;
                auto refPos = [&](int off) { // haplotype offset -> reference position
                    if (off < (int)left.size()) return ls + off;
                    if (off < (int)(left.size() + mid.size())) return L.start + std::min(off - (int)left.size(), refMid);
                    return L.end + off - (int)(left.size() + mid.size());
                };
                int pairs = (int)((long)lcov * hap.size() / (2 * std::max(1, readLen)));
                // stutter: each fragment draws its own repeat sizes around the haplotype's (many candidate sizes)
                static const int JITTER[] = {0, 2, 10, 30};
                int jit = JITTER[(l[L_COVER] >> 5) & 3];
                string hap0 = hap;
                size_t mid0 = mid.size();
                for (int i = 0; i < pairs && budget; i++, budget--) {
                    if (jit) {
                        mid.clear();
                        for (const Feature &f : L.feats) {
                            int cp = f.kind == 'L' ? 0 : f.hap[h];
                            if (f.kind == 'R' || f.kind == 'P') cp = std::max(0, cp + (int)r.below(2 * jit + 1) - jit);
                            mid += featureSeq(f, cp, r, l[L_IMPURE] & 127);
                        }
                        hap = left + mid + right;
                        for (char &ch : hap) if (!strchr("ACGT", ch)) ch = ch == 'N' ? 'N' : r.base();
                    }
                    int f = std::max(1, fragLen + (int)r.below(fragLen / 4 + 1) - fragLen / 8);
                    if (f > (int)hap.size()) f = hap.size();
                    int s = r.below(hap.size() - f + 1), rl = std::min(readLen, f);
                    string s1 = hap.substr(s, rl), s2 = revcomp(hap.substr(s + f - rl, rl));
                    addPair("p" + std::to_string(k) + "_" + std::to_string(h) + "_" + std::to_string(i), L.contigIndex, refPos(s), s1, L.contigIndex, refPos(s + f - rl), s2, r);
                }
                (void)hap0; (void)mid0;
            }
            // in-repeat read pairs: both mates made of the motif, placed in the off-target regions (or at the locus)
            Rng r(H(H_SEED) * 7919ULL + k);
            const Feature *f = nullptr;
            for (const Feature &g : L.feats) if (g.kind == 'R' || g.kind == 'P') { f = &g; break; }
            static const int IRR[] = {0, 1, 2, 5, 10, 20, 40, 80};
            int nIrr = f ? IRR[l[L_IRR] & 7] : 0;
            for (int i = 0; i < nIrr && budget; i++, budget--) {
                string u = concrete(f->a, r), run = rep(u, readLen / std::max<size_t>(1, u.size()) + 2);
                string s1 = run.substr(r.below(u.size()), readLen), s2 = revcomp(run.substr(r.below(u.size()), readLen));
                if (l[L_IRR] & 0x10) std::swap(s1, s2); // the other strand
                int ref1 = L.contigIndex, pos1 = L.start, ref2 = ref1, pos2 = L.start;
                if (!L.offtargets.empty() && !(l[L_IRR] & 0x20)) {
                    auto &o = L.offtargets[i % L.offtargets.size()];
                    ref1 = ref2 = contigId(o.first);
                    pos1 = o.second.first + r.below(20);
                    pos2 = (l[L_IRR] & 0x40) ? pos1 + 200 : pos1;
                }
                if (l[L_IRR] & 0x80) { // one mate anchored in the flank
                    ref1 = L.contigIndex;
                    pos1 = std::max(0, L.start - readLen - 20 - (int)r.below(100));
                    s1 = ref.substr(pos1, readLen);
                    for (auto &ch : s1) ch = toupper((unsigned char)ch);
                }
                addPair("irr" + std::to_string(k) + "_" + std::to_string(i), ref1, pos1, s1, ref2, pos2, s2, r);
            }
        }
        // noise: pairs far from every locus (on the extra contig)
        Rng r(H(H_SEED) + 77);
        // (with extra loci: among them, so that one pair is near many loci)
        int nc = nExtra ? contigs.size() - 2 : contigs.size() - 1;
        const string &ex = contigs[nc].seq;
        for (int i = 0; i < (H(H_NOISE) & 15); i++) {
            int rl = std::min<int>(readLen, 200), lo = nExtra ? 1300 : 2200;
            int s = lo + r.below(std::max<int>(1, (int)ex.size() - lo - 2 * rl - 10));
            if (s + 2 * rl > (int)ex.size()) continue;
            addPair("noise" + std::to_string(i), nc, s, ex.substr(s, rl), nc, s + rl, revcomp(ex.substr(s + rl, rl)), r);
        }
        extras();
        // order: coordinate sorted (unmapped last) unless shuffled; then the unmapped tail
        int order = (H(H_CONTAINER) >> 4) & 3;
        if (order == 0) std::stable_sort(recs.begin(), recs.end(), [](const bam::Rec &a, const bam::Rec &b) { unsigned ra = a.ref, rb = b.ref; return ra != rb ? ra < rb : a.pos < b.pos; });
        else if (order == 1) { Rng s(H(H_SEED) + 5); for (size_t i = recs.size(); i > 1; i--) std::swap(recs[i - 1], recs[s.below(i)]); }
        else if (order == 2) std::stable_sort(recs.begin(), recs.end(), [](const bam::Rec &a, const bam::Rec &b) { return a.name < b.name; });
        else std::reverse(recs.begin(), recs.end());
        Rng u(H(H_SEED) + 9);
        for (int i = 0; i < (H(H_UNMAPPED) & 15); i++) {
            bam::Rec a;
            a.name = "un" + std::to_string(i / 2);
            a.flag = 0x1 | 0x4 | 0x8 | (i % 2 ? 0x80 : 0x40);
            a.seq = randomSeq(u.next(), std::min(readLen, 100));
            a.qual = string(a.seq.size(), 'I');
            size_t at = (H(H_UNMAPPED) & 0x10) ? u.below(recs.size() + 1) : recs.size(); // inside the file instead of at its end
            recs.insert(recs.begin() + at, a);
        }
    }
    // single unusual records
    void extras() {
        size_t base = HDR + loci.size() * LREC;
        Rng r(H(H_SEED) + 31337);
        for (size_t x = 0; x < MAXEXTRA && base + (x + 1) * XREC <= n; x++) {
            const uint8_t *e = d + base + x * XREC;
            int kind = e[0] % 24;
            if (!kind) continue;
            const Locus &L = loci[e[1] % loci.size()];
            const string &ref = contigs[L.contigIndex].seq;
            int rl = std::min<int>(READLEN[e[3] & 15], ref.size());
            int pos = std::max(0, std::min<int>(ref.size() - rl, L.start - rl + (int)e[2] * 2 - 100 + ((e[4] & 0x80) ? 600 : 0)));
            string s = ref.substr(pos, rl);
            for (auto &ch : s) ch = toupper((unsigned char)ch);
            switch (e[4] & 7) { // content
            case 1: s = randomSeq(r.next(), rl); break;
            case 2: s = string(rl, 'N'); break;
            case 3: { const Feature *f = nullptr; for (const Feature &g : L.feats) if (g.kind == 'R' || g.kind == 'P') f = &g; if (f) { Rng q(1); s = rep(concrete(f->a, q), rl / f->a.size() + 1).substr(0, rl); } break; }
            case 4: s = string(rl, 'A'); break;
            case 5: for (size_t i = 0; i < s.size(); i += 2) s[i] = tolower((unsigned char)s[i]); break;
            case 6: s = revcomp(s); break;
            case 7: if (s.size() > 4) { s[1] = '='; s[2] = 'R'; s[3] = '.'; } break;
            }
            string nm = "x" + std::to_string(x);
            bam::Rec a;
            a.name = nm; a.ref = L.contigIndex; a.pos = pos; a.mref = L.contigIndex; a.mpos = pos + 100; a.mapq = e[5] & 63;
            a.seq = s; a.qual = qualString(s.size(), r, e[5] >> 3); a.cigar = std::to_string(s.size()) + "M";
            a.flag = 0x1 | 0x40 | 0x20;
            bam::Rec b = a;
            b.flag = 0x1 | 0x80 | 0x10; b.pos = a.mpos; b.mpos = a.pos;
            b.seq = revcomp(ref.substr(std::min<int>(ref.size() - rl, pos + 100), rl));
            for (auto &ch : b.seq) ch = toupper((unsigned char)ch);
            b.qual = string(b.seq.size(), 'I'); b.cigar = std::to_string(b.seq.size()) + "M";
            bool pair = true;
            switch (kind) {
            case 1: a.flag = 0; a.mref = -1; a.mpos = -1; pair = false; break;                 // unpaired read
            case 2: a.flag |= 0x100; break;                                                    // secondary first mate (+ primary second)
            case 3: a.flag |= 0x800; break;                                                    // supplementary
            case 4: pair = false; break;                                                       // a lone mate
            case 5: a.ref = -1; a.pos = -1; a.flag |= 0x4; a.cigar = "*"; break;               // unmapped read inside the file
            case 6: b.ref = contigs.size() - 1; b.pos = 100 + e[6] * 10; a.mref = b.ref; a.mpos = b.pos; break; // mate on another contig
            case 7: a.seq.clear(); a.qual.clear(); a.cigar = "*"; break;                       // no sequence
            case 8: a.flag |= 0x400; b.flag |= 0x400; break;                                   // duplicates
            case 9: a.flag |= 0x200; break;                                                    // QC fail
            case 10: a.cigar = "10S" + std::to_string(std::max<int>(1, (int)s.size() - 10)) + "M"; break; // soft clip
            case 11: recs.push_back(a); recs.push_back(b); break;                              // the pair twice (4 records, one name)
            case 12: { bam::Rec t = a; t.flag = 0x1 | 0x40; recs.push_back(t); break; }        // a third record with the name
            case 13: a.flag |= 0x40 | 0x80; break;                                             // both first and second
            case 14: b.flag = (b.flag & ~0x80) | 0x40; break;                                  // two first mates
            case 15: a.mref = -1; a.mpos = -1; a.flag |= 0x8; pair = false; break;             // mate unmapped and absent
            case 16: b.ref = -1; b.pos = -1; b.flag |= 0x4; a.flag |= 0x8; b.cigar = "*"; break; // mate unmapped but present
            case 17: a.pos = 0; b.pos = (int)ref.size() - 1; a.mpos = b.pos; b.mpos = a.pos; break; // contig ends
            case 18: a.name = b.name = string(std::min<int>(1 + e[6], adapterOpt("invalid_reads_files", "0") == "1" ? 256 : 254), 'q'); break; // long names (254 is the format's limit)
            case 19: a.qual = string(a.seq.size(), (char)(33 + (e[6] % 94))); break;           // one quality value
            case 20: a.aux = string("NMC\x01RGZgrp1\0", 13); a.auxText = "NM:i:1\tRG:Z:grp1"; break;
            case 21: a.pos = pos + 1000000; break;                                             // position beyond the contig
            case 22: a.cigar = std::to_string(s.size() / 2) + "M500N" + std::to_string(s.size() - s.size() / 2) + "M"; break;
            case 23: a.seq = s.substr(0, 1); a.qual = a.qual.substr(0, a.qual.empty() ? 0 : 1); a.cigar = "1M"; break; // one base
            }
            switch (e[7] & 7) { // a second property of the same record
            case 1: a.pos = -1; break;                                                          // POS 0 on a named contig
            case 2: for (size_t i = 0; i < a.seq.size(); i += 7) a.seq[i] = "RYKMSWBDHV"[(i / 7) % 10]; break; // IUPAC codes
            case 3: a.name.clear(); if (pair) b.name.clear(); break;                           // no read name
            case 4: if (a.seq.size() > 30) a.seq.erase(a.seq.size() / 2, 2), a.qual = string(a.seq.size(), 'I'), a.cigar = std::to_string(a.seq.size()) + "M"; break; // an indel
            case 5: a.mapq = 0; a.flag &= ~0x2; break;
            case 6: a.tlen = 0x7fffffff; break;
            case 7: a.flag |= 0x1000; break;                                                    // a flag bit outside the specification
            }
            recs.push_back(a);
            if (pair) recs.push_back(b);
        }
    }
    // ---- reads file
    string samText(const string &head) {
        string s = head;
        for (const bam::Rec &r : recs) {
            auto nm = [&](int id) { return id < 0 || id >= (int)contigs.size() ? string("*") : contigs[id].name; };
            s += r.name + "\t" + std::to_string(r.flag) + "\t" + nm(r.ref) + "\t" + std::to_string(r.pos + 1) + "\t" + std::to_string(r.mapq) + "\t" + (r.cigar.empty() ? "*" : r.cigar) + "\t" +
                 (r.mref < 0 ? "*" : r.mref == r.ref ? "=" : nm(r.mref)) + "\t" + std::to_string(r.mpos + 1) + "\t" + std::to_string(r.tlen) + "\t" + (r.seq.empty() ? "*" : r.seq) + "\t" + (r.qual.empty() ? "*" : r.qual) +
                 (r.auxText.empty() ? "" : "\t" + r.auxText) + "\n";
        }
        return s;
    }
    void writeReads() {
        int cont = H(H_CONTAINER) & 3, hv = H(H_HEADER) & 15;
        // input constraint (README.md): the reads file is valid; SAM text without @SQ lines is not
        if (hv == 9 && cont == 2 && adapterOpt("invalid_reads_files", "0") != "1") hv = 0;
        // header variants: contig order, a contig the reference lacks, lengths that disagree, no @HD, an @RG line
        vector<bam::Ref> refs;
        for (const Contig &C : contigs) refs.push_back({C.name, (int)C.seq.size()});
        vector<int> remap(contigs.size());
        for (size_t i = 0; i < remap.size(); i++) remap[i] = i;
        if (hv == 1) { std::reverse(refs.begin(), refs.end()); for (size_t i = 0; i < remap.size(); i++) remap[i] = remap.size() - 1 - i; }
        if (hv == 2) refs.push_back({"chrExtra", 5000});
        if (hv == 3) { refs.insert(refs.begin(), {"chrExtra", 5000}); for (auto &m : remap) m++; }
        if (hv == 4) refs[0].len += 10;
        if (hv == 5 && refs.size() > 1) { refs.pop_back(); }  // the last contig (off-targets, noise) is not in the header
        string text = hv == 6 ? "" : "@HD\tVN:1.6\tSO:coordinate\n";
        if (hv != 9) for (auto &r : refs) text += "@SQ\tSN:" + r.name + "\tLN:" + std::to_string(r.len) + "\n";
        if (hv == 7) text += "@RG\tID:grp1\tSM:sample1\n@PG\tID:sim\tPN:sim\n@CO\tsimulated\n";
        if (hv == 8) text = replaceAll(text, "SO:coordinate", "SO:unsorted");
        if (hv == 10) { // the reads file names contigs in the other convention (chr1 <-> 1)
            for (auto &r : refs) r.name = r.name.compare(0, 3, "chr") == 0 ? r.name.substr(3) : "chr" + r.name;
            text = "@HD\tVN:1.6\tSO:coordinate\n";
            for (auto &r : refs) text += "@SQ\tSN:" + r.name + "\tLN:" + std::to_string(r.len) + "\n";
        }
        if (cont == 2) {
            c.vars["reads_ext"] = ".sam";
            c.put("reads.sam", samText(text));
            c.tags.insert("sam");
            return;
        }
        string data = bam::header(text, refs);
        vector<bam::Span> spans;
        for (bam::Rec r : recs) {
            auto m = [&](int id) { return id < 0 ? -1 : id < (int)remap.size() ? (hv == 5 && remap[id] >= (int)refs.size() ? -1 : remap[id]) : id; };
            r.ref = m(r.ref);
            r.mref = m(r.mref);
            size_t at = data.size();
            data += bam::record(r);
            spans.push_back({r.ref, r.pos, r.pos + std::max<int>(1, r.seq.size()), at, data.size()});
        }
        size_t block = BLOCK[((H(H_CONTAINER) >> 6) & 3) | (cont == 1 ? 4 : 0)];
        bool eof = !(H(H_CONTAINER) & 4);
        int level = cont == 3 ? 0 : (H(H_CONTAINER) & 8) ? 9 : 1;
        c.vars["reads_ext"] = ".bam";
        vector<size_t> starts;
        c.put("reads.bam", bam::bgzf(data, block, level, eof, &starts));
        // an index next to the reads: the rewrite's streaming reader uses one when it is there, the original never does
        int ix = H(H_RES) & 3;
        if (ix == 1) c.put("reads.bam.bai", bam::bai(spans, refs.size(), block, starts));
        if (ix == 2) c.put("reads.bam.bai", bam::bai({}, refs.size(), block, starts));          // valid, but lists nothing
        if (ix == 3) { // stale: the offsets of another record order
            vector<bam::Span> s2 = spans;
            for (size_t i = 0; i + 1 < s2.size(); i += 2) { std::swap(s2[i].ref, s2[i + 1].ref); std::swap(s2[i].beg, s2[i + 1].beg); std::swap(s2[i].end, s2[i + 1].end); }
            c.put("reads.bam.bai", bam::bai(s2, refs.size(), block, starts));
        }
        if (ix) c.tags.insert(ix == 1 ? "index" : ix == 2 ? "index-empty" : "index-stale");
        if (block < 65280) c.tags.insert("block" + std::to_string(block));
        if (!eof) c.tags.insert("noeof");
        if (cont == 3) c.tags.insert("stored");
    }
    // ---- options
    void options() {
        int o = H(H_OPTIONS);
        if (o & 1) { c.args.push_back("--sex"); c.args.push_back("male"); c.tags.insert("male"); }
        if (o & 2) { c.args.push_back("--aligner"); c.args.push_back("path-aligner"); c.tags.insert("path-aligner"); }
        int th = THREADS[(o >> 2) & 3];
        if (th == 8 && (H(H_RES) & 0x40)) th = 16;   // the rewrite's queue budget changes at 16
        if (th != 1) { c.args.push_back("--threads"); c.args.push_back(std::to_string(th)); c.tags.insert("threads" + std::to_string(th)); }
        int ext = EXTLEN[H(H_EXT) & 15];
        // input constraint (README.md): the documented range of the option
        if (ext != -1 && (ext < 500 || ext > 1500) && adapterOpt("extension_range", "documented") == "documented") ext = -1;
        if (ext != -1) { c.args.push_back("--region-extension-length"); c.args.push_back(std::to_string(ext)); c.tags.insert("ext" + std::to_string(ext)); }
        if (o & 0x30) { c.args.push_back("--log-level"); c.args.push_back((H(H_RES) & 0x80) ? "error" : (o & 0x30) == 0x30 ? "trace" : (o & 0x10) ? "warn" : "debug"); }
        if (o & 0x40) { c.args.push_back("--disable-bamlet-output"); c.tags.insert("nobamlet"); }
        if (o & 0x80) { c.args.push_back("--sex"); c.args.push_back("female"); }
        // command-line errors, one per case: both programs must reject them alike
        switch ((H(H_RES) >> 2) & 15) {
        case 1: c.args.insert(c.args.end(), {"--sex", "unknown"}); break;
        case 2: c.args.insert(c.args.end(), {"--aligner", "bogus-aligner"}); break;
        case 3: c.args.insert(c.args.end(), {"--threads", "0"}); break;
        case 4: c.args.insert(c.args.end(), {"--log-level", "loud"}); break;
        case 5: c.vars["reads_ext"] = ".none"; break;                       // no such reads file
        case 6: unlink((c.in + "/ref.fa").c_str()); break;
        case 7: unlink((c.in + "/catalog.json").c_str()); break;
        case 8: c.args.insert(c.args.end(), {"--no-such-option", "1"}); break;
        case 9: c.args.push_back("--help"); break;
        case 10: c.args.push_back("--version"); break;
        case 11: c.args.insert(c.args.end(), {"--threads", "many"}); break;
        case 12: c.args.insert(c.args.end(), {"--region-extension-length", "long"}); break;
        case 13: unlink((c.in + "/ref.fa.fai").c_str()); break;             // the FASTA index is built on the fly
        case 14: c.args.insert(c.args.end(), {"--analysis-mode", "streaming"}); break; // an option given twice
        case 15: c.args.insert(c.args.end(), {"--threads", "-1"}); break;
        }
        if ((H(H_RES) >> 2) & 15) c.tags.insert("cli" + std::to_string((H(H_RES) >> 2) & 15));
        // options that only the rewrite has: it must give the original's output with any of them
        if (adapterOpt("rewrite_options", "1") == "1") {
            int u = H(H_NUOPT), u2 = H(H_NU2);
            static const char *TH[] = {"", "0", "1", "2", "4", "3", "7", "16"};
            if (u & 7) { c.argsB.push_back("--decode-threads"); c.argsB.push_back(TH[u & 7]); c.tags.insert(string("decode") + TH[u & 7]); }
            if ((u >> 3) & 7) { c.argsB.push_back("--encode-threads"); c.argsB.push_back(TH[(u >> 3) & 7]); c.tags.insert(string("encode") + TH[(u >> 3) & 7]); }
            static const char *QB[] = {"", "0", "1", "64"};
            if (u >> 6) { c.argsB.push_back("--bamlet-queue-mb"); c.argsB.push_back(QB[u >> 6]); c.tags.insert(string("queue") + QB[u >> 6]); }
            if (u2 & 1) { // a prepared catalog cache: one extra run of the rewrite writes it, the compared run reads it
                c.preB.push_back({"--prepare-catalog-cache"});
                c.argsB.push_back("--catalog-cache");
                c.argsB.push_back("{out}/catalog.cache");
                c.tags.insert("cache");
            }
            // invalid uses of these options: the original has no counterpart, so the programs differ by design
            // (tag rewrite-option-error); they are here for the rewrite's own error paths
            int bad = (u2 >> 1) & 7;
            if (bad && adapterOpt("rewrite_option_errors", "0") == "1") {
                switch (bad) {
                case 1: c.argsB.insert(c.argsB.end(), {"--decode-threads", "x"}); break;
                case 2: c.argsB.insert(c.argsB.end(), {"--encode-threads", ""}); break;
                case 3: c.argsB.insert(c.argsB.end(), {"--bamlet-queue-mb", "x"}); break;
                case 4: c.argsB.push_back("--prepare-catalog-cache"); break;                    // without --catalog-cache
                case 5: c.argsB.insert(c.argsB.end(), {"--decode-threads", "2147483648"}); break;
                case 6: c.preB.push_back({"--prepare-catalog-cache", "--catalog-cache", "{out}/other.cache", "--sex", "male"});
                        c.argsB.insert(c.argsB.end(), {"--catalog-cache", "{out}/missing.cache"}); break; // no such cache
                case 7: c.preB.push_back({"--prepare-catalog-cache", "--catalog-cache", "{out}/twice.cache"});
                        c.preB.push_back({"--prepare-catalog-cache", "--catalog-cache", "{out}/twice.cache"}); break; // prepared twice
                }
                c.tags.insert("rewrite-option-error");
            }
        }
    }
    bool run() {
        if (n < HDR + LREC) return false;
        buildGenome();
        buildCatalog();
        writeGenome();
        simulate();
        writeReads();
        options();
        string note = std::to_string(loci.size()) + " loci" + (nExtra ? " + " + std::to_string(nExtra) + " without reads" : "") + ":";
        for (const Locus &L : loci) {
            note += " " + L.id + "@" + L.contig + ":" + std::to_string(L.start) + "-" + std::to_string(L.end) + "{";
            for (const Feature &f : L.feats) note += string(1, f.kind) + (f.kind == 'L' ? "" : ":" + f.a + ":ref" + std::to_string(f.refCopies) + ":" + std::to_string(f.hap[0]) + "/" + std::to_string(f.hap[1])) + " ";
            note += "}";
        }
        // input properties on which the original's behaviour is undefined (see README.md, Exemptions)
        // (the original queries its genome mask with the mate's contig before it looks at any flag)
        for (const bam::Rec &r : recs) if (r.mref < 0 && r.ref >= 0) { c.tags.insert("mate-without-contig"); break; }
        c.note = note + "; " + std::to_string(recs.size()) + " records, read length " + std::to_string(readLen) + ", fragment " + std::to_string(fragLen);
        return true;
    }
};

// A base case for the raw surfaces: {data}/base/<n>/ holds ref.fa, ref.fa.fai, catalog.json, reads.bam (written by
// make_data.py from seed inputs of the `locus` surface). The first input byte selects it.
bool linkBase(Case &c, uint8_t sel, const char *skip) {
    int nb = atoi(adapterOpt("base_cases", "1").c_str());
    string base = cfg().data + "/base/" + std::to_string(sel % std::max(1, nb));
    for (const char *f : {"ref.fa", "ref.fa.fai", "catalog.json", "reads.bam"}) {
        if (!strcmp(f, skip)) continue;
        if (symlink((base + "/" + f).c_str(), (c.in + "/" + f).c_str()) != 0) return false;
    }
    c.vars["reads_ext"] = ".bam";
    return true;
}
// A region string "<contig>:<start>-<end>" with start > end anywhere in a catalog text.
bool hasInvertedRegion(const string &s) {
    for (size_t i = 0; i + 3 < s.size(); i++) {
        if (s[i] != ':' || !isdigit((unsigned char)s[i + 1])) continue;
        size_t j = i + 1;
        unsigned long long a = 0, b = 0;
        while (j < s.size() && isdigit((unsigned char)s[j]) && j - i < 20) a = a * 10 + (s[j++] - '0');
        if (j >= s.size() || s[j] != '-') continue;
        size_t k = ++j;
        while (j < s.size() && isdigit((unsigned char)s[j]) && j - k < 20) b = b * 10 + (s[j++] - '0');
        if (j > k && a > b) return true;
    }
    return false;
}
// Raw surfaces: byte 0 selects the base case (and, for `bam`, the BGZF layout), byte 1 the options; the rest is the file.
void rawOptions(Case &c, uint8_t o) {
    int th = THREADS[o & 3];
    if (th != 1) { c.args.push_back("--threads"); c.args.push_back(std::to_string(th)); c.tags.insert("threads" + std::to_string(th)); }
    if (o & 4) { c.args.push_back("--sex"); c.args.push_back("male"); c.tags.insert("male"); }
    if (o & 8) { c.args.push_back("--aligner"); c.args.push_back("path-aligner"); c.tags.insert("path-aligner"); }
}
} // namespace

bool dfz_decode(const uint8_t *data, size_t size, Case &c) {
    if (c.surface == "locus") return Sim(data, size, c).run();
    if (size < 3) return false;
    string body((const char *)data + 2, size - 2);
    rawOptions(c, data[1]);
    if (c.surface == "catalog") {
        if (adapterOpt("inverted_regions", "0") != "1" && hasInvertedRegion(body)) return false;   // input constraint
        if (!linkBase(c, data[0], "catalog.json")) return false;
        c.put("catalog.json", body);
        return true;
    }
    if (c.surface == "sam") {
        if (!linkBase(c, data[0], "reads.bam")) return false;
        c.put("reads.sam", body);
        c.vars["reads_ext"] = ".sam";
        return true;
    }
    if (c.surface == "bam") {
        // byte 0: base case (low 4 bits), block size, missing EOF block; the body is the uncompressed BAM stream
        if (!linkBase(c, data[0] & 15, "reads.bam")) return false;
        c.put("reads.bam", bam::bgzf(body, BLOCK[(data[0] >> 4) & 7], 1, !(data[0] & 0x80)));
        return true;
    }
    return false;
}

// The realigned BAM is compared as header plus the multiset of decoded records (record order and compression are
// documented to differ).
bool dfz_normalize(const Case &, const string &file, int, string &content) {
    if (file.size() < 4 || file.compare(file.size() - 4, 4, ".bam") != 0) return false;
    string text;
    if (!bam::toText(content, text)) return false;
    content = text;
    return true;
}
