#!/usr/bin/env python3
"""OpenAI-compatible multi-reviewer client for the Plan Jury skill."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Iterator
from urllib import error, request


ENV_CONFIG = "PLAN_JURY_CONFIG"
ENV_USAGE_LOG = "PLAN_JURY_USAGE_LOG"
ENV_REVIEW_DIR = "PLAN_JURY_REVIEW_DIR"

DEFAULT_CONFIG = Path.home() / ".codex" / "plan-jury" / "reviewer.json"
DEFAULT_USAGE_LOG = Path.home() / ".codex" / "plan-jury" / "usage.jsonl"
DEFAULT_REVIEW_DIR = Path.home() / ".codex" / "plan-jury" / "reviews"
DEFAULT_ENDPOINT = "/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_LANGUAGE = "中文"
DEFAULT_SYSTEM_PROMPT = (
    "You are an external senior technical plan reviewer. Review plans for "
    "correctness, completeness, hidden assumptions, implementation risk, "
    "migration safety, tests, rollout, rollback, observability, security, "
    "performance, compatibility, and maintainability. Return markdown."
)
APPROVING_VERDICTS = {"APPROVED", "MOSTLY_GOOD"}
REVISION_VERDICTS = {"NEEDS_REVISION", "BLOCKED"}


class ReviewerConfigError(Exception):
    """Raised when reviewer configuration is missing or invalid."""


class ReviewerRequestError(Exception):
    """Raised when one or more reviewer API requests fail."""


@dataclass
class LoadedConfig:
    config: dict[str, Any]
    source: str


@dataclass
class ReviewRun:
    review_id: str
    description: str
    directory: Path


@dataclass
class ReviewerArtifacts:
    name: str
    input_path: Path
    output_path: Path
    meta_path: Path


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

    if os.environ.get(ENV_USAGE_LOG):
        config["usage_log"] = os.environ[ENV_USAGE_LOG]
    if os.environ.get(ENV_REVIEW_DIR):
        config["review_dir"] = os.environ[ENV_REVIEW_DIR]

    config.setdefault("language", DEFAULT_LANGUAGE)
    config.setdefault("timeout", DEFAULT_TIMEOUT_SECONDS)
    config.setdefault("usage_log", str(DEFAULT_USAGE_LOG))
    config.setdefault("review_dir", str(DEFAULT_REVIEW_DIR))
    config.setdefault("system_prompt", DEFAULT_SYSTEM_PROMPT)

    validate_config(config)
    return LoadedConfig(config=config, source=source)


def validate_config(config: dict[str, Any]) -> None:
    language = config.get("language")
    if not isinstance(language, str) or not language.strip():
        raise ReviewerConfigError("language must be a non-empty string")

    timeout = config.get("timeout")
    if timeout != DEFAULT_TIMEOUT_SECONDS:
        raise ReviewerConfigError("timeout must be 300 seconds for multi-reviewer Plan Jury")

    for path_field in ("usage_log", "review_dir"):
        value = config.get(path_field)
        if not isinstance(value, str) or not value.strip():
            raise ReviewerConfigError(f"{path_field} must be a non-empty string")

    reviewers = config.get("reviewers")
    if not isinstance(reviewers, list) or not reviewers:
        raise ReviewerConfigError("reviewers must be a non-empty array")

    names: set[str] = set()
    for index, reviewer in enumerate(reviewers, start=1):
        if not isinstance(reviewer, dict):
            raise ReviewerConfigError(f"reviewers[{index}] must be an object")
        name = reviewer.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ReviewerConfigError(f"reviewers[{index}].name must be a non-empty string")
        safe_name = slugify(name)
        if safe_name in names:
            raise ReviewerConfigError(f"duplicate reviewer name: {name}")
        names.add(safe_name)
        for required in ("base_url", "model"):
            value = reviewer.get(required)
            if not isinstance(value, str) or not value.strip():
                raise ReviewerConfigError(f"reviewer {name} is missing required field: {required}")
        if not str(reviewer["base_url"]).startswith(("http://", "https://")):
            raise ReviewerConfigError(f"reviewer {name} base_url must start with http:// or https://")
        endpoint = reviewer.get("endpoint", DEFAULT_ENDPOINT)
        if not isinstance(endpoint, str) or not endpoint.startswith("/"):
            raise ReviewerConfigError(f"reviewer {name} endpoint must begin with /")
        api_key = reviewer.get("api_key")
        if api_key is not None and (not isinstance(api_key, str) or not api_key):
            raise ReviewerConfigError(f"reviewer {name} api_key must be a non-empty string")
        temperature = reviewer.get("temperature", 0.2)
        if not isinstance(temperature, (int, float)):
            raise ReviewerConfigError(f"reviewer {name} temperature must be a number")
        max_tokens = reviewer.get("max_tokens", 4096)
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ReviewerConfigError(f"reviewer {name} max_tokens must be a positive integer")
        for optional_object in ("extra_headers", "extra_body"):
            value = reviewer.get(optional_object, {})
            if value is not None and not isinstance(value, dict):
                raise ReviewerConfigError(f"reviewer {name} {optional_object} must be a JSON object")


def config_help() -> str:
    return f"""Plan Jury reviewer APIs are not configured.

