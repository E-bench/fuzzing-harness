#!/usr/bin/env bash
# Submit a campaign to Slurm: one job per engine and surface (times REPS).
#   DFZ_TARGET=<target> TAG=<campaign> [ENGINES="afl afl-blind libafl libfuzzer hfuzz"] [SURFACES="<all of the target>"]
#   [DUR=3600] [NCPU=<n>] [REPS=1] [MEM=<n>G] pipeline/steps/submit.sh
# NCPU defaults to what fills the CPU budget (DFZ_CPU_BUDGET / number of jobs, at most DFZ_JOB_CPUS). Slurm options
# come from DFZ_SBATCH (env.local.sh). DRY=1 prints the commands instead.
source "$(dirname "$0")/../env.sh"
TAG=${TAG:?}; DUR=${DUR:-3600}; REPS=${REPS:-1}
ENGINES=${ENGINES:-$(cd "$DFZ_ROOT/pipeline/engines" && ls *.sh | sed 's/\.sh$//' | tr '\n' ' ')}
SURFACES=${SURFACES:-$CFG_SURFACES}
njobs=$(( $(echo $ENGINES | wc -w) * $(echo $SURFACES | wc -w) * REPS ))
if [ -z "${NCPU:-}" ]; then
  NCPU=$(( DFZ_CPU_BUDGET / njobs )); [ "$NCPU" -gt "$DFZ_JOB_CPUS" ] && NCPU=$DFZ_JOB_CPUS; [ "$NCPU" -lt 1 ] && NCPU=$DFZ_JOB_CPUS
fi
MEM=${MEM:-$(dfz_mem "$NCPU")}
echo "$njobs jobs x $NCPU CPUs = $(( njobs * NCPU )) CPUs (budget $DFZ_CPU_BUDGET), $MEM each"
mkdir -p "$T/runs/$TAG/logs"
LIMIT=$(( DUR / 60 + 30 ))
for rep in $(seq 1 "$REPS"); do for e in $ENGINES; do for s in $SURFACES; do
  cmd=(sbatch --parsable $DFZ_SBATCH -J "dfz_$TAG" -c "$NCPU" --mem="$MEM" -t "$LIMIT" -o "$T/runs/$TAG/logs/$e.$s.%j.log"
       --wrap "DFZ_TARGET=$DFZ_TARGET ENGINE=$e SURFACE=$s TAG=$TAG DUR=$DUR NCPU=$NCPU exec bash $DFZ_ROOT/pipeline/steps/campaign.sh")
  if [ -n "${DRY:-}" ]; then printf '%q ' "${cmd[@]}"; echo; else echo "$e $s job $("${cmd[@]}")"; fi
done; done; done
