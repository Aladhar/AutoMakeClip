from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from .config import AnalysisConfig
from .ffmpeg import extract_audio_samples, iter_analysis_frames
from .types import AnalysisTimeline, Segment, VideoMetadata


@dataclass
class MontageProfile:
    mood: str
    target_bpm: float
    silly_segment: Optional[Segment]


def analyze_gameplay(source_path, metadata: VideoMetadata, config: AnalysisConfig) -> AnalysisTimeline:
    frame_times, visual_motion, killfeed_motion, hud_motion, center_motion, scene_change = _analyze_frames(source_path, metadata, config)
    audio_rms, audio_flux = _analyze_audio(source_path, metadata.duration, config)

    timeline_times = frame_times
    audio_rms = _fit_to_length(audio_rms, len(timeline_times))
    audio_flux = _fit_to_length(audio_flux, len(timeline_times))
    gameplay_confidence = _smooth(
        0.42 * _robust_normalize(center_motion)
        + 0.33 * _robust_normalize(hud_motion)
        + 0.25 * _robust_normalize(visual_motion),
        max(3, config.score_smoothing_frames - 1),
    )

    weighted = (
        config.weights["visual_motion"] * _robust_normalize(visual_motion)
        + config.weights["killfeed_motion"] * _robust_normalize(killfeed_motion)
        + config.weights["hud_motion"] * _robust_normalize(hud_motion)
        + 0.12 * _robust_normalize(center_motion)
        + config.weights["audio_rms"] * _robust_normalize(audio_rms)
        + config.weights["audio_flux"] * _robust_normalize(audio_flux)
        + config.weights["scene_change"] * _robust_normalize(scene_change)
    )
    synergy = np.clip(_robust_normalize(killfeed_motion), 0, None) * np.clip(_robust_normalize(audio_flux), 0, None) * 0.15
    smoothed = _smooth(weighted + synergy + 0.10 * np.clip(gameplay_confidence, 0, None), config.score_smoothing_frames)

    return AnalysisTimeline(
        times=timeline_times.tolist(),
        visual_motion=visual_motion.tolist(),
        killfeed_motion=killfeed_motion.tolist(),
        hud_motion=hud_motion.tolist(),
        center_motion=center_motion.tolist(),
        audio_rms=audio_rms.tolist(),
        audio_flux=audio_flux.tolist(),
        scene_change=scene_change.tolist(),
        gameplay_confidence=gameplay_confidence.tolist(),
        scores=smoothed.tolist(),
        duration=metadata.duration,
    )


