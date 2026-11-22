#!/usr/bin/env bash
# Fetch and build the fuzzers into $DFZ_TOOLS (default work/tools):  [STEPS="afl hfuzz libafl"] pipeline/steps/tools.sh
#   afl     AFL++ 5.04a: afl-fuzz, the compiler wrappers, the runtime (also used to instrument the programs under test)
#   hfuzz   honggfuzz (and a private libunwind with ptrace support if the system has none)
#   libafl  LibAFL's libafl-fuzz (needs a recent stable rustc: `cargo +stable`)
# libFuzzer needs nothing: it ships with clang.
# An existing checkout is reused: point AFL_ROOT / HFUZZ_ROOT at it, or set DFZ_LIBAFL_BIN to a built libafl-fuzz.
source "$(dirname "$0")/../env.sh"
STEPS=${STEPS:-afl hfuzz libafl}
AFL_COMMIT=5f81f2ac530377d605d0cb0793fa43854766517a      # 5.04a
HFUZZ_COMMIT=940b958dfeb6f9131fd846cae31fcc0fe996ae98
LIBAFL_COMMIT=8cff4abf9e17ecde46365043b1c6ff2395771fbf   # 0.16.1
J=$NPROC; L=$DFZ_WORK/logs
fetch() { [ -d "$2/.git" ] || git clone -q "$1" "$2"; git -C "$2" checkout -q "$3"; }
for s in $STEPS; do echo "=== $s"; case $s in
afl)
  fetch https://github.com/AFLplusplus/AFLplusplus "$AFL_ROOT" $AFL_COMMIT
  make -C "$AFL_ROOT" -j"$J" source-only NO_NYX=1 NO_PYTHON=1 > "$L/afl-make.log" 2>&1; echo "afl make rc=$?"
  ls -la "$AFL_ROOT"/afl-fuzz "$AFL_ROOT"/afl-clang-fast++ "$AFL_ROOT"/afl-compiler-rt.o ;;
hfuzz)
  fetch https://github.com/google/honggfuzz "$HFUZZ_ROOT" $HFUZZ_COMMIT
  U=$DFZ_TOOLS/libunwind
  if ! echo '#include <libunwind-ptrace.h>' | gcc -E - >/dev/null 2>&1 && [ ! -f "$U/include/libunwind-ptrace.h" ]; then
    B=$DFZ_TOOLS/unw_src; rm -rf "$B"; mkdir -p "$B" && cd "$B" && curl -sL https://github.com/libunwind/libunwind/releases/download/v1.8.1/libunwind-1.8.1.tar.gz | tar xz && cd libunwind-1.8.1 \
      && CC=gcc CXX=g++ ./configure --prefix="$U" --disable-tests --disable-documentation --disable-minidebuginfo --disable-zlibdebuginfo > "$L/libunwind.log" 2>&1 \
      && make -j"$J" >> "$L/libunwind.log" 2>&1 && make install >> "$L/libunwind.log" 2>&1; echo "libunwind rc=$?"; rm -rf "$B"
  fi
  cd "$HFUZZ_ROOT" && make clean >/dev/null 2>&1
  CC=gcc CFLAGS="-O3 -I$U/include" LDFLAGS="-L$U/lib -Wl,-rpath,$U/lib" make -j"$J" CC=gcc > "$L/hfuzz.log" 2>&1 \
    || CC=gcc CFLAGS="-O3 -I$U/include" LDFLAGS="-L$U/lib -Wl,-rpath,$U/lib" make CC=gcc >> "$L/hfuzz.log" 2>&1   # serial retry: the parallel build can race
  echo "honggfuzz rc=$?"; ls -la honggfuzz hfuzz_cc/hfuzz-clang++ ;;
libafl)
  if [ -n "${DFZ_LIBAFL_BIN:-}" ] && [ -x "$DFZ_LIBAFL_BIN" ]; then cp -f "$DFZ_LIBAFL_BIN" "$DFZ_TOOLS/bin/libafl-fuzz"; echo "libafl-fuzz copied from $DFZ_LIBAFL_BIN"; continue; fi
  fetch https://github.com/AFLplusplus/LibAFL "$DFZ_TOOLS/LibAFL" $LIBAFL_COMMIT
  ( cd "$DFZ_TOOLS/LibAFL/fuzzers/forkserver/libafl-fuzz" && cargo +stable build --release > "$L/libafl.log" 2>&1 ); echo "libafl-fuzz rc=$?"
  f=$(ls "$DFZ_TOOLS"/LibAFL/fuzzers/forkserver/libafl-fuzz/target/release/libafl-fuzz "$DFZ_TOOLS"/LibAFL/target/release/libafl-fuzz 2>/dev/null | head -1)
  [ -n "$f" ] && cp -f "$f" "$DFZ_TOOLS/bin/libafl-fuzz"; ls -la "$DFZ_TOOLS/bin/libafl-fuzz" ;;
*) echo "unknown step $s"; exit 2;;
esac; done
