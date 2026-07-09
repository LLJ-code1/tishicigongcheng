# Model-Driven Random Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace static browser random prompts with a two-stage model-generated Anima prompt using the selected local or external provider.

**Architecture:** Add `generate_random_text_prompt()` to the prompt engine for blueprint and compilation calls, expose it through `/api/text/random`, and connect the existing full-random button to asynchronous generation progress and the existing structured editor.

**Tech Stack:** Python OpenAI-compatible transport, existing Anima prompt modules and normalizer, standard-library HTTP server, vanilla JavaScript reducer, Python unittest, Node test runner.

---

### Task 1: Define Contracts With Failing Tests

- [ ] Add prompt-engine tests proving blueprint and compilation calls use the selected provider and creative density.
- [ ] Add server tests proving an empty-input random request is accepted and saved provider settings are used.
- [ ] Add Node tests proving the full-random button calls `/api/text/random` and static `randomOutput()` is no longer used.
- [ ] Run focused tests and confirm they fail for missing behavior.

### Task 2: Implement Two-Stage Random Generation

- [ ] Add a random blueprint system prompt and strict JSON schema.
- [ ] Compile the blueprint with the existing Anima modules and bilingual output schema.
- [ ] Normalize and validate with creative-mode density requirements.
- [ ] Retry malformed or thin final output once.
- [ ] Run focused prompt-engine tests.

### Task 3: Add HTTP and Frontend Integration

- [ ] Add `process_text_random_request()` and `POST /api/text/random`.
- [ ] Add random loading, success, and failure reducer actions.
- [ ] Replace the synchronous button handler with an API call using the selected provider.
- [ ] Reuse the existing generation progress panel and structured result application.
- [ ] Run server and Node tests.

### Task 4: Real External API and Full Verification

- [ ] Call `/api/text/random` using the saved DeepSeek configuration.
- [ ] Record output lengths, block count, provider, and elapsed time without exposing the API key.
- [ ] Run complete Python and Node suites.
- [ ] Update `docs/development-log.md`.
