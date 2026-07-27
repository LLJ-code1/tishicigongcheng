"""Recipe-v2 role cards and explicit local-edit conflict checks."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
MAX_ROLES = 12
MAX_RELATIONSHIPS = 48


class RoleCardError(ValueError):
    pass


def _text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise RoleCardError(f"{label} 无效")
    return value.strip()


def normalize_role_cards(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion", "roles", "relationships"
    }:
        raise RoleCardError("roleCards 字段无效")
    if value["schemaVersion"] != 1:
        raise RoleCardError("roleCards schemaVersion 不受支持")
    roles = value["roles"]
    relationships = value["relationships"]
    if not isinstance(roles, list) or not roles or len(roles) > MAX_ROLES:
        raise RoleCardError("角色卡必须包含 1 至 12 个角色")
    normalized_roles = []
    role_ids = set()
    for role in roles:
        if not isinstance(role, dict) or set(role) != {"id", "name", "tags", "locked"}:
            raise RoleCardError("角色字段无效")
        role_id = role["id"]
        if not isinstance(role_id, str) or not SAFE_ID.fullmatch(role_id) or role_id in role_ids:
            raise RoleCardError("角色 ID 无效或重复")
        tags = role["tags"]
        if not isinstance(tags, list) or len(tags) > 64:
            raise RoleCardError("角色标签无效")
        normalized_tags = [_text(tag, "角色标签", 512) for tag in tags]
        if len(set(normalized_tags)) != len(normalized_tags):
            raise RoleCardError("角色标签重复")
        if not isinstance(role["locked"], bool):
            raise RoleCardError("角色锁定状态无效")
        role_ids.add(role_id)
        normalized_roles.append(
            {
                "id": role_id,
                "name": _text(role["name"], "角色名称", 160),
                "tags": normalized_tags,
                "locked": role["locked"],
            }
        )
    if not isinstance(relationships, list) or len(relationships) > MAX_RELATIONSHIPS:
        raise RoleCardError("互动关系无效")
    normalized_relationships = []
    relationship_ids = set()
    for relationship in relationships:
        if not isinstance(relationship, dict) or set(relationship) != {
            "id", "fromRoleId", "toRoleId", "kind", "description", "locked"
        }:
            raise RoleCardError("互动关系字段无效")
        relation_id = relationship["id"]
        if (
            not isinstance(relation_id, str)
            or not SAFE_ID.fullmatch(relation_id)
            or relation_id in relationship_ids
        ):
            raise RoleCardError("互动关系 ID 无效或重复")
        if (
            relationship["fromRoleId"] not in role_ids
            or relationship["toRoleId"] not in role_ids
            or relationship["fromRoleId"] == relationship["toRoleId"]
        ):
            raise RoleCardError("互动关系角色引用无效")
        if not isinstance(relationship["locked"], bool):
            raise RoleCardError("互动关系锁定状态无效")
        relationship_ids.add(relation_id)
        normalized_relationships.append(
            {
                "id": relation_id,
                "fromRoleId": relationship["fromRoleId"],
                "toRoleId": relationship["toRoleId"],
                "kind": _text(relationship["kind"], "互动关系类型", 80),
                "description": _text(relationship["description"], "互动关系描述", 1_000),
                "locked": relationship["locked"],
            }
        )
    return {
        "schemaVersion": 1,
        "roles": normalized_roles,
        "relationships": normalized_relationships,
    }


def preview_local_role_edit(
    role_cards: Any, role_id: Any, patch: Any, *, confirm_affected: bool = False
) -> dict:
    normalized = normalize_role_cards(role_cards)
    if not isinstance(role_id, str) or not SAFE_ID.fullmatch(role_id):
        raise RoleCardError("目标角色 ID 无效")
    if not isinstance(patch, dict) or not patch or set(patch) - {"name", "tags"}:
        raise RoleCardError("角色局部修改只允许 name 或 tags")
    roles = {role["id"]: role for role in normalized["roles"]}
    target = roles.get(role_id)
    if target is None:
        raise RoleCardError("目标角色不存在")
    if target["locked"]:
        raise RoleCardError("目标角色已锁定")
    candidate = deepcopy(target)
    if "name" in patch:
        candidate["name"] = _text(patch["name"], "角色名称", 160)
    if "tags" in patch:
        candidate["tags"] = normalize_role_cards(
            {"schemaVersion": 1, "roles": [{**candidate, "tags": patch["tags"]}], "relationships": []}
        )["roles"][0]["tags"]
    affected = [
        relationship
        for relationship in normalized["relationships"]
        if role_id in {relationship["fromRoleId"], relationship["toRoleId"]}
    ]
    locked_others = sorted(other_id for other_id in roles if other_id != role_id)
    conflict_required = bool(affected)
    if conflict_required and not confirm_affected:
        return {
            "applied": False,
            "requiresConfirmation": True,
            "targetRoleId": role_id,
            "lockedRoleIds": locked_others,
            "affectedRelationships": affected,
        }
    for index, role in enumerate(normalized["roles"]):
        if role["id"] == role_id:
            normalized["roles"][index] = candidate
            break
    return {
        "applied": True,
        "requiresConfirmation": False,
        "targetRoleId": role_id,
        "roleCards": normalized,
        "lockedRoleIds": locked_others,
        "affectedRelationships": affected,
    }


def preview_relationship_edit(
    role_cards: Any,
    relationship_id: Any,
    patch: Any,
    *,
    confirm_affected: bool = False,
) -> dict:
    """Preview an explicit relationship-only edit without changing roles."""

    normalized = normalize_role_cards(role_cards)
    if not isinstance(relationship_id, str) or not SAFE_ID.fullmatch(relationship_id):
        raise RoleCardError("关系 ID 无效")
    if not isinstance(patch, dict) or not patch or set(patch) - {"kind", "description"}:
        raise RoleCardError("关系修改只允许 kind 或 description")
    target = next(
        (item for item in normalized["relationships"] if item["id"] == relationship_id),
        None,
    )
    if target is None:
        raise RoleCardError("目标关系不存在")
    if target["locked"]:
        raise RoleCardError("目标关系已锁定")
    candidate = deepcopy(target)
    if "kind" in patch:
        candidate["kind"] = _text(patch["kind"], "互动关系类型", 80)
    if "description" in patch:
        candidate["description"] = _text(patch["description"], "互动关系描述", 1_000)
    affected_role_ids = [target["fromRoleId"], target["toRoleId"]]
    if not confirm_affected:
        return {
            "applied": False,
            "requiresConfirmation": True,
            "relationshipId": relationship_id,
            "affectedRoleIds": affected_role_ids,
        }
    for index, relationship in enumerate(normalized["relationships"]):
        if relationship["id"] == relationship_id:
            normalized["relationships"][index] = candidate
            break
    return {
        "applied": True,
        "requiresConfirmation": False,
        "relationshipId": relationship_id,
        "roleCards": normalized,
        "affectedRoleIds": affected_role_ids,
    }
