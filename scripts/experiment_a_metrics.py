#!/usr/bin/env python3
"""Compute benchmark metrics for Experiment A and write a JSON/Markdown report."""

import json
from pathlib import Path
from typing import Any, Dict, List

REF_PATH = Path("benchmarks/overwatch_eklipse_parity/references/vod_001_eklipse_gameplay_only.json")
BASELINE_PLAN_PATH = Path("output/vod_001_repro_run_a.plan.json")
EXPERIMENT_PLAN_PATH = Path("output/vod_001_experiment_a_sustained_candidates.plan.json")
REPORT_JSON = Path("benchmarks/overwatch_eklipse_parity/review/experiment_a_report.json")
REPORT_MD = Path("benchmarks/overwatch_eklipse_parity/review/experiment_a_report.md")
CORE_EVENT_GROUPS = {"triple_kill_001", "double_kill_001", "multi_kill_001", "multi_kill_002"}


def anchor_time(segment: Dict[str, Any]) -> float:
    if segment.get("highlight_time") is not None:
        return float(segment["highlight_time"])
    return (float(segment["start"]) + float(segment["end"])) / 2.0


def strict_match(window: Dict[str, Any], segment: Dict[str, Any]) -> bool:
    i_start = max(window["start_sec"], float(segment["start"]))
    i_end = min(window["end_sec"], float(segment["end"]))
    intersection = max(0.0, i_end - i_start)
    u_start = min(window["start_sec"], float(segment["start"]))
    u_end = max(window["end_sec"], float(segment["end"]))
    union = max(0.0, u_end - u_start)
    iou = intersection / union if union > 0 else 0.0
    distance = abs(((window["start_sec"] + window["end_sec"]) / 2.0) - anchor_time(segment))
    return iou >= 0.50 or distance <= 2.0


def load_plan(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compute_plan_metrics(plan: Dict[str, Any], reference_windows: List[Dict[str, Any]]) -> Dict[str, Any]:
    segments = plan["segments"]
    anchor_hits = [anchor_time(segment) for segment in segments]
    anchor_inside = sum(1 for a in anchor_hits if any(w["start_sec"] <= a <= w["end_sec"] for w in reference_windows))
    unique_detected = sum(1 for w in reference_windows if any(w["start_sec"] <= a <= w["end_sec"] for a in anchor_hits))
    strict_matches = sum(1 for w in reference_windows if any(strict_match(w, segment) for segment in segments))
    extra_count = len(segments) - anchor_inside
    core_hits = {
        w["event_group"]: any(w["start_sec"] <= a <= w["end_sec"] for a in anchor_hits)
        for w in reference_windows
        if w["event_group"] in CORE_EVENT_GROUPS
    }
    return {
        "selected_local_clips": len(segments),
        "extra_local_selections": extra_count,
        "anchor_inside_local_detections": anchor_inside,
        "unique_eklipse_targets_detected": f"{unique_detected} / {len(reference_windows)}",
        "strict_matches": strict_matches,
        "core_kill_based_targets_detected": sum(1 for hit in core_hits.values() if hit),
        "core_kill_based_hits": core_hits,
        "top_15_extras": [segment for segment in sorted(segments, key=lambda x: float(x["score"]), reverse=True) if not any(w["start_sec"] <= anchor_time(segment) <= w["end_sec"] for w in reference_windows)][:15],
    }


def render_md(report: Dict[str, Any], baseline: Dict[str, Any], experiment: Dict[str, Any], reference_windows: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("# Experiment A Report: Sustained Candidate Branch")
    lines.append("")
    lines.append("## Metric Comparison")
    lines.append("")
    lines.append("| Metric | Baseline | Experiment A | Change |")
    lines.append("| --- | --- | --- | --- |")
    for key, label in [
        ("selected_local_clips", "Selected local clips"),
        ("extra_local_selections", "Extra local selections"),
        ("anchor_inside_local_detections", "Anchor-inside local detections"),
        ("unique_eklipse_targets_detected", "Unique Eklipse targets detected"),
        ("strict_matches", "Strict matches"),
        ("core_kill_based_targets_detected", "Core kill-based targets detected"),
    ]:
        base_val = baseline[key]
        exp_val = experiment[key]
        change = exp_val if isinstance(exp_val, int) else exp_val
        if isinstance(base_val, int) and isinstance(exp_val, int):
            delta = exp_val - base_val
            change = f"{delta:+d}"
        elif isinstance(base_val, str) and isinstance(exp_val, str):
            change = ""
        lines.append(f"| {label} | {base_val} | {exp_val} | {change} |")
    lines.append("")

    lines.append("## Core Kill-Based Target Results")
    lines.append("")
    lines.append("| Eklipse Event | Window | Detected? |")
    lines.append("| --- | --- | --- |")
    for window in reference_windows:
        if window["event_group"] in CORE_EVENT_GROUPS:
            hit = experiment["core_kill_based_hits"].get(window["event_group"], False)
            lines.append(f"| {window['label']} | {window['start_sec']:.1f}–{window['end_sec']:.1f} | {'✅ Yes' if hit else '❌ No'} |")
    lines.append("")

    lines.append("## Top 15 Extras After Experiment A")
    lines.append("")
    lines.append("These top 15 extras are the highest-scoring selected segments whose anchor falls outside all Eklipse windows.")
    lines.append("")
    lines.append("| Rank | Start | End | Score | Candidate Type | Note |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for rank, segment in enumerate(experiment["top_15_extras"], start=1):
        lines.append(
            f"| {rank} | {segment['start']:.2f} | {segment['end']:.2f} | {segment['score']:.2f} | {segment.get('candidate_type', '') or ''} | {segment['note']} |"
        )
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append("- Experiment A adds sustained multi-kill/team-fight candidates only; the baseline generic candidate path is preserved.")
    lines.append("- No Eklipse timing information is used in the detection logic.")
    return "\n".join(lines)


def main() -> int:
    reference_windows = json.loads(REF_PATH.read_text(encoding="utf-8"))
    baseline_plan = load_plan(BASELINE_PLAN_PATH)
    experiment_plan = load_plan(EXPERIMENT_PLAN_PATH)
    baseline_metrics = compute_plan_metrics(baseline_plan, reference_windows)
    experiment_metrics = compute_plan_metrics(experiment_plan, reference_windows)
    report = {
        "baseline": baseline_metrics,
        "experiment": experiment_metrics,
        "reference_windows": reference_windows,
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    REPORT_MD.write_text(render_md(report, baseline_metrics, experiment_metrics, reference_windows), encoding="utf-8")
    print(f"Wrote JSON report to {REPORT_JSON}")
    print(f"Wrote Markdown report to {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
