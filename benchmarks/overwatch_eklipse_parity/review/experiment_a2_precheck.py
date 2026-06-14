#!/usr/bin/env python3
import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from automakeclip.analysis import (
    _absolute_presence_score,
    _extract_sustained_multikill_segments,
    _local_burst_signal,
    _robust_normalize,
    analyze_gameplay,
)
from automakeclip.config import AnalysisConfig
from automakeclip.ffmpeg import probe_video

VOD_PATH = Path("benchmarks/overwatch_eklipse_parity/downloads/vod.mp4").resolve()
REFERENCE_PATH = Path("benchmarks/overwatch_eklipse_parity/references/vod_001_eklipse_gameplay_only.json").resolve()
EXPERIMENT_PLAN_PATH = Path("output/vod_001_experiment_a_sustained_candidates.plan.json").resolve()
PRECHECK_DIR = Path("benchmarks/overwatch_eklipse_parity/review/vod_001/experiment_a_sustained_candidates/a2_precheck").resolve()
PRECHECK_DIR.mkdir(parents=True, exist_ok=True)
REPORT_JSON_PATH = PRECHECK_DIR / "a2_precheck_visual_and_signal_report.json"
REPORT_MD_PATH = PRECHECK_DIR / "a2_precheck_visual_and_signal_report.md"

WINDOW_KEYS = ["triple_kill_001", "double_kill_001", "multi_kill_001", "multi_kill_002"]


def _is_local_peak(values: np.ndarray, index: int) -> bool:
    left = values[index - 1] if index > 0 else -np.inf
    right = values[index + 1] if index + 1 < len(values) else -np.inf
    return values[index] >= left and values[index] >= right


def _best_window_peak(indices: List[int], sustained_signal: np.ndarray) -> Optional[int]:
    if not indices:
        return None
    return int(max(indices, key=lambda i: float(sustained_signal[i])))


def _window_gate_reason(index: int, sustained_signal: np.ndarray, gameplay: np.ndarray, threshold: float, floor: float) -> str:
    if sustained_signal[index] < threshold:
        return "threshold"
    if gameplay[index] < floor:
        return "gameplay_floor"
    if not _is_local_peak(sustained_signal, index):
        return "not_local_peak"
    return "unspecified"


def _candidate_window_metrics(index: int, times: np.ndarray, positive_killfeed: np.ndarray, positive_killfeed_burst: np.ndarray, positive_center: np.ndarray, positive_audio: np.ndarray, gameplay: np.ndarray, sustained_signal: np.ndarray, config: AnalysisConfig) -> Dict:
    peak_time = float(times[index])
    window_half = config.sustained_candidate_window_seconds / 2.0
    window_start = max(0.0, peak_time - window_half)
    window_end = min(times[-1], peak_time + window_half)
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
    strong_peaks = int(np.sum(window_sustained >= np.percentile(sustained_signal, config.sustained_candidate_peak_threshold_percentile) * 0.72))
    sustained_ratio = float(np.mean(window_sustained >= np.percentile(sustained_signal, config.sustained_candidate_peak_threshold_percentile) * 0.40))
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


def build_window_report(window_def: dict, times: np.ndarray, sustained_signal: np.ndarray, generic_signal: np.ndarray, killfeed: np.ndarray, killfeed_burst: np.ndarray, center: np.ndarray, center_burst: np.ndarray, audio_flux: np.ndarray, gameplay: np.ndarray, sustained_threshold: float, gameplay_floor: float, candidate_indices: List[int], accepted_indices: List[int], config: AnalysisConfig) -> Dict:
    start, end = float(window_def["start_sec"]), float(window_def["end_sec"])
    indices = [int(i) for i, t in enumerate(times) if t >= start and t <= end]
    max_sustained = float(np.max(sustained_signal[indices])) if indices else 0.0
    sustained_peaks = [i for i in indices if sustained_signal[i] >= sustained_threshold * 0.72 and _is_local_peak(sustained_signal, i)]
    best_index = _best_window_peak(indices, sustained_signal)
    best_reason = _window_gate_reason(best_index, sustained_signal, gameplay, sustained_threshold, gameplay_floor) if best_index is not None else "no_peak"
    return {
        "event_group": window_def["event_group"],
        "label": window_def["label"],
        "start_sec": start,
        "end_sec": end,
        "max_sustained_signal": max_sustained,
        "num_sustained_peaks": len(sustained_peaks),
        "best_peak_index": best_index,
        "best_peak_time": float(times[best_index]) if best_index is not None else None,
        "best_gate_reason": best_reason,
        "all_candidate_indices": [int(i) for i in indices if i in candidate_indices],
        "accepted_candidate_indices": [int(i) for i in indices if i in accepted_indices],
        "timeseries": [
            {
                "time": float(times[i]),
                "killfeed_motion": float(killfeed[i]),
                "killfeed_burst": float(killfeed_burst[i]),
                "center_motion": float(center[i]),
                "center_burst": float(center_burst[i]),
                "audio_flux": float(audio_flux[i]),
                "gameplay_confidence": float(gameplay[i]),
                "generic_signal": float(generic_signal[i]),
                "sustained_signal": float(sustained_signal[i]),
                "passes_threshold": bool(float(sustained_signal[i]) >= sustained_threshold),
                "passes_gameplay_floor": bool(float(gameplay[i]) >= gameplay_floor),
                "is_local_peak": bool(_is_local_peak(sustained_signal, i)),
            }
            for i in indices
        ],
    }


