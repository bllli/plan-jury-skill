#!/usr/bin/env python3
"""OpenAI-compatible chat completions client for the Plan Jury skill."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib import error, request


ENV_CONFIG = "PLAN_JURY_CONFIG"
ENV_BASE_URL = "PLAN_JURY_BASE_URL"
ENV_MODEL = "PLAN_JURY_MODEL"
ENV_ENDPOINT = "PLAN_JURY_ENDPOINT"
ENV_TIMEOUT = "PLAN_JURY_REVIEWER_TIMEOUT"
ENV_TEMPERATURE = "PLAN_JURY_TEMPERATURE"
ENV_MAX_TOKENS = "PLAN_JURY_MAX_TOKENS"

DEFAULT_CONFIG = Path.home() / ".codex" / "plan-jury" / "reviewer.json"
DEFAULT_ENDPOINT = "/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 600
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
  {ENV_CONFIG}, {ENV_BASE_URL}, {ENV_MODEL}, {ENV_ENDPOINT}
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
    req = request.Request(completion_url(config), data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=config["timeout"]) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise ReviewerRequestError(f"Reviewer API HTTP {exc.code}: {response_body}") from exc
    except error.URLError as exc:
        raise ReviewerRequestError(f"Reviewer API request failed: {exc}") from exc

    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise ReviewerRequestError(f"Reviewer API returned non-JSON response: {response_body}") from exc

    return extract_content(data)


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
