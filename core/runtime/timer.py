from typing import Callable, Awaitable
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.executors.asyncio import AsyncIOExecutor
from astrbot.api import logger
from ..config.options import LifeSettings
from ..life.tools import parse_schedule_time
from ..clock import TIMEZONE

class LifeRhythmClock:
    def __init__(self, config: LifeSettings, 
                 daily_task: Callable[[], Awaitable[None]],
                 weekly_task: Callable[[], Awaitable[None]],
                 auto_update_task: Callable[[], Awaitable[None]],
                 review_task: Callable[[], Awaitable[None]] | None = None,
                 proactive_revisit_task: Callable[[], Awaitable[None]] | None = None,
                 proactive_idle_task: Callable[[], Awaitable[None]] | None = None):
        self.config = config
        self.scheduler = AsyncIOScheduler(
            timezone=TIMEZONE,
            executors={"default": AsyncIOExecutor()},
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 120
            }
        )
        self.daily_task = daily_task
        self.weekly_task = weekly_task
        self.auto_update_task = auto_update_task
        self.review_task = review_task
        self.proactive_revisit_task = proactive_revisit_task
        self.proactive_idle_task = proactive_idle_task

    def start(self):
        try:
            h, m = parse_schedule_time(self.config.schedule_time)
            self.config.schedule_time = f"{h:02d}:{m:02d}"
            self.scheduler.add_job(
                self.daily_task, 'cron', hour=h, minute=m, id="daily", replace_existing=True
            )
            
            wh, wm = parse_schedule_time(self.config.week_plan_time, default="06:00")
            self.config.week_plan_time = f"{wh:02d}:{wm:02d}"
            day_map = {"monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu", "friday": "fri", "saturday": "sat", "sunday": "sun"}
            self.scheduler.add_job(
                self.weekly_task, 'cron', day_of_week=day_map.get(self.config.week_plan_day, "mon"),
                hour=wh, minute=wm, id="weekly", replace_existing=True
            )
            
            if self.config.state.enabled:
                interval = max(5, int(self.config.state.refresh_minutes or 30))
                self.scheduler.add_job(
                    self.auto_update_task, 'interval', minutes=interval,
                    id="auto_life_check", replace_existing=True
                )

            proactive = self.config.proactive
            if proactive.enabled and self.proactive_idle_task:
                interval = max(5, int(proactive.revisit_interval_minutes or 30))
                self.scheduler.add_job(
                    self.proactive_idle_task, 'interval', minutes=interval,
                    id="proactive_idle_check", replace_existing=True
                )
            if proactive.enabled and proactive.private_revisit_enabled and self.proactive_revisit_task:
                interval = max(5, int(proactive.revisit_interval_minutes or 30))
                self.scheduler.add_job(
                    self.proactive_revisit_task, 'interval', minutes=interval,
                    id="private_revisit_check", replace_existing=True
                )

            lifecycle = self.config.lifecycle
            if self.review_task:
                rh, rm = parse_schedule_time(lifecycle.review_time, default="23:45")
                lifecycle.review_time = f"{rh:02d}:{rm:02d}"
                self.scheduler.add_job(
                    self.review_task, 'cron', hour=rh, minute=rm,
                    id="daily_review", replace_existing=True
                )
                
            if not self.scheduler.running:
                self.scheduler.start()
        except Exception as e:
            logger.error(f"[日常生活] 调度器初始化失败：{e}")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()

    def update_daily_time(self, new_time: str):
        h, m = parse_schedule_time(new_time)
        normalized_time = f"{h:02d}:{m:02d}"
        self.config.schedule_time = normalized_time
        if self.scheduler.get_job("daily"):
            self.scheduler.reschedule_job("daily", trigger="cron", hour=h, minute=m)
        else:
            self.scheduler.add_job(
                self.daily_task, 'cron', hour=h, minute=m, id="daily", replace_existing=True
            )
        logger.info(f"[日常生活] 每日生活背景任务已重新排程至 {normalized_time}")
