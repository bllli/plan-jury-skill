#!/usr/bin/env python3
"""OpenAI-compatible chat completions client for the Plan Jury skill."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib import error, request


ENV_CONFIG = "PLAN_JURY_CONFIG"
ENV_BASE_URL = "PLAN_JURY_BASE_URL"
ENV_MODEL = "PLAN_JURY_MODEL"
ENV_ENDPOINT = "PLAN_JURY_ENDPOINT"
ENV_TIMEOUT = "PLAN_JURY_REVIEWER_TIMEOUT"
ENV_TEMPERATURE = "PLAN_JURY_TEMPERATURE"
ENV_MAX_TOKENS = "PLAN_JURY_MAX_TOKENS"
ENV_USAGE_LOG = "PLAN_JURY_USAGE_LOG"

DEFAULT_CONFIG = Path.home() / ".codex" / "plan-jury" / "reviewer.json"
DEFAULT_USAGE_LOG = Path.home() / ".codex" / "plan-jury" / "usage.jsonl"
DEFAULT_ENDPOINT = "/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 1200
DEFAULT_LANGUAGE = "中文"
DEFAULT_SYSTEM_PROMPT = (
    "You are an external senior technical plan reviewer. Review plans for "
    "correctness, completeness, hidden assumptions, implementation risk, "
    "migration safety, tests, rollout, rollback, observability, security, "
    "performance, compatibility, and maintainability. Return markdown."
)


class ReviewerConfigError(Exception):
    """Raised when reviewer configuration is missing or invalid."""


class ReviewerRequestError(Exception):
    """Raised when the reviewer API request fails."""


@dataclass
class LoadedConfig:
    config: dict[str, Any]
    source: str


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ReviewerConfigError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReviewerConfigError(f"Reviewer config must be a JSON object: {path}")
    return data


def _optional_float(raw: str, name: str) -> float:
    try:
        return float(raw)
    except ValueError as exc:
        raise ReviewerConfigError(f"{name} must be a number.") from exc


def _optional_int(raw: str, name: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ReviewerConfigError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise ReviewerConfigError(f"{name} must be greater than 0.")
    return value


def default_config_path(path: str | None = None) -> Path:
    if path:
        return Path(path).expanduser()
    configured = os.environ.get(ENV_CONFIG)
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_CONFIG


def load_config(path: str | None = None) -> LoadedConfig:
    config_path = default_config_path(path)
    config = _read_json(config_path)
    source = str(config_path) if config_path.exists() else "environment"

    env_overrides: dict[str, Any] = {
        "base_url": os.environ.get(ENV_BASE_URL),
        "model": os.environ.get(ENV_MODEL),
        "endpoint": os.environ.get(ENV_ENDPOINT),
        "usage_log": os.environ.get(ENV_USAGE_LOG),
    }
    for key, value in env_overrides.items():
        if value:
            config[key] = value

    if os.environ.get(ENV_TIMEOUT):
        config["timeout"] = _optional_int(os.environ[ENV_TIMEOUT], ENV_TIMEOUT)
    if os.environ.get(ENV_TEMPERATURE):
        config["temperature"] = _optional_float(os.environ[ENV_TEMPERATURE], ENV_TEMPERATURE)
    if os.environ.get(ENV_MAX_TOKENS):
        config["max_tokens"] = _optional_int(os.environ[ENV_MAX_TOKENS], ENV_MAX_TOKENS)

    config.setdefault("endpoint", DEFAULT_ENDPOINT)
    config.setdefault("timeout", DEFAULT_TIMEOUT_SECONDS)
    config.setdefault("usage_log", str(DEFAULT_USAGE_LOG))
    config.setdefault("system_prompt", DEFAULT_SYSTEM_PROMPT)
    config.setdefault("temperature", 0.2)
    config.setdefault("language", DEFAULT_LANGUAGE)

    validate_config(config)
    return LoadedConfig(config=config, source=source)


def validate_config(config: dict[str, Any]) -> None:
    for required in ("base_url", "model"):
        value = config.get(required)
        if not isinstance(value, str) or not value.strip():
            raise ReviewerConfigError(f"Reviewer config is missing required field: {required}")

    if not str(config["base_url"]).startswith(("http://", "https://")):
        raise ReviewerConfigError("base_url must start with http:// or https://")

    endpoint = config.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip().startswith("/"):
        raise ReviewerConfigError("endpoint must be a path beginning with /")

    timeout = config.get("timeout")
    if not isinstance(timeout, int) or timeout <= 0:
        raise ReviewerConfigError("timeout must be a positive integer")

    usage_log = config.get("usage_log")
    if usage_log is not None and (not isinstance(usage_log, str) or not usage_log.strip()):
        raise ReviewerConfigError("usage_log must be a non-empty string")

    for optional_number in ("temperature",):
        value = config.get(optional_number)
        if value is not None and not isinstance(value, (int, float)):
            raise ReviewerConfigError(f"{optional_number} must be a number")

    max_tokens = config.get("max_tokens")
    if max_tokens is not None and (not isinstance(max_tokens, int) or max_tokens <= 0):
        raise ReviewerConfigError("max_tokens must be a positive integer")

    language = config.get("language")
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise ReviewerConfigError("language must be a non-empty string")

    for optional_object in ("extra_headers", "extra_body"):
        value = config.get(optional_object, {})
        if value is not None and not isinstance(value, dict):
            raise ReviewerConfigError(f"{optional_object} must be a JSON object")


def config_help() -> str:
    return f"""Plan Jury reviewer API is not configured.

