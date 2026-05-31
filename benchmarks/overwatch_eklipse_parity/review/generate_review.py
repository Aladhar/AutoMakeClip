#!/usr/bin/env python3
"""
Generate visual review package for vod_001 Eklipse parity comparison.
Diagnosis only — no detector, ranking, trimming, or render changes.

Usage:
    python3 benchmarks/overwatch_eklipse_parity/review/generate_review.py
"""
import json
import subprocess
from pathlib import Path

VOD_ID = "vod_001"
VOD_PATH = Path("benchmarks/overwatch_eklipse_parity/downloads/vod.mp4").resolve()
EXLIPSE_REF_PATH = Path("benchmarks/overwatch_eklipse_parity/references/vod_001_eklipse_gameplay_only.json").resolve()
LOCAL_PLAN_PATH = Path("output/vod_001_local_detection.plan.json").resolve()
REVIEW_DIR = (Path("benchmarks/overwatch_eklipse_parity/review") / VOD_ID).resolve()
REVIEW_DIR.mkdir(parents=True, exist_ok=True)
BASE_DIR = Path.cwd().resolve()

def rel_path(p: Path) -> str:
    try:
        return str(p.relative_to(BASE_DIR))
    except ValueError:
        return str(p)

# Thresholds (from benchmark.py)
IOU_THRESHOLD = 0.50
ANCHOR_DISTANCE_THRESHOLD_SEC = 2.0

# Load data
with open(EXLIPSE_REF_PATH) as f:
    eklipse_clips = json.load(f)
with open(LOCAL_PLAN_PATH) as f:
    plan = json.load(f)
local_clips = plan["segments"]
SOURCE_DURATION = plan.get("source_duration", 0)

def fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"

def local_anchor(lc: dict) -> float:
    return lc.get("highlight_time") or ((lc["start"] + lc["end"]) / 2.0)

def sec_filename(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}-{m:02d}-{s:02d}"

def extract_preview(start_sec: float, duration_sec: float, output_path: Path, max_width: int = 640):
    """Extract a low-size playable MP4 preview from the VOD (no text overlay — drawtext unavailable)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", str(VOD_PATH),
        "-t", str(duration_sec),
        "-vf", f"scale={max_width}:-2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "28",
        "-c:a", "aac", "-b:a", "64k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  WARNING: ffmpeg failed for {output_path.name}: {result.stderr.strip()[:200]}")
        return False
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  Generated: {output_path.name} ({size_mb:.1f} MB)")
    return True

def get_thumbnail_frame(start_sec: float, output_path: Path, max_width: int = 640):
    """Extract a single representative frame as JPEG (no text overlay)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", str(VOD_PATH),
        "-vframes", "1",
        "-vf", f"scale={max_width}:-2",
        "-q:v", "5",
        str(output_path),
    ]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  WARNING: thumbnail failed for {output_path.name}: {result.stderr.strip()[:200]}")
        return False
    return True

# ======================== A. Eklipse Gameplay-Only Reference Previews =======
print("=" * 60)
print("A. Extracting Eklipse Gameplay-Only Reference Previews (9 clips)")
print("=" * 60)

eklipse_previews = []
for i, ec in enumerate(eklipse_clips):
    es = ec["start_sec"]
    ee = ec["end_sec"]
    duration = ee - es
    safe_label = ec.get("label", ec.get("event_type", "event")).replace(" ", "_").replace("-", "_").replace("/", "_")
    fn = f"eklipse_{i+1:02d}_{safe_label}_{sec_filename(es)}_to_{sec_filename(ee)}.mp4"
    out_path = REVIEW_DIR / "eklipse_references" / fn
    extract_preview(es, duration, out_path)
    thumb_path = out_path.with_suffix(".jpg")
    get_thumbnail_frame((es + ee) / 2.0, thumb_path)
    eklipse_previews.append({
        "index": i + 1, "label": ec.get("label", ""), "event_type": ec.get("event_type", ""),
        "start_sec": es, "end_sec": ee, "duration": duration,
        "filename": fn, "path": rel_path(out_path), "thumbnail": rel_path(thumb_path),
    })

