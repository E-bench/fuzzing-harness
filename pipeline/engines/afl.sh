# AFL++ (afl-fuzz): one main and N-1 secondary instances with different power schedules; MOpt on a quarter of them.
ENGINE_HARNESS=afl
engine_available() { [ -x "$AFL_ROOT/afl-fuzz" ] && [ -x "$AFL_ROOT/afl-clang-fast++" ]; }
engine_build() {  # <flags> <sources> <out> <libs>
  AFL_PATH=$AFL_ROOT AFL_QUIET=1 "$AFL_ROOT/afl-clang-fast++" $1 -DDFZ_ENGINE_AFL -DDFZ_GUARDS=${DFZ_GUARDS:-524288} -fsanitize=fuzzer $2 -o "$3" $4
}
afl_env() {
  export AFL_MAP_SIZE=$(( 65536 + ${DFZ_GUARDS:-524288} ))   # the harness registers DFZ_GUARDS feedback guards besides its own edges
  export AFL_SKIP_CPUFREQ=1 AFL_NO_AFFINITY=1 AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 AFL_NO_UI=1 AFL_FAST_CAL=1
  export AFL_IGNORE_SEED_PROBLEMS=1 AFL_SKIP_CRASHES=1 AFL_FORKSRV_INIT_TMOUT=60000 AFL_NO_STARTUP_CALIBRATION=1
}
engine_run() {  # <harness> <seeds> <out> <ncpu> <seconds> <max_len> <min_len> <dict>
  local h=$1 seeds=$2 out=$3 n=$4 dur=$5 maxlen=$6 minlen=$7 dict=$8 i role extra
  local sched=(explore fast coe lin quad exploit rare explore fast coe lin quad exploit rare explore fast)
  afl_env
  for i in $(seq 0 $((n - 1))); do
    role="-S s$i"; [ "$i" = 0 ] && role="-M m0"
    extra="-p ${sched[$((i % 16))]}"
    [ $((i % 4)) = 3 ] && extra="$extra -L 0"
    [ -n "$dict" ] && extra="$extra -x $dict"
    [ "$minlen" -gt 0 ] && extra="$extra -g $minlen"
    ( "$AFL_ROOT/afl-fuzz" -i "$seeds" -o "$out/afl" $role -t $(( ${ENGINE_TIMEOUT_S:-300} * 1000 )) -m none -G "$maxlen" -V "$dur" $extra -- "$h" > "$out/log_$i.txt" 2>&1; echo "instance $i ended rc=$?" ) &
    sleep 1
  done
}
engine_stats() {  # <out> -> "execs=<n> corpus=<n>"
  cat "$1"/afl/*/fuzzer_stats 2>/dev/null | awk '$1=="execs_done"{e+=$3} $1=="corpus_count"{c+=$3} END{printf "execs=%d corpus=%d\n", e, c}'
}
engine_corpus() { ls -d "$1"/afl/*/queue 2>/dev/null; }