def pick_segments(timeline: AnalysisTimeline, metadata: VideoMetadata, config: AnalysisConfig, target_seconds: float, include_silly: bool) -> Tuple[List[Segment], MontageProfile]:
    candidates = extract_candidate_segments(timeline, metadata, config)
    selected = select_top_segments(candidates, target_seconds, config)
    silly_segment = _pick_silly_segment(timeline, metadata, config, selected) if include_silly else None
    if silly_segment is not None:
        insert_at = min(len(selected), max(1, len(selected) // 3 + 1))
        selected.insert(insert_at, silly_segment)

    mood, target_bpm = infer_montage_profile(selected)
    return selected, MontageProfile(mood=mood, target_bpm=target_bpm, silly_segment=silly_segment)


def extract_candidate_segments(timeline: AnalysisTimeline, metadata: VideoMetadata, config: AnalysisConfig) -> List[Segment]:
    event_candidates = _extract_event_segments(timeline, metadata, config)
    generic_peak_candidates = _extract_generic_peak_segments(timeline, metadata, config)
    heuristic_candidates = _extract_heuristic_segments(timeline, metadata, config)

    candidates = list(event_candidates)
    candidates = _extend_diverse_candidates(
        candidates,
        generic_peak_candidates,
        quota=config.generic_peak_quota if event_candidates else max(config.generic_peak_quota, 4),
    )
    candidates = _extend_diverse_candidates(
        candidates,
        [
            Segment(
                start=candidate.start,
                end=candidate.end,
                score=candidate.score * 0.88,
                label="fight",
                note="High-action fallback window without a logged kill event.",
            )
            for candidate in heuristic_candidates
        ],
        quota=config.fallback_fight_quota if (event_candidates or generic_peak_candidates) else max(config.fallback_fight_quota, 3),
    )
    return candidates or heuristic_candidates


def _extract_heuristic_segments(timeline: AnalysisTimeline, metadata: VideoMetadata, config: AnalysisConfig) -> List[Segment]:
    times = np.array(timeline.times, dtype=np.float32)
    scores = np.array(timeline.scores, dtype=np.float32)
    gameplay = np.array(timeline.gameplay_confidence, dtype=np.float32)
    threshold = np.percentile(scores, config.highlight_threshold_percentile)
    gameplay_floor = np.percentile(gameplay, 58)
    above = (scores >= threshold) & (gameplay >= gameplay_floor)
    candidates: List[Segment] = []

    start_index = None
    for index, is_active in enumerate(above):
        if is_active and start_index is None:
            start_index = index
        elif not is_active and start_index is not None:
            candidate = _segment_from_range(start_index, index - 1, scores, times, metadata.duration, config)
            if candidate is not None:
                candidates.append(candidate)
            start_index = None
    if start_index is not None:
        candidate = _segment_from_range(start_index, len(scores) - 1, scores, times, metadata.duration, config)
        if candidate is not None:
            candidates.append(candidate)

    return candidates


def select_top_segments(candidates: List[Segment], target_seconds: float, config: AnalysisConfig) -> List[Segment]:
    candidates.sort(key=lambda segment: segment.score, reverse=True)
    selected: List[Segment] = []
    budget = max(8.0, target_seconds - config.intro_seconds)
    for candidate in candidates:
        if any(_overlap(candidate, existing) > 0.60 for existing in selected):
            continue
        if sum(item.duration for item in selected) + candidate.duration > budget + 1.5 and selected:
            continue
        selected.append(candidate)
        if sum(item.duration for item in selected) >= budget:
            break

    selected.sort(key=lambda segment: segment.start)
    return selected


def _analyze_frames(source_path, metadata: VideoMetadata, config: AnalysisConfig):
    motion: List[float] = []
    killfeed: List[float] = []
    hud: List[float] = []
    center: List[float] = []
    scene: List[float] = []
    times: List[float] = []

    previous_gray = None
    for time_seconds, frame in iter_analysis_frames(source_path, config.analysis_fps, config.analysis_width, metadata):
        gray = frame.astype(np.float32).mean(axis=2) / 255.0
        if previous_gray is None:
            diff = np.zeros_like(gray)
        else:
            diff = np.abs(gray - previous_gray)

        motion.append(float(diff.mean()))
        killfeed.append(float(_roi(diff, config.killfeed_roi).mean()))
        hud.append(float(_roi(diff, config.hud_roi).mean()))
        center.append(float(_roi(diff, config.center_roi).mean()))
        scene.append(float(np.percentile(diff, 95)))
        times.append(time_seconds)
        previous_gray = gray

    if not times:
        raise RuntimeError("No analysis frames were generated.")

    return (
        np.array(times, dtype=np.float32),
        np.array(motion, dtype=np.float32),
        np.array(killfeed, dtype=np.float32),
        np.array(hud, dtype=np.float32),
        np.array(center, dtype=np.float32),
        np.array(scene, dtype=np.float32),
    )


def _analyze_audio(source_path, duration: float, config: AnalysisConfig) -> Tuple[np.ndarray, np.ndarray]:
    samples = extract_audio_samples(source_path, config.audio_sample_rate)
    window_size = max(256, int(config.audio_sample_rate / max(config.analysis_fps, 1)))
    window_count = max(1, int(np.ceil(duration * config.analysis_fps)))

    rms_values = np.zeros(window_count, dtype=np.float32)
    flux_values = np.zeros(window_count, dtype=np.float32)
    previous_spectrum = None

    for index in range(window_count):
        start = index * window_size
        end = min(len(samples), start + window_size)
        window = samples[start:end]
        if window.size == 0:
            continue

        rms_values[index] = float(np.sqrt(np.mean(np.square(window)) + 1e-10))
        padded = window if window.size == window_size else np.pad(window, (0, window_size - window.size))
        spectrum = np.abs(np.fft.rfft(padded * np.hanning(window_size)))
        if previous_spectrum is not None:
            flux_values[index] = float(np.mean(np.clip(spectrum - previous_spectrum, 0.0, None)))
        previous_spectrum = spectrum

    return rms_values, flux_values


def _pick_silly_segment(timeline: AnalysisTimeline, metadata: VideoMetadata, config: AnalysisConfig, selected: List[Segment]) -> Optional[Segment]:
    scores = np.array(timeline.scores, dtype=np.float32)
    scenes = np.array(timeline.scene_change, dtype=np.float32)
    center = np.array(timeline.center_motion, dtype=np.float32)
    gameplay = np.array(timeline.gameplay_confidence, dtype=np.float32)
    killfeed = np.array(timeline.killfeed_motion, dtype=np.float32)
    times = np.array(timeline.times, dtype=np.float32)

    lower = np.percentile(scores, 42)
    upper = np.percentile(scores, 72)
    gameplay_floor = np.percentile(gameplay, 68)
    killfeed_cap = np.percentile(killfeed, 62)
    novelty = (
        0.70 * _robust_normalize(scenes)
        + 0.45 * _robust_normalize(center)
        - 0.30 * _robust_normalize(killfeed)
    )
    mask = (scores >= lower) & (scores <= upper) & (gameplay >= gameplay_floor) & (killfeed <= killfeed_cap)
    if not np.any(mask):
        return None

    candidate_indices = np.where(mask)[0]
    if candidate_indices.size == 0:
        return None

    duration = float(np.mean(config.comedy_segment_seconds))
    for best_index in candidate_indices[np.argsort(novelty[candidate_indices])[::-1]]:
        midpoint = float(times[int(best_index)])
        if _near_kill_event(midpoint, metadata.kill_events, window=4.0):
            continue
        segment = Segment(
            start=max(0.0, midpoint - duration * 0.55),
            end=min(timeline.duration, midpoint + duration * 0.45),
            score=float(scores[int(best_index)]),
            label="silly",
            note="Short chaos/comedy breath between bigger plays.",
        )
        if any(_overlap(segment, item) > 0.45 for item in selected):
            continue
        if segment.duration < config.comedy_segment_seconds[0]:
            continue
        return segment
    return None


def infer_montage_profile(segments: List[Segment]) -> Tuple[str, float]:
    if not segments:
        return "balanced", 120.0

    avg_duration = float(np.mean([segment.duration for segment in segments]))
    slay_share = sum(1 for segment in segments if segment.label == "slay") / float(len(segments))
    silly_present = any(segment.label == "silly" for segment in segments)

    if slay_share >= 0.72 and avg_duration < 4.9:
        return "aggro", 152.0 if silly_present else 160.0
    if silly_present:
        return "chaotic", 126.0
    if slay_share >= 0.50:
        return "aggro", 148.0
    return "balanced", 138.0


def _segment_from_range(start_index: int, end_index: int, scores: np.ndarray, times: np.ndarray, duration: float, config: AnalysisConfig) -> Optional[Segment]:
    if end_index < start_index:
        return None

    local_scores = scores[start_index : end_index + 1]
    peak_offset = int(np.argmax(local_scores))
    peak_index = start_index + peak_offset
    peak_time = float(times[peak_index])
    peak_score = float(scores[peak_index])

    start = max(0.0, peak_time - config.pre_roll_seconds)
    end = min(duration, peak_time + config.post_roll_seconds)
    segment_duration = end - start
    if segment_duration < config.min_segment_seconds:
        extension = (config.min_segment_seconds - segment_duration) / 2.0
        start = max(0.0, start - extension)
        end = min(duration, end + extension)
    if (end - start) > config.max_segment_seconds:
        end = start + config.max_segment_seconds

    label = "slay" if peak_score > np.percentile(scores, 90) else "fight"
    note = "Kill-feed heavy fight window." if label == "slay" else "Active team-fight section."
    segment = Segment(start=start, end=end, score=peak_score, label=label, note=note)
    if segment.duration < config.min_segment_seconds:
        return None
    return segment


def _extract_event_segments(timeline: AnalysisTimeline, metadata: VideoMetadata, config: AnalysisConfig) -> List[Segment]:
    if not metadata.kill_events:
        return []

    times = np.array(timeline.times, dtype=np.float32)
    scores = np.array(timeline.scores, dtype=np.float32)
    gameplay = np.array(timeline.gameplay_confidence, dtype=np.float32)
    killfeed = np.array(timeline.killfeed_motion, dtype=np.float32)

    clusters: List[List[dict]] = []
    current: List[dict] = []
    for event in sorted(metadata.kill_events, key=lambda item: float(item.get("timestamp", 0.0))):
        if not current:
            current = [event]
            continue
        if float(event["timestamp"]) - float(current[-1]["timestamp"]) <= config.event_merge_gap_seconds:
            current.append(event)
        else:
            clusters.append(current)
            current = [event]
    if current:
        clusters.append(current)

    segments: List[Segment] = []
    for cluster in clusters:
        first_time = float(cluster[0]["timestamp"])
        last_time = float(cluster[-1]["timestamp"])
        start = max(0.0, first_time - config.event_pre_roll_seconds)
        end = min(metadata.duration, last_time + config.event_post_roll_seconds)
        start, end = _clamp_segment(start, end, metadata.duration, config)
        if end - start < config.min_segment_seconds:
            continue

        local_mask = (times >= start) & (times <= end)
        local_score = float(np.max(scores[local_mask])) if np.any(local_mask) else 0.0
        local_gameplay = float(np.mean(gameplay[local_mask])) if np.any(local_mask) else 0.0
        local_killfeed = float(np.max(killfeed[local_mask])) if np.any(local_mask) else 0.0
        event_points = sum(_event_weight(str(event.get("name") or "")) for event in cluster)
        cluster_size = len(cluster)

        score = event_points * 1.15 + local_score * 0.65 + max(local_gameplay, 0.0) * 0.35 + max(local_killfeed, 0.0) * 0.20
        label = "slay" if cluster_size >= 2 or event_points >= 2.2 else "fight"
        note = "SteelSeries multi-kill sequence." if label == "slay" else "SteelSeries kill event window."
        segments.append(Segment(start=start, end=end, score=score, label=label, note=note))

    return segments


def _extract_generic_peak_segments(timeline: AnalysisTimeline, metadata: VideoMetadata, config: AnalysisConfig) -> List[Segment]:
    times = np.array(timeline.times, dtype=np.float32)
    scores = np.array(timeline.scores, dtype=np.float32)
    gameplay = np.array(timeline.gameplay_confidence, dtype=np.float32)
    killfeed = np.array(timeline.killfeed_motion, dtype=np.float32)
    center = np.array(timeline.center_motion, dtype=np.float32)
    hud = np.array(timeline.hud_motion, dtype=np.float32)
    audio_flux = np.array(timeline.audio_flux, dtype=np.float32)
    scenes = np.array(timeline.scene_change, dtype=np.float32)

    positive_killfeed = np.clip(_robust_normalize(killfeed), 0.0, None)
    positive_audio = np.clip(_robust_normalize(audio_flux), 0.0, None)
    positive_center = np.clip(_robust_normalize(center), 0.0, None)
    positive_hud = np.clip(_robust_normalize(hud), 0.0, None)
    positive_scene = np.clip(_robust_normalize(scenes), 0.0, None)
    positive_scores = np.clip(_robust_normalize(scores), 0.0, None)

    generic_signal = (
        0.33 * positive_killfeed
        + 0.21 * positive_audio
        + 0.16 * positive_center
        + 0.12 * positive_hud
        + 0.10 * positive_scene
        + 0.08 * positive_scores
    )
    threshold = np.percentile(generic_signal, config.generic_peak_threshold_percentile)
    gameplay_floor = np.percentile(gameplay, 60)
    candidate_indices: List[int] = []
    for index, value in enumerate(generic_signal):
        if value < threshold or gameplay[index] < gameplay_floor:
            continue
        left = generic_signal[index - 1] if index > 0 else -np.inf
        right = generic_signal[index + 1] if index + 1 < len(generic_signal) else -np.inf
        if value < left or value < right:
            continue
        candidate_indices.append(index)

    accepted_indices: List[int] = []
    segments: List[Segment] = []
    for index in sorted(candidate_indices, key=lambda item: generic_signal[item], reverse=True):
        peak_time = float(times[index])
        if any(abs(peak_time - float(times[accepted])) < config.generic_peak_min_spacing_seconds for accepted in accepted_indices):
            continue
        accepted_indices.append(index)
        start = max(0.0, peak_time - config.pre_roll_seconds)
        end = min(metadata.duration, peak_time + config.post_roll_seconds)
        start, end = _clamp_segment(start, end, metadata.duration, config)
        peak_strength = float(generic_signal[index])
        label = "slay" if (positive_killfeed[index] + 0.7 * positive_audio[index]) >= 1.25 else "fight"
        note = "Generic kill-heavy peak window." if label == "slay" else "Generic high-activity fight window."
        segments.append(
            Segment(
                start=start,
                end=end,
                score=peak_strength * 2.4 + float(scores[index]) * 0.55 + max(float(gameplay[index]), 0.0) * 0.25,
                label=label,
                note=note,
            )
        )
    return segments


def _event_weight(name: str) -> float:
    normalized = name.upper()
    if "PENTA" in normalized:
        return 5.0
    if "QUAD" in normalized:
        return 4.0
    if "TRIPLE" in normalized:
        return 3.0
    if "DOUBLE" in normalized:
        return 2.0
    return 1.0


def _clamp_segment(start: float, end: float, duration: float, config: AnalysisConfig) -> Tuple[float, float]:
    segment_duration = end - start
    if segment_duration < config.min_segment_seconds:
        extension = (config.min_segment_seconds - segment_duration) / 2.0
        start = max(0.0, start - extension)
        end = min(duration, end + extension)
    if (end - start) > config.max_segment_seconds:
        midpoint = (start + end) / 2.0
        half = config.max_segment_seconds / 2.0
        start = max(0.0, midpoint - half)
        end = min(duration, midpoint + half)
        if (end - start) > config.max_segment_seconds:
            end = min(duration, start + config.max_segment_seconds)
    return start, end


def _near_kill_event(time_seconds: float, kill_events: List[dict], window: float) -> bool:
    for event in kill_events:
        try:
            timestamp = float(event.get("timestamp", 0.0))
        except (AttributeError, TypeError, ValueError):
            continue
        if abs(timestamp - time_seconds) <= window:
            return True
    return False


def _extend_diverse_candidates(existing: List[Segment], additions: List[Segment], quota: int) -> List[Segment]:
    if quota <= 0:
        return existing
    merged = list(existing)
    accepted = 0
    for candidate in sorted(additions, key=lambda item: item.score, reverse=True):
        if accepted >= quota:
            break
        if any(_overlap(candidate, current) > 0.45 for current in merged):
            continue
        merged.append(candidate)
        accepted += 1
    return merged


def _fit_to_length(values: np.ndarray, target_length: int) -> np.ndarray:
    if values.size == target_length:
        return values
    if values.size == 0:
        return np.zeros(target_length, dtype=np.float32)
    source_positions = np.linspace(0, 1, num=values.size, dtype=np.float32)
    target_positions = np.linspace(0, 1, num=target_length, dtype=np.float32)
    return np.interp(target_positions, source_positions, values).astype(np.float32)


def _roi(frame: np.ndarray, roi: Tuple[float, float, float, float]) -> np.ndarray:
    x, y, width, height = roi
    frame_height, frame_width = frame.shape[:2]
    x0 = int(frame_width * x)
    y0 = int(frame_height * y)
    x1 = min(frame_width, int(frame_width * (x + width)))
    y1 = min(frame_height, int(frame_height * (y + height)))
    return frame[y0:y1, x0:x1]


def _robust_normalize(values: np.ndarray) -> np.ndarray:
    values = np.array(values, dtype=np.float32)
    median = np.median(values)
    upper = np.percentile(values, 75)
    lower = np.percentile(values, 25)
    spread = max(upper - lower, 1e-6)
    return (values - median) / spread


def _smooth(values: np.ndarray, window_size: int) -> np.ndarray:
    if window_size <= 1:
        return values
    kernel = np.ones(window_size, dtype=np.float32) / window_size
    return np.convolve(values, kernel, mode="same")


def _overlap(first: Segment, second: Segment) -> float:
    intersection = max(0.0, min(first.end, second.end) - max(first.start, second.start))
    if intersection <= 0:
        return 0.0
    minimum = max(min(first.duration, second.duration), 1e-6)
    return intersection / minimum
