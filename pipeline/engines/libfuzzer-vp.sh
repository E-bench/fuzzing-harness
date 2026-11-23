# libFuzzer with its value profile: it keeps the bytes of every comparison the target makes (memcmp/strcmp/switch),
# which matters where the fuzzed input meets a long key or value (catalog JSON names, VCF/JSON field matching).
ENGINE_HARNESS=libfuzzer
engine_available() { command -v clang++ >/dev/null; }
engine_build() { clang++ $1 -DDFZ_ENGINE_LIBFUZZER -fsanitize=fuzzer $2 -o "$3" $4; }
engine_run() {  # <harness> <seeds> <out> <ncpu> <seconds> <max_len> <min_len> <dict>
  local h=$1 seeds=$2 out=$3 n=$4 dur=$5 maxlen=$6 dict=$8 extra=
  mkdir -p "$out/corpus"
  [ -n "$dict" ] && extra="-dict=$dict"
  ( cd "$out" && "$h" "$out/corpus" "$seeds" -fork="$n" -ignore_crashes=1 -ignore_timeouts=1 -ignore_ooms=1 -max_len="$maxlen" -len_control=0 \
      -timeout="${ENGINE_TIMEOUT_S:-300}" -rss_limit_mb=0 -max_total_time="$dur" -print_final_stats=1 -use_value_profile=1 $extra > "$out/log_0.txt" 2>&1; echo "libfuzzer-vp ended rc=$?" ) &
}
engine_stats() {
  local e; e=$(grep -a -o '^#[0-9]*:' "$1/log_0.txt" 2>/dev/null | tail -1 | tr -d '#:')
  printf "execs=%d corpus=%d\n" "${e:-0}" "$(ls "$1/corpus" 2>/dev/null | wc -l)"
}
engine_corpus() { echo "$1/corpus"; }
