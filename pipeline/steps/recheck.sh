#!/usr/bin/env bash
# Replay every stored difference of a campaign on the reference builds under the current configuration, and list the
# inputs on which the programs still differ (pipeline/tools/recheck.py):
#   DFZ_TARGET=<target> TAG=<campaign> [SHARDS=n] [WAIT=1] pipeline/steps/recheck.sh
# The step to repeat after each change that should make differences disappear: an input constraint, an exemption
# or a normalisation in the adapter, a patch. SHARDS defaults to what the CPU budget allows (env.sh): with one shard everything
# runs here; otherwise the replay is submitted as SHARDS Slurm jobs plus a last job that merges, whose id is printed
# (WAIT=1: wait for it and print the counts).
source "$(dirname "$0")/../env.sh"
TAG=${TAG:?}; SHARDS=${SHARDS:-$(dfz_shards)}
PY=(python3 -I "$DFZ_ROOT/pipeline/tools/recheck.py" "$T" "$TAG" "$TARGET_DIR/target.toml" --jobs "$NPROC")
if [ -n "${PHASE:-}" ]; then exec "${PY[@]}" --phase "$PHASE" --shard "${SHARD:-0/1}" "$@"; fi
if [ "$SHARDS" -le 1 ]; then exec "${PY[@]}" "$@"; fi
scancel -u "$USER" -n "dfz_rck_$TAG" 2>/dev/null   # an earlier submission of this step and tag would write into the same directory
rm -rf "$T/recheck/$TAG/.shards"; mkdir -p "$T/recheck/$TAG"   # the results of an earlier run stay until the last job replaces them
me="DFZ_TARGET=$DFZ_TARGET TAG=$TAG bash $DFZ_ROOT/pipeline/steps/recheck.sh $*"
sb=(sbatch --parsable $DFZ_SBATCH -J "dfz_rck_$TAG" -c "$DFZ_JOB_CPUS" --mem="${MEM:-$(dfz_mem)}" -t "${LIMIT:-120}")
ids=
for k in $(seq 0 $((SHARDS - 1))); do ids=$ids:$("${sb[@]}" -o "$T/logs/recheck_$TAG.$k.%j.log" --wrap "PHASE=replay SHARD=$k/$SHARDS $me"); done
last=$("${sb[@]}" --dependency=afterany$ids -o "$T/logs/recheck_$TAG.merge.%j.log" --wrap "PHASE=merge $me")
echo "$last"
[ -n "${WAIT:-}" ] && { dfz_wait "$last"; tail -1 "$T/logs/recheck_$TAG.merge.$last.log"; }
