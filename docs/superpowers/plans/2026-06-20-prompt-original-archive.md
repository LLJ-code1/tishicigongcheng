# Prompt Original Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract every identifiable LLM prompt framework from the local source collection into a browsable, lossless archive.

**Architecture:** A Python standard-library extractor reads workflow JSON, selected plugin ZIP configuration files, and standalone rule text. It writes exact prompt bodies into a deduplicated reading tree and a complete per-source tree, with a JSON manifest for traceability.

**Tech Stack:** Python 3 standard library, `unittest`, JSON, ZIP, AST, SHA-256.

---

### Task 1: Define extraction behavior

**Files:**
- Create: `scripts/tests/test_extract_prompt_frameworks.py`

- [ ] Write tests for workflow prompt detection, exact text preservation, deduplication, adult-content separation, and manifest source mapping.
- [ ] Run the test and verify it fails because the extractor module does not exist.

### Task 2: Implement the extractor

**Files:**
- Create: `scripts/extract_prompt_frameworks.py`

- [ ] Implement workflow JSON scanning with directive-text filtering.
- [ ] Implement explicit plugin prompt extraction from JSON, Python, and JavaScript sources inside ZIP files.
- [ ] Implement deterministic filenames, SHA-256 manifests, reading copies, and source copies.
- [ ] Run the unit tests and verify they pass.

### Task 3: Generate and verify the real archive

**Files:**
- Create: `提示词原文归档/README.md`
- Create: `提示词原文归档/manifest.json`
- Create: `提示词原文归档/去重阅读版/**/*.txt`
- Create: `提示词原文归档/按来源完整副本/**/*.txt`

- [ ] Run the extractor against `G:\新建文件夹`.
- [ ] Re-read every emitted text file and verify its SHA-256 against the manifest.
- [ ] Confirm counts for unique prompts, source occurrences, adult/mixed prompts, and source groups.
