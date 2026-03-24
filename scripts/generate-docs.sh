#!/usr/bin/env bash
# Generate Terraform Registry documentation using tfplugindocs.
#
# Requires:
#   - tofu or terraform in PATH
#   - tfplugindocs in PATH or TFPLUGINDOCS env var
#   - uv in PATH (provider run via `uv run terraform-provider-terrible --dev`)
#
# Usage:
#   scripts/generate-docs.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

TFPLUGINDOCS="${TFPLUGINDOCS:-tfplugindocs}"
TF="${TF:-$(command -v tofu 2>/dev/null || command -v terraform 2>/dev/null)}"

if [[ -z "${TF}" ]]; then
    echo "ERROR: tofu or terraform not found in PATH" >&2
    exit 1
fi

if ! command -v "${TFPLUGINDOCS}" &>/dev/null; then
    echo "ERROR: tfplugindocs not found. Install with:" >&2
    echo "  curl -fsSL https://github.com/hashicorp/terraform-plugin-docs/releases/download/v0.20.0/tfplugindocs_0.20.0_linux_amd64.zip | ..." >&2
    exit 1
fi

TMPDIR="$(mktemp -d)"
PROVIDER_LOG="${TMPDIR}/provider.log"
trap 'rm -rf "${TMPDIR}"; kill "${PROVIDER_PID}" 2>/dev/null || true' EXIT

# Start provider in dev mode; it prints TF_REATTACH_PROVIDERS JSON to stdout
echo "Starting provider in dev mode..."
cd "${REPO_DIR}"
PYTHONUNBUFFERED=1 uv run terraform-provider-terrible --dev > "${PROVIDER_LOG}" 2>&1 &
PROVIDER_PID=$!

# Wait for the reattach JSON to appear
for i in $(seq 1 20); do
    if grep -q 'TF_REATTACH_PROVIDERS' "${PROVIDER_LOG}" 2>/dev/null; then
        break
    fi
    sleep 0.5
done

REATTACH_JSON="$(grep -o '{.*}' "${PROVIDER_LOG}" | tail -1)"
if [[ -z "${REATTACH_JSON}" ]]; then
    echo "ERROR: provider did not start in time. Log:" >&2
    cat "${PROVIDER_LOG}" >&2
    exit 1
fi

# Generate schema via a temp Terraform config using dev-reattach mode
TFDIR="${TMPDIR}/tfschema"
mkdir -p "${TFDIR}"

cat > "${TFDIR}/main.tf" << 'EOF'
terraform {
  required_providers {
    terrible = { source = "registry.terraform.io/rhencke/terrible" }
  }
}
provider "terrible" {}
EOF

echo "Initialising Terraform config..."
"${TF}" -chdir="${TFDIR}" init -no-color >/dev/null

echo "Exporting provider schema..."
TF_REATTACH_PROVIDERS="${REATTACH_JSON}" "${TF}" -chdir="${TFDIR}" providers schema -json > "${TMPDIR}/schema.json"

# Rekey schema from registry.terraform.io/rhencke/terrible → registry.terraform.io/hashicorp/terrible
# (tfplugindocs resolves "terrible" → registry.terraform.io/hashicorp/terrible internally)
python3 - "${TMPDIR}/schema.json" "${TMPDIR}/schema-rekeyed.json" << 'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    schema = json.load(f)
ps = schema["provider_schemas"]
ps["registry.terraform.io/hashicorp/terrible"] = ps.pop("registry.terraform.io/rhencke/terrible")
with open(sys.argv[2], "w") as f:
    json.dump(schema, f)
PYEOF

echo "Generating docs..."
cd "${REPO_DIR}"
"${TFPLUGINDOCS}" generate \
    --provider-name "terrible" \
    --providers-schema "${TMPDIR}/schema-rekeyed.json" \
    --rendered-provider-name "terrible"

echo "Docs generated in docs/"
