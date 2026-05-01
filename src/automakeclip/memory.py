from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .types import Segment


MEMORY_VERSION = 1
MIN_LEARNED_DURATION_SECONDS = 0.35
MAX_TRIM_DELTA_SECONDS = 12.0
FEEDBACK_TRIM_SECONDS = 0.8


def review_memory_path() -> Path:
    return Path.cwd() / "review_sessions" / "review_memory.json"


def empty_review_memory() -> Dict[str, object]:
    return {
        "version": MEMORY_VERSION,
        "updated_at": "",
        "clips": {},
    }


def load_review_memory(path: Optional[Path] = None) -> Dict[str, object]:
    memory_path = path or review_memory_path()
    try:
        payload = json.loads(memory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_review_memory()
    if not isinstance(payload, dict):
        return empty_review_memory()
    clips = payload.get("clips")
    if not isinstance(clips, dict):
        clips = {}
    return {
        "version": int(payload.get("version") or MEMORY_VERSION),
        "updated_at": str(payload.get("updated_at") or ""),
        "clips": clips,
    }


def save_review_memory(memory: Dict[str, object], path: Optional[Path] = None) -> None:
    memory_path = path or review_memory_path()
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory["version"] = MEMORY_VERSION
    memory["updated_at"] = datetime.now().isoformat()
    temp_path = memory_path.with_suffix(f"{memory_path.suffix}.tmp")
    temp_path.write_text(json.dumps(memory, indent=2), encoding="utf-8")
    temp_path.replace(memory_path)


def persist_review_clip(
    clip: object,
    decision: str = "",
    feedback: str = "",
    edit: Optional[Dict[str, float]] = None,
    session_dir: Optional[Path] = None,
    path: Optional[Path] = None,
) -> Dict[str, object]:
    memory = load_review_memory(path)
    record_review_clip(memory, clip, decision=decision, feedback=feedback, edit=edit, session_dir=session_dir)
    save_review_memory(memory, path)
    return memory


def flush_review_session_memory(
    clips: Iterable[object],
    decisions: Dict[str, str],
    clip_feedback: Dict[str, str],
    clip_edits: Dict[str, Dict[str, float]],
    session_feedback: str = "",
    session_dir: Optional[Path] = None,
    path: Optional[Path] = None,
) -> Dict[str, object]:
    memory = load_review_memory(path)
    for clip in clips:
        clip_id = str(_clip_attr(clip, "clip_id", ""))
        record_review_clip(
            memory,
            clip,
            decision=str(decisions.get(clip_id, "")),
            feedback=str(clip_feedback.get(clip_id, "")),
            edit=clip_edits.get(clip_id),
            session_feedback=session_feedback,
            session_dir=session_dir,
        )
    save_review_memory(memory, path)
    return memory


def record_review_clip(
    memory: Dict[str, object],
    clip: object,
    decision: str = "",
    feedback: str = "",
    edit: Optional[Dict[str, float]] = None,
    session_feedback: str = "",
    session_dir: Optional[Path] = None,
) -> Dict[str, object]:
    clips = memory.setdefault("clips", {})
    if not isinstance(clips, dict):
        clips = {}
        memory["clips"] = clips

    source_path = str(_clip_attr(clip, "source_path", ""))
    start = _float_attr(clip, "start")
    end = _float_attr(clip, "end")
    source_duration = _float_attr(clip, "source_duration")
    key = _memory_key(source_path, start, end)
    normalized_edit = _normalize_edit(edit, source_duration)
    entry = {
        "key": key,
        "clip_id": str(_clip_attr(clip, "clip_id", "")),
        "session_dir": str(session_dir) if session_dir else "",
        "source_path": source_path,
        "source_key": _source_key(source_path),
        "source_name": Path(source_path).name if source_path else "",
        "start": round(start, 3),
        "end": round(end, 3),
        "score": round(_float_attr(clip, "score"), 6),
        "label": str(_clip_attr(clip, "label", "")),
        "note": str(_clip_attr(clip, "note", "")),
        "bucket": clip_feature_bucket(clip),
        "highlight_time": _optional_float_attr(clip, "highlight_time"),
        "source_duration": round(source_duration, 3),
        "decision": decision if decision in {"yes", "no", "skip"} else "",
        "feedback": feedback.strip(),
        "session_feedback": session_feedback.strip(),
        "feedback_directives": feedback_directives(feedback, session_feedback),
        "edit": normalized_edit,
        "updated_at": datetime.now().isoformat(),
    }
    clips[key] = entry
    memory["updated_at"] = entry["updated_at"]
    return entry


def apply_memory_to_candidates(
    candidates: List[Segment],
    source_durations: Optional[Dict[str, float]] = None,
    memory: Optional[Dict[str, object]] = None,
    memory_path: Optional[Path] = None,
) -> List[Segment]:
    if not candidates:
        return []
    payload = memory if memory is not None else load_review_memory(memory_path)
    entries = list(_memory_entries(payload))
    if not entries:
        return list(candidates)

    bucket_stats = _build_bucket_stats(entries)
    global_feedback = _build_global_feedback_stats(entries)
    direct_entries = [entry for entry in entries if entry.get("decision") in {"yes", "no", "skip"}]
    adjusted: List[Segment] = []
    for candidate in candidates:
        bucket = segment_feature_bucket(candidate)
        stats = bucket_stats.get(bucket)
        score = float(candidate.score)
        start = float(candidate.start)
        end = float(candidate.end)
        highlight_time = candidate.highlight_time
        notes: List[str] = []

        if stats:
            score_delta = _bucket_score_delta(stats)
            if abs(score_delta) >= 0.01:
                score += max(abs(score), 1.0) * score_delta
                if score_delta > 0:
                    notes.append("Memory-boosted from prior accepted clips.")
                else:
                    notes.append("Memory-downranked from prior rejected clips.")
            trim = _learned_trim_delta(stats)
            if trim is not None:
                trimmed = _apply_trim_delta(
                    start,
                    end,
                    highlight_time,
                    trim["start_delta"],
                    trim["end_delta"],
                    _source_duration_for(candidate.source_path, source_durations),
                )
                if trimmed is not None:
                    start, end, highlight_time = trimmed
                    notes.append("Memory-trimmed from prior review edits.")

            feedback_trim = _learned_feedback_trim_delta(stats)
            if feedback_trim is not None:
                trimmed = _apply_trim_delta(
                    start,
                    end,
                    highlight_time,
                    feedback_trim["start_delta"],
                    feedback_trim["end_delta"],
                    _source_duration_for(candidate.source_path, source_durations),
                )
                if trimmed is not None:
                    start, end, highlight_time = trimmed
                    notes.append("Memory-adjusted from prior clip notes.")

        direct_delta = _direct_score_delta(candidate, direct_entries)
        if abs(direct_delta) >= 0.01:
            score += max(abs(score), 1.0) * direct_delta
            if direct_delta > 0:
                notes.append("Memory-boosted from a matching approved clip.")
            else:
                notes.append("Memory-downranked from a matching rejected clip.")

        feedback_delta = _feedback_score_delta(candidate, stats, global_feedback)
        if abs(feedback_delta) >= 0.01:
            score += max(abs(score), 1.0) * feedback_delta
            if feedback_delta > 0:
                notes.append("Memory-boosted from prior clip notes.")
            else:
                notes.append("Memory-downranked from prior clip notes.")

        adjusted.append(
            Segment(
                start=round(start, 3),
                end=round(end, 3),
                score=round(score, 6),
                label=candidate.label,
                note=_append_memory_notes(candidate.note, notes),
                source_path=candidate.source_path,
                highlight_time=round(highlight_time, 3) if highlight_time is not None else None,
            )
        )
    return adjusted


def segment_feature_bucket(segment: Segment) -> str:
    return _feature_bucket(segment.label, segment.note)


def clip_feature_bucket(clip: object) -> str:
    return _feature_bucket(str(_clip_attr(clip, "label", "")), str(_clip_attr(clip, "note", "")))


def _feature_bucket(label: str, note: str) -> str:
    normalized_label = (label or "unknown").strip().lower().replace(" ", "_")
    note_lower = (note or "").lower()
    if normalized_label == "silly" or "silly" in note_lower or "comedy" in note_lower:
        note_bucket = "silly"
    elif "steelseries multi-kill sequence" in note_lower:
        note_bucket = "steelseries_multi_kill"
    elif "steelseries kill event window" in note_lower:
        note_bucket = "steelseries_kill_event"
    elif "generic kill-heavy peak window" in note_lower or "kill-feed heavy fight window" in note_lower:
        note_bucket = "kill_feed_fight"
    elif "fallback" in note_lower or "generic high-activity fight window" in note_lower or "active team-fight" in note_lower:
        note_bucket = "fallback_fight"
    else:
        note_bucket = "general"
    return f"{normalized_label}:{note_bucket}"


def _build_bucket_stats(entries: List[Dict[str, object]]) -> Dict[str, Dict[str, float]]:
    stats_by_bucket: Dict[str, Dict[str, float]] = {}
    for entry in entries:
        bucket = str(entry.get("bucket") or _feature_bucket(str(entry.get("label") or ""), str(entry.get("note") or "")))
        stats = stats_by_bucket.setdefault(
            bucket,
            {
                "yes": 0.0,
                "no": 0.0,
                "skip": 0.0,
                "total": 0.0,
                "trim_weight": 0.0,
                "trim_start_delta": 0.0,
                "trim_end_delta": 0.0,
                "feedback_weight": 0.0,
                "feedback_score": 0.0,
                "feedback_trim_weight": 0.0,
                "feedback_trim_start_delta": 0.0,
                "feedback_trim_end_delta": 0.0,
            },
        )
        decision = str(entry.get("decision") or "")
        if decision in {"yes", "no", "skip"}:
            stats[decision] += 1.0
            stats["total"] += 1.0

        directives = _coerce_directives(entry.get("feedback_directives"))
        if directives:
            feedback_weight = _feedback_weight(decision)
            score_delta = _entry_feedback_score_delta(directives, decision)
            if abs(score_delta) >= 0.01:
                stats["feedback_weight"] += feedback_weight
                stats["feedback_score"] += score_delta * feedback_weight
            trim_delta = _feedback_trim_delta(directives)
            if trim_delta is not None:
                stats["feedback_trim_weight"] += feedback_weight
                stats["feedback_trim_start_delta"] += trim_delta["start_delta"] * feedback_weight
                stats["feedback_trim_end_delta"] += trim_delta["end_delta"] * feedback_weight

        edit = entry.get("edit")
        if not isinstance(edit, dict):
            continue
        if decision == "no":
            continue
        try:
            original_start = float(entry.get("start"))
            original_end = float(entry.get("end"))
            edit_start = float(edit.get("start"))
            edit_end = float(edit.get("end"))
        except (TypeError, ValueError):
            continue
        start_delta = edit_start - original_start
        end_delta = edit_end - original_end
        if abs(start_delta) > MAX_TRIM_DELTA_SECONDS or abs(end_delta) > MAX_TRIM_DELTA_SECONDS:
            continue
        weight = 1.0 if decision == "yes" else 0.5
        stats["trim_weight"] += weight
        stats["trim_start_delta"] += start_delta * weight
        stats["trim_end_delta"] += end_delta * weight
    return stats_by_bucket


def _build_global_feedback_stats(entries: List[Dict[str, object]]) -> Dict[str, float]:
    stats = {
        "weight": 0.0,
        "prefer_multi_kill": 0.0,
        "prefer_kill_event": 0.0,
        "prefer_action": 0.0,
        "avoid_silly": 0.0,
        "avoid_fallback": 0.0,
        "avoid_generic": 0.0,
        "avoid_dead_air": 0.0,
        "avoid_training": 0.0,
    }
    for entry in entries:
        directives = _coerce_directives(entry.get("feedback_directives"))
        if not directives:
            continue
        weight = _feedback_weight(str(entry.get("decision") or ""))
        stats["weight"] += weight
        for key in stats:
            if key == "weight":
                continue
            if directives.get(key):
                stats[key] += weight
    return stats


def _bucket_score_delta(stats: Dict[str, float]) -> float:
    preference = stats["yes"] - (stats["no"] * 1.25) - (stats["skip"] * 0.15)
    confidence = min(1.0, max(stats["total"], 1.0) / 4.0)
    return _clamp(preference * confidence * 0.14, -0.65, 0.85)


def _learned_trim_delta(stats: Dict[str, float]) -> Optional[Dict[str, float]]:
    trim_weight = stats.get("trim_weight", 0.0)
    if trim_weight <= 0.0:
        return None
    start_delta = stats["trim_start_delta"] / trim_weight
    end_delta = stats["trim_end_delta"] / trim_weight
    if abs(start_delta) < 0.05 and abs(end_delta) < 0.05:
        return None
    return {
        "start_delta": _clamp(start_delta, -MAX_TRIM_DELTA_SECONDS, MAX_TRIM_DELTA_SECONDS),
        "end_delta": _clamp(end_delta, -MAX_TRIM_DELTA_SECONDS, MAX_TRIM_DELTA_SECONDS),
    }


def _learned_feedback_trim_delta(stats: Dict[str, float]) -> Optional[Dict[str, float]]:
    trim_weight = stats.get("feedback_trim_weight", 0.0)
    if trim_weight <= 0.0:
        return None
    start_delta = stats["feedback_trim_start_delta"] / trim_weight
    end_delta = stats["feedback_trim_end_delta"] / trim_weight
    if abs(start_delta) < 0.05 and abs(end_delta) < 0.05:
        return None
    return {
        "start_delta": _clamp(start_delta, -MAX_TRIM_DELTA_SECONDS, MAX_TRIM_DELTA_SECONDS),
        "end_delta": _clamp(end_delta, -MAX_TRIM_DELTA_SECONDS, MAX_TRIM_DELTA_SECONDS),
    }


def _feedback_score_delta(
    segment: Segment,
    bucket_stats: Optional[Dict[str, float]],
    global_stats: Dict[str, float],
) -> float:
    delta = 0.0
    if bucket_stats and bucket_stats.get("feedback_weight", 0.0) > 0.0:
        delta += bucket_stats["feedback_score"] / bucket_stats["feedback_weight"]

    weight = max(global_stats.get("weight", 0.0), 1.0)
    note = (segment.note or "").lower()
    label = (segment.label or "").lower()
    is_multi = "steelseries multi-kill sequence" in note
    is_event = "steelseries kill event window" in note or is_multi
    is_generic = "generic" in note or "fallback" in note or "kill-feed heavy" in note
    is_silly = label == "silly" or "silly" in note or "comedy" in note

    delta += 0.30 * (global_stats.get("prefer_multi_kill", 0.0) / weight) if is_multi else 0.0
    delta += 0.18 * (global_stats.get("prefer_kill_event", 0.0) / weight) if is_event else 0.0
    delta += 0.12 * (global_stats.get("prefer_action", 0.0) / weight) if label in {"highlight", "fight"} else 0.0
    delta -= 0.70 * (global_stats.get("avoid_silly", 0.0) / weight) if is_silly else 0.0
    delta -= 0.45 * (global_stats.get("avoid_fallback", 0.0) / weight) if "fallback" in note else 0.0
    delta -= 0.35 * (global_stats.get("avoid_generic", 0.0) / weight) if is_generic else 0.0
    delta -= 0.30 * (global_stats.get("avoid_dead_air", 0.0) / weight) if is_generic or is_silly else 0.0
    delta -= 0.75 * (global_stats.get("avoid_training", 0.0) / weight) if "training" in note or "practice" in note else 0.0
    return _clamp(delta, -0.80, 0.80)


def feedback_directives(*feedback_values: str) -> Dict[str, bool]:
    text = " ".join(value for value in feedback_values if value).lower()
    return {
        "more_like_this": _has_any(text, ["more like this", "keep like this", "good clip", "good one", "this is good", "use this"]),
        "less_like_this": _has_any(text, ["less like this", "not like this", "bad clip", "this is bad", "don't use", "dont use"]),
        "shorter": _has_any(text, ["shorter", "too long", "less lead", "less lead-in", "less setup", "dragging"]),
        "start_later": _has_any(text, ["start later", "cut later", "too much before", "less before", "skip intro", "skip the start"]),
        "end_sooner": _has_any(text, ["end sooner", "cut earlier", "too much after", "less after", "ends late"]),
        "more_lead_in": _has_any(text, ["more before", "more lead", "more lead-in", "start earlier", "needs setup"]),
        "more_after": _has_any(text, ["more after", "more aftermath", "end later", "let it breathe", "show final"]),
        "prefer_multi_kill": _has_any(text, ["more multi", "multikill", "multi-kill", "double kill", "triple", "quad", "penta"]),
        "prefer_kill_event": _has_any(text, ["more kills", "kill clips", "elims", "eliminations", "final blow"]),
        "prefer_action": _has_any(text, ["more action", "more fights", "more hype", "faster", "aggro"]),
        "avoid_silly": _has_any(text, ["no silly", "less silly", "no comedy", "less comedy", "no filler"]),
        "avoid_fallback": _has_any(text, ["no fallback", "less fallback", "avoid fallback"]),
        "avoid_generic": _has_any(text, ["no generic", "less generic", "avoid generic", "generic sucks"]),
        "avoid_dead_air": _has_any(text, ["dead air", "boring", "slow", "nothing happens", "too quiet"]),
        "avoid_training": _has_any(text, ["no training", "no practice", "practice range", "training range"]),
    }


def _coerce_directives(value: object) -> Dict[str, bool]:
    return value if isinstance(value, dict) else {}


def _entry_feedback_score_delta(directives: Dict[str, bool], decision: str) -> float:
    delta = 0.0
    if directives.get("more_like_this"):
        delta += 0.22
    if directives.get("less_like_this") or directives.get("avoid_dead_air"):
        delta -= 0.28
    if directives.get("prefer_multi_kill") or directives.get("prefer_kill_event") or directives.get("prefer_action"):
        delta += 0.10
    if directives.get("avoid_silly") or directives.get("avoid_fallback") or directives.get("avoid_generic"):
        delta -= 0.12
    if decision == "yes" and delta < 0:
        delta *= 0.35
    if decision == "no" and delta > 0:
        delta *= 0.35
    return _clamp(delta, -0.45, 0.45)


def _feedback_trim_delta(directives: Dict[str, bool]) -> Optional[Dict[str, float]]:
    start_delta = 0.0
    end_delta = 0.0
    if directives.get("shorter"):
        start_delta += FEEDBACK_TRIM_SECONDS * 0.5
        end_delta -= FEEDBACK_TRIM_SECONDS * 0.5
    if directives.get("start_later"):
        start_delta += FEEDBACK_TRIM_SECONDS
    if directives.get("end_sooner"):
        end_delta -= FEEDBACK_TRIM_SECONDS
    if directives.get("more_lead_in"):
        start_delta -= FEEDBACK_TRIM_SECONDS
    if directives.get("more_after"):
        end_delta += FEEDBACK_TRIM_SECONDS
    if abs(start_delta) < 0.05 and abs(end_delta) < 0.05:
        return None
    return {"start_delta": start_delta, "end_delta": end_delta}


def _feedback_weight(decision: str) -> float:
    if decision == "yes":
        return 1.2
    if decision == "no":
        return 1.0
    if decision == "skip":
        return 0.55
    return 0.75


def _has_any(text: str, phrases: List[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _apply_trim_delta(
    start: float,
    end: float,
    highlight_time: Optional[float],
    start_delta: float,
    end_delta: float,
    source_duration: float,
) -> Optional[tuple[float, float, Optional[float]]]:
    next_start = max(0.0, start + start_delta)
    next_end = end + end_delta
    if source_duration > 0.0:
        next_end = min(source_duration, next_end)
    next_end = max(0.0, next_end)
    if next_end - next_start < MIN_LEARNED_DURATION_SECONDS:
        return None
    if highlight_time is not None:
        highlight_time = min(max(highlight_time, next_start), next_end)
    return next_start, next_end, highlight_time


def _direct_score_delta(candidate: Segment, entries: List[Dict[str, object]]) -> float:
    if not candidate.source_path:
        return 0.0
    candidate_source = _source_key(candidate.source_path)
    best_delta = 0.0
    for entry in entries:
        if str(entry.get("source_key") or "") != candidate_source:
            continue
        try:
            overlap = _overlap_ratio(float(candidate.start), float(candidate.end), float(entry.get("start")), float(entry.get("end")))
        except (TypeError, ValueError):
            continue
        if overlap <= 0.55:
            continue
        decision = str(entry.get("decision") or "")
        if decision == "yes":
            best_delta = max(best_delta, 0.35)
        elif decision == "no":
            best_delta = min(best_delta, -0.70)
        elif decision == "skip":
            best_delta = min(best_delta, -0.08)
    return best_delta


def _overlap_ratio(first_start: float, first_end: float, second_start: float, second_end: float) -> float:
    intersection = max(0.0, min(first_end, second_end) - max(first_start, second_start))
    if intersection <= 0.0:
        return 0.0
    return intersection / max(min(first_end - first_start, second_end - second_start), 1e-6)


def _source_duration_for(source_path: Optional[str], source_durations: Optional[Dict[str, float]]) -> float:
    if not source_path or not source_durations:
        return 0.0
    if source_path in source_durations:
        return _safe_float(source_durations[source_path])
    source_key = _source_key(source_path)
    for key, value in source_durations.items():
        if _source_key(str(key)) == source_key:
            return _safe_float(value)
    return 0.0


def _append_memory_notes(note: str, notes: List[str]) -> str:
    result = note or ""
    for memory_note in notes:
        if memory_note.lower() in result.lower():
            continue
        result = f"{result} {memory_note}".strip()
    return result


def _memory_entries(memory: Dict[str, object]) -> Iterable[Dict[str, object]]:
    clips = memory.get("clips")
    if not isinstance(clips, dict):
        return []
    return [entry for entry in clips.values() if isinstance(entry, dict)]


def _memory_key(source_path: str, start: float, end: float) -> str:
    return f"{_source_key(source_path)}|{round(start, 3):.3f}|{round(end, 3):.3f}"


def _source_key(source_path: str) -> str:
    if not source_path:
        return ""
    try:
        return str(Path(source_path).expanduser().resolve()).lower()
    except OSError:
        return source_path.lower()


def _normalize_edit(edit: Optional[Dict[str, float]], source_duration: float) -> Optional[Dict[str, float]]:
    if not isinstance(edit, dict):
        return None
    try:
        start = float(edit.get("start"))
        end = float(edit.get("end"))
    except (TypeError, ValueError):
        return None
    upper = source_duration if source_duration > 0.0 else max(end, 0.0)
    start = _clamp(start, 0.0, upper)
    end = _clamp(end, 0.0, upper)
    if end - start < MIN_LEARNED_DURATION_SECONDS:
        return None
    return {"start": round(start, 3), "end": round(end, 3)}


def _clip_attr(clip: object, name: str, default: object = None) -> object:
    if isinstance(clip, dict):
        return clip.get(name, default)
    return getattr(clip, name, default)


def _float_attr(clip: object, name: str) -> float:
    return _safe_float(_clip_attr(clip, name, 0.0))


def _optional_float_attr(clip: object, name: str) -> Optional[float]:
    value = _clip_attr(clip, name, None)
    if value is None:
        return None
    return round(_safe_float(value), 3)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))
