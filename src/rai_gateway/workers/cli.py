"""CLI for RaiCentral background workers.

``rai-worker runtime-events`` (the default, also bare ``rai-worker --once``)
and ``rai-worker onboarding``.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "onboarding":
        from rai_gateway.workers.onboarding import main as onboarding_main

        return onboarding_main(args[1:])
    from rai_gateway.workers.runtime_events import main as runtime_main

    return runtime_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
