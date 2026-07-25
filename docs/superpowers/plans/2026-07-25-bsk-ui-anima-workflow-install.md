# bsk_UI 与 Anima 全功能工作流安装 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将下载的 bsk_UI 安装到当前 ComfyUI，并交付一个可独立拖入原生界面的 Anima 全功能工作流。

**Architecture:** 采用文件级复制安装，不执行插件自身的联网安装器。先验证源和目标，再停止当前 ComfyUI，复制插件与工作流，最后通过 HTTP、日志和节点注册信息验证并重新打开页面。

**Tech Stack:** Windows PowerShell、ComfyUI Python 扩展、ComfyUI HTTP API

## Global Constraints

- 源插件固定为 `G:\新建文件夹\插件-260523-120932684\ComfyUI-bsk_UI_26072301\ComfyUI-bsk_UI`。
- 安装目标固定为 `H:\conmfui\ComfyUI-aki(1)\ComfyUI-aki-v3\ComfyUI-aki-v3\ComfyUI\custom_nodes\ComfyUI-bsk_UI`。
- 独立工作流固定为 `H:\comyfui\workflows\bsk_UI\Anima_全功能.png`。
- 不覆盖 `ComfyUI-WD14-Tagger`，不下载模型，不执行插件安装 API。
- 不清空或修改 ComfyUI 的 `input`、`output`、模型和已有工作流。

---

### Task 1: 安装前检查与安全停止

**Files:**
- Read: `G:\新建文件夹\插件-260523-120932684\ComfyUI-bsk_UI_26072301\ComfyUI-bsk_UI\__init__.py`
- Read: `G:\新建文件夹\插件-260523-120932684\ComfyUI-bsk_UI_26072301\ComfyUI-bsk_UI\panel\Anima\Anima_全功能.png`

**Interfaces:**
- Consumes: 当前监听 `127.0.0.1:8191` 的 ComfyUI 进程
- Produces: 已确认的源目录、空闲目标目录和停止状态

- [ ] **Step 1: 验证源文件和目标状态**

Run:

```powershell
$source = 'G:\新建文件夹\插件-260523-120932684\ComfyUI-bsk_UI_26072301\ComfyUI-bsk_UI'
$target = 'H:\conmfui\ComfyUI-aki(1)\ComfyUI-aki-v3\ComfyUI-aki-v3\ComfyUI\custom_nodes\ComfyUI-bsk_UI'
$workflow = Join-Path $source 'panel\Anima\Anima_全功能.png'
Test-Path -LiteralPath (Join-Path $source '__init__.py')
Test-Path -LiteralPath $workflow
Test-Path -LiteralPath $target
```

Expected: 前两项为 `True`，目标为 `False`；若目标为 `True`，停止并报告，不覆盖。

- [ ] **Step 2: 停止当前 ComfyUI 进程**

Run:

```powershell
$listener = Get-NetTCPConnection -LocalPort 8191 -State Listen -ErrorAction SilentlyContinue
if ($listener) { Stop-Process -Id $listener.OwningProcess }
```

Expected: `Get-NetTCPConnection -LocalPort 8191 -State Listen` 不再返回监听器。

### Task 2: 复制插件与独立工作流

**Files:**
- Create: `H:\conmfui\ComfyUI-aki(1)\ComfyUI-aki-v3\ComfyUI-aki-v3\ComfyUI\custom_nodes\ComfyUI-bsk_UI\`
- Create: `H:\comyfui\workflows\bsk_UI\Anima_全功能.png`

**Interfaces:**
- Consumes: Task 1 验证的源和空闲目标
- Produces: 完整插件目录和独立工作流图片

- [ ] **Step 1: 复制完整插件目录**

Run:

```powershell
Copy-Item -LiteralPath $source -Destination $target -Recurse
```

Expected: `$target\__init__.py`、`$target\js\panel.js` 和 `$target\panel\Anima\Anima_全功能.png` 均存在。

- [ ] **Step 2: 创建并复制独立工作流**

Run:

```powershell
$workflowDirectory = 'H:\comyfui\workflows\bsk_UI'
New-Item -ItemType Directory -Path $workflowDirectory -Force | Out-Null
Copy-Item -LiteralPath $workflow -Destination (Join-Path $workflowDirectory 'Anima_全功能.png')
```

Expected: 源与目标工作流的 SHA256 哈希一致。

### Task 3: 启动和验证

**Files:**
- Read: `H:\comyfui\comfyui_anima_stdout.log`
- Read: `H:\comyfui\comfyui_anima_stderr.log`

**Interfaces:**
- Consumes: 已安装插件和现有 `H:\comyfui\start_blackbeast_comfyui.py`
- Produces: 运行于 `http://127.0.0.1:8191` 的 ComfyUI 与已注册的 bsk_UI 节点

- [ ] **Step 1: 使用现有启动脚本启动 ComfyUI**

Run:

```powershell
& 'H:\提示词工程\scripts\start_comfyui_only.ps1'
```

Expected: 输出 `ComfyUI: http://127.0.0.1:8191`。

- [ ] **Step 2: 验证 HTTP 与节点注册**

Run:

```powershell
$stats = Invoke-RestMethod 'http://127.0.0.1:8191/system_stats'
$objects = Invoke-RestMethod 'http://127.0.0.1:8191/object_info'
$stats.system
$objects.PSObject.Properties.Name -contains 'RandomSeedNode'
```

Expected: 系统信息存在，节点检查为 `True`。

- [ ] **Step 3: 检查插件启动日志**

Run:

```powershell
Select-String -Path 'H:\comyfui\comfyui_anima_stdout.log','H:\comyfui\comfyui_anima_stderr.log' -Pattern 'ComfyUI Panel|bsk_UI|Traceback|IMPORT FAILED'
```

Expected: 包含 bsk_UI 加载成功信息，且没有由该插件引起的导入失败。

- [ ] **Step 4: 打开页面并交付用法**

Run:

```powershell
Start-Process 'http://127.0.0.1:8191'
```

Expected: 浏览器打开 ComfyUI；交付独立工作流路径、加载步骤、随机种子要求和缺失模型排查方法。
