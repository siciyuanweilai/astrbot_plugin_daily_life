"""独立保存数字衣物，视觉资料复核不会覆盖拥有、穿着或洗护事实。"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
from typing import Any, Protocol

from ..clock import TIMEZONE
from ..models import STYLE_CATALOG_CLOTHING_KINDS


def instant(value):
    try:
        parsed = datetime.datetime.fromisoformat(str(value or ""))
        return (
            parsed.astimezone(TIMEZONE).replace(tzinfo=None)
            if parsed.tzinfo
            else parsed
        )
    except (ValueError, TypeError):
        return None


def unpack(value: str) -> dict:
    try:
        result = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class GarmentArchiveComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _conn: Any
    _record_wardrobe_wear_unlocked: Any
    _run_db: Any
    _synchronize_wardrobe_unlocked: Any
    _wardrobe_event_unlocked: Any
    _wardrobe_item_state_unlocked: Any
    _wardrobe_action_issue_unlocked: Any
    _prepare_outfit_action_unlocked: Any
    _style_catalog_ids: Any


class GarmentArchiveComponent:
    def __init__(self, context: GarmentArchiveComponentContext) -> None:
        self.context = context

    def _synchronize_wardrobe_unlocked(self, image_hash: str = "", *, repair_legacy: bool = False) -> None:
        """新增/重审目录时同步同图单品；历史修复只由版本迁移显式启用。"""
        rows = self.context._conn.execute(
            "SELECT id,kind,source_image_hash,last_used_at,created_at,attributes_json FROM style_catalog_items"
            + (" WHERE source_image_hash=?" if image_hash else ""),
            (image_hash,) if image_hash else (),
        ).fetchall()
        groups: dict[str, set[str]] = {}
        for row in rows:
            groups.setdefault(row["source_image_hash"], set()).add(row["kind"])
        units = {
            row["unit_id"]: dict(row)
            for row in self.context._conn.execute("SELECT * FROM wardrobe_units")
        }
        legacy_units = {
            key
            for key, unit in units.items()
            if repair_legacy and unit["ownership"] == "candidate"
            and unit["revision"] == 0
            and unit["condition"] == "clean"
            and not unit["acquired_at"]
            and not unit["changed_at"]
            and not unpack(unit["payload_json"])
        }
        recoveries: dict[str, tuple[tuple[bool, bool], dict]] = {}
        care_conditions = {"clean", "worn", "dirty", "washing", "drying", "stored"}
        links = {
            (row[0], row[1])
            for row in self.context._conn.execute(
                "SELECT catalog_id,unit_id FROM wardrobe_links"
            )
        }
        for row in rows:
            if row["kind"] not in STYLE_CATALOG_CLOTHING_KINDS:
                continue
            attributes = unpack(row["attributes_json"])
            saved = attributes.get("wardrobe")
            saved = saved if isinstance(saved, dict) else {}
            # 确认入库后才建立拥有关系；生成的候选方案仍需复核。
            admitted = (
                not attributes.get("wardrobe_job")
                or saved.get("ownership") == "owned"
                or bool(row["last_used_at"])
            )
            saved_units = (
                {
                    unit["unit_id"]: unit
                    for unit in saved.get("units", [])
                    if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str)
                }
                if isinstance(saved.get("units"), list)
                else {}
            )
            piece_roles = groups[row["source_image_hash"]] | {
                role
                for role in ("top", "bottom")
                if row["source_image_hash"] + ":" + role in saved_units
            }
            roles = (
                [role for role in ("top", "bottom") if role in piece_roles]
                if row["kind"] == "outfit" and {"top", "bottom"} & piece_roles
                else [row["kind"]]
            )
            old_key = row["source_image_hash"] + ":outfit"
            # 套装与同图单品共用实物；连身衣只有一个主体单品也不能另算一件。
            # 分批识别出组成时，新单品继承已有记录，已有单品保留自己的洗护事实。
            parents = (
                [
                    units[key]
                    for catalog_id, key in links
                    if catalog_id == row["id"] and key in units
                ]
                if row["kind"] == "outfit" and roles != ["outfit"]
                else []
            )
            parent = max(
                parents, key=lambda unit: unit.get("changed_at", ""), default=None
            )
            for role in roles:
                key = row["source_image_hash"] + ":" + role
                if key not in units:
                    # 仅在本地没有对应事实时，恢复该单品的准确事实。
                    facts = saved_units.get(key, saved)
                    condition = facts.get("condition", "clean")
                    if (
                        not isinstance(condition, str)
                        or condition not in care_conditions
                    ):
                        condition = "clean"
                    try:
                        revision = max(0, int(facts.get("revision", 0)))
                    except (ValueError, TypeError, OverflowError):
                        revision = 0
                    owned = admitted or facts.get("ownership") == "owned"
                    acquired_at = (
                        str(facts.get("acquired_at") or row["created_at"] or "")
                        if owned
                        else ""
                    )
                    unit = {
                        "ownership": "owned" if owned else "candidate",
                        "condition": condition,
                        "acquired_at": acquired_at,
                        "changed_at": str(facts.get("changed_at") or acquired_at),
                        "revision": revision,
                        "payload_json": encode(
                            facts.get("payload")
                            if isinstance(facts.get("payload"), dict)
                            else {}
                        ),
                    }
                    if parent and key not in saved_units:
                        unit = dict(parent)
                        if parent.get("unit_id") in legacy_units:
                            legacy_units.add(key)
                    self.context._conn.execute(
                        "INSERT INTO wardrobe_units(unit_id,ownership,condition,acquired_at,changed_at,revision,payload_json) VALUES(?,?,?,?,?,?,?)",
                        (
                            key,
                            unit["ownership"],
                            unit["condition"],
                            unit["acquired_at"],
                            unit["changed_at"],
                            unit["revision"],
                            unit["payload_json"],
                        ),
                    )
                    units[key] = unit
                if (row["id"], key) not in links:
                    self.context._conn.execute(
                        "INSERT OR IGNORE INTO wardrobe_links VALUES(?,?)",
                        (row["id"], key),
                    )
                    links.add((row["id"], key))
                facts = saved_units.get(key, saved)
                if (
                    key in legacy_units
                    and isinstance(facts.get("condition"), str)
                    and facts["condition"] in care_conditions
                ):
                    priority = (key in saved_units, row["kind"] == role)
                    if key not in recoveries or priority >= recoveries[key][0]:
                        recoveries[key] = (priority, facts)
                # 目录确认入库时同步归属，保留已有穿着与洗护事实。
                if (
                    admitted or saved_units.get(key, {}).get("ownership") == "owned"
                ) and units[key]["ownership"] == "candidate":
                    self.context._conn.execute(
                        "UPDATE wardrobe_units SET ownership='owned', acquired_at=CASE WHEN acquired_at='' THEN ? ELSE acquired_at END, revision=revision+1 WHERE unit_id=?",
                        (row["created_at"] or row["last_used_at"], key),
                    )
                    units[key]["ownership"] = "owned"
                    units[key]["revision"] += 1
            if roles != ["outfit"] and (row["id"], old_key) in links:
                self.context._conn.execute(
                    "DELETE FROM wardrobe_links WHERE catalog_id=? AND unit_id=?",
                    (row["id"], old_key),
                )
                self.context._conn.execute(
                    "DELETE FROM wardrobe_units WHERE unit_id=? AND NOT EXISTS(SELECT 1 FROM wardrobe_links WHERE unit_id=?)",
                    (old_key, old_key),
                )
                links.discard((row["id"], old_key))

        # 旧版恢复会创建空占位记录；应优先采用单品事实，而非套装摘要。
        for key, (_, facts) in recoveries.items():
            try:
                revision = max(0, int(facts.get("revision", 0)))
            except (ValueError, TypeError, OverflowError):
                revision = 0
            acquired_at = str(facts.get("acquired_at") or "")
            self.context._conn.execute(
                "UPDATE wardrobe_units SET condition=?, changed_at=CASE WHEN ?!='' THEN ? ELSE changed_at END, "
                "acquired_at=CASE WHEN ownership='owned' AND ?!='' THEN ? ELSE acquired_at END, "
                "payload_json=?, revision=MAX(revision,?)+1 WHERE unit_id=?",
                (
                    facts["condition"],
                    str(facts.get("changed_at") or ""),
                    str(facts.get("changed_at") or ""),
                    acquired_at,
                    acquired_at,
                    encode(
                        facts.get("payload")
                        if isinstance(facts.get("payload"), dict)
                        else {}
                    ),
                    revision,
                    key,
                ),
            )

    def _wardrobe_item_state_unlocked(self, item_id: int) -> dict:
        rows = self.context._conn.execute(
            "SELECT u.* FROM wardrobe_units u JOIN wardrobe_links l ON l.unit_id=u.unit_id WHERE l.catalog_id=?",
            (item_id,),
        ).fetchall()
        if not rows:
            return {}
        owned = all(row["ownership"] == "owned" for row in rows)
        conditions = {row["condition"] for row in rows}
        condition = next(
            (
                s
                for s in ("dirty", "washing", "drying", "stored", "worn", "clean")
                if s in conditions
            ),
            "clean",
        )
        return {
            "version": 2,
            "ownership": "owned" if owned else "candidate",
            "condition": condition,
            "available": owned and conditions <= {"clean", "worn", "stored"},
            "unit_ids": [row["unit_id"] for row in rows],
            "currently_worn": any(
                unpack(row["payload_json"]).get("wearing") for row in rows
            ),
            "revision": sum(row["revision"] for row in rows),
            "units": [
                {
                    "unit_id": row["unit_id"],
                    "ownership": row["ownership"],
                    "condition": row["condition"],
                    "acquired_at": row["acquired_at"],
                    "changed_at": row["changed_at"],
                    "revision": row["revision"],
                    "payload": unpack(row["payload_json"]),
                }
                for row in sorted(rows, key=lambda unit: unit["unit_id"])
            ],
        }

    def _wardrobe_action_issue_unlocked(self, raw_ids, *, retained_ids=()) -> str:
        """在世界结算事务内复核衣物，防止判断后被送洗。"""
        if not self.context._conn.execute(
            "SELECT 1 FROM wardrobe_links LIMIT 1"
        ).fetchone():
            return ""
        ids = (
            [int(v) for v in raw_ids if str(v).isdigit()]
            if isinstance(raw_ids, (list, tuple))
            else []
        )
        retained_units = {
            row[0]
            for item_id in retained_ids
            for row in self.context._conn.execute(
                "SELECT unit_id FROM wardrobe_links WHERE catalog_id=?", (item_id,)
            )
        }
        units = {
            row["unit_id"]: row
            for item_id in ids
            for row in self.context._conn.execute(
                "SELECT u.* FROM wardrobe_units u JOIN wardrobe_links l "
                "ON l.unit_id=u.unit_id WHERE l.catalog_id=?", (item_id,)
            )
        }
        # 待洗但仍穿在身上的衣物可以继续穿；换鞋或拿包不等于重新穿回它。
        # 正在清洗、晾晒或尚未拥有的物品仍不能进入新的穿着回执。
        def can_wear(unit):
            if unit["ownership"] != "owned":
                return False
            if unit["condition"] in {"clean", "worn", "stored"}:
                return True
            return (
                unit["condition"] == "dirty"
                and unit["unit_id"] in retained_units
                and bool(unpack(unit["payload_json"]).get("wearing"))
            )

        if not units or any(not can_wear(unit) for unit in units.values()):
            return "所选衣物尚未拥有或正在洗护，换装回执未生效"
        return ""

    def _prepare_outfit_action_unlocked(self, day, action):
        """按最新穿着合并动作引用，并在事务内复核可穿性。"""
        from ..life.wardrobe import (
            merge_outfit_catalog_references,
            outfit_action_components,
        )

        if action.action_type != "change_outfit":
            return action, ""
        # 已结算的动作只重放回执，不因之后送洗而重新拒绝原动作。
        if isinstance(unpack(day.meta.get("life_action_settlements")).get(action.action_id), dict):
            return action, ""
        prepared = copy.deepcopy(action)
        previous = self.context._style_catalog_ids(
            str(day.meta.get("style_catalog_reference_ids") or "").split(",")
        )
        raw = prepared.payload.get("catalog_reference_ids") or []
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        selected = self.context._style_catalog_ids(values)
        lookup_ids = list(dict.fromkeys(previous + selected))
        kinds = {}
        if lookup_ids:
            placeholders = ",".join("?" for _ in lookup_ids)
            kinds = dict(self.context._conn.execute(
                f"SELECT id,kind FROM style_catalog_items WHERE id IN ({placeholders})",
                lookup_ids,
            ))
        components, full_change = outfit_action_components(
            day.meta.get("outfit_components"),
            prepared.payload.get("outfit_components"),
            prepared.target,
        )
        references = merge_outfit_catalog_references(
            previous, selected, kinds, components, partial=not full_change,
        )
        prepared.payload["catalog_reference_ids"] = references
        issue = self.context._wardrobe_action_issue_unlocked(
            references, retained_ids=previous
        )
        return prepared, issue

    async def prepare_outfit_action(self, day, action):
        """在线程中检查动作；最终提交仍须在事务内重新复核。"""
        return await self.context._run_db(
            lambda: self.context._prepare_outfit_action_unlocked(day, action)
        )

    async def get_wardrobe_snapshot(self) -> dict:
        from ..life.stylememory import PERSONAL_STYLE_KINDS
        def read():
            ids = [
                row[0]
                for row in self.context._conn.execute(
                    "SELECT DISTINCT catalog_id FROM wardrobe_links"
                )
            ]
            profile = self.context._conn.execute(
                "SELECT * FROM wardrobe_profile WHERE name='self'"
            ).fetchone()
            events = self.context._conn.execute(
                "SELECT * FROM wardrobe_events ORDER BY occurred_at DESC,event_id DESC LIMIT 80"
            ).fetchall()
            wear_history = self.context._conn.execute(
                "SELECT event_id,kind,occurred_at,payload_json FROM wardrobe_events "
                "WHERE kind='wear' ORDER BY occurred_at DESC,event_id DESC LIMIT 160"
            ).fetchall()
            feedback_history = self.context._conn.execute(
                "SELECT event_id,kind,occurred_at,payload_json FROM wardrobe_events "
                "WHERE kind='feedback' ORDER BY occurred_at DESC,event_id DESC LIMIT 80"
            ).fetchall()
            catalog_feedback = self.context._conn.execute(
                "SELECT f.*,i.description,i.kind AS category FROM style_catalog_feedback f "
                "JOIN style_catalog_items i ON i.id=f.item_id ORDER BY f.id DESC LIMIT 80"
            ).fetchall()
            jobs = self.context._conn.execute(
                "SELECT * FROM wardrobe_jobs WHERE status NOT IN ('completed','cancelled') ORDER BY CASE WHEN status IN ('failed','uncertain') THEN 1 ELSE 0 END,updated_at DESC LIMIT 50"
            ).fetchall()
            recent = self.context._conn.execute(
                "SELECT job_id,status,updated_at,payload_json,error FROM wardrobe_jobs ORDER BY updated_at DESC LIMIT 16"
            ).fetchall()
            style_history = [row for kind in PERSONAL_STYLE_KINDS for row in self.context._conn.execute(
                "SELECT event_id,kind,occurred_at,payload_json FROM wardrobe_events "
                "WHERE kind='style_use' AND json_extract(payload_json,'$.category')=? "
                "ORDER BY occurred_at DESC,event_id DESC LIMIT 60", (kind,)).fetchall()]
            style_history.sort(key=lambda row: (row["occurred_at"], row["event_id"]), reverse=True)
            return {
                "style_history": [{"event_id": r["event_id"], "kind": r["kind"],
                                   "occurred_at": r["occurred_at"], "payload": unpack(r["payload_json"])}
                                  for r in style_history],
                "items": {str(i): self.context._wardrobe_item_state_unlocked(i) for i in ids},
                "profile": unpack(profile["payload_json"]) if profile else {},
                "profile_revision": profile["revision"] if profile else 0,
                "events": [
                    {**dict(r), "payload": unpack(r["payload_json"])} for r in events
                ],
                "wear_history": [
                    {"event_id": r["event_id"], "kind": r["kind"], "occurred_at": r["occurred_at"],
                     "payload": unpack(r["payload_json"])} for r in wear_history
                ],
                "feedback_history": [
                    {"event_id": r["event_id"], "kind": r["kind"], "occurred_at": r["occurred_at"],
                     "payload": unpack(r["payload_json"])} for r in feedback_history
                ] + [
                    {"event_id": "catalog-feedback:" + str(r["id"]), "kind": "feedback",
                     "occurred_at": r["created_at"] + "+00:00",
                     "payload": {"item_ids": [r["item_id"]], "text": r["feedback"],
                                 "outfit": r["description"], "origin": "user_feedback",
                                 "categories": [r["category"]], "sentiment": r["sentiment"]}} for r in catalog_feedback
                ],
                "jobs": [
                    {
                        **dict(r),
                        "payload": unpack(r["payload_json"]),
                        "progress": unpack(r["progress_json"]),
                    }
                    for r in jobs
                ],
                "recent_jobs": [
                    {"job_id": r["job_id"], "status": r["status"],
                     "updated_at": r["updated_at"], "payload": {
                         k: v for k, v in unpack(r["payload_json"]).items()
                         if k in {"requirement", "motive", "idea_key", "reason", "automatic", "requested_at"}
                     },
                     "error": r["error"]}
                    for r in recent
                ],
            }

        return await self.context._run_db(read)

    def _wardrobe_event_unlocked(self, event_id, kind, at, source, payload) -> bool:
        return bool(
            self.context._conn.execute(
                "INSERT OR IGNORE INTO wardrobe_events VALUES(?,?,?,?,?)",
                (event_id, kind, at, source, encode(payload)),
            ).rowcount
        )

    async def adopt_wardrobe_items(
        self, ids: list[int], *, event_id: str, at: str, reason: str
    ) -> bool:
        if not ids or not reason.strip():
            return False

        def write():
            self.context._conn.execute("BEGIN IMMEDIATE")
            try:
                records = [
                    self.context._conn.execute(
                        "SELECT status,kind,confidence FROM style_catalog_items WHERE id=?",
                        (i,),
                    ).fetchone()
                    for i in ids
                ]
                if any(
                    r is None
                    or r["status"] != "active"
                    or r["confidence"] < 0.72
                    or r["kind"] not in STYLE_CATALOG_CLOTHING_KINDS
                    for r in records
                ):
                    self.context._conn.rollback()
                    return False
                if not self.context._wardrobe_event_unlocked(
                    event_id,
                    "adopt",
                    at,
                    "digital_wardrobe",
                    {"item_ids": ids, "reason": reason},
                ):
                    self.context._conn.rollback()
                    return False
                for i in ids:
                    self.context._conn.execute(
                        "UPDATE wardrobe_units SET ownership='owned', acquired_at=?, changed_at=?, revision=revision+1 WHERE ownership='candidate' AND unit_id IN (SELECT unit_id FROM wardrobe_links WHERE catalog_id=?)",
                        (at, at, i),
                    )
                self.context._conn.commit()
                return True
            except BaseException:
                self.context._conn.rollback()
                raise

        return await self.context._run_db(write)

    def _record_style_use_unlocked(self, day, source: str) -> bool:
        from ..life.stylememory import PERSONAL_STYLE_KINDS, STYLE_USAGE_KEY, style_values, usage_context
        latest = dict(self.context._conn.execute("SELECT key,value FROM day_meta WHERE date=?", (day.date,)))
        fields = [STYLE_USAGE_KEY, "outfit_fact_confirmed_at",
                  *(field for kind in PERSONAL_STYLE_KINDS for field in (kind, kind + "_style"))]
        if any(str(latest.get(key) or "") != str(day.meta.get(key) or "") for key in fields):
            return False
        context, changed = usage_context(day.meta), False
        for kind, value in style_values(day.meta).items():
            confirmed = context.get(kind) or {}
            if any(confirmed.get(key, "") != text for key, text in value.items()):
                confirmed = {}
            at = confirmed.get("changed_at") or day.meta.get("outfit_fact_confirmed_at")
            if not instant(at):
                continue
            row = self.context._conn.execute(
                "SELECT occurred_at,payload_json FROM wardrobe_events WHERE kind='style_use' "
                "AND json_extract(payload_json,'$.category')=? ORDER BY occurred_at DESC LIMIT 1", (kind,)
            ).fetchone()
            previous = unpack(row["payload_json"]) if row else {}
            if row and (instant(at) <= instant(row["occurred_at"]) or
                        all(previous.get(key, "") == text for key, text in value.items())):
                continue
            if not row and not any(value.values()) and not confirmed:
                continue
            payload = {**value, "category": kind, "day": day.date,
                       "changed_at": confirmed.get("changed_at", ""),
                       "choice_reason": confirmed.get("reason", ""),
                       "choice_origin": confirmed.get("origin", "observed"),
                       "transition": confirmed.get("transition", "observed")}
            receipt_id = "style-use:" + hashlib.sha256(encode([kind, at, value]).encode()).hexdigest()[:24]
            changed |= self.context._wardrobe_event_unlocked(receipt_id, "style_use", str(at), source, payload)
        return changed

    def _record_wardrobe_wear_unlocked(
        self, day, *, event_id: str = "archive", source: str = "outfit_receipt"
    ) -> bool:
        style_changed = self._record_style_use_unlocked(day, source) if event_id else False
        raw = str(day.meta.get("style_catalog_reference_ids") or "")
        ids = [int(v) for v in raw.split(",") if v.strip().isdigit()]
        at = str(day.meta.get("outfit_fact_confirmed_at") or "")
        if not at or not event_id or (not ids and not day.outfit.strip()):
            return style_changed
        # 同一穿着回执可能来自换装结算、外观更新和定时核对，只计一次经历。
        receipt_id = (
            "wear:"
            + hashlib.sha256(encode([day.date, at, sorted(ids), day.outfit if not ids else ""]).encode()).hexdigest()[
                :24
            ]
        )

        # 后到的旧回执不能把当前穿着退回去。
        latest = dict(
            self.context._conn.execute(
                "SELECT key,value FROM day_meta WHERE date=? AND key IN ('style_catalog_reference_ids','outfit_fact_confirmed_at')",
                (day.date,),
            )
        )
        if (
            latest.get("style_catalog_reference_ids", "") != raw
            or latest.get("outfit_fact_confirmed_at") != at
        ):
            return style_changed
        units = {
            r[0]
            for i in ids
            for r in self.context._conn.execute(
                "SELECT unit_id FROM wardrobe_links WHERE catalog_id=?", (i,)
            )
        }
        if ids and not units:
            return style_changed
        prior = self.context._conn.execute(
            "SELECT payload_json FROM wardrobe_events WHERE kind='wear' ORDER BY occurred_at DESC LIMIT 1"
        ).fetchone()
        if prior:
            previous = unpack(prior[0])
            previous_units = {
                r[0]
                for i in previous.get("item_ids", [])
                for r in self.context._conn.execute(
                    "SELECT unit_id FROM wardrobe_links WHERE catalog_id=?",
                    (i,),
                )
            }
            if (previous.get("day") == day.date and units == previous_units
                    and (units or previous.get("outfit") == day.outfit)):
                return style_changed
        if not self.context._wardrobe_event_unlocked(
            receipt_id,
            "wear",
            at,
            source,
            {"item_ids": ids, "outfit": day.outfit, "day": day.date,
             "choice_reason": str(day.meta.get("outfit_reason") or "")[:500],
             "scene_category": str(day.meta.get("outfit_scene_category") or ""),
             "choice_context": {"fact_source": str(day.meta.get("outfit_fact_source") or ""),
                                "decision": str(day.meta.get("outfit_decision") or "")}},
        ):
            return style_changed
        newest = self.context._conn.execute(
            "SELECT MAX(occurred_at) FROM wardrobe_events WHERE kind='wear'"
        ).fetchone()[0]
        if newest and newest > at:
            return True
        units = {
            r[0]
            for i in ids
            for r in self.context._conn.execute(
                "SELECT unit_id FROM wardrobe_links WHERE catalog_id=?", (i,)
            )
        }
        for row in self.context._conn.execute(
            "SELECT unit_id,payload_json FROM wardrobe_units"
        ).fetchall():
            data = unpack(row["payload_json"])
            wearing = row["unit_id"] in units
            if bool(data.get("wearing")) != wearing:
                data["wearing"] = wearing
                self.context._conn.execute(
                    "UPDATE wardrobe_units SET payload_json=?,revision=revision+1 WHERE unit_id=?",
                    (encode(data), row["unit_id"]),
                )
        self.context._conn.execute(
            "UPDATE wardrobe_units SET condition='clean',changed_at=?,revision=revision+1 WHERE condition='worn'",
            (at,),
        )
        for key in units:
            self.context._conn.execute(
                "UPDATE wardrobe_units SET ownership='owned',condition='worn',acquired_at=CASE WHEN acquired_at='' THEN ? ELSE acquired_at END,changed_at=?,revision=revision+1 WHERE unit_id=? AND condition IN ('clean','worn','stored')",
                (at, at, key),
            )
        return True

    async def record_wardrobe_wear(
        self, day, *, event_id: str, source: str = "outfit_receipt"
    ) -> bool:
        def write():
            self.context._conn.execute("BEGIN IMMEDIATE")
            try:
                changed = self.context._record_wardrobe_wear_unlocked(
                    day, event_id=event_id, source=source
                )
                self.context._conn.commit()
                return changed
            except BaseException:
                self.context._conn.rollback()
                raise

        return await self.context._run_db(write)

    async def record_wardrobe_feedback(
        self, *, event_id: str, item_ids: list[int], text: str, at: str, scope: str
    ) -> bool:
        def write():
            return self.context._wardrobe_event_unlocked(
                event_id,
                "feedback",
                at,
                scope,
                {"item_ids": item_ids, "text": text[:1000], "origin": "user_feedback"},
            )

        return await self.context._run_db(write)

    async def save_wardrobe_review(
        self, payload: dict, *, expected_revision: int, at: str,
        generation: dict | None = None,
    ) -> bool:
        def write():
            self.context._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.context._conn.execute(
                    "SELECT revision FROM wardrobe_profile WHERE name='self'"
                ).fetchone()
                if (row["revision"] if row else 0) != expected_revision:
                    self.context._conn.rollback()
                    return False
                profile = copy.deepcopy(payload)
                choice = profile.get("acquisition")
                if generation and isinstance(choice, dict):
                    jobs = self.context._conn.execute(
                        "SELECT job_id,status,payload_json,progress_json FROM wardrobe_jobs"
                    ).fetchall()
                    existing = next((r for r in jobs if r["job_id"] == generation["job_id"]), None)
                    waiting = any(
                        unpack(r["payload_json"]).get("automatic")
                        and r["status"] not in {"completed", "failed", "cancelled"}
                        for r in jobs
                    )
                    since = (instant(at) - datetime.timedelta(hours=24)).isoformat(sep=" ")
                    from ..life.wardrobefit import generation_conflicts
                    recent = any(
                        generation_conflicts(generation["payload"], unpack(r["payload_json"]))
                        and (unpack(r["progress_json"]).get("submitted_at")
                             or unpack(r["payload_json"]).get("requested_at", "")) >= since
                        for r in jobs
                    )
                    if existing:
                        choice["status"] = (
                            "already_prepared" if existing["status"] == "completed"
                            else "needs_attention" if existing["status"] in {"failed", "uncertain", "cancelled"}
                            else "queued"
                        )
                        choice["job_id"] = existing["job_id"]
                    elif waiting:
                        choice["status"] = "waiting"
                    elif recent:
                        choice["status"] = "cooldown"
                    else:
                        self.context._conn.execute(
                            "INSERT INTO wardrobe_jobs(job_id,updated_at,payload_json) VALUES(?,?,?)",
                            (generation["job_id"], at, encode(generation["payload"])),
                        )
                        choice.update(status="queued", job_id=generation["job_id"])
                if isinstance(choice, dict):
                    choice["reviewed_at"] = at
                    history = profile.get("acquisition_history") or []
                    def comparable(item):
                        return {k: v for k, v in item.items() if k != "reviewed_at"}
                    if not history or comparable(history[-1]) != comparable(choice):
                        history = [*history, copy.deepcopy(choice)]
                    profile["acquisition_history"] = history[-12:]
                self.context._conn.execute(
                    "INSERT INTO wardrobe_profile VALUES('self',?,?,?) ON CONFLICT(name) DO UPDATE SET revision=excluded.revision,updated_at=excluded.updated_at,payload_json=excluded.payload_json WHERE wardrobe_profile.revision=?",
                    (expected_revision + 1, at, encode(profile), expected_revision),
                )
                self.context._conn.commit()
                return True
            except BaseException:
                self.context._conn.rollback()
                raise

        return await self.context._run_db(write)

    async def start_wardrobe_care(
        self,
        ids: list[int],
        *,
        decision: str,
        event_id: str,
        at: str,
        observer: str,
        duration_minutes: int,
        reason: str,
        drying_minutes: int = 120,
    ) -> bool:
        transitions = {
            "dirty": {"clean", "worn"},
            "washing": {"dirty"},
            "stored": {"clean"},
            "clean": {"stored"},
        }
        if decision not in transitions or not ids or not reason:
            return False

        def write():
            self.context._conn.execute("BEGIN IMMEDIATE")
            try:
                keys = {
                    r[0]
                    for i in ids
                    for r in self.context._conn.execute(
                        "SELECT unit_id FROM wardrobe_links WHERE catalog_id=?", (i,)
                    )
                }
                rows = [
                    self.context._conn.execute(
                        "SELECT * FROM wardrobe_units WHERE unit_id=?", (k,)
                    ).fetchone()
                    for k in keys
                ]
                if not rows or any(
                    r["ownership"] != "owned"
                    or r["condition"] not in transitions[decision]
                    or (
                        decision in {"washing", "stored"}
                        and unpack(r["payload_json"]).get("wearing")
                    )
                    for r in rows
                ):
                    self.context._conn.rollback()
                    return False
                if not self.context._wardrobe_event_unlocked(
                    event_id,
                    "care_start",
                    at,
                    "digital_care",
                    {"item_ids": ids, "decision": decision, "reason": reason},
                ):
                    self.context._conn.rollback()
                    return False
                for row in rows:
                    data = unpack(row["payload_json"])
                    if decision == "washing":
                        data["care"] = {
                            "observer": observer,
                            "last_observed": at,
                            "seconds": 0,
                            "wash_seconds": max(5, min(120, duration_minutes)) * 60,
                            "dry_seconds": max(30, min(1440, drying_minutes)) * 60,
                            "id": event_id,
                        }
                    self.context._conn.execute(
                        "UPDATE wardrobe_units SET condition=?,changed_at=?,payload_json=?,revision=revision+1 WHERE unit_id=?",
                        (decision, at, encode(data), row["unit_id"]),
                    )
                self.context._conn.commit()
                return True
            except BaseException:
                self.context._conn.rollback()
                raise

        return await self.context._run_db(write)

    async def advance_wardrobe_care(
        self, *, now: datetime.datetime, observer: str
    ) -> int:
        at = now.isoformat(sep=" ")

        def write():
            changed = 0
            self.context._conn.execute("BEGIN IMMEDIATE")
            try:
                for row in self.context._conn.execute(
                    "SELECT * FROM wardrobe_units WHERE condition IN ('washing','drying')"
                ).fetchall():
                    data = unpack(row["payload_json"])
                    care = data.get("care") or {}
                    previous = instant(care.get("last_observed"))
                    seconds = (now - previous).total_seconds() if previous else 0
                    if care.get("observer") == observer and 0 <= seconds <= 180:
                        care["seconds"] = float(care.get("seconds") or 0) + seconds
                    care.update(observer=observer, last_observed=at)
                    condition = row["condition"]
                    required = care.get(
                        "wash_seconds" if condition == "washing" else "dry_seconds",
                        1800,
                    )
                    if care.get("seconds", 0) >= required:
                        condition = "drying" if condition == "washing" else "clean"
                        care["seconds"] = 0
                        changed += 1
                        self.context._wardrobe_event_unlocked(
                            f"care:{care.get('id')}:{row['unit_id']}:{condition}",
                            "care_receipt",
                            at,
                            "observed_digital_execution",
                            {"unit_id": row["unit_id"], "condition": condition},
                        )
                    data["care"] = care
                    self.context._conn.execute(
                        "UPDATE wardrobe_units SET condition=?,changed_at=?,payload_json=?,revision=revision+1 WHERE unit_id=?",
                        (condition, at, encode(data), row["unit_id"]),
                    )
                self.context._conn.commit()
                return changed
            except BaseException:
                self.context._conn.rollback()
                raise

        return await self.context._run_db(write)

    async def enqueue_wardrobe_job(
        self, job_id: str, payload: dict, *, at: str
    ) -> dict:
        def write():
            self.context._conn.execute(
                "INSERT OR IGNORE INTO wardrobe_jobs(job_id,updated_at,payload_json) VALUES(?,?,?)",
                (job_id, at, encode(payload)),
            )
            return dict(
                self.context._conn.execute(
                    "SELECT * FROM wardrobe_jobs WHERE job_id=?", (job_id,)
                ).fetchone()
            )

        return await self.context._run_db(write)

    async def wardrobe_auto_requested_since(
        self, since: str, *, exclude: str = "", payload: dict | None = None
    ) -> bool:
        from ..life.wardrobefit import generation_conflicts

        def read():
            return any(
                row[0] != exclude
                and generation_conflicts(payload or {}, unpack(row[1]))
                and (
                    unpack(row[2]).get("submitted_at")
                    or unpack(row[1]).get("requested_at", "")
                )
                >= since
                for row in self.context._conn.execute(
                    "SELECT job_id,payload_json,progress_json FROM wardrobe_jobs"
                )
            )

        return await self.context._run_db(read)

    async def claim_wardrobe_job(
        self, job_id: str, owner: str, *, now: datetime.datetime
    ) -> dict | None:
        at = now.isoformat(sep=" ")
        until = (now + datetime.timedelta(hours=6)).isoformat(sep=" ")

        def write():
            cursor = self.context._conn.execute(
                "UPDATE wardrobe_jobs SET lease_owner=?,lease_until=?,updated_at=? WHERE job_id=? AND status NOT IN ('completed','failed','cancelled','uncertain') AND (lease_owner='' OR lease_until<?) AND (next_at='' OR next_at<=?)",
                (owner, until, at, job_id, at, at),
            )
            if not cursor.rowcount:
                return None
            row = dict(
                self.context._conn.execute(
                    "SELECT * FROM wardrobe_jobs WHERE job_id=?", (job_id,)
                ).fetchone()
            )
            row["payload"] = unpack(row["payload_json"])
            row["progress"] = unpack(row["progress_json"])
            return row

        return await self.context._run_db(write)

    async def update_wardrobe_job(
        self,
        job_id: str,
        owner: str,
        *,
        status: str,
        progress: dict,
        at: str,
        error: str = "",
        release: bool = False,
    ) -> bool:
        if status not in {
            "planned",
            "generating",
            "recognizing",
            "pending",
            "completed",
            "failed",
            "cancelled",
            "uncertain",
        }:
            raise ValueError("无效衣橱任务状态")

        def write():
            next_at = (
                (instant(at) + datetime.timedelta(minutes=2)).isoformat(sep=" ")
                if status == "pending"
                else ""
            )
            return bool(
                self.context._conn.execute(
                    "UPDATE wardrobe_jobs SET status=?,progress_json=?,updated_at=?,error=?,next_at=?,lease_owner=?,lease_until=? WHERE job_id=? AND lease_owner=?",
                    (
                        status,
                        encode(progress),
                        at,
                        error[:240],
                        next_at,
                        "" if release else owner,
                        ""
                        if release
                        else (instant(at) + datetime.timedelta(hours=6)).isoformat(
                            sep=" "
                        ),
                        job_id,
                        owner,
                    ),
                ).rowcount
            )

        return await self.context._run_db(write)

    async def recover_wardrobe_jobs(self, owner: str) -> None:
        def write():
            rows = self.context._conn.execute(
                "SELECT * FROM wardrobe_jobs WHERE lease_owner!='' AND lease_owner!=?",
                (owner,),
            ).fetchall()
            for row in rows:
                progress = unpack(row["progress_json"])
                current = progress.get("current") or {}
                status = (
                    "pending"
                    if current.get("task_id") or current.get("path")
                    else "uncertain"
                    if current.get("submitting")
                    else row["status"]
                )
                self.context._conn.execute(
                    "UPDATE wardrobe_jobs SET status=?,lease_owner='',lease_until='' WHERE job_id=? AND lease_owner=?",
                    (status, row["job_id"], row["lease_owner"]),
                )

        await self.context._run_db(write)

    async def release_wardrobe_jobs(self, owner: str) -> None:
        def write():
            self.context._conn.execute(
                "UPDATE wardrobe_jobs SET lease_owner='',lease_until='' WHERE lease_owner=?",
                (owner,),
            )

        await self.context._run_db(write)
