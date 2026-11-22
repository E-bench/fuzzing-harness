#!/usr/bin/env bash
# Build steps of one target:  DFZ_TARGET=<target> [STEPS="..."] pipeline/steps/build.sh
#   fetch    both sides at the pinned commits                        -> work/<target>/src/<side>
#   deps     each side's dependencies (target.toml [build] deps)     -> work/<target>/deps/<side>
#   ref      reference builds: the documented compiler, no instrumentation, no patches   -> bin/<side>_ref
#   refp     the same with the side's temporary patches (only for sides that have any)   -> bin/<side>_refp
#   fuzz     edge-coverage builds (AFL++ compiler wrapper), patched                      -> bin/<side>_fuzz
#   cov      source-based coverage builds (clang -fprofile-instr-generate -fcoverage-mapping), patched -> bin/<side>_cov
# Temporary patches ([side.<x>] patches in target.toml) hide known bugs so that fuzzing can go on behind them. Their
# paths reach the target's build command in DFZ_PATCHES. The unpatched reference builds stay: a difference is only
# reported against them.
#   harness  the differential harness, once per engine, plus dfz_tool        -> bin/harness_<engine>, bin/dfz_tool
#   data     the target's fixed data and seed corpora (target.toml [build] data)
# A flavour is nothing but a compiler and flags handed to the target's own build command; the pipeline does not know
# how a target is built. SIDES="a b" restricts the per-side steps.
source "$(dirname "$0")/../env.sh"
STEPS=${STEPS:-fetch deps ref refp fuzz cov harness data}
SIDES=${SIDES:-a b}
side_name() { [ "$1" = a ] && echo "$CFG_A_NAME" || echo "$CFG_B_NAME"; }
side_patches() {  # absolute paths of the side's patches
  local v=CFG_${1^^}_PATCHES p; for p in ${!v}; do printf '%s ' "$TARGET_DIR/$p"; done
}
build_flavour() {  # build_flavour <flavour> [nopatch|onlypatched]: the caller exported DFZ_CC DFZ_CXX DFZ_CFLAGS DFZ_CXXFLAGS DFZ_LDFLAGS
  local fl=$1 mode=${2:-} pids=() s n
  for s in $SIDES; do n=$(side_name $s)
    local patches; patches=$(side_patches $s); [ "$mode" = nopatch ] && patches=
    [ "$mode" = onlypatched ] && [ -z "$patches" ] && { rm -f "$T/bin/${n}_$fl"; continue; }
    ( export DFZ_FLAVOUR=$fl DFZ_PATCHES="$patches" NPROC=$(( NPROC / 2 > 0 ? NPROC / 2 : 1 ))
      eval "$(dfz_expand "$CFG_BUILD_PROGRAM" "$n" "$fl")" > "$T/logs/build_${n}_$fl.log" 2>&1
      echo "$n $fl rc=$? $(ls -la "$T/bin/${n}_$fl" 2>/dev/null | awk '{print $5" bytes"}')" ) &
    pids+=($!)
  done
  [ ${#pids[@]} -gt 0 ] && wait "${pids[@]}"
}
for s in $STEPS; do echo "=== $s"; case $s in
fetch)
  for x in $SIDES; do n=$(side_name $x); X=${x^^}; repo=CFG_${X}_REPO; commit=CFG_${X}_COMMIT
    [ -d "$T/src/$n/.git" ] || git clone -q "${!repo}" "$T/src/$n"
    git -C "$T/src/$n" checkout -q "${!commit}" && echo "$n $(git -C "$T/src/$n" rev-parse HEAD)"
  done ;;
deps)
  [ -n "$CFG_BUILD_DEPS" ] || { echo "(none)"; continue; }
  pids=()
  for x in $SIDES; do n=$(side_name $x)
    ( export NPROC=$(( NPROC / 2 > 0 ? NPROC / 2 : 1 )); eval "$(dfz_expand "$CFG_BUILD_DEPS" "$n")" > "$T/logs/deps_$n.log" 2>&1; echo "deps $n rc=$?" ) & pids+=($!)
  done; wait "${pids[@]}" ;;
