"""
Integration test fixtures — run against the native host (no VMs).

Session-scoped:
  provider_binary  — builds the self-contained pex binary once per session
  provider_install — installs it into a tmp plugin dir and writes a
                     .terraformrc with a filesystem_mirror so tofu init
                     resolves the provider locally (no network needed)

Prerequisites:
  tofu or terraform on PATH
  uv (for pex build)
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
PROVIDER_HOST = "local"
PROVIDER_NS = "terrible"
PROVIDER_TYPE = "terrible"
PROVIDER_VERSION = "0.0.1"


def _find_tf() -> str:
    for name in ("tofu", "terraform"):
        if shutil.which(name):
            return name
    raise RuntimeError("Neither 'tofu' nor 'terraform' found on PATH")


@pytest.fixture(scope="session")
def provider_binary():
    """Build the self-contained provider binary once for the test session."""
    binary = REPO_ROOT / "terraform-provider-terrible"
    print("\n[setup] Building provider binary (pex)...", flush=True)
    subprocess.run(["make", "build-binary"], cwd=str(REPO_ROOT), check=True)
    assert binary.exists(), f"build-binary did not produce {binary}"
    print(f"[setup] Binary ready: {binary}", flush=True)
    return binary


@pytest.fixture(scope="session")
def provider_install(provider_binary, tmp_path_factory):
    """
    Install the provider binary into a filesystem mirror directory and write a
    .terraformrc that points tofu/terraform at it.  This lets 'tofu init' run
    offline — no registry lookup required.
    """
    from tf.runner import install_provider

    plugin_dir = tmp_path_factory.mktemp("plugins")
    print(f"[setup] Installing provider into {plugin_dir} ...", flush=True)
    install_provider(
        PROVIDER_HOST,
        PROVIDER_NS,
        PROVIDER_TYPE,
        PROVIDER_VERSION,
        plugin_dir,
        provider_binary,
    )

    tfrc = tmp_path_factory.mktemp("tfrc") / ".terraformrc"
    tfrc.write_text(
        f'provider_installation {{\n'
        f'  filesystem_mirror {{\n'
        f'    path    = "{plugin_dir}"\n'
        f'    include = ["{PROVIDER_HOST}/{PROVIDER_NS}/{PROVIDER_TYPE}"]\n'
        f'  }}\n'
        f'  direct {{\n'
        f'    exclude = ["{PROVIDER_HOST}/{PROVIDER_NS}/{PROVIDER_TYPE}"]\n'
        f'  }}\n'
        f'}}\n'
    )

    tf_bin = _find_tf()
    print(f"[setup] Using terraform binary: {tf_bin}", flush=True)
    return {"plugin_dir": plugin_dir, "tfrc": tfrc, "tf_bin": tf_bin}
