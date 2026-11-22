#!/usr/bin/env bash
# Recheck of the differences, coverage, sanitizer replay, report and check of a finished campaign, in parallel within
# the CPU budget:
#   DFZ_TARGET=<target> TAG=<campaign> [WAIT=1] pipeline/steps/post.sh
# A quarter of the budget goes to the recheck, half to the coverage replay, a quarter to the sanitizer replay (when
# the builds of flavour san exist); the campaign report and the check (steps/check.sh) run when all are done. Prints
# the job ids (WAIT=1: wait for the report).
source "$(dirname "$0")/../env.sh"
TAG=${TAG:?}; S=$DFZ_ROOT/pipeline/steps
SAN=; [ -x "$T/bin/${CFG_A_NAME}_san" ] && [ -x "$T/bin/${CFG_B_NAME}_san" ] && SAN=1
if [ "$(dfz_shards)" -le 1 ]; then bash "$S/recheck.sh"; bash "$S/coverage.sh"; [ -n "$SAN" ] && bash "$S/sanitize.sh"; bash "$S/report.sh"; exec bash "$S/check.sh"; fi
w=${WAIT:-}; unset WAIT
scancel -u "$USER" -n "dfz_rep_$TAG" 2>/dev/null   # the report job of an earlier submission
rck=$(SHARDS=$(dfz_shards 25) bash "$S/recheck.sh" | tail -1)
cov=$(SHARDS=$(dfz_shards 50) bash "$S/coverage.sh" | tail -1)
san=; [ -n "$SAN" ] && san=$(SHARDS=$(dfz_shards 25) bash "$S/sanitize.sh" | tail -1)
rep=$(sbatch --parsable $DFZ_SBATCH -J "dfz_rep_$TAG" --dependency=afterany:$rck:$cov${san:+:$san} -c 1 --mem=4G -t 30 -o "$T/logs/report_$TAG.%j.log" --wrap "export DFZ_TARGET=$DFZ_TARGET TAG=$TAG; bash $S/report.sh; bash $S/check.sh")
echo "recheck=$rck coverage=$cov sanitize=${san:-none} report=$rep"
[ -n "$w" ] && { dfz_wait "$rep"; grep -v "^#\|^\$" "$T/logs/report_$TAG.$rep.log" | tail -60; }
