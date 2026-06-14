#!/usr/bin/env python3
import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from automakeclip.analysis import (
    _extract_sustained_multikill_segments,
    _local_burst_signal,
    _robust_normalize,
    analyze_gameplay,
)
from automakeclip.config import AnalysisConfig
from automakeclip.ffmpeg import probe_video

VOD_PATH = Path("benchmarks/overwatch_eklipse_parity/downloads/vod.mp4").resolve()
REFERENCE_PATH = Path("benchmarks/overwatch_eklipse_parity/references/vod_001_eklipse_gameplay_only.json").resolve()
PRECHECK_DIR = Path("benchmarks/overwatch_eklipse_parity/review/vod_001/experiment_a_sustained_candidates/a2_precheck").resolve()
PRECHECK_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_JSON = PRECHECK_DIR / "a2_exact_gate_trace.json"
OUTPUT_MD = PRECHECK_DIR / "a2_exact_gate_trace.md"

WINDOW_KEYS = ["triple_kill_001", "double_kill_001", "multi_kill_001", "multi_kill_002"]
GATE_ORDER = [
    "threshold",
    "gameplay_floor",
    "not_local_peak",
    "avg_killfeed",
    "avg_center",
    "avg_audio",
    "burst_count",
    "strong_peaks",
    "sustained_ratio",
    "accepted",
]


def _is_local_peak(values: np.ndarray, index: int) -> bool:
    left = values[index - 1] if index > 0 else -np.inf
    right = values[index + 1] if index + 1 < len(values) else -np.inf
    return values[index] >= left and values[index] >= right


def _window_metrics(
    index: int,
    times: np.ndarray,
    sustained_signal: np.ndarray,
    positive_killfeed: np.ndarray,
    positive_killfeed_burst: np.ndarray,
    positive_center: np.ndarray,
    positive_audio: np.ndarray,
    gameplay: np.ndarray,
    threshold: float,
    config: AnalysisConfig,
) -> Dict[str, float]:
    peak_time = float(times[index])
    half = config.sustained_candidate_window_seconds / 2.0
    window_start = max(0.0, peak_time - half)
    window_end = min(times[-1], peak_time + half)
    left_index = int(np.searchsorted(times, window_start, side="left"))
    right_index = int(np.searchsorted(times, window_end, side="right"))
    if right_index <= left_index:
        return {}
    window_killfeed = positive_killfeed[left_index:right_index]
    window_center = positive_center[left_index:right_index]
    window_audio = positive_audio[left_index:right_index]
    window_gameplay = gameplay[left_index:right_index]
    window_sustained = sustained_signal[left_index:right_index]
    burst_count = int(np.sum(positive_killfeed_burst[left_index:right_index] >= 0.18))
    strong_peaks = int(np.sum(window_sustained >= threshold * 0.72))
    sustained_ratio = float(np.mean(window_sustained >= threshold * 0.40))
    return {
        "window_start": float(window_start),
        "window_end": float(window_end),
        "avg_killfeed": float(np.mean(window_killfeed)) if window_killfeed.size else 0.0,
        "avg_center": float(np.mean(window_center)) if window_center.size else 0.0,
        "avg_audio": float(np.mean(window_audio)) if window_audio.size else 0.0,
        "avg_gameplay": float(np.mean(window_gameplay)) if window_gameplay.size else 0.0,
        "burst_count": burst_count,
        "strong_peaks": strong_peaks,
        "sustained_ratio": sustained_ratio,
    }


