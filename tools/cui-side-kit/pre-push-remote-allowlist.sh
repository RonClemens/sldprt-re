#!/bin/sh
# CUI-side pre-push hook (docs/data-boundary-cui-side.md): refuse any push to a remote that is not on this repository's allow-list.
#   .cui-allowed-remotes    one approved remote URL (or URL prefix) per line, committed and reviewed
#   .cui-public-patterns    optional: one extended regex per line for URLs that must never be pushed to (public hosting); an allow-listed URL still wins
# Install: cp tools/cui-side-kit/pre-push-remote-allowlist.sh .git/hooks/pre-push && chmod +x .git/hooks/pre-push   (or core.hooksPath)
top="$(git rev-parse --show-toplevel)"; remote_name="$1"; remote_url="$2"
allowed="$top/.cui-allowed-remotes"; public="$top/.cui-public-patterns"
if [ ! -f "$allowed" ]; then echo "cui-boundary: $allowed is missing: refusing every push until the approved remotes are listed" >&2; exit 1; fi
ok=1
while IFS= read -r line; do
  case "$line" in ''|'#'*) continue ;; esac
  case "$remote_url" in "$line"*) ok=0 ;; esac
done < "$allowed"
if [ "$ok" -ne 0 ]; then
  echo "cui-boundary: refusing to push to '$remote_name' ($remote_url): not an approved remote (see .cui-allowed-remotes)" >&2
  if [ -f "$public" ] && grep -Eqf "$public" <<EOF
$remote_url
EOF
  then echo "cui-boundary: it also matches a public-hosting pattern. Controlled data must never reach a public repository." >&2; fi
  exit 1
fi
exit 0
