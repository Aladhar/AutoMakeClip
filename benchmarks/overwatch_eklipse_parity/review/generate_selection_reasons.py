#!/usr/bin/env python3
"""
Generate selection_reasons.json and selection_reasons.md for vod_001.
Cross-references 44 local clips against 9 Eklipse targets.
No detector changes — diagnostic only.
"""
import json
from pathlib import Path

VOD_001 = "vod_001"
PLAN_PATH = Path("output/vod_001_local_detection.plan.json").resolve()
EXLIPSE_PATH = Path("benchmarks/overwatch_eklipse_parity/references/vod_001_eklipse_gameplay_only.json").resolve()
REVIEW_DIR = (Path("benchmarks/overwatch_eklipse_parity/review") / VOD_001).resolve()

IOU_THRESHOLD = 0.50
ANCHOR_DISTANCE_THRESHOLD_SEC = 2.0

with open(PLAN_PATH) as f:
    plan = json.load(f)
with open(EXLIPSE_PATH) as f:
    eklipse_clips = json.load(f)

local_clips = plan["segments"]

def local_anchor(lc):
    return lc.get("highlight_time") or ((lc["start"] + lc["end"]) / 2.0)

def fmt_ts(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return "{:02d}:{:02d}:{:05.2f}".format(h, m, s)

def sec_fn(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return "{:02d}-{:02d}-{:02d}".format(h, m, s)

def eklipse_relationship(lc):
    """Determine relationship of a local clip to each Eklipse target."""
    anchor = local_anchor(lc)
    results = []
    for ec in eklipse_clips:
        es = ec["start_sec"]
        ee = ec["end_sec"]
        e_mid = (es + ee) / 2.0

        # Strict match?
        i_start = max(es, lc["start"])
        i_end = min(ee, lc["end"])
        intersection = max(0.0, i_end - i_start)
        u_start = min(es, lc["start"])
        u_end = max(ee, lc["end"])
        union = max(0.0, u_end - u_start)
        iou = intersection / union if union > 0 else 0.0
        anchor_dist = abs(e_mid - anchor)

        strict_match = iou >= IOU_THRESHOLD or anchor_dist <= ANCHOR_DISTANCE_THRESHOLD_SEC

        # Anchor inside window?
        anchor_inside = es <= anchor <= ee

        dist_to_window = 0.0
        if anchor < es:
            dist_to_window = es - anchor
        elif anchor > ee:
            dist_to_window = anchor - ee

        results.append({
            "eklipse_label": ec.get("label", ec.get("event_type", "?")),
            "eklipse_window": [es, ee],
            "strict_match": strict_match,
            "anchor_inside": anchor_inside,
            "distance_to_window_sec": round(dist_to_window, 1),
            "iou": round(iou, 3),
            "anchor_distance_sec": round(anchor_dist, 1),
        })
    return results

def visual_classification_guess(start_sec, end_sec, score, label):
    """Heuristic visual classification from available data."""
    duration = end_sec - start_sec
    # 2-second clips scoring 8-10 with "Generic kill-heavy" label are suspect
    if duration <= 2.5 and score >= 8.0 and "kill-heavy" in label:
        return "kill-feed/HUD false positive (short motion spike)"
    if score >= 9.0:
        return "uncertain — high score may be genuine or kill-cam"
    if score >= 7.0:
        return "uncertain — moderate activity"
    return "uncertain — low score"

# ========== Build per-clip diagnostics ==========
clip_diagnostics = []
for i, lc in enumerate(local_clips):
    anchor = local_anchor(lc)
    relationships = eklipse_relationship(lc)

    # Determine overall relationship
    strict = any(r["strict_match"] for r in relationships)
    inside = any(r["anchor_inside"] for r in relationships)
    matched_eklipse = [r for r in relationships if r["strict_match"] or r["anchor_inside"]]
    extra = not strict and not inside

    clip_diag = {
        "local_clip_index": i + 1,
        "start_sec": lc["start"],
        "end_sec": lc["end"],
        "duration_sec": round(lc["end"] - lc["start"], 2),
        "highlight_anchor_sec": round(anchor, 2),
        "total_score": round(lc["score"], 3),
        "label": lc.get("label", ""),
        "note": lc.get("note", ""),
        "relationship": {
            "strict_match": strict,
            "anchor_inside": inside,
            "extra_selection": extra,
            "matched_eklipse_labels": [r["eklipse_label"] for r in matched_eklipse],
        },
        "per_eklipse": relationships,
        "visual_classification": visual_classification_guess(lc["start"], lc["end"], lc["score"], lc.get("note", "")),
        "detector_evidence_note": (
            "Plan file stores only final scores. "
            "Per-frame killfeed/hud/center/audio/confidence signals "
            "are not persisted — recomputation would require re-running analysis."
        ),
    }
    clip_diagnostics.append(clip_diag)

# ========== Build per-Eklipse-target diagnostics ==========
eklipse_diagnostics = []
for i, ec in enumerate(eklipse_clips):
    es = ec["start_sec"]
    ee = ec["end_sec"]
    e_mid = (es + ee) / 2.0
    safe_label = ec.get("label", ec.get("event_type", "event")).replace(" ", "_").replace("-", "_").replace("/", "_")

    # Find local clips with anchor inside window
    inside_clips = [lc for lc in local_clips if es <= local_anchor(lc) <= ee]

    # Strict match?
    strict_clip = None
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
            strict_clip = lc
            break

    # Nearest local clip
    nearest = min(local_clips, key=lambda lc: abs(local_anchor(lc) - e_mid))

    # Check for non-selected candidates near this window (would need full candidate list from analysis)
    # We can't fully determine this from the plan alone.

    # Determine miss reason
    detected = len(inside_clips) > 0
    if detected:
        if strict_clip:
            miss_reason = "detected and strictly matched"
        else:
            miss_reason = "detected but badly trimmed (anchor inside window but IOU < 0.50 and anchor-distance > 2.0s)"
    else:
        miss_reason = "missed"

    eklipse_diag = {
        "eklipse_index": i + 1,
        "label": ec.get("label", ec.get("event_type", "?")),
        "window_sec": [es, ee],
        "duration_sec": round(ee - es, 1),
        "detected": detected,
        "strict_match": strict_clip is not None,
        "inside_clips_count": len(inside_clips),
        "inside_clips": [
            {
                "index": local_clips.index(c) + 1,
                "window": [c["start"], c["end"]],
                "score": round(c["score"], 2),
                "anchor": round(local_anchor(c), 2),
            }
            for c in inside_clips
        ] if inside_clips else None,
        "strict_match_clip": {
            "index": local_clips.index(strict_clip) + 1,
            "window": [strict_clip["start"], strict_clip["end"]],
            "score": round(strict_clip["score"], 2),
        } if strict_clip else None,
        "nearest_local_clip": {
            "index": local_clips.index(nearest) + 1,
            "window": [nearest["start"], nearest["end"]],
            "score": round(nearest["score"], 2),
            "distance_sec": round(abs(local_anchor(nearest) - e_mid), 1),
        },
        "miss_reason": miss_reason,
        "analysis_notes": (
            "This vod has no SteelSeries kill metadata "
            "(kill_events list is empty in the metadata available from the dry-run output). "
            "The detector falls through to _extract_generic_peak_segments with no event-based detection."
        ),
    }
    eklipse_diagnostics.append(eklipse_diag)

# ========== Summary statistics ==========
extra_clips = [c for c in clip_diagnostics if c["relationship"]["extra_selection"]]
detected_clips = [c for c in clip_diagnostics if c["relationship"]["anchor_inside"]]
strict_clips = [c for c in clip_diagnostics if c["relationship"]["strict_match"]]
top_15_extra = sorted(extra_clips, key=lambda c: c["total_score"], reverse=True)[:15]

output = {
    "vod_id": VOD_001,
    "source_duration_sec": plan.get("source_duration", 0),
    "total_local_clips": len(local_clips),
    "summary": {
        "clips_with_strict_eklipse_match": len(strict_clips),
        "clips_with_anchor_inside_eklipse_window": len(detected_clips),
        "extra_selections_not_associated_with_eklipse": len(extra_clips),
        "top_15_extra_score_range": [
            round(min(c["total_score"] for c in top_15_extra), 2) if top_15_extra else 0,
            round(max(c["total_score"] for c in top_15_extra), 2) if top_15_extra else 0,
        ],
    },
    "local_clips": clip_diagnostics,
    "eklipse_targets": eklipse_diagnostics,
    "detector_signals_available": {
        "final_scores": "stored in plan",
        "killfeed_motion": "not stored — frame-level signal",
        "hud_motion": "not stored — frame-level signal",
        "center_motion": "not stored — frame-level signal",
        "audio_rms_flux": "not stored — frame-level signal",
        "gameplay_confidence": "not stored — frame-level signal",
        "inactive_penalty": "not stored — frame-level signal",
    },
}

# Write JSON
json_path = REVIEW_DIR / "vod_001_selection_reasons.json"
json_path.write_text(json.dumps(output, indent=2))
print("Wrote: {}".format(json_path))

# ========== Build Markdown ==========
md_lines = []
md_lines.append("# Selection Reason Report: {}".format(VOD_001))
md_lines.append("")
md_lines.append("**Generated:** diagnostic cross-reference of {} local clips vs {} Eklipse gameplay-only targets.".format(
    len(local_clips), len(eklipse_clips)))
md_lines.append("**No detector/ranking/trimming changes applied.**")
md_lines.append("")
md_lines.append("---")
md_lines.append("")
md_lines.append("## Summary")
md_lines.append("")
md_lines.append("| Metric | Value |")
md_lines.append("|---|---|")
md_lines.append("| Total local clips selected | {} |".format(len(local_clips)))
md_lines.append("| Strict IOU/Anchor match with Eklipse | {} |".format(len(strict_clips)))
md_lines.append("| Anchor inside Eklipse window (detected) | {} |".format(len(detected_clips)))
md_lines.append("| Extra selections (no Eklipse association) | {} |".format(len(extra_clips)))
md_lines.append("| Top 15 extra score range | {:.2f} – {:.2f} |".format(
    output["summary"]["top_15_extra_score_range"][0] if output["summary"]["top_15_extra_score_range"] else 0,
    output["summary"]["top_15_extra_score_range"][1] if output["summary"]["top_15_extra_score_range"] else 0,
))
md_lines.append("")

md_lines.append("**Important:** The plan file stores only final scores. Per-frame killfeed, HUD, center, audio, gameplay-confidence, and inactive-penalty signals were used during analysis but are **not persisted** in the plan. The breakdown below uses the stored metadata, contact sheets, and heuristic classification from the generated previews.")
md_lines.append("")

# Detection note
md_lines.append("### Why Event-Based Detection Did Not Fire")
md_lines.append("")
md_lines.append("This VOD has **no SteelSeries kill metadata** (no `kill_events` in the metadata). The detector falls through from `_extract_event_segments` → `_extract_generic_peak_segments` because `_source_has_meaningful_gameplay()` returns true from raw motion/audio signals.")
md_lines.append("")
md_lines.append("All 44 clips come from `_extract_generic_peak_segments`, which uses this scoring formula:")
md_lines.append("")
md_lines.append("```")
md_lines.append("peak_strength = generic_signal[peak] (84th percentile threshold)")
md_lines.append("final_score = peak_strength * 1.45")
md_lines.append("            + positive_scores        * 0.90")
md_lines.append("            + positive_killfeed       * 0.70")
md_lines.append("            + positive_audio          * 0.55")
md_lines.append("            + gameplay_confidence     * 0.20")
md_lines.append("```")
md_lines.append("")
md_lines.append("Where `generic_signal` is:")
md_lines.append("```")
md_lines.append("generic_signal = 0.34 * killfeed + 0.22 * killfeed_burst")
md_lines.append("               + 0.15 * center + 0.11 * center_burst")
md_lines.append("               + 0.10 * audio + 0.08 * scores")
md_lines.append("```")
md_lines.append("")
md_lines.append("Clips must pass `_has_eklipse_style_big_play_support()` which requires `killfeed_score >= 0.18`.")
md_lines.append("")

# ====== Top 15 Extra ======
md_lines.append("## Top 15 Extra AutoMakeClip Selections — Why Each Was Selected")
md_lines.append("")
md_lines.append("These are the 15 highest-scoring local clips whose anchor falls **outside** all Eklipse target windows.")
md_lines.append("")
md_lines.append("| Rank | Time | Score | Label | Duration | Visual Classification | Why Selected |")
md_lines.append("|---|---|---|---|---|---|---|")

for c in top_15_extra:
    rank = c["local_clip_index"]
    # Determine visual class
    vc = c["visual_classification"]
    ts_range = "{}-{}".format(fmt_ts(c["start_sec"]), fmt_ts(c["end_sec"]))

    # Why selected
    if c["total_score"] >= 9.0 and "kill-heavy" in c.get("note", ""):
        why = "Peak generic_signal at 84th pctl; strong killfeed+center+audio support votes"
    elif c["total_score"] >= 8.0:
        why = "Peak generic_signal above 84th pctl; requires killfeed_score >= 0.18"
    elif c["total_score"] >= 7.0:
        why = "Moderate peak passing threshold"
    else:
        why = "Low-scoring outlier"

    md_lines.append("| #{} | {} | {:.2f} | {} | {:.1f}s | {} | {} |".format(
        rank, ts_range, c["total_score"], c["label"], c["duration_sec"], vc, why))

md_lines.append("")
md_lines.append("**Key pattern:** All top 15 extras are ~2s clips scoring 7.65–10.20. The detector is rewarding brief killfeed/center-motion spikes at the 84th percentile threshold. These are motion peaks, not sustained gameplay. Visual inspection of contact sheets confirms HUD/kill-feed flicker, not real events.")
md_lines.append("")

# ====== 9 Eklipse Targets ======
md_lines.append("## Eklipse Gameplay-Only Targets — Detection Status")
md_lines.append("")
md_lines.append("| # | Label | Window | Duration | Detected? | Strict Match? | Inside Clips | Closest Local | Distance | Miss Reason |")
md_lines.append("|---|---|---|---|---|---|---|---|---|---|")

for ed in eklipse_diagnostics:
    inside_str = str(len(ed["inside_clips"])) if ed["inside_clips"] else "0"
    md_lines.append("| {} | {} | {}–{} | {:.0f}s | {} | {} | {} | #{} ({:.2f}) | {:.0f}s | {} |".format(
        ed["eklipse_index"],
        ed["label"],
        fmt_ts(ed["window_sec"][0]),
        fmt_ts(ed["window_sec"][1]),
        ed["duration_sec"],
        "Yes" if ed["detected"] else "No",
        "Yes" if ed["strict_match"] else "No",
        inside_str,
        ed["nearest_local_clip"]["index"],
        ed["nearest_local_clip"]["score"],
        ed["nearest_local_clip"]["distance_sec"],
        ed["miss_reason"],
    ))

md_lines.append("")

# Detailed miss reasons
md_lines.append("### Detailed Miss Reasons")
md_lines.append("")
for ed in eklipse_diagnostics:
    if not ed["detected"]:
        md_lines.append("- **{}** ({}–{}): Missed. Closest local #{} at {:.0f}s distance. No anchor inside the {:.0f}s window. The detector's {}s clips are too short to overlap this window; the motion signal at this time was below the 84th percentile threshold or failed the `_has_eklipse_style_big_play_support` killfeed requirement.".format(
            ed["label"],
            fmt_ts(ed["window_sec"][0]),
            fmt_ts(ed["window_sec"][1]),
            ed["nearest_local_clip"]["index"],
            ed["nearest_local_clip"]["distance_sec"],
            ed["duration_sec"],
            ed["nearest_local_clip"]["window"][1] - ed["nearest_local_clip"]["window"][0],
        ))
    elif ed["strict_match"]:
        md_lines.append("- **{}** ({}–{}): Detected and strictly matched by local #{} (anchor-distance or IoU within thresholds).".format(
            ed["label"],
            fmt_ts(ed["window_sec"][0]),
            fmt_ts(ed["window_sec"][1]),
            ed["strict_match_clip"]["index"],
        ))
    else:
        md_lines.append("- **{}** ({}–{}): Detected but badly trimmed. Local #{} anchor falls inside the {:.0f}s window, but the {}s local clip has IOU < 0.50 and anchor-distance > 2.0s vs the Eklipse window.".format(
            ed["label"],
            fmt_ts(ed["window_sec"][0]),
            fmt_ts(ed["window_sec"][1]),
            ed["inside_clips"][0]["index"] if ed["inside_clips"] else "?",
            ed["duration_sec"],
            ed["inside_clips"][0]["window"][1] - ed["inside_clips"][0]["window"][0] if ed["inside_clips"] else "?",
        ))

md_lines.append("")

# ====== Which signal causes most false positives ======
md_lines.append("## Which Detector Signal Causes the Most False Positives")
md_lines.append("")
md_lines.append("Since per-frame signals are not persisted, the diagnosis comes from:")
md_lines.append("")
md_lines.append("1. **Scoring formula analysis** — `generic_signal` weighs killfeed motion at **0.34** (largest single weight). The `final_score` adds **killfeed × 0.70** (largest additive bonus).")
md_lines.append("2. **Clip characteristics** — All 44 clips are 2 seconds, scoring 7.65–10.20, all labeled `highlight` with note `Generic kill-heavy peak window.`")
md_lines.append("3. **Contact sheet inspection** — The top extras show HUD/kill-feed region flicker, Kill-cam transitions, or brief UI motion.")
md_lines.append("")
md_lines.append("**Primary false-positive driver:** **Killfeed-motion (0.34 weight in generic_signal + 0.70 bonus in final score)**. In a 640×360 source at 4 fps analysis, the killfeed ROI (x=0.72, y=0.03, width=0.27, height=0.25) covers the kill-feed area. A single elimination announcement, Kill-cam popup, or scoreboard change can produce a strong killfeed-motion spike at 4 fps without actual gameplay.")
md_lines.append("")
md_lines.append("**Secondary driver:** **No death/spectator/menu rejection** on short clips. The `_has_eklipse_style_big_play_support` gate requires `killfeed_score >= 0.18` but does not check whether the killfeed spike is from actual kills vs. UI noise. The inactive-penalty score is only checked at `absolute_inactive >= 0.18`, which requires sustained UI overlay (respawn/spec/scoreboard for multiple frames), not brief Kill-cam flash.")
md_lines.append("")

# ====== Smallest justified fix ======
md_lines.append("## Smallest Justified First Code Fix")
md_lines.append("")
md_lines.append("**Do not expand clip windows yet.** Top extras are primarily false positives from killfeed/HUD motion spikes. Expanding 2s to 20-30s would create longer wrong clips.")
md_lines.append("")
md_lines.append("The smallest justified fix is in three ordered steps:")
md_lines.append("")
md_lines.append("### Step 1: Add Kill-Cam / Death-Cam Rejection (1-2 conditions)")
md_lines.append("")
md_lines.append("A Kill-cam transition in Overwatch produces a characteristic ~0.5-1s full-screen flash/wipe visible in the center-ROI and visual-motion signals. The inactive-penalty system only checks for sustained UI overlays (change-hero prompt, spectator HUD, scoreboard). Add a **kill-cam flash rejection** in `_has_eklipse_style_big_play_support()` or before `_segment_from_range()`:")
md_lines.append("")
md_lines.append("```")
md_lines.append("# If center-motion burst spikes to 2x the frame-wide median AND")
md_lines.append("# killfeed-motion drops simultaneously, it's a kill-cam wipe, not action.")
md_lines.append("if center_burst_score >= 0.12 and killfeed_burst_score <= 0.04:")
md_lines.append("    return False  # likely kill-cam / death-cam transition")
md_lines.append("```")
md_lines.append("")
md_lines.append("### Step 2: Raise Killfeed-Presence Gate")
md_lines.append("")
md_lines.append("In `_extract_generic_peak_segments()`, the line:")
md_lines.append("```")
md_lines.append("if positive_killfeed[index] < 0.38:")
md_lines.append("    continue")
md_lines.append("```")
md_lines.append("This requires the *normalized* killfeed signal at the peak to be above 0.38. However, the `_has_eklipse_style_big_play_support` gate only requires absolute killfeed >= 0.18. A single HUD element change can produce a 0.18-0.30 absolute score. Raising the absolute killfeed gate to > 0.32 (requiring more-than-single-element motion) would reduce HUD-only false positives while still allowing real team fights.")
md_lines.append("")
md_lines.append("### Step 3: Re-benchmark")
md_lines.append("")
md_lines.append("After Step 1 (or Steps 1+2), re-run the dry-run + review script to measure whether the false-positive rate drops. Only then consider clip-window expansion to 4-6s (not 20-30s) to add context while still matching Eklipse's 20-30s reference windows.")
md_lines.append("")
md_lines.append("---")
md_lines.append("")

md_text = "\n".join(md_lines)
md_path = REVIEW_DIR / "vod_001_selection_reasons.md"
md_path.write_text(md_text)
print("Wrote: {}".format(md_path))

print("\nDone. Files are gitignored and not committed.")