"""PyInstaller entry point — uses absolute import to avoid relative-import error."""
import sys

# Force line-buffered stdout so print() flushes on each newline when piped.
# PYTHONUNBUFFERED=1 is unreliable inside a PyInstaller binary.
sys.stdout.reconfigure(line_buffering=True)

from terrible_provider.cli import main

main()
