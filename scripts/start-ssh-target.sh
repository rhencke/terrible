#!/usr/bin/env bash
# Start an SSH target on the local runner for integration tests.
# Generates a temporary key pair, authorizes it, and starts sshd on port 2222.
# Prints environment variable exports for TERRIBLE_SSH_* on stdout.
set -euo pipefail

KEY="${TERRIBLE_SSH_KEY_PATH:-/tmp/terrible_test_key}"

ssh-keygen -t ed25519 -N "" -f "${KEY}" -q

mkdir -p ~/.ssh
chmod 700 ~/.ssh
cat "${KEY}.pub" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# Start sshd on port 2222 (doesn't need root, uses user config)
if command -v sshd >/dev/null 2>&1; then
    sudo /usr/sbin/sshd -p 2222 -o StrictModes=no
else
    echo "ERROR: sshd not found" >&2
    exit 1
fi

# Wait for sshd to be ready
for i in $(seq 1 10); do
    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=2 \
        -i "${KEY}" -p 2222 "$(whoami)@127.0.0.1" true 2>/dev/null && break
    sleep 1
done

cat <<EOF
export TERRIBLE_SSH_HOST=127.0.0.1
export TERRIBLE_SSH_PORT=2222
export TERRIBLE_SSH_USER=$(whoami)
export TERRIBLE_SSH_KEY=${KEY}
EOF
