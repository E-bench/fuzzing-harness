#!/usr/bin/env bash
# One fuzzer on one surface on one machine:
#   DFZ_TARGET=<target> ENGINE=<engine> SURFACE=<surface> TAG=<campaign> DUR=<seconds> [NCPU=<n>] [SAN=1] pipeline/steps/campaign.sh
# ENGINE is a file name in pipeline/engines; SURFACE one of the target's [surface.*] tables.
# The fuzzer works in a node-local temporary directory; the results go to work/<target>/runs/<TAG>/<engine>.<surface>.<id>/:
#   meta.txt     engine, surface, host, CPUs, duration, start time, map sizes
#   stats.tsv    every POLL seconds: elapsed seconds, executions, corpus size, difference signatures stored
#   find.tgz     the inputs the harness stored, one directory per signature (find/<hash>/sig.txt, *.bin, FOUND = seconds)
#   corpus.tgz   the fuzzer's corpus (file times kept: the coverage step uses them) and its logs
# The body is one brace group, so that bash has read all of it before it starts: a deployment that replaces this file
# during a run (on a network file system the running shell would lose it) cannot cut the run short.
{
source "$(dirname "$0")/../env.sh"
ENGINE=${ENGINE:?}; SURFACE=${SURFACE:?}; TAG=${TAG:-pilot}; DUR=${DUR:-600}
N=${NCPU:-$NPROC}; ID=${RUN_ID:-$(date +%s)$(printf %05d $(($$ % 100000)))}
source "$DFZ_ROOT/pipeline/engines/$ENGINE.sh" || { echo "unknown engine $ENGINE"; exit 2; }
engine_available || { echo "engine $ENGINE is not installed"; exit 2; }
SU=${SURFACE^^}; SU=${SU//-/_}; v=CFG_SURFACE_${SU}_MAX_LEN; MAXLEN=${!v:?unknown surface $SURFACE}
v=CFG_SURFACE_${SU}_MIN_LEN; MINLEN=${!v}; v=CFG_SURFACE_${SU}_DICT; DICT=${!v}; [ -n "$DICT" ] && DICT=$TARGET_DIR/$DICT
RUN=$T/runs/$TAG/$ENGINE.$SURFACE.$ID; mkdir -p "$RUN"
W=${TMPDIR:-/tmp}/dfz_$ID; SHM=$W/work; [ -w /dev/shm ] && SHM=/dev/shm/dfz_$ID
mkdir -p "$W"/{bin,out,find} "$SHM"
trap 'rm -rf "$W" "$SHM"' EXIT
# Stopping: pkill reaches the fuzzers, the harnesses and the programs under them alike, so a harness can see one of
# its two programs killed. The stop file tells it to store nothing from then on (DFZ_STOP_FILE, dfz.h).
trap 'touch "$W/stop"; pkill -u "$USER" -f "$W/"' TERM INT   # the loop below then ends and the results are archived
# SAN=1 (default: [run] sanitizers of the target): the fuzzsan builds, which carry sanitizers besides the edge coverage
SAN=${SAN:-$CFG_FUZZ_SANITIZERS}; FL=fuzz; [ "$SAN" = 1 ] && FL=fuzzsan
A=$T/bin/${CFG_A_NAME}_$FL; B=$T/bin/${CFG_B_NAME}_$FL; H=$T/bin/harness_$ENGINE_HARNESS
[ -x "$A" ] && [ -x "$B" ] || { echo "no $FL builds: ./dfz build $DFZ_TARGET STEPS=\"$FL harness\""; exit 2; }
cp "$A" "$B" "$H" "$W/bin/"; cp -r "$T/seeds/$SURFACE" "$W/seeds"; cp -r "$T/data" "$W/data"
for try in 1 2 3 4 5; do cp "$DFZ_CFG" "$W/harness.cfg" && break; sleep 2; done   # the configuration this run uses
[ -s "$W/harness.cfg" ] || { echo "cannot copy $DFZ_CFG"; exit 2; }
export DFZ_CFG=$W/harness.cfg; cp "$W/harness.cfg" "$RUN/harness.cfg"
AMAP=$(ASAN_OPTIONS=detect_leaks=0 AFL_DUMP_MAP_SIZE=1 "$A"); BMAP=$(ASAN_OPTIONS=detect_leaks=0 AFL_DUMP_MAP_SIZE=1 "$B"); AMAP=$(( (AMAP + 63) / 64 * 64 )); BMAP=$(( (BMAP + 63) / 64 * 64 ))
export DFZ_GUARDS=$(cat "$T/bin/guards" 2>/dev/null || echo 524288)
export DFZ_SURFACE=$SURFACE DFZ_A=$W/bin/$(basename "$A") DFZ_B=$W/bin/$(basename "$B") DFZ_A_MAP=$AMAP DFZ_B_MAP=$BMAP
export DFZ_WORK=$SHM DFZ_FIND=$W/find DFZ_DATA=$W/data DFZ_STOP_FILE=$W/stop
[ "$SAN" = 1 ] && dfz_san_env   # the sanitizers' options, and the limits scaled by what such a build costs
# the fuzzer's own limit on one execution of the harness: both programs and an extra run at their time limit, and more
export ENGINE_TIMEOUT_S=$(( ${DFZ_TIMEOUT_MS:-$CFG_TIMEOUT_MS} * 3 / 1000 + 30 )); [ "$ENGINE_TIMEOUT_S" -lt 300 ] && ENGINE_TIMEOUT_S=300
T0=$(date +%s)
printf 'engine=%s\nsurface=%s\ntag=%s\ndur=%s\nhost=%s\ncpus=%s\nstart=%s\na_map=%s\nb_map=%s\nguards=%s\nseeds=%s\nsanitizers=%s\ntimeout_ms=%s\nmemory_mb=%s\n' \
  "$ENGINE" "$SURFACE" "$TAG" "$DUR" "$(hostname -s)" "$N" "$T0" "$AMAP" "$BMAP" "$DFZ_GUARDS" "$(ls "$W/seeds" | wc -l)" \
  "$SAN" "${DFZ_TIMEOUT_MS:-$CFG_TIMEOUT_MS}" "${DFZ_MEMORY_MB:-$CFG_MEMORY_MB}" | tee "$RUN/meta.txt"
printf 'elapsed\texecs\tcorpus\tsignatures\n' > "$RUN/stats.tsv"
collect() {
  local now e
  now=$(( $(date +%s) - T0 ))
  # FOUND: seconds from the start to the first input of a signature
  for d in "$W"/find/*/; do [ -d "$d" ] && [ ! -f "$d/FOUND" ] && echo $(( $(stat -c %Y "$d/sig.txt" 2>/dev/null || date +%s) - T0 )) > "$d/FOUND"; done
  e=$(engine_stats "$W/out"); e=${e//execs=/}; e=${e// corpus=/$'\t'}
  printf '%s\t%s\t%s\n' "$now" "$e" "$(ls "$W/find" | wc -l)" >> "$RUN/stats.tsv"
  tar czf "$RUN/.find.tgz" -C "$W" find 2>/dev/null && mv "$RUN/.find.tgz" "$RUN/find.tgz"
}
engine_run "$W/bin/harness_$ENGINE_HARNESS" "$W/seeds" "$W/out" "$N" "$DUR" "$MAXLEN" "$MINLEN" "$DICT"
while true; do
  left=$(( DUR + ${GRACE:-90} + 1 - ($(date +%s) - T0) )); p=${POLL:-60}; [ "$left" -lt "$p" ] && p=$(( left > 1 ? left : 1 ))
  sleep "$p" & wait $!
  pgrep -u "$USER" -f "$W/" > /dev/null || break
  collect
  # a fuzzer may overrun its time limit by a long stage on one input (afl-fuzz finishes its current queue entry)
  [ $(( $(date +%s) - T0 )) -gt $((DUR + ${GRACE:-90})) ] && break
done
touch "$W/stop"; pkill -u "$USER" -f "$W/" 2>/dev/null; wait
collect
tail -3 "$W/out/log_0.txt" | cut -c1-300
( cd "$W/out" && tar czf "$RUN/corpus.tgz" $(engine_corpus "$W/out" | sed "s#^$W/out/##") $(ls log_*.txt */*/fuzzer_stats */*/plot_data 2>/dev/null) 2>/dev/null )
echo "end=$(date +%s)" >> "$RUN/meta.txt"
echo "done signatures=$(ls "$W/find" | wc -l) $(tail -1 "$RUN/stats.tsv")"
exit 0
}
