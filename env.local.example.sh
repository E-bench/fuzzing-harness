# Machine values: copy to env.local.sh (git-ignored) and adjust. Sourced by pipeline/env.sh.
# Toolchain: clang/LLVM 20 (with llvm-cov, llvm-profdata, libFuzzer), python 3.12+, cmake on PATH, for example:
# module load llvm/20 python/3.12 cmake
export DFZ_SBATCH="--account=<account> --partition=<partition>"   # options for every sbatch call
export DFZ_CPU_BUDGET=${DFZ_CPU_BUDGET:-200}   # CPUs to use at once; the steps split their work into jobs to fill it
export DFZ_JOB_CPUS=${DFZ_JOB_CPUS:-16}        # CPUs of one job (the largest job the cluster accepts)
export DFZ_MEM_PER_CPU=${DFZ_MEM_PER_CPU:-2}   # GB per CPU a job asks for (clusters cap memory per user too)
# LibAFL needs a recent stable rustc (rustup): make `cargo +stable` work here, or set DFZ_LIBAFL_BIN to a built libafl-fuzz
