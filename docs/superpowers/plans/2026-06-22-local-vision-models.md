# Local Vision Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independently runnable WD14, JoyCaption, and Qwen3-VL image analyzers to Prompt Studio, then prove each analyzer and the combined pipeline with real uploaded images.

**Architecture:** Keep the web server lightweight and launch every visual model through a JSON-speaking worker subprocess. `vision_engine.py` owns installation status, serial scheduling, failure isolation, and LLM merging; model-specific loading remains in focused worker modules so one failed or incompatible dependency cannot take down the web server.

**Tech Stack:** Python 3, `subprocess`, Pillow, NumPy, ONNX Runtime, Transformers/PyTorch, llama-cpp-python multimodal, vanilla JavaScript, Python `unittest`, Node test runner.

---

## File Map

- Create `prototype/vision_worker_common.py`: shared worker CLI, JSON output, image validation, and model error normalization.
- Create `prototype/vision_worker_wd14.py`: CPU ONNX inference and Danbooru tag formatting.
- Create `prototype/vision_worker_joycaption.py`: existing local JoyCaption model loading and deterministic caption generation.
- Create `prototype/vision_worker_qwenvl.py`: Qwen3-VL GGUF and mmproj loading through multimodal llama-cpp.
- Create `prototype/scripts/install_vision_models.py`: idempotent model and dependency verification/download.
- Modify `prototype/vision_engine.py`: analyzer registry, status reporting, serial worker execution, partial success, and merge input.
- Modify `prototype/server.py`: expose analyzer status and preserve partial results.
- Modify `prototype/data.js`: enable analyzers from backend status rather than fixed demo availability.
- Modify `prototype/app.js`: status fetch, multi-selection, per-model progress, raw outputs, and retry behavior.
- Modify `prototype/index.html`: analyzer progress and raw result surfaces.
- Modify `prototype/styles.css`: compact desktop layout for analyzer states and outputs.
- Create `prototype/tests/test_vision_workers.py`: worker preprocessing and JSON contract tests.
- Modify `prototype/tests/test_vision_engine.py`: registry, scheduling, failure isolation, and merge tests.
- Modify `prototype/tests/test_server.py`: status and multi-analyzer request tests.
- Modify `prototype/tests/app.test.js`: analyzer availability, progress, and partial-success UI tests.
- Modify `docs/development-log.md`: installation paths, versions, actual timings, and known limits.

### Task 1: Lock the Multi-Analyzer Contract

**Files:**
- Modify: `prototype/tests/test_vision_engine.py`
- Modify: `prototype/tests/test_server.py`
- Modify: `prototype/tests/app.test.js`

- [ ] **Step 1: Write failing engine tests**

Add tests that define a registry and serial runner contract:

```python
def test_runs_selected_analyzers_in_declared_order_and_keeps_raw_results(self):
    calls = []

    def runner(analyzer_id, image_path, timeout):
        calls.append(analyzer_id)
        return f"{analyzer_id} output"

    result = analyze_image_bytes(
        b"image",
        "input.png",
        ["qwenvl", "wd14", "florence"],
        {},
        runner=runner,
        decomposer=fake_decomposer,
        translator=lambda items, settings: {"translations": []},
    )

    self.assertEqual(calls, ["wd14", "florence", "qwenvl"])
    self.assertEqual(result["rawResults"]["wd14"], "wd14 output")


def test_returns_partial_success_when_one_analyzer_fails(self):
    def runner(analyzer_id, image_path, timeout):
        if analyzer_id == "joycaption":
            raise VisionEngineError("out of memory", "vision_worker_failed", 502)
        return f"{analyzer_id} output"

    result = analyze_image_bytes(
        b"image",
        "input.png",
        ["wd14", "joycaption"],
        {},
        runner=runner,
        decomposer=fake_decomposer,
        translator=lambda items, settings: {"translations": []},
    )

    statuses = {item["id"]: item["status"] for item in result["analyzers"]}
    self.assertEqual(statuses, {"wd14": "ready", "joycaption": "error"})
```

- [ ] **Step 2: Write failing API and UI tests**