# ======================== B. Detected-but-Badly-Trimmed =====================
print("\n" + "=" * 60)
print("B. Detected-but-Badly-Trimmed Event Analysis")
print("=" * 60)

for ec in eklipse_clips:
    es = ec["start_sec"]
    ee = ec["end_sec"]
    e_mid = (es + ee) / 2.0

    # Find strict match candidate
    best_local = None
    for li, lc in enumerate(local_clips):
        i_start = max(es, lc["start"])
        i_end = min(ee, lc["end"])
        intersection = max(0.0, i_end - i_start)
        u_start = min(es, lc["start"])
        u_end = max(ee, lc["end"])
        union = max(0.0, u_end - u_start)
        iou = intersection / union if union > 0 else 0.0
        l_anchor = local_anchor(lc)
        anchor_dist = abs(e_mid - l_anchor)
        if iou >= IOU_THRESHOLD or anchor_dist <= ANCHOR_DISTANCE_THRESHOLD_SEC:
            if best_local is None or iou > 0:  # just take first match
                best_local = lc

    # Find anchor detection
    detecting_local = None
    detecting_anchor = None
    for lc in local_clips:
        anchor = local_anchor(lc)
        if es <= anchor <= ee:
            detecting_local = lc
            detecting_anchor = anchor
            break

    if detecting_local is not None and best_local is None:
        safe_label = ec.get("label", ec.get("event_type", "event")).replace(" ", "_").replace("-", "_")
        print(f"  Badly trimmed: {ec.get('label','?')} at {fmt_ts(es)}-{fmt_ts(ee)}")
        print(f"    Detected by: {fmt_ts(detecting_local['start'])}-{fmt_ts(detecting_local['end'])} anchor={detecting_anchor:.2f}")
        lstart = sec_filename(detecting_local["start"])
        lend = sec_filename(detecting_local["end"])
        fn = f"detected_badly_{safe_label}_local_{lstart}_to_{lend}.mp4"
        extract_preview(detecting_local["start"], detecting_local["end"] - detecting_local["start"],
                        REVIEW_DIR / "detected_but_badly_trimmed" / fn)

# ======================== C. Missed Eklipse Events ==========================
print("\n" + "=" * 60)
print("C. Missed Eklipse Events")
print("=" * 60)

for ec in eklipse_clips:
    es = ec["start_sec"]
    ee = ec["end_sec"]
    e_mid = (es + ee) / 2.0

    detected = any(es <= local_anchor(lc) <= ee for lc in local_clips)
    if detected:
        continue

    closest = min(local_clips, key=lambda lc: abs(local_anchor(lc) - e_mid))
    closest_anchor = local_anchor(closest)
    closest_dist = abs(closest_anchor - e_mid)

    safe_label = ec.get("label", ec.get("event_type", "event")).replace(" ", "_").replace("-", "_")
    print(f"  Missed: {ec.get('label','?')} at {fmt_ts(es)}-{fmt_ts(ee)}")
    print(f"    Closest: {fmt_ts(closest['start'])}-{fmt_ts(closest['end'])} anchor={closest_anchor:.2f} dist={closest_dist:.1f}s score={closest['score']:.2f}")

    # Eklipse reference preview
    efn = f"missed_eklipse_{safe_label}_{sec_filename(es)}_to_{sec_filename(ee)}.mp4"
    extract_preview(es, ee - es, REVIEW_DIR / "missed_events" / efn)

    # Closest local preview
    lfn = f"missed_closest_local_{safe_label}_{sec_filename(closest['start'])}_to_{sec_filename(closest['end'])}.mp4"
    extract_preview(closest["start"], closest["end"] - closest["start"], REVIEW_DIR / "missed_events" / lfn)

    # Representative frames
    for offset_name, offset_sec in [("during", e_mid), ("before_2s", es + 1), ("after_2s", ee - 1)]:
        get_thumbnail_frame(offset_sec, REVIEW_DIR / "missed_events" / f"frame_{safe_label}_{offset_name}_{sec_filename(offset_sec)}.jpg")

# ======================== D. Extra AutoMakeClip Selections (Top 15) ==========
print("\n" + "=" * 60)
print("D. Extra AutoMakeClip Selections — Top 15")
print("=" * 60)

