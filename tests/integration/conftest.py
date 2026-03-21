"""
Integration test fixtures — run against the native host (no VMs).

Session-scoped:
  provider_process — starts the provider in --dev (reattach) mode once per
                     session; dev mode uses insecure gRPC so there is no TLS
                     overhead and no per-command provider process spawn.
  provider_install — installs the provider into a filesystem mirror for
                     'tofu init', and exposes TF_REATTACH_PROVIDERS so all
                     subsequent terraform commands reuse the live process.

Prerequisites:
  tofu or terraform on PATH
  uv sync (or pip install -e .) already run
"""

import os
import re as _re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
PROVIDER_HOST = "local"
PROVIDER_NS = "terrible"
PROVIDER_TYPE = "terrible"
PROVIDER_VERSION = "0.0.1"


def _find_provider_entrypoint() -> Path:
    # Prefer the venv-local binary; fall back to wherever it's on PATH.
    venv_bin = REPO_ROOT / ".venv" / "bin" / "terraform-provider-terrible"
    if venv_bin.exists():
        return venv_bin
    on_path = shutil.which("terraform-provider-terrible")
    if on_path:
        return Path(on_path)
    raise RuntimeError(
        "terraform-provider-terrible not found in .venv/bin/ or on PATH.\nRun 'uv sync' or 'pip install -e .' first."
    )


_PROVIDER_ENTRYPOINT = _find_provider_entrypoint()
_REATTACH_RE = _re.compile(r"TF_REATTACH_PROVIDERS='(.+)'")


def _find_tf() -> str:
    for name in ("tofu", "terraform"):
        if shutil.which(name):
            return name
    raise RuntimeError("Neither 'tofu' nor 'terraform' found on PATH")


@pytest.fixture(scope="session")
def provider_process():
    """
    Start the provider in --dev mode and capture TF_REATTACH_PROVIDERS.

    Dev mode uses an insecure gRPC socket (no TLS) and keeps the provider
    process alive across all terraform commands in the session, eliminating
    per-command Python startup (~340ms each).
    """
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [str(_PROVIDER_ENTRYPOINT), "--dev"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )

    reattach = None
    for _ in range(30):
        line = proc.stdout.readline()
        if not line:
            break
        m = _REATTACH_RE.search(line)
        if m:
            reattach = m.group(1)
            break

    if not reattach:
        proc.kill()
        proc.wait()
        raise RuntimeError("Provider did not emit TF_REATTACH_PROVIDERS within expected output")

    print(f"\n[setup] Provider PID={proc.pid} started in dev/reattach mode", flush=True)
    yield reattach
    proc.kill()
    proc.wait()


@pytest.fixture(scope="session")
def provider_install(tmp_path_factory, provider_process):
    """
    Install the provider binary into a filesystem mirror directory and write a
    .terraformrc pointing at it so tofu init works offline.

    Also exposes TF_REATTACH_PROVIDERS so test commands reuse the live process.
    """
    from tf.runner import install_provider

    plugin_dir = tmp_path_factory.mktemp("plugins")
    print(f"\n[setup] Installing provider from {_PROVIDER_ENTRYPOINT}", flush=True)
    install_provider(
        PROVIDER_HOST,
        PROVIDER_NS,
        PROVIDER_TYPE,
        PROVIDER_VERSION,
        plugin_dir,
        _PROVIDER_ENTRYPOINT,
    )

    tfrc = tmp_path_factory.mktemp("tfrc") / ".terraformrc"
    tfrc.write_text(
        f"provider_installation {{\n"
        f"  filesystem_mirror {{\n"
        f'    path    = "{plugin_dir}"\n'
        f'    include = ["{PROVIDER_HOST}/{PROVIDER_NS}/{PROVIDER_TYPE}"]\n'
        f"  }}\n"
        f"  direct {{\n"
        f'    exclude = ["{PROVIDER_HOST}/{PROVIDER_NS}/{PROVIDER_TYPE}"]\n'
        f"  }}\n"
        f"}}\n"
    )

    tf_bin = _find_tf()
    print(f"[setup] Using terraform binary: {tf_bin}", flush=True)
    return {
        "plugin_dir": plugin_dir,
        "tfrc": tfrc,
        "tf_bin": tf_bin,
        "reattach_json": provider_process,
    }