Add a server test asserting `analyzerIds` is preserved for all selected models, and Node tests asserting installed analyzers become selectable and partial failures remain visible.

```javascript
test("applies backend analyzer status and preserves partial failures", () => {
  let state = createInitialState();
  state = reduceState(state, {
    type: "APPLY_VISION_STATUS",
    models: {
      florence: { installed: true, label: "已安装" },
      wd14: { installed: true, label: "已安装" },
      joycaption: { installed: true, label: "已安装" },
      qwenvl: { installed: true, label: "已安装" },
    },
  });
  state = reduceState(state, {
    type: "APPLY_IMAGE_ANALYSIS",
    item: {
      positiveEn: "1girl",
      blocks: [],
      rawResults: { wd14: "1girl" },
      analyzers: [
        { id: "wd14", status: "ready", raw: "1girl" },
        { id: "joycaption", status: "error", error: "显存不足" },
      ],
    },
  });
  assert.equal(state.analyzers.wd14.available, true);
  assert.equal(state.analyzers.joycaption.status, "error");
});
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
python -m unittest prototype.tests.test_vision_engine prototype.tests.test_server -v
node --test prototype/tests/app.test.js
```

Expected: failures for the missing registry runner signature, status action, and multi-analyzer UI behavior.

### Task 2: Add Shared Worker Protocol and WD14

**Files:**
- Create: `prototype/vision_worker_common.py`
- Create: `prototype/vision_worker_wd14.py`
- Create: `prototype/tests/test_vision_workers.py`
- Create: `prototype/scripts/install_vision_models.py`

- [ ] **Step 1: Write failing WD14 preprocessing and output tests**

```python
def test_wd14_preprocesses_to_bgr_square_float_batch(self):
    image = Image.new("RGB", (640, 320), "red")
    batch = prepare_wd14_image(image, 448)
    self.assertEqual(batch.shape, (1, 448, 448, 3))
    self.assertEqual(batch.dtype, numpy.float32)


def test_worker_emits_one_json_result_line(self):
    payload = encode_worker_result("wd14", "1girl, solo", 1.25)
    self.assertEqual(json.loads(payload)["result"], "1girl, solo")
```

- [ ] **Step 2: Run worker tests and verify RED**

Run:

```powershell
python -m unittest prototype.tests.test_vision_workers -v
```

Expected: import failures because the worker modules do not exist.

- [ ] **Step 3: Implement the shared JSON contract**

Implement:

```python
def encode_worker_result(analyzer_id: str, result: str, elapsed: float) -> str:
    if not result.strip():
        raise ValueError("empty analyzer result")
    return json.dumps(
        {"analyzer": analyzer_id, "result": result.strip(), "elapsed": elapsed},
        ensure_ascii=False,
    )
```

The worker CLI must accept `--image` and model-specific paths, print exactly one final JSON line, and send diagnostics to stderr.

- [ ] **Step 4: Implement WD14 CPU inference**

Use `InferenceSession(model_path, providers=["CPUExecutionProvider"])`, resize and white-pad the image, convert RGB to BGR float32, apply general threshold `0.35` and character threshold `0.85`, and emit comma-separated tags.

- [ ] **Step 5: Add idempotent WD14 installation**

Download these official repository files only when absent:

```python
WD14_REPO = "SmilingWolf/wd-v1-4-convnextv2-tagger-v2"
WD14_FILES = ("model.onnx", "selected_tags.csv")
```

Store them under `H:\提示词工程\models\vision\wd14\wd-v1-4-convnextv2-tagger-v2`.

- [ ] **Step 6: Run worker tests and verify GREEN**

Run:

```powershell
python -m unittest prototype.tests.test_vision_workers -v
```

Expected: all worker contract and WD14 preprocessing tests pass.

### Task 3: Add JoyCaption and Qwen3-VL Workers

**Files:**
- Create: `prototype/vision_worker_joycaption.py`
- Create: `prototype/vision_worker_qwenvl.py`
- Modify: `prototype/tests/test_vision_workers.py`
- Modify: `prototype/scripts/install_vision_models.py`

