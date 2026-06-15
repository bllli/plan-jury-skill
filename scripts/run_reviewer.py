#!/usr/bin/env python3
"""Run the configured OpenAI-compatible reviewer for the Plan Jury skill."""

from __future__ import annotations

import argparse
import sys

from reviewer_client import call_reviewer, exit_with_error, load_config


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the configured Plan Jury OpenAI-compatible reviewer.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify reviewer API configuration without making a network request.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Reviewer JSON config path. Defaults to PLAN_JURY_CONFIG or ~/.codex/plan-jury/reviewer.json.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Override request timeout in seconds.",
    )
    args = parser.parse_args()

    try:
        loaded = load_config(args.config)
        if args.timeout is not None:
            if args.timeout <= 0:
                raise ValueError("--timeout must be greater than 0")
            loaded.config["timeout"] = args.timeout

        if args.check:
            auth_mode = "none"
            if loaded.config.get("api_key_env"):
                auth_mode = f"env:{loaded.config['api_key_env']}"
            elif loaded.config.get("api_key"):
                auth_mode = "config:api_key"
            print(
                "Reviewer API configured "
                f"from {loaded.source}: base_url={loaded.config['base_url']} "
                f"endpoint={loaded.config['endpoint']} model={loaded.config['model']} "
                f"auth={auth_mode}"
            )
            return 0

        output = call_reviewer(sys.stdin.read(), loaded.config)
        print(output, end="" if output.endswith("\n") else "\n")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI should convert all failures to stderr.
        return exit_with_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
