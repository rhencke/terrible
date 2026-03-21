from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Install the local provider into a terraform plugin directory.

    Mirrors the behavior of `scripts/install_provider.py` but from the package
    so it can be exposed as a console script entry point.
    """
    import argparse

    argv = argv or sys.argv[1:]

    p = argparse.ArgumentParser(description="Install local Python Terraform provider")
    p.add_argument("--host", default="local", help="Provider host (default: local)")
    p.add_argument("--namespace", default="terrible", help="Provider namespace (default: terrible)")
    p.add_argument("--project", default="terrible", help="Provider project (default: terrible)")
    p.add_argument("--version", default="0.0.1", help="Provider version (default: 0.0.1)")
    p.add_argument(
        "--plugin-dir",
        default=str(Path.home() / ".terraform.d" / "plugins"),
        help="Terraform plugin directory to install into",
    )
    p.add_argument(
        "--provider-script",
        default=str(Path.cwd() / "bin" / "terraform-provider-terrible"),
        help="Path to the provider executable script",
    )

    args = p.parse_args(argv)

    plugin_dir = Path(args.plugin_dir).expanduser()
    provider_script = Path(args.provider_script)

    if not provider_script.exists():
        print(f"Provider script not found: {provider_script}", file=sys.stderr)
        return 2

    try:
        from tf.runner import install_provider
    except Exception as e:
        print("Failed to import tf.runner.install_provider:", e, file=sys.stderr)
        return 3

    plugin_dir.mkdir(parents=True, exist_ok=True)

    try:
        install_provider(
            args.host,
            args.namespace,
            args.project,
            args.version,
            plugin_dir,
            provider_script,
        )
    except Exception as e:
        print("install_provider failed:", e, file=sys.stderr)
        return 4

    print(f"Installed provider {args.host}/{args.namespace}/{args.project} at {plugin_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
