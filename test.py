"""Convenience shim for demos.

Allows running:
  python test.py --username <name> --person <tmdb_id>

This delegates to etcs/test.py.
"""

from __future__ import annotations

import os
import runpy


def main() -> None:
	path = os.path.join(os.path.dirname(__file__), "etcs", "test.py")
	runpy.run_path(path, run_name="__main__")


if __name__ == "__main__":
	main()