def run() -> int:
    if not VOD_PATH.exists():
        print(f"VOD not found: {VOD_PATH}")
        return 1

    metadata = probe_video(VOD_PATH)
    config = AnalysisConfig()
    config.enable_sustained_multikill_candidates = True
    timeline = analyze_gameplay(VOD_PATH, metadata, config)

    times = np.array(timeline.times, dtype=np.float32)
    killfeed = np.array(timeline.killfeed_motion, dtype=np.float32)
    center = np.array(timeline.center_motion, dtype=np.float32)
    audio_flux = np.array(timeline.audio_flux, dtype=np.float32)
    gameplay = np.array(timeline.gameplay_confidence, dtype=np.float32)
    scores = np.array(timeline.scores, dtype=np.float32)

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
        + 0.12 * positive_center_burst
        + 0.10 * positive_audio
        + 0.08 * positive_scores
    )
    sustained_threshold = float(np.percentile(sustained_signal, config.sustained_candidate_peak_threshold_percentile))
    gameplay_floor = float(np.percentile(gameplay, config.sustained_candidate_min_gameplay_percentile))

    candidate_indices: List[int] = []
    for index, value in enumerate(sustained_signal):
        if value < sustained_threshold or gameplay[index] < gameplay_floor:
            continue
        left = sustained_signal[index - 1] if index > 0 else -np.inf
        right = sustained_signal[index + 1] if index + 1 < len(sustained_signal) else -np.inf
        if value < left or value < right:
            continue
        candidate_indices.append(index)

    accepted_indices: List[int] = []
    for index in sorted(candidate_indices, key=lambda item: sustained_signal[item], reverse=True):
        peak_time = float(times[index])
        if any(abs(peak_time - float(times[accepted])) < config.sustained_candidate_spacing_seconds for accepted in accepted_indices):
            continue
        accepted_indices.append(index)

    generic_signal = (
        0.34 * positive_killfeed
        + 0.22 * np.clip(_robust_normalize(_local_burst_signal(killfeed, radius=3)), 0.0, None)
        + 0.15 * positive_center
        + 0.11 * positive_center_burst
        + 0.10 * positive_audio
        + 0.08 * positive_scores
    )

    windows = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    windows = [w for w in windows if w["event_group"] in WINDOW_KEYS]
    report = {
        "video_duration": float(metadata.duration),
        "analysis_fps": config.analysis_fps,
        "sustained_threshold": sustained_threshold,
        "gameplay_floor": gameplay_floor,
        "sustained_signal_count": int(len(sustained_signal)),
        "candidate_indices": [int(i) for i in candidate_indices],
        "accepted_indices": [int(i) for i in accepted_indices],
        "accepted_times": [float(times[i]) for i in accepted_indices],
        "window_reports": [],
        "sustained_candidates": [],
        "summary": {},
    }

    for window_def in windows:
        report["window_reports"].append(
            build_window_report(
                window_def,
                times,
                sustained_signal,
                generic_signal,
                killfeed,
                np.clip(_robust_normalize(_local_burst_signal(killfeed, radius=3)), 0.0, None),
                center,
                np.clip(_robust_normalize(_local_burst_signal(center, radius=3)), 0.0, None),
                audio_flux,
                gameplay,
                sustained_threshold,
                gameplay_floor,
                candidate_indices,
                accepted_indices,
                config,
            )
        )

    sustained_segments = _extract_sustained_multikill_segments(timeline, metadata, config)
    for seg in sustained_segments:
        report["sustained_candidates"].append(
            {
                "start": float(seg.start),
                "end": float(seg.end),
                "score": float(seg.score),
                "candidate_type": seg.candidate_type,
                "highlight_time": float(seg.highlight_time) if seg.highlight_time is not None else None,
                "note": seg.note,
            }
        )

    report["summary"] = {
        "sustained_candidates_generated": len(sustained_segments),
        "sustained_candidates_selected": 0,
        "core_targets_with_candidates": sum(
            1
            for w in report["window_reports"]
            if any(
                seg["start"] <= w["best_peak_time"] <= seg["end"]
                for seg in report["sustained_candidates"]
                if w["best_peak_time"] is not None
            )
        ),
        "windows": [
            {
                "event_group": w["event_group"],
                "max_sustained_signal": w["max_sustained_signal"],
                "num_sustained_peaks": w["num_sustained_peaks"],
                "best_gate_reason": w["best_gate_reason"],
            }
            for w in report["window_reports"]
        ],
    }

    REPORT_JSON_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# A2 Precheck Visual and Signal Report",
        "",
        "## Summary",
        f"- sustained_threshold: {sustained_threshold:.4f}",
        f"- gameplay_floor: {gameplay_floor:.4f}",
        f"- sustained candidates generated by Experiment A branch: {len(sustained_segments)}",
        "",
        "## Window Summaries",
        "",
        "| Window | Max Sustained Signal | Number of Sustained Peaks | Gate That Prevented Candidate | Visually Valuable? | What Signal Is Missing? |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for w in report["window_reports"]:
        missing = "unknown"
        gate = w["best_gate_reason"]
        if gate == "threshold":
            missing = "sustained_signal too low"
        elif gate == "gameplay_floor":
            missing = "gameplay support"
        elif gate == "not_local_peak":
            missing = "not a strong local sustained peak"
        elif gate == "no_peak":
            missing = "no candidate peak in window"
        lines.append(
            f"| {w['event_group']} | {w['max_sustained_signal']:.4f} | {w['num_sustained_peaks']} | {gate} | review assets generated | {missing} |"
        )

    lines.append("\n## Sustained Candidate Details")
    for seg in report["sustained_candidates"]:
        lines.append(f"- {seg['candidate_type']} {seg['start']:.3f}-{seg['end']:.3f} score={seg['score']:.3f} note={seg['note']}")

    REPORT_MD_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(f"Generated A2 precheck report at {REPORT_MD_PATH} and {REPORT_JSON_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
