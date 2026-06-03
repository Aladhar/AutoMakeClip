#!/usr/bin/env python3
"""
Generate clean baseline metrics before applying any detector changes.
Reads the existing plan and selection_reasons.json.
Output: benchmarks/overwatch_eklipse_parity/review/vod_001/vod_001_baseline_metrics.json
"""
import json
from pathlib import Path

PLAN_PATH = Path("output/vod_001_local_detection.plan.json").resolve()
EXLIPSE_PATH = Path("benchmarks/overwatch_eklipse_parity/references/vod_001_eklipse_gameplay_only.json").resolve()
REVIEW_DIR = (Path("benchmarks/overwatch_eklipse_parity/review") / "vod_001").resolve()

with open(PLAN_PATH) as f:
    plan = json.load(f)
with open(EXLIPSE_PATH) as f:
    eklipse_clips = json.load(f)

local_clips = plan["segments"]

def local_anchor(lc):
    return lc.get("highlight_time") or ((lc["start"] + lc["end"]) / 2.0)

IOU_THRESHOLD = 0.50
ANCHOR_DISTANCE_THRESHOLD_SEC = 2.0

total_candidates = 511

# Count strict match clips
strict_count = 0
strict_details = []
for lc in local_clips:
    anchor = local_anchor(lc)
    for ec in eklipse_clips:
        es, ee = ec["start_sec"], ec["end_sec"]
        e_mid = (es + ee) / 2.0
        i_start = max(es, lc["start"])
        i_end = min(ee, lc["end"])
        intersection = max(0.0, i_end - i_start)
        u_start = min(es, lc["start"])
        u_end = max(ee, lc["end"])
        union = max(0.0, u_end - u_start)
        iou = intersection / union if union > 0 else 0.0
        anchor_dist = abs(e_mid - anchor)
        if iou >= IOU_THRESHOLD or anchor_dist <= ANCHOR_DISTANCE_THRESHOLD_SEC:
            strict_count += 1
            strict_details.append({
                "clip_index": local_clips.index(lc) + 1,
                "clip_start": round(lc["start"], 1),
                "clip_end": round(lc["end"], 1),
                "anchor": round(anchor, 2),
                "score": round(lc["score"], 2),
                "eklipse_label": ec.get("label", ec.get("event_type", "?")),
                "eklipse_window": [es, ee],
                "iou": round(iou, 3),
                "anchor_dist": round(anchor_dist, 1),
            })
            break

inside_count = 0
inside_details = []
for lc in local_clips:
    anchor = local_anchor(lc)
    for ec in eklipse_clips:
        es, ee = ec["start_sec"], ec["end_sec"]
        if es <= anchor <= ee:
            inside_count += 1
            inside_details.append({
                "clip_index": local_clips.index(lc) + 1,
                "anchor": round(anchor, 2),
                "score": round(lc["score"], 2),
                "eklipse_label": ec.get("label", ec.get("event_type", "?")),
                "eklipse_window": [es, ee],
            })
            break

extra_count = len(local_clips) - inside_count

extra_by_score = []
for i, lc in enumerate(local_clips):
    anchor = local_anchor(lc)
    is_inside = any(ec["start_sec"] <= anchor <= ec["end_sec"] for ec in eklipse_clips)
    if not is_inside:
        extra_by_score.append((i, lc))
extra_by_score.sort(key=lambda x: x[1]["score"], reverse=True)
top_15_extra = extra_by_score[:15]

fp_count = sum(1 for _, e in top_15_extra
               if e["end"] - e["start"] <= 2.5 and "kill-heavy" in e.get("note", ""))

eklipse_status = []
for i, ec in enumerate(eklipse_clips):
    es, ee = ec["start_sec"], ec["end_sec"]
    e_mid = (es + ee) / 2.0
    anchor_inside = any(es <= local_anchor(lc) <= ee for lc in local_clips)
    strict = False
    for lc in local_clips:
        anchor = local_anchor(lc)
        i_start = max(es, lc["start"])
        i_end = min(ee, lc["end"])
        intersection = max(0.0, i_end - i_start)
        u_start = min(es, lc["start"])
        u_end = max(ee, lc["end"])
        union = max(0.0, u_end - u_start)
        iou = intersection / union if union > 0 else 0.0
        if iou >= IOU_THRESHOLD or abs(e_mid - anchor) <= ANCHOR_DISTANCE_THRESHOLD_SEC:
            strict = True
            break
    nearest = min(local_clips, key=lambda lc: abs(local_anchor(lc) - e_mid))
    nearest_anchor = local_anchor(nearest)
    eklipse_status.append({
        "index": i + 1,
        "label": ec.get("label", ec.get("event_type", "?")),
        "window": [es, ee],
        "detected_anchor_inside": anchor_inside,
        "strict_match": strict,
        "nearest_local_index": local_clips.index(nearest) + 1,
        "nearest_local_distance": round(abs(nearest_anchor - e_mid), 1),
    })

baseline = {
    "vod": "vod_001",
    "source_duration_sec": 5022.89,
    "total_candidates_generated": total_candidates,
    "total_selected_clips": len(local_clips),
    "strict_match_clips": strict_count,
    "strict_match_details": strict_details,
    "anchor_inside_window_clips": inside_count,
    "anchor_inside_details": inside_details,
    "extra_selections": extra_count,
    "top_15_extra_total": 15,
    "top_15_extra_fp_estimate": fp_count,
    "eklipse_targets_total": len(eklipse_clips),
    "eklipse_targets_detected": sum(1 for e in eklipse_status if e["detected_anchor_inside"]),
    "eklipse_targets_strict_match": sum(1 for e in eklipse_status if e["strict_match"]),
    "eklipse_target_details": eklipse_status,
}

out_path = REVIEW_DIR / "vod_001_baseline_metrics.json"
out_path.write_text(json.dumps(baseline, indent=2))
print("Wrote: {}".format(out_path))

print()
print("=== BASELINE METRICS (before killcam rejection) ===")
print("  Total candidates generated:         {}".format(total_candidates))
print("  Total selected clips:                {}".format(len(local_clips)))
print("  Strict matches (IoU>=0.50|dist<=2s): {}".format(strict_count))
for sd in strict_details:
    print("    #{} at {}s: {} anchor_dist={:.1f}s score={}".format(
        sd["clip_index"], round(sd["anchor"], 1), sd["eklipse_label"],
        sd["anchor_dist"], sd["score"]))
print("  Anchor-inside Eklipse window:         {}".format(inside_count))
print("  Extra selections (no association):    {}".format(extra_count))
print("  Top 15 extras FP estimate:            {}/15".format(fp_count))
print("  Eklipse targets detected (anchor in):  {}/9".format(
    sum(1 for e in eklipse_status if e["detected_anchor_inside"])))
print("  Eklipse targets strict match:         {}/9".format(
    sum(1 for e in eklipse_status if e["strict_match"])))
print()
for e in eklipse_status:
    print("  #{:2d} {:25s} anchor_in={} strict={} nearest=#{} dist={}s".format(
        e["index"], e["label"],
        "Y" if e["detected_anchor_inside"] else "N",
        "Y" if e["strict_match"] else "N",
        e["nearest_local_index"], e["nearest_local_distance"]))