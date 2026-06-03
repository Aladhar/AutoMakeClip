#!/usr/bin/env python3
"""Generate visual review previews for Experiment A sustained candidate detection."""

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from automakeclip.config import AnalysisConfig
from automakeclip.types import VideoMetadata

VOD_PATH = Path("benchmarks/overwatch_eklipse_parity/downloads/vod.mp4").resolve()
REFERENCE_PATH = Path("benchmarks/overwatch_eklipse_parity/references/vod_001_eklipse_gameplay_only.json").resolve()
BASELINE_PLAN_PATH = Path("output/vod_001_repro_run_a.plan.json").resolve()
EXPERIMENT_PLAN_PATH = Path("output/vod_001_experiment_a_sustained_candidates.plan.json").resolve()
REVIEW_DIR = Path("benchmarks/overwatch_eklipse_parity/review/vod_001/experiment_a_sustained_candidates").resolve()
REVIEW_DIR.mkdir(parents=True, exist_ok=True)

CONFIG = AnalysisConfig()


def rel_path(p: Path) -> str:
    try:
        return str(p.relative_to(Path.cwd()))
    except ValueError:
        return str(p)


def fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"


def sec_filename(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}-{m:02d}-{s:02d}"


def crop_filter(roi: Tuple[float, float, float, float], width: int, height: int) -> str:
    x = int(width * roi[0])
    y = int(height * roi[1])
    w = int(width * roi[2])
    h = int(height * roi[3])
    return f"crop={w}:{h}:{x}:{y},scale=640:-2"


def extract_preview(start_sec: float, duration_sec: float, output_path: Path, max_width: int = 640) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", str(VOD_PATH),
        "-t", str(duration_sec),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-vf", f"scale={max_width}:-2",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "28",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"WARNING: preview failed for {output_path.name}: {result.stderr.strip()[:200]}")
        return False
    return True


def extract_frames(start_sec: float, duration_sec: float, output_dir: Path, prefix: str, fps: int, crop: Optional[str] = None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    filters = [f"fps={fps}"]
    if crop:
        filters.append(crop)
    vf = ",".join(filters)
    args = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", str(VOD_PATH),
        "-t", str(duration_sec),
        "-vf", vf,
        "-q:v", "5",
        str(output_dir / f"{prefix}_%03d.png"),
    ]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"WARNING: frame extraction failed for {prefix}: {result.stderr.strip()[:200]}")


def choose_closest_segment(window_start: float, window_end: float, segments: List[Dict]) -> Optional[Dict]:
    window_center = (window_start + window_end) / 2.0
    if not segments:
        return None
    return min(segments, key=lambda seg: abs((seg.get("highlight_time") or ((seg["start"] + seg["end"]) / 2.0)) - window_center))


def candidate_anchor(seg: Dict) -> float:
    return seg.get("highlight_time") if seg.get("highlight_time") is not None else (seg["start"] + seg["end"]) / 2.0


