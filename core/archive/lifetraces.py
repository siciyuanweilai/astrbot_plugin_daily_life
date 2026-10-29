"""有来源的关联、阶段回忆、原话档案和阅读记录；模型解释不替代原始证据。"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from ..clock import now as life_now
from .longterm import LongTermMemoryArchiveComponent


def revision(row: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                row.get(k, "")
                for k in (
                    "scope",
                    "category",
                    "title",
                    "content",
                    "status",
                    "source_table",
                    "source_id",
                    "date",
                    "session_id",
                    "expires_at",
                )
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def stamp() -> str:
    return life_now().isoformat(sep=" ", timespec="seconds")


def periods(date: str) -> dict[str, str]:
    day = dt.date.fromisoformat(date[:10])
    year, week, _ = day.isocalendar()
    return {
        "week": f"{year}-W{week:02}",
        "month": day.strftime("%Y-%m"),
        "quarter": f"{day.year}-Q{(day.month - 1) // 3 + 1}",
        "year": str(day.year),
    }


class MemoryLifeArchive:
    def __init__(self, archive):
        self.archive = archive

    @property
    def conn(self):
        return self.archive._conn

    def _transaction(self, fn):
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            return fn()

    def _memories(self, scopes: list[str] | None = None) -> list[dict]:
        args: list[Any] = []
        clause = ""
        if scopes is not None:
            if not scopes:
                return []
            clause = " AND m.scope IN (" + ",".join("?" for _ in scopes) + ")"
            args.extend(scopes)
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT m.* FROM long_term_memories m WHERE m.status='active' "
                "AND (m.expires_at='' OR m.expires_at>=?) "
                + clause
                + LongTermMemoryArchiveComponent._active_source_revision_clause()
                + " ORDER BY m.date,m.id",
                (life_now().date().isoformat(), *args),
            )
        ]

    def _valid_sources(self, sources: dict, scope: str) -> bool:
        live = {str(r["id"]): revision(r) for r in self._memories([scope])}
        return bool(sources) and all(live.get(str(k)) == v for k, v in sources.items())

    async def pending(self, limit: int = 8) -> list[dict]:
        def read():
            done = {
                r["memory_id"]: r["source_revision"]
                for r in self.conn.execute("SELECT * FROM memory_enrichments")
            }
            rows = [r for r in self._memories() if done.get(r["id"]) != revision(r)]
            if not rows:
                return []
            scope, date = rows[0]["scope"], rows[0]["date"]
            result, size = [], 0
            for row in rows:
                if row["scope"] != scope or row["date"] != date:
                    continue
                amount = len(row["content"])
                if result and size + amount > 10000:
                    break
                result.append({**row, "revision": revision(row)})
                size += amount
                if len(result) >= limit:
                    break
            return result

        return await self.archive._run_db(read)

    async def entities(self, scope: str) -> list[dict]:
        return await self.archive._run_db(
            lambda: [
                dict(r)
                for r in self.conn.execute(
                    "SELECT id,entity_type,name,aliases FROM memory_entities WHERE scope=? AND status='active' ORDER BY last_seen DESC LIMIT 80",
                    (scope,),
                )
            ]
        )

    async def save_enrichment(self, offered: list[dict], payload: dict) -> int:
        """只接受本批实际来源；写入时重查版本，纠错与后台分析并发也不会复活旧记忆。"""
        if not offered:
            return 0
        scope = offered[0]["scope"]
        expected = {str(r["id"]): r["revision"] for r in offered}

        def write():
            if not self._valid_sources(expected, scope):
                return 0
            memories = {r["id"]: r for r in offered}
            local = {}
            for entity in payload.get("entities", [])[:24]:
                key, name, kind = (
                    str(entity.get("key", "")),
                    str(entity.get("name", "")).strip()[:100],
                    entity.get("kind"),
                )
                if (
                    not key
                    or not name
                    or kind not in {"person", "place", "object", "activity", "topic"}
                ):
                    continue
                existing = int(entity.get("existing_id") or 0)
                row = (
                    self.conn.execute(
                        "SELECT id FROM memory_entities WHERE id=? AND scope=? AND entity_type=? AND status='active'",
                        (existing, scope, kind),
                    ).fetchone()
                    if existing
                    else None
                )
                if existing and not row:
                    continue
                if not row:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO memory_entities(scope,entity_type,name,first_seen,last_seen) VALUES(?,?,?,?,?)",
                        (scope, kind, name, offered[0]["date"], offered[-1]["date"]),
                    )
                    row = self.conn.execute(
                        "SELECT id FROM memory_entities WHERE scope=? AND entity_type=? AND name=?",
                        (scope, kind, name),
                    ).fetchone()
                redirect = self.conn.execute(
                    "SELECT e.* FROM memory_identity_redirects r JOIN memory_entities e ON e.id=r.canonical_id WHERE r.entity_id=? AND e.status='active'",
                    (row["id"],),
                ).fetchone()
                if redirect:
                    row = redirect
                local[key] = row["id"]
            for r in offered:
                self.conn.execute(
                    "DELETE FROM memory_associations WHERE memory_id=?", (r["id"],)
                )
                self.conn.execute(
                    "DELETE FROM memory_entity_links WHERE memory_id=? AND relation='semantic'",
                    (r["id"],),
                )
            for edge in payload.get("relations", [])[:48]:
                mid = int(edge.get("memory_id") or 0)
                quote = str(edge.get("quote") or "").strip()
                a, b = local.get(str(edge.get("from"))), local.get(str(edge.get("to")))
                relation = str(edge.get("relation") or "").strip()[:120]
                if (
                    mid not in memories
                    or not a
                    or not b
                    or a == b
                    or not relation
                    or not quote
                    or quote not in memories[mid]["content"]
                ):
                    continue
                self.conn.execute(
                    "INSERT OR IGNORE INTO memory_associations(source_entity_id,target_entity_id,memory_id,source_revision,relation,occurred_at) VALUES(?,?,?,?,?,?)",
                    (a, b, mid, expected[str(mid)], relation, memories[mid]["date"]),
                )
                for eid in (a, b):
                    self.conn.execute(
                        "INSERT OR IGNORE INTO memory_entity_links(entity_id,memory_id,relation) VALUES(?,?,'semantic')",
                        (eid, mid),
                    )
            for mid, digest in expected.items():
                self.conn.execute(
                    "INSERT OR REPLACE INTO memory_enrichments VALUES(?,?,?)",
                    (int(mid), digest, stamp()),
                )
            diary = payload.get("diary")
            if isinstance(diary, dict) and diary.get("write") is True:
                ids = [
                    int(i) for i in diary.get("memory_ids", []) if str(i) in expected
                ]
                body = str(diary.get("content") or "").strip()[:4000]
                date = str(diary.get("date") or "")
                try:
                    dt.date.fromisoformat(date)
                except ValueError:
                    return len(offered)
                if ids and body and all(memories[i]["date"][:10] == date for i in ids):
                    refs = [f"memory:{i}:{expected[str(i)]}" for i in ids]
                    previous = self.conn.execute(
                        "SELECT evidence_ids_json FROM grounded_diary_entries WHERE date=? AND scope=? AND source='memory_life'",
                        (date, scope),
                    ).fetchone()
                    if previous:
                        old_refs = json.loads(previous["evidence_ids_json"])
                        old_sources = {
                            p[1]: p[2]
                            for ref in old_refs
                            if len(p := str(ref).split(":")) == 3 and p[0] == "memory"
                        }
                        if self._valid_sources(old_sources, scope):
                            refs = list(dict.fromkeys([*old_refs, *refs]))
                    self.conn.execute(
                        "INSERT INTO grounded_diary_entries(date,scope,title,summary,evidence_ids_json,source) VALUES(?,?,?,?,?,'memory_life') ON CONFLICT(date,scope) DO UPDATE SET title=excluded.title,summary=excluded.summary,evidence_ids_json=excluded.evidence_ids_json,updated_at=CURRENT_TIMESTAMP WHERE grounded_diary_entries.source='memory_life'",
                        (
                            date,
                            scope,
                            str(diary.get("title") or date)[:100],
                            body,
                            json.dumps(refs),
                        ),
                    )
            return len(offered)

        return await self.archive._run_db(self._transaction, write)

    async def graph(
        self,
        scopes: list[str],
        *,
        entity_id: int = 0,
        memory_ids: list[int] | None = None,
        hops: int = 2,
        limit: int = 60,
    ) -> dict:
        def read():
            live = {r["id"]: r for r in self._memories(scopes)}
            edges = [
                dict(r)
                for r in self.conn.execute(
                    "SELECT * FROM memory_associations ORDER BY occurred_at DESC,id DESC"
                )
                if r["memory_id"] in live
                and revision(live[r["memory_id"]]) == r["source_revision"]
            ]
            allowed = {
                r["id"]: dict(r)
                for r in self.conn.execute(
                    "SELECT * FROM memory_entities WHERE status='active'"
                )
                if r["scope"] in scopes
            }
            edges = [
                r
                for r in edges
                if r["source_entity_id"] in allowed and r["target_entity_id"] in allowed
            ]
            frontier = {entity_id} if entity_id in allowed else set()
            if entity_id and not frontier:
                return {"nodes": [], "edges": [], "memories": []}
            if memory_ids:
                for e in edges:
                    if e["memory_id"] in memory_ids:
                        frontier.update((e["source_entity_id"], e["target_entity_id"]))
            if entity_id or memory_ids:
                selected = {}
                for _ in range(max(1, min(hops, 3))):
                    neighbors = set()
                    for e in edges:
                        if frontier.intersection(
                            (e["source_entity_id"], e["target_entity_id"])
                        ):
                            selected[e["id"]] = e
                            neighbors.update(
                                (e["source_entity_id"], e["target_entity_id"])
                            )
                    frontier.update(neighbors)
                edges = list(selected.values())
            edges = edges[: max(1, min(limit, 100))]
            used = {
                i for e in edges for i in (e["source_entity_id"], e["target_entity_id"])
            }
            mids = list(dict.fromkeys(e["memory_id"] for e in edges))
            return {
                "nodes": [allowed[i] for i in sorted(used)],
                "edges": edges,
                "memories": [live[i] for i in mids],
            }

        return await self.archive._run_db(read)

    async def chapter_candidate(self) -> dict | None:
        def read():
            grouped = {}
            for row in self._memories():
                try:
                    ps = periods(row["date"])
                except (ValueError, TypeError):
                    continue
                for level, period in ps.items():
                    grouped.setdefault((row["scope"], level, period), []).append(row)
            old = {
                (r["scope"], r["level"], r["period"]): dict(r)
                for r in self.conn.execute("SELECT * FROM memory_chapters")
            }
            for key, rows in sorted(
                grouped.items(),
                key=lambda kv: (
                    ["week", "month", "quarter", "year"].index(kv[0][1]),
                    kv[0][2],
                    kv[0][0],
                ),
            ):
                if len(rows) < 2:
                    continue
                refs = {str(r["id"]): revision(r) for r in rows}
                prior = old.get(key)
                if prior and json.loads(prior["sources_json"]) == refs:
                    continue
                # 上层引用完整有效的下层章节；未覆盖部分保留原始来源，禁止无声截断。
                level = key[1]
                lower = {"month": "week", "quarter": "month", "year": "quarter"}.get(
                    level
                )
                children = [
                    r
                    for k, r in old.items()
                    if k[0] == key[0]
                    and k[1] == lower
                    and self._valid_sources(json.loads(r["sources_json"]), key[0])
                    and set(json.loads(r["sources_json"])).issubset(refs)
                ]
                covered = {i for c in children for i in json.loads(c["sources_json"])}
                remaining = [r for r in rows if str(r["id"]) not in covered]
                return {
                    "scope": key[0],
                    "level": level,
                    "period": key[2],
                    "sources": refs,
                    "memories": remaining,
                    "children": children,
                }
            return None

        return await self.archive._run_db(read)

    async def save_chapter(self, candidate: dict, title: str, content: str) -> bool:
        if not content.strip():
            return False

        def write():
            if not self._valid_sources(candidate["sources"], candidate["scope"]):
                return False
            self.conn.execute(
                "INSERT INTO memory_chapters(scope,level,period,title,content,sources_json,children_json,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(scope,level,period) DO UPDATE SET title=excluded.title,content=excluded.content,sources_json=excluded.sources_json,children_json=excluded.children_json,updated_at=excluded.updated_at",
                (
                    candidate["scope"],
                    candidate["level"],
                    candidate["period"],
                    title[:160],
                    content[:8000],
                    json.dumps(candidate["sources"]),
                    json.dumps([c["id"] for c in candidate["children"]]),
                    stamp(),
                ),
            )
            return True

        return await self.archive._run_db(self._transaction, write)

    async def chapters(self, scopes: list[str], limit: int = 20) -> list[dict]:
        def read():
            result = []
            for r in self.conn.execute(
                "SELECT * FROM memory_chapters ORDER BY period DESC,id DESC"
            ):
                item = dict(r)
                if item["scope"] in scopes and self._valid_sources(
                    json.loads(item["sources_json"]), item["scope"]
                ):
                    item["memory_ids"] = list(
                        map(int, json.loads(item.pop("sources_json")))
                    )
                    result.append(item)
            return result[: max(1, min(100, limit))] if limit else result

        return await self.archive._run_db(read)

    def _diaries(self, scopes: list[str], memories: list[dict]) -> list[dict]:
        live = {(r["scope"], str(r["id"])): revision(r) for r in memories}
        out = []
        for row in self.conn.execute(
            "SELECT * FROM grounded_diary_entries ORDER BY date DESC,id DESC"
        ):
            r = dict(row)
            if r["scope"] not in scopes:
                continue
            refs = json.loads(r["evidence_ids_json"])
            if r["source"] == "memory_life":
                sources = {
                    p[1]: p[2]
                    for ref in refs
                    if len(p := str(ref).split(":")) == 3 and p[0] == "memory"
                }
                if not sources or any(
                    live.get((r["scope"], mid)) != version
                    for mid, version in sources.items()
                ):
                    continue
            r["evidence_ids"] = refs
            out.append(r)
        return out

    async def diaries(self, scopes: list[str], limit: int = 20) -> list[dict]:
        def read():
            rows = self._diaries(scopes, self._memories(scopes))
            return rows[: max(1, min(100, limit))] if limit else rows

        return await self.archive._run_db(read)

    async def timeline(
        self, scopes: list[str], *, offset: int = 0, limit: int = 12
    ) -> dict:
        """只读呈现已经留下的经历和感受，不触发模型或改变记忆。"""

        def read():
            memories = self._memories(scopes)
            by_id = {r["id"]: r for r in memories}
            entries, covered = [], set()
            for row in self._diaries(scopes, memories):
                ids = set()
                for ref in row["evidence_ids"]:
                    parts = str(ref).split(":")
                    if (
                        len(parts) in {2, 3}
                        and parts[0] == "memory"
                        and parts[1].isdecimal()
                    ):
                        mid = int(parts[1])
                        if mid in by_id and by_id[mid]["scope"] == row["scope"]:
                            ids.add(mid)
                covered.update(ids)
                entries.append(
                    {
                        "id": f"diary:{row['id']}",
                        "kind": "diary",
                        "scope": row["scope"],
                        "date": row["date"],
                        "title": row["title"],
                        "content": row["summary"],
                        "memory_ids": sorted(ids),
                    }
                )
            for row in memories:
                if row["id"] not in covered:
                    entries.append(
                        {
                            "id": f"memory:{row['id']}",
                            "kind": "memory",
                            "scope": row["scope"],
                            "date": row["date"],
                            "title": row["title"],
                            "content": row["content"],
                            "memory_ids": [row["id"]],
                        }
                    )
            for row in self.conn.execute(
                "SELECT r.*,p.source_id,s.title,s.scope FROM knowledge_readings r "
                "JOIN knowledge_passages p ON p.id=r.passage_id "
                "JOIN knowledge_sources s ON s.id=p.source_id"
            ):
                if row["scope"] not in scopes:
                    continue
                entries.append(
                    {
                        "id": f"reading:{row['id']}",
                        "kind": "reading",
                        "scope": row["scope"],
                        "date": row["created_at"],
                        "title": f"读到《{row['title']}》",
                        "content": "\n\n".join(
                            dict.fromkeys(
                                s for s in (row["summary"], row["reflection"]) if s
                            )
                        ),
                        "question": row["question"],
                        "evidence_quote": row["evidence_quote"],
                        "source_id": row["source_id"],
                        "passage_id": row["passage_id"],
                        "memory_ids": [],
                    }
                )
            entries.sort(
                key=lambda r: (r["date"], int(r["id"].split(":")[1]), r["kind"]),
                reverse=True,
            )
            start, count = max(0, offset), max(1, min(120, limit))
            return {
                "items": entries[start : start + count],
                "next_offset": start + count if len(entries) > start + count else None,
            }

        return await self.archive._run_db(read)

    async def originals(
        self,
        scopes: list[str],
        *,
        start: str = "",
        end: str = "",
        offset: int = 0,
        limit: int = 12,
    ) -> dict:
        if not scopes:
            return {"messages": [], "next_offset": None}

        def read():
            clause = "session_id IN (" + ",".join("?" for _ in scopes) + ")"
            args = list(scopes)
            for field, op, val in [
                ("occurred_at", ">=", start),
                ("occurred_at", "<=", end),
            ]:
                if val:
                    dt.datetime.fromisoformat(val)
                    clause += f" AND life_timestamp({field}) {op} life_timestamp(?)"
                    args.append(val)
            count = max(1, min(24, limit))
            rows = self.conn.execute(
                f"SELECT * FROM conversation_originals WHERE {clause} ORDER BY life_timestamp(occurred_at),event_key LIMIT ? OFFSET ?",
                (*args, count + 1, max(0, offset)),
            ).fetchall()
            return {
                "messages": [dict(r) for r in rows[:count]],
                "next_offset": max(0, offset) + count if len(rows) > count else None,
            }

        return await self.archive._run_db(read)

    async def import_source(
        self, *, scope: str, title: str, content: str, origin: str = ""
    ) -> dict:
        content, title, scope = (
            str(content).strip(),
            str(title).strip(),
            str(scope).strip(),
        )
        if not scope or not title or not content:
            raise ValueError("资料范围、标题和正文不能为空")
        if len(content.encode()) > 2 * 1024 * 1024:
            raise ValueError("单份文字资料不能超过 2 MiB")
        digest = hashlib.sha256(content.encode()).hexdigest()
        # 按段落和字符窗口切分，与语义决策无关；每段保留可逐字核对的原文。
        chunks, current = [], ""
        for paragraph in content.splitlines(keepends=True):
            if len(current) + len(paragraph) > 2400 and current:
                chunks.append(current)
                current = ""
            while len(paragraph) > 2400:
                chunks.append(paragraph[:2400])
                paragraph = paragraph[2400:]
            current += paragraph
        if current:
            chunks.append(current)

        def write():
            old = self.conn.execute(
                "SELECT * FROM knowledge_sources WHERE scope=? AND content_hash=?",
                (scope, digest),
            ).fetchone()
            if old:
                return {**dict(old), "duplicate": True}
            sid = self.conn.execute(
                "INSERT INTO knowledge_sources(scope,title,origin,content_hash,created_at) VALUES(?,?,?,?,?)",
                (scope, title[:200], origin[:2000], digest, stamp()),
            ).lastrowid
            self.conn.executemany(
                "INSERT INTO knowledge_passages(source_id,ordinal,content) VALUES(?,?,?)",
                [(sid, i + 1, text) for i, text in enumerate(chunks)],
            )
            return {
                "id": sid,
                "title": title,
                "passages": len(chunks),
                "duplicate": False,
            }

        return await self.archive._run_db(self._transaction, write)

    async def library(self, scopes: list[str]) -> list[dict]:
        def read():
            return [
                dict(r)
                for r in self.conn.execute(
                    "SELECT s.*,COUNT(DISTINCT p.id) AS total,COUNT(DISTINCT r.passage_id) AS read_count FROM knowledge_sources s LEFT JOIN knowledge_passages p ON p.source_id=s.id LEFT JOIN knowledge_readings r ON r.passage_id=p.id GROUP BY s.id ORDER BY s.id DESC"
                )
                if r["scope"] in scopes
            ]

        return await self.archive._run_db(read)

    async def passage(
        self,
        source_id: int,
        scopes: list[str],
        *,
        passage_id: int = 0,
        include_inactive: bool = False,
    ) -> dict | None:
        def read():
            source = self.conn.execute(
                "SELECT * FROM knowledge_sources WHERE id=?",
                (source_id,),
            ).fetchone()
            if (
                not source
                or source["scope"] not in scopes
                or (not include_inactive and source["status"] != "available")
            ):
                return None
            if passage_id:
                row = self.conn.execute(
                    "SELECT * FROM knowledge_passages WHERE source_id=? AND id=?",
                    (source_id, passage_id),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT p.* FROM knowledge_passages p WHERE p.source_id=? AND NOT EXISTS(SELECT 1 FROM knowledge_readings r WHERE r.passage_id=p.id) ORDER BY ordinal LIMIT 1",
                    (source_id,),
                ).fetchone()
            if not row:
                return None
            previous = self.conn.execute(
                "SELECT r.* FROM knowledge_readings r JOIN knowledge_passages p ON p.id=r.passage_id WHERE p.source_id=? ORDER BY r.id DESC LIMIT 1",
                (source_id,),
            ).fetchone()
            return {
                **dict(row),
                "source": dict(source),
                "previous": dict(previous) if previous else None,
            }

        return await self.archive._run_db(read)

    async def save_reading(
        self, offered: dict, payload: dict, *, action_id: str = ""
    ) -> bool:
        quote, summary = (
            str(payload.get("quote") or "").strip(),
            str(payload.get("summary") or "").strip(),
        )
        if (
            not quote
            or quote not in offered["content"]
            or not summary
            or payload.get("understood") is not True
        ):
            return False

        def write():
            current = self.conn.execute(
                "SELECT p.* FROM knowledge_passages p JOIN knowledge_sources s ON s.id=p.source_id WHERE p.id=? AND s.status='available' AND s.content_hash=?",
                (offered["id"], offered["source"]["content_hash"]),
            ).fetchone()
            if not current or current["content"] != offered["content"]:
                return False
            # 同一次进度提交幂等；重读由显式 passage_id 入口决定，保留观点演变。
            previous = offered.get("previous")
            last = self.conn.execute(
                "SELECT r.* FROM knowledge_readings r JOIN knowledge_passages p ON p.id=r.passage_id WHERE p.source_id=? ORDER BY r.id DESC LIMIT 1",
                (offered["source_id"],),
            ).fetchone()
            if (last["id"] if last else None) != (previous["id"] if previous else None):
                return False
            self.conn.execute(
                "INSERT INTO knowledge_readings(passage_id,summary,reflection,question,evidence_quote,previous_reading_id,action_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    offered["id"],
                    summary[:1800],
                    str(payload.get("reflection") or "")[:1800],
                    str(payload.get("question") or "")[:1000],
                    quote[:2400],
                    previous["id"] if previous else None,
                    action_id,
                    stamp(),
                ),
            )
            return True

        return await self.archive._run_db(self._transaction, write)

    async def source_passages(
        self, scopes: list[str], *, offset: int = 0, limit: int = 16
    ) -> dict:
        if not scopes:
            return {"passages": [], "next_offset": None}

        def read():
            placeholders = ",".join("?" for _ in scopes)
            count = max(1, min(24, limit))
            rows = self.conn.execute(
                f"SELECT p.*,s.title,s.origin,s.scope FROM knowledge_passages p JOIN knowledge_sources s ON s.id=p.source_id WHERE s.scope IN ({placeholders}) AND s.status='available' ORDER BY p.id LIMIT ? OFFSET ?",
                (*scopes, count + 1, max(0, offset)),
            ).fetchall()
            return {
                "passages": [dict(r) for r in rows[:count]],
                "next_offset": max(0, offset) + count if len(rows) > count else None,
            }

        return await self.archive._run_db(read)

    async def reading_receipts(self, action_id: str, source_id: int) -> list[dict]:
        if not action_id:
            return []
        return await self.archive._run_db(
            lambda: [
                dict(r)
                for r in self.conn.execute(
                    "SELECT r.* FROM knowledge_readings r JOIN knowledge_passages p ON p.id=r.passage_id WHERE r.action_id=? AND p.source_id=? ORDER BY r.id",
                    (action_id, source_id),
                )
            ]
        )

    async def reading_notes(self, scopes: list[str], limit: int = 30) -> list[dict]:
        return await self.archive._run_db(
            lambda: [
                dict(r)
                for r in self.conn.execute(
                    "SELECT r.*,p.source_id,p.ordinal,s.title,s.scope,s.origin FROM knowledge_readings r JOIN knowledge_passages p ON p.id=r.passage_id JOIN knowledge_sources s ON s.id=p.source_id ORDER BY r.id DESC"
                )
                if r["scope"] in scopes
            ][: max(1, min(100, limit))]
        )

    async def set_source_status(self, source_id: int, status: str) -> bool:
        if status not in {"available", "paused", "removed"}:
            raise ValueError("资料状态无效")
        return await self.archive._run_db(
            lambda: bool(
                self.conn.execute(
                    "UPDATE knowledge_sources SET status=? WHERE id=?",
                    (status, source_id),
                ).rowcount
            )
        )

    async def manage_identity(
        self,
        *,
        operation: str,
        entity_id: int,
        target_id: int = 0,
        name: str = "",
        memory_ids: list[int] | None = None,
    ) -> int:
        def write():
            original = self.conn.execute(
                "SELECT * FROM memory_entities WHERE id=? AND status='active'",
                (entity_id,),
            ).fetchone()
            if not original:
                raise ValueError("人物或地点已不存在")
            if operation == "merge":
                target = self.conn.execute(
                    "SELECT * FROM memory_entities WHERE id=? AND status='active'",
                    (target_id,),
                ).fetchone()
                if (
                    not target
                    or target_id == entity_id
                    or target["scope"] != original["scope"]
                    or target["entity_type"] != original["entity_type"]
                ):
                    raise ValueError("只能合并同一可见范围内、同一类型的不同实体")
                mids = [
                    r[0]
                    for r in self.conn.execute(
                        "SELECT memory_id FROM memory_entity_links WHERE entity_id=?",
                        (entity_id,),
                    )
                ]
            elif operation == "split":
                if not name.strip() or not memory_ids:
                    raise ValueError("拆分需要新名称和明确的来源记忆编号")
                mids = list(set(map(int, memory_ids)))
                owned = {
                    r[0]
                    for r in self.conn.execute(
                        "SELECT memory_id FROM memory_entity_links WHERE entity_id=?",
                        (entity_id,),
                    )
                }
                if not set(mids).issubset(owned):
                    raise ValueError("拆分来源不属于这个实体")
                target_id_local = self.conn.execute(
                    "INSERT INTO memory_entities(scope,entity_type,name) VALUES(?,?,?)",
                    (original["scope"], original["entity_type"], name.strip()[:100]),
                ).lastrowid
                target = {"id": target_id_local}
            else:
                raise ValueError("实体操作无效")
            dest = target["id"]
            for mid in mids:
                self.conn.execute(
                    "INSERT OR IGNORE INTO memory_entity_links(entity_id,memory_id,relation,weight) SELECT ?,memory_id,relation,weight FROM memory_entity_links WHERE entity_id=? AND memory_id=?",
                    (dest, entity_id, mid),
                )
                self.conn.execute(
                    "DELETE FROM memory_entity_links WHERE entity_id=? AND memory_id=?",
                    (entity_id, mid),
                )
                rows = self.conn.execute(
                    "SELECT * FROM memory_associations WHERE memory_id=? AND (source_entity_id=? OR target_entity_id=?)",
                    (mid, entity_id, entity_id),
                ).fetchall()
                for e in rows:
                    a = (
                        dest
                        if e["source_entity_id"] == entity_id
                        else e["source_entity_id"]
                    )
                    b = (
                        dest
                        if e["target_entity_id"] == entity_id
                        else e["target_entity_id"]
                    )
                    self.conn.execute(
                        "DELETE FROM memory_associations WHERE id=?", (e["id"],)
                    )
                    if a != b:
                        self.conn.execute(
                            "INSERT OR IGNORE INTO memory_associations(source_entity_id,target_entity_id,memory_id,source_revision,relation,occurred_at) VALUES(?,?,?,?,?,?)",
                            (
                                a,
                                b,
                                mid,
                                e["source_revision"],
                                e["relation"],
                                e["occurred_at"],
                            ),
                        )
            if operation == "merge":
                aliases = list(
                    dict.fromkeys(
                        [
                            *str(target["aliases"] or "").split(","),
                            original["name"],
                            *str(original["aliases"] or "").split(","),
                        ]
                    )
                )
                self.conn.execute(
                    "UPDATE memory_entities SET aliases=? WHERE id=?",
                    (",".join(a for a in aliases if a), dest),
                )
                self.conn.execute(
                    "INSERT OR REPLACE INTO memory_identity_redirects VALUES(?,?)",
                    (entity_id, dest),
                )
                self.conn.execute(
                    "UPDATE memory_identity_redirects SET canonical_id=? WHERE canonical_id=?",
                    (dest, entity_id),
                )
                self.conn.execute(
                    "UPDATE memory_entities SET status='merged' WHERE id=?",
                    (entity_id,),
                )
            return dest

        return await self.archive._run_db(self._transaction, write)

    async def scopes(self) -> list[str]:
        return await self.archive._run_db(
            lambda: [
                r[0]
                for r in self.conn.execute(
                    "SELECT scope FROM long_term_memories UNION SELECT scope FROM knowledge_sources UNION SELECT session_id FROM conversation_originals UNION SELECT scope FROM grounded_diary_entries UNION SELECT scope FROM memory_chapters ORDER BY 1"
                )
                if r[0]
            ]
        )
