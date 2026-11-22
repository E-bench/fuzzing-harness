# AFL++ without any feedback from the programs under test (DFZ_NOFEEDBACK): the black-box baseline. Same mutators and
# schedules as afl.sh; the corpus grows only by the harness's own few edges.
source "$(dirname "${BASH_SOURCE[0]}")/afl.sh"
engine_build() { return 0; }          # uses harness_afl
afl_blind_run=$(declare -f engine_run)
eval "${afl_blind_run/engine_run/afl_engine_run}"
engine_run() { export DFZ_NOFEEDBACK=1; afl_engine_run "$@"; }