- [ ] **Step 1: Write failing model path and prompt tests**

```python
def test_joycaption_reuses_existing_fp8_directory(self):
    model = resolve_joycaption_model(existing_paths=[BETA_FP8, ALPHA_TWO])
    self.assertEqual(model, BETA_FP8)


def test_qwenvl_requires_model_and_mmproj(self):
    status = qwen_model_status(Path("missing.gguf"), Path("missing-mmproj.gguf"))
    self.assertFalse(status["installed"])
    self.assertEqual(len(status["missing"]), 2)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m unittest prototype.tests.test_vision_workers -v
```

Expected: missing JoyCaption and QwenVL modules.

- [ ] **Step 3: Implement JoyCaption worker**

Load the existing Beta One FP8 directory through `AutoProcessor` and `LlavaForConditionalGeneration`, use deterministic generation, and ask for a factual image-generation caption:

```python
SYSTEM_PROMPT = "You are a precise image captioner for image-generation prompt extraction."
USER_PROMPT = (
    "Describe only visible subjects, appearance, clothing, pose, interaction, "
    "scene, composition, lighting, effects, and art style. Do not invent names."
)
```

Use `do_sample=False`, `max_new_tokens=384`, then let the process exit to release CUDA memory.

- [ ] **Step 4: Install Qwen3-VL GGUF files**

Download to `H:\提示词工程\models\vision\qwenvl\Qwen3-VL-4B-Instruct-GGUF`:

```text
Qwen3VL-4B-Instruct-Q4_K_M.gguf
mmproj-Qwen3VL-4B-Instruct-F16.gguf
```

Source repository: `Qwen/Qwen3-VL-4B-Instruct-GGUF`.

- [ ] **Step 5: Implement Qwen3-VL worker**

Load with the installed multimodal llama-cpp build:

```python
handler = Qwen3VLChatHandler(
    clip_model_path=str(mmproj_path),
    image_max_tokens=4096,
    verbose=False,
)
llm = Llama(
    model_path=str(model_path),
    chat_handler=handler,
    n_ctx=8192,
    n_batch=512,
    n_gpu_layers=-1,
    verbose=False,
)
```

Send the uploaded image as a Base64 data URL and return the assistant content. If the installed wheel lacks `Qwen3VLChatHandler`, stop with a precise dependency error rather than silently running text-only.

- [ ] **Step 6: Run worker tests and verify GREEN**

Run:

```powershell
python -m unittest prototype.tests.test_vision_workers -v
```

Expected: all path selection, prompt, and dependency validation tests pass without loading full models.

### Task 4: Integrate Registry, Serial Scheduling, and Partial Results

**Files:**
- Modify: `prototype/vision_engine.py`
- Modify: `prototype/tests/test_vision_engine.py`
- Modify: `prototype/server.py`
- Modify: `prototype/tests/test_server.py`

- [ ] **Step 1: Implement analyzer registry**

Define:

```python
ANALYZERS = {
    "wd14": AnalyzerSpec("wd14", VISION_PYTHON, ROOT / "vision_worker_wd14.py", 180),
    "florence": AnalyzerSpec("florence", VISION_PYTHON, ROOT / "vision_worker.py", 300),
    "joycaption": AnalyzerSpec("joycaption", VISION_PYTHON, ROOT / "vision_worker_joycaption.py", 600),
    "qwenvl": AnalyzerSpec("qwenvl", VISION_PYTHON, ROOT / "vision_worker_qwenvl.py", 600),
}
ANALYZER_ORDER = ("wd14", "florence", "joycaption", "qwenvl")
```

Status must include `installed`, `label`, `missing`, and model path information without exposing secrets.

- [ ] **Step 2: Implement generic worker execution**

Run a subprocess with captured UTF-8 output, parse only the final JSON line, and normalize timeout, CUDA OOM, missing dependency, and invalid JSON into `VisionEngineError`.

- [ ] **Step 3: Implement partial-success analysis**

For each selected analyzer in declared order:

