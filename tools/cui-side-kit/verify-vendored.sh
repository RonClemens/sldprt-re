#!/bin/sh
# Verify a verbatim vendored copy of a public repository (docs/data-boundary-cui-side.md).
#   usage: verify-vendored.sh <vendored-dir> <repo-url> <tag-or-branch> <expected-commit-sha>
# <tag-or-branch> is a release tag (the normal case) or a branch name such as main (when the mirror deliberately tracks a branch).
# Fetches it, checks that it resolves to the expected commit, and compares the vendored tree with that commit's tree: any difference (a local edit, a missing or extra file) fails.
# With a branch, the check also fails once the branch has moved past <expected-commit-sha>: updating a tracked mirror is then a deliberate step (pull, then pass the new sha).
set -e
dir="$1"; url="$2"; ref="$3"; sha="$4"
[ -n "$dir" ] && [ -n "$url" ] && [ -n "$ref" ] && [ -n "$sha" ] || { echo "usage: $0 <vendored-dir> <repo-url> <tag-or-branch> <expected-sha>" >&2; exit 2; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
# core.autocrlf=false: the reference is the committed bytes (LF), whatever this machine's Windows default is.
git clone --quiet --no-checkout "$url" "$tmp/r"
git -C "$tmp/r" config core.autocrlf false
if got="$(git -C "$tmp/r" rev-parse --verify --quiet "refs/tags/$ref^{commit}")"; then kind=tag
elif got="$(git -C "$tmp/r" rev-parse --verify --quiet "refs/remotes/origin/$ref^{commit}")"; then kind=branch
else echo "vendored: $ref is neither a tag nor a branch of $url" >&2; exit 1; fi
[ "$got" = "$sha" ] || { echo "vendored: $kind $ref resolves to $got, expected $sha" >&2; exit 1; }
git -C "$tmp/r" checkout --quiet "$sha"
if diff -r -q --exclude=.git "$tmp/r" "$dir" >/dev/null 2>&1; then echo "vendored: $dir matches $ref ($sha)"; else
  echo "vendored: $dir differs from $ref ($sha):" >&2; diff -r -q --exclude=.git "$tmp/r" "$dir" | sed 's/^/  /' >&2
  if diff -r -q --strip-trailing-cr --exclude=.git "$tmp/r" "$dir" >/dev/null 2>&1; then echo "  (the only differences are line endings: $dir was probably checked out with core.autocrlf=true; clone it again with git -c core.autocrlf=false)" >&2; fi
  exit 1; fi
