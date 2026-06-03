from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from .config import AnalysisConfig
from .ffmpeg import extract_audio_samples, iter_analysis_frames
from .types import AnalysisTimeline, Segment, VideoMetadata


REPETITIVE_EVENT_MIN_COUNT = 14
REPETITIVE_EVENT_RATE_PER_MINUTE = 15.0
REPETITIVE_EVENT_BURST_COUNT = 10
REPETITIVE_EVENT_BURST_SECONDS = 22.0


@dataclass
class MontageProfile:
    mood: str
    target_bpm: float
    silly_segment: Optional[Segment]


def analyze_gameplay(source_path, metadata: VideoMetadata, config: AnalysisConfig) -> AnalysisTimeline:
    frame_times, visual_motion, killfeed_motion, hud_motion, center_motion, scene_change, inactive_prompt = _analyze_frames(source_path, metadata, config)
    audio_rms, audio_flux = _analyze_audio(source_path, metadata.duration, config)

    timeline_times = frame_times
    audio_rms = _fit_to_length(audio_rms, len(timeline_times))
    audio_flux = _fit_to_length(audio_flux, len(timeline_times))
    absolute_presence = (
        0.34 * _absolute_presence_score(center_motion, floor=0.010, scale=0.080)
        + 0.26 * _absolute_presence_score(hud_motion, floor=0.010, scale=0.080)
        + 0.20 * _absolute_presence_score(visual_motion, floor=0.010, scale=0.080)
        + 0.12 * _absolute_presence_score(killfeed_motion, floor=0.020, scale=0.120)
        + 0.08 * _absolute_presence_score(scene_change, floor=0.030, scale=0.240)
    )
    inactive_penalty = (
        0.62 * _absolute_presence_score(inactive_prompt, floor=0.025, scale=0.090)
        + 0.38 * np.clip(_robust_normalize(inactive_prompt), 0.0, None)
    )
    gameplay_confidence = _smooth(
        0.46 * _robust_normalize(center_motion)
        + 0.30 * _robust_normalize(hud_motion)
        + 0.14 * _robust_normalize(visual_motion)
        + 0.60 * absolute_presence,
        max(3, config.score_smoothing_frames - 1),
    )
    gameplay_confidence = gameplay_confidence - 0.95 * inactive_penalty

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
    smoothed = _smooth(
        weighted + synergy + 0.10 * np.clip(gameplay_confidence, 0, None) - 0.75 * inactive_penalty,
        config.score_smoothing_frames,
    )

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
        inactive_overlay=inactive_prompt.tolist(),
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


def collect_candidate_segments(
    timeline: AnalysisTimeline,
    metadata: VideoMetadata,
    config: AnalysisConfig,
    include_silly: bool,
) -> List[Segment]:
    candidates = list(extract_candidate_segments(timeline, metadata, config))
    silly_segment = _pick_silly_segment(timeline, metadata, config, []) if include_silly else None
    if silly_segment is not None and not any(_overlap(silly_segment, candidate) > 0.45 for candidate in candidates):
        candidates.append(silly_segment)
    return sorted(candidates, key=lambda segment: (segment.start, segment.end, -segment.score))


def extract_candidate_segments(timeline: AnalysisTimeline, metadata: VideoMetadata, config: AnalysisConfig) -> List[Segment]:
    if not metadata.kill_events and not _source_has_meaningful_gameplay(timeline):
        return []

    event_candidates = _extract_event_segments(timeline, metadata, config)
    if event_candidates:
        return list(event_candidates)

    generic_peak_candidates = _extract_generic_peak_segments(timeline, metadata, config)
    return list(generic_peak_candidates)


