import copy
import datetime
import sys
import types
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


def _install_stubs():
    modules = {
        "aiohttp": types.ModuleType("aiohttp"),
        "apscheduler": types.ModuleType("apscheduler"),
        "apscheduler.executors": types.ModuleType("apscheduler.executors"),
        "apscheduler.executors.asyncio": types.ModuleType("apscheduler.executors.asyncio"),
        "apscheduler.schedulers": types.ModuleType("apscheduler.schedulers"),
        "apscheduler.schedulers.asyncio": types.ModuleType("apscheduler.schedulers.asyncio"),
        "chinese_calendar": types.ModuleType("chinese_calendar"),
        "astrbot": types.ModuleType("astrbot"),
        "astrbot.api": types.ModuleType("astrbot.api"),
        "astrbot.api.star": types.ModuleType("astrbot.api.star"),
        "astrbot.api.event": types.ModuleType("astrbot.api.event"),
        "astrbot.api.event.filter": types.ModuleType("astrbot.api.event.filter"),
        "astrbot.api.message_components": types.ModuleType("astrbot.api.message_components"),
        "astrbot.core": types.ModuleType("astrbot.core"),
        "astrbot.core.provider": types.ModuleType("astrbot.core.provider"),
        "astrbot.core.provider.entities": types.ModuleType("astrbot.core.provider.entities"),
        "astrbot.core.star": types.ModuleType("astrbot.core.star"),
        "astrbot.core.star.context": types.ModuleType("astrbot.core.star.context"),
        "astrbot.core.star.star_tools": types.ModuleType("astrbot.core.star.star_tools"),
    }
    modules["astrbot.api"].__path__ = []
    modules["astrbot.core.star"].__path__ = []
    modules["astrbot.api"].logger = _Logger()
    modules["aiohttp"].ClientSession = object
    modules["aiohttp"].ClientTimeout = lambda *args, **kwargs: types.SimpleNamespace(args=args, kwargs=kwargs)

    class _Star:
        def __init__(self, *args, **kwargs):
            pass

    class _EventMessageType:
        GROUP_MESSAGE = 1
        PRIVATE_MESSAGE = 2

    class _MessageChain:
        def __init__(self):
            self.items = []
            self.chain = self.items

        def message(self, text):
            self.items.append(str(text))
            return self

        def file_image(self, path):
            self.items.append({"type": "image", "file": str(path)})
            return self

        def url_image(self, url):
            self.items.append({"type": "image", "url": str(url)})
            return self

    class _Image:
        @staticmethod
        def fromFileSystem(path):
            return {"type": "image", "file": str(path)}

        @staticmethod
        def fromURL(url):
            return {"type": "image", "url": str(url), "file": str(url)}

    class _Video:
        @staticmethod
        def fromURL(url):
            return {"type": "video", "url": str(url), "file": str(url)}

        @staticmethod
        def fromFileSystem(path):
            return {"type": "video", "file": str(path)}

    class _Record:
        def __init__(self, file=None, **kwargs):
            self.type = "record"
            self.file = str(file or "")
            self.extra = kwargs

        def __eq__(self, other):
            return isinstance(other, dict) and other.get("type") == "record" and other.get("file") == self.file

    class _ProviderRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.prompt = str(kwargs.get("prompt") or "")
            self.system_prompt = str(kwargs.get("system_prompt") or "")
            self.contexts = list(kwargs.get("contexts") or [])
            self.session_id = str(kwargs.get("session_id") or "")

    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = _Star
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.event"].MessageChain = _MessageChain
    modules["astrbot.api.event"].filter = types.SimpleNamespace(
        llm_tool=lambda **kwargs: (lambda func: func),
        on_llm_request=lambda *args, **kwargs: (lambda func: func),
        on_llm_response=lambda *args, **kwargs: (lambda func: func),
        on_decorating_result=lambda *args, **kwargs: (lambda func: func),
        after_message_sent=lambda *args, **kwargs: (lambda func: func),
        event_message_type=lambda *args, **kwargs: (lambda func: func),
        command=lambda *args, **kwargs: (lambda func: func),
    )
    modules["astrbot.api.message_components"].Image = _Image
    modules["astrbot.api.message_components"].Video = _Video
    modules["astrbot.api.message_components"].Record = _Record
    modules["astrbot.api.event.filter"].EventMessageType = _EventMessageType
    modules["astrbot.core.provider.entities"].ProviderRequest = _ProviderRequest
    modules["astrbot.core.star.context"].Context = object
    modules["astrbot.core.star.star_tools"].StarTools = types.SimpleNamespace(
        get_data_dir=lambda plugin_id: PLUGIN_ROOT / ".test-data" / str(plugin_id)
    )
    modules["chinese_calendar"].get_holiday_detail = lambda date: (False, None)
    modules["chinese_calendar"].is_workday = lambda date: date.weekday() < 5

    class _AsyncIOScheduler:
        def __init__(self, *args, **kwargs):
            self.running = False
            self.jobs = {}

        def add_job(self, func, trigger, **kwargs):
            self.jobs[kwargs.get("id")] = (func, trigger, kwargs)

        def get_job(self, job_id):
            return self.jobs.get(job_id)

        def reschedule_job(self, job_id, **kwargs):
            if job_id in self.jobs:
                func, trigger, old_kwargs = self.jobs[job_id]
                old_kwargs.update(kwargs)
                self.jobs[job_id] = (func, trigger, old_kwargs)

        def start(self):
            self.running = True

        def shutdown(self):
            self.running = False

    modules["apscheduler.executors.asyncio"].AsyncIOExecutor = object
    modules["apscheduler.schedulers.asyncio"].AsyncIOScheduler = _AsyncIOScheduler
    sys.modules.update(modules)


_install_stubs()

from core.interface import DailyLifeCommandCenter, DailyLifeDashboardMixin  # noqa: E402
from core.sources import ContactNameResolver  # noqa: E402
from core.clock import today as life_today  # noqa: E402
from astrbot.core.provider.entities import ProviderRequest  # noqa: E402
from core.models import (  # noqa: E402
    ActionDecisionRecord,
    BehaviorFeedbackRecord,
    BehaviorPatternRecord,
    BehaviorSceneRecord,
    CatalogItemRecord,
    ChatSummaryRecord,
    CommitmentRecord,
    DayRecord,
    EmojiAssetRecord,
    EventRecord,
    ExpressionIntentRecord,
    ExpressionProfileRecord,
    ExpressionReviewRecord,
    FocusSlotRecord,
    FocusTargetRecord,
    GroupEnvironmentRecord,
    HairStyleRecord,
    DailyReviewRecord,
    LifeEpisodeRecord,
    LifeEventRecord,
    LifeState,
    LifeTermRecord,
    MemoryBoundaryRecord,
    MemoryCorrectionRecord,
    MemoryEvidenceRecord,
    MemoryMaintenanceRecord,
    MessageVisibilityRecord,
    PlaceRecord,
    PreferenceRecord,
    RelationshipNote,
    RelationshipPoint,
    RelationshipRecord,
    RelationshipContactRecord,
    ReplyEffectRecord,
    SessionMidSummaryRecord,
    TemporaryExpressionStateRecord,
    TimelineItem,
    WeekPlanRecord,
    WeekTemplateRecord,
)
from core.life import LifeBackgroundComposer  # noqa: E402
from core.runtime import DailyLifeRuntime  # noqa: E402
from core.presets import (  # noqa: E402
    DEFAULT_DAILY_THEMES,
    DEFAULT_MOOD_COLORS,
    DEFAULT_NIGHT_HAIRSTYLES,
    DEFAULT_OUTFIT_STYLES,
    DEFAULT_SCHEDULE_TYPES,
    DEFAULT_SLEEP_STYLES,
    DEFAULT_STYLE_TO_HAIR_MAP,
)
from core.config.options import LifeSettings  # noqa: E402
from core.archive import LifeArchive  # noqa: E402
from core.archive.categories import STORAGE_CATEGORIES, normalize_storage_category  # noqa: E402
from core.life.tools import (  # noqa: E402
    get_current_timeline_status,
    get_time_period,
    resolve_business_now,
    resolve_daily_hint,
    resolve_daily_suggested,
)


class ConversationManager:
    def __init__(self, conversations=None):
        self.conversations = conversations or {}
        self.current_ids = {session_id: "current" for session_id in self.conversations}
        self.calls = []
        self.next_id = 1

    async def get_curr_conversation_id(self, session_id):
        self.calls.append(("get_curr_conversation_id", session_id))
        return self.current_ids.get(session_id)

    async def get_conversation(self, session_id, cid):
        self.calls.append(("get_conversation", session_id, cid))
        return self.conversations.get(session_id)

    async def new_conversation(self, session_id, platform_id=None, content=None, title=None, persona_id=None):
        self.calls.append(("new_conversation", session_id, platform_id, content, title, persona_id))
        cid = f"created-{self.next_id}"
        self.next_id += 1
        self.current_ids[session_id] = cid
        self.conversations[session_id] = types.SimpleNamespace(history=content or [])
        return cid

    async def update_conversation(
        self,
        session_id,
        conversation_id=None,
        history=None,
        title=None,
        persona_id=None,
        token_usage=None,
    ):
        self.calls.append(("update_conversation", session_id, conversation_id, history, title, persona_id, token_usage))
        self.current_ids[session_id] = conversation_id or self.current_ids.get(session_id) or "current"
        conversation = self.conversations.get(session_id)
        if not conversation:
            conversation = types.SimpleNamespace(history=[])
            self.conversations[session_id] = conversation
        if history is not None:
            conversation.history = history

    async def delete_conversation(self, session_id, cid):
        pass


