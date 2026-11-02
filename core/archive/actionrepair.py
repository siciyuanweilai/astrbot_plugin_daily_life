"""按原始成功结算修复旧版重复校验造成的投影错误，不重放生活副作用。"""
from __future__ import annotations

import json
import sqlite3

from ..models import DayRecord, TimelineItem, INTERNAL_SIMULATED_ACTION_TYPES
from .timeline import project_committed_timeline


def _json(value, default):
    try:
        result = json.loads(value)
    except (TypeError, ValueError):
        return default
    return result if isinstance(result, type(default)) else default


def repair_committed_actions(conn: sqlite3.Connection) -> dict:
    """在调用者事务内执行，可重复运行；仅纠正已确认的领域复查误判。"""
    report = {"actions": [], "timeline_nodes": 0}
    world_row = conn.execute("SELECT value FROM meta WHERE key='continuous_life_world'").fetchone()
    world = _json(world_row[0], {}) if world_row else {}
    history = {item.get('action', {}).get('action_id'): item
               for item in world.get('history', []) if isinstance(item, dict)
               and isinstance(item.get('action'), dict) and item.get('status') == 'completed'}
    dates = conn.execute("SELECT date,value FROM day_meta WHERE key='life_action_settlements'").fetchall()
    for date, text in dates:
        outcomes = _json(text, {})
        meta = dict(conn.execute('SELECT key,value FROM day_meta WHERE date=?', (date,)))
        planned = _json(meta.get('planned_life_actions'), [])
        actions = {a.get('action_id'): a for a in planned if isinstance(a, dict)}
        corrected = []
        audit = _json(meta.get('action_reconciliation'), {})
        times = _json(meta.get('life_action_execution_times'), {})
        for aid, canonical in outcomes.items():
            if not isinstance(canonical, dict) or canonical.get('status') != 'committed':
                continue
            old = conn.execute('SELECT status,reason,committed_at,effects_json FROM life_action_outcomes WHERE action_id=? AND date=?', (aid, date)).fetchone()
            failed = conn.execute("SELECT id,evidence_json FROM life_action_receipts WHERE action_id=? AND status='failed' AND source='life_domain_validation' ORDER BY id DESC LIMIT 1", (aid,)).fetchone()
            if not old or old[0] != 'failed' or not failed or old[1] not in _json(failed[1], []):
                continue
            at = canonical.get('committed_at')
            if not at or not canonical.get('action_type'):
                continue
            evidence = str(canonical.get('evidence') or '')
            audit[aid] = {'previous_status': old[0], 'previous_reason': old[1],
                          'previous_updated_at': old[2], 'invalid_receipt_id': failed[0],
                          'restored_from': 'life_action_settlements', 'committed_at': at}
            conn.execute("UPDATE life_action_outcomes SET status='committed',reason=?,committed_at=?,effects_json=?,evidence_json=?,updated_at=CURRENT_TIMESTAMP WHERE action_id=?",
                         (canonical.get('reason', ''), at,
                          json.dumps({**_json(old[3], {}), 'settled': canonical.get('state_changes', {})}, ensure_ascii=False),
                          json.dumps([evidence] if evidence else [], ensure_ascii=False), aid))
            conn.execute("INSERT OR IGNORE INTO life_action_receipts(receipt_id,action_id,date,action_type,status,evidence_json,source,source_id,occurred_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         ('reconciled:'+aid, aid, date, canonical['action_type'],
                          'simulated' if canonical['action_type'] in INTERNAL_SIMULATED_ACTION_TYPES else 'confirmed',
                          json.dumps([evidence, '依据原始成功结算纠正后续领域复查误判；未重新执行动作'], ensure_ascii=False),
                          'life_action_reconciliation', aid, at))
            conn.execute("UPDATE activity_sessions SET status='completed',ended_at=?,updated_at=CURRENT_TIMESTAMP WHERE action_id=?", (at, aid))
            conn.execute("UPDATE decision_traces SET decision='committed',reason_code='action_reconciled',outcome=?,updated_at=CURRENT_TIMESTAMP WHERE trace_id=?",
                         (canonical.get('reason', ''), 'life_action:'+aid))
            corrected.append(aid)
            report['actions'].append({'date': date, 'action_id': aid, 'committed_at': at})
        if not corrected:
            continue
        rows = conn.execute('SELECT * FROM timelines WHERE date=? ORDER BY sort_order', (date,))
        columns = [column[0] for column in rows.description]
        raw_nodes = [dict(zip(columns, row)) for row in rows.fetchall()]
        day = DayRecord(date=date, meta=meta, timeline=[TimelineItem.from_value(row) for row in raw_nodes])
        project_committed_timeline(day)
        for index, (before, item) in enumerate(zip(raw_nodes, day.timeline)):
            if item.execution_state != before['execution_state']:
                conn.execute('UPDATE timelines SET execution_state=?,execution_reason=?,execution_evidence=?,execution_updated_at=? WHERE date=? AND sort_order=?',
                             (item.execution_state, item.execution_reason, item.execution_evidence, item.execution_updated_at, date, before['sort_order']))
                report['timeline_nodes'] += 1
        for aid in corrected:
            action = actions.get(aid, {})
            index = action.get('timeline_index')
            payload = action.get('payload') or {}
            if (action.get('action_type') in {'move', 'travel'} and payload.get('origin') and payload.get('destination')
                    and type(index) is int and 0 <= index < len(raw_nodes)
                    and sum(a.get('timeline_index') == index for a in actions.values()) == 1):
                conn.execute('UPDATE timelines SET place=?,travel_origin=?,travel_mode=? WHERE date=? AND sort_order=?',
                             (payload['destination'], payload['origin'], payload.get('travel_mode', ''), date, raw_nodes[index]['sort_order']))
        for aid, run in history.items():
            if aid in actions and run.get('started_at') and aid not in times:
                times[aid] = {'started_at': run['started_at']}
                if isinstance(run.get('active_seconds'), (int, float)):
                    times[aid]['active_seconds'] = run['active_seconds']
        for key, value in [('action_reconciliation', audit), ('life_action_execution_times', times)]:
            conn.execute('INSERT INTO day_meta(date,key,value) VALUES(?,?,?) ON CONFLICT(date,key) DO UPDATE SET value=excluded.value',
                         (date, key, json.dumps(value, ensure_ascii=False)))
        if 'schedule_replan_pending' not in day.meta:
            conn.execute("DELETE FROM day_meta WHERE date=? AND key='schedule_replan_pending'", (date,))
        conn.execute('UPDATE days SET revision=revision+1 WHERE date=?', (date,))
    return report
