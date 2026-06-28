"""Build hook-first, non-linear reel plans from ranked highlights."""
from __future__ import annotations

from typing import Dict, Iterable, List


MIN_PART_SECONDS = 0.65
MAX_HOOK_SECONDS = 9.0


def _part(start: float, end: float, role: str) -> Dict:
    return {
        "start_time": round(float(start), 3),
        "end_time": round(float(end), 3),
        "role": role,
    }


def build_edit_parts(highlight: Dict, nonlinear: bool = True) -> List[Dict]:
    start = float(highlight["start_time"])
    end = float(highlight["end_time"])
    hook_start = float(highlight.get("hook_start_time", start))
    hook_end = float(highlight.get("hook_end_time", min(end, hook_start + 5.0)))

    hook_start = max(start, min(hook_start, end))
    hook_end = max(hook_start, min(hook_end, end, hook_start + MAX_HOOK_SECONDS))

    if (
        not nonlinear
        or hook_start - start < 2.0
        or hook_end - hook_start < MIN_PART_SECONDS
    ):
        return [_part(start, end, "linear")]

    parts = [_part(hook_start, hook_end, "cold_open")]
    if hook_start - start >= MIN_PART_SECONDS:
        parts.append(_part(start, hook_start, "context"))
    if end - hook_end >= MIN_PART_SECONDS:
        parts.append(_part(hook_end, end, "payoff"))
    return parts


def _caption_segments(parts: Iterable[Dict], transcript: Dict) -> List[Dict]:
    source_segments = transcript.get("segments") or []
    captions: List[Dict] = []
    output_offset = 0.0

    for part in parts:
        part_start = float(part["start_time"])
        part_end = float(part["end_time"])
        for segment in source_segments:
            segment_start = float(segment.get("start", 0.0))
            segment_end = float(segment.get("end", segment_start + 1.0))
            overlap_start = max(part_start, segment_start)
            overlap_end = min(part_end, segment_end)
            text = " ".join(str(segment.get("text") or "").split())
            if overlap_end <= overlap_start or not text:
                continue
            captions.append(
                {
                    "start": round(output_offset + overlap_start - part_start, 3),
                    "end": round(output_offset + overlap_end - part_start, 3),
                    "text": text,
                }
            )
        output_offset += part_end - part_start
    return captions


def prepare_reel(highlight: Dict, transcript: Dict, nonlinear: bool = True) -> Dict:
    planned = dict(highlight)
    parts = build_edit_parts(planned, nonlinear=nonlinear)
    planned["edit_parts"] = parts
    planned["edit_mode"] = "hook_first" if parts[0]["role"] == "cold_open" else "linear"
    planned["_caption_segments"] = _caption_segments(parts, transcript)
    planned["duration"] = round(
        sum(float(part["end_time"]) - float(part["start_time"]) for part in parts),
        3,
    )
    return planned
