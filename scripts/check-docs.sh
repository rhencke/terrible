#!/usr/bin/env bash
# Check that docs/ is up to date by regenerating and diffing.
# Installs tfplugindocs automatically if not found.
#
# Usage: scripts/check-docs.sh

set -euo pipefail

TFPLUGINDOCS="${TFPLUGINDOCS:-/tmp/tfplugindocs}"
if ! command -v "${TFPLUGINDOCS}" &>/dev/null && [[ ! -x "${TFPLUGINDOCS}" ]]; then
    OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
    ARCH="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
    echo "check-docs: downloading tfplugindocs..."
    curl -fsSL "https://github.com/hashicorp/terraform-plugin-docs/releases/download/v0.20.0/tfplugindocs_0.20.0_${OS}_${ARCH}.zip" \
        -o /tmp/tfplugindocs.zip
    unzip -o /tmp/tfplugindocs.zip tfplugindocs -d /tmp
    chmod +x /tmp/tfplugindocs
fi

TFPLUGINDOCS="${TFPLUGINDOCS}" make install-provider docs
if ! git diff --exit-code docs/ >/dev/null 2>&1; then
    echo ""
    echo "ERROR: docs/ is stale — run 'make docs' and commit the result." >&2
    git diff --stat docs/
    exit 1
fi
