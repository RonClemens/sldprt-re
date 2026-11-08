#!/bin/sh
# Installs the data-boundary git hooks for this clone (docs/data-boundary.md): pre-commit checks the staged files, pre-push the range being pushed.
set -e
cd "$(git rev-parse --show-toplevel)"
chmod +x tools/githooks/pre-commit tools/githooks/pre-push
git config core.hooksPath tools/githooks
echo "boundary hooks installed (core.hooksPath = tools/githooks). Optional private deny-list: ~/.config/boundary/denylist.txt (see docs/data-boundary.md)."
