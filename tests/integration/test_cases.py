"""
Integration tests against the native host — one test per cases/ subdirectory
and one test per example that ships a verify.sh.

Pattern per case:
  Arrange — cleanup.sh wipes any prior state on the host
  Act     — terraform apply
  Assert  — verify.sh confirms the action took effect on the host
           — terraform plan asserts no drift (idempotency)
  Cleanup — terraform destroy + cleanup.sh

Skip unless TERRIBLE_INTEGRATION=1 is set.

Usage:
  TERRIBLE_INTEGRATION=1 uv run pytest tests/integration/ -v
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("TERRIBLE_INTEGRATION"),
    reason="Set TERRIBLE_INTEGRATION=1 to run integration tests",
)

_REPO_ROOT = Path(__file__).parent.parent.parent
CASES_DIR = Path(__file__).parent / "cases"
EXAMPLES_DIR = _REPO_ROOT / "examples"


def _collect_cases() -> list[Path]:
    cases = sorted(
        d for d in CASES_DIR.iterdir()
        if d.is_dir() and (d / "main.tf").exists()
    )
    # Examples that ship a verify.sh are integration-testable
    examples = sorted(
        d for d in EXAMPLES_DIR.iterdir()
        if d.is_dir() and (d / "main.tf").exists() and (d / "verify.sh").exists()
    )
    return cases + examples


_cases = _collect_cases()


def _run_script(case_dir: Path, name: str, *, check: bool = False) -> int:
    script = case_dir / name
    if not script.exists():
        return 0
    result = subprocess.run(["bash", str(script)], check=False)
    if check and result.returncode != 0:
        raise AssertionError(f"{name} failed with exit code {result.returncode}")
    return result.returncode


def _tf(label: str, args: list[str], *, ws: Path, env: dict, check: bool = True):
    print(f"  → {label}", flush=True)
    return subprocess.run(args, cwd=str(ws), env=env, check=check)


@pytest.mark.parametrize("case_dir", _cases, ids=[d.name for d in _cases])
def test_case(case_dir, tmp_path, provider_install):
    tf_bin = provider_install["tf_bin"]
    tf_env = {
        **os.environ,
        "TF_CLI_CONFIG_FILE": str(provider_install["tfrc"]),
        "TF_REATTACH_PROVIDERS": provider_install["reattach_json"],
    }
    # init doesn't call ConfigureProvider; strip reattach to avoid edge cases
    tf_env_init = {k: v for k, v in tf_env.items() if k != "TF_REATTACH_PROVIDERS"}
    state_file = str(tmp_path / "terrible.json")
    name = case_dir.name

    print(f"\n[{name}]", flush=True)

    # Copy case to isolated workspace so each test has independent TF state
    ws = tmp_path / name
    shutil.copytree(str(case_dir), str(ws))

    # --- Arrange ---
    print(f"[{name}] cleanup (pre)", flush=True)
    _run_script(case_dir, "cleanup.sh")

    _tf("init", [tf_bin, "init", "-no-color"], ws=ws, env=tf_env_init)

    try:
        # --- Act ---
        _tf("apply", [tf_bin, "apply", "-auto-approve", "-no-color",
                      "-var", f"state_file={state_file}"], ws=ws, env=tf_env)

        # --- Assert: side effects landed on the host ---
        print(f"[{name}] verify.sh", flush=True)
        _run_script(case_dir, "verify.sh", check=True)

        # --- Assert: terraform outputs match expected values ---
        expected_file = case_dir / "expected_outputs.json"
        if expected_file.exists():
            expected = json.loads(expected_file.read_text())
            raw = subprocess.run(
                [tf_bin, "output", "-json", "-no-color"],
                cwd=str(ws), env=tf_env, check=True, capture_output=True, text=True,
            )
            actual = {k: v["value"] for k, v in json.loads(raw.stdout).items()}
            for key, want in expected.items():
                assert actual.get(key) == want, (
                    f"[{name}] output {key!r}: expected {want!r}, got {actual.get(key)!r}"
                )

        # --- Assert: no drift on a second plan ---
        result = _tf("plan (idempotency)", [tf_bin, "plan", "-detailed-exitcode", "-no-color",
                                            "-var", f"state_file={state_file}"],
                     ws=ws, env=tf_env, check=False)
        assert result.returncode == 0, (
            f"[{name}] Plan after apply should show no changes "
            f"(got exit {result.returncode})"
        )

    finally:
        _tf("destroy", [tf_bin, "destroy", "-auto-approve", "-no-color",
                        "-var", f"state_file={state_file}"], ws=ws, env=tf_env, check=False)
        print(f"[{name}] cleanup (post)", flush=True)
        _run_script(case_dir, "cleanup.sh")
