#!/usr/bin/env bash
# Build recipe of ExpansionHunter v5.0.0 (side eh) and NuExpansionHunter (side nueh). Both use the same layout: a CMake
# superbuild that downloads and builds the dependencies, and the ehunter/ sub-project that builds the program.
#
#   build.sh deps    <side> <checkout> <deps dir>                     dependencies, once per side, with the system GCC
#   build.sh program <side> <checkout> <deps dir> <build dir> <out>   the program in one flavour -> <out>
#
# `program` reads the compiler and flags of the flavour from the environment (set by the pipeline):
#   DFZ_CC DFZ_CXX DFZ_CFLAGS DFZ_CXXFLAGS DFZ_LDFLAGS, and DFZ_PATCHES (patch files to apply first, may be empty)
# Build fixes (no product code is changed):
#   eh    Boost 1.73.0 URL (boostorg.jfrog.io no longer serves it) -> archives.boost.io;
#         graph-tools GraphCoordinates.cpp: #include <limits> (newer libstdc++);
#         googletest 1.10 (only needed by find_package(GTest)): -Wno-error=maybe-uninitialized under GCC 11.
#   both  -static-libgcc -static-libstdc++ dropped (this system has no static libstdc++);
#         with a compiler other than GCC: -Werror dropped (clang 20 warns in sparsepp and nlohmann/json) and -no-pie
#         added (htslib's libhts.a is built without -fPIC).
#   nueh  the optional libraries (libdeflate, ISA-L, jemalloc) are switched off; its documentation says they do not
#         change the output.
set -euo pipefail
CMD=$1; SIDE=$2; SRC=$(cd "$3" && pwd); DEPS=$4
J=${NPROC:-8}
mkdir -p "$DEPS"; DEPS=$(cd "$DEPS" && pwd)
case $CMD in
deps)
  [ -d "$DEPS/src" ] || { cp -r "$SRC" "$DEPS/src"; rm -rf "$DEPS/src/.git"; }
  cd "$DEPS/src"
  if [ "$SIDE" = eh ]; then
    sed -i 's#https://boostorg.jfrog.io/artifactory/main/release/1.73.0/source/boost_1_73_0.tar.bz2#https://archives.boost.io/release/1.73.0/source/boost_1_73_0.tar.bz2#' CMakeLists.txt
    GC=ehunter/thirdparty/graph-tools-master-f421f4c/src/graphcore/GraphCoordinates.cpp
    grep -q '#include <limits>' "$GC" || sed -i '0,/#include/s//#include <limits>\n#include/' "$GC"
  fi
  sed -i 's/set(STATIC_FLAGS -static-libgcc -static-libstdc++)/set(STATIC_FLAGS)/' ehunter/CMakeLists.txt
  mkdir -p "$DEPS/build"; cd "$DEPS/build"
  EXTRA=(); [ "$SIDE" = nueh ] && EXTRA=(-DEH_WITH_LIBDEFLATE=OFF -DEH_WITH_ISAL=OFF -DEH_WITH_JEMALLOC=OFF)
  # the sub-configures of the dependencies read CXXFLAGS from the environment (GCC: a specific -Wno-error=<w> wins
  # over a later -Werror)
  export CXXFLAGS="-Wno-error=maybe-uninitialized"
  cmake -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 "${EXTRA[@]}" "$DEPS/src"
  cmake --build . --target Boost spdlog htslib googletest abseil -- -j"$J"
  ls "$DEPS/build/install/lib" | head -20
  ;;
program)
  B=$5; OUT=$6
  rm -rf "$B"; mkdir -p "$B"
  cp -r "$DEPS/src/ehunter" "$B/ehunter"; [ -d "$DEPS/src/cmake" ] && cp -r "$DEPS/src/cmake" "$B/cmake"
  # temporary patches of this side (paths relative to the repository root), if the pipeline passes any
  for p in ${DFZ_PATCHES:-}; do echo "applying $p"; patch -d "$B" -p1 < "$p"; done
  CC=${DFZ_CC:-gcc}; CXX=${DFZ_CXX:-g++}; LD=${DFZ_LDFLAGS:-}
  if ! "$CXX" --version | grep -q 'Free Software Foundation'; then
    sed -i 's/add_compile_options(-Wall -Werror -Wextra)/add_compile_options(-Wall -Wextra)/' "$B/ehunter/CMakeLists.txt"
    sed -i 's/ -Wall -Werror -pedantic/ -Wall -pedantic/' "$B/ehunter/thirdparty/graph-tools-master-f421f4c/CMakeLists.txt"
    LD="-no-pie $LD"
  fi
  EXTRA=(); [ "$SIDE" = nueh ] && EXTRA=(-DEH_WITH_ISAL=OFF -DEH_WITH_JEMALLOC=OFF -DEH_HTS_CODEC_LIBRARY=)
  cmake -S "$B/ehunter" -B "$B/b" -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
        -DCMAKE_PREFIX_PATH="$DEPS/build/install" -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        "-DCMAKE_C_FLAGS=${DFZ_CFLAGS:-}" "-DCMAKE_CXX_FLAGS=${DFZ_CXXFLAGS:-}" "-DCMAKE_EXE_LINKER_FLAGS=$LD" "${EXTRA[@]}"
  make -C "$B/b" -j"$J" ExpansionHunter
  cp -f "$B/b/ExpansionHunter" "$OUT"
  ;;
*) echo "unknown command $CMD" >&2; exit 2;;
esac
