#!/usr/bin/env python3
"""Configure OpenAI-compatible reviewer jury APIs for Plan Jury."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from reviewer_client import (
    DEFAULT_CONFIG,
    DEFAULT_LANGUAGE,
    DEFAULT_REVIEW_DIR,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USAGE_LOG,
    exit_with_error,
    validate_config,
)


def parse_scalar(raw: str) -> object:
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def parse_reviewer(raw: str) -> dict[str, object]:
    reviewer: dict[str, object] = {}
    for item in raw.split(","):
        if "=" not in item:
            raise ValueError(f"Invalid --reviewer item, expected key=value: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(f"Invalid --reviewer item, expected key=value: {item}")
        if key == "extra_body_json":
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise ValueError("extra_body_json must be a JSON object")
            reviewer["extra_body"] = parsed
        elif key.startswith("header."):
            headers = reviewer.setdefault("extra_headers", {})
            if not isinstance(headers, dict):
                raise ValueError("extra_headers must be an object")
            headers[key.removeprefix("header.")] = value
        else:
            reviewer[key] = parse_scalar(value)
    return reviewer


def write_config(config: dict[str, object], config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    config_path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write Plan Jury reviewer jury config.",
    )
    parser.add_argument(
        "--reviewer",
        action="append",
        required=True,
        help=(
            "Reviewer definition as comma-separated key=value pairs. Required keys: "
            "name,base_url,model. Optional: api_key,endpoint,temperature,max_tokens,header.X,extra_body_json."
        ),
    )
    parser.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        help=f"Language reviewer responses, plan drafts, and final plans must use. Defaults to {DEFAULT_LANGUAGE}.",
    )
    parser.add_argument(
        "--usage-log",
        default=str(DEFAULT_USAGE_LOG),
        help=f"Reviewer usage log path. Defaults to {DEFAULT_USAGE_LOG}.",
    )
    parser.add_argument(
        "--review-dir",
        default=str(DEFAULT_REVIEW_DIR),
        help=f"Per-review artifact directory. Defaults to {DEFAULT_REVIEW_DIR}.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"Config file path. Defaults to {DEFAULT_CONFIG}.",
    )
    args = parser.parse_args()

    try:
        config: dict[str, object] = {
            "language": args.language,
            "timeout": DEFAULT_TIMEOUT_SECONDS,
            "usage_log": args.usage_log,
            "review_dir": args.review_dir,
            "reviewers": [parse_reviewer(raw) for raw in args.reviewer],
        }
        validate_config(config)
        config_path = Path(args.config).expanduser()
        write_config(config, config_path)
        print(f"Reviewer jury config written to {config_path}")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI should convert all failures to stderr.
        return exit_with_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
