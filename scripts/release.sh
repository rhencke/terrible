#!/usr/bin/env bash
# Release script for terraform-provider-terrible.
#
# Usage:
#   scripts/release.sh VERSION <<'EOF'
#   Release notes markdown (used for both the tag message and GitHub release)
#   EOF
#
# The script will:
#   1. Verify on main branch, in sync with origin/main, and working tree is clean
#   2. Verify pyproject.toml version matches VERSION
#   3. Run the full test suite
#   4. Create an annotated git tag vVERSION and push it
#   5. Wait for the Actions release workflow to build and publish assets
#   6. Report the release URL
#
# Requirements: git, uv, gh (GitHub CLI authenticated)

set -euo pipefail

VERSION="${1:?Usage: $0 VERSION  (release notes read from stdin)}"
TAG="v${VERSION}"

# Verify on main branch
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "${CURRENT_BRANCH}" != "main" ]]; then
    echo "ERROR: not on main branch (currently on '${CURRENT_BRANCH}')." >&2
    exit 1
fi

# Fetch and verify in sync with origin/main
git fetch origin main
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"
if [[ "${LOCAL}" != "${REMOTE}" ]]; then
    echo "ERROR: local main is not in sync with origin/main." >&2
    echo "       local:  ${LOCAL}" >&2
    echo "       remote: ${REMOTE}" >&2
    exit 1
fi

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
# The release workflow's publish job creates the GitHub release and uploads assets.
# Do NOT create the release here — creating it before assets are uploaded makes it
# immutable and the subsequent `gh release upload` call fails with HTTP 422.
git tag -a "${TAG}" -m "${TAG} — ${TITLE}"$'\n\n'"${NOTES}"
git push origin "${TAG}"

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

echo "Watching run ${RUN_ID}..." >&2
echo "  https://github.com/rhencke/terraform-provider-terrible/actions/runs/${RUN_ID}" >&2
echo "" >&2

# Poll until the run completes, printing timestamped per-job progress.
STATUS=1
while true; do
    RUN_JSON="$(gh run view "${RUN_ID}" --json status,conclusion,jobs 2>/dev/null)"
    RUN_STATUS="$(echo "${RUN_JSON}" | jq -r '.status')"
    RUN_CONCLUSION="$(echo "${RUN_JSON}" | jq -r '.conclusion // ""')"

    TIMESTAMP="$(date '+%H:%M:%S')"
    echo "[${TIMESTAMP}] run: ${RUN_STATUS}${RUN_CONCLUSION:+ (${RUN_CONCLUSION})}" >&2

    # For each job, show its conclusion or the currently-running step
    echo "${RUN_JSON}" | jq -r '
        .jobs[] |
        . as $job |
        ($job.conclusion // (
            ($job.steps // [] | map(select(.status == "in_progress")) | last // ($job.steps // [] | last) | .name // "starting")
        )) as $detail |
        "  \($job.name): \($detail)"
    ' 2>/dev/null >&2

    echo "" >&2

    if [[ "${RUN_STATUS}" == "completed" ]]; then
        [[ "${RUN_CONCLUSION}" == "success" ]] && STATUS=0 || STATUS=1
        break
    fi

    sleep 20
done

if [[ $STATUS -eq 0 ]]; then
    echo ""
    echo "Released ${TAG}: $(gh release view "${TAG}" --json url -q '.url')"
else
    echo ""
    echo "ERROR: release workflow failed. Assets were NOT published." >&2
    echo "Check failed jobs with: gh run view ${RUN_ID} --json jobs --jq '.jobs[] | {name, conclusion}'" >&2
    exit 1
fi
