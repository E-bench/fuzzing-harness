# honggfuzz, persistent mode through its libFuzzer-compatible entry point. On Linux it traces the harness's children too,
# so a crash of a program under test also lands in its own crash directory (hfw/); the harness records it either way.
ENGINE_HARNESS=hfuzz
engine_available() { [ -x "$HFUZZ_ROOT/honggfuzz" ] && [ -x "$HFUZZ_ROOT/hfuzz_cc/hfuzz-clang++" ]; }
engine_build() { "$HFUZZ_ROOT/hfuzz_cc/hfuzz-clang++" $1 -DDFZ_ENGINE_PCGUARD -DDFZ_GUARDS=${DFZ_GUARDS:-524288} $2 -o "$3" $4; }
engine_run() {  # <harness> <seeds> <out> <ncpu> <seconds> <max_len> <min_len> <dict>
  local h=$1 seeds=$2 out=$3 n=$4 dur=$5 maxlen=$6 dict=$8 extra=
  mkdir -p "$out/corpus" "$out/hfw"
  [ -n "$dict" ] && extra="-w $dict"
  ( "$HFUZZ_ROOT/honggfuzz" -i "$seeds" -o "$out/corpus" -W "$out/hfw" -n "$n" -t 300 -F "$maxlen" --run_time "$dur" -q --statsfile "$out/hfuzz_stats.csv" $extra -- "$h" > "$out/log_0.txt" 2>&1; echo "honggfuzz ended rc=$?" ) &
}
engine_stats() {  # the stats file: unix_time, last_cov_update, total_exec, exec_per_sec, ...
  local e; e=$(grep -v '^#' "$1/hfuzz_stats.csv" 2>/dev/null | tail -1 | cut -d, -f3 | tr -d ' ')
  printf "execs=%d corpus=%d\n" "${e:-0}" "$(ls "$1/corpus" 2>/dev/null | wc -l)"
}
engine_corpus() { echo "$1/corpus"; }