def _classify_peak(
    index: int,
    sustained_signal: np.ndarray,
    gameplay: np.ndarray,
    threshold: float,
    gameplay_floor: float,
    config: AnalysisConfig,
    window_metrics: Dict[str, float],
) -> Dict[str, object]:
    value = float(sustained_signal[index])
    gameplay_value = float(gameplay[index])
    local_peak = _is_local_peak(sustained_signal, index)
    reason = None
    if value < threshold:
        reason = "threshold"
    elif gameplay_value < gameplay_floor:
        reason = "gameplay_floor"
    elif not local_peak:
        reason = "not_local_peak"
    elif window_metrics["avg_killfeed"] < config.sustained_candidate_min_average_killfeed:
        reason = "avg_killfeed"
    elif window_metrics["avg_center"] < config.sustained_candidate_min_average_center:
        reason = "avg_center"
    elif window_metrics["avg_audio"] < config.sustained_candidate_min_average_audio:
        reason = "avg_audio"
    elif window_metrics["burst_count"] < config.sustained_candidate_min_burst_events:
        reason = "burst_count"
    elif window_metrics["strong_peaks"] < config.sustained_candidate_min_peak_count:
        reason = "strong_peaks"
    elif window_metrics["sustained_ratio"] < 0.28:
        reason = "sustained_ratio"
    else:
        reason = "accepted"

    return {
        "sustained_signal": value,
        "gameplay_value": gameplay_value,
        "threshold": threshold,
        "gameplay_floor": gameplay_floor,
        "passes_threshold": bool(value >= threshold),
        "passes_gameplay_floor": bool(gameplay_value >= gameplay_floor),
        "is_local_peak": bool(local_peak),
        "window_metrics": window_metrics,
        "final_gate": reason,
    }


def _candidate_presence(index: int, accepted_indices: List[int]) -> bool:
    return index in accepted_indices


def _peak_distance(peak_time: float, start: float, end: float) -> float:
    if peak_time < start:
        return start - peak_time
    if peak_time > end:
        return peak_time - end
    return 0.0


