# Plan Jury

Plan Jury：将技术方案生成可审计的 plan markdown，并通过 OpenAI 兼容模型进行最多 5 轮独立评审，达成共识或交由人类裁决。

`plan-jury` is a Codex skill that turns a technical approach into a detailed plan markdown, then asks an external OpenAI-compatible reviewer model to critique it for up to 5 rounds.

The goal is not to let one model rubber-stamp its own plan. Codex drafts and revises the plan; the reviewer model acts as an independent second opinion. If both sides converge, the review ends early. If they still disagree after 5 rounds, the final plan records the unresolved issues for human judgment.

## Features

- Generates a human-reviewable technical plan markdown.
- Calls an external OpenAI-compatible `/chat/completions` reviewer.
- Supports `base_url`, `model`, config-file API keys, local no-auth models, provider headers, and extra request body fields.
- Records metadata for every reviewer request, including provider URL, model, token counts, duration, status, and errors.
- Estimates review duration from plan size and prior local usage history before making a request.
- Runs a bounded review loop with verdicts: `APPROVED`, `MOSTLY_GOOD`, `NEEDS_REVISION`, `BLOCKED`.
- Stops early when consensus is reached.
- Escalates unresolved disagreements to a `Needs Human Decision` section after 5 rounds.
- Produces one clean final plan document without persisted review transcript details.
- Uses prompt-injection boundaries around the reviewed plan.
- Requires privacy classification and stop-lines before sending content to the reviewer.

## Repository Layout

```text
.
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   └── review-prompt-template.md
└── scripts/
    ├── configure_reviewer.py
    ├── reviewer_client.py
    └── run_reviewer.py
```

## Installation

Clone the repository into your Codex skills directory:

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/bllli/plan-jury-skill.git ~/.codex/skills/plan-jury
```

Or copy an existing checkout:

```bash
mkdir -p ~/.codex/skills
cp -R /path/to/plan-jury ~/.codex/skills/plan-jury
```

Validate the skill:

```bash
uv run --with pyyaml python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py ~/.codex/skills/plan-jury
```

## Reviewer Configuration

The reviewer must expose an OpenAI-compatible chat completions API.

OpenAI example:

```bash
python3 ~/.codex/skills/plan-jury/scripts/configure_reviewer.py \
  --base-url 'https://api.openai.com/v1' \
  --model 'gpt-4.1' \
  --api-key 'YOUR_API_KEY' \
  --test
```

Local no-auth compatible server example:

```bash
python3 ~/.codex/skills/plan-jury/scripts/configure_reviewer.py \
  --base-url 'http://localhost:1234/v1' \
  --model 'local-reviewer-model' \
  --no-api-key \
  --test
