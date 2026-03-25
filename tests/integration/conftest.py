"""
Integration test fixtures — run against the native host (no VMs).

Two modes, selected by TERRIBLE_DEV_MODE:

  Dev mode (TERRIBLE_DEV_MODE=1):
    Starts the provider binary from the local venv in --dev (reattach) mode.
    Installs it into a filesystem mirror so 'tofu init' works offline.
    Used by 'make integration-test'.

  Registry mode (default, no env var):
    No provider process is started and no filesystem mirror is configured.
    'tofu init' pulls the provider directly from registry.terraform.io.
    Used by 'make registry-test' and the validate_registry CI stage.

Prerequisites:
  tofu or terraform on PATH
  uv sync (or pip install -e .) already run (dev mode only)
"""

import os
import re as _re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
PROVIDER_HOST = "registry.terraform.io"
PROVIDER_NS = "rhencke"
PROVIDER_TYPE = "terrible"
PROVIDER_VERSION = "0.10.0"

_DEV_MODE = bool(os.environ.get("TERRIBLE_DEV_MODE"))


def _find_provider_entrypoint() -> Path:
    # Allow explicit override — used by validate_binary CI stage.
    override = os.environ.get("TERRIBLE_PROVIDER_BIN")
    if override:
        return Path(override)
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


_REATTACH_RE = _re.compile(r"TF_REATTACH_PROVIDERS='(.+)'")


def _find_tf() -> str:
    for name in ("tofu", "terraform"):
        if shutil.which(name):
            return name
    raise RuntimeError("Neither 'tofu' nor 'terraform' found on PATH")


@pytest.fixture(scope="session")
def provider_process():
    """
    Dev mode: start the provider binary in --dev mode and capture TF_REATTACH_PROVIDERS.
    Registry mode: no-op, yields None.
    """
    if not _DEV_MODE:
        print("\n[setup] Registry mode — no local provider process", flush=True)
        yield None
        return

    entrypoint = _find_provider_entrypoint()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [str(entrypoint), "--dev"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # PyInstaller binary writes to stderr; merge into stdout
        text=True,
        env=env,
    )

    reattach = None
    _lines: list[str] = []

    def _scan():
        nonlocal reattach
        for line in proc.stdout:
            _lines.append(line.rstrip())
            m = _REATTACH_RE.search(line)
            if m:
                reattach = m.group(1)
                return  # found it; leave remaining output to be consumed naturally

    _t = threading.Thread(target=_scan, daemon=True)
    _t.start()
    _t.join(timeout=30)

    if not reattach:
        proc.kill()
        proc.wait()
        preview = "\n".join(_lines[-20:]) if _lines else "<no output>"
        raise RuntimeError(
            f"Provider did not emit TF_REATTACH_PROVIDERS within 30 s\n"
            f"Last {min(20, len(_lines))} lines of output:\n{preview}"
        )

    print(f"\n[setup] Provider PID={proc.pid} started in dev/reattach mode", flush=True)
    yield reattach
    proc.kill()
    proc.wait()


@pytest.fixture(scope="session")
def provider_install(tmp_path_factory, provider_process):
    """
    Dev mode: install the provider into a filesystem mirror; write a .terraformrc
    pointing at it so 'tofu init' works offline. Exposes TF_REATTACH_PROVIDERS.

    Registry mode: write a .terraformrc that forces direct registry access
    (suppresses any dev_overrides on the runner). No filesystem mirror.
    """
    tf_bin = _find_tf()
    print(f"[setup] Using terraform binary: {tf_bin}", flush=True)

    tfrc = tmp_path_factory.mktemp("tfrc") / ".terraformrc"

    if not _DEV_MODE:
        tfrc.write_text("provider_installation {\n  direct {}\n}\n")
        return {
            "tfrc": tfrc,
            "tf_bin": tf_bin,
            "reattach_json": None,
        }

    from tf.runner import install_provider

    entrypoint = _find_provider_entrypoint()
    plugin_dir = tmp_path_factory.mktemp("plugins")
    print(f"\n[setup] Installing provider from {entrypoint}", flush=True)
    install_provider(
        PROVIDER_HOST,
        PROVIDER_NS,
        PROVIDER_TYPE,
        PROVIDER_VERSION,
        plugin_dir,
        entrypoint,
    )

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

    return {
        "plugin_dir": plugin_dir,
        "tfrc": tfrc,
        "tf_bin": tf_bin,
        "reattach_json": provider_process,
    }


@pytest.fixture(scope="session")
def host_vars() -> list[str]:
    """
    Return extra -var arguments for tofu apply based on connection mode.

    Local mode (default): connection=local, host=127.0.0.1
    SSH mode (TERRIBLE_SSH_HOST set): connection=ssh + SSH credentials from env.
    """
    ssh_host = os.environ.get("TERRIBLE_SSH_HOST")
    if ssh_host:
        return [
            "-var",
            "connection=ssh",
            "-var",
            f"host={ssh_host}",
            "-var",
            f"ssh_port={os.environ.get('TERRIBLE_SSH_PORT', '22')}",
            "-var",
            f"ssh_user={os.environ.get('TERRIBLE_SSH_USER', '')}",
            "-var",
            f"ssh_key={os.environ.get('TERRIBLE_SSH_KEY', '')}",
        ]
    return [
        "-var",
        "connection=local",
        "-var",
        "host=127.0.0.1",
    ]
