from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


ALIASES = {
    "paraformer-zh": "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "fsmn-vad": "speech_fsmn_vad_zh-cn-16k-common-pytorch",
}
SEGMENT_TARGET_MS = 25_000
SEGMENT_MIN_TOKENS = 24


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用 FunASR 转写音频。")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--vad-model", required=True)
    parser.add_argument("--models-dir", default="")
    parser.add_argument("--batch-size-s", type=int, default=300)
    return parser.parse_args()


def configure_cache(models_dir: str) -> None:
    if not models_dir:
        return
    root = Path(models_dir).resolve()
    os.environ.setdefault("MODELSCOPE_CACHE", str(root / "cache"))
    os.environ.setdefault("HF_HOME", str(root / "huggingface"))
    os.environ.setdefault("HF_HUB_CACHE", str(root / "huggingface" / "hub"))


def resolve_model(name: str, models_dir: str) -> str:
    model_ref = str(name or "").strip()
    if not model_ref:
        return model_ref
    direct = Path(model_ref)
    if direct.exists():
        return str(direct.resolve())
    if models_dir:
        root = Path(models_dir).resolve()
        candidates = [root / model_ref]
        alias_name = ALIASES.get(model_ref)
        if alias_name:
            candidates.append(root / alias_name)
        if "/" in model_ref:
            namespace, model_name = model_ref.split("/", 1)
            candidates.extend(
                [
                    root / model_name,
                    root / "models" / namespace / model_name,
                    root / "cache" / "models" / namespace / model_name,
                    root / model_ref.rsplit("/", 1)[-1],
                ]
            )
        for candidate in candidates:
            if candidate.exists():
                return str(candidate.resolve())
    return model_ref


def transcript_payload(result: Any) -> dict[str, Any]:
    plain_text = plain_funasr_text(result)
    segments = timestamped_segments(result)
    text = "\n".join(f"[{item['start']}-{item['end']}] {item['text']}" for item in segments).strip()
    return {"text": text or plain_text, "plain_text": plain_text, "segments": segments}


def plain_funasr_text(result: Any) -> str:
    items = result if isinstance(result, list) else [result]
    parts: list[str] = []
    for item in items:
        text = str(item.get("text", "") if isinstance(item, dict) else item or "").strip()
        if text:
            parts.append(text)
    return normalize_spacing("\n".join(parts))


def timestamped_segments(result: Any) -> list[dict[str, Any]]:
    items = result if isinstance(result, list) else [result]
    segments: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "") or "").strip()
        timestamps = [_timestamp_pair(value) for value in list(item.get("timestamp") or [])]
        timestamps = [value for value in timestamps if value is not None]
        tokens = _tokens_for_timestamps(text, len(timestamps))
        if tokens and len(tokens) == len(timestamps):
            segments.extend(_chunk_tokens(tokens, timestamps))
    return segments


def _tokens_for_timestamps(text: str, expected_count: int) -> list[str]:
    if expected_count <= 0:
        return []
    by_space = [token for token in re.split(r"\s+", text.strip()) if token]
    if len(by_space) == expected_count:
        return by_space
    chars = [char for char in re.sub(r"\s+", "", text) if char]
    return chars if len(chars) == expected_count else []


def _timestamp_pair(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        start = max(0, int(float(value[0])))
        end = max(start, int(float(value[1])))
    except (TypeError, ValueError):
        return None
    return start, end


def _chunk_tokens(tokens: list[str], timestamps: list[tuple[int, int]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current: list[str] = []
    start_ms = 0
    end_ms = 0
    for token, timestamp in zip(tokens, timestamps):
        if not current:
            start_ms = timestamp[0]
        current.append(token)
        end_ms = timestamp[1]
        if end_ms - start_ms >= SEGMENT_TARGET_MS and len(current) >= SEGMENT_MIN_TOKENS:
            segments.append(_segment(start_ms, end_ms, current))
            current = []
    if current:
        segments.append(_segment(start_ms, end_ms, current))
    return segments


def _segment(start_ms: int, end_ms: int, tokens: list[str]) -> dict[str, Any]:
    return {
        "start": _format_time(start_ms),
        "end": _format_time(end_ms),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "text": _join_tokens(tokens),
    }


def _join_tokens(tokens: list[str]) -> str:
    if any(re.search(r"[A-Za-z0-9]", token) for token in tokens):
        return normalize_spacing(" ".join(tokens))
    return normalize_spacing("".join(tokens))


def _format_time(milliseconds: int) -> str:
    total = max(0, int(round(milliseconds / 1000)))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def normalize_spacing(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or ""))
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"\s+([,.;:!?，。！？；：、])", r"\1", text)
    text = re.sub(r"([(\[（【])\s+", r"\1", text)
    text = re.sub(r"\s+([)\]）】])", r"\1", text)
    return text.strip()


def transcribe(args: argparse.Namespace) -> dict[str, Any]:
    configure_cache(args.models_dir)
    try:
        from funasr import AutoModel
    except ImportError as exc:
        raise RuntimeError("当前 Python 环境未安装 funasr，无法执行本地语音转写") from exc

    model = AutoModel(
        model=resolve_model(args.model, args.models_dir),
        vad_model=resolve_model(args.vad_model, args.models_dir),
        device="cpu",
        disable_update=True,
    )
    result = model.generate(input=str(Path(args.input).resolve()), batch_size_s=max(1, int(args.batch_size_s or 300)))
    payload = transcript_payload(result)
    if not payload.get("plain_text") and not payload.get("segments"):
        raise RuntimeError("FunASR 返回了空转写结果")
    return payload


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(transcribe(args), ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise
