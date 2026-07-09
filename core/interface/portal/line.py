from __future__ import annotations

from ...clock import now as life_now
from ...models import TimelineItem


class PortalLineMixin:
    @staticmethod
    def _page_validate_timeline(raw_timeline) -> list[TimelineItem]:
        if not isinstance(raw_timeline, list):
            raise ValueError("时间轴必须是数组")
        timeline = []
        for index, raw_item in enumerate(raw_timeline, start=1):
            item = TimelineItem.from_value(raw_item)
            if not item.time or not item.activity:
                raise ValueError(f"第 {index} 条时间轴缺少时间或活动")
            parts = item.time.split(":", 1)
            if len(parts) != 2:
                raise ValueError(f"第 {index} 条时间格式应为 HH:MM")
            try:
                hour, minute = int(parts[0]), int(parts[1])
            except ValueError:
                raise ValueError(f"第 {index} 条时间格式应为 HH:MM")
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(f"第 {index} 条时间超出范围")
            item.time = f"{hour:02d}:{minute:02d}"
            timeline.append(item)
        timeline.sort(key=lambda item: item.time)
        return timeline

    async def page_timeline_save(self):
        async def handler():
            body = await self._page_json_body()
            now = life_now()
            date_str = str(body.get("date") or "").strip()
            if not date_str:
                date_str, _ = await self.runtime.resolve_injection_target(now)
            timeline = self._page_validate_timeline(body.get("timeline"))
            day = await self.runtime.archive.replace_day_timeline(date_str, timeline)
            if not day:
                raise ValueError(f"未找到日期：{date_str}")
            return {"day": self._page_day(day, now, False)}

        return await self._page_json(handler)
