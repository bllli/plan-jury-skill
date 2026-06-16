#!/usr/bin/env python3
"""Run the configured OpenAI-compatible reviewer for the Plan Jury skill."""

from __future__ import annotations

import argparse
import json
import sys

from reviewer_client import call_reviewer, estimate_duration, exit_with_error, load_config, usage_log_path


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
        "--estimate",
        action="store_true",
        help="Estimate request duration from stdin and local usage history without making a network request.",
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
            if loaded.config.get("api_key"):
                auth_mode = "config:api_key"
            print(
                "Reviewer API configured "
                f"from {loaded.source}: base_url={loaded.config['base_url']} "
                f"endpoint={loaded.config['endpoint']} model={loaded.config['model']} "
                f"language={loaded.config.get('language')} "
                f"timeout={loaded.config.get('timeout')} "
                f"usage_log={usage_log_path(loaded.config)} "
                f"auth={auth_mode}"
            )
            return 0

        prompt = sys.stdin.read()
        if args.estimate:
            print(json.dumps(estimate_duration(prompt, loaded.config), ensure_ascii=False, indent=2))
            return 0

        output = call_reviewer(prompt, loaded.config)
        print(output, end="" if output.endswith("\n") else "\n")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI should convert all failures to stderr.
        return exit_with_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
