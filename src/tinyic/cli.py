"""TinyIC command-line bootstrap for the M0 foundation milestone."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the minimal parser; product commands arrive in later milestones."""
    return argparse.ArgumentParser(
        prog="tinyic",
        description="TinyIC — an AI investment committee simulator.",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the TinyIC command-line bootstrap."""
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