associated_anchors = set()
for ec in eklipse_clips:
    for lc in local_clips:
        anchor = local_anchor(lc)
        if ec["start_sec"] <= anchor <= ec["end_sec"]:
            associated_anchors.add(anchor)

extra_clips = [lc for lc in local_clips if local_anchor(lc) not in associated_anchors]
extra_clips_sorted = sorted(extra_clips, key=lambda lc: lc["score"], reverse=True)

extra_data = []
for i, lc in enumerate(extra_clips_sorted[:15]):
    fn = f"extra_{i+1:02d}_{lc.get('label','highlight')}_{sec_filename(lc['start'])}_to_{sec_filename(lc['end'])}_score{lc['score']:.2f}.mp4"
    extract_preview(lc["start"], lc["end"] - lc["start"], REVIEW_DIR / "extra_clips" / fn)
    extra_data.append({
        "rank": i + 1, "start": lc["start"], "end": lc["end"],
        "score": lc["score"], "label": lc.get("label", ""), "note": lc.get("note", ""),
        "filename": fn, "path": rel_path(REVIEW_DIR / "extra_clips" / fn),
    })
    print(f"  #{i+1}: {fmt_ts(lc['start'])}-{fmt_ts(lc['end'])} score={lc['score']:.2f} {lc.get('label','')}")

# ======================== Markdown Report ===================================
print("\n" + "=" * 60)
print("Writing diagnostic markdown report...")
print("=" * 60)

report_lines = [
    f"# Visual Review Report: {VOD_ID}",
    "",
    "**Generated:** diagnosis run — no detector/ranking/trimming changes applied.",
    f"**Eklipse reference:** `{EXLIPSE_REF_PATH}`",
    f"**Local plan:** `{LOCAL_PLAN_PATH}`",
    f"**Source VOD:** `{VOD_PATH}` (duration: {SOURCE_DURATION:.0f}s)",
    "",
    "**Note:** Text overlays on preview videos were disabled because `drawtext` filter is not available in this ffmpeg build. Filenames encode the label and timestamps.",
    "",
    "## Summary: Eklipse Gameplay-Only Events vs AutoMakeClip Detection",
    "",
    "| # | Eklipse Event | Window | Detected? | Strict Match? | Credited Local Clip | Local Anchor |",
    "|---|---|---|---|---|---|---|",
]

for i, ec in enumerate(eklipse_clips):
    es = ec["start_sec"]
    ee = ec["end_sec"]
    e_mid = (es + ee) / 2.0

    detecting_local = None
    detecting_anchor = None
    for lc in local_clips:
        anchor = local_anchor(lc)
        if es <= anchor <= ee:
            detecting_local = lc
            detecting_anchor = anchor
            break

    strict = False
    for lc in local_clips:
        l_anchor = local_anchor(lc)
        i_start = max(es, lc["start"])
        i_end = min(ee, lc["end"])
        intersection = max(0.0, i_end - i_start)
        u_start = min(es, lc["start"])
        u_end = max(ee, lc["end"])
        union = max(0.0, u_end - u_start)
        iou = intersection / union if union > 0 else 0.0
        anchor_dist = abs(e_mid - l_anchor)
        if iou >= IOU_THRESHOLD or anchor_dist <= ANCHOR_DISTANCE_THRESHOLD_SEC:
            strict = True
            break

    detected_str = "✅ Yes" if detecting_local else "❌ No"
    strict_str = "✅ Yes" if strict else "❌ No"
    credited = f"{fmt_ts(detecting_local['start'])}–{fmt_ts(detecting_local['end'])}" if detecting_local else "—"
    anchor_str = f"{detecting_anchor:.2f}s" if detecting_anchor else "—"

    safe_label = ec.get("label", ec.get("event_type", "event")).replace(" ", "_").replace("-", "_")
    e_preview = f"eklipse_references/eklipse_{i+1:02d}_{safe_label}_{sec_filename(es)}_to_{sec_filename(ee)}.mp4"
    report_lines.append(f"| {i+1} | [{ec.get('label', ec.get('event_type', '?'))}]({e_preview}) | {fmt_ts(es)}–{fmt_ts(ee)} | {detected_str} | {strict_str} | {credited} | {anchor_str} |")

