#!/usr/bin/env bash
# Release script for terrible.
#
# Usage:
#   scripts/release.sh VERSION <<'EOF'
#   Release notes markdown (used for both the tag message and GitHub release)
#   EOF
#
# The script will:
#   1. Verify the working tree is clean
#   2. Run the full test suite
#   3. Create an annotated git tag vVERSION
#   4. Push the tag to origin
#   5. Create a GitHub release with the supplied notes
#
# Requirements: git, uv, gh (GitHub CLI authenticated)

set -euo pipefail

VERSION="${1:?Usage: $0 VERSION  (release notes read from stdin)}"
TAG="v${VERSION}"

# Verify clean working tree
if [[ -n "$(git status --porcelain)" ]]; then
    echo "ERROR: working tree is dirty — commit all changes before releasing." >&2
    exit 1
fi

# Read release notes from stdin
echo "Reading release notes from stdin (Ctrl-D to finish if interactive)..."
NOTES="$(cat)"

if [[ -z "${NOTES}" ]]; then
    echo "ERROR: release notes must not be empty." >&2
    exit 1
fi

# Extract first non-empty line as the release title
TITLE="$(echo "${NOTES}" | awk 'NF{print; exit}')"

# Run the test suite
echo "Running test suite..."
uv run pytest -q

# Create and push the annotated tag
git tag -a "${TAG}" -m "${TAG} — ${TITLE}"$'\n\n'"${NOTES}"
git push origin "${TAG}"

# Create GitHub release
gh release create "${TAG}" \
    --title "${TAG} — ${TITLE}" \
    --notes "${NOTES}"

echo ""
echo "Released ${TAG}: $(gh release view "${TAG}" --json url -q '.url')"
