#!/usr/bin/env bash
# Copy this checkout to the machine that builds and fuzzes (the work/ directory there is never touched).
# Needs remote.local.sh (git-ignored): DFZ_REMOTE=<user@host>, DFZ_REMOTE_DIR=<directory, relative to the remote home>.
set -euo pipefail
cd "$(dirname "$0")"; source ./remote.local.sh
ssh -q -T "$DFZ_REMOTE" "mkdir -p $DFZ_REMOTE_DIR"
rsync -a --delete --exclude work --exclude .git --exclude __pycache__ --exclude remote.local.sh ./ "$DFZ_REMOTE:$DFZ_REMOTE_DIR/"
echo "deployed to $DFZ_REMOTE:$DFZ_REMOTE_DIR"
