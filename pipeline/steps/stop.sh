#!/usr/bin/env bash
# End a running campaign early and keep its results:  DFZ_TARGET=<target> TAG=<campaign> pipeline/steps/stop.sh
# Every process of the campaign's jobs gets SIGTERM: the fuzzers stop, and each job archives its findings and corpus
# as at a normal end (campaign.sh). A plain `scancel` does the same; `scancel --batch` does not (it signals only the
# job's shell, and the job is killed before it can archive).
source "$(dirname "$0")/../env.sh"
scancel -u "$USER" -n "dfz_${TAG:?}" --signal=TERM --full
echo "sent SIGTERM to the jobs of dfz_$TAG; they archive and leave the queue within a minute"