def run() -> int:
    if not VOD_PATH.exists():
        print(f"VOD not found: {VOD_PATH}")
        return 1

    baseline_plan = json.loads(BASELINE_PLAN_PATH.read_text(encoding="utf-8"))
    experiment_plan = json.loads(EXPERIMENT_PLAN_PATH.read_text(encoding="utf-8"))
    refs = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))

    # Core missed kill-based windows.
    for ref_window in refs:
        if ref_window["event_group"] not in {"triple_kill_001", "double_kill_001", "multi_kill_001", "multi_kill_002"}:
            continue
        label = ref_window["label"].replace(" ", "_").replace("-", "_")
        prefix = f"core_{ref_window['event_group']}_{label}_{sec_filename(ref_window['start_sec'])}_to_{sec_filename(ref_window['end_sec'])}"
        ref_dir = REVIEW_DIR / prefix
        ref_dir.mkdir(parents=True, exist_ok=True)

        extract_preview(ref_window["start_sec"], ref_window["end_sec"] - ref_window["start_sec"], ref_dir / f"{prefix}_ref.mp4")

        baseline_segment = choose_closest_segment(ref_window["start_sec"], ref_window["end_sec"], baseline_plan["segments"])
        if baseline_segment:
            extract_preview(baseline_segment["start"], baseline_segment["end"] - baseline_segment["start"], ref_dir / f"{prefix}_baseline_closest.mp4")
        experiment_segment = choose_closest_segment(ref_window["start_sec"], ref_window["end_sec"], experiment_plan["segments"])
        if experiment_segment:
            extract_preview(experiment_segment["start"], experiment_segment["end"] - experiment_segment["start"], ref_dir / f"{prefix}_experiment_closest.mp4")

        extract_frames(ref_window["start_sec"], ref_window["end_sec"] - ref_window["start_sec"], ref_dir / ref_dir.name, "context_1fps", fps=1)
        action_center = ref_window["start_sec"] + min(6.0, (ref_window["end_sec"] - ref_window["start_sec"]) / 2.0)
        extract_frames(max(0.0, action_center - 3.0), min(6.0, ref_window["end_sec"] - ref_window["start_sec"]), ref_dir / ref_dir.name, "action_4fps", fps=4)

        width = 2560
        height = 1440
        killfeed_crop = crop_filter(CONFIG.killfeed_roi, width, height)
        hud_crop = crop_filter(CONFIG.hud_roi, width, height)
        extract_frames(ref_window["start_sec"], ref_window["end_sec"] - ref_window["start_sec"], ref_dir / ref_dir.name, "killfeed_1fps", fps=1, crop=killfeed_crop)
        extract_frames(ref_window["start_sec"], ref_window["end_sec"] - ref_window["start_sec"], ref_dir / ref_dir.name, "hud_1fps", fps=1, crop=hud_crop)

    # Selected sustained candidates in experiment A.
    sustained_segments = [seg for seg in experiment_plan["segments"] if seg.get("candidate_type") in {"sustained_multikill", "sustained_teamfight"}]
    for index, seg in enumerate(sustained_segments, start=1):
        seg_dir = REVIEW_DIR / f"sustained_candidate_{index:02d}_{sec_filename(seg['start'])}_to_{sec_filename(seg['end'])}"
        seg_dir.mkdir(parents=True, exist_ok=True)
        extract_preview(seg["start"], seg["end"] - seg["start"], seg_dir / f"candidate_{index:02d}.mp4")
        extract_frames(seg["start"], seg["end"] - seg["start"], seg_dir / seg_dir.name, "context_1fps", fps=1)
        extract_frames(seg["start"], seg["end"] - seg["start"], seg_dir / seg_dir.name, "action_4fps", fps=4)
        extract_frames(seg["start"], seg["end"] - seg["start"], seg_dir / seg_dir.name, "killfeed_1fps", fps=1, crop=crop_filter(CONFIG.killfeed_roi, 2560, 1440))
        extract_frames(seg["start"], seg["end"] - seg["start"], seg_dir / seg_dir.name, "hud_1fps", fps=1, crop=crop_filter(CONFIG.hud_roi, 2560, 1440))

    # Top 15 extras in experiment A.
    ref_windows = refs
    extras = [seg for seg in experiment_plan["segments"] if not any(w["start_sec"] <= candidate_anchor(seg) <= w["end_sec"] for w in ref_windows)]
    extras = sorted(extras, key=lambda item: float(item["score"]), reverse=True)[:15]
    extras_dir = REVIEW_DIR / "top_15_extras"
    extras_dir.mkdir(parents=True, exist_ok=True)
    for index, seg in enumerate(extras, start=1):
        segment_dir = extras_dir / f"extra_{index:02d}_{sec_filename(seg['start'])}_to_{sec_filename(seg['end'])}"
        segment_dir.mkdir(parents=True, exist_ok=True)
        extract_preview(seg["start"], seg["end"] - seg["start"], segment_dir / f"extra_{index:02d}.mp4")
        extract_frames(seg["start"], seg["end"] - seg["start"], segment_dir / segment_dir.name, "context_1fps", fps=1)
        extract_frames(seg["start"], seg["end"] - seg["start"], segment_dir / segment_dir.name, "action_4fps", fps=4)
        extract_frames(seg["start"], seg["end"] - seg["start"], segment_dir / segment_dir.name, "killfeed_1fps", fps=1, crop=crop_filter(CONFIG.killfeed_roi, 2560, 1440))

    print(f"Generated review artifacts under {REVIEW_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