```python
try:
    raw = runner(analyzer_id, image_path, spec.timeout)
    results[analyzer_id] = raw
    analyzer_states.append({"id": analyzer_id, "status": "ready", "raw": raw})
except VisionEngineError as error:
    analyzer_states.append(
        {"id": analyzer_id, "status": "error", "error": error.message}
    )
```

Raise only when no analyzer succeeds. Concatenate successful raw outputs with source headings before calling the existing local LLM decomposer.

- [ ] **Step 4: Expose status and preserve request selection**

Add `GET /api/vision/status` and ensure `POST /api/vision/analyze` passes every selected installed analyzer to the engine.

- [ ] **Step 5: Run engine and server tests**

Run:

```powershell
python -m unittest prototype.tests.test_vision_engine prototype.tests.test_server -v
```

Expected: all registry, serial scheduling, partial failure, and API tests pass.

### Task 5: Enable Multi-Model UI and Progress

**Files:**
- Modify: `prototype/data.js`
- Modify: `prototype/app.js`
- Modify: `prototype/index.html`
- Modify: `prototype/styles.css`
- Modify: `prototype/tests/app.test.js`

- [ ] **Step 1: Apply backend installation status**

Fetch `/api/vision/status` when the image workspace opens and dispatch:

```javascript
{
  type: "APPLY_VISION_STATUS",
  models: payload.models,
}
```

Availability comes only from backend status; remove fixed `available: false` assumptions for WD14, JoyCaption, and QwenVL.

- [ ] **Step 2: Send all selected analyzers**

Build `analyzerIds` from checked and available analyzers. Disable analysis only when no image or no analyzer is selected.

- [ ] **Step 3: Render per-analyzer states and raw outputs**

Show stable states `未安装 / 等待 / 分析中 / 完成 / 失败`, and render each successful raw output in its own labeled section. A failed model remains visible while successful merged output is still applied.

- [ ] **Step 4: Run Node tests**

Run:

```powershell
node --test prototype/tests/app.test.js
```

Expected: all UI state, upload, multi-select, and progress tests pass.

### Task 6: Install, Run Real Models, and Record Evidence

**Files:**
- Modify: `docs/development-log.md`
- Create: `prototype/data/vision-validation-results.json`

- [ ] **Step 1: Record baseline resource state**

Run:

```powershell
nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free --format=csv,noheader
Get-PSDrive H | Select-Object Used,Free
```

Record the results before downloads.

- [ ] **Step 2: Run the idempotent installer**

Run:

```powershell
& 'H:\conmfui\ComfyUI-aki(1)\ComfyUI-aki-v3\ComfyUI-aki-v3\python\python.exe' prototype/scripts/install_vision_models.py
```

Expected: WD14 and Qwen3-VL files are present with nonzero size; JoyCaption reports reuse of the existing local FP8 model.

- [ ] **Step 3: Test each worker directly**

For each of the three validation images, run WD14, Florence, JoyCaption, and QwenVL workers independently. Save analyzer ID, filename, elapsed seconds, success/error, and raw output to `vision-validation-results.json`.

- [ ] **Step 4: Test the HTTP endpoint**

Upload a real image through `POST /api/vision/analyze` first with one analyzer at a time, then with:

```json
["wd14", "florence", "joycaption", "qwenvl"]
```

Expected: selected analyzers receive the same uploaded image; successful raw results differ across substantially different images; combined output includes source-specific information.

- [ ] **Step 5: Test through the browser**

Open `http://127.0.0.1:57913/`, upload each validation image, confirm preview, model selection, per-model progress, raw results, merged bilingual output, and visible partial errors.

- [ ] **Step 6: Run the complete automated suite**

Run:

```powershell
python -m unittest discover -s prototype\tests -p 'test_*.py' -v
node --test prototype/tests/app.test.js
```

Expected: zero failures.

- [ ] **Step 7: Record final resource state and limits**

Add exact model paths, file sizes, inference times, GPU memory observations, any model incompatibilities, and recovery instructions to `docs/development-log.md`.

## Execution Notes

This workspace is not currently recognized as a valid Git worktree, so the normal per-task commit steps cannot be performed. Use the checklist and test checkpoints as the audit trail, and do not alter unrelated user files.
