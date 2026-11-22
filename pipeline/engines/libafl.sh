# LibAFL's libafl-fuzz (an afl-fuzz replacement on the LibAFL library); drives the AFL++ harness build.
ENGINE_HARNESS=afl
engine_available() { [ -x "$DFZ_TOOLS/bin/libafl-fuzz" ] && [ -x "$AFL_ROOT/afl-clang-fast++" ]; }
engine_build() { return 0; }          # uses harness_afl
engine_run() {  # <harness> <seeds> <out> <ncpu> <seconds> <max_len> <min_len> <dict>
  local h=$1 seeds=$2 out=$3 n=$4 dur=$5 maxlen=$6 minlen=$7 dict=$8 extra=
  export AFL_MAP_SIZE=$(( 65536 + ${DFZ_GUARDS:-524288} ))
  export AFL_SKIP_CPUFREQ=1 AFL_NO_AFFINITY=1 AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 AFL_NO_UI=1 AFL_SKIP_CRASHES=1 AFL_FORKSRV_INIT_TMOUT=60000
  # libafl-fuzz starts its own clients, one per core of AFL_CORES (no -M/-S)
  export AFL_CORES=$(taskset -cp $$ | sed 's/.*: //') AFL_BROKER_PORT=$((20000 + $$ % 20000))
  # it takes its execution timeout from AFL_HANG_TMOUT (default 100 ms), not from -t
  export AFL_HANG_TMOUT=$(( ${ENGINE_TIMEOUT_S:-300} * 1000 )) AFL_IGNORE_SEED_ISSUES=1
  [ -n "$dict" ] && extra="$extra -x $dict"
  [ "$minlen" -gt 0 ] && extra="$extra -g $minlen"
  # Its brokers listen on fixed TCP ports (AFL_BROKER_PORT moves only one of them), so two campaigns on one machine
  # collide. A private network namespace gives each its own loopback.
  local ns=()
  if unshare -rn true 2>/dev/null; then ns=(unshare -rn sh -c 'ip link set lo up; exec "$@"' sh); else echo "libafl: no network namespace; one campaign per machine only"; fi
  ( "${ns[@]}" "$DFZ_TOOLS/bin/libafl-fuzz" -i "$seeds" -o "$out/libafl" -t $(( ${ENGINE_TIMEOUT_S:-300} * 1000 )) -G "$maxlen" -V "$dur" $extra -- "$h" > "$out/log_0.txt" 2>&1; echo "libafl-fuzz ended rc=$?" ) &
}
engine_stats() {
  cat "$1"/libafl/*/fuzzer_stats 2>/dev/null | awk '$1=="execs_done"{e+=$3} $1=="corpus_count"{c+=$3} END{printf "execs=%d corpus=%d\n", e, c}'
}
engine_corpus() { ls -d "$1"/libafl/*/queue 2>/dev/null; }
