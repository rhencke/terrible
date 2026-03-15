"""
Integration test fixtures — run against the native host (no VMs).

Session-scoped:
  provider_install — installs the dev-mode provider binary into a tmp plugin
                     dir and writes a .terraformrc filesystem_mirror so
                     tofu init resolves the provider locally (no network).

The provider binary used is the editable-install entrypoint from the project
venv (.venv/bin/terraform-provider-terrible), so no separate build step is
needed — just 'uv sync' (or 'pip install -e .').

Prerequisites:
  tofu or terraform on PATH
  uv sync (or pip install -e .) already run
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

_PROVIDER_ENTRYPOINT = REPO_ROOT / ".venv" / "bin" / "terraform-provider-terrible"


def _find_tf() -> str:
    for name in ("tofu", "terraform"):
        if shutil.which(name):
            return name
    raise RuntimeError("Neither 'tofu' nor 'terraform' found on PATH")


@pytest.fixture(scope="session")
def provider_install(tmp_path_factory):
    """
    Install the dev provider into a filesystem mirror directory and write a
    .terraformrc pointing at it so tofu init works offline.
    """
    from tf.runner import install_provider

    assert _PROVIDER_ENTRYPOINT.exists(), (
        f"Provider entrypoint not found: {_PROVIDER_ENTRYPOINT}\n"
        "Run 'uv sync' or 'pip install -e .' first."
    )

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
