#!/usr/bin/env python3
"""Run the configured OpenAI-compatible reviewer jury for Plan Jury."""

from __future__ import annotations

import argparse
import json
import sys

from reviewer_client import (
    call_reviewers,
    estimate_duration,
    exit_with_error,
    infer_review_description,
    load_config,
    review_dir_path,
    usage_log_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the configured Plan Jury OpenAI-compatible reviewer jury.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify reviewer jury configuration without making a network request.",
    )
    parser.add_argument(
        "--estimate",
        action="store_true",
        help="Estimate parallel request duration from stdin and local usage history without making a network request.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Reviewer JSON config path. Defaults to PLAN_JURY_CONFIG or ~/.codex/plan-jury/reviewer.json.",
    )
    parser.add_argument(
        "--description",
        default=None,
        help="Short review description used in the unique review id. Defaults to the prompt title or first non-empty line.",
    )
    args = parser.parse_args()

    try:
        loaded = load_config(args.config)
        reviewers = loaded.config["reviewers"]

        if args.check:
            reviewer_summary = ", ".join(
                f"{reviewer['name']}:{reviewer['model']}@{reviewer['base_url']}" for reviewer in reviewers
            )
            print(
                "Reviewer jury configured "
                f"from {loaded.source}: language={loaded.config['language']} "
                f"timeout={loaded.config['timeout']} "
                f"reviewer_count={len(reviewers)} "
                f"majority_threshold={(len(reviewers) // 2) + 1} "
                f"usage_log={usage_log_path(loaded.config)} "
                f"review_dir={review_dir_path(loaded.config)} "
                f"reviewers=[{reviewer_summary}]"
            )
            return 0

        prompt = sys.stdin.read()
        if args.estimate:
            print(json.dumps(estimate_duration(prompt, loaded.config), ensure_ascii=False, indent=2))
            return 0

        description = args.description or infer_review_description(prompt)
        result = call_reviewers(prompt, loaded.config, review_description=description)
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI should convert all failures to stderr.
        return exit_with_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
