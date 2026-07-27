"""Deterministic experiment-matrix plans, without invoking an image provider."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any


MAX_VARIABLES = 8
MAX_VALUES_PER_VARIABLE = 8
MAX_CELLS = 64
MAX_MATRICES_PER_VERSION = 16
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")


class ExperimentMatrixError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_seed(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise ExperimentMatrixError("attributionSeed 必须是非负安全整数")
    return value


def _normalize_variables(value: Any) -> list[dict]:
    if not isinstance(value, list) or not value or len(value) > MAX_VARIABLES:
        raise ExperimentMatrixError("variables 必须包含 1 至 8 个变量")
    normalized: list[dict] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"key", "values"}:
            raise ExperimentMatrixError("每个变量只能包含 key 和 values")
        key = item["key"]
        values = item["values"]
        if not isinstance(key, str) or not SAFE_ID.fullmatch(key) or key in seen:
            raise ExperimentMatrixError("变量 key 无效或重复")
        if not isinstance(values, list) or not values or len(values) > MAX_VALUES_PER_VARIABLE:
            raise ExperimentMatrixError("每个变量必须有 1 至 8 个候选值")
        for candidate in values:
            if candidate is None or isinstance(candidate, (dict, list, bool)):
                raise ExperimentMatrixError("变量候选值必须是字符串或数字")
            if isinstance(candidate, float) and not candidate == candidate:
                raise ExperimentMatrixError("变量候选值不能为 NaN")
            if not isinstance(candidate, (str, int, float)):
                raise ExperimentMatrixError("变量候选值必须是字符串或数字")
            if isinstance(candidate, str) and len(candidate) > 1_000:
                raise ExperimentMatrixError("变量候选文本过长")
        seen.add(key)
        normalized.append({"key": key, "values": deepcopy(values)})
    return normalized


def _cell_id(seed: int, parameters: dict) -> str:
    digest = hashlib.sha256(f"{seed}:{_canonical(parameters)}".encode("utf-8")).hexdigest()
    return f"cell-{digest[:20]}"


def plan_experiment_matrix(payload: dict) -> dict:
    """Build either single-variable controls or a bounded Cartesian matrix."""

    if not isinstance(payload, dict) or set(payload) != {
        "name", "version", "attributionSeed", "design", "variables"
    }:
        raise ExperimentMatrixError("实验矩阵请求字段无效")
    name = payload["name"]
    version = payload["version"]
    design = payload["design"]
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
        raise ExperimentMatrixError("实验名称无效")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ExperimentMatrixError("version 必须是正整数")
    if design not in {"one-factor", "cartesian"}:
        raise ExperimentMatrixError("design 仅支持 one-factor 或 cartesian")
    seed = _require_seed(payload["attributionSeed"])
    variables = _normalize_variables(payload["variables"])
    baseline = {item["key"]: item["values"][0] for item in variables}
    parameters: list[dict] = [baseline]
    if design == "one-factor":
        for variable in variables:
            for value in variable["values"][1:]:
                parameters.append({**baseline, variable["key"]: value})
    else:
        parameters = [{}]
        for variable in variables:
            parameters = [
                {**current, variable["key"]: value}
                for current in parameters
                for value in variable["values"]
            ]
            if len(parameters) > MAX_CELLS:
                raise ExperimentMatrixError("笛卡尔矩阵超过 64 个格子上限")
    if len(parameters) > MAX_CELLS:
        raise ExperimentMatrixError("实验矩阵超过 64 个格子上限")
    matrix_body = {
        "schemaVersion": 1,
        "id": "matrix-" + hashlib.sha256(
            _canonical(
                {
                    "name": name.strip(),
                    "version": version,
                    "seed": seed,
                    "design": design,
                    "variables": variables,
                }
            ).encode("utf-8")
        ).hexdigest()[:20],
        "name": name.strip(),
        "version": version,
        "design": design,
        "attributionSeed": seed,
        "variables": variables,
        "cells": [
            {
                "id": _cell_id(seed, item),
                "parameters": item,
                # Same seed deliberately isolates the declared variable changes.
                "generationSeed": seed,
                "assetId": None,
                "evidenceId": None,
            }
            for item in parameters
        ],
    }
    return matrix_body


def normalize_experiment_matrices_by_version(value: Any) -> dict[str, list[dict]]:
    """Validate persisted plans/results while keeping the project as authority."""

    if value in (None, {}):
        return {}
    if not isinstance(value, dict) or len(value) > 512:
        raise ExperimentMatrixError("experimentMatricesByVersion 必须是对象")
    normalized: dict[str, list[dict]] = {}
    for raw_version, matrices in value.items():
        if not isinstance(raw_version, str) or not raw_version.isdecimal() or int(raw_version) < 1:
            raise ExperimentMatrixError("实验矩阵版本键无效")
        if not isinstance(matrices, list) or len(matrices) > MAX_MATRICES_PER_VERSION:
            raise ExperimentMatrixError("每个版本最多保存 16 个实验矩阵")
        items: list[dict] = []
        for matrix in matrices:
            if not isinstance(matrix, dict):
                raise ExperimentMatrixError("实验矩阵必须是对象")
            plan = plan_experiment_matrix(
                {
                    key: matrix.get(key)
                    for key in ("name", "version", "attributionSeed", "design", "variables")
                }
            )
            if plan["version"] != int(raw_version):
                raise ExperimentMatrixError("实验矩阵版本必须与版本键一致")
            incoming_cells = matrix.get("cells")
            if not isinstance(incoming_cells, list) or len(incoming_cells) != len(plan["cells"]):
                raise ExperimentMatrixError("实验矩阵格子与计划不一致")
            by_id = {item["id"]: item for item in incoming_cells if isinstance(item, dict)}
            if len(by_id) != len(plan["cells"]):
                raise ExperimentMatrixError("实验矩阵格子 ID 无效或重复")
            for cell in plan["cells"]:
                source = by_id.get(cell["id"])
                if source is None or source.get("parameters") != cell["parameters"]:
                    raise ExperimentMatrixError("实验矩阵格子参数与计划不一致")
                for reference_key in ("assetId", "evidenceId"):
                    reference = source.get(reference_key)
                    if reference is not None and (
                        not isinstance(reference, str) or not SAFE_ID.fullmatch(reference)
                    ):
                        raise ExperimentMatrixError(f"{reference_key} 无效")
                    cell[reference_key] = reference
            items.append(plan)
        normalized[raw_version] = items
    return normalized