def extract_borderline_review_segments(
    timeline: AnalysisTimeline,
    metadata: VideoMetadata,
    config: AnalysisConfig,
    protected_segments: Optional[List[Segment]] = None,
) -> List[Segment]:
    protected = list(protected_segments) if protected_segments is not None else extract_candidate_segments(timeline, metadata, config)
    times = np.array(timeline.times, dtype=np.float32)
    scores = np.array(timeline.scores, dtype=np.float32)
    gameplay = np.array(timeline.gameplay_confidence, dtype=np.float32)
    killfeed = np.array(timeline.killfeed_motion, dtype=np.float32)
    center = np.array(timeline.center_motion, dtype=np.float32)
    audio_flux = np.array(timeline.audio_flux, dtype=np.float32)
    scenes = np.array(timeline.scene_change, dtype=np.float32)

    lower = np.percentile(scores, max(52.0, config.highlight_threshold_percentile - 16.0))
    upper = np.percentile(scores, min(92.0, config.highlight_threshold_percentile + 4.0))
    gameplay_floor = np.percentile(gameplay, 55)
    band_halfwidth = max((upper - lower) / 2.0, 1e-6)
    band_midpoint = (lower + upper) / 2.0

    positive_killfeed = np.clip(_robust_normalize(killfeed), 0.0, None)
    positive_center = np.clip(_robust_normalize(center), 0.0, None)
    positive_audio = np.clip(_robust_normalize(audio_flux), 0.0, None)
    positive_scenes = np.clip(_robust_normalize(scenes), 0.0, None)
    score_band_closeness = 1.0 - np.minimum(np.abs(scores - band_midpoint) / band_halfwidth, 1.0)
    borderline_metric = (
        0.42 * score_band_closeness
        + 0.23 * positive_killfeed
        + 0.17 * positive_audio
        + 0.10 * positive_center
        + 0.08 * positive_scenes
    )
    mask = (scores >= lower) & (scores <= upper) & (gameplay >= gameplay_floor)
    candidate_indices: List[int] = []
    for index, value in enumerate(borderline_metric):
        if not mask[index]:
            continue
        left = borderline_metric[index - 1] if index > 0 else -np.inf
        right = borderline_metric[index + 1] if index + 1 < len(borderline_metric) else -np.inf
        if value < left or value < right:
            continue
        candidate_indices.append(index)

    segments: List[Segment] = []
    accepted_peak_times: List[float] = []
    max_segments = max(2, min(5, config.fallback_fight_quota + 3))
    min_spacing_seconds = max(2.0, config.generic_peak_min_spacing_seconds - 0.5)
    for index in sorted(candidate_indices, key=lambda item: borderline_metric[item], reverse=True):
        peak_time = float(times[index])
        if any(abs(peak_time - accepted) < min_spacing_seconds for accepted in accepted_peak_times):
            continue
        segment = _segment_around_peak(
            peak_time=peak_time,
            peak_score=float(scores[index]) * 0.97,
            duration=metadata.duration,
            config=config,
            label="highlight" if (positive_killfeed[index] + 0.6 * positive_audio[index]) >= 1.15 else "fight",
            note="Borderline discard candidate: active, but likely too weak or too messy for the final montage.",
        )
        if any(_overlap(segment, protected_segment) > 0.40 for protected_segment in protected):
            continue
        if any(_overlap(segment, existing) > 0.45 for existing in segments):
            continue
        segments.append(segment)
        accepted_peak_times.append(peak_time)
        if len(segments) >= max_segments:
            break

    return segments


