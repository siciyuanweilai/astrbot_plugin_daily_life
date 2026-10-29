from __future__ import annotations

from ..clock import now as life_now

from .mirror import tempo as _part_snapshot_tempo_component
from .mirror import export as _part_snapshot_export_component
from .mirror import pack as _part_snapshot_pack_component
from . import veil as _part_inject_veil_component
from .shade import render as _part_hidden_experience_component
from .layer import chain as _part_layer_chain_component
from typing import Protocol


class InjectComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    pass


class InjectComponent:
    def __init__(self, context: InjectComponentContext) -> None:
        self.context = context

    def _life_injection_now(self):
        return life_now()

    @staticmethod
    def is_internal_llm_session(req: _part_snapshot_tempo_component.ProviderRequest) -> bool:
        return _part_snapshot_tempo_component.SnapshotTempoComponent.is_internal_llm_session(req)

    @staticmethod
    def _share_current_facts(data: _part_snapshot_export_component.Any) -> dict[str, _part_snapshot_export_component.Any]:
        return _part_snapshot_export_component.SnapshotExportComponent._share_current_facts(data)

    @staticmethod
    def _life_context_meta_line(meta: dict[str, _part_snapshot_export_component.Any]) -> str:
        return _part_snapshot_export_component.SnapshotExportComponent._life_context_meta_line(meta)

    @staticmethod
    def _life_context_weather(data: _part_snapshot_export_component.Any) -> str:
        return _part_snapshot_export_component.SnapshotExportComponent._life_context_weather(data)

    @staticmethod
    def _life_context_subject(state_dict: dict[str, _part_snapshot_export_component.Any], interrupt: dict[str, _part_snapshot_export_component.Any]) -> dict[str, _part_snapshot_export_component.Any]:
        return _part_snapshot_export_component.SnapshotExportComponent._life_context_subject(state_dict, interrupt)

    @staticmethod
    def _life_context_people_keys(relationships: list[_part_snapshot_export_component.Any]) -> set[str]:
        return _part_snapshot_export_component.SnapshotExportComponent._life_context_people_keys(relationships)

    @classmethod
    def _life_context_filter_people_records(cls, records: list[_part_snapshot_export_component.Any], people_keys: set[str], *, keep_without_people: bool) -> list[_part_snapshot_export_component.Any]:
        return _part_snapshot_export_component.SnapshotExportComponent._life_context_filter_people_records.__func__(cls, records, people_keys, keep_without_people=keep_without_people)

    @staticmethod
    def _share_record_value(item: _part_snapshot_export_component.Any, key: str, default: _part_snapshot_export_component.Any='') -> _part_snapshot_export_component.Any:
        return _part_snapshot_export_component.SnapshotExportComponent._share_record_value(item, key, default)

    @classmethod
    def _share_text(cls, item: _part_snapshot_export_component.Any, key: str, limit: int=160) -> str:
        return _part_snapshot_export_component.SnapshotExportComponent._share_text.__func__(cls, item, key, limit)

    @classmethod
    def _share_exact_scope(cls, records: list[_part_snapshot_export_component.Any], scope: str) -> list[_part_snapshot_export_component.Any]:
        return _part_snapshot_export_component.SnapshotExportComponent._share_exact_scope.__func__(cls, records, scope)

    @classmethod
    def _share_scope_context(cls, target_umo: str) -> tuple[str, str, bool, bool]:
        return _part_snapshot_export_component.SnapshotExportComponent._share_scope_context.__func__(cls, target_umo)

    @classmethod
    def _share_episode_payload(cls, episodes: list[_part_snapshot_export_component.Any], people_keys: set[str], *, is_private: bool) -> list[dict[str, str]]:
        return _part_snapshot_export_component.SnapshotExportComponent._share_episode_payload.__func__(cls, episodes, people_keys, is_private=is_private)

    @classmethod
    def _share_expression_payload(cls, profiles: list[_part_snapshot_export_component.Any]) -> dict[str, list[str]]:
        return _part_snapshot_export_component.SnapshotExportComponent._share_expression_payload.__func__(cls, profiles)

    @classmethod
    def _share_behavior_payload(cls, scenes: list[_part_snapshot_export_component.Any]) -> list[dict[str, str]]:
        return _part_snapshot_export_component.SnapshotExportComponent._share_behavior_payload.__func__(cls, scenes)

    @classmethod
    def _share_terms_payload(cls, terms: list[_part_snapshot_export_component.Any]) -> list[dict[str, str]]:
        return _part_snapshot_export_component.SnapshotExportComponent._share_terms_payload.__func__(cls, terms)

    @classmethod
    def _share_interaction_payload(cls, effects: list[_part_snapshot_export_component.Any], feedback: list[_part_snapshot_export_component.Any]) -> dict[str, _part_snapshot_export_component.Any]:
        return _part_snapshot_export_component.SnapshotExportComponent._share_interaction_payload.__func__(cls, effects, feedback)

    _INJECTION_SNAPSHOT_CACHE_TTL_SECONDS = _part_snapshot_pack_component.SnapshotPackComponent._INJECTION_SNAPSHOT_CACHE_TTL_SECONDS

    _INJECTION_SNAPSHOT_CACHE_LIMIT = _part_snapshot_pack_component.SnapshotPackComponent._INJECTION_SNAPSHOT_CACHE_LIMIT

    @classmethod
    def _cached_injection_snapshot(cls, cache: dict[str, _part_snapshot_pack_component.Any], cache_key: str) -> dict[str, _part_snapshot_pack_component.Any] | None:
        return _part_snapshot_pack_component.SnapshotPackComponent._cached_injection_snapshot.__func__(cls, cache, cache_key)

    @staticmethod
    def _hidden_text(value: _part_inject_veil_component.Any, limit: int=120) -> str:
        return _part_inject_veil_component.InjectVeilComponent._hidden_text(value, limit)

    @staticmethod
    def _hidden_lines(items: list[_part_hidden_experience_component.Any] | None, count: int, render: _part_hidden_experience_component.Callable[[_part_hidden_experience_component.Any], str]) -> list[str]:
        return _part_hidden_experience_component.HiddenExperienceComponent._hidden_lines(items, count, render)

    @staticmethod
    def _hidden_section(sections: list[str], title: str, lines: list[str], intro: str='') -> None:
        return _part_hidden_experience_component.HiddenExperienceComponent._hidden_section(sections, title, lines, intro)

    _CHAT_MEMORY_RANK_BUDGET_SECONDS = _part_layer_chain_component.LayerChainComponent._CHAT_MEMORY_RANK_BUDGET_SECONDS

    _EXPERIENCE_RELEVANCE_FIELDS = _part_layer_chain_component.LayerChainComponent._EXPERIENCE_RELEVANCE_FIELDS

    @staticmethod
    def _semantic_snapshot_id(kind: str, item: _part_layer_chain_component.Any, index: int) -> str:
        return _part_layer_chain_component.LayerChainComponent._semantic_snapshot_id(kind, item, index)

    @staticmethod
    def _format_cognition_context(snapshot: dict[str, _part_layer_chain_component.Any]) -> str:
        return _part_layer_chain_component.LayerChainComponent._format_cognition_context(snapshot)