class Provider:
    def __init__(self, responses=(), system_prompt="测试人格住在北京，喜欢甜妹风和小甜点。", provider_id=""):
        self.responses = list(responses)
        self.prompts = []
        self.system_prompts = []
        self.vision_prompts = []
        self.system_prompt = system_prompt
        self.provider_id = provider_id

    def meta(self):
        return types.SimpleNamespace(id=self.provider_id)

    async def text_chat(self, prompt, session_id=None, system_prompt=None, **kwargs):
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)
        text = self.responses.pop(0) if self.responses else ""
        if isinstance(text, BaseException):
            raise text
        return types.SimpleNamespace(completion_text=text)

    async def image_chat(self, prompt, image="", session_id=None, **kwargs):
        self.vision_prompts.append({"prompt": prompt, "image": image, "session_id": session_id, "kwargs": kwargs})
        text = self.responses.pop(0) if self.responses else ""
        if isinstance(text, BaseException):
            raise text
        return types.SimpleNamespace(completion_text=text)


class AltProvider(Provider):
    async def text_chat(self, prompt, session_id=None, system_prompt=None, **kwargs):
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)
        text = self.responses.pop(0) if self.responses else ""
        if isinstance(text, BaseException):
            raise text
        return {"content": text}


class Context:
    def __init__(
        self,
        provider,
        selected_provider=None,
        conversation_manager=None,
        persona_manager=None,
        providers=None,
        config=None,
    ):
        self.provider = provider
        self.selected_provider = selected_provider
        self.conversation_manager = conversation_manager or ConversationManager()
        self.persona_manager = persona_manager
        self.providers = dict(providers or {})
        if selected_provider:
            self.providers.setdefault("selected", selected_provider)
        self.config = dict(config or {})
        self.sent_messages = []
        self.send_failures = {}
        self.message_history_manager = types.SimpleNamespace(
            records={},
            inserts=[],
        )

        async def insert(platform_id, user_id, content, sender_id=None, sender_name=None, llm_checkpoint_id=None):
            record = types.SimpleNamespace(
                platform_id=platform_id,
                user_id=user_id,
                content=content,
                sender_id=sender_id,
                sender_name=sender_name,
                llm_checkpoint_id=llm_checkpoint_id,
            )
            self.message_history_manager.inserts.append(record)
            self.message_history_manager.records.setdefault((platform_id, user_id), []).append(record)
            return record

        async def get(platform_id, user_id, page=1, page_size=200):
            return list(self.message_history_manager.records.get((platform_id, user_id), []))

        self.message_history_manager.insert = insert
        self.message_history_manager.get = get

    def get_using_provider(self):
        return self.provider

    async def get_provider_by_id(self, provider_id):
        return self.providers.get(provider_id)

    async def send_message(self, uid, chain):
        failure = self.send_failures.get(uid)
        if failure:
            raise failure
        self.sent_messages.append((uid, chain))

    def get_config(self, umo=None):
        return self.config


