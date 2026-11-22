// Minimal BAM/BGZF writer and reader for the eh-nueh adapter (zlib only; no htslib).
//   writer: alignment records -> BAM bytes -> BGZF blocks of a chosen size (so records can span block boundaries)
//   reader: BGZF -> BAM -> canonical text (header, then one line per record, records sorted), for comparing the
//           realigned BAMs of the two programs as multisets of decoded records
#pragma once
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include <zlib.h>

namespace bam {
using std::string;
using std::vector;

struct Rec {
    string name;
    int flag = 0;
    int ref = -1, pos = -1; // 0-based position, -1 = unmapped
    int mapq = 0;
    string cigar;           // SAM text ("" or "*" = none)
    int mref = -1, mpos = -1;
    int tlen = 0;
    string seq;             // "" = no sequence
    string qual;            // Phred+33 text; "" = missing (0xff in BAM, '*' in SAM)
    string aux;             // raw BAM aux bytes (and, for SAM, see auxText)
    string auxText;         // SAM form of the same tags (TAB separated, no leading TAB)
};
struct Ref { string name; int len; };

inline void put32(string &s, uint32_t v) { for (int i = 0; i < 4; i++) s.push_back((char)(v >> (8 * i))); }
inline void put16(string &s, uint16_t v) { s.push_back((char)v); s.push_back((char)(v >> 8)); }
inline int reg2bin(int beg, int end) {
    --end;
    if (beg >> 14 == end >> 14) return ((1 << 15) - 1) / 7 + (beg >> 14);
    if (beg >> 17 == end >> 17) return ((1 << 12) - 1) / 7 + (beg >> 17);
    if (beg >> 20 == end >> 20) return ((1 << 9) - 1) / 7 + (beg >> 20);
    if (beg >> 23 == end >> 23) return ((1 << 6) - 1) / 7 + (beg >> 23);
    if (beg >> 26 == end >> 26) return ((1 << 3) - 1) / 7 + (beg >> 26);
    return 0;
}
inline bool parseCigar(const string &c, vector<uint32_t> &ops, int &refLen) {
    ops.clear();
    refLen = 0;
    if (c.empty() || c == "*") return true;
    uint32_t n = 0;
    bool have = false;
    for (char ch : c) {
        if (ch >= '0' && ch <= '9') { n = n * 10 + (ch - '0'); have = true; continue; }
        const char *t = "MIDNSHP=X", *p = strchr(t, ch);
        if (!p || !have) return false;
        ops.push_back(n << 4 | (uint32_t)(p - t));
        if (strchr("MDN=X", ch)) refLen += n;
        n = 0;
        have = false;
    }
    return !have;
}
inline string header(const string &text, const vector<Ref> &refs) {
    string s = "BAM\1";
    put32(s, text.size());
    s += text;
    put32(s, refs.size());
    for (auto &r : refs) { put32(s, r.name.size() + 1); s += r.name; s.push_back(0); put32(s, r.len); }
    return s;
}
inline string record(const Rec &r) {
    vector<uint32_t> ops;
    int refLen = 0;
    parseCigar(r.cigar, ops, refLen);
    string b;
    put32(b, r.ref);
    put32(b, r.pos);
    b.push_back((char)(r.name.size() + 1));
    b.push_back((char)r.mapq);
    put16(b, reg2bin(r.pos < 0 ? 0 : r.pos, (r.pos < 0 ? 0 : r.pos) + (refLen ? refLen : 1)));
    put16(b, ops.size());
    put16(b, r.flag);
    put32(b, r.seq.size());
    put32(b, r.mref);
    put32(b, r.mpos);
    put32(b, r.tlen);
    b += r.name;
    b.push_back(0);
    for (uint32_t o : ops) put32(b, o);
    static const char *codes = "=ACMGRSVTWYHKDBN";
    for (size_t i = 0; i < r.seq.size(); i += 2) {
        auto code = [&](char ch) { const char *p = strchr(codes, toupper((unsigned char)ch)); return p && *p ? (int)(p - codes) : 15; };
        int hi = code(r.seq[i]), lo = i + 1 < r.seq.size() ? code(r.seq[i + 1]) : 0;
        b.push_back((char)(hi << 4 | lo));
    }
    for (size_t i = 0; i < r.seq.size(); i++) b.push_back(r.qual.size() == r.seq.size() ? (char)(r.qual[i] - 33) : (char)0xff);
    b += r.aux;
    string out;
    put32(out, b.size());
    return out + b;
}
// One BGZF block from up to 65280 bytes of data. level 0 = stored.
// os: the header's operating-system byte (255 in every BGZF writer; readers must not depend on it).
inline string bgzfBlock(const char *data, size_t n, int level, int os = 255) {
    vector<unsigned char> comp(n + n / 100 + 64);
    z_stream zs;
    memset(&zs, 0, sizeof zs);
    deflateInit2(&zs, level, Z_DEFLATED, -15, 8, Z_DEFAULT_STRATEGY);
    zs.next_in = (Bytef *)data;
    zs.avail_in = n;
    zs.next_out = comp.data();
    zs.avail_out = comp.size();
    deflate(&zs, Z_FINISH);
    size_t clen = zs.total_out;
    deflateEnd(&zs);
    string s("\x1f\x8b\x08\x04\0\0\0\0\0\xff\x06\0BC\x02\0", 16);
    s[9] = (char)os;
    put16(s, clen + 25);
    s.append((char *)comp.data(), clen);
    put32(s, crc32(crc32(0, nullptr, 0), (const Bytef *)data, n));
    put32(s, n);
    return s;
}
// blockSize: uncompressed bytes per block (1..65280). eof: append the empty end-of-file block.
// starts (optional): the file offset of every data block.
inline string bgzf(const string &data, size_t blockSize, int level, bool eof, vector<size_t> *starts = nullptr) {
    string out;
    blockSize = std::max<size_t>(1, std::min<size_t>(blockSize, 65280));
    for (size_t i = 0; i < data.size(); i += blockSize) {
        if (starts) starts->push_back(out.size());
        out += bgzfBlock(data.data() + i, std::min(blockSize, data.size() - i), level);
    }
    if (starts) starts->push_back(out.size());
    if (eof) out += bgzfBlock("", 0, 6);
    return out;
}
// The same with blocks that start at chosen offsets of the data (`cuts`, ascending), as htslib's own writer starts
// blocks at record boundaries; a piece longer than 65280 bytes is split. ustarts: the data offset of every block.
inline string bgzfAt(const string &data, const vector<size_t> &cuts, int level, bool eof, int os, vector<size_t> &starts, vector<size_t> &ustarts) {
    string out;
    vector<size_t> at{0};
    for (size_t c : cuts) if (c > at.back() && c < data.size()) at.push_back(c);
    at.push_back(data.size());
    for (size_t k = 0; k + 1 < at.size(); k++)
        for (size_t i = at[k]; i < at[k + 1]; i += 65280) {
            starts.push_back(out.size());
            ustarts.push_back(i);
            out += bgzfBlock(data.data() + i, std::min<size_t>(65280, at[k + 1] - i), level, os);
        }
    starts.push_back(out.size());
    ustarts.push_back(data.size());
    if (eof) out += bgzfBlock("", 0, 6);
    return out;
}
// ---- BAI index of a BAM written by bgzf() with a uniform block size, or by bgzfAt() (ustarts given)
struct Span { int ref, beg, end; size_t from, to; }; // reference interval and byte range in the uncompressed stream
inline void put64(string &s, uint64_t v) { for (int i = 0; i < 8; i++) s.push_back((char)(v >> (8 * i))); }
inline string bai(const vector<Span> &spans, size_t nref, size_t blockSize, const vector<size_t> &starts, const vector<size_t> *ustarts = nullptr) {
    blockSize = std::max<size_t>(1, std::min<size_t>(blockSize, 65280));
    auto voff = [&](size_t u) {
        if (ustarts && !ustarts->empty()) {
            size_t b = std::upper_bound(ustarts->begin(), ustarts->end(), u) - ustarts->begin();
            b = b ? b - 1 : 0;
            return (uint64_t)starts[b] << 16 | (u - (*ustarts)[b]);
        }
        size_t b = u / blockSize;
        if (b >= starts.size()) b = starts.empty() ? 0 : starts.size() - 1;
        return (uint64_t)(starts.empty() ? 0 : starts[b]) << 16 | (u % blockSize);
    };
    string s = "BAI\1";
    put32(s, nref);
    uint64_t nocoor = 0;
    for (size_t r = 0; r < nref; r++) {
        std::map<int, std::pair<uint64_t, uint64_t>> bins; // one chunk per bin
        vector<uint64_t> lin;
        for (const Span &x : spans) {
            if (x.ref != (int)r || x.beg < 0) continue;
            uint64_t a = voff(x.from), b = voff(x.to);
            int bin = reg2bin(x.beg, x.end);
            auto it = bins.find(bin);
            if (it == bins.end()) bins[bin] = {a, b};
            else { it->second.first = std::min(it->second.first, a); it->second.second = std::max(it->second.second, b); }
            for (int w = x.beg >> 14; w <= (x.end - 1) >> 14; w++) {
                if ((int)lin.size() <= w) lin.resize(w + 1, 0);
                if (!lin[w] || a < lin[w]) lin[w] = a;
            }
        }
        put32(s, bins.size());
        for (auto &b : bins) { put32(s, b.first); put32(s, 1); put64(s, b.second.first); put64(s, b.second.second); }
        put32(s, lin.size());
        for (uint64_t v : lin) put64(s, v);
    }
    for (const Span &x : spans) nocoor += x.ref < 0;
    put64(s, nocoor);
    return s;
}

// ---- reader
inline bool inflateAll(const string &in, string &out) {
    out.clear();
    size_t i = 0;
    while (i + 18 <= in.size()) {
        const unsigned char *p = (const unsigned char *)in.data() + i;
        if (p[0] != 0x1f || p[1] != 0x8b) return false;
        size_t xlen = p[10] | p[11] << 8, bsize = 0;
        for (size_t x = 12; x + 4 <= 12 + xlen && i + x + 4 <= in.size();) {
            size_t slen = p[x + 2] | p[x + 3] << 8;
            if (p[x] == 'B' && p[x + 1] == 'C' && slen == 2) bsize = (p[x + 4] | p[x + 5] << 8) + 1;
            x += 4 + slen;
        }
        if (!bsize || i + bsize > in.size() || bsize < 12 + xlen + 8) return false;
        size_t isize = p[bsize - 4] | p[bsize - 3] << 8 | p[bsize - 2] << 16 | (size_t)p[bsize - 1] << 24;
        if (isize > 0x10000) return false;
        string buf(isize, 0);
        z_stream zs;
        memset(&zs, 0, sizeof zs);
        inflateInit2(&zs, -15);
        zs.next_in = (Bytef *)p + 12 + xlen;
        zs.avail_in = bsize - 12 - xlen - 8;
        zs.next_out = (Bytef *)&buf[0];
        zs.avail_out = isize;
        int rc = inflate(&zs, Z_FINISH);
        inflateEnd(&zs);
        if (rc != Z_STREAM_END && isize) return false;
        out += buf;
        i += bsize;
    }
    return i == in.size();
}
inline uint32_t get32(const string &s, size_t i) { return (unsigned char)s[i] | (unsigned char)s[i + 1] << 8 | (unsigned char)s[i + 2] << 16 | (uint32_t)(unsigned char)s[i + 3] << 24; }
// BAM file bytes -> text: "@header text", "@ref name len" lines, then the records as sorted lines. false: not a BAM.
inline bool toText(const string &file, string &text) {
    string d;
    if (!inflateAll(file, d) || d.size() < 12 || d.compare(0, 4, "BAM\1") != 0) return false;
    size_t i = 4;
    uint32_t lt = get32(d, i);
    i += 4;
    if (i + lt + 4 > d.size()) return false;
    string head = d.substr(i, lt);
    while (!head.empty() && head.back() == 0) head.pop_back();
    i += lt;
    uint32_t nref = get32(d, i);
    i += 4;
    vector<string> refNames;
    string refs;
    for (uint32_t r = 0; r < nref; r++) {
        if (i + 4 > d.size()) return false;
        uint32_t ln = get32(d, i);
        i += 4;
        if (i + ln + 4 > d.size() || ln == 0) return false;
        string nm = d.substr(i, ln - 1);
        i += ln;
        refs += "@ref\t" + nm + "\t" + std::to_string(get32(d, i)) + "\n";
        refNames.push_back(nm);
        i += 4;
    }
    vector<string> recs;
    auto refName = [&](int32_t id) { return id < 0 ? string("*") : (size_t)id < refNames.size() ? refNames[id] : "?" + std::to_string(id); };
    while (i + 4 <= d.size()) {
        uint32_t bs = get32(d, i);
        i += 4;
        if (bs < 32 || i + bs > d.size()) return false;
        const string b = d.substr(i, bs);
        i += bs;
        int32_t ref = get32(b, 0), pos = get32(b, 4);
        unsigned lname = (unsigned char)b[8], mapq = (unsigned char)b[9];
        unsigned ncig = (unsigned char)b[12] | (unsigned char)b[13] << 8, flag = (unsigned char)b[14] | (unsigned char)b[15] << 8;
        uint32_t lseq = get32(b, 16);
        int32_t mref = get32(b, 20), mpos = get32(b, 24), tlen = get32(b, 28);
        size_t o = 32;
        if (o + lname + 4ull * ncig + (lseq + 1) / 2 + lseq > b.size()) return false;
        string line = b.substr(o, lname ? lname - 1 : 0);
        o += lname;
        line += "\t" + std::to_string(flag) + "\t" + refName(ref) + "\t" + std::to_string(pos + 1) + "\t" + std::to_string(mapq) + "\t";
        if (!ncig) line += "*";
        for (unsigned c = 0; c < ncig; c++, o += 4) { uint32_t v = get32(b, o); line += std::to_string(v >> 4) + "MIDNSHP=X???????"[v & 15]; }
        line += "\t" + refName(mref) + "\t" + std::to_string(mpos + 1) + "\t" + std::to_string(tlen) + "\t";
        for (uint32_t k = 0; k < lseq; k++) line += "=ACMGRSVTWYHKDBN"[((unsigned char)b[o + k / 2] >> (k % 2 ? 0 : 4)) & 15];
        if (!lseq) line += "*";
        o += (lseq + 1) / 2;
        line += "\t";
        for (uint32_t k = 0; k < lseq; k++) { unsigned char q = b[o + k]; line += q == 0xff ? '*' : (char)(q < 94 ? q + 33 : '~'); }
        if (!lseq) line += "*";
        o += lseq;
        // aux fields: kept as hex of the raw bytes after printable tag:type (order and encoding are part of the record)
        line += "\t";
        static const char *hx = "0123456789abcdef";
        for (; o < b.size(); o++) { unsigned char ch = b[o]; if (ch >= 33 && ch < 127 && ch != '\\') line += (char)ch; else { line += '\\'; line += hx[ch >> 4]; line += hx[ch & 15]; } }
        recs.push_back(line);
    }
    std::sort(recs.begin(), recs.end());
    text = "@header\n" + head + (head.empty() || head.back() == '\n' ? "" : "\n") + refs;
    for (auto &r : recs) text += r + "\n";
    return true;
}
} // namespace bam