def run() -> int:
    if not VOD_PATH.exists():
        print(f"VOD not found: {VOD_PATH}")
        return 1

    metadata = probe_video(VOD_PATH)
    config = AnalysisConfig()
    config.enable_sustained_multikill_candidates = True
    timeline = analyze_gameplay(VOD_PATH, metadata, config)

    times = np.array(timeline.times, dtype=np.float32)
    scores = np.array(timeline.scores, dtype=np.float32)
    gameplay = np.array(timeline.gameplay_confidence, dtype=np.float32)
    killfeed = np.array(timeline.killfeed_motion, dtype=np.float32)
    center = np.array(timeline.center_motion, dtype=np.float32)
    audio_flux = np.array(timeline.audio_flux, dtype=np.float32)

    positive_killfeed = np.clip(_robust_normalize(killfeed), 0.0, None)
    positive_killfeed_burst = np.clip(_robust_normalize(_local_burst_signal(killfeed, radius=3)), 0.0, None)
    positive_center = np.clip(_robust_normalize(center), 0.0, None)
    positive_center_burst = np.clip(_robust_normalize(_local_burst_signal(center, radius=3)), 0.0, None)
    positive_audio = np.clip(_robust_normalize(audio_flux), 0.0, None)
    positive_scores = np.clip(_robust_normalize(scores), 0.0, None)

    sustained_signal = (
        0.22 * positive_killfeed
        + 0.18 * positive_killfeed_burst
        + 0.18 * positive_center
        + 0.12 * np.clip(_robust_normalize(_local_burst_signal(center, radius=3)), 0.0, None)
        + 0.10 * positive_audio
        + 0.08 * positive_scores
    )
    threshold = float(np.percentile(sustained_signal, config.sustained_candidate_peak_threshold_percentile))
    gameplay_floor = float(np.percentile(gameplay, config.sustained_candidate_min_gameplay_percentile))
    sustained_segments = _extract_sustained_multikill_segments(timeline, None, config)

    accepted_indices = []
    for segment in sustained_segments:
        if segment.highlight_time is None:
            continue
        nearest = int(np.argmin(np.abs(times - float(segment.highlight_time))))
        if abs(float(times[nearest]) - float(segment.highlight_time)) <= 0.5:
            accepted_indices.append(nearest)

    windows = [w for w in json.loads(REFERENCE_PATH.read_text(encoding="utf-8")) if w["event_group"] in WINDOW_KEYS]

    trace: Dict[str, object] = {
        "analysis_config": config.__dict__,
        "sustained_signal_threshold": threshold,
        "sustained_gameplay_floor": gameplay_floor,
        "sustained_segments": [
            {
                "start": float(seg.start),
                "end": float(seg.end),
                "highlight_time": float(seg.highlight_time) if seg.highlight_time is not None else None,
                "score": float(seg.score),
                "candidate_type": seg.candidate_type,
                "label": seg.label,
                "note": seg.note,
            }
            for seg in sustained_segments
        ],
        "windows": [],
    }

    for window in windows:
        start = float(window["start_sec"])
        end = float(window["end_sec"])
        window_center = (start + end) / 2.0
        candidate_peaks = []
        window_local_indices = [
            int(i)
            for i, t in enumerate(times)
            if t >= max(0.0, start - 5.0) and t <= min(times[-1], end + 5.0)
        ]
        local_peak_indices = [i for i in window_local_indices if _is_local_peak(sustained_signal, i)]
        selected_peaks = sorted(
            local_peak_indices,
            key=lambda i: (-float(sustained_signal[i]), abs(float(times[i]) - window_center)),
        )
        for index in selected_peaks[:8]:
            peak_time = float(times[index])
            metrics = _window_metrics(
                index,
                times,
                sustained_signal,
                positive_killfeed,
                positive_killfeed_burst,
                positive_center,
                positive_audio,
                gameplay,
                threshold,
                config,
            )
            classify = _classify_peak(
                index,
                sustained_signal,
                gameplay,
                threshold,
                gameplay_floor,
                config,
                metrics,
            )
            candidate_peaks.append(
                {
                    "index": index,
                    "peak_time": peak_time,
                    "distance_to_window": _peak_distance(peak_time, start, end),
                    "in_window": start <= peak_time <= end,
                    "sustained_signal": float(sustained_signal[index]),
                    "gameplay_value": float(gameplay[index]),
                    "generic_signal": float(
                        0.34 * positive_killfeed[index]
                        + 0.22 * positive_killfeed_burst[index]
                        + 0.15 * positive_center[index]
                        + 0.11 * positive_center_burst[index]
                        + 0.10 * positive_audio[index]
                        + 0.08 * positive_scores[index]
                    ),
                    "window_metrics": metrics,
                    "final_gate": classify["final_gate"],
                    "passes_threshold": classify["passes_threshold"],
                    "passes_gameplay_floor": classify["passes_gameplay_floor"],
                    "is_local_peak": classify["is_local_peak"],
                    "final_candidate_created": _candidate_presence(index, accepted_indices),
                }
            )

        gameplay_series = [
            {"time": float(times[i]), "gameplay": float(gameplay[i])}
            for i in range(len(times))
            if times[i] >= start and times[i] <= end
        ]
        trace["windows"].append(
            {
                "event_group": window["event_group"],
                "label": window["label"],
                "start_sec": start,
                "end_sec": end,
                "best_peak": candidate_peaks[0] if candidate_peaks else None,
                "candidate_peaks": candidate_peaks,
                "playback_times": gameplay_series,
                "highest_gameplay": {
                    "value": float(max(gameplay_series, key=lambda item: item["gameplay"])["gameplay"]) if gameplay_series else 0.0,
                    "time": float(max(gameplay_series, key=lambda item: item["gameplay"])["time"]) if gameplay_series else None,
                },
                "visual_support": "unclear_from_visuals",
            }
        )

    OUTPUT_JSON.write_text(json.dumps(trace, indent=2), encoding="utf-8")

    lines = [
        "# A2 Exact Gate Trace",
        "",
        "## Summary",
        f"- Sustained signal threshold: {threshold:.4f}",
        f"- Gameplay floor: {gameplay_floor:.4f}",
        f"- Sustained candidate segments produced by current generator: {len(sustained_segments)}",
        f"- Sustained candidates selected into final plan: {len(accepted_indices)}",
        "",
        "## Core target gate trace",
        "",
        "| Target | Peak Time | In Window | Sustained | Gameplay | Gameplay Floor | Final Gate | Final Candidate Created | Distance to Window |",
        "| --- | ---: | --- | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for window in trace["windows"]:
        if not window["candidate_peaks"]:
            lines.append(
                f"| {window['event_group']} | N/A | N/A | N/A | N/A | N/A | no_peak | false | N/A |"
            )
            continue
        for peak in window["candidate_peaks"]:
            lines.append(
                f"| {window['event_group']} | {peak['peak_time']:.3f} | {str(peak['in_window']).lower()} | {peak['sustained_signal']:.4f} | {peak['gameplay_value']:.4f} | {gameplay_floor:.4f} | {peak['final_gate']} | {str(peak['final_candidate_created']).lower()} | {peak['distance_to_window']:.3f} |"
            )
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Generated exact gate trace: {OUTPUT_MD} and {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
