# Plan Jury

Plan Jury：将技术方案生成可审计的 plan markdown，并通过 OpenAI 兼容模型进行最多 5 轮独立评审，达成共识或交由人类裁决。

`plan-jury` is a Codex skill that turns a technical approach into a detailed plan markdown, then asks multiple external OpenAI-compatible reviewer models to critique it concurrently for up to 5 rounds.

The goal is not to let one model rubber-stamp its own plan. Codex drafts and revises the plan; the reviewer jury acts as independent second opinions. If a majority of configured reviewers approve the current plan, the review ends early. If no majority approves after 5 rounds, the final plan records unresolved decisions for human judgment.

## Features

- Generates a human-reviewable technical plan markdown.
- Calls multiple external OpenAI-compatible `/chat/completions` reviewers concurrently.
- Supports per-reviewer `base_url`, `model`, config-file API keys, local no-auth models, provider headers, and extra request body fields.
- Records metadata for every reviewer request, including provider URL, model, token counts, duration, status, and errors.
- Estimates review duration from plan size and prior local usage history before making a request.
- Runs a bounded review loop with verdicts: `APPROVED`, `MOSTLY_GOOD`, `NEEDS_REVISION`, `BLOCKED`.
- Stops early when a majority of configured reviewers returns `APPROVED` or `MOSTLY_GOOD`.
- Escalates unresolved disagreements to a `Needs Human Decision` section after 5 rounds.
- Produces one clean final plan document while keeping reviewer input/output artifacts separate from the final plan.
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

Each reviewer must expose an OpenAI-compatible chat completions API. This is a breaking configuration format: `reviewers` is now required, and top-level `base_url` / `model` fields are not supported.

Two-reviewer example:

```bash
python3 ~/.codex/skills/plan-jury/scripts/configure_reviewer.py \
  --reviewer 'name=siliconflow,base_url=https://api.siliconflow.cn/v1,model=deepseek-ai/DeepSeek-V4-Pro,api_key=YOUR_API_KEY' \
  --reviewer 'name=local,base_url=http://localhost:1234/v1,model=local-reviewer-model'
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

- `language`: language that reviewer responses, plan drafts, and the final plan markdown must use, default `中文`
- `timeout`: fixed at `300` seconds for every reviewer request
- `usage_log`: metadata-only usage log path, default `~/.codex/plan-jury/usage.jsonl`
- `review_dir`: per-review input, streaming output, and metadata directory, default `~/.codex/plan-jury/reviews`
- `reviewers`: non-empty array of reviewer objects

Each reviewer object supports:

- `name`: unique reviewer name
- `base_url`: OpenAI-compatible base URL, usually ending in `/v1`
- `model`: reviewer model name
- `api_key`: optional API key stored in the config file
- `endpoint`: endpoint path, default `/chat/completions`
- `temperature`: default `0.2`
- `max_tokens`: default `4096`
- `extra_headers`: provider-specific HTTP headers
- `extra_body`: JSON object merged into the request body

Runtime environment overrides:

- `PLAN_JURY_CONFIG`
- `PLAN_JURY_USAGE_LOG`
- `PLAN_JURY_REVIEW_DIR`

## Usage Records and Duration Estimate

Every review round gets one unique `review_id` in the form `YYYYMMDD-HHMM-description`. Pass a description explicitly when useful:

```bash
python3 ~/.codex/skills/plan-jury/scripts/run_reviewer.py \
  --description "auth migration round 1" < reviewer-prompt.md
```

The helper creates a directory at `review_dir/{review_id}/` and writes one set of files per configured reviewer:

- `{reviewer}.input.md`: complete reviewer input, including system and user messages
- `{reviewer}.output.md`: reviewer output, written incrementally while the model streams
- `{reviewer}.meta.json`: provider, model, token, duration, status, verdict, and file-path metadata
- `summary.json`: majority result across all configured reviewers

Every individual reviewer request also appends one JSON object to the usage log. It records provider and request metadata: base URL, request URL, model, configured language, timeout, estimated prompt tokens, provider token counts when returned, duration, status, verdict, `review_id`, artifact paths, and truncated error text for failures. It does not record API keys.

During a long reviewer call, inspect the output file reported on stderr:

```bash
tail -f ~/.codex/plan-jury/reviews/{review_id}/{reviewer}.output.md
stat ~/.codex/plan-jury/reviews/{review_id}/{reviewer}.output.md
cat ~/.codex/plan-jury/reviews/{review_id}/summary.json
```

Every reviewer request uses the same 5 minute timeout. This is fixed by the skill to keep jury rounds bounded and comparable.

Before calling the reviewer, estimate the rough wait time:

```bash
python3 ~/.codex/skills/plan-jury/scripts/run_reviewer.py --estimate < plan.md
```

For best accuracy, pipe the exact prompt that will be sent to the reviewer. If no history exists for a configured provider and model, the estimate falls back to a simple heuristic based on document size. Because requests run concurrently, the overall estimate is the slowest configured reviewer.

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
6. Send the plan concurrently to all configured reviewers.
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

The final document intentionally excludes review rounds, reviewer verdict tables, ratings, traceability tables, raw prompts, transcript summaries, artifact paths, and review log paths.

## Design Notes

This skill intentionally keeps reviewer models behind OpenAI-compatible APIs instead of depending on a specific CLI. That makes it usable with OpenAI, OpenRouter, vLLM, LiteLLM, LM Studio, Ollama-compatible proxies, or internal gateways that implement `/chat/completions`.

The review loop is bounded. Reviewers can block or request revision, but after 5 rounds the skill must stop and surface unresolved disagreements to a human. A round is accepted only when more than half of all configured reviewers return `APPROVED` or `MOSTLY_GOOD`.

## References

This project borrows process ideas from several public review and second-opinion workflows:

- [cathrynlavery/codex-skill](https://github.com/cathrynlavery/codex-skill): independent second opinion and automatic plan review pattern.
- [longranger2/claude-gpt-workflow](https://github.com/longranger2/claude-gpt-workflow): iterative plan review, review log convention, and status-driven refinement.
- [wan-huiyan/agent-review-panel](https://github.com/wan-huiyan/agent-review-panel): adversarial review framing, prompt-injection boundaries, review lenses, and evidence discipline.
- [wrsmith108/plan-review-skill](https://github.com/wrsmith108/plan-review-skill): multi-perspective plan review framing.
- [pimenov/codex-pro-review-bundle-skill](https://github.com/pimenov/codex-pro-review-bundle-skill): review bundle structure, stop-lines, privacy classification, and decision log ideas.
- [serbanghita/claude-code-plan-critique](https://github.com/serbanghita/claude-code-plan-critique): iterative critique of plan files against project context.
- [dementev-dev/adversarial-review](https://github.com/dementev-dev/adversarial-review): bounded adversarial review loop with a maximum of 5 rounds.

`plan-jury` differs by keeping reviewer integration provider-neutral through per-reviewer `base_url` and `model` configuration for any OpenAI-compatible chat completions endpoint.

## License

Add a license before publishing if you want others to reuse this repository.
