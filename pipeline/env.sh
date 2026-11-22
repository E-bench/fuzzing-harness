# Common environment of every step (source it; needs DFZ_TARGET=<name of a directory under targets/>).
#   DFZ_ROOT   this checkout           DFZ_WORK  all generated data (default $DFZ_ROOT/work)
#   DFZ_TOOLS  the fuzzers             T         the target's work directory ($DFZ_WORK/<target>)
# Machine values (modules, Slurm account, ...) come from $DFZ_ROOT/env.local.sh when it exists.
DFZ_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[ -f "$DFZ_ROOT/env.local.sh" ] && source "$DFZ_ROOT/env.local.sh"
export DFZ_ROOT DFZ_WORK=${DFZ_WORK:-$DFZ_ROOT/work}
export DFZ_TOOLS=${DFZ_TOOLS:-$DFZ_WORK/tools}
export AFL_ROOT=${AFL_ROOT:-$DFZ_TOOLS/AFLplusplus} HFUZZ_ROOT=${HFUZZ_ROOT:-$DFZ_TOOLS/honggfuzz}
# CPUs of this process. Inside a Slurm job its allocation counts, whatever was inherited: sbatch passes the
# submitting shell's environment on, and a value from the login node would start that many workers on fewer CPUs.
export NPROC=${SLURM_CPUS_PER_TASK:-${NPROC:-$(nproc)}}
# How much may run at once. DFZ_CPU_BUDGET: CPUs in total; DFZ_JOB_CPUS: CPUs of one Slurm job (the largest job the
# cluster accepts). The steps split their work into as many jobs as the budget allows (dfz_shards below). Without
# Slurm, or without a budget, everything runs here on NPROC CPUs.
export DFZ_JOB_CPUS=${DFZ_JOB_CPUS:-$NPROC} DFZ_CPU_BUDGET=${DFZ_CPU_BUDGET:-0}
# Memory a job asks for, in GB per CPU. Clusters cap memory per user as well as CPUs: asking for more than the work
# needs leaves CPUs of the budget idle behind the memory cap.
export DFZ_MEM_PER_CPU=${DFZ_MEM_PER_CPU:-2}
dfz_mem() { echo "$(( ${1:-$DFZ_JOB_CPUS} * DFZ_MEM_PER_CPU ))G"; }
dfz_shards() {  # dfz_shards [percent of the budget] -> number of jobs of DFZ_JOB_CPUS CPUs
  local n=$(( DFZ_CPU_BUDGET * ${1:-100} / 100 / DFZ_JOB_CPUS ))
  command -v sbatch >/dev/null 2>&1 || n=1
  echo $(( n > 1 ? n : 1 ))
}
dfz_wait() {  # dfz_wait <job id>: block until the Slurm job has left the queue
  while squeue -h -j "$1" 2>/dev/null | grep -q .; do sleep 20; done
}
mkdir -p "$DFZ_WORK/logs" "$DFZ_TOOLS/bin"
ulimit -c 0     # no core dumps: crashing children would fill the disk
if [ -n "${DFZ_TARGET:-}" ]; then
  export TARGET_DIR=$DFZ_ROOT/targets/$DFZ_TARGET T=$DFZ_WORK/$DFZ_TARGET
  [ -f "$TARGET_DIR/target.toml" ] || { echo "no such target: $DFZ_TARGET" >&2; exit 2; }
  mkdir -p "$T"/{src,deps,build,bin,data,seeds,runs,logs}
  # target.toml -> shell variables (CFG_*) and the harness's flat configuration file
  eval "$(python3 -I "$DFZ_ROOT/pipeline/cfg.py" shell "$TARGET_DIR/target.toml")"
  # (rewritten only when it changes: many jobs start at once, and replacing a file that others are opening fails
  # for some of them on a network file system)
  _cfg=$(mktemp) && python3 -I "$DFZ_ROOT/pipeline/cfg.py" harness "$TARGET_DIR/target.toml" > "$_cfg" || { echo "cannot read $TARGET_DIR/target.toml" >&2; exit 2; }
  cmp -s "$_cfg" "$T/harness.cfg" || { cp "$_cfg" "$T/harness.cfg.$(hostname -s).$$" && mv "$T/harness.cfg.$(hostname -s).$$" "$T/harness.cfg"; }
  rm -f "$_cfg"
  export DFZ_CFG=$T/harness.cfg
fi
# expand the placeholders of a target.toml command: {target} {side} {src} {deps} {build} {out} {data} {seeds} {jobs}
dfz_expand() {  # dfz_expand <template> [side] [flavour]
  local s=$1 side=${2:-} fl=${3:-}
  s=${s//\{target\}/$TARGET_DIR}; s=${s//\{side\}/$side}; s=${s//\{flavour\}/$fl}
  s=${s//\{src\}/$T/src/$side}; s=${s//\{deps\}/$T/deps/$side}; s=${s//\{build\}/$T/build/${side}_$fl}
  s=${s//\{out\}/$T/bin/${side}_$fl}; s=${s//\{data\}/$T/data}; s=${s//\{seeds\}/$T/seeds}; s=${s//\{bin\}/$T/bin}
  s=${s//\{jobs\}/$NPROC}; s=${s//\{pipeline\}/$DFZ_ROOT/pipeline}
  printf '%s' "$s"
}
