#!/usr/bin/env bash
# Submit a campaign to Slurm: one job per engine and surface (times REPS).
#   DFZ_TARGET=<target> TAG=<campaign> [ENGINES="afl afl-blind libafl libfuzzer hfuzz"] [SURFACES="<all of the target>"]
#   [DUR=3600] [NCPU=<n>] [REPS=1] [MEM=<n>G] [SAN=1] pipeline/steps/submit.sh
# SAN=1 fuzzes on the builds with sanitizers (flavour fuzzsan); the default is [run] sanitizers of the target.
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
# every fuzzer instance can have one program at the memory limit at the same time
SAN=${SAN:-$CFG_FUZZ_SANITIZERS}   # 1: fuzz on the builds with sanitizers (campaign.sh)
LIMIT_MB=${CFG_MEMORY_MB:-0}; [ "$SAN" = 1 ] && LIMIT_MB=$(( LIMIT_MB * CFG_SAN_LARGER ))   # their memory limit is the scaled one
[ "$LIMIT_MB" -gt 0 ] && [ $(( ${MEM%G} * 1024 )) -lt $(( NCPU * LIMIT_MB )) ] &&
  echo "warning: jobs of $MEM for $NCPU fuzzer instances with a memory limit of $LIMIT_MB MB per program run: the machine may end processes when many inputs reach the limit at once" >&2
echo "$njobs jobs x $NCPU CPUs = $(( njobs * NCPU )) CPUs (budget $DFZ_CPU_BUDGET), $MEM each$([ "$SAN" = 1 ] && echo ', on the builds with sanitizers')"
mkdir -p "$T/runs/$TAG/logs"
LIMIT=$(( DUR / 60 + 30 ))
for rep in $(seq 1 "$REPS"); do for e in $ENGINES; do for s in $SURFACES; do
  cmd=(sbatch --parsable $DFZ_SBATCH -J "dfz_$TAG" -c "$NCPU" --mem="$MEM" -t "$LIMIT" -o "$T/runs/$TAG/logs/$e.$s.%j.log"
       --wrap "DFZ_TARGET=$DFZ_TARGET ENGINE=$e SURFACE=$s TAG=$TAG DUR=$DUR NCPU=$NCPU SAN=$SAN exec bash $DFZ_ROOT/pipeline/steps/campaign.sh")
  if [ -n "${DRY:-}" ]; then printf '%q ' "${cmd[@]}"; echo; else echo "$e $s job $("${cmd[@]}")"; fi
done; done; done