report_lines += [
    "",
    "## Correction: Double Kill Detection Credit",
    "",
    "The previous report incorrectly credited local clip `00:58:53–00:58:55` (anchor=3534.0s) for detecting the Eklipse Double kill window `00:59:03–00:59:25` (3543–3565s). That anchor falls **outside** the Eklipse window.",
    "",
    "The correct detecting clip is **`00:59:20–00:59:22`** (anchor=3561.75s), which falls inside 3543–3565s. This clip is not an export-window match (IoU < 0.50 and anchor-distance > 2.0s), but it does count as *event-detected* because its highlight anchor falls inside the Eklipse window.",
    "",
    "The Stream moment at 01:21:30–01:22:00 (4890–4920s) was correctly detected by local clip **`01:21:39–01:21:41`** (anchor=4900.0s).",
    "",
    "## What the Detector Is Currently Rewarding",
    "",
    "From the 44 local clips, the current scoring behavior rewards:",
    "",
    "1. **Localized kill-feed / HUD motion peaks** — many 2-second clips scored 8–10 appear to be brief moments of high motion intensity in the score/kill-feed region, not sustained gameplay action.",
    "2. **Generic center-screen activity** — clips with high center-motion scores but no Overwatch event understanding.",
    "3. **No event-type discrimination** — most clips are labeled `highlight` with note `Generic kill-heavy peak window.` regardless of actual content.",
    "4. **No clip-window contextualization** — clips are 2 seconds flat with no setup or aftermath padding, unlike Eklipse's 20–30s windows.",
    "5. **No death/spectator/menu rejection** — clips may include respawn or menu time if the motion signal happened nearby.",
    "6. **Eklipse window multi_kill_001 (01:07:50–01:08:38, 48s) is much longer** than typical Eklipse windows, suggesting Eklipse detected the entire team fight, not just a single kill burst.",
    "",
    "## Smallest Justified Next Fix",
    "",
    "Based on this visual review, the first minimal changes should be:",
    "",
    "1. **Check the Eklipse reference data** — Some windows (01:07:50–01:08:38 = 48s) are much longer than the typical 20–30s. Verify if this is a multi-kill across an extended team fight or a reference-file anomaly before changing any detector logic.",
    "2. **Increase clip window** — 2-second windows are too short to match Eklipse's 20–30s windows. The detector needs to select context windows, not just 2-second motion peaks.",
    "3. **Add Overwatch event discrimination** — Replace `Generic kill-heavy peak window` notes with meaningful event labels (kill_feed_spike, ult_usage, team_fight, objective_push) even if heuristic-based.",
    "4. **Add death/spectator/menu rejection** before clip-window or ranking changes.",
    "5. **Re-benchmark with corrected event-detection report** after the above changes before adjusting ranking or trimming.",
    "",
    "## Detected-But-Badly-Trimmed Events",
    "",
    "These events have an anchor inside the Eklipse window but no strict export-window match:",
    "",
]

for ec in eklipse_clips:
    es = ec["start_sec"]
    ee = ec["end_sec"]
    detecting_local = None
    detecting_anchor = None
    for lc in local_clips:
        anchor = local_anchor(lc)
        if es <= anchor <= ee:
            detecting_local = lc
            detecting_anchor = anchor
            break
    if detecting_local is None:
        continue

    e_mid = (es + ee) / 2.0
    strict = False
    for lc in local_clips:
        l_anchor = local_anchor(lc)
        i_start = max(es, lc["start"])
        i_end = min(ee, lc["end"])
        intersection = max(0.0, i_end - i_start)
        u_start = min(es, lc["start"])
        u_end = max(ee, lc["end"])
        union = max(0.0, u_end - u_start)
        iou = intersection / union if union > 0 else 0.0
        if iou >= IOU_THRESHOLD or abs(e_mid - l_anchor) <= ANCHOR_DISTANCE_THRESHOLD_SEC:
            strict = True
            break
    if strict:
        continue

    safe_label = ec.get("label", ec.get("event_type", "event")).replace(" ", "_").replace("-", "_")
    lstart = sec_filename(detecting_local["start"])
    lend = sec_filename(detecting_local["end"])
    report_lines += [
        f"### {ec.get('label', ec.get('event_type', '?'))} at {fmt_ts(es)}–{fmt_ts(ee)}",
        "",
        f"- **Eklipse window:** `{fmt_ts(es)}–{fmt_ts(ee)}` ({ee - es:.0f}s)",
        f"- **Local anchor inside window:** `{fmt_ts(detecting_local['start'])}–{fmt_ts(detecting_local['end'])}` (anchor={detecting_anchor:.2f}s)",
        f"- **Local clip duration:** {detecting_local['end'] - detecting_local['start']:.1f}s (vs Eklipse {ee - es:.0f}s)",
        f"- **Score:** {detecting_local['score']:.2f}",
        f"- **Why no strict match:** local window is only {detecting_local['end'] - detecting_local['start']:.0f}s vs Eklipse {ee - es:.0f}s → IoU < 0.50; anchor distance > 2.0s",
        f"- **Local preview:** `detected_but_badly_trimmed/detected_badly_{safe_label}_local_{lstart}_to_{lend}.mp4`",
        "",
    ]

