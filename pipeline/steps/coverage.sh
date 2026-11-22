#!/usr/bin/env bash
# Replay a campaign's corpora on the coverage builds and write the coverage report (pipeline/tools/coverage.py):
#   DFZ_TARGET=<target> TAG=<campaign> [SHARDS=n] [WAIT=1] pipeline/steps/coverage.sh [coverage.py options]
# SHARDS defaults to what the CPU budget allows (env.sh). With one shard everything runs here; otherwise the replay is
# submitted as SHARDS Slurm jobs plus a last job that builds the report, whose id is printed (WAIT=1: wait for it).
# PHASE and SHARD are set by those jobs.
source "$(dirname "$0")/../env.sh"
TAG=${TAG:?}; SHARDS=${SHARDS:-$(dfz_shards)}
PY=(python3 -I "$DFZ_ROOT/pipeline/tools/coverage.py" "$T" "$TAG" "$TARGET_DIR/target.toml" --jobs "$NPROC")
if [ -n "${PHASE:-}" ]; then exec "${PY[@]}" --phase "$PHASE" --shard "${SHARD:-0/1}" "$@"; fi
if [ "$SHARDS" -le 1 ]; then exec "${PY[@]}" "$@"; fi
scancel -u "$USER" -n "dfz_cov_$TAG" 2>/dev/null   # an earlier submission of this step and tag would write into the same directory
rm -rf "$T/coverage/$TAG/.shards"; mkdir -p "$T/coverage/$TAG"   # the results of an earlier run stay until the last job replaces them
me="DFZ_TARGET=$DFZ_TARGET TAG=$TAG bash $DFZ_ROOT/pipeline/steps/coverage.sh $*"
sb=(sbatch --parsable $DFZ_SBATCH -J "dfz_cov_$TAG" -c "$DFZ_JOB_CPUS" --mem="${MEM:-$(dfz_mem)}" -t "${LIMIT:-240}")
ids=
for k in $(seq 0 $((SHARDS - 1))); do ids=$ids:$("${sb[@]}" -o "$T/logs/cov_$TAG.$k.%j.log" --wrap "PHASE=replay SHARD=$k/$SHARDS $me"); done
last=$("${sb[@]}" --dependency=afterany$ids -o "$T/logs/cov_$TAG.report.%j.log" --wrap "PHASE=report $me")
echo "$last"
[ -n "${WAIT:-}" ] && { dfz_wait "$last"; tail -4 "$T/logs/cov_$TAG.report.$last.log"; }
