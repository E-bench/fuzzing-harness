#!/usr/bin/env bash
# Recheck of the differences, coverage, report and check of a finished campaign, in parallel within the CPU budget:
#   DFZ_TARGET=<target> TAG=<campaign> [WAIT=1] pipeline/steps/post.sh
# A third of the budget goes to the recheck, two thirds to the coverage replay; the campaign report and the check
# (steps/check.sh) run when both are done. Prints the job ids (WAIT=1: wait for the report).
source "$(dirname "$0")/../env.sh"
TAG=${TAG:?}; S=$DFZ_ROOT/pipeline/steps
if [ "$(dfz_shards)" -le 1 ]; then bash "$S/recheck.sh"; bash "$S/coverage.sh"; bash "$S/report.sh"; exec bash "$S/check.sh"; fi
w=${WAIT:-}; unset WAIT
rck=$(SHARDS=$(dfz_shards 33) bash "$S/recheck.sh" | tail -1)
cov=$(SHARDS=$(dfz_shards 66) bash "$S/coverage.sh" | tail -1)
rep=$(sbatch --parsable $DFZ_SBATCH -J "dfz_rep_$TAG" --dependency=afterany:$rck:$cov -c 1 --mem=4G -t 30 -o "$T/logs/report_$TAG.%j.log" --wrap "export DFZ_TARGET=$DFZ_TARGET TAG=$TAG; bash $S/report.sh; bash $S/check.sh")
echo "recheck=$rck coverage=$cov report=$rep"
[ -n "$w" ] && { dfz_wait "$rep"; grep -v "^#\|^\$" "$T/logs/report_$TAG.$rep.log" | tail -60; }
