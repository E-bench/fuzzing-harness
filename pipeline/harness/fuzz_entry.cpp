// Fuzz entry point shared by every engine: LLVMFuzzerTestOneInput.
//
// The two programs run as child processes, so the engine cannot see their coverage by itself. Each child is an
// AFL++-instrumented binary that writes its edge counters into a private shared-memory map; after the run the maps
// are copied into the engine's own feedback:
//   -DDFZ_ENGINE_AFL        AFL++'s map, through extra guards in this module's sancov guard section
//   -DDFZ_ENGINE_LIBFUZZER  libFuzzer's extra counters section (also read by other libFuzzer-compatible engines)
//   -DDFZ_ENGINE_PCGUARD    any engine with trace-pc-guard callbacks (honggfuzz)
//   none of these           no feedback
// "Virtual edges" derived from the difference signature are added as well, so an input that shows a new kind of
// difference is kept by the engine even when it reaches no new code. DFZ_NOFEEDBACK=1 switches all feedback off
// (black-box baseline: same mutations, no guidance).
#define DFZ_MAIN
#include "dfz.h"

static const size_t VIRT = 16384; // virtual edges for difference signatures

#if defined(DFZ_ENGINE_AFL) || defined(DFZ_ENGINE_PCGUARD)
// Engines built on SanitizerCoverage trace-pc-guard: a block of extra guards placed in the guard section of this
// module. The engine numbers them at start-up together with the harness's own edges, so its map is sized for them
// and no fixed offsets are needed. Guard i stands for byte i of the feedback layout below.
#ifndef DFZ_GUARDS
#define DFZ_GUARDS (1 << 19)
#endif
static const size_t NG = DFZ_GUARDS;
#ifdef __APPLE__
static uint32_t g_guards[NG];
#else
__attribute__((section("__sancov_guards"), used)) static uint32_t g_guards[NG];
#endif
static size_t fbSize() { return NG; }
#if defined(DFZ_ENGINE_AFL)
extern "C" unsigned char *__afl_area_ptr;
static inline void hit(size_t i, unsigned char v) { if (g_guards[i]) __afl_area_ptr[g_guards[i]] = v; }
#else
extern "C" void __sanitizer_cov_trace_pc_guard(uint32_t *guard);
static inline void hit(size_t i, unsigned char) { __sanitizer_cov_trace_pc_guard(&g_guards[i]); }
#endif
#elif defined(DFZ_ENGINE_LIBFUZZER)
// `used`: the array is only written here and only read by the engine; without it the optimiser drops the stores
__attribute__((section("__libfuzzer_extra_counters"), used)) static unsigned char g_extra[1 << 21];
static size_t fbSize() { return sizeof g_extra; }
static inline void hit(size_t i, unsigned char v) { g_extra[i] = v; }
#else
static size_t fbSize() { return 0; }
static inline void hit(size_t, unsigned char) {}
#endif

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    using namespace dfz;
    Case c;
    Outcome o;
#if defined(DFZ_ENGINE_PCGUARD)
    if (!runInput(data, size, c, o)) return 0; // honggfuzz aborts on any other return value
#else
    if (!runInput(data, size, c, o)) return -1; // "do not add to the corpus" for libFuzzer-compatible engines
#endif
    static const bool noFeedback = getenv("DFZ_NOFEEDBACK") != nullptr;
    size_t n = noFeedback ? 0 : fbSize();
    static bool once = false;
    if (!once && cfg().verbose) fprintf(stderr, "dfz: feedback slots %zu, child maps %zu + %zu\n", n, o.a.cov.size(), o.b.cov.size());
    once = true;
    if (n) {
        size_t off = VIRT;
        for (const auto *cov : {&o.a.cov, &o.b.cov}) {
            size_t k = std::min(cov->size(), n > off ? n - off : 0);
            for (size_t i = 0; i < k; i++)
                if ((*cov)[i]) hit(off + i, (*cov)[i]);
            off += cov->size();
        }
        if (n >= VIRT)
            for (const Finding &f : o.findings) {
                // one virtual edge per kind of difference, and one per (kind, tag)
                string kind = c.surface + " " + f.atoms;
                hit(fnv(kind) % VIRT, 1);
                for (auto &t : c.tags) hit(fnv(kind + "|" + t) % VIRT, 1);
            }
    }
    store(c, o, data, size);
    return 0;
}
