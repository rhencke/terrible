#!/usr/bin/env bash
# Release script for terraform-provider-terrible.
#
# Usage:
#   scripts/release.sh VERSION <<'EOF'
#   Release notes markdown (used for both the tag message and GitHub release)
#   EOF
#
# The script will:
#   1. Verify the working tree is clean
#   2. Verify pyproject.toml version matches VERSION
#   3. Run the full test suite
#   4. Create an annotated git tag vVERSION and push it
#   5. Create the GitHub release (assets uploaded by the Actions release workflow)
#   6. Wait for and report the release workflow result
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

# Verify pyproject.toml version matches
TOML_VERSION="$(grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')"
if [[ "${TOML_VERSION}" != "${VERSION}" ]]; then
    echo "ERROR: pyproject.toml version is '${TOML_VERSION}', expected '${VERSION}'." >&2
    echo "       Bump the version in pyproject.toml (and uv.lock) before releasing." >&2
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

# Create the GitHub release (placeholder — assets will be attached by the workflow)
gh release create "${TAG}" \
    --title "${TAG} — ${TITLE}" \
    --notes "${NOTES}" \
    --draft=false

echo ""
echo "Tag pushed. Waiting for release workflow..."
echo ""

# Find and watch the workflow run triggered by this tag
sleep 5  # give GitHub a moment to register the run
RUN_ID="$(gh run list --workflow=release.yml --limit=1 --json databaseId --jq '.[0].databaseId')"

if [[ -z "${RUN_ID}" ]]; then
    echo "WARNING: could not find release workflow run. Check:"
    echo "  gh run list --workflow=release.yml"
    exit 1
fi

echo "Watching run ${RUN_ID}..."
if gh run watch "${RUN_ID}" --exit-status; then
    echo ""
    echo "Released ${TAG}: $(gh release view "${TAG}" --json url -q '.url')"
else
    echo ""
    echo "ERROR: release workflow failed. Assets were NOT published." >&2
    echo "Fix the issue, delete the tag and release, then re-release:" >&2
    echo "  git tag -d ${TAG} && git push origin :${TAG}" >&2
    echo "  gh release delete ${TAG} --yes" >&2
    exit 1
fi
