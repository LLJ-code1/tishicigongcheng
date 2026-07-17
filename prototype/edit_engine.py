"""Deterministic minimal-difference editing for complete Prompt Studio recipes.

This module deliberately contains no database, HTTP, or model calls.  It handles
the small, deterministic subset of Chinese edit instructions that can be mapped
without guessing.  Instructions outside that subset are returned as unresolved
so a caller can ask a model or the user for an explicit proposal.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from recipe import (
    BLOCK_IDS,
    RecipeValidationError,
    get_block_definitions,
    normalize_recipe,
    recipe_hash,
)


EDIT_SCHEMA_VERSION = "minimal-edit-v1"
VERSION_SCHEMA_VERSION = "recipe-version-v1"
MAX_INSTRUCTION_CHARS = 2_000
MAX_RECIPE_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 48

BLOCK_LABELS = {
    definition["id"]: definition["label"]
    for definition in get_block_definitions()
}
PERSON_BLOCK_IDS = (
    "identity",
    "appearance",
    "clothing",
    "expression",
    "action",
    "interaction",
)

# Aliases identify a block only; they do not authorize copying arbitrary trailing
# prose into a prompt.  Values still have to match KNOWN_VALUES below.
CATEGORY_ALIASES = {
    "identity": ("人物身份", "角色身份", "身份"),
    "appearance": ("身体特征", "外观特征", "人物外观", "外貌", "外观"),
    "clothing": ("服装配饰", "服装", "服饰", "衣服", "穿着", "配饰"),
    "expression": ("人物表情", "神情", "表情"),
    "action": ("姿势动作", "人物动作", "姿势", "动作", "姿态"),
    "interaction": ("互动关系", "人物互动", "互动", "关系"),
    "scene": ("场景环境", "场景", "环境", "背景"),
    "camera": ("构图镜头", "镜头构图", "构图", "镜头", "视角", "景别"),
    "lighting": ("光影色彩", "灯光色彩", "光影", "灯光", "光线", "色彩"),
    "style": ("风格艺术家", "艺术风格", "画风", "风格", "艺术家"),
    "effects": ("道具特效", "道具", "特效", "效果"),
    "quality": ("质量增强", "画质", "质量"),
    "negative": ("负面提示词", "负向提示词", "负面", "负向", "不要的内容"),
}

# term -> (block id, English prompt fragment, normalized Chinese fragment)
# The table intentionally stays finite.  Unknown free text must not be presented
# as if it had been translated or safely applied.
KNOWN_VALUES = {
    # identity
    "女魔法师": ("identity", "female mage", "女魔法师"),
    "男魔法师": ("identity", "male mage", "男魔法师"),
    "女骑士": ("identity", "female knight", "女骑士"),
    "骑士": ("identity", "knight", "骑士"),
    "成年女性": ("identity", "adult woman", "成年女性"),
    "成年男性": ("identity", "adult man", "成年男性"),
    # appearance
    "银色长发": ("appearance", "long silver hair", "银色长发"),
    "白色长发": ("appearance", "long white hair", "白色长发"),
    "黑色长发": ("appearance", "long black hair", "黑色长发"),
    "金色长发": ("appearance", "long blonde hair", "金色长发"),
    "蓝色眼睛": ("appearance", "blue eyes", "蓝色眼睛"),
    "红色眼睛": ("appearance", "red eyes", "红色眼睛"),
    "绿色眼睛": ("appearance", "green eyes", "绿色眼睛"),
    "白发": ("appearance", "white hair", "白发"),
    "银发": ("appearance", "silver hair", "银发"),
    "黑发": ("appearance", "black hair", "黑发"),
    "金发": ("appearance", "blonde hair", "金发"),
    "红发": ("appearance", "red hair", "红发"),
    "长发": ("appearance", "long hair", "长发"),
    "短发": ("appearance", "short hair", "短发"),
    "蓝眼": ("appearance", "blue eyes", "蓝眼"),
    "红眼": ("appearance", "red eyes", "红眼"),
    # clothing
    "红色连衣裙": ("clothing", "red dress", "红色连衣裙"),
    "白色连衣裙": ("clothing", "white dress", "白色连衣裙"),
    "黑色晚礼服": ("clothing", "black evening gown", "黑色晚礼服"),
    "黑色礼服": ("clothing", "black formal dress", "黑色礼服"),
    "魔法师长袍": ("clothing", "mage robe", "魔法师长袍"),
    "全身盔甲": ("clothing", "full-body armor", "全身盔甲"),
    "校服": ("clothing", "school uniform", "校服"),
    "和服": ("clothing", "kimono", "和服"),
    "盔甲": ("clothing", "armor", "盔甲"),
    "西装": ("clothing", "suit", "西装"),
    "旗袍": ("clothing", "qipao", "旗袍"),
    "运动服": ("clothing", "sportswear", "运动服"),
    "泳装": ("clothing", "swimsuit", "泳装"),
    "连衣裙": ("clothing", "dress", "连衣裙"),
    "帽子": ("clothing", "hat", "帽子"),
    # expression
    "温柔微笑": ("expression", "gentle smile", "温柔微笑"),
    "微笑": ("expression", "smile", "微笑"),
    "大笑": ("expression", "laughing", "大笑"),
    "哭泣": ("expression", "crying", "哭泣"),
    "愤怒": ("expression", "angry expression", "愤怒"),
    "害羞": ("expression", "shy expression", "害羞"),
    "平静": ("expression", "calm expression", "平静"),
    "惊讶": ("expression", "surprised expression", "惊讶"),
    # action
    "回头看": ("action", "looking back", "回头看"),
    "双手叉腰": ("action", "hands on hips", "双手叉腰"),
    "站立": ("action", "standing", "站立"),
    "坐下": ("action", "seated", "坐下"),
    "奔跑": ("action", "running", "奔跑"),
    "跑步": ("action", "running", "跑步"),
    "跳跃": ("action", "jumping", "跳跃"),
    "挥手": ("action", "waving", "挥手"),
    "躺下": ("action", "lying down", "躺下"),
    "跪姿": ("action", "kneeling", "跪姿"),
    # interaction
    "背靠背": ("interaction", "back-to-back", "背靠背"),
    "手牵手": ("interaction", "holding hands", "手牵手"),
    "牵手": ("interaction", "holding hands", "牵手"),
    "拥抱": ("interaction", "hugging", "拥抱"),
    "对视": ("interaction", "eye contact", "对视"),
    # scene
    "夜晚城市": ("scene", "city at night", "夜晚城市"),
    "城市天台": ("scene", "city rooftop", "城市天台"),
    "魔法森林": ("scene", "enchanted forest", "魔法森林"),
    "森林": ("scene", "forest", "森林"),
    "海边": ("scene", "beach", "海边"),
    "夜景": ("scene", "nighttime setting", "夜景"),
    "室内": ("scene", "indoors", "室内"),
    "街道": ("scene", "street", "街道"),
    "教室": ("scene", "classroom", "教室"),
    "城堡": ("scene", "castle", "城堡"),
    "雪地": ("scene", "snowy field", "雪地"),
    # camera
    "第一人称视角": ("camera", "first-person view", "第一人称视角"),
    "大特写": ("camera", "extreme close-up", "大特写"),
    "全身镜头": ("camera", "full body shot", "全身镜头"),
    "半身镜头": ("camera", "upper body shot", "半身镜头"),
    "俯拍": ("camera", "overhead shot", "俯拍"),
    "仰拍": ("camera", "low-angle shot", "仰拍"),
    "特写": ("camera", "close-up", "特写"),
    "全身": ("camera", "full body shot", "全身"),
    "半身": ("camera", "upper body shot", "半身"),
    "广角": ("camera", "wide-angle shot", "广角"),
    "侧面": ("camera", "side view", "侧面"),
    "背面": ("camera", "rear view", "背面"),
    # lighting
    "霓虹灯光": ("lighting", "neon lighting", "霓虹灯光"),
    "暖色灯光": ("lighting", "warm lighting", "暖色灯光"),
    "冷色灯光": ("lighting", "cool lighting", "冷色灯光"),
    "暖光": ("lighting", "warm lighting", "暖光"),
    "冷光": ("lighting", "cool lighting", "冷光"),
    "逆光": ("lighting", "backlighting", "逆光"),
    "侧光": ("lighting", "side lighting", "侧光"),
    "柔光": ("lighting", "soft lighting", "柔光"),
    "霓虹": ("lighting", "neon lighting", "霓虹"),
    "月光": ("lighting", "moonlight", "月光"),
    # style
    "赛博朋克风格": ("style", "cyberpunk style", "赛博朋克风格"),
    "水彩画风": ("style", "watercolor style", "水彩画风"),
    "油画风格": ("style", "oil painting style", "油画风格"),
    "动漫风格": ("style", "anime style", "动漫风格"),
    "写实风格": ("style", "photorealistic style", "写实风格"),
    "赛博朋克": ("style", "cyberpunk style", "赛博朋克"),
    "水彩": ("style", "watercolor style", "水彩"),
    "油画": ("style", "oil painting style", "油画"),
    "动漫": ("style", "anime style", "动漫"),
    "写实": ("style", "photorealistic style", "写实"),
    "素描": ("style", "pencil sketch", "素描"),
    "厚涂": ("style", "painterly style", "厚涂"),
    "国风": ("style", "Chinese-inspired style", "国风"),
    # effects
    "魔法粒子": ("effects", "magical particles", "魔法粒子"),
    "花瓣": ("effects", "flower petals", "花瓣"),
    "火焰": ("effects", "flames", "火焰"),
    "闪电": ("effects", "lightning", "闪电"),
    "粒子": ("effects", "particles", "粒子"),
    "烟雾": ("effects", "smoke", "烟雾"),
    "雨滴": ("effects", "raindrops", "雨滴"),
    # quality
    "超精细": ("quality", "ultra-detailed", "超精细"),
    "高对比": ("quality", "high contrast", "高对比"),
    "高细节": ("quality", "highly detailed", "高细节"),
    "美学增强": ("quality", "very aesthetic", "美学增强"),
    # negative
    "多余手指": ("negative", "extra fingers", "多余手指"),
    "畸形手": ("negative", "deformed hands", "畸形手"),
    "低质量": ("negative", "low quality", "低质量"),
    "JPEG压缩痕迹": ("negative", "jpeg artifacts", "JPEG压缩痕迹"),
    "水印": ("negative", "watermark", "水印"),
    "模糊": ("negative", "blurry", "模糊"),
    "文字": ("negative", "text", "文字"),
}

EDIT_CUES = (
    "换成",
    "更换为",
    "替换成",
    "替换为",
    "改成",
    "改为",
    "调整为",
    "变成",
    "更换",
    "替换",
    "修改",
    "调整",
    "加强",
    "强化",
    "增加",
    "添加",
    "加上",
    "补充",
    "营造",
    "去掉",
    "删除",
    "移除",
    "只改",
    "仅改",
    "改",
    "换",
)
REPLACE_CUES = ("换成", "更换为", "替换成", "替换为", "改成", "改为", "调整为", "变成", "更换", "替换", "只改", "仅改", "修改", "调整", "改", "换")
APPEND_CUES = ("加强", "强化", "增加", "添加", "加上", "补充", "营造")
REMOVE_CUES = ("去掉", "删除", "移除")


@dataclass
class EditEngineError(ValueError):
    message: str
    code: str = "edit_engine_error"
    status: int = 400

    def __str__(self) -> str:
        return self.message


def _validate_json_value(value: Any, path: str = "$", depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise EditEngineError("配方 JSON 嵌套过深", "recipe_too_deep", 400)
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise EditEngineError(
                f"{path} 包含无效 Unicode", "invalid_recipe", 400
            ) from error
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EditEngineError(f"{path} 包含非有限数字", "invalid_recipe", 400)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]", depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise EditEngineError(f"{path} 包含非字符串键", "invalid_recipe", 400)
            _validate_json_value(item, f"{path}.{key}", depth + 1)
        return
    raise EditEngineError(f"{path} 包含不能写入 JSON 的值", "invalid_recipe", 400)


def canonical_json(value: Any) -> str:
    """Return the strict canonical JSON representation used by recipe hashes."""

    _validate_json_value(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise EditEngineError("无法规范化配方 JSON", "invalid_recipe", 400) from error


def _clone_json(value: Any) -> Any:
    # Validation happens before deepcopy so custom objects cannot smuggle state.
    _validate_json_value(value)
    return copy.deepcopy(value)


def _validate_recipe(recipe: Any) -> tuple[dict, dict[str, dict], list[str]]:
    try:
        normalized = normalize_recipe(recipe)
    except RecipeValidationError as error:
        raise EditEngineError(str(error), "invalid_recipe", 400) from error
    encoded = canonical_json(normalized).encode("utf-8")
    if len(encoded) > MAX_RECIPE_BYTES:
        raise EditEngineError("recipe 超过 2 MiB 限制", "recipe_too_large", 413)
    blocks = normalized["blocks"]
    block_map: dict[str, dict] = {}
    order: list[str] = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise EditEngineError(
                f"recipe.blocks[{index}] 必须是对象", "invalid_recipe", 400
            )
        block_id = block.get("id")
        if block_id not in BLOCK_IDS:
            raise EditEngineError(
                f"不支持的结构块：{block_id!r}", "unsupported_block", 400
            )
        if block_id in block_map:
            raise EditEngineError(
                f"结构块重复：{block_id}", "duplicate_block", 400
            )
        block_map[block_id] = block
        order.append(block_id)
    return normalized, block_map, order


def _normalize_instruction(instruction: Any) -> tuple[str, str]:
    if not isinstance(instruction, str):
        raise EditEngineError("instruction 必须是字符串", "invalid_instruction", 400)
    original = instruction.strip()
    if not original:
        raise EditEngineError("instruction 不能为空", "invalid_instruction", 400)
    try:
        original.encode("utf-8")
    except UnicodeEncodeError as error:
        raise EditEngineError(
            "instruction 包含无效 Unicode", "invalid_instruction", 400
        ) from error
    if len(original) > MAX_INSTRUCTION_CHARS:
        raise EditEngineError("instruction 超过 2000 字限制", "instruction_too_long", 413)
    if any(ord(character) < 32 and character not in "\t\n\r" for character in original):
        raise EditEngineError("instruction 包含控制字符", "invalid_instruction", 400)
    normalized = unicodedata.normalize("NFKC", original)
    normalized = re.sub(r"[\t\r ]+", " ", normalized).strip()
    return original, normalized


def _preserved_ids(clause: str) -> set[str]:
    result: set[str] = set()
    if re.search(
        r"(?:人物|角色|主体)(?:保持)?不变|(?:保持|保留|不改|不修改|不调整|别改|别修改|别调整|不要改|不要修改|不要调整)(?:人物|角色|主体)",
        clause,
    ):
        result.update(PERSON_BLOCK_IDS)
    for block_id, aliases in CATEGORY_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            escaped = re.escape(alias)
            if re.search(
                rf"(?:保持|保留|不改|不修改|不调整|别改|别修改|别调整|不要改|不要修改|不要调整){escaped}(?:不变)?|{escaped}(?:保持)?不变|{escaped}(?:别动|不要变)",
                clause,
            ):
                result.add(block_id)
                break
    return result


def _explicit_target_ids(clause: str, clause_preserved: set[str]) -> set[str]:
    result: set[str] = set()
    verbs = "换成|更换为|替换成|替换为|改成|改为|调整为|变成|更换|替换|修改|调整|加强|强化|增加|添加|加上|补充|去掉|删除|移除|改|换"
    for block_id, aliases in CATEGORY_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            escaped = re.escape(alias)
            patterns = (
                rf"(?:只|仅)(?:改|修改|调整|替换)(?:一下)?{escaped}",
                rf"(?:{verbs})(?:一下)?(?:这个)?{escaped}",
                rf"{escaped}(?:的内容)?(?:{verbs})",
            )
            if any(re.search(pattern, clause) for pattern in patterns):
                # A pure "不要改服装" preservation clause contains "改服装";
                # do not reinterpret it as a target.
                if block_id in clause_preserved and not re.search(
                    rf"(?:但是|但|同时|然后).*(?:{verbs}).*{escaped}|{escaped}.*(?:但是|但|同时|然后).*(?:{verbs})",
                    clause,
                ):
                    continue
                result.add(block_id)
                break
    return result


def _exclusive_target_ids(text: str) -> set[str]:
    """Return blocks constrained by an explicit "只/仅改..." scope."""

    result: set[str] = set()
    for block_id, aliases in CATEGORY_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if re.search(
                rf"(?:只|仅)(?:改|修改|调整|替换)(?:一下)?{re.escape(alias)}",
                text,
            ):
                result.add(block_id)
                break
    return result


def _operation_mode(clause: str, block_id: str) -> str:
    if block_id == "negative" and re.search(r"(?:不要|避免)", clause):
        return "append"
    if any(cue in clause for cue in REMOVE_CUES):
        return "remove"
    if any(cue in clause for cue in APPEND_CUES):
        return "append"
    if any(cue in clause for cue in REPLACE_CUES):
        return "replace"
    return "replace"


def _find_known_values(text: str) -> list[dict]:
    occupied: list[tuple[int, int]] = []
    matches: list[dict] = []
    candidates = []
    for term, (block_id, en, zh) in KNOWN_VALUES.items():
        for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            candidates.append((match.start(), match.end(), term, block_id, en, zh))
    # Prefer a longer phrase at the same/overlapping position ("红色连衣裙"
    # instead of both "红色连衣裙" and "连衣裙").
    candidates.sort(key=lambda item: (-(item[1] - item[0]), item[0], item[2]))
    for start, end, term, block_id, en, zh in candidates:
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        occupied.append((start, end))
        matches.append(
            {
                "blockId": block_id,
                "matched": term,
                "en": en,
                "zh": zh,
                "position": start,
            }
        )
    matches.sort(key=lambda item: (item["position"], BLOCK_IDS.index(item["blockId"])))
    return matches


def _parse_instruction_internal(instruction: Any) -> tuple[dict, dict[str, list[dict]], list[dict]]:
    original, normalized = _normalize_instruction(instruction)
    clauses = [
        item.strip()
        for item in re.split(r"[，,。；;\n]+|(?:同时|然后)", normalized)
        if item.strip()
    ]
    if not clauses:
        clauses = [normalized]

    exclusive_targets = _exclusive_target_ids(normalized)
    targets: set[str] = set()
    preserved: set[str] = set()
    operations: dict[str, list[dict]] = {}
    unresolved: list[dict] = []
    conflicts: list[dict] = []
    explicit_targets: set[str] = set()
    explicit_target_text: dict[str, str] = {}
    unhandled_clauses: list[str] = []

    for clause in clauses:
        clause_preserved = _preserved_ids(clause)
        preserved.update(clause_preserved)
        clause_targets = _explicit_target_ids(clause, clause_preserved)
        clause_handled = bool(clause_preserved or clause_targets)
        outside_explicit_scope = (
            clause_targets.difference(exclusive_targets)
            if exclusive_targets
            else set()
        )
        for block_id in sorted(outside_explicit_scope, key=BLOCK_IDS.index):
            conflicts.append(
                {
                    "code": "outside_exclusive_scope",
                    "severity": "blocking",
                    "affectedId": block_id,
                    "exclusiveIds": sorted(exclusive_targets, key=BLOCK_IDS.index),
                    "message": (
                        f"指令要求只修改"
                        f"{'、'.join(BLOCK_LABELS[item] for item in sorted(exclusive_targets, key=BLOCK_IDS.index))}，"
                        f"但又点名了{BLOCK_LABELS[block_id]}。"
                    ),
                }
            )
        clause_targets.difference_update(outside_explicit_scope)
        explicit_targets.update(clause_targets)
        for block_id in clause_targets:
            explicit_target_text.setdefault(block_id, clause)

        special_night = "夜景氛围" in clause and any(
            cue in clause for cue in (*EDIT_CUES, "营造", "改得更")
        )
        concept_text = clause.replace("夜景氛围", " " if special_night else "夜景氛围")
        concepts = _find_known_values(concept_text)
        has_edit_cue = any(cue in clause for cue in EDIT_CUES)
        negative_shortcut = bool(re.search(r"(?:不要|避免)", clause)) and any(
            concept["blockId"] == "negative" for concept in concepts
        )

        if special_night:
            clause_handled = True
            special_targets = {"scene", "lighting"}
            if exclusive_targets:
                special_targets.intersection_update(exclusive_targets)
            # A category named in the same clause narrows the otherwise
            # two-block "夜景氛围" expansion.
            if clause_targets.intersection({"scene", "lighting"}):
                special_targets.intersection_update(clause_targets)
            clause_targets.update(special_targets)
            if "scene" in special_targets:
                operations.setdefault("scene", []).append(
                    {
                        "blockId": "scene",
                        "mode": "append",
                        "matched": "夜景氛围",
                        "en": "nighttime setting",
                        "zh": "夜景环境",
                    }
                )
            if "lighting" in special_targets:
                operations.setdefault("lighting", []).append(
                    {
                        "blockId": "lighting",
                        "mode": "append",
                        "matched": "夜景氛围",
                        "en": "moody night lighting",
                        "zh": "夜景氛围光影",
                    }
                )

        if clause_targets:
            clause_handled = True
            targets.update(clause_targets)
            for concept in concepts:
                block_id = concept["blockId"]
                if block_id not in clause_targets:
                    conflicts.append(
                        {
                            "code": "category_value_mismatch",
                            "severity": "blocking",
                            "targetIds": sorted(clause_targets, key=BLOCK_IDS.index),
                            "valueBlockId": block_id,
                            "matched": concept["matched"],
                            "message": (
                                f"“{concept['matched']}”属于{BLOCK_LABELS[block_id]}，"
                                "与句中点名的修改块不一致。"
                            ),
                        }
                    )
                    continue
                operation = {key: value for key, value in concept.items() if key != "position"}
                operation["mode"] = _operation_mode(clause, block_id)
                operations.setdefault(block_id, []).append(operation)
        elif (
            re.search(r"(?:不要|避免)", clause)
            and not has_edit_cue
            and concepts
            and not negative_shortcut
        ):
            clause_handled = True
            for concept in concepts:
                unresolved.append(
                    {
                        "code": "ambiguous_negation",
                        "blockId": concept["blockId"],
                        "text": clause,
                        "message": "该否定表达无法确定应删除现有内容还是加入负向约束。",
                    }
                )
        elif (has_edit_cue or negative_shortcut) and concepts:
            clause_handled = True
            for concept in concepts:
                block_id = concept["blockId"]
                # "不要红裙" is not safely equivalent to changing clothing or
                # adding the phrase to the negative prompt.  Only known negative
                # concepts use the short "不要..." form.
                if (
                    re.search(r"(?:不要|避免)", clause)
                    and not has_edit_cue
                    and block_id != "negative"
                ):
                    unresolved.append(
                        {
                            "code": "ambiguous_negation",
                            "blockId": block_id,
                            "text": clause,
                            "message": "该否定表达无法确定应删除现有内容还是加入负向约束。",
                        }
                    )
                    continue
                if exclusive_targets and block_id not in exclusive_targets:
                    conflicts.append(
                        {
                            "code": "outside_exclusive_scope",
                            "severity": "blocking",
                            "affectedId": block_id,
                            "exclusiveIds": sorted(
                                exclusive_targets, key=BLOCK_IDS.index
                            ),
                            "message": (
                                f"“{concept['matched']}”会修改{BLOCK_LABELS[block_id]}，"
                                "超出指令的“只改”范围。"
                            ),
                        }
                    )
                    continue
                targets.add(block_id)
                operation = {key: value for key, value in concept.items() if key != "position"}
                operation["mode"] = _operation_mode(clause, block_id)
                operations.setdefault(block_id, []).append(operation)
        elif clause_targets:
            targets.update(clause_targets)

        if not clause_handled:
            unhandled_clauses.append(clause)

    # An explicitly named target without a finite known value is a valid parse
    # of the scope, but not a valid deterministic edit.  Defer this check until
    # all clauses have been read so "只改服装，换成红色连衣裙" resolves as one edit.
    for block_id in explicit_targets:
        if block_id not in operations:
            unresolved.append(
                {
                    "code": "replacement_requires_model",
                    "blockId": block_id,
                    "text": explicit_target_text[block_id],
                    "message": (
                        f"已识别要修改{BLOCK_LABELS[block_id]}，但新内容不在确定性词典中，"
                        "需要模型或人工给出中英文候选。"
                    ),
                }
            )

    if targets:
        unresolved.extend(
            {
                "code": "clause_requires_model",
                "blockId": None,
                "text": clause,
                "message": "该指令片段没有确定性映射，必须由模型或人工解析后再应用。",
            }
            for clause in unhandled_clauses
        )
    elif not unresolved:
        unresolved.append(
            {
                "code": "no_deterministic_mapping",
                "blockId": None,
                "text": normalized,
                "message": "没有识别到可安全执行的结构块和值，需要模型或人工解析。",
            }
        )

    overlap = targets.intersection(preserved)
    for block_id in sorted(overlap, key=BLOCK_IDS.index):
        conflicts.append(
            {
                "code": "preserved_block_targeted",
                "severity": "blocking",
                "affectedId": block_id,
                "lockedId": block_id,
                "message": f"指令同时要求保留和修改{BLOCK_LABELS[block_id]}。",
            }
        )

    # Multiple replacement values for the same atomic block are ambiguous.  We
    # do not silently choose the first/last phrase.
    for block_id, block_operations in operations.items():
        replacements = [item for item in block_operations if item["mode"] == "replace"]
        unique_values = {(item["en"], item["zh"]) for item in replacements}
        if len(unique_values) > 1:
            conflicts.append(
                {
                    "code": "multiple_replacements",
                    "severity": "blocking",
                    "affectedId": block_id,
                    "message": f"{BLOCK_LABELS[block_id]}同时出现多个替换值，不能自动选择。",
                }
            )

    affected_ids = [block_id for block_id in BLOCK_IDS if block_id in targets]
    preserved_ids = [block_id for block_id in BLOCK_IDS if block_id in preserved]
    public_operations = [
        _clone_json(operation)
        for block_id in BLOCK_IDS
        for operation in operations.get(block_id, [])
    ]
    parse = {
        "normalizedInstruction": normalized,
        "affectedIds": affected_ids,
        "preservedIds": preserved_ids,
        "explicitTargetIds": [
            block_id for block_id in BLOCK_IDS if block_id in explicit_targets
        ],
        "exclusiveTargetIds": [
            block_id for block_id in BLOCK_IDS if block_id in exclusive_targets
        ],
        "operations": public_operations,
        "unresolved": unresolved,
        "requiresModel": bool(unresolved),
        "status": (
            "conflict"
            if conflicts
            else "unresolved"
            if unresolved
            else "resolved"
        ),
        # Keep integral confidence values as JSON integers. JavaScript parses
        # both ``1.0`` and ``1`` as the same Number and serializes it back as
        # ``1``; using integers keeps previewHash stable across the browser
        # request/response boundary.
        "confidence": 1 if targets and not unresolved and not conflicts else 0,
        "originalInstruction": original,
    }
    return parse, operations, conflicts


def parse_instruction(instruction: str) -> dict:
    """Parse the deterministic subset of Chinese minimal-edit instructions."""

    parse, _operations, conflicts = _parse_instruction_internal(instruction)
    return {**parse, "conflicts": _clone_json(conflicts)}


def _split_prompt_values(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [
        part.strip()
        for part in re.split(r"[,，;；\n]+", value)
        if part.strip()
    ]


def _append_prompt_value(current: Any, value: str, separator: str) -> str:
    current_text = current.strip() if isinstance(current, str) else ""
    parts = _split_prompt_values(current_text)
    if value.casefold() in {part.casefold() for part in parts}:
        return current_text
    return separator.join([part for part in (current_text, value) if part])


def _remove_prompt_value(current: Any, value: str, separator: str) -> tuple[str, bool]:
    parts = _split_prompt_values(current)
    kept = [part for part in parts if part.casefold() != value.casefold()]
    return separator.join(kept), len(kept) != len(parts)


def _apply_operations_to_block(
    block: dict, operations: list[dict]
) -> tuple[dict, list[dict]]:
    after = _clone_json(block)
    unresolved: list[dict] = []
    for operation in operations:
        mode = operation["mode"]
        if mode == "replace":
            after["en"] = operation["en"]
            after["zh"] = operation["zh"]
        elif mode == "append":
            after["en"] = _append_prompt_value(after.get("en"), operation["en"], ", ")
            after["zh"] = _append_prompt_value(after.get("zh"), operation["zh"], "，")
        elif mode == "remove":
            next_en, removed_en = _remove_prompt_value(
                after.get("en"), operation["en"], ", "
            )
            next_zh, removed_zh = _remove_prompt_value(
                after.get("zh"), operation["zh"], "，"
            )
            if not (removed_en or removed_zh):
                unresolved.append(
                    {
                        "code": "value_not_present",
                        "blockId": block["id"],
                        "text": operation["matched"],
                        "message": (
                            f"{BLOCK_LABELS[block['id']]}中没有可精确删除的“"
                            f"{operation['matched']}”。"
                        ),
                    }
                )
            else:
                after["en"] = next_en
                after["zh"] = next_zh
        else:  # defensive: internal operations must never reach this branch
            raise EditEngineError("未知编辑操作", "invalid_preview", 400)
    return after, unresolved


def _changed_fields(before: dict, after: dict) -> list[str]:
    return sorted(
        key
        for key in set(before).union(after)
        if before.get(key) != after.get(key)
    )


def _block_text(block: dict | None) -> str:
    if not block:
        return ""
    return " ".join(
        value.casefold()
        for key in ("en", "zh")
        if isinstance((value := block.get(key)), str)
    )


def _dependency_conflicts(
    proposed: dict[str, dict], block_map: dict[str, dict], affected_ids: list[str]
) -> list[dict]:
    """Find only explicit lexical contradictions with immutable blocks."""

    affected = set(affected_ids)
    conflicts: list[dict] = []
    camera_text = _block_text(proposed.get("camera"))
    action_text = _block_text(block_map.get("action"))
    if (
        "camera" in affected
        and "action" not in affected
        and ("overhead shot" in camera_text or "俯拍" in camera_text)
        and any(
            marker in action_text
            for marker in ("low-angle", "low angle", "from below", "eye-level camera", "仰拍", "平视镜头")
        )
    ):
        conflicts.append(
            {
                "code": "locked_block_conflict",
                "severity": "blocking",
                "affectedId": "camera",
                "lockedId": "action",
                "message": "俯拍与已锁定动作中的仰拍/平视镜头要求冲突，未自动改写动作。",
            }
        )

    scene_text = _block_text(proposed.get("scene"))
    lighting_text = _block_text(proposed.get("lighting"))
    current_scene_text = _block_text(block_map.get("scene"))
    current_lighting_text = _block_text(block_map.get("lighting"))
    night_markers = ("night", "夜景", "夜晚")
    day_markers = ("daylight", "midday", "daytime", "白天", "正午", "日光")
    if (
        "scene" in affected
        and "lighting" not in affected
        and any(marker in scene_text for marker in night_markers)
        and any(marker in current_lighting_text for marker in day_markers)
    ):
        conflicts.append(
            {
                "code": "locked_block_conflict",
                "severity": "blocking",
                "affectedId": "scene",
                "lockedId": "lighting",
                "message": "夜景与已锁定的白昼光影冲突，未自动改写光影。",
            }
        )
    if (
        "lighting" in affected
        and "scene" not in affected
        and any(marker in lighting_text for marker in night_markers)
        and any(marker in current_scene_text for marker in day_markers)
    ):
        conflicts.append(
            {
                "code": "locked_block_conflict",
                "severity": "blocking",
                "affectedId": "lighting",
                "lockedId": "scene",
                "message": "夜景光影与已锁定的白天场景冲突，未自动改写场景。",
            }
        )
    return conflicts


def preview_edit(instruction: str, recipe: dict, base_hash: str) -> dict:
    """Create a no-write preview that locks every block not named by the edit.

    ``base_hash`` is mandatory optimistic concurrency state.  A mismatch raises
    instead of producing a preview for a stale recipe.
    """

    normalized_recipe, block_map, block_order = _validate_recipe(recipe)
    if not isinstance(base_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", base_hash):
        raise EditEngineError("base_hash 必须是 SHA-256 十六进制值", "invalid_base_hash", 400)
    actual_hash = recipe_hash(normalized_recipe)
    if not hmac.compare_digest(actual_hash, base_hash.casefold()):
        raise EditEngineError(
            "配方已变化，不能基于过期 hash 预览修改",
            "base_hash_mismatch",
            409,
        )

    parse, operations, conflicts = _parse_instruction_internal(instruction)
    affected_ids = list(parse["affectedIds"])
    locked_ids = [block_id for block_id in block_order if block_id not in affected_ids]
    proposed_blocks: dict[str, dict] = {}
    diffs: list[dict] = []
    runtime_unresolved: list[dict] = []

    for block_id in affected_ids:
        before = block_map.get(block_id)
        if before is None:
            runtime_unresolved.append(
                {
                    "code": "missing_target_block",
                    "blockId": block_id,
                    "text": parse["normalizedInstruction"],
                    "message": f"当前配方没有{BLOCK_LABELS[block_id]}结构块。",
                }
            )
            continue
        if bool(before.get("locked")):
            conflicts.append(
                {
                    "code": "target_already_locked",
                    "severity": "blocking",
                    "affectedId": block_id,
                    "lockedId": block_id,
                    "message": f"{BLOCK_LABELS[block_id]}已被用户锁定。",
                }
            )
        after, operation_unresolved = _apply_operations_to_block(
            before, operations.get(block_id, [])
        )
        runtime_unresolved.extend(operation_unresolved)
        proposed_blocks[block_id] = after
        changed_fields = _changed_fields(before, after)
        if (
            operations.get(block_id)
            and not changed_fields
            and not operation_unresolved
        ):
            runtime_unresolved.append(
                {
                    "code": "no_effect",
                    "blockId": block_id,
                    "text": parse["normalizedInstruction"],
                    "message": f"{BLOCK_LABELS[block_id]}已经是目标值，没有可应用差异。",
                }
            )
        diffs.append(
            {
                "id": block_id,
                "label": BLOCK_LABELS[block_id],
                "before": _clone_json(before),
                "after": after,
                "changed": bool(changed_fields),
                "changedFields": changed_fields,
            }
        )

    conflicts.extend(
        _dependency_conflicts(proposed_blocks, block_map, affected_ids)
    )
    if runtime_unresolved:
        parse["unresolved"] = [*parse["unresolved"], *runtime_unresolved]
        parse["requiresModel"] = True
    parse["status"] = (
        "conflict"
        if conflicts
        else "unresolved"
        if parse["unresolved"]
        else "resolved"
    )
    parse["confidence"] = 1 if parse["status"] == "resolved" else 0

    proposed_recipe = _clone_json(normalized_recipe)
    proposed_recipe_blocks = proposed_recipe["blocks"]
    by_id = {block["id"]: index for index, block in enumerate(proposed_recipe_blocks)}
    for block_id, after in proposed_blocks.items():
        proposed_recipe_blocks[by_id[block_id]] = _clone_json(after)
    result_hash = recipe_hash(proposed_recipe)
    ready = bool(affected_ids) and not conflicts and not parse["unresolved"] and all(
        diff["changed"] for diff in diffs
    )
    preview = {
        "schemaVersion": EDIT_SCHEMA_VERSION,
        "instruction": parse["originalInstruction"],
        "recipeHash": actual_hash,
        "baseHash": actual_hash,
        "resultHash": result_hash,
        "affectedIds": affected_ids,
        "lockedIds": locked_ids,
        "conflicts": _clone_json(conflicts),
        "diffs": diffs,
        "parse": parse,
        "ready": ready,
    }
    preview["previewHash"] = _compute_preview_hash(preview)
    return preview


def _validate_version_reference(value: Any, field: str) -> Any:
    if isinstance(value, bool) or value is None:
        raise EditEngineError(f"{field} 不能为空", "invalid_version_reference", 400)
    if isinstance(value, int):
        if value < 0:
            raise EditEngineError(f"{field} 不能为负数", "invalid_version_reference", 400)
        return value
    if isinstance(value, str) and 0 < len(value.strip()) <= 128:
        return value.strip()
    raise EditEngineError(f"{field} 无效", "invalid_version_reference", 400)


def _security_view(preview: dict) -> dict:
    keys = (
        "schemaVersion",
        "instruction",
        "recipeHash",
        "baseHash",
        "resultHash",
        "affectedIds",
        "lockedIds",
        "conflicts",
        "diffs",
        "parse",
        "ready",
        "previewHash",
    )
    return {key: preview.get(key) for key in keys}


def _preview_integrity_view(preview: dict) -> dict:
    return {
        key: value
        for key, value in _security_view(preview).items()
        if key != "previewHash"
    }


def _compute_preview_hash(preview: dict) -> str:
    return hashlib.sha256(
        canonical_json(_preview_integrity_view(preview)).encode("utf-8")
    ).hexdigest()


def apply_edit_preview(
    recipe: dict,
    preview: dict,
    parent_version: Any,
    new_version: Any | None = None,
) -> dict:
    """Confirm a preview and return an append-only version value.

    The preview is deterministically recomputed.  Only the recomputed
    ``affectedIds`` are merged; a changed ``after`` value, extra diff, or stale
    recipe is rejected rather than partially trusted.
    """

    normalized_recipe, _block_map, _block_order = _validate_recipe(recipe)
    if not isinstance(preview, dict):
        raise EditEngineError("preview 必须是对象", "invalid_preview", 400)
    parent_version = _validate_version_reference(parent_version, "parent_version")
    if new_version is not None:
        new_version = _validate_version_reference(new_version, "new_version")
    provided_preview_hash = preview.get("previewHash")
    if (
        not isinstance(provided_preview_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", provided_preview_hash)
        or not hmac.compare_digest(
            provided_preview_hash, _compute_preview_hash(preview)
        )
    ):
        raise EditEngineError(
            "previewHash 无效，预览可能已被篡改",
            "preview_tampered",
            409,
        )
    try:
        expected = preview_edit(
            preview.get("instruction"),
            normalized_recipe,
            preview.get("baseHash"),
        )
    except EditEngineError:
        raise
    if canonical_json(_security_view(preview)) != canonical_json(_security_view(expected)):
        raise EditEngineError(
            "预览内容已被修改或不再可复现",
            "preview_tampered",
            409,
        )
    if not expected["ready"]:
        raise EditEngineError(
            "预览仍有冲突或未解析内容，不能确认应用",
            "preview_not_ready",
            409,
        )

    result = _clone_json(normalized_recipe)
    result_index = {
        block["id"]: index for index, block in enumerate(result["blocks"])
    }
    affected = set(expected["affectedIds"])
    for diff in expected["diffs"]:
        if diff["id"] not in affected:
            raise EditEngineError("差异越过 affectedIds", "preview_out_of_scope", 400)
        result["blocks"][result_index[diff["id"]]] = _clone_json(diff["after"])

    result_hash = recipe_hash(result)
    if not hmac.compare_digest(result_hash, expected["resultHash"]):
        raise EditEngineError("应用结果 hash 不一致", "invalid_preview", 409)
    change_core = {
        "type": "instruction",
        "instruction": expected["instruction"],
        "previewHash": expected["previewHash"],
        "recipeHash": expected["recipeHash"],
        "baseHash": expected["baseHash"],
        "resultHash": result_hash,
        "affectedIds": expected["affectedIds"],
        "lockedIds": expected["lockedIds"],
        "parse": expected["parse"],
        "diffs": expected["diffs"],
    }
    change_id = "change-" + hashlib.sha256(
        canonical_json(change_core).encode("utf-8")
    ).hexdigest()[:24]
    version_value = {
        "schemaVersion": VERSION_SCHEMA_VERSION,
        "parentVersion": parent_version,
        "recipe": result,
        "change": {"id": change_id, **change_core},
    }
    if new_version is not None:
        version_value["version"] = new_version
    return version_value


def confirm_edit(
    recipe: dict,
    preview: dict,
    parent_version: Any,
    new_version: Any | None = None,
) -> dict:
    """Readable alias for :func:`apply_edit_preview`."""

    return apply_edit_preview(recipe, preview, parent_version, new_version)


def create_undo_version(
    current_recipe: dict,
    parent_recipe: dict,
    current_version: Any,
    parent_version: Any,
    new_version: Any | None = None,
) -> dict:
    """Copy a parent recipe into a *new* version; never mutate/delete history.

    The new version's ``parentVersion`` points to the current version, while
    ``change.restoredFromVersion`` identifies the recipe that was copied.
    """

    current_normalized, current_blocks, _current_order = _validate_recipe(
        current_recipe
    )
    parent_normalized, parent_blocks, _parent_order = _validate_recipe(parent_recipe)
    current_version = _validate_version_reference(current_version, "current_version")
    parent_version = _validate_version_reference(parent_version, "parent_version")
    if new_version is not None:
        new_version = _validate_version_reference(new_version, "new_version")

    before_hash = recipe_hash(current_normalized)
    result_recipe = _clone_json(parent_normalized)
    result_hash = recipe_hash(result_recipe)
    affected_ids: list[str] = []
    diffs: list[dict] = []
    for block_id in BLOCK_IDS:
        before = current_blocks.get(block_id)
        after = parent_blocks.get(block_id)
        if before == after:
            continue
        affected_ids.append(block_id)
        diffs.append(
            {
                "id": block_id,
                "label": BLOCK_LABELS[block_id],
                "before": _clone_json(before),
                "after": _clone_json(after),
                "changed": True,
                "changedFields": (
                    _changed_fields(before, after)
                    if isinstance(before, dict) and isinstance(after, dict)
                    else ["block"]
                ),
            }
        )
    top_level_changed = sorted(
        key
        for key in set(current_normalized).union(parent_normalized)
        if key != "blocks"
        and current_normalized.get(key) != parent_normalized.get(key)
    )
    change_core = {
        "type": "undo",
        "undoneVersion": current_version,
        "restoredFromVersion": parent_version,
        "beforeHash": before_hash,
        "resultHash": result_hash,
        "affectedIds": affected_ids,
        "diffs": diffs,
        "topLevelChangedFields": top_level_changed,
        "historyPolicy": "append-only",
    }
    change_id = "change-" + hashlib.sha256(
        canonical_json(change_core).encode("utf-8")
    ).hexdigest()[:24]
    version_value = {
        "schemaVersion": VERSION_SCHEMA_VERSION,
        # Append after current; do not re-parent history to the restored version.
        "parentVersion": current_version,
        "recipe": result_recipe,
        "change": {"id": change_id, **change_core},
    }
    if new_version is not None:
        version_value["version"] = new_version
    return version_value


def undo_edit(
    current_recipe: dict,
    parent_recipe: dict,
    current_version: Any,
    parent_version: Any,
    new_version: Any | None = None,
) -> dict:
    """Readable alias for :func:`create_undo_version`."""

    return create_undo_version(
        current_recipe,
        parent_recipe,
        current_version,
        parent_version,
        new_version,
    )


__all__ = [
    "BLOCK_IDS",
    "BLOCK_LABELS",
    "EDIT_SCHEMA_VERSION",
    "EditEngineError",
    "apply_edit_preview",
    "canonical_json",
    "confirm_edit",
    "create_undo_version",
    "parse_instruction",
    "preview_edit",
    "recipe_hash",
    "undo_edit",
]
