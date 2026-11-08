#!/bin/sh
# Verify a verbatim vendored copy of a public repository (docs/data-boundary-cui-side.md).
#   usage: verify-vendored.sh <vendored-dir> <repo-url> <tag> <expected-commit-sha>
# Fetches the tag, checks that it resolves to the expected commit, and compares the vendored tree with the tag's tree: any difference (a local edit, a missing or extra file) fails.
set -e
dir="$1"; url="$2"; tag="$3"; sha="$4"
[ -n "$dir" ] && [ -n "$url" ] && [ -n "$tag" ] && [ -n "$sha" ] || { echo "usage: $0 <vendored-dir> <repo-url> <tag> <expected-sha>" >&2; exit 2; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
git clone --quiet --no-checkout "$url" "$tmp/r"
got="$(git -C "$tmp/r" rev-parse "refs/tags/$tag^{commit}")"
[ "$got" = "$sha" ] || { echo "vendored: tag $tag resolves to $got, expected $sha" >&2; exit 1; }
git -C "$tmp/r" checkout --quiet "$sha"
if diff -r -q --exclude=.git "$tmp/r" "$dir" >/dev/null 2>&1; then echo "vendored: $dir matches $tag ($sha)"; else
  echo "vendored: $dir differs from $tag ($sha):" >&2; diff -r -q --exclude=.git "$tmp/r" "$dir" | sed 's/^/  /' >&2; exit 1; fi