def _extract_heuristic_segments(timeline: AnalysisTimeline, metadata: VideoMetadata, config: AnalysisConfig) -> List[Segment]:
    times = np.array(timeline.times, dtype=np.float32)
    scores = np.array(timeline.scores, dtype=np.float32)
    gameplay = np.array(timeline.gameplay_confidence, dtype=np.float32)
    killfeed = np.array(timeline.killfeed_motion, dtype=np.float32)
    hud = np.array(timeline.hud_motion, dtype=np.float32)
    center = np.array(timeline.center_motion, dtype=np.float32)
    visual = np.array(timeline.visual_motion, dtype=np.float32)
    audio_flux = np.array(timeline.audio_flux, dtype=np.float32)
    positive_killfeed = np.clip(_robust_normalize(killfeed), 0.0, None)
    positive_hud = np.clip(_robust_normalize(hud), 0.0, None)
    positive_center = np.clip(_robust_normalize(center), 0.0, None)
    positive_visual = np.clip(_robust_normalize(visual), 0.0, None)
    positive_audio = np.clip(_robust_normalize(audio_flux), 0.0, None)
    absolute_killfeed = _absolute_presence_score(killfeed, floor=0.020, scale=0.120)
    absolute_hud = _absolute_presence_score(hud, floor=0.010, scale=0.080)
    absolute_center = _absolute_presence_score(center, floor=0.010, scale=0.080)
    absolute_visual = _absolute_presence_score(visual, floor=0.010, scale=0.080)
    absolute_audio = _absolute_presence_score(audio_flux, floor=0.050, scale=0.300)
    threshold = np.percentile(scores, config.highlight_threshold_percentile)
    gameplay_floor = np.percentile(gameplay, 58)
    above = (scores >= threshold) & (gameplay >= gameplay_floor)
    candidates: List[Segment] = []

    start_index = None
    for index, is_active in enumerate(above):
        if is_active and start_index is None:
            start_index = index
        elif not is_active and start_index is not None:
            candidate = _segment_from_range(
                start_index,
                index - 1,
                scores,
                times,
                metadata.duration,
                config,
                positive_killfeed,
                positive_hud,
                positive_center,
                positive_visual,
                positive_audio,
                absolute_killfeed,
                absolute_hud,
                absolute_center,
                absolute_visual,
                absolute_audio,
            )
            if candidate is not None:
                candidates.append(candidate)
            start_index = None
    if start_index is not None:
        candidate = _segment_from_range(
            start_index,
            len(scores) - 1,
            scores,
            times,
            metadata.duration,
            config,
            positive_killfeed,
            positive_hud,
            positive_center,
            positive_visual,
            positive_audio,
            absolute_killfeed,
            absolute_hud,
            absolute_center,
            absolute_visual,
            absolute_audio,
        )
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
    inactive_prompt: List[float] = []
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
        inactive_prompt.append(_inactive_overlay_score(frame))
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
        np.array(inactive_prompt, dtype=np.float32),
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
            highlight_time=midpoint,
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
    highlight_share = sum(1 for segment in segments if segment.label == "highlight") / float(len(segments))
    silly_present = any(segment.label == "silly" for segment in segments)

    if highlight_share >= 0.72 and avg_duration < 4.9:
        return "aggro", 152.0 if silly_present else 160.0
    if silly_present:
        return "chaotic", 126.0
    if highlight_share >= 0.50:
        return "aggro", 148.0
    return "balanced", 138.0


