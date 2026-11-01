"""分享用事实投影：稳定行动身份、执行阶段和已生效的外观版本。"""

from __future__ import annotations

import hashlib
import json

from ...life.actionchain import actions, settlements
from ...life.appearance import current_appearance_values


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def project(day):
    meta = getattr(day, "meta", None)
    meta = meta if isinstance(meta, dict) else {}
    planned = [item for item in actions(day) if item.action_id] if meta else []
    parent = {item.action_id: item.action_id for item in planned}

    def root(key):
        while parent[key] != key:
            key = parent[key]
        return key

    groups = {}
    for item in planned:
        chain = item.payload.get("execution_chain_id")
        keys = [("action", item.action_id)]
        if isinstance(chain, str) and chain:
            keys.append(("chain", chain))
        if item.timeline_index is not None:
            keys.append(("timeline", item.timeline_index))
        for key in keys:
            previous = groups.setdefault(key, item.action_id)
            parent[root(item.action_id)] = root(previous)
        dependencies = item.payload.get("depends_on_action_ids") or []
        for identity in dependencies if isinstance(dependencies, list) else []:
            if isinstance(identity, str) and identity in parent:
                parent[root(item.action_id)] = root(identity)

    families = {}
    for item in planned:
        families.setdefault(root(item.action_id), []).append(item)
    outcomes = settlements(day) if meta else {}
    try:
        run = json.loads(meta.get("continuous_execution") or "{}")
    except (ValueError, TypeError):
        run = {}
    run = run if isinstance(run, dict) else {}
    current = run.get("action")
    current_id = current.get("action_id") if isinstance(current, dict) else None
    timeline = [item.as_dict() for item in getattr(day, "timeline", [])]
    progress, action_episodes = [], {}
    for members in families.values():
        # 身份只来自结构化编号；数组重排、文案和时间更新不制造新经历。
        chains = sorted({item.payload["execution_chain_id"] for item in members
                         if isinstance(item.payload.get("execution_chain_id"), str)
                         and item.payload["execution_chain_id"]})
        ids = {item.action_id for item in members}
        episode = digest(["life_episode", day.date, chains[0] if chains else min(ids)])
        complete = {identity for identity in ids if isinstance(outcomes.get(identity), dict)
                    and outcomes[identity].get("status") == "committed"}
        indices = {item.timeline_index for item in members if item.timeline_index is not None}
        states = {timeline[index].get("execution_state") for index in indices if 0 <= index < len(timeline)}
        stage = ("cancelled" if states.intersection({"cancelled", "skipped", "expired"}) else
                 "completed" if complete == ids else
                 "running" if current_id in ids and run.get("status") == "running"
                 and run.get("observation_state", "observed") == "observed" else "pending")
        progress.append({"episode_id": episode, "state": stage, "completed_steps": len(complete), "total_steps": len(ids)})
        action_episodes.update({identity: episode for identity in ids})
        for index in indices:
            if 0 <= index < len(timeline):
                timeline[index].update(episode_id=episode, episode_state=stage)
    return {
        "appearance_version": digest({k: v for k, v in current_appearance_values(day).items() if k != "style"}),
        "action_progress_version": digest(sorted(progress, key=lambda item: item["episode_id"])),
        "action_progress": progress,
        "action_episodes": action_episodes,
        "timeline": timeline,
    }