report_lines += [
    "## Missed Eklipse Events (No Local Anchor Inside Window)",
    "",
    "| # | Eklipse Event | Window | Closest Local | Distance | Closest Score |",
    "|---|---|---|---|---|---|",
]

for i, ec in enumerate(eklipse_clips):
    es = ec["start_sec"]
    ee = ec["end_sec"]
    e_mid = (es + ee) / 2.0
    if any(es <= local_anchor(lc) <= ee for lc in local_clips):
        continue
    closest = min(local_clips, key=lambda lc: abs(local_anchor(lc) - e_mid))
    closest_anchor = local_anchor(closest)
    safe_label = ec.get("label", ec.get("event_type", "event")).replace(" ", "_").replace("-", "_")
    e_preview = f"missed_events/missed_eklipse_{safe_label}_{sec_filename(es)}_to_{sec_filename(ee)}.mp4"
    report_lines.append(f"| {i+1} | [{ec.get('label', ec.get('event_type', '?'))}]({e_preview}) | {fmt_ts(es)}–{fmt_ts(ee)} | {fmt_ts(closest['start'])}–{fmt_ts(closest['end'])} | {abs(closest_anchor - e_mid):.1f}s | {closest['score']:.2f} |")

report_lines += [
    "",
    "## Extra AutoMakeClip Selections (Top 15, Not Associated with Eklipse Events)",
    "",
    "These are the 15 highest-scoring local clips whose highlight anchor falls outside all Eklipse gameplay-only windows.",
    "",
    "| Rank | Local Window | Score | Label | Note | Preview | Visual Assessment |",
    "|---|---|---|---|---|---|---|",
]

for ed in extra_data:
    report_lines.append(
        f"| {ed['rank']} | {fmt_ts(ed['start'])}–{fmt_ts(ed['end'])} | {ed['score']:.2f} "
        f"| {ed['label']} | {ed['note']} "
        f"| [preview](extra_clips/{ed['filename']}) | *(requires visual inspection)* |"
    )

report_lines += [
    "",
    "**Note:** Each preview needs visual inspection to classify as real gameplay, weak action, UI/HUD motion, death/spectator/menu noise, or other false positive.",
    "",
]

report_path = REVIEW_DIR / f"{VOD_ID}_visual_review.md"
report_path.write_text("\n".join(report_lines))
print(f"\nReport written to: {report_path}")

# Summary
print("\n" + "=" * 60)
print("GENERATION COMPLETE")
print("=" * 60)
print(f"\nReview folder: {REVIEW_DIR}")
print(f"  - eklipse_references/   ({len(eklipse_previews)} previews)")
print(f"  - detected_but_badly_trimmed/")
print(f"  - missed_events/")
print(f"  - extra_clips/          (top {len(extra_data)} extra clips)")
print(f"  - {VOD_ID}_visual_review.md")

total_mb = sum(f.stat().st_size for f in REVIEW_DIR.rglob("*") if f.is_file() and f.suffix in (".mp4", ".jpg")) / (1024 * 1024)
print(f"\nTotal size: {total_mb:.1f} MB")