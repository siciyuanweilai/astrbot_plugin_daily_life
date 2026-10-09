"""独立保存数字衣物，视觉资料复核不会覆盖拥有、穿着或洗护事实。"""

from __future__ import annotations

import datetime
import json
import hashlib
from typing import Any

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


class GarmentArchiveMixin:
    def _wardrobe_bootstrap_unlocked(self) -> None:
        rows = self._conn.execute(
            "SELECT id,kind,source_image_hash,last_used_at FROM style_catalog_items"
        ).fetchall()
        groups: dict[str, set[str]] = {}
        for row in rows:
            groups.setdefault(row["source_image_hash"], set()).add(row["kind"])
        units = {
            row["unit_id"]: dict(row)
            for row in self._conn.execute("SELECT * FROM wardrobe_units")
        }
        links = {
            (row[0], row[1])
            for row in self._conn.execute(
                "SELECT catalog_id,unit_id FROM wardrobe_links"
            )
        }
        for row in rows:
            if row["kind"] not in STYLE_CATALOG_CLOTHING_KINDS:
                continue
            roles = (
                ["top", "bottom"]
                if row["kind"] == "outfit"
                and {"top", "bottom"} <= groups[row["source_image_hash"]]
                else [row["kind"]]
            )
            old_key = row["source_image_hash"] + ":outfit"
            parent = (
                units.get(old_key)
                if roles == ["top", "bottom"] and (row["id"], old_key) in links
                else None
            )
            for role in roles:
                key = row["source_image_hash"] + ":" + role
                if key not in units:
                    self._conn.execute(
                        "INSERT INTO wardrobe_units(unit_id,ownership,acquired_at,changed_at) VALUES(?,?,?,?)",
                        (
                            key,
                            "owned" if row["last_used_at"] else "candidate",
                            row["last_used_at"],
                            row["last_used_at"],
                        ),
                    )
                    units[key] = {
                        "ownership": "owned" if row["last_used_at"] else "candidate",
                        "revision": 0,
                    }
                if parent and units[key]["revision"] == 0:
                    self._conn.execute(
                        "UPDATE wardrobe_units SET ownership=?,condition=?,acquired_at=?,changed_at=?,revision=?,payload_json=? WHERE unit_id=?",
                        (
                            parent["ownership"],
                            parent["condition"],
                            parent["acquired_at"],
                            parent["changed_at"],
                            parent["revision"],
                            parent["payload_json"],
                            key,
                        ),
                    )
                    units[key] = dict(parent)
                if (row["id"], key) not in links:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO wardrobe_links VALUES(?,?)",
                        (row["id"], key),
                    )
                    links.add((row["id"], key))
                # 历史采用记录仅用于首次建立拥有事实，不覆盖洗护状态。
                if row["last_used_at"] and units[key]["ownership"] == "candidate":
                    self._conn.execute(
                        "UPDATE wardrobe_units SET ownership='owned', acquired_at=CASE WHEN acquired_at='' THEN ? ELSE acquired_at END WHERE unit_id=?",
                        (row["last_used_at"], key),
                    )
                    units[key]["ownership"] = "owned"
            if parent:
                self._conn.execute(
                    "DELETE FROM wardrobe_links WHERE catalog_id=? AND unit_id=?",
                    (row["id"], old_key),
                )
                self._conn.execute(
                    "DELETE FROM wardrobe_units WHERE unit_id=? AND NOT EXISTS(SELECT 1 FROM wardrobe_links WHERE unit_id=?)",
                    (old_key, old_key),
                )

    def _wardrobe_item_state_unlocked(self, item_id: int) -> dict:
        rows = self._conn.execute(
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
            "ownership": "owned" if owned else "candidate",
            "condition": condition,
            "available": owned and conditions <= {"clean", "worn"},
            "unit_ids": [row["unit_id"] for row in rows],
            "currently_worn": any(
                unpack(row["payload_json"]).get("wearing") for row in rows
            ),
            "revision": sum(row["revision"] for row in rows),
        }

    def _wardrobe_action_issue_unlocked(self, raw_ids) -> str:
        """在世界结算事务内复核衣物，防止判断后被送洗。"""
        self._wardrobe_bootstrap_unlocked()
        if not self._conn.execute("SELECT 1 FROM wardrobe_links LIMIT 1").fetchone():
            return ""
        ids = (
            [int(v) for v in raw_ids if str(v).isdigit()]
            if isinstance(raw_ids, (list, tuple))
            else []
        )
        states = [self._wardrobe_item_state_unlocked(i) for i in ids]
        clothes = [state for state in states if state]
        if not clothes or any(not state.get("available") for state in clothes):
            return "所选衣物尚未拥有或正在洗护，换装回执未生效"
        return ""

    async def get_wardrobe_snapshot(self) -> dict:
        def read():
            self._wardrobe_bootstrap_unlocked()
            ids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT DISTINCT catalog_id FROM wardrobe_links"
                )
            ]
            profile = self._conn.execute(
                "SELECT * FROM wardrobe_profile WHERE name='self'"
            ).fetchone()
            events = self._conn.execute(
                "SELECT * FROM wardrobe_events ORDER BY occurred_at DESC,event_id DESC LIMIT 80"
            ).fetchall()
            jobs = self._conn.execute(
                "SELECT * FROM wardrobe_jobs WHERE status NOT IN ('completed','cancelled') ORDER BY CASE WHEN status IN ('failed','uncertain') THEN 1 ELSE 0 END,updated_at DESC LIMIT 50"
            ).fetchall()
            return {
                "items": {str(i): self._wardrobe_item_state_unlocked(i) for i in ids},
                "profile": unpack(profile["payload_json"]) if profile else {},
                "profile_revision": profile["revision"] if profile else 0,
                "events": [
                    {**dict(r), "payload": unpack(r["payload_json"])} for r in events
                ],
                "jobs": [
                    {
                        **dict(r),
                        "payload": unpack(r["payload_json"]),
                        "progress": unpack(r["progress_json"]),
                    }
                    for r in jobs
                ],
            }

        return await self._run_db(read)

    def _wardrobe_event_unlocked(self, event_id, kind, at, source, payload) -> bool:
        return bool(
            self._conn.execute(
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
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._wardrobe_bootstrap_unlocked()
                records = [
                    self._conn.execute(
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
                    self._conn.rollback()
                    return False
                if not self._wardrobe_event_unlocked(
                    event_id,
                    "adopt",
                    at,
                    "digital_wardrobe",
                    {"item_ids": ids, "reason": reason},
                ):
                    self._conn.rollback()
                    return False
                for i in ids:
                    self._conn.execute(
                        "UPDATE wardrobe_units SET ownership='owned', acquired_at=?, changed_at=?, revision=revision+1 WHERE ownership='candidate' AND unit_id IN (SELECT unit_id FROM wardrobe_links WHERE catalog_id=?)",
                        (at, at, i),
                    )
                self._conn.commit()
                return True
            except BaseException:
                self._conn.rollback()
                raise

        return await self._run_db(write)

    def _record_wardrobe_wear_unlocked(
        self, day, *, event_id: str = "archive", source: str = "outfit_receipt"
    ) -> bool:
        raw = str(day.meta.get("style_catalog_reference_ids") or "")
        ids = [int(v) for v in raw.split(",") if v.strip().isdigit()]
        at = str(day.meta.get("outfit_fact_confirmed_at") or "")
        if not ids or not at or not event_id:
            return False
        # 同一穿着回执可能来自换装结算、外观更新和定时核对，只计一次经历。
        receipt_id = (
            "wear:"
            + hashlib.sha256(encode([day.date, at, sorted(ids)]).encode()).hexdigest()[
                :24
            ]
        )

        self._wardrobe_bootstrap_unlocked()
        # 后到的旧回执不能把当前穿着退回去。
        latest = dict(
            self._conn.execute(
                "SELECT key,value FROM day_meta WHERE date=? AND key IN ('style_catalog_reference_ids','outfit_fact_confirmed_at')",
                (day.date,),
            )
        )
        if (
            latest.get("style_catalog_reference_ids") != raw
            or latest.get("outfit_fact_confirmed_at") != at
        ):
            return False
        units = {
            r[0]
            for i in ids
            for r in self._conn.execute(
                "SELECT unit_id FROM wardrobe_links WHERE catalog_id=?", (i,)
            )
        }
        if not units:
            return False
        prior = self._conn.execute(
            "SELECT payload_json FROM wardrobe_events WHERE kind='wear' ORDER BY occurred_at DESC LIMIT 1"
        ).fetchone()
        if prior:
            previous = unpack(prior[0])
            previous_units = {
                r[0]
                for i in previous.get("item_ids", [])
                for r in self._conn.execute(
                    "SELECT unit_id FROM wardrobe_links WHERE catalog_id=?",
                    (i,),
                )
            }
            if previous.get("day") == day.date and units == previous_units:
                return False
        if not self._wardrobe_event_unlocked(
            receipt_id,
            "wear",
            at,
            source,
            {"item_ids": ids, "outfit": day.outfit, "day": day.date},
        ):
            return False
        newest = self._conn.execute(
            "SELECT MAX(occurred_at) FROM wardrobe_events WHERE kind='wear'"
        ).fetchone()[0]
        if newest and newest > at:
            return True
        units = {
            r[0]
            for i in ids
            for r in self._conn.execute(
                "SELECT unit_id FROM wardrobe_links WHERE catalog_id=?", (i,)
            )
        }
        for row in self._conn.execute(
            "SELECT unit_id,payload_json FROM wardrobe_units"
        ).fetchall():
            data = unpack(row["payload_json"])
            wearing = row["unit_id"] in units
            if bool(data.get("wearing")) != wearing:
                data["wearing"] = wearing
                self._conn.execute(
                    "UPDATE wardrobe_units SET payload_json=?,revision=revision+1 WHERE unit_id=?",
                    (encode(data), row["unit_id"]),
                )
        self._conn.execute(
            "UPDATE wardrobe_units SET condition='clean',changed_at=?,revision=revision+1 WHERE condition='worn'",
            (at,),
        )
        for key in units:
            self._conn.execute(
                "UPDATE wardrobe_units SET ownership='owned',condition='worn',acquired_at=CASE WHEN acquired_at='' THEN ? ELSE acquired_at END,changed_at=?,revision=revision+1 WHERE unit_id=? AND condition IN ('clean','worn')",
                (at, at, key),
            )
        return True

    async def record_wardrobe_wear(
        self, day, *, event_id: str, source: str = "outfit_receipt"
    ) -> bool:
        def write():
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                changed = self._record_wardrobe_wear_unlocked(
                    day, event_id=event_id, source=source
                )
                self._conn.commit()
                return changed
            except BaseException:
                self._conn.rollback()
                raise

        return await self._run_db(write)

    async def record_wardrobe_feedback(
        self, *, event_id: str, item_ids: list[int], text: str, at: str, scope: str
    ) -> bool:
        def write():
            return self._wardrobe_event_unlocked(
                event_id,
                "feedback",
                at,
                scope,
                {"item_ids": item_ids, "text": text[:1000], "origin": "user_feedback"},
            )

        return await self._run_db(write)

    async def save_wardrobe_review(
        self, payload: dict, *, expected_revision: int, at: str
    ) -> bool:
        def write():
            row = self._conn.execute(
                "SELECT revision FROM wardrobe_profile WHERE name='self'"
            ).fetchone()
            if (row["revision"] if row else 0) != expected_revision:
                return False
            return bool(
                self._conn.execute(
                    "INSERT INTO wardrobe_profile VALUES('self',?,?,?) ON CONFLICT(name) DO UPDATE SET revision=excluded.revision,updated_at=excluded.updated_at,payload_json=excluded.payload_json WHERE wardrobe_profile.revision=?",
                    (expected_revision + 1, at, encode(payload), expected_revision),
                ).rowcount
            )

        return await self._run_db(write)

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
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._wardrobe_bootstrap_unlocked()
                keys = {
                    r[0]
                    for i in ids
                    for r in self._conn.execute(
                        "SELECT unit_id FROM wardrobe_links WHERE catalog_id=?", (i,)
                    )
                }
                rows = [
                    self._conn.execute(
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
                    self._conn.rollback()
                    return False
                if not self._wardrobe_event_unlocked(
                    event_id,
                    "care_start",
                    at,
                    "digital_care",
                    {"item_ids": ids, "decision": decision, "reason": reason},
                ):
                    self._conn.rollback()
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
                    self._conn.execute(
                        "UPDATE wardrobe_units SET condition=?,changed_at=?,payload_json=?,revision=revision+1 WHERE unit_id=?",
                        (decision, at, encode(data), row["unit_id"]),
                    )
                self._conn.commit()
                return True
            except BaseException:
                self._conn.rollback()
                raise

        return await self._run_db(write)

    async def advance_wardrobe_care(
        self, *, now: datetime.datetime, observer: str
    ) -> int:
        at = now.isoformat(sep=" ")

        def write():
            changed = 0
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for row in self._conn.execute(
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
                        self._wardrobe_event_unlocked(
                            f"care:{care.get('id')}:{row['unit_id']}:{condition}",
                            "care_receipt",
                            at,
                            "observed_digital_execution",
                            {"unit_id": row["unit_id"], "condition": condition},
                        )
                    data["care"] = care
                    self._conn.execute(
                        "UPDATE wardrobe_units SET condition=?,changed_at=?,payload_json=?,revision=revision+1 WHERE unit_id=?",
                        (condition, at, encode(data), row["unit_id"]),
                    )
                self._conn.commit()
                return changed
            except BaseException:
                self._conn.rollback()
                raise

        return await self._run_db(write)

    async def enqueue_wardrobe_job(
        self, job_id: str, payload: dict, *, at: str
    ) -> dict:
        def write():
            self._conn.execute(
                "INSERT OR IGNORE INTO wardrobe_jobs(job_id,updated_at,payload_json) VALUES(?,?,?)",
                (job_id, at, encode(payload)),
            )
            return dict(
                self._conn.execute(
                    "SELECT * FROM wardrobe_jobs WHERE job_id=?", (job_id,)
                ).fetchone()
            )

        return await self._run_db(write)

    async def wardrobe_auto_requested_since(
        self, since: str, *, exclude: str = ""
    ) -> bool:
        def read():
            return any(
                row[0] != exclude
                and unpack(row[1]).get("automatic")
                and (
                    unpack(row[2]).get("submitted_at")
                    or unpack(row[1]).get("requested_at", "")
                )
                >= since
                for row in self._conn.execute(
                    "SELECT job_id,payload_json,progress_json FROM wardrobe_jobs"
                )
            )

        return await self._run_db(read)

    async def claim_wardrobe_job(
        self, job_id: str, owner: str, *, now: datetime.datetime
    ) -> dict | None:
        at = now.isoformat(sep=" ")
        until = (now + datetime.timedelta(hours=6)).isoformat(sep=" ")

        def write():
            cursor = self._conn.execute(
                "UPDATE wardrobe_jobs SET lease_owner=?,lease_until=?,updated_at=? WHERE job_id=? AND status NOT IN ('completed','failed','cancelled','uncertain') AND (lease_owner='' OR lease_until<?) AND (next_at='' OR next_at<=?)",
                (owner, until, at, job_id, at, at),
            )
            if not cursor.rowcount:
                return None
            row = dict(
                self._conn.execute(
                    "SELECT * FROM wardrobe_jobs WHERE job_id=?", (job_id,)
                ).fetchone()
            )
            row["payload"] = unpack(row["payload_json"])
            row["progress"] = unpack(row["progress_json"])
            return row

        return await self._run_db(write)

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
                self._conn.execute(
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

        return await self._run_db(write)

    async def recover_wardrobe_jobs(self, owner: str) -> None:
        def write():
            rows = self._conn.execute(
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
                self._conn.execute(
                    "UPDATE wardrobe_jobs SET status=?,lease_owner='',lease_until='' WHERE job_id=? AND lease_owner=?",
                    (status, row["job_id"], row["lease_owner"]),
                )

        await self._run_db(write)

    async def release_wardrobe_jobs(self, owner: str) -> None:
        def write():
            self._conn.execute(
                "UPDATE wardrobe_jobs SET lease_owner='',lease_until='' WHERE lease_owner=?",
                (owner,),
            )

        await self._run_db(write)