ref)
  export DFZ_CC=$CFG_REF_CC DFZ_CXX=$CFG_REF_CXX DFZ_CFLAGS= DFZ_CXXFLAGS= DFZ_LDFLAGS=
  build_flavour ref nopatch ;;
refp)
  export DFZ_CC=$CFG_REF_CC DFZ_CXX=$CFG_REF_CXX DFZ_CFLAGS= DFZ_CXXFLAGS= DFZ_LDFLAGS=
  build_flavour refp onlypatched; [ -z "$(side_patches a)$(side_patches b)" ] && echo "(no patches)" ;;
fuzz)
  export AFL_PATH=$AFL_ROOT AFL_QUIET=1
  export DFZ_CC=$AFL_ROOT/afl-clang-fast DFZ_CXX=$AFL_ROOT/afl-clang-fast++ DFZ_CFLAGS= DFZ_CXXFLAGS= DFZ_LDFLAGS=
  build_flavour fuzz
  for x in $SIDES; do n=$(side_name $x); [ -x "$T/bin/${n}_fuzz" ] && echo "$n edges: $(AFL_DUMP_MAP_SIZE=1 "$T/bin/${n}_fuzz")"; done ;;
cov)
  # covshim.o: writes the profile when the program dies by a signal (harness/covshim.c)
  clang -O1 -c "$DFZ_ROOT/pipeline/harness/covshim.c" -o "$T/build/covshim.o"
  export DFZ_CC=clang DFZ_CXX=clang++ DFZ_CFLAGS="-fprofile-instr-generate -fcoverage-mapping" \
         DFZ_CXXFLAGS="-fprofile-instr-generate -fcoverage-mapping" DFZ_LDFLAGS="-fprofile-instr-generate $T/build/covshim.o"
  build_flavour cov ;;
harness)
  H=$DFZ_ROOT/pipeline/harness; A=$TARGET_DIR/$CFG_ADAPTER
  FLAGS="-std=c++17 -O2 -I$H -I$TARGET_DIR"
  # feedback guards of the harness: 16384 virtual edges + both programs' maps, rounded up to a power of two
  need=16384
  for x in a b; do n=$(side_name $x); [ -x "$T/bin/${n}_fuzz" ] && need=$(( need + ($(AFL_DUMP_MAP_SIZE=1 "$T/bin/${n}_fuzz") + 63) / 64 * 64 )); done
  DFZ_GUARDS=65536; while [ $DFZ_GUARDS -lt $need ]; do DFZ_GUARDS=$(( DFZ_GUARDS * 2 )); done
  export DFZ_GUARDS; echo $DFZ_GUARDS > "$T/bin/guards"; echo "feedback guards: $DFZ_GUARDS (needed $need)"
  g++ $FLAGS "$H/tool.cpp" "$A" -o "$T/bin/dfz_tool" $CFG_HARNESS_LIBS; echo "dfz_tool rc=$?"
  for e in "$DFZ_ROOT"/pipeline/engines/*.sh; do
    ( source "$e"; name=$(basename "$e" .sh)
      if engine_available; then engine_build "$FLAGS" "$H/fuzz_entry.cpp $A" "$T/bin/harness_$name" "$CFG_HARNESS_LIBS" > "$T/logs/harness_$name.log" 2>&1; echo "harness_$name rc=$?"
      else echo "harness_$name skipped: engine not installed"; fi )
  done ;;
data)
  [ -n "$CFG_BUILD_DATA" ] || { echo "(none)"; continue; }
  eval "$(dfz_expand "$CFG_BUILD_DATA")"; echo "data rc=$?" ;;
*) echo "unknown step $s"; exit 2;;
esac; done
ls -la "$T/bin"