class DataManager:
    def __init__(self):
        self.days = {}
        self.week_plans = {}
        self.week_templates = {}
        self.catalog_items = {}
        self.hair_styles = {}
        self.builtin_states = {}
        self.commitments = {}
        self.day_commitments = {}
        self.next_commitment_id = 1
        self.relationships = {}
        self.relationship_contacts = {}
        self.chat_summaries = {}
        self.next_chat_summary_id = 1
        self.group_environments = {}
        self.next_group_environment_id = 1
        self.message_visibility = {}
        self.next_message_visibility_id = 1
        self.action_decisions = {}
        self.next_action_decision_id = 1
        self.places = {}
        self.events = []
        self.daily_reviews = {}
        self.preferences = {}
        self.next_preference_id = 1
        self.life_events = {}
        self.next_life_event_id = 1
        self.life_episodes = {}
        self.next_life_episode_id = 1
        self.memory_evidence = {}
        self.next_memory_evidence_id = 1
        self.behavior_feedback = {}
        self.next_behavior_feedback_id = 1
        self.reply_effects = {}
        self.next_reply_effect_id = 1
        self.memory_corrections = {}
        self.next_memory_correction_id = 1
        self.expression_profiles = {}
        self.next_expression_profile_id = 1
        self.expression_reviews = {}
        self.next_expression_review_id = 1
        self.behavior_patterns = {}
        self.next_behavior_pattern_id = 1
        self.behavior_scenes = {}
        self.next_behavior_scene_id = 1
        self.session_mid_summaries = {}
        self.temporary_expression_states = {}
        self.next_temporary_expression_state_id = 1
        self.focus_slots = {}
        self.next_focus_slot_id = 1
        self.focus_targets = {}
        self.next_focus_target_id = 1
        self.expression_intents = {}
        self.next_expression_intent_id = 1
        self.emoji_assets = {}
        self.next_emoji_asset_id = 1
        self.life_terms = {}
        self.next_life_term_id = 1
        self.memory_boundaries = {}
        self.next_memory_boundary_id = 1
        self.memory_maintenance = {}
        self.next_memory_maintenance_id = 1

    @staticmethod
    def _text(value):
        return str(value or "").strip()

    async def get_day(self, date_str):
        return self.days.get(date_str)

    async def save_day(self, day):
        self.days[day.date] = day

    async def replace_day_timeline(self, date_str, timeline):
        day = self.days.get(date_str)
        if not day:
            return None
        day.timeline = [TimelineItem.from_value(item) for item in timeline]
        self.days[date_str] = day
        return day

    async def delete_day(self, date_str):
        self.days.pop(date_str, None)

    async def get_all_week_plans(self):
        return dict(self.week_plans)

    async def save_week_plan(self, plan):
        self.week_plans[plan.week_id] = plan

    async def get_custom_week_templates(self, include_disabled=False):
        if include_disabled:
            return dict(self.week_templates)
        return {key: value for key, value in self.week_templates.items() if value.enabled}

    async def save_custom_week_template(self, template):
        self.week_templates[template.template_id] = template
        return template

    async def set_custom_week_template_weight(self, template_id, weight):
        template = self.week_templates.get(template_id)
        if not template:
            return False
        template.weight = max(float(weight), 0.0)
        return True

    async def set_custom_week_template_enabled(self, template_id, enabled):
        template = self.week_templates.get(template_id)
        if not template:
            return False
        template.enabled = bool(enabled)
        return True

    async def delete_custom_week_template(self, template_id):
        return self.week_templates.pop(template_id, None) is not None

    async def get_builtin_item_states(self, kind, scope=""):
        return dict(self.builtin_states.get((kind, scope), {}))

    async def set_builtin_item_enabled(self, kind, item_id, enabled, scope=""):
        self.builtin_states.setdefault((kind, scope), {})[item_id] = bool(enabled)
        return True

    async def save_commitment(self, commitment):
        item = commitment if isinstance(commitment, CommitmentRecord) else CommitmentRecord.from_value(commitment)
        if not item:
            raise ValueError("承诺内容不能为空")
        if not item.id:
            item.id = self.next_commitment_id
            self.next_commitment_id += 1
        self.commitments[item.id] = item
        return item

    async def get_commitments(self, status="active", limit=20):
        values = list(self.commitments.values())
        if status:
            values = [item for item in values if item.status == status]
        values.sort(key=lambda item: (item.trigger_date or "9999-12-31", -item.id))
        return values[:limit] if limit > 0 else values

    async def get_commitment(self, commitment_id):
        return self.commitments.get(int(commitment_id))

    async def get_due_commitments(self, date_str, include_scheduled=False):
        statuses = {"active", "scheduled"} if include_scheduled else {"active"}
        return [
            item
            for item in self.commitments.values()
            if item.status in statuses
            and (item.trigger_date == date_str or (not item.trigger_date and item.time_window in {"next_chat", "next_time"}))
        ]

    async def set_commitment_status(self, commitment_id, status, when=""):
        item = self.commitments.get(int(commitment_id))
        if not item:
            return False
        item.status = status
        if status in {"done", "cancelled", "expired"}:
            item.completed_at = when
        else:
            item.activated_at = when
        return True

    async def reschedule_commitment(self, commitment_id, trigger_date, time_window=""):
        item = self.commitments.get(int(commitment_id))
        if not item:
            return False
        item.trigger_date = trigger_date
        item.time_window = time_window
        item.status = "active"
        item.activated_at = ""
        return True

    async def link_commitments_to_day(self, date_str, commitment_ids):
        ids = [int(item) for item in commitment_ids if int(item or 0) > 0]
        self.day_commitments.setdefault(date_str, set()).update(ids)
        for commitment_id in ids:
            item = self.commitments.get(commitment_id)
            if item and item.status == "active":
                item.status = "scheduled"

    async def get_custom_catalog_items(self, include_disabled=False):
        items = {}
        for (category, item_id), value in self.catalog_items.items():
            if include_disabled or value.enabled:
                items.setdefault(category, []).append(value)
        for category in items:
            items[category].sort(key=lambda item: (item.sort_order, item.item_id))
        return items

    async def save_custom_catalog_item(self, item):
        if not item.item_id:
            item.item_id = f"item_{len(self.catalog_items) + 1}"
        if not item.sort_order:
            item.sort_order = len([key for key in self.catalog_items if key[0] == item.category])
        self.catalog_items[(item.category, item.item_id)] = item
        return item

    async def set_custom_catalog_item_enabled(self, category, item_id, enabled):
        item = self.catalog_items.get((category, item_id))
        if not item:
            return False
        item.enabled = bool(enabled)
        return True

    async def delete_custom_catalog_item(self, category, item_id):
        return self.catalog_items.pop((category, item_id), None) is not None

    async def get_custom_hair_styles(self, include_disabled=False):
        values = list(self.hair_styles.values())
        if not include_disabled:
            values = [item for item in values if item.enabled]
        values.sort(key=lambda item: (item.sort_order, item.style_id))
        return {item.style_id: item for item in values}

    async def save_custom_hair_style(self, style):
        if not style.style_id:
            style.style_id = f"hair_{len(self.hair_styles) + 1}"
        if not style.sort_order:
            style.sort_order = len(self.hair_styles)
        self.hair_styles[style.style_id] = style
        return style

    async def set_custom_hair_style_enabled(self, style_id, enabled):
        style = self.hair_styles.get(style_id)
        if not style:
            return False
        style.enabled = bool(enabled)
        return True

    async def delete_custom_hair_style(self, style_id):
        return self.hair_styles.pop(style_id, None) is not None

    async def add_events(self, date_str, events):
        for event in events or []:
            item = event if isinstance(event, EventRecord) else EventRecord.from_value(event, date=date_str)
            if item and item.summary:
                self.events.append(item)

    async def get_recent_events(self, limit=8):
        recent = self.events[-limit:] if limit > 0 else self.events
        return list(reversed(recent))

    async def touch_places(self, date_str, places, source="daily"):
        for place in places or []:
            place = place if isinstance(place, PlaceRecord) else PlaceRecord.from_value(place)
            if not place:
                continue
            name = place.name
            place_type = place.type
            hint = place.hint
            if not name:
                continue
            current = self.places.get(name)
            if not current:
                current = PlaceRecord(
                    name=name,
                    type=place_type,
                    hint=hint,
                    visits=0,
                    first_seen=date_str,
                    source=source,
                )
                self.places[name] = current
            current.visits += 1
            current.last_seen = date_str
            current.source = source
            if hint:
                current.hint = hint
            if place_type and current.type == "place":
                current.type = place_type

    async def get_recent_places(self, limit=10):
        values = list(self.places.values())
        values.sort(key=lambda item: (str(item.last_seen), int(item.visits)), reverse=True)
        return values[:limit] if limit > 0 else values

    async def touch_relationship(
        self,
        profile_id,
        name="",
        note="",
        date_str="",
        source="chat",
        platform="",
        user_id="",
        alias="",
        persona_hint="",
        subjective_name="",
        subjective_tags=None,
        relationship_story="",
        contact_type="",
        target_scope="",
        group_id="",
        group_name="",
        is_reachable=True,
        blocked_reason="",
    ):
        key = str(profile_id or name or "").strip()
        if not key:
            return
        profile = self.relationships.get(key)
        if not profile:
            profile = RelationshipRecord(
                id=key,
                name=name or key,
                interactions=0,
                first_seen=date_str,
            )
            self.relationships[key] = profile
        if name and (not profile.name or profile.name == key):
            profile.name = name
        profile.platform = platform or profile.platform
        profile.user_id = user_id or profile.user_id
        profile.alias = alias or profile.alias
        profile.persona_hint = persona_hint or profile.persona_hint
        profile.subjective_name = self._text(subjective_name) or profile.subjective_name
        if subjective_tags:
            profile.subjective_tags = [self._text(item) for item in subjective_tags if self._text(item)]
        profile.relationship_story = self._text(relationship_story) or profile.relationship_story
        profile.interactions += 1
        profile.last_seen = date_str
        profile.source = source
        if note:
            profile.notes.append(RelationshipNote(date=date_str, content=self._text(note), source=source))
            profile.notes = profile.notes[-20:]
        if contact_type or target_scope or group_id:
            await self.touch_relationship_contact(
                key,
                platform=platform or profile.platform,
                user_id=user_id or profile.user_id,
                contact_type=contact_type or "unknown",
                target_scope=target_scope,
                group_id=group_id,
                group_name=group_name,
                date_str=date_str,
                is_reachable=is_reachable,
                blocked_reason=blocked_reason,
                source=source,
            )

    async def touch_relationship_contact(
        self,
        profile_id,
        *,
        platform="",
        user_id="",
        contact_type="unknown",
        target_scope="",
        group_id="",
        group_name="",
        date_str="",
        is_reachable=True,
        blocked_reason="",
        source="chat",
    ):
        key = str(profile_id or "").strip()
        if not key:
            return
        contact_key = (key, str(contact_type or "unknown").strip(), str(target_scope or "").strip(), str(group_id or "").strip())
        current = self.relationship_contacts.get(contact_key)
        first_seen = current.first_seen if current else date_str
        contact = RelationshipContactRecord(
            profile_id=key,
            platform=platform,
            user_id=user_id,
            contact_type=contact_key[1],
            target_scope=contact_key[2],
            group_id=contact_key[3],
            group_name=group_name or (current.group_name if current else ""),
            first_seen=first_seen,
            last_seen=date_str,
            is_reachable=bool(is_reachable),
            blocked_reason=self._text(blocked_reason),
            source=source,
        )
        self.relationship_contacts[contact_key] = contact
        profile = self.relationships.get(key)
        if profile:
            profile.contacts = [
                item for item in self.relationship_contacts.values() if item.profile_id == key
            ]

    async def mark_relationship_contact_unreachable(self, target_scope, reason, *, contact_type="friend"):
        scope = str(target_scope or "").strip()
        kind = str(contact_type or "friend").strip()
        for key, contact in list(self.relationship_contacts.items()):
            if contact.target_scope == scope and contact.contact_type == kind:
                contact.is_reachable = False
                contact.blocked_reason = self._text(reason)
                profile = self.relationships.get(contact.profile_id)
                if profile:
                    profile.contacts = [
                        item for item in self.relationship_contacts.values() if item.profile_id == contact.profile_id
                    ]

    async def get_reachable_relationship_contacts(self, profile_id, *, contact_type="friend"):
        key = str(profile_id or "").strip()
        kind = str(contact_type or "friend").strip()
        contacts = [
            item for item in self.relationship_contacts.values()
            if item.profile_id == key and item.contact_type == kind and item.is_reachable
        ]
        contacts.sort(key=lambda item: str(item.last_seen), reverse=True)
        return contacts

    async def add_relationship_point(self, profile_id, content, date_str="", source="memory", weight=1.0):
        key = str(profile_id or "").strip()
        text = self._text(content)
        if not key or not text:
            return
        profile = self.relationships.get(key)
        if not profile:
            profile = RelationshipRecord(id=key, name=key, first_seen=date_str, last_seen=date_str, source=source)
            self.relationships[key] = profile
        if profile.memory_points and profile.memory_points[-1].content == text:
            return
        profile.memory_points.append(
            RelationshipPoint(date=date_str, content=text, source=source, weight=float(weight or 0.0))
        )
        profile.memory_points = profile.memory_points[-20:]

    async def get_recent_relationships(self, limit=8):
        values = list(self.relationships.values())
        for profile in values:
            profile.contacts = [
                item for item in self.relationship_contacts.values() if item.profile_id == profile.id
            ]
        values.sort(key=lambda item: (str(item.last_seen), int(item.interactions)), reverse=True)
        return values[:limit] if limit > 0 else values

    async def get_relationship(self, profile_id):
        return self.relationships.get(str(profile_id or "").strip())

    async def revise_relationship_profile(
        self,
        profile_id,
        *,
        date_str="",
        source="语义校准",
        subjective_name="",
        subjective_tags=None,
        relationship_story="",
        note="",
        relationship_points=None,
    ):
        key = str(profile_id or "").strip()
        profile = self.relationships.get(key)
        if not profile:
            return
        if subjective_name:
            profile.subjective_name = self._text(subjective_name)
        if subjective_tags:
            profile.subjective_tags = [self._text(item) for item in subjective_tags if self._text(item)]
        if relationship_story:
            profile.relationship_story = self._text(relationship_story)
        profile.source = source
        if note:
            profile.notes.append(RelationshipNote(date=date_str, content=self._text(note), source=source))
            profile.notes = profile.notes[-20:]
        for point in relationship_points or []:
            text = self._text(point)
            if not text:
                continue
            if profile.memory_points and profile.memory_points[-1].content == text:
                continue
            profile.memory_points.append(RelationshipPoint(date=date_str, content=text, source=source, weight=1.0))
        profile.memory_points = profile.memory_points[-20:]

    async def save_chat_summary(self, summary):
        item = summary if isinstance(summary, ChatSummaryRecord) else ChatSummaryRecord.from_value(summary)
        if not item:
            raise ValueError("聊天摘要不能为空")
        if not item.id:
            item.id = self.next_chat_summary_id
            self.next_chat_summary_id += 1
        self.chat_summaries[item.id] = item
        return item

    async def get_recent_chat_summaries(self, limit=8):
        values = sorted(self.chat_summaries.values(), key=lambda item: item.id, reverse=True)
        return values[:limit] if limit > 0 else values

    async def save_group_environment(self, environment):
        item = environment if isinstance(environment, GroupEnvironmentRecord) else GroupEnvironmentRecord.from_value(environment)
        if not item:
            raise ValueError("群聊环境快照不能为空")
        item.topic = self._text(item.topic)
        item.summary = self._text(item.summary)
        if not item.id:
            item.id = self.next_group_environment_id
            self.next_group_environment_id += 1
        self.group_environments[item.id] = item
        return item

    async def get_recent_group_environments(self, limit=8):
        values = [item for item in self.group_environments.values() if item.group_id]
        values.sort(key=lambda item: item.id, reverse=True)
        return values[:limit] if limit > 0 else values

    @staticmethod
    def _recent_scoped_items(values, limit=8):
        latest = []
        seen = set()
        for item in sorted(values, key=lambda entry: entry.id, reverse=True):
            key = item.group_id or item.session_id or item.sender_profile_id or str(item.id)
            if key in seen:
                continue
            seen.add(key)
            latest.append(item)
            if limit > 0 and len(latest) >= limit:
                break
        return latest

    async def save_message_visibility(self, visibility):
        item = visibility if isinstance(visibility, MessageVisibilityRecord) else MessageVisibilityRecord.from_value(visibility)
        if not item:
            raise ValueError("消息可见性记录不能为空")
        item.reason = self._text(item.reason)
        item.reactivation_hint = self._text(item.reactivation_hint)
        if not item.id:
            item.id = self.next_message_visibility_id
            self.next_message_visibility_id += 1
        self.message_visibility[item.id] = item
        return item

    async def get_recent_message_visibility(self, limit=8):
        return self._recent_scoped_items(self.message_visibility.values(), limit)

    async def get_message_visibility_records(self, limit=20):
        values = sorted(self.message_visibility.values(), key=lambda item: item.id, reverse=True)
        return values[:limit] if limit > 0 else values

    async def save_action_decision(self, decision):
        item = decision if isinstance(decision, ActionDecisionRecord) else ActionDecisionRecord.from_value(decision)
        if not item:
            raise ValueError("动作裁定记录不能为空")
        item.reason = self._text(item.reason)
        item.inner_monologue = self._text(item.inner_monologue)
        item.reply_strategy = self._text(item.reply_strategy)
        if not item.id:
            item.id = self.next_action_decision_id
            self.next_action_decision_id += 1
        self.action_decisions[item.id] = item
        return item

    async def get_recent_action_decisions(self, limit=8):
        return self._recent_scoped_items(self.action_decisions.values(), limit)

    async def get_action_decision_records(self, limit=20):
        values = sorted(self.action_decisions.values(), key=lambda item: item.id, reverse=True)
        return values[:limit] if limit > 0 else values

    async def save_life_episode(self, episode):
        item = episode if isinstance(episode, LifeEpisodeRecord) else LifeEpisodeRecord.from_value(episode)
        if not item:
            raise ValueError("生活片段标题不能为空")
        if not item.id:
            item.id = self.next_life_episode_id
            self.next_life_episode_id += 1
        self.life_episodes[item.id] = item
        return item

    async def get_life_episodes(self, limit=20, status=""):
        values = list(self.life_episodes.values())
        if status:
            values = [item for item in values if item.status == status]
        values.sort(key=lambda item: (item.date, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def save_memory_evidence(self, evidence):
        item = evidence if isinstance(evidence, MemoryEvidenceRecord) else MemoryEvidenceRecord.from_value(evidence)
        if not item:
            raise ValueError("记忆证据不能为空")
        item.summary = self._text(item.summary)
        if not item.id:
            item.id = self.next_memory_evidence_id
            self.next_memory_evidence_id += 1
        self.memory_evidence[item.id] = item
        return item

    async def get_memory_evidence(self, target_type="", limit=20):
        values = list(self.memory_evidence.values())
        if target_type:
            values = [item for item in values if item.target_type == target_type]
        values.sort(key=lambda item: item.id, reverse=True)
        return values[:limit] if limit > 0 else values

    async def save_daily_review(self, review):
        item = review if isinstance(review, DailyReviewRecord) else DailyReviewRecord.from_value(review)
        if not item:
            raise ValueError("每日复盘日期不能为空")
        saved_prefs = await self.upsert_preferences(item.preference_points, item.date)
        item.preference_points = saved_prefs
        saved_events = []
        for event in item.life_events:
            saved = await self.add_life_event(event)
            if saved:
                saved_events.append(saved)
        item.life_events = saved_events
        self.daily_reviews[item.date] = item
        return item

    async def get_daily_review(self, date_str):
        return self.daily_reviews.get(date_str)

    async def get_recent_daily_reviews(self, limit=7):
        values = sorted(self.daily_reviews.values(), key=lambda item: item.date, reverse=True)
        return values[:limit] if limit > 0 else values

    async def upsert_preferences(self, preferences, date_str=""):
        saved = []
        for pref in preferences or []:
            item = pref if isinstance(pref, PreferenceRecord) else PreferenceRecord.from_value(pref, date=date_str)
            if not item:
                continue
            key = (item.category, item.content)
            current = self.preferences.get(key)
            if current:
                current.weight = min(5.0, current.weight + max(item.weight, 0.1))
                current.evidence = item.evidence or current.evidence
                current.last_seen = item.last_seen or date_str or current.last_seen
                current.source = item.source or current.source
                saved.append(current)
            else:
                item.id = self.next_preference_id
                self.next_preference_id += 1
                if not item.last_seen:
                    item.last_seen = date_str
                self.preferences[key] = item
                saved.append(item)
        return saved

    async def get_preferences(self, limit=20, category=""):
        values = list(self.preferences.values())
        if category:
            values = [item for item in values if item.category == category]
        values.sort(key=lambda item: (item.weight, item.last_seen, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def add_life_event(self, event):
        item = event if isinstance(event, LifeEventRecord) else LifeEventRecord.from_value(event)
        if not item:
            return None
        if not item.id:
            item.id = self.next_life_event_id
            self.next_life_event_id += 1
        self.life_events[item.id] = item
        return item

    async def get_life_events(self, status="", limit=20):
        values = list(self.life_events.values())
        if status:
            values = [item for item in values if item.status == status]
        values.sort(key=lambda item: (item.date, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def set_life_event_status(self, event_id, status):
        item = self.life_events.get(int(event_id))
        if not item:
            return False
        item.status = status
        return True

    async def save_life_episode(self, episode):
        item = episode if isinstance(episode, LifeEpisodeRecord) else LifeEpisodeRecord.from_value(episode)
        if not item:
            raise ValueError("生活片段标题不能为空")
        if not item.id:
            existing = next(
                (
                    current
                    for current in self.life_episodes.values()
                    if current.date == item.date
                    and current.title == item.title
                    and current.source == item.source
                    and not current.protected
                ),
                None,
            )
            if existing:
                item.id = existing.id
            else:
                item.id = self.next_life_episode_id
                self.next_life_episode_id += 1
        self.life_episodes[item.id] = item
        return item

    async def get_life_episodes(self, limit=20, status=""):
        values = list(self.life_episodes.values())
        if status:
            values = [item for item in values if item.status == status]
        values.sort(key=lambda item: (item.date, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def correct_life_episode(self, episode_id, correction, protected=True):
        item = self.life_episodes.get(int(episode_id))
        if not item:
            return False
        item.correction = str(correction or "").strip()
        item.protected = bool(protected)
        item.status = "corrected"
        return True

    async def set_life_episode_protected(self, episode_id, protected):
        item = self.life_episodes.get(int(episode_id))
        if not item:
            return False
        item.protected = bool(protected)
        return True

    async def save_memory_evidence(self, evidence):
        item = evidence if isinstance(evidence, MemoryEvidenceRecord) else MemoryEvidenceRecord.from_value(evidence)
        if not item:
            return None
        item.summary = self._text(item.summary)
        if not item.id:
            item.id = self.next_memory_evidence_id
            self.next_memory_evidence_id += 1
        self.memory_evidence[item.id] = item
        return item

    async def get_memory_evidence(self, target_type="", target_id="", limit=30):
        values = list(self.memory_evidence.values())
        if target_type:
            values = [item for item in values if item.target_type == target_type]
        if target_id:
            values = [item for item in values if item.target_id == target_id]
        values.sort(key=lambda item: item.id, reverse=True)
        return values[:limit] if limit > 0 else values

    async def add_behavior_feedback(self, feedback):
        item = feedback if isinstance(feedback, BehaviorFeedbackRecord) else BehaviorFeedbackRecord.from_value(feedback)
        if not item:
            return None
        for existing in sorted(self.behavior_feedback.values(), key=lambda entry: entry.id, reverse=True):
            if (
                existing.date == item.date
                and existing.target_type == item.target_type
                and existing.target_id == item.target_id
                and existing.scene == item.scene
                and existing.action == item.action
                and existing.feedback == item.feedback
                and existing.result == item.result
                and existing.reason == item.reason
                and existing.source == item.source
            ):
                return existing
        if not item.id:
            item.id = self.next_behavior_feedback_id
            self.next_behavior_feedback_id += 1
        self.behavior_feedback[item.id] = item
        return item

    async def get_behavior_feedback(self, limit=20):
        values = sorted(self.behavior_feedback.values(), key=lambda item: (item.date, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def save_reply_effect(self, effect):
        item = effect if isinstance(effect, ReplyEffectRecord) else ReplyEffectRecord.from_value(effect)
        if not item:
            return None
        if not item.id:
            item.id = self.next_reply_effect_id
            self.next_reply_effect_id += 1
        self.reply_effects[item.id] = item
        return item

    async def update_reply_effect_outcome(self, effect_id, *, outcome, evidence="", warmth=None, continuity=None, friction=None):
        item = self.reply_effects.get(int(effect_id))
        if not item:
            return False
        item.outcome = str(outcome or item.outcome)
        item.evidence = str(evidence or item.evidence)
        if warmth is not None:
            item.warmth = max(0, min(int(warmth), 100))
        if continuity is not None:
            item.continuity = max(0, min(int(continuity), 100))
        if friction is not None:
            item.friction = max(0, min(int(friction), 100))
        return True

    async def expire_stale_reply_effects(self, max_age_seconds, *, evidence="闲时续话后一段时间内没有新的可见回应"):
        now = datetime.datetime.now()
        expired = 0
        for item in self.reply_effects.values():
            if item.outcome != "pending":
                continue
            raw_time = item.updated_at or item.created_at
            try:
                updated_at = datetime.datetime.fromisoformat(str(raw_time or "").replace(" ", "T"))
            except ValueError:
                continue
            if (now - updated_at).total_seconds() <= max(int(max_age_seconds or 0), 1):
                continue
            item.outcome = "ignored"
            item.evidence = item.evidence or evidence
            item.warmth = min(item.warmth, 35)
            item.continuity = min(item.continuity, 25)
            item.friction = max(item.friction, 10)
            item.updated_at = now.strftime("%Y-%m-%d %H:%M:%S")
            expired += 1
        return expired

    async def get_reply_effects(self, limit=20, *, scope="", outcome=""):
        values = list(self.reply_effects.values())
        if scope:
            values = [item for item in values if item.scope == scope]
        if outcome:
            values = [item for item in values if item.outcome == outcome]
        values.sort(key=lambda item: item.id, reverse=True)
        return values[:limit] if limit > 0 else values

    async def save_memory_correction(self, correction):
        item = correction if isinstance(correction, MemoryCorrectionRecord) else MemoryCorrectionRecord.from_value(correction)
        if not item:
            return None
        if not item.id:
            item.id = self.next_memory_correction_id
            self.next_memory_correction_id += 1
        self.memory_corrections[item.id] = item
        return item

    async def mark_memory_correction_applied(self, correction_id, applied=True):
        item = self.memory_corrections.get(int(correction_id))
        if not item:
            return False
        item.applied = bool(applied)
        return True

    async def get_memory_corrections(self, limit=20, *, target_type="", target_id="", unapplied_only=False):
        values = list(self.memory_corrections.values())
        if target_type:
            values = [item for item in values if item.target_type == target_type]
        if target_id:
            values = [item for item in values if item.target_id == target_id]
        if unapplied_only:
            values = [item for item in values if not item.applied]
        values.sort(key=lambda item: item.id, reverse=True)
        return values[:limit] if limit > 0 else values

    async def upsert_expression_profile(self, profile):
        item = profile if isinstance(profile, ExpressionProfileRecord) else ExpressionProfileRecord.from_value(profile)
        if not item:
            return None
        key = (item.scope, item.profile_id, item.label or item.scope or item.profile_id)
        existing = next(
            (
                current
                for current in self.expression_profiles.values()
                if (current.scope, current.profile_id, current.label) == key
            ),
            None,
        )
        if existing:
            item.id = existing.id
            item.confidence = max(existing.confidence, item.confidence)
            item.label = item.label or existing.label
            item.tone = item.tone or existing.tone
            item.habits = item.habits or existing.habits
            item.avoid = item.avoid or existing.avoid
        elif not item.id:
            item.id = self.next_expression_profile_id
            self.next_expression_profile_id += 1
        if not item.label:
            item.label = key[2] or "表达习惯"
        self.expression_profiles[item.id] = item
        return item

    async def get_expression_profiles(self, limit=20, *, scope="", profile_id=""):
        values = list(self.expression_profiles.values())
        if scope:
            values = [item for item in values if item.scope == scope]
        if profile_id:
            values = [item for item in values if item.profile_id == profile_id]
        values.sort(key=lambda item: (item.confidence, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def save_expression_review(self, review):
        item = review if isinstance(review, ExpressionReviewRecord) else ExpressionReviewRecord.from_value(review)
        if not item:
            return None
        if not item.id:
            item.id = self.next_expression_review_id
            self.next_expression_review_id += 1
        self.expression_reviews[item.id] = item
        return item

    async def get_expression_reviews(self, limit=20, *, scope="", passed=None):
        values = list(self.expression_reviews.values())
        if scope:
            values = [item for item in values if item.scope == scope]
        if passed is not None:
            values = [item for item in values if item.passed == bool(passed)]
        values.sort(key=lambda item: item.id, reverse=True)
        return values[:limit] if limit > 0 else values

    async def upsert_behavior_pattern(self, pattern):
        item = pattern if isinstance(pattern, BehaviorPatternRecord) else BehaviorPatternRecord.from_value(pattern)
        if not item:
            return None
        key = (item.scope, item.scene, item.pattern)
        existing = next(
            (
                current
                for current in self.behavior_patterns.values()
                if (current.scope, current.scene, current.pattern) == key
            ),
            None,
        )
        if existing:
            existing.suggested_action = item.suggested_action or existing.suggested_action
            existing.confidence = max(existing.confidence, item.confidence)
            existing.support_count += max(1, item.support_count)
            existing.score = max(-5.0, min(5.0, existing.score + item.score))
            existing.evidence = item.evidence or existing.evidence
            existing.last_seen = item.last_seen or existing.last_seen
            return existing
        if not item.id:
            item.id = self.next_behavior_pattern_id
            self.next_behavior_pattern_id += 1
        self.behavior_patterns[item.id] = item
        return item

    async def get_behavior_patterns(self, limit=20, *, scope="", scene=""):
        values = list(self.behavior_patterns.values())
        if scope:
            values = [item for item in values if item.scope in {scope, ""}]
        if scene:
            values = [item for item in values if item.scene == scene]
        values.sort(key=lambda item: (item.confidence, item.support_count, item.last_seen, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def upsert_behavior_scene(self, scene):
        item = scene if isinstance(scene, BehaviorSceneRecord) else BehaviorSceneRecord.from_value(scene)
        if not item:
            return None
        existing = next(
            (
                current
                for current in self.behavior_scenes.values()
                if current.scope == item.scope and current.scene == item.scene
            ),
            None,
        )
        if existing:
            item.id = existing.id
            item.cues = item.cues or existing.cues
            item.preferred_action = item.preferred_action or existing.preferred_action
            item.avoid_action = item.avoid_action or existing.avoid_action
            item.outcome_hint = item.outcome_hint or existing.outcome_hint
            item.confidence = max(existing.confidence, item.confidence)
            item.support_count = existing.support_count + max(1, item.support_count)
        elif not item.id:
            item.id = self.next_behavior_scene_id
            self.next_behavior_scene_id += 1
        self.behavior_scenes[item.id] = item
        return item

    async def get_behavior_scenes(self, limit=20, *, scope=""):
        values = list(self.behavior_scenes.values())
        if scope:
            values = [item for item in values if item.scope in {scope, ""}]
        values.sort(key=lambda item: (item.confidence, item.support_count, item.last_seen, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def upsert_session_mid_summary(self, summary):
        item = summary if isinstance(summary, SessionMidSummaryRecord) else SessionMidSummaryRecord.from_value(summary)
        if not item:
            return None
        current = self.session_mid_summaries.get(item.session_id)
        if current:
            item.message_count = max(item.message_count, current.message_count + 1)
            item.scope_label = item.scope_label or current.scope_label
            item.summary = item.summary or current.summary
            item.topic = item.topic or current.topic
            item.mood = item.mood or current.mood
            item.participants = item.participants or current.participants
            item.last_message_id = item.last_message_id or current.last_message_id
        elif not item.message_count:
            item.message_count = 1
        self.session_mid_summaries[item.session_id] = item
        return item

    async def get_session_mid_summaries(self, limit=20, *, session_id=""):
        values = list(self.session_mid_summaries.values())
        if session_id:
            values = [item for item in values if item.session_id == session_id]
        values.sort(key=lambda item: (item.message_count, item.updated_at), reverse=True)
        return values[:limit] if limit > 0 else values

    async def upsert_temporary_expression_state(self, state):
        item = (
            state
            if isinstance(state, TemporaryExpressionStateRecord)
            else TemporaryExpressionStateRecord.from_value(state)
        )
        if not item:
            return None
        key = (item.scope, item.label or item.tone or "临时表达状态")
        existing = next(
            (
                current
                for current in self.temporary_expression_states.values()
                if (current.scope, current.label) == key
            ),
            None,
        )
        if existing:
            item.id = existing.id
            item.label = item.label or existing.label
            item.tone = item.tone or existing.tone
            item.reason = item.reason or existing.reason
        elif not item.id:
            item.id = self.next_temporary_expression_state_id
            self.next_temporary_expression_state_id += 1
        if not item.label:
            item.label = key[1]
        self.temporary_expression_states[item.id] = item
        return item

    async def get_temporary_expression_states(self, limit=20, *, scope="", active_only=True):
        values = list(self.temporary_expression_states.values())
        if scope:
            values = [item for item in values if item.scope in {scope, ""}]
        if active_only:
            today = life_today().isoformat()
            values = [item for item in values if not item.expires_at or item.expires_at >= today]
        values.sort(key=lambda item: (item.intensity, item.updated_at, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def upsert_focus_slot(self, slot):
        item = slot if isinstance(slot, FocusSlotRecord) else FocusSlotRecord.from_value(slot)
        if not item:
            return None
        existing = next(
            (
                current
                for current in self.focus_slots.values()
                if current.scope == item.scope and current.focus_key == item.focus_key
            ),
            None,
        )
        if existing:
            item.id = existing.id
        elif not item.id:
            item.id = self.next_focus_slot_id
            self.next_focus_slot_id += 1
        self.focus_slots[item.id] = item
        return item

    async def get_focus_slots(self, limit=20, *, scope="", active_only=True):
        values = list(self.focus_slots.values())
        if scope:
            values = [item for item in values if item.scope in {scope, ""}]
        if active_only:
            today = life_today().isoformat()
            values = [item for item in values if not item.expires_at or item.expires_at >= today]
        values.sort(key=lambda item: (item.priority, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def upsert_focus_target(self, target):
        item = target if isinstance(target, FocusTargetRecord) else FocusTargetRecord.from_value(target)
        if not item:
            return None
        existing = next(
            (
                current
                for current in self.focus_targets.values()
                if current.target_type == item.target_type
                and current.target_id == item.target_id
                and current.scope == item.scope
            ),
            None,
        )
        if existing:
            item.id = existing.id
        elif not item.id:
            item.id = self.next_focus_target_id
            self.next_focus_target_id += 1
        self.focus_targets[item.id] = item
        return item

    async def get_focus_targets(self, limit=20, enabled_only=True, include_expired=False):
        values = list(self.focus_targets.values())
        if enabled_only:
            values = [item for item in values if item.enabled]
        if not include_expired:
            today = life_today().isoformat()
            values = [item for item in values if not item.expires_at or item.expires_at >= today]
        values.sort(key=lambda item: (item.priority, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def set_focus_target_enabled(self, target_id, enabled):
        item = self.focus_targets.get(int(target_id))
        if not item:
            return False
        item.enabled = bool(enabled)
        return True

    async def save_expression_intent(self, intent):
        item = intent if isinstance(intent, ExpressionIntentRecord) else ExpressionIntentRecord.from_value(intent)
        if not item:
            return None
        if not item.id:
            item.id = self.next_expression_intent_id
            self.next_expression_intent_id += 1
        self.expression_intents[item.id] = item
        return item

    async def get_expression_intents(self, limit=20, *, scope=""):
        values = list(self.expression_intents.values())
        if scope:
            values = [item for item in values if item.scope in {scope, ""}]
        values.sort(key=lambda item: item.id, reverse=True)
        return values[:limit] if limit > 0 else values

    async def upsert_emoji_asset(self, asset):
        item = asset if isinstance(asset, EmojiAssetRecord) else EmojiAssetRecord.from_value(asset)
        if not item:
            return None
        existing = next(
            (
                current
                for current in self.emoji_assets.values()
                if current.file_hash == item.file_hash
            ),
            None,
        )
        if existing:
            item.id = existing.id
            item.used_count = existing.used_count
            item.last_used_at = existing.last_used_at
            if existing.status in {"failed", "disabled"} and item.status == "pending":
                item.status = existing.status
        elif not item.id:
            item.id = self.next_emoji_asset_id
            self.next_emoji_asset_id += 1
        self.emoji_assets[item.id] = item
        return item

    async def get_emoji_asset_by_hash(self, file_hash):
        return next(
            (item for item in self.emoji_assets.values() if item.file_hash == file_hash),
            None,
        )

    async def get_emoji_assets(self, limit=20, *, status=""):
        values = list(self.emoji_assets.values())
        if status:
            values = [item for item in values if item.status == status]
        values.sort(key=lambda item: (item.used_count, item.id))
        return values[:limit] if limit > 0 else values

    async def delete_emoji_assets(self, emoji_ids):
        ids = {int(item) for item in emoji_ids if int(item or 0) > 0}
        deleted = 0
        for emoji_id in ids:
            if self.emoji_assets.pop(emoji_id, None) is not None:
                deleted += 1
        return deleted

    async def mark_emoji_used(self, emoji_id, used_at=""):
        item = self.emoji_assets.get(int(emoji_id))
        if not item:
            return False
        item.used_count += 1
        item.last_used_at = str(used_at or "")
        return True

    async def upsert_life_term(self, term):
        item = term if isinstance(term, LifeTermRecord) else LifeTermRecord.from_value(term)
        if not item:
            return None
        item.meaning = self._text(item.meaning)
        item.evidence = self._text(item.evidence)
        existing = next(
            (
                current
                for current in self.life_terms.values()
                if current.term == item.term and current.scope == item.scope
            ),
            None,
        )
        if existing:
            item.id = existing.id
            item.confidence = max(existing.confidence, item.confidence)
            item.scene = item.scene or existing.scene
            item.examples = item.examples or existing.examples
            item.familiarity = max(existing.familiarity, item.familiarity)
        elif not item.id:
            item.id = self.next_life_term_id
            self.next_life_term_id += 1
        self.life_terms[item.id] = item
        return item

    async def get_life_terms(self, limit=20, *, scope=""):
        values = list(self.life_terms.values())
        if scope:
            values = [item for item in values if item.scope in {scope, ""}]
        values = sorted(values, key=lambda item: (item.last_seen, item.confidence, item.familiarity, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def set_memory_boundary(self, boundary):
        item = boundary if isinstance(boundary, MemoryBoundaryRecord) else MemoryBoundaryRecord.from_value(boundary)
        if not item or MemoryBoundaryRecord.is_same_scope(item.source_scope, item.target_scope):
            return None
        existing = next(
            (
                current
                for current in self.memory_boundaries.values()
                if current.source_scope == item.source_scope and current.target_scope == item.target_scope
            ),
            None,
        )
        if existing:
            item.id = existing.id
        elif not item.id:
            item.id = self.next_memory_boundary_id
            self.next_memory_boundary_id += 1
        self.memory_boundaries[item.id] = item
        return item

    async def get_memory_boundaries(self, limit=20, enabled_only=True):
        values = list(self.memory_boundaries.values())
        values = [item for item in values if not MemoryBoundaryRecord.is_same_scope(item.source_scope, item.target_scope)]
        if enabled_only:
            values = [item for item in values if item.enabled]
        values.sort(key=lambda item: item.id, reverse=True)
        return values[:limit] if limit > 0 else values

    async def save_memory_maintenance(self, maintenance):
        item = (
            maintenance
            if isinstance(maintenance, MemoryMaintenanceRecord)
            else MemoryMaintenanceRecord.from_value(maintenance)
        )
        if not item:
            return None
        if not item.id:
            item.id = self.next_memory_maintenance_id
            self.next_memory_maintenance_id += 1
        self.memory_maintenance[item.id] = item
        return item

    async def get_memory_maintenance(self, limit=20):
        values = sorted(self.memory_maintenance.values(), key=lambda item: (item.date, item.id), reverse=True)
        return values[:limit] if limit > 0 else values

    async def get_life_health_report(self, policy=None):
        storage = await self.get_storage_overview(policy)
        checks = [
            {"key": "episodes", "label": "生活片段", "ok": bool(self.life_episodes), "count": len(self.life_episodes)},
            {"key": "evidence", "label": "证据链", "ok": bool(self.memory_evidence), "count": len(self.memory_evidence)},
            {"key": "focus", "label": "关注目标", "ok": bool(self.focus_targets), "count": len(self.focus_targets)},
            {"key": "feedback", "label": "行为反馈", "ok": bool(self.behavior_feedback), "count": len(self.behavior_feedback)},
            {"key": "terms", "label": "场景词", "ok": bool(self.life_terms), "count": len(self.life_terms)},
            {"key": "boundaries", "label": "记忆边界", "ok": True, "count": len(self.memory_boundaries)},
            {"key": "expression", "label": "表达习惯", "ok": bool(self.expression_profiles), "count": len(self.expression_profiles)},
            {"key": "patterns", "label": "行为模式", "ok": bool(self.behavior_patterns), "count": len(self.behavior_patterns)},
            {"key": "reply_effects", "label": "回复效果", "ok": bool(self.reply_effects), "count": len(self.reply_effects)},
            {"key": "scenes", "label": "行为场景", "ok": bool(self.behavior_scenes), "count": len(self.behavior_scenes)},
        ]
        ok_count = sum(1 for item in checks if item["ok"])
        return {
            "score": round(ok_count / len(checks) * 100),
            "checks": checks,
            "summary": f"体验层 {ok_count}/{len(checks)} 项可用",
            "storage_rows": storage["total_rows"],
        }

    def _storage_keep_days(self, category, policy=None):
        key = f"{category.key}_keep_days"
        value = None
        if isinstance(policy, dict):
            value = policy.get(key)
        elif policy is not None:
            value = getattr(policy, key, None)
        if value is None:
            value = category.default_keep_days
        try:
            return max(int(float(value)), 0)
        except (TypeError, ValueError):
            return category.default_keep_days

    def _table_counts(self):
        relationship_notes = sum(len(item.notes) for item in self.relationships.values())
        relationship_points = sum(len(item.memory_points) for item in self.relationships.values())
        review_points = sum(len(item.memory_points) for item in self.daily_reviews.values())
        review_prefs = sum(len(item.preference_points) for item in self.daily_reviews.values())
        return {
            "days": len(self.days),
            "timelines": sum(len(day.timeline) for day in self.days.values()),
            "outfit_history": sum(len(day.outfit_history) for day in self.days.values()),
            "day_meta": sum(len(day.meta) for day in self.days.values()),
            "states": sum(1 for day in self.days.values() if day.state),
            "state_logs": sum(len(day.state_log) for day in self.days.values()),
            "day_places": sum(len(day.places) for day in self.days.values()),
            "day_events": sum(len(day.new_events) for day in self.days.values()),
            "day_event_people": sum(len(event.people) for day in self.days.values() for event in day.new_events),
            "week_plans": len(self.week_plans),
            "week_goals": sum(len(plan.goals) for plan in self.week_plans.values()),
            "week_hints": sum(len(plan.daily_hints) for plan in self.week_plans.values()),
            "week_suggestions": sum(len(items) for plan in self.week_plans.values() for items in plan.suggested_activities.values()),
            "custom_week_templates": len(self.week_templates),
            "custom_week_template_goals": sum(len(item.goals) for item in self.week_templates.values()),
            "custom_week_template_hints": sum(len(item.daily_hints) for item in self.week_templates.values()),
            "custom_week_template_suggestions": sum(len(values) for item in self.week_templates.values() for values in item.suggested_activities.values()),
            "custom_week_template_tags": sum(len(item.tags) for item in self.week_templates.values()),
            "custom_catalog_items": len(self.catalog_items),
            "custom_hair_styles": len(self.hair_styles),
            "custom_hair_options": sum(len(item.hairstyles) for item in self.hair_styles.values()),
            "builtin_item_states": sum(len(values) for values in self.builtin_states.values()),
            "commitments": len(self.commitments),
            "commitment_people": sum(len(item.people) for item in self.commitments.values()),
            "day_commitments": sum(len(values) for values in self.day_commitments.values()),
            "events": len(self.events),
            "event_people": sum(len(item.people) for item in self.events),
            "places": len(self.places),
            "relationships": len(self.relationships),
            "relationship_notes": relationship_notes,
            "relationship_points": relationship_points,
            "relationship_contacts": len(self.relationship_contacts),
            "chat_summaries": len(self.chat_summaries),
            "chat_summary_people": sum(len(item.people) for item in self.chat_summaries.values()),
            "group_environments": len(self.group_environments),
            "message_visibility": len(self.message_visibility),
            "action_decisions": len(self.action_decisions),
            "life_episodes": len(self.life_episodes),
            "life_episode_people": sum(len(item.related_people) for item in self.life_episodes.values()),
            "life_episode_places": sum(len(item.related_places) for item in self.life_episodes.values()),
            "memory_evidence": len(self.memory_evidence),
            "behavior_feedback": len(self.behavior_feedback),
            "reply_effects": len(self.reply_effects),
            "memory_corrections": len(self.memory_corrections),
            "expression_profiles": len(self.expression_profiles),
            "expression_reviews": len(self.expression_reviews),
            "behavior_patterns": len(self.behavior_patterns),
            "behavior_scenes": len(self.behavior_scenes),
            "session_mid_summaries": len(self.session_mid_summaries),
            "temporary_expression_states": len(self.temporary_expression_states),
            "focus_slots": len(self.focus_slots),
            "expression_intents": len(self.expression_intents),
            "emoji_assets": len(self.emoji_assets),
            "focus_targets": len(self.focus_targets),
            "life_terms": len(self.life_terms),
            "memory_boundaries": len(self.memory_boundaries),
            "memory_maintenance": len(self.memory_maintenance),
            "daily_reviews": len(self.daily_reviews),
            "daily_review_points": review_points,
            "preferences": len(self.preferences),
            "review_preferences": review_prefs,
            "life_events": len(self.life_events),
        }

    async def get_storage_categories(self, policy=None):
        counts = self._table_counts()
        items = []
        for category in STORAGE_CATEGORIES.values():
            tables = [{"name": table, "rows": counts.get(table, 0)} for table in category.tables]
            rows_by_table = {item["name"]: item["rows"] for item in tables}
            groups = [
                {
                    "key": group.key,
                    "label": group.label,
                    "total_rows": sum(rows_by_table.get(table, 0) for table in group.tables),
                    "tables": [
                        {"name": table, "rows": rows_by_table.get(table, 0)}
                        for table in group.tables
                    ],
                }
                for group in category.groups
            ]
            keep_days = self._storage_keep_days(category, policy)
            items.append(
                {
                    "key": category.key,
                    "label": category.label,
                    "description": category.description,
                    "retention_days": keep_days,
                    "auto_cleanup": bool(category.auto_cleanup and keep_days > 0),
                    "total_rows": sum(item["rows"] for item in tables),
                    "tables": tables,
                    "groups": groups,
                }
            )
        return items

    async def get_storage_overview(self, policy=None):
        categories = await self.get_storage_categories(policy)
        return {
            "categories": categories,
            "total_rows": sum(item["total_rows"] for item in categories),
        }

    async def clear_storage_category(self, category_key):
        key = normalize_storage_category(category_key)
        category = STORAGE_CATEGORIES.get(key)
        if not category:
            raise ValueError("存储分类不存在")
        before = (await self.get_storage_categories()).copy()
        before_rows = next((item["total_rows"] for item in before if item["key"] == key), 0)
        if key == "daily":
            self.days.clear()
        elif key == "memory":
            self.relationships.clear()
            self.relationship_contacts.clear()
            self.places.clear()
            self.events.clear()
            self.chat_summaries.clear()
            self.group_environments.clear()
            self.message_visibility.clear()
            self.action_decisions.clear()
            self.life_episodes.clear()
            self.memory_evidence.clear()
            self.behavior_feedback.clear()
            self.reply_effects.clear()
            self.memory_corrections.clear()
            self.expression_profiles.clear()
            self.expression_reviews.clear()
            self.behavior_patterns.clear()
            self.behavior_scenes.clear()
            self.session_mid_summaries.clear()
            self.temporary_expression_states.clear()
            self.focus_slots.clear()
            self.expression_intents.clear()
            self.emoji_assets.clear()
            self.focus_targets.clear()
            self.life_terms.clear()
            self.memory_boundaries.clear()
            self.memory_maintenance.clear()
            self.preferences.clear()
            self.life_events.clear()
        elif key == "planning":
            self.week_plans.clear()
            self.commitments.clear()
            self.day_commitments.clear()
        elif key == "review":
            self.daily_reviews.clear()
        elif key == "workshop":
            self.week_templates.clear()
            self.catalog_items.clear()
            self.hair_styles.clear()
            self.builtin_states.clear()
        return {"category": key, "label": category.label, "deleted_rows": before_rows}

    def _older_than(self, date_str, cutoff):
        return bool(date_str) and str(date_str) < cutoff

    async def cleanup_storage_category(self, category_key, keep_days=None):
        key = normalize_storage_category(category_key)
        category = STORAGE_CATEGORIES.get(key)
        if not category:
            raise ValueError("存储分类不存在")
        retention = self._storage_keep_days(category) if keep_days is None else max(int(keep_days), 0)
        cutoff = (datetime.datetime.now().date() - datetime.timedelta(days=retention)).strftime("%Y-%m-%d")
        deleted = 0
        if retention > 0 and key == "daily":
            stale = [date for date in self.days if self._older_than(date, cutoff)]
            deleted = len(stale)
            for date in stale:
                self.days.pop(date, None)
        elif retention > 0 and key == "review":
            stale = [date for date in self.daily_reviews if self._older_than(date, cutoff)]
            deleted = len(stale)
            for date in stale:
                self.daily_reviews.pop(date, None)
        elif retention > 0 and key == "memory":
            old_events = [item for item in self.events if self._older_than(item.date, cutoff)]
            self.events = [item for item in self.events if not self._older_than(item.date, cutoff)]
            stale_summaries = [key for key, item in self.chat_summaries.items() if self._older_than(item.date, cutoff)]
            for summary_id in stale_summaries:
                self.chat_summaries.pop(summary_id, None)
            stale_episodes = [
                key
                for key, item in self.life_episodes.items()
                if self._older_than(item.date, cutoff)
                and not item.protected
                and item.status in {"done", "closed", "cancelled", "expired", "resolved", "corrected"}
            ]
            for episode_id in stale_episodes:
                self.life_episodes.pop(episode_id, None)
            stale_evidence = [key for key, item in self.memory_evidence.items() if self._older_than(item.date, cutoff)]
            for evidence_id in stale_evidence:
                self.memory_evidence.pop(evidence_id, None)
            stale_feedback = [key for key, item in self.behavior_feedback.items() if self._older_than(item.date, cutoff)]
            for feedback_id in stale_feedback:
                self.behavior_feedback.pop(feedback_id, None)
            stale_reply_effects = [key for key, item in self.reply_effects.items() if self._older_than(item.updated_at, cutoff)]
            for effect_id in stale_reply_effects:
                self.reply_effects.pop(effect_id, None)
            stale_corrections = [
                key
                for key, item in self.memory_corrections.items()
                if item.applied and self._older_than(item.updated_at, cutoff)
            ]
            for correction_id in stale_corrections:
                self.memory_corrections.pop(correction_id, None)
            stale_reviews = [key for key, item in self.expression_reviews.items() if self._older_than(item.created_at, cutoff)]
            for review_id in stale_reviews:
                self.expression_reviews.pop(review_id, None)
            stale_patterns = [key for key, item in self.behavior_patterns.items() if self._older_than(item.last_seen, cutoff)]
            for pattern_id in stale_patterns:
                self.behavior_patterns.pop(pattern_id, None)
            stale_scenes = [key for key, item in self.behavior_scenes.items() if self._older_than(item.last_seen, cutoff)]
            for scene_id in stale_scenes:
                self.behavior_scenes.pop(scene_id, None)
            stale_temp_states = [
                key
                for key, item in self.temporary_expression_states.items()
                if item.expires_at and self._older_than(item.expires_at, cutoff)
            ]
            for state_id in stale_temp_states:
                self.temporary_expression_states.pop(state_id, None)
            stale_slots = [
                key
                for key, item in self.focus_slots.items()
                if item.expires_at and self._older_than(item.expires_at, cutoff)
            ]
            for slot_id in stale_slots:
                self.focus_slots.pop(slot_id, None)
            stale_intents = [key for key, item in self.expression_intents.items() if self._older_than(item.created_at, cutoff)]
            for intent_id in stale_intents:
                self.expression_intents.pop(intent_id, None)
            stale_maintenance = [key for key, item in self.memory_maintenance.items() if self._older_than(item.date, cutoff)]
            for maintenance_id in stale_maintenance:
                self.memory_maintenance.pop(maintenance_id, None)
            deleted = (
                len(old_events)
                + len(stale_summaries)
                + len(stale_episodes)
                + len(stale_evidence)
                + len(stale_feedback)
                + len(stale_reply_effects)
                + len(stale_corrections)
                + len(stale_reviews)
                + len(stale_patterns)
                + len(stale_scenes)
                + len(stale_temp_states)
                + len(stale_slots)
                + len(stale_intents)
                + len(stale_maintenance)
            )
        elif retention > 0 and key == "planning":
            stale_links = [date for date in self.day_commitments if self._older_than(date, cutoff)]
            for date in stale_links:
                deleted += len(self.day_commitments.pop(date, set()))
            stale_commitments = [
                commitment_id
                for commitment_id, item in self.commitments.items()
                if item.status in {"done", "cancelled", "expired"} and self._older_than(item.completed_at or item.trigger_date, cutoff)
            ]
            for commitment_id in stale_commitments:
                self.commitments.pop(commitment_id, None)
            deleted += len(stale_commitments)
        return {
            "category": key,
            "label": category.label,
            "keep_days": retention,
            "deleted_rows": deleted,
        }

    async def cleanup_by_storage_policy(self, policy=None):
        results = []
        for category in STORAGE_CATEGORIES.values():
            keep_days = self._storage_keep_days(category, policy)
            if keep_days > 0 and category.auto_cleanup:
                results.append(await self.cleanup_storage_category(category.key, keep_days))
        return {
            "results": results,
            "deleted_rows": sum(item["deleted_rows"] for item in results),
        }


class WeatherClient:
    async def get_weather(self, city):
        return "北京 晴 20°C"


class Event:
    def __init__(
        self,
        bot=None,
        sender_name="平台名",
        sender_id="123456",
        platform_name="aiocqhttp",
        unified_msg_origin="aiocqhttp:FriendMessage:123456",
        group_id="",
        group_name="",
        message_id="",
        self_id="",
    ):
        self.bot = bot
        self._sender_name = sender_name
        self._sender_id = sender_id
        self._self_id = self_id
        self._platform_name = platform_name
        self._group_id = group_id
        self._group_name = group_name
        self.message_id = message_id
        self.unified_msg_origin = unified_msg_origin
        self.message_str = ""
        self.message_items = []
        self.message_obj = types.SimpleNamespace(message=self.message_items)
        self.sent_messages = []
        self.stopped = False
        self.call_llm = False
        self.is_at_or_wake_command = False
        self._extras = {}
        self._result = None

    def get_sender_id(self):
        return self._sender_id

    def get_self_id(self):
        return self._self_id

    def get_sender_name(self):
        return self._sender_name

    def get_platform_name(self):
        return self._platform_name

    def get_group_id(self):
        return self._group_id

    def get_group_name(self):
        return self._group_name

    def get_message_id(self):
        return self.message_id

    def get_messages(self):
        return list(self.message_items)

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return types.SimpleNamespace(chain=list(chain or []), result_content_type="LLM_RESULT")

    def set_result(self, result):
        self._result = result

    def get_result(self):
        return self._result

    def clear_result(self):
        self._result = None

    async def send(self, message):
        self.sent_messages.append(message)

    def stop_event(self):
        self.stopped = True

    def is_stopped(self):
        return self.stopped

    def should_call_llm(self, call_llm):
        self.call_llm = bool(call_llm)

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_extra(self, key=None, default=None):
        if key is None:
            return self._extras
        return self._extras.get(key, default)


class OneBot:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        return self.response


class ActionBot:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        response = self.responses.get(action, {})
        if callable(response):
            return response(action, **params)
        return response


class DirectActionBot(OneBot):
    pass


class PlatformInstance:
    def __init__(self, bot, platform_id="aiocqhttp", platform_type="aiocqhttp"):
        self.bot = bot
        self.config = {"id": platform_id, "type": platform_type}

    def get_client(self):
        return self.bot

    def meta(self):
        return types.SimpleNamespace(id=self.config["id"], name=self.config["type"])


class PlatformManager:
    def __init__(self, bot=None, instances=None):
        self._instances = instances or [PlatformInstance(bot)]

    def get_insts(self):
        return self._instances


class PlatformHistoryManager:
    def __init__(self, records=None):
        self.records = records or {}
        self.calls = []
        self.inserts = []

    async def get(self, platform_id, user_id, page=1, page_size=200):
        self.calls.append((platform_id, user_id, page, page_size))
        return list(self.records.get((platform_id, user_id), []))

    async def insert(
        self,
        platform_id,
        user_id,
        content,
        sender_id=None,
        sender_name=None,
        llm_checkpoint_id=None,
    ):
        record = types.SimpleNamespace(
            platform_id=platform_id,
            user_id=user_id,
            content=content,
            sender_id=sender_id,
            sender_name=sender_name,
            llm_checkpoint_id=llm_checkpoint_id,
        )
        self.inserts.append(record)
        self.records.setdefault((platform_id, user_id), []).append(record)
        return record


class PersonaManager:
    def __init__(self, user_name="", prompt="", scoped_prompts=None):
        self.persona = {"user_name": user_name, "prompt": prompt}
        self.scoped_prompts = dict(scoped_prompts or {})
        self.calls = []
        self.selected_default_persona_v3 = self.persona

    async def get_default_persona_v3(self, umo=""):
        self.calls.append(str(umo or ""))
        prompt = self.scoped_prompts.get(str(umo or "").strip())
        if prompt is not None:
            return {"user_name": self.persona.get("user_name", ""), "prompt": prompt}
        return self.persona


def make_config(provider_id="", extra_config=None):
    data = {
        "rhythm_config": {
            "llm_provider": provider_id,
            "history_days": 0,
            "reference_groups": [],
            "reference_users": [],
        },
    }
    for key, value in dict(extra_config or {}).items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    return LifeSettings.from_dict(data)


def make_composer(
    responses=(),
    provider_id="",
    selected_responses=(),
    persona_manager=None,
    providers=None,
    context_config=None,
    config_overrides=None,
):
    provider = Provider(responses)
    selected = AltProvider(selected_responses) if provider_id else None
    context = Context(provider, selected, persona_manager=persona_manager, providers=providers, config=context_config)
    archive = DataManager()
    composer = LifeBackgroundComposer(context, make_config(provider_id, config_overrides), archive, WeatherClient())
    return composer, provider, selected, archive


async def async_return(value):
    return value
