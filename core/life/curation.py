"""衣橱自主选择的证据校验与展示，不从聊天或衣物描述匹配关键词。"""

from __future__ import annotations

from .dressing import digest
from .tools import timeline_item_datetime
from .stylememory import design_categories


CHOICE_LABELS = {
    "keep": "继续穿已有衣物",
    "remix": "尝试已有单品组合",
    "consider": "暂存新造型想法",
    "generate": "准备新造型",
}
STATUS_LABELS = {
    "decided": "已判断",
    "queued": "后台准备中",
    "cooldown": "先保留想法，稍后重新考虑",
    "waiting": "先等当前造型处理完成",
    "disabled": "已保留想法，创意衣橱未开启",
    "already_prepared": "这个想法已有生成记录",
    "needs_attention": "原任务待处理，未重复生成",
    "unverified": "依据暂不足，未生成",
}


def upcoming_outfit_plans(day, now) -> list[dict]:
    """只提供尚可执行的角色安排；计划不构成已参与活动的证据。"""
    return [
        {
            "time": at.isoformat(),
            "activity": item.activity,
            "activity_kind": item.activity_kind,
            "place": item.place,
            "place_kind": item.place_kind,
            "day_offset": item.day_offset,
        }
        for item in day.timeline
        if item.execution_state not in {"completed", "cancelled", "skipped", "expired"}
        if (at := timeline_item_datetime(item, day.date, meta=day.meta)) is not None
        and at >= now
    ][:12]


def validate_wardrobe_choice(raw: object, sources: dict, items: dict) -> dict:
    """只接受有结构化来源的选择；无效生成保留原因，不擅自补齐动机。"""
    value = raw if isinstance(raw, dict) else {}
    choice = {
        "decision": str(value.get("decision") or ""),
        "motive": str(value.get("motive") or ""),
        "key": str(value.get("key") or "").strip()[:100],
        "requirement": str(value.get("requirement") or "").strip()[:1000],
        "reason": str(value.get("reason") or "").strip()[:300],
        "difference": str(value.get("difference") or "").strip()[:300],
        "evidence_ids": value.get("evidence_ids")
        if isinstance(value.get("evidence_ids"), list)
        else [],
        "item_ids": value.get("item_ids")
        if isinstance(value.get("item_ids"), list)
        else [],
        "status": "decided",
        "constraint": "",
        "categories": design_categories(value.get("categories"), default=("outfit",)),
    }
    refs = choice["evidence_ids"]
    ids = choice["item_ids"]
    issue = ""
    if not choice["categories"]:
        issue = "造型目标类别无效"
        choice["categories"] = []
    elif choice["decision"] not in CHOICE_LABELS or not choice["reason"]:
        issue = "模型没有给出明确的衣橱选择及理由"
    elif any(not isinstance(ref, str) or ref not in sources for ref in refs):
        issue = "想法引用了未提供的生活依据"
    elif any(type(i) is not int or str(i) not in items for i in ids):
        issue = "搭配引用了不存在的衣物"
    elif choice["decision"] == "remix" and (
        not ids or any(not items[str(i)].get("available") for i in ids)
    ):
        issue = "已有单品组合尚缺少可穿衣物依据"
    elif choice["decision"] == "generate":
        required_kinds = {
            "need": {"conditions", "wear", "activity", "care_receipt"},
            "interest": {"persona", "own_aesthetic", "own_interest"},
            "occasion": {"upcoming_plan"},
            "rotation": {"rotation"},
        }.get(choice["motive"], set())
        if not required_kinds or not any(
            sources[ref].get("kind") in required_kinds for ref in refs
        ):
            issue = "新造型动机缺少对应的自身需求、兴趣或活动依据"
        elif not all(choice[k] for k in ("key", "requirement", "difference")):
            issue = "尚未说明具体想法及与已有衣物的区别"
        elif choice["motive"] == "rotation":
            if not set(choice["categories"]) & {"outfit", "top", "bottom"}:
                choice.update(status="unverified", constraint="衣物轮换依据不能充当仪容改变的理由")
                return choice
            evidence = [sources[ref] for ref in refs
                        if sources[ref].get("kind") == "rotation" and sources[ref].get("rotation_pressure")]
            if not evidence:
                issue = "近期穿着与可用库存尚未证明轮换需求"
            else:
                # 同一库存不因模型改写想法反复生成。
                choice["key"] = "rotation:" + digest(sorted(
                    (source["scene_category"], tuple(source["owned_unit_ids"])) for source in evidence
                ))
    if issue:
        choice.update(status="unverified", constraint=issue)
    return choice


def wardrobe_choice_text(choice: dict) -> str:
    if not choice:
        return ""
    label = CHOICE_LABELS.get(choice.get("decision"), "衣橱想法待确认")
    state = STATUS_LABELS.get(choice.get("status"), "待确认")
    reason = str(choice.get("reason") or "").strip()
    constraint = str(choice.get("constraint") or "").strip()
    return "；".join(part for part in (label, reason, state, constraint) if part)


def current_wardrobe_choice(snapshot: dict) -> dict:
    """任务结束后展示其真实结果，不把上次的排队状态一直显示为生成中。"""
    choice = dict(snapshot.get("profile", {}).get("acquisition") or {})
    job = next(
        (
            item
            for item in snapshot.get("recent_jobs", [])
            if item["job_id"] == choice.get("job_id")
        ),
        None,
    )
    if job:
        if job["status"] == "completed":
            choice["status"] = "already_prepared"
        elif job["status"] in {"failed", "cancelled", "uncertain"}:
            choice.update(status="needs_attention", constraint=job.get("error", ""))
        elif job.get("error"):
            choice["constraint"] = job["error"]
    return choice


def wardrobe_choice_job_id(choice: dict, residence: str) -> str:
    return "outfit-idea:" + digest([residence, choice["motive"], choice["key"]])
