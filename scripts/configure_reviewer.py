#!/usr/bin/env python3
"""Configure the OpenAI-compatible reviewer API for the Plan Jury skill."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from reviewer_client import (
    DEFAULT_CONFIG,
    DEFAULT_ENDPOINT,
    DEFAULT_LANGUAGE,
    call_reviewer,
    exit_with_error,
    validate_config,
    write_config,
)


TEST_PROMPT = """You are the external technical plan reviewer.

Return markdown with:
- Verdict: AGREE
- A one-sentence confirmation that the Plan Jury reviewer API configuration works.

This is a configuration smoke test.
"""


def parse_extra_header(raw_headers: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw in raw_headers:
        if ":" not in raw:
            raise ValueError(f"Invalid --extra-header value, expected 'Name: Value': {raw}")
        name, value = raw.split(":", 1)
        name = name.strip()
        value = value.strip()
        if not name or not value:
            raise ValueError(f"Invalid --extra-header value, expected 'Name: Value': {raw}")
        headers[name] = value
    return headers


def parse_extra_body(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("--extra-body-json must be a JSON object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write the Plan Jury OpenAI-compatible reviewer API config.",
    )
    parser.add_argument("--base-url", required=True, help="OpenAI-compatible base URL, usually ending in /v1.")
    parser.add_argument("--model", required=True, help="Reviewer model name.")
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key to store in the config file.",
    )
    parser.add_argument(
        "--no-api-key",
        action="store_true",
        help="Allow a local or unauthenticated OpenAI-compatible endpoint.",
    )
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"Completions endpoint path. Defaults to {DEFAULT_ENDPOINT}.",
    )
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        help=f"Language the reviewer must use for all review responses. Defaults to {DEFAULT_LANGUAGE}.",
    )
    parser.add_argument(
        "--extra-header",
        action="append",
        default=[],
        help="Additional HTTP header in 'Name: Value' form. May be repeated.",
    )
    parser.add_argument(
        "--extra-body-json",
        default=None,
        help="Additional JSON object merged into the chat completions request body.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"Config file path. Defaults to {DEFAULT_CONFIG}.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run a chat completions smoke test before writing the config.",
    )
    args = parser.parse_args()

    try:
        if sum(bool(v) for v in (args.api_key, args.no_api_key)) != 1:
            raise ValueError("Choose exactly one of --api-key or --no-api-key.")
        if args.max_tokens <= 0:
            raise ValueError("--max-tokens must be greater than 0.")
        if args.timeout <= 0:
            raise ValueError("--timeout must be greater than 0.")

        config: dict[str, object] = {
            "base_url": args.base_url.rstrip("/"),
            "model": args.model,
            "endpoint": args.endpoint,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "timeout": args.timeout,
        }
        config["language"] = args.language
        if args.api_key:
            config["api_key"] = args.api_key
        extra_headers = parse_extra_header(args.extra_header)
        if extra_headers:
            config["extra_headers"] = extra_headers
        extra_body = parse_extra_body(args.extra_body_json)
        if extra_body:
            config["extra_body"] = extra_body

        validate_config(config)
        if args.test:
            output = call_reviewer(TEST_PROMPT, config)
            print("Reviewer API smoke test output:")
            print(output, end="" if output.endswith("\n") else "\n")

        config_path = Path(args.config).expanduser()
        write_config(config, config_path)
        print(f"Reviewer API config written to {config_path}")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI should convert all failures to stderr.
        return exit_with_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