def _segment_from_range(
    start_index: int,
    end_index: int,
    scores: np.ndarray,
    times: np.ndarray,
    duration: float,
    config: AnalysisConfig,
    positive_killfeed: np.ndarray,
    positive_hud: np.ndarray,
    positive_center: np.ndarray,
    positive_visual: np.ndarray,
    positive_audio: np.ndarray,
    absolute_killfeed: np.ndarray,
    absolute_hud: np.ndarray,
    absolute_center: np.ndarray,
    absolute_visual: np.ndarray,
    absolute_audio: np.ndarray,
) -> Optional[Segment]:
    if end_index < start_index:
        return None

    local_scores = scores[start_index : end_index + 1]
    peak_offset = int(np.argmax(local_scores))
    peak_index = start_index + peak_offset
    peak_time = float(times[peak_index])
    support_votes = sum(
        [
            positive_killfeed[peak_index] >= 0.45,
            positive_audio[peak_index] >= 0.35,
            positive_hud[peak_index] >= 0.45,
            positive_center[peak_index] >= 0.40,
            positive_visual[peak_index] >= 0.40,
        ]
    )
    if support_votes < 2:
        return None
    if positive_killfeed[peak_index] < 0.30 and positive_audio[peak_index] < 0.30:
        return None
    if not _has_absolute_action_support(
        absolute_killfeed[peak_index],
        absolute_audio[peak_index],
        absolute_hud[peak_index],
        absolute_center[peak_index],
        absolute_visual[peak_index],
    ):
        return None

    peak_score = (
        float(scores[peak_index])
        + 0.35 * float(positive_killfeed[peak_index])
        + 0.22 * float(positive_audio[peak_index])
        + 0.18 * float(positive_hud[peak_index])
        + 0.14 * float(positive_center[peak_index])
    )

    label = "highlight" if (positive_killfeed[peak_index] + 0.65 * positive_audio[peak_index] + 0.30 * positive_hud[peak_index]) >= 1.35 else "fight"
    note = "Kill-feed heavy highlight window." if label == "highlight" else "Active team-fight section."
    segment = _segment_around_peak(
        peak_time=peak_time,
        peak_score=peak_score,
        duration=duration,
        config=config,
        label=label,
        note=note,
    )
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

    repetitive_event_stream = _has_repetitive_event_stream(metadata.kill_events, metadata.duration)
    segments: List[Segment] = []
    for cluster in clusters:
        first_time = float(cluster[0]["timestamp"])
        last_time = float(cluster[-1]["timestamp"])
        start = max(0.0, first_time - config.event_pre_roll_seconds)
        end = min(metadata.duration, last_time + config.event_post_roll_seconds)
        start, end = _clamp_event_segment(start, end, anchor=last_time, duration=metadata.duration, config=config)
        if end - start < config.min_segment_seconds:
            continue

        local_mask = (times >= start) & (times <= end)
        local_score = float(np.max(scores[local_mask])) if np.any(local_mask) else 0.0
        local_gameplay = float(np.mean(gameplay[local_mask])) if np.any(local_mask) else 0.0
        local_killfeed = float(np.max(killfeed[local_mask])) if np.any(local_mask) else 0.0
        event_points = sum(_event_weight(str(event.get("name") or "")) for event in cluster)
        cluster_size = len(cluster)

        score = event_points * 1.15 + local_score * 0.65 + max(local_gameplay, 0.0) * 0.35 + max(local_killfeed, 0.0) * 0.20
        label = "highlight" if cluster_size >= 2 or event_points >= 2.2 else "fight"
        note = "SteelSeries multi-kill sequence." if label == "highlight" else "SteelSeries kill event window."
        if repetitive_event_stream:
            score *= 0.25
            label = "fight"
            note = "Deprioritized high-density kill stream; possible repeated practice/training clip."
        segments.append(Segment(start=start, end=end, score=score, label=label, note=note, highlight_time=last_time))

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
    inactive_overlay = np.array(getattr(timeline, "inactive_overlay", []) or [], dtype=np.float32)
    inactive_overlay = _fit_to_length(inactive_overlay, len(times))

    positive_killfeed = np.clip(_robust_normalize(killfeed), 0.0, None)
    positive_audio = np.clip(_robust_normalize(audio_flux), 0.0, None)
    positive_center = np.clip(_robust_normalize(center), 0.0, None)
    positive_hud = np.clip(_robust_normalize(hud), 0.0, None)
    positive_scene = np.clip(_robust_normalize(scenes), 0.0, None)
    positive_scores = np.clip(_robust_normalize(scores), 0.0, None)
    killfeed_burst = _local_burst_signal(killfeed, radius=3)
    center_burst = _local_burst_signal(center, radius=3)
    positive_killfeed_burst = np.clip(_robust_normalize(killfeed_burst), 0.0, None)
    positive_center_burst = np.clip(_robust_normalize(center_burst), 0.0, None)
    absolute_killfeed = _absolute_presence_score(killfeed, floor=0.020, scale=0.120)
    absolute_killfeed_burst = _absolute_presence_score(killfeed_burst, floor=0.010, scale=0.060)
    absolute_hud = _absolute_presence_score(hud, floor=0.010, scale=0.080)
    absolute_center = _absolute_presence_score(center, floor=0.010, scale=0.080)
    absolute_center_burst = _absolute_presence_score(center_burst, floor=0.010, scale=0.060)
    absolute_visual = _absolute_presence_score(np.array(timeline.visual_motion, dtype=np.float32), floor=0.010, scale=0.080)
    absolute_audio = _absolute_presence_score(audio_flux, floor=0.050, scale=0.300)
    absolute_scene = _absolute_presence_score(scenes, floor=0.030, scale=0.240)
    absolute_inactive = _absolute_presence_score(inactive_overlay, floor=0.025, scale=0.090)
    positive_inactive = np.clip(_robust_normalize(inactive_overlay), 0.0, None)
    activity_signal = (
        0.42 * positive_killfeed
        + 0.22 * positive_killfeed_burst
        + 0.18 * positive_center
        + 0.10 * positive_center_burst
        + 0.08 * positive_scores
    )

    generic_signal = (
        0.34 * positive_killfeed
        + 0.22 * positive_killfeed_burst
        + 0.15 * positive_center
        + 0.11 * positive_center_burst
        + 0.10 * positive_audio
        + 0.08 * positive_scores
    )
    threshold = np.percentile(generic_signal, config.generic_peak_threshold_percentile)
    gameplay_floor = np.percentile(gameplay, 66)
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
        signal_votes = sum(
            [
                positive_killfeed[index] >= 0.55,
                positive_killfeed_burst[index] >= 0.55,
                positive_center[index] >= 0.45,
                positive_center_burst[index] >= 0.45,
                positive_scores[index] >= 0.55,
            ]
        )
        if signal_votes < 2:
            continue
        # Loosen killfeed presence requirement to improve recall on eventless
        # sources while still requiring multiple signal votes for acceptance.
        if positive_killfeed[index] < 0.38:
            continue
        if absolute_inactive[index] >= 0.18 or positive_inactive[index] >= 0.60:
            continue
        if not _has_eklipse_style_big_play_support(
            absolute_killfeed[index],
            absolute_killfeed_burst[index],
            absolute_audio[index],
            absolute_center[index],
            absolute_center_burst[index],
            absolute_hud[index],
            absolute_visual[index],
            absolute_scene[index],
        ):
            continue
        accepted_indices.append(index)
        start, end = _big_play_segment_bounds(
            peak_index=index,
            times=times,
            activity_signal=activity_signal,
            duration=metadata.duration,
            config=config,
        )
        peak_strength = float(generic_signal[index])
        label = "highlight" if (positive_killfeed[index] + 0.7 * positive_audio[index] + 0.35 * positive_hud[index]) >= 1.45 else "fight"
        note = "Generic kill-heavy peak window." if label == "highlight" else "Generic high-activity fight window."
        segments.append(
            Segment(
                start=start,
                end=end,
                score=(
                    peak_strength * 1.45
                    + float(positive_scores[index]) * 0.90
                    + float(positive_killfeed[index]) * 0.70
                    + float(positive_audio[index]) * 0.55
                    + max(float(gameplay[index]), 0.0) * 0.20
                ),
                label=label,
                note=note,
                highlight_time=peak_time,
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


def _has_repetitive_event_stream(kill_events: List[dict], duration: float) -> bool:
    timestamps: List[float] = []
    for event in kill_events:
        if str(event.get("type") or "").upper() != "KILL":
            continue
        try:
            timestamps.append(float(event.get("timestamp", 0.0)))
        except (TypeError, ValueError):
            continue

    if len(timestamps) < REPETITIVE_EVENT_MIN_COUNT:
        if len(timestamps) < REPETITIVE_EVENT_BURST_COUNT:
            return False
    events_per_minute = len(timestamps) / max(duration, 1.0) * 60.0
    if events_per_minute >= REPETITIVE_EVENT_RATE_PER_MINUTE:
        return True

    timestamps.sort()
    left_index = 0
    for right_index, timestamp in enumerate(timestamps):
        while timestamp - timestamps[left_index] > REPETITIVE_EVENT_BURST_SECONDS:
            left_index += 1
        if right_index - left_index + 1 >= REPETITIVE_EVENT_BURST_COUNT:
            return True
    return False


def _segment_around_peak(
    peak_time: float,
    peak_score: float,
    duration: float,
    config: AnalysisConfig,
    label: str,
    note: str,
) -> Segment:
    start = max(0.0, peak_time - config.pre_roll_seconds)
    end = min(duration, peak_time + config.post_roll_seconds)
    segment_duration = end - start
    if segment_duration < config.min_segment_seconds:
        extension = (config.min_segment_seconds - segment_duration) / 2.0
        start = max(0.0, start - extension)
        end = min(duration, end + extension)
    if (end - start) > config.max_segment_seconds:
        end = start + config.max_segment_seconds
    return Segment(start=start, end=end, score=peak_score, label=label, note=note, highlight_time=peak_time)


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


def _clamp_event_segment(start: float, end: float, anchor: float, duration: float, config: AnalysisConfig) -> Tuple[float, float]:
    if (end - start) > config.max_segment_seconds:
        target_duration = config.max_segment_seconds
        post_roll = min(config.event_post_roll_seconds, target_duration * 0.45)
        start = anchor - (target_duration - post_roll)
        end = anchor + post_roll

    start = max(0.0, start)
    end = min(duration, end)
    if end - start < config.min_segment_seconds:
        extension = (config.min_segment_seconds - (end - start)) / 2.0
        start = max(0.0, start - extension)
        end = min(duration, end + extension)

    if anchor < start:
        shift = start - anchor
        start = max(0.0, start - shift)
        end = min(duration, end - shift)
    elif anchor > end:
        shift = anchor - end
        start = max(0.0, start + shift)
        end = min(duration, end + shift)

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


def _change_hero_prompt_score(frame: np.ndarray) -> float:
    prompt = _roi(frame.astype(np.float32) / 255.0, (0.39, 0.86, 0.24, 0.12))
    if prompt.size == 0:
        return 0.0
    return _orange_ui_ratio(prompt)


def _death_spectating_score(frame: np.ndarray) -> float:
    prompt = _roi(frame.astype(np.float32) / 255.0, (0.02, 0.03, 0.34, 0.17))
    if prompt.size == 0:
        return 0.0
    return _orange_ui_ratio(prompt)


def _scoreboard_overlay_score(frame: np.ndarray) -> float:
    normalized = frame.astype(np.float32) / 255.0
    tabs = _roi(normalized, (0.02, 0.03, 0.26, 0.11))
    panel = _roi(normalized, (0.14, 0.18, 0.72, 0.60))
    if tabs.size == 0 or panel.size == 0:
        return 0.0

    orange_tabs = _orange_ui_ratio(tabs)
    blue_mask = (
        (tabs[..., 2] >= 0.34)
        & (tabs[..., 1] >= 0.24)
        & (tabs[..., 0] <= 0.24)
    )
    tabs_score = 0.55 * orange_tabs + 0.45 * float(blue_mask.mean())

    brightness = panel.mean(axis=2)
    saturation = np.max(panel, axis=2) - np.min(panel, axis=2)
    white_mask = (brightness >= 0.72) & (saturation <= 0.16)
    teal_mask = (
        (panel[..., 1] >= 0.35)
        & (panel[..., 2] >= 0.35)
        & (panel[..., 0] <= 0.28)
    )
    red_mask = (
        (panel[..., 0] >= 0.35)
        & (panel[..., 1] <= 0.28)
        & (panel[..., 2] <= 0.28)
    )
    panel_score = 0.55 * float(white_mask.mean()) + 0.25 * float(teal_mask.mean()) + 0.20 * float(red_mask.mean())
    return 0.55 * tabs_score + 0.45 * panel_score


def _killcam_bottom_hud_absent_score(frame: np.ndarray) -> float:
    """
    Detect kill-cam replay state by checking the bottom HUD region (health bar,
    abilities, ult charge). During normal gameplay this region has visible
    colored elements. During kill-cam replay the bottom HUD disappears,
    leaving a dark bar. ROI: x=0.10..0.80, y=0.75..0.90 (ability bar area).
    """
    normalized = frame.astype(np.float32) / 255.0
    region = _roi(normalized, (0.10, 0.75, 0.70, 0.15))
    if region.size == 0:
        return 0.0
    brightness = region.mean(axis=2)
    dim_threshold = 0.12
    dim_fraction = float((brightness < dim_threshold).mean())
    if dim_fraction > 0.70:
        return min(1.0, (dim_fraction - 0.70) / 0.20)
    return 0.0


def _inactive_overlay_score(frame: np.ndarray) -> float:
    change_hero = _change_hero_prompt_score(frame)
    death_spectating = _death_spectating_score(frame)
    scoreboard = _scoreboard_overlay_score(frame)
    killcam = _killcam_bottom_hud_absent_score(frame)
    return max(change_hero, death_spectating, scoreboard, killcam)


def _orange_ui_ratio(region: np.ndarray) -> float:
    red = region[..., 0]
    green = region[..., 1]
    blue = region[..., 2]
    orange_mask = (
        (red >= 0.48)
        & (green >= 0.18)
        & (green <= 0.62)
        & (blue <= 0.24)
        & ((red - green) >= 0.12)
    )
    return float(orange_mask.mean())


def _robust_normalize(values: np.ndarray) -> np.ndarray:
    values = np.array(values, dtype=np.float32)
    if values.size == 0:
        return values
    median = np.median(values)
    upper = np.percentile(values, 75)
    lower = np.percentile(values, 25)
    spread = max(upper - lower, float(np.std(values)) * 0.5, 1e-6)
    normalized = (values - median) / spread
    return np.clip(normalized, -3.5, 4.0)


def _absolute_presence_score(values: np.ndarray, floor: float, scale: float) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if values.size == 0:
        return values
    normalized = (values - floor) / max(scale, 1e-6)
    return np.clip(normalized, 0.0, 1.5)


def _has_absolute_action_support(
    killfeed_score: float,
    audio_score: float,
    hud_score: float,
    center_score: float,
    visual_score: float,
) -> bool:
    if killfeed_score >= 0.12:
        return True
    if audio_score >= 0.20 and max(hud_score, center_score, visual_score) >= 0.08:
        return True
    return False


def _has_eklipse_style_big_play_support(
    killfeed_score: float,
    killfeed_burst_score: float,
    audio_score: float,
    center_score: float,
    center_burst_score: float,
    hud_score: float,
    visual_score: float,
    scene_score: float,
) -> bool:
    if killfeed_score < 0.18:
        return False
    if killfeed_burst_score < 0.08 and center_burst_score < 0.08:
        return False
    if killfeed_burst_score >= 0.16 and max(center_score, center_burst_score, visual_score) >= 0.08:
        return True
    if killfeed_score >= 0.32 and center_burst_score >= 0.10:
        return True
    if killfeed_burst_score >= 0.22 and max(audio_score, scene_score) >= 0.12 and center_score >= 0.08:
        return True
    return False


def _source_has_meaningful_gameplay(timeline: AnalysisTimeline) -> bool:
    visual = np.array(timeline.visual_motion, dtype=np.float32)
    killfeed = np.array(timeline.killfeed_motion, dtype=np.float32)
    hud = np.array(timeline.hud_motion, dtype=np.float32)
    center = np.array(timeline.center_motion, dtype=np.float32)
    audio_flux = np.array(timeline.audio_flux, dtype=np.float32)
    killfeed_burst = _local_burst_signal(killfeed, radius=4)

    return bool(
        np.percentile(visual, 95) >= 0.012
        and np.percentile(center, 95) >= 0.012
        and (
            np.percentile(killfeed_burst, 95) >= 0.014
            or (
                np.percentile(killfeed, 95) >= 0.028
                and np.percentile(center, 95) >= 0.050
            )
            or (
                np.percentile(audio_flux, 95) >= 0.080
                and np.percentile(killfeed, 90) >= 0.016
                and np.percentile(hud, 95) >= 0.012
            )
        )
    )


def _big_play_segment_bounds(
    peak_index: int,
    times: np.ndarray,
    activity_signal: np.ndarray,
    duration: float,
    config: AnalysisConfig,
) -> Tuple[float, float]:
    peak_time = float(times[peak_index])
    floor = max(0.42, float(activity_signal[peak_index]) * 0.46)
    left = peak_index
    right = peak_index

    while left > 0 and (peak_time - float(times[left - 1])) <= 1.35 and float(activity_signal[left - 1]) >= floor:
        left -= 1
    while right + 1 < len(times) and (float(times[right + 1]) - peak_time) <= 2.05 and float(activity_signal[right + 1]) >= floor:
        right += 1

    start = max(0.0, float(times[left]) - 0.55)
    end = min(duration, float(times[right]) + 0.90)
    segment_duration = end - start
    max_big_play_seconds = min(config.max_segment_seconds, 4.2)
    if segment_duration > max_big_play_seconds:
        half = max_big_play_seconds / 2.0
        start = max(0.0, peak_time - half * 0.9)
        end = min(duration, peak_time + half * 1.1)
    return _clamp_segment(start, end, duration, config)


def _local_burst_signal(values: np.ndarray, radius: int = 3) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if values.size == 0:
        return values
    burst = np.zeros_like(values)
    for index in range(values.size):
        left = max(0, index - radius)
        right = min(values.size, index + radius + 1)
        neighborhood = values[left:right]
        if neighborhood.size <= 1:
            continue
        baseline = float(np.median(np.delete(neighborhood, min(index - left, neighborhood.size - 1))))
        burst[index] = max(float(values[index]) - baseline, 0.0)
    return burst


def _smooth(values: np.ndarray, window_size: int) -> np.ndarray:
    if window_size <= 1 or values.size == 0:
        return values
    window_size = min(window_size, values.size)
    kernel = np.ones(window_size, dtype=np.float32) / window_size
    return np.convolve(values, kernel, mode="same")


def _overlap(first: Segment, second: Segment) -> float:
    intersection = max(0.0, min(first.end, second.end) - max(first.start, second.start))
    if intersection <= 0:
        return 0.0
    minimum = max(min(first.duration, second.duration), 1e-6)
    return intersection / minimum