```

Verify configuration:

```bash
python3 ~/.codex/skills/plan-jury/scripts/run_reviewer.py --check
```

The default config file is:

```text
~/.codex/plan-jury/reviewer.json
```

Supported config fields:

- `base_url`: OpenAI-compatible base URL, usually ending in `/v1`
- `model`: reviewer model name
- `api_key`: API key stored in the config file
- `language`: language that reviewer responses, plan drafts, and the final plan markdown must use, default `中文`
- `endpoint`: endpoint path, default `/chat/completions`
- `temperature`: default `0.2`
- `max_tokens`: default `4096`
- `timeout`: default `1200` seconds
- `usage_log`: metadata-only usage log path, default `~/.codex/plan-jury/usage.jsonl`
- `extra_headers`: provider-specific HTTP headers
- `extra_body`: JSON object merged into the request body

Runtime environment overrides:

- `PLAN_JURY_CONFIG`
- `PLAN_JURY_BASE_URL`
- `PLAN_JURY_MODEL`
- `PLAN_JURY_ENDPOINT`
- `PLAN_JURY_TEMPERATURE`
- `PLAN_JURY_MAX_TOKENS`
- `PLAN_JURY_REVIEWER_TIMEOUT`
- `PLAN_JURY_USAGE_LOG`

## Usage Records and Duration Estimate

Every actual reviewer call appends one JSON object to the usage log. It records provider and request metadata only: base URL, request URL, model, configured language, timeout, estimated prompt tokens, provider token counts when returned, duration, status, and truncated error text for failures. It does not record API keys, raw prompts, or full reviewer responses.

The default timeout is 20 minutes. Increase `timeout` in the config, pass `--timeout`, or set `PLAN_JURY_REVIEWER_TIMEOUT` when a provider routinely needs longer.

Before calling the reviewer, estimate the rough wait time:

```bash
python3 ~/.codex/skills/plan-jury/scripts/run_reviewer.py --estimate < plan.md
```

For best accuracy, pipe the exact prompt that will be sent to the reviewer. If no history exists for the configured provider and model, the estimate falls back to a simple heuristic based on document size.

## Usage

Ask Codex to use the skill:

```text
Use $plan-jury to turn the current technical approach into a reviewed plan markdown.
```

Typical prompts:

```text
Use $plan-jury to create a reviewed implementation plan for this auth refactor.
```

```text
Use $plan-jury to write a plan markdown for the database migration and run reviewer rounds before finalizing it.
```

The skill will:

1. Gather repository and conversation context.
2. Draft a detailed plan markdown.
3. Classify privacy, stop-lines, assumptions, and evidence gaps.
4. Use the configured `language` for the draft, reviewer prompt requirements, and final plan.
5. Estimate the review duration from the current prompt and usage history.
6. Send the plan to the configured reviewer.
7. Apply accepted reviewer feedback.
8. Repeat until consensus or 5 rounds.
9. Produce one final conclusion plan for human review.

## Final Plan Contents

The final markdown is expected to include:

It must be written in the configured `language` from `~/.codex/plan-jury/reviewer.json`, defaulting to `中文`.

- Objective
- Background and current state
- Requirements and non-goals
- Assumptions and constraints
- Stop-lines / no-touch zones
- Privacy classification and redactions
- Evidence gathered and evidence gaps
- Proposed design
- Data model, API, interface, config, and migration changes
- Implementation phases
- Test and validation strategy
- Rollout and rollback plan
- Security, privacy, performance, compatibility, and observability notes
- Risks and mitigations
- Open questions
- Human review checklist

The final document intentionally excludes review rounds, reviewer verdicts, ratings, traceability tables, raw prompts, transcript summaries, and review log paths.

## Design Notes

This skill intentionally keeps the reviewer model behind an OpenAI-compatible API instead of depending on a specific CLI. That makes it usable with OpenAI, OpenRouter, vLLM, LiteLLM, LM Studio, Ollama-compatible proxies, or internal gateways that implement `/chat/completions`.

The review loop is bounded. A reviewer can block or request revision, but after 5 rounds the skill must stop and surface unresolved disagreements to a human.

## References

This project borrows process ideas from several public review and second-opinion workflows:

- [cathrynlavery/codex-skill](https://github.com/cathrynlavery/codex-skill): independent second opinion and automatic plan review pattern.
- [longranger2/claude-gpt-workflow](https://github.com/longranger2/claude-gpt-workflow): iterative plan review, review log convention, and status-driven refinement.
- [wan-huiyan/agent-review-panel](https://github.com/wan-huiyan/agent-review-panel): adversarial review framing, prompt-injection boundaries, review lenses, and evidence discipline.
- [wrsmith108/plan-review-skill](https://github.com/wrsmith108/plan-review-skill): multi-perspective plan review framing.
- [pimenov/codex-pro-review-bundle-skill](https://github.com/pimenov/codex-pro-review-bundle-skill): review bundle structure, stop-lines, privacy classification, and decision log ideas.
- [serbanghita/claude-code-plan-critique](https://github.com/serbanghita/claude-code-plan-critique): iterative critique of plan files against project context.
- [dementev-dev/adversarial-review](https://github.com/dementev-dev/adversarial-review): bounded adversarial review loop with a maximum of 5 rounds.

`plan-jury` differs by keeping the reviewer integration provider-neutral through `base_url` and `model` configuration for any OpenAI-compatible chat completions endpoint.

## License

Add a license before publishing if you want others to reuse this repository.