Configure an OpenAI-compatible chat completions endpoint:
  python3 /Users/bllli/.codex/skills/plan-jury/scripts/configure_reviewer.py \\
    --base-url 'https://api.openai.com/v1' \\
    --model 'gpt-4.1' \\
    --api-key 'YOUR_API_KEY' \\
    --test

Configuration file:
  {DEFAULT_CONFIG}

Environment overrides:
  {ENV_CONFIG}, {ENV_BASE_URL}, {ENV_MODEL}, {ENV_ENDPOINT}, {ENV_USAGE_LOG},
  {ENV_TIMEOUT}, {ENV_TEMPERATURE}, {ENV_MAX_TOKENS}
"""


def resolve_api_key(config: dict[str, Any]) -> str | None:
    api_key = config.get("api_key")
    if isinstance(api_key, str) and api_key:
        return api_key
    return None


def completion_url(config: dict[str, Any]) -> str:
    base_url = str(config["base_url"]).rstrip("/")
    endpoint = str(config.get("endpoint") or DEFAULT_ENDPOINT)
    if base_url.endswith("/chat/completions"):
        return base_url
    return base_url + endpoint


def approximate_token_count(text: str) -> int:
    """Approximate tokens without provider-specific tokenizers."""
    if not text:
        return 0
    ascii_chars = 0
    non_ascii_chars = 0
    for char in text:
        if char.isspace():
            continue
        if ord(char) < 128:
            ascii_chars += 1
        else:
            non_ascii_chars += 1
    return max(1, int((ascii_chars / 4) + (non_ascii_chars / 1.6)))


def usage_log_path(config: dict[str, Any]) -> Path:
    configured = os.environ.get(ENV_USAGE_LOG) or config.get("usage_log") or DEFAULT_USAGE_LOG
    return Path(str(configured)).expanduser()


def append_usage_record(config: dict[str, Any], record: dict[str, Any]) -> None:
    path = usage_log_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def base_usage_record(
    *,
    config: dict[str, Any],
    prompt: str,
    request_url: str,
) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "base_url": config.get("base_url"),
        "endpoint": config.get("endpoint") or DEFAULT_ENDPOINT,
        "request_url": request_url,
        "model": config.get("model"),
        "language": config.get("language") or DEFAULT_LANGUAGE,
        "temperature": config.get("temperature"),
        "max_tokens": config.get("max_tokens"),
        "timeout_seconds": config.get("timeout"),
        "prompt_chars": len(prompt),
        "estimated_prompt_tokens": approximate_token_count(prompt),
    }


def add_usage_fields(record: dict[str, Any], data: dict[str, Any]) -> None:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return
    token_fields = {
        "prompt_tokens": "prompt_tokens",
        "completion_tokens": "completion_tokens",
        "total_tokens": "total_tokens",
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
    }
    for output_key, usage_key in token_fields.items():
        value = usage.get(usage_key)
        if isinstance(value, (int, float)):
            record[output_key] = value
    record["usage"] = usage


def load_usage_records(config: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
    path = usage_log_path(config)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def estimate_duration(prompt: str, config: dict[str, Any]) -> dict[str, Any]:
    estimated_tokens = approximate_token_count(prompt)
    matching = [
        item
        for item in load_usage_records(config)
        if item.get("status") == "success"
        and item.get("base_url") == config.get("base_url")
        and item.get("model") == config.get("model")
        and isinstance(item.get("duration_seconds"), (int, float))
    ]
    seconds_per_token: list[float] = []
    for item in matching:
        token_count = (
            item.get("total_tokens")
            or _sum_numeric(item.get("input_tokens"), item.get("output_tokens"))
            or _sum_numeric(item.get("prompt_tokens"), item.get("completion_tokens"))
            or item.get("estimated_prompt_tokens")
        )
        duration = item.get("duration_seconds")
        if isinstance(token_count, (int, float)) and token_count > 0 and isinstance(duration, (int, float)):
            seconds_per_token.append(float(duration) / float(token_count))

    if seconds_per_token:
        seconds_per_token.sort()
        median = seconds_per_token[len(seconds_per_token) // 2]
        estimated_seconds = max(10.0, median * max(estimated_tokens, 1))
        basis = "history"
    else:
        estimated_seconds = max(30.0, min(900.0, estimated_tokens * 0.08))
        basis = "heuristic"

    timeout_seconds = config.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    return {
        "base_url": config.get("base_url"),
        "endpoint": config.get("endpoint") or DEFAULT_ENDPOINT,
        "model": config.get("model"),
        "language": config.get("language") or DEFAULT_LANGUAGE,
        "prompt_chars": len(prompt),
        "estimated_prompt_tokens": estimated_tokens,
        "estimated_seconds": round(estimated_seconds, 1),
        "estimated_minutes": round(estimated_seconds / 60, 2),
        "timeout_seconds": timeout_seconds,
        "history_matches": len(seconds_per_token),
        "basis": basis,
        "usage_log": str(usage_log_path(config)),
    }


def _sum_numeric(*values: Any) -> float | None:
    total = 0.0
    seen = False
    for value in values:
        if isinstance(value, (int, float)):
            total += float(value)
            seen = True
    return total if seen else None


def call_reviewer(prompt: str, config: dict[str, Any]) -> str:
    if not prompt.strip():
        raise ReviewerConfigError("Reviewer prompt was empty.")

    system_prompt = config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
    language = config.get("language") or DEFAULT_LANGUAGE
    if isinstance(language, str) and language.strip():
        system_prompt = (
            f"{system_prompt}\n\n"
            f"You must write the entire review response in this language: {language.strip()}."
        )

    payload: dict[str, Any] = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": config.get("temperature", 0.2),
        "stream": False,
    }
    if config.get("max_tokens"):
        payload["max_tokens"] = config["max_tokens"]
    if config.get("extra_body"):
        payload.update(config["extra_body"])
        payload["stream"] = False

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    api_key = resolve_api_key(config)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if config.get("extra_headers"):
        headers.update({str(k): str(v) for k, v in config["extra_headers"].items()})

    body = json.dumps(payload).encode("utf-8")
    url = completion_url(config)
    req = request.Request(url, data=body, headers=headers, method="POST")
    started_at = time.monotonic()
    record = base_usage_record(
        config=config,
        prompt=prompt,
        request_url=url,
    )
    try:
        with request.urlopen(req, timeout=config["timeout"]) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        duration = time.monotonic() - started_at
        response_body = exc.read().decode("utf-8", errors="replace")
        record.update(
            {
                "status": "http_error",
                "http_status": exc.code,
                "duration_seconds": round(duration, 3),
                "error": response_body[:1000],
            }
        )
        append_usage_record(config, record)
        raise ReviewerRequestError(f"Reviewer API HTTP {exc.code}: {response_body}") from exc
    except error.URLError as exc:
        duration = time.monotonic() - started_at
        record.update(
            {
                "status": "request_error",
                "duration_seconds": round(duration, 3),
                "error": str(exc)[:1000],
            }
        )
        append_usage_record(config, record)
        raise ReviewerRequestError(f"Reviewer API request failed: {exc}") from exc

    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        duration = time.monotonic() - started_at
        record.update(
            {
                "status": "invalid_json",
                "duration_seconds": round(duration, 3),
                "response_chars": len(response_body),
                "error": response_body[:1000],
            }
        )
        append_usage_record(config, record)
        raise ReviewerRequestError(f"Reviewer API returned non-JSON response: {response_body}") from exc

    try:
        content = extract_content(data)
    except ReviewerRequestError as exc:
        duration = time.monotonic() - started_at
        record.update(
            {
                "status": "invalid_response",
                "duration_seconds": round(duration, 3),
                "response_chars": len(response_body),
                "error": str(exc)[:1000],
            }
        )
        add_usage_fields(record, data)
        append_usage_record(config, record)
        raise

    duration = time.monotonic() - started_at
    record.update(
        {
            "status": "success",
            "duration_seconds": round(duration, 3),
            "response_chars": len(content),
            "estimated_response_tokens": approximate_token_count(content),
        }
    )
    add_usage_fields(record, data)
    append_usage_record(config, record)
    return content


def extract_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ReviewerRequestError("Reviewer API response did not include choices.")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ReviewerRequestError("Reviewer API choice was not an object.")

    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        text = _content_to_text(content)
        if text:
            return text

    legacy_text = choice.get("text")
    if isinstance(legacy_text, str) and legacy_text.strip():
        return legacy_text

    raise ReviewerRequestError("Reviewer API response did not include message.content.")


def _content_to_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        joined = "".join(parts)
        return joined if joined.strip() else None
    return None


def write_config(config: dict[str, Any], config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    config_path.chmod(0o600)


def exit_with_error(exc: Exception) -> int:
    if isinstance(exc, ReviewerConfigError):
        print(str(exc), file=sys.stderr)
        if "missing required field" in str(exc) or "not configured" in str(exc):
            print(config_help(), file=sys.stderr)
        return 2
    print(str(exc), file=sys.stderr)
    return 1