Configure one or more OpenAI-compatible chat completions endpoints:
  python3 /Users/bllli/.codex/skills/plan-jury/scripts/configure_reviewer.py \\
    --reviewer name=siliconflow,base_url=https://api.siliconflow.cn/v1,model=deepseek-ai/DeepSeek-V4-Pro,api_key=YOUR_API_KEY

Configuration file:
  {DEFAULT_CONFIG}

Environment overrides:
  {ENV_CONFIG}, {ENV_USAGE_LOG}, {ENV_REVIEW_DIR}
"""


def review_dir_path(config: dict[str, Any]) -> Path:
    return Path(str(os.environ.get(ENV_REVIEW_DIR) or config["review_dir"])).expanduser()


def usage_log_path(config: dict[str, Any]) -> Path:
    return Path(str(os.environ.get(ENV_USAGE_LOG) or config["usage_log"])).expanduser()


def approximate_token_count(text: str) -> int:
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


def slugify(raw: str | None) -> str:
    value = (raw or "").strip() or "review"
    output: list[str] = []
    previous_dash = False
    for char in value:
        if char.isalnum() or ord(char) > 127:
            output.append(char)
            previous_dash = False
        elif not previous_dash:
            output.append("-")
            previous_dash = True
    return ("".join(output).strip("-") or "review")[:80]


def infer_review_description(prompt: str) -> str:
    for line in prompt.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped and not stripped.startswith(("═", "---")):
            return stripped[:80]
    return "review"


def create_review_run(config: dict[str, Any], description: str | None) -> ReviewRun:
    safe_description = slugify(description)
    base_dir = review_dir_path(config)
    base_dir.mkdir(parents=True, exist_ok=True)
    try:
        base_dir.chmod(0o700)
    except OSError:
        pass

    timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    base_review_id = f"{timestamp}-{safe_description}"
    review_id = base_review_id
    suffix = 2
    while (base_dir / review_id).exists():
        review_id = f"{base_review_id}-{suffix}"
        suffix += 1

    directory = base_dir / review_id
    directory.mkdir()
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    return ReviewRun(review_id=review_id, description=safe_description, directory=directory)


def create_reviewer_artifacts(run: ReviewRun, reviewer_name: str) -> ReviewerArtifacts:
    safe_name = slugify(reviewer_name)
    return ReviewerArtifacts(
        name=safe_name,
        input_path=run.directory / f"{safe_name}.input.md",
        output_path=run.directory / f"{safe_name}.output.md",
        meta_path=run.directory / f"{safe_name}.meta.json",
    )


def completion_url(reviewer: dict[str, Any]) -> str:
    base_url = str(reviewer["base_url"]).rstrip("/")
    endpoint = str(reviewer.get("endpoint") or DEFAULT_ENDPOINT)
    if base_url.endswith("/chat/completions"):
        return base_url
    return base_url + endpoint


def resolve_api_key(reviewer: dict[str, Any]) -> str | None:
    api_key = reviewer.get("api_key")
    if isinstance(api_key, str) and api_key:
        return api_key
    return None


def format_reviewer_input(system_prompt: str, user_prompt: str) -> str:
    return (
        "# Plan Jury Reviewer Input\n\n"
        "## System Message\n\n"
        f"{system_prompt}\n\n"
        "## User Message\n\n"
        f"{user_prompt}\n"
    )


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def read_json_object(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(fallback)
    return data if isinstance(data, dict) else dict(fallback)


def update_meta(path: Path, updates: dict[str, Any]) -> None:
    data = read_json_object(path, {})
    data.update(updates)
    write_json(path, data)


def append_usage_record(config: dict[str, Any], record: dict[str, Any]) -> None:
    path = usage_log_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def iter_sse_events(response: Any) -> Iterator[dict[str, Any]]:
    data_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            if not data_lines:
                continue
            data = "\n".join(data_lines)
            data_lines = []
            if data == "[DONE]":
                return
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                yield parsed
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())


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


def stream_content_delta(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    if not isinstance(choice, dict):
        return ""
    delta = choice.get("delta")
    if isinstance(delta, dict):
        text = _content_to_text(delta.get("content"))
        if text:
            return text
    message = choice.get("message")
    if isinstance(message, dict):
        text = _content_to_text(message.get("content"))
        if text:
            return text
    legacy_text = choice.get("text")
    return legacy_text if isinstance(legacy_text, str) else ""


def add_usage_fields(record: dict[str, Any], data: dict[str, Any]) -> None:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            record[key] = value
    record["usage"] = usage


def extract_verdict(content: str) -> str:
    match = re.search(r"(?im)^\s*(?:[-*]\s*)?(?:verdict|结论|评审结论)\s*[:：]\s*`?([A-Z_]+)`?", content)
    if match:
        verdict = match.group(1).upper()
        if verdict in APPROVING_VERDICTS | REVISION_VERDICTS:
            return verdict
    for verdict in ("APPROVED", "MOSTLY_GOOD", "NEEDS_REVISION", "BLOCKED"):
        if re.search(rf"\b{verdict}\b", content):
            return verdict
    return "UNKNOWN"


def reviewer_base_record(
    *,
    config: dict[str, Any],
    run: ReviewRun,
    reviewer: dict[str, Any],
    artifacts: ReviewerArtifacts,
    request_url: str,
    prompt: str,
    input_text: str,
) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "review_id": run.review_id,
        "review_description": run.description,
        "reviewer": artifacts.name,
        "base_url": reviewer.get("base_url"),
        "endpoint": reviewer.get("endpoint") or DEFAULT_ENDPOINT,
        "request_url": request_url,
        "model": reviewer.get("model"),
        "language": config.get("language") or DEFAULT_LANGUAGE,
        "temperature": reviewer.get("temperature", 0.2),
        "max_tokens": reviewer.get("max_tokens", 4096),
        "timeout_seconds": config["timeout"],
        "prompt_chars": len(prompt),
        "estimated_prompt_tokens": approximate_token_count(prompt),
        "input_chars": len(input_text),
        "estimated_input_tokens": approximate_token_count(input_text),
        "input_path": str(artifacts.input_path),
        "output_path": str(artifacts.output_path),
        "meta_path": str(artifacts.meta_path),
    }


def call_one_reviewer(
    *,
    config: dict[str, Any],
    run: ReviewRun,
    reviewer: dict[str, Any],
    prompt: str,
    output_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    name = str(reviewer["name"])
    artifacts = create_reviewer_artifacts(run, name)
    system_prompt = str(config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT)
    language = str(config.get("language") or DEFAULT_LANGUAGE).strip()
    system_prompt = f"{system_prompt}\n\nYou must write the entire review response in this language: {language}."
    payload: dict[str, Any] = {
        "model": reviewer["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": reviewer.get("temperature", 0.2),
        "max_tokens": reviewer.get("max_tokens", 4096),
        "stream": True,
    }
    if reviewer.get("extra_body"):
        payload.update(reviewer["extra_body"])
        payload["stream"] = True
    payload.setdefault("stream_options", {"include_usage": True})

    input_text = format_reviewer_input(system_prompt, prompt)
    artifacts.input_path.write_text(input_text, encoding="utf-8")
    artifacts.output_path.write_text("", encoding="utf-8")
    for path in (artifacts.input_path, artifacts.output_path):
        try:
            path.chmod(0o600)
        except OSError:
            pass

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = resolve_api_key(reviewer)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if reviewer.get("extra_headers"):
        headers.update({str(k): str(v) for k, v in reviewer["extra_headers"].items()})

    request_url = completion_url(reviewer)
    record = reviewer_base_record(
        config=config,
        run=run,
        reviewer=reviewer,
        artifacts=artifacts,
        request_url=request_url,
        prompt=prompt,
        input_text=input_text,
    )
    write_json(artifacts.meta_path, {**record, "status": "started"})
    print(
        f"Plan Jury review_id={run.review_id} reviewer={artifacts.name} "
        f"input={artifacts.input_path} output={artifacts.output_path} meta={artifacts.meta_path}",
        file=sys.stderr,
        flush=True,
    )

    started_at = time.monotonic()
    try:
        req = request.Request(
            request_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        content_parts: list[str] = []
        last_data: dict[str, Any] = {}
        with request.urlopen(req, timeout=config["timeout"]) as response:
            with artifacts.output_path.open("a", encoding="utf-8") as output_file:
                for data in iter_sse_events(response):
                    if time.monotonic() - started_at > config["timeout"]:
                        raise TimeoutError("Reviewer API request exceeded the 300 second timeout.")
                    last_data = data
                    delta = stream_content_delta(data)
                    if not delta:
                        continue
                    content_parts.append(delta)
                    output_file.write(delta)
                    output_file.flush()
                    if output_callback:
                        output_callback(delta)
        content = "".join(content_parts)
        duration = time.monotonic() - started_at
        if not content.strip():
            raise ReviewerRequestError("Reviewer API stream ended without content.")

        verdict = extract_verdict(content)
        record.update(
            {
                "status": "success",
                "duration_seconds": round(duration, 3),
                "response_chars": len(content),
                "estimated_response_tokens": approximate_token_count(content),
                "verdict": verdict,
                "approves_plan": verdict in APPROVING_VERDICTS,
            }
        )
        add_usage_fields(record, last_data)
        update_meta(artifacts.meta_path, record)
        append_usage_record(config, record)
        return {**record, "content": content}
    except error.HTTPError as exc:
        duration = time.monotonic() - started_at
        response_body = exc.read().decode("utf-8", errors="replace")
        record.update(
            {
                "status": "http_error",
                "http_status": exc.code,
                "duration_seconds": round(duration, 3),
                "error": response_body[:1000],
                "verdict": "ERROR",
                "approves_plan": False,
            }
        )
    except Exception as exc:
        duration = time.monotonic() - started_at
        record.update(
            {
                "status": "request_error",
                "duration_seconds": round(duration, 3),
                "error": str(exc)[:1000],
                "verdict": "ERROR",
                "approves_plan": False,
            }
        )

    update_meta(artifacts.meta_path, record)
    append_usage_record(config, record)
    return {**record, "content": ""}


def majority_threshold(total: int) -> int:
    return (total // 2) + 1


def summarize_multi_review(config: dict[str, Any], run: ReviewRun, results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(config["reviewers"])
    approvals = sum(1 for result in results if result.get("approves_plan") is True)
    successes = sum(1 for result in results if result.get("status") == "success")
    threshold = majority_threshold(total)
    majority_approved = approvals >= threshold
    summary = {
        "review_id": run.review_id,
        "description": run.description,
        "status": "majority_approved" if majority_approved else "needs_revision",
        "majority_approved": majority_approved,
        "approval_count": approvals,
        "success_count": successes,
        "reviewer_count": total,
        "majority_threshold": threshold,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "review_dir": str(run.directory),
        "results": [
            {
                "reviewer": result.get("reviewer"),
                "status": result.get("status"),
                "verdict": result.get("verdict"),
                "approves_plan": result.get("approves_plan"),
                "duration_seconds": result.get("duration_seconds"),
                "model": result.get("model"),
                "base_url": result.get("base_url"),
                "input_path": result.get("input_path"),
                "output_path": result.get("output_path"),
                "meta_path": result.get("meta_path"),
                "error": result.get("error"),
            }
            for result in results
        ],
    }
    write_json(run.directory / "summary.json", summary)
    return summary


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
    estimates: list[dict[str, Any]] = []
    for reviewer in config["reviewers"]:
        name = slugify(str(reviewer["name"]))
        matching = [
            item
            for item in load_usage_records(config)
            if item.get("status") == "success"
            and item.get("reviewer") == name
            and item.get("base_url") == reviewer.get("base_url")
            and item.get("model") == reviewer.get("model")
            and isinstance(item.get("duration_seconds"), (int, float))
        ]
        rates: list[float] = []
        for item in matching:
            token_count = (
                item.get("total_tokens")
                or _sum_numeric(item.get("input_tokens"), item.get("output_tokens"))
                or _sum_numeric(item.get("prompt_tokens"), item.get("completion_tokens"))
                or item.get("estimated_input_tokens")
                or item.get("estimated_prompt_tokens")
            )
            duration = item.get("duration_seconds")
            if isinstance(token_count, (int, float)) and token_count > 0 and isinstance(duration, (int, float)):
                rates.append(float(duration) / float(token_count))
        if rates:
            rates.sort()
            estimate = max(10.0, rates[len(rates) // 2] * max(estimated_tokens, 1))
            basis = "history"
        else:
            estimate = max(30.0, min(300.0, estimated_tokens * 0.08))
            basis = "heuristic"
        estimates.append(
            {
                "reviewer": name,
                "base_url": reviewer.get("base_url"),
                "model": reviewer.get("model"),
                "estimated_seconds": round(estimate, 1),
                "history_matches": len(rates),
                "basis": basis,
            }
        )
    return {
        "prompt_chars": len(prompt),
        "estimated_prompt_tokens": estimated_tokens,
        "timeout_seconds": config["timeout"],
        "parallel_estimated_seconds": round(max(item["estimated_seconds"] for item in estimates), 1),
        "reviewer_count": len(config["reviewers"]),
        "majority_threshold": majority_threshold(len(config["reviewers"])),
        "review_dir": str(review_dir_path(config)),
        "usage_log": str(usage_log_path(config)),
        "reviewers": estimates,
    }


def _sum_numeric(*values: Any) -> float | None:
    total = 0.0
    seen = False
    for value in values:
        if isinstance(value, (int, float)):
            total += float(value)
            seen = True
    return total if seen else None


def call_reviewers(
    prompt: str,
    config: dict[str, Any],
    *,
    review_description: str | None = None,
    output_callback: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    if not prompt.strip():
        raise ReviewerConfigError("Reviewer prompt was empty.")

    run = create_review_run(config, review_description or infer_review_description(prompt))
    print(f"Plan Jury review_id={run.review_id} dir={run.directory}", file=sys.stderr, flush=True)

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(config["reviewers"])) as executor:
        futures = []
        for reviewer in config["reviewers"]:
            name = slugify(str(reviewer["name"]))
            callback = None
            if output_callback:
                callback = lambda chunk, reviewer_name=name: output_callback(reviewer_name, chunk)
            futures.append(
                executor.submit(
                    call_one_reviewer,
                    config=config,
                    run=run,
                    reviewer=reviewer,
                    prompt=prompt,
                    output_callback=callback,
                )
            )
        for future in as_completed(futures):
            results.append(future.result())

    summary = summarize_multi_review(config, run, results)
    return {"summary": summary, "results": results}


def exit_with_error(exc: Exception) -> int:
    if isinstance(exc, ReviewerConfigError):
        print(str(exc), file=sys.stderr)
        if "missing" in str(exc) or "reviewers" in str(exc):
            print(config_help(), file=sys.stderr)
        return 2
    print(str(exc), file=sys.stderr)
    return 1
