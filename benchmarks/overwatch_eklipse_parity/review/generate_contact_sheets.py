#!/usr/bin/env python3
"""
Generate visual contact sheets (image panels) for vod_001 clip diagnosis.
Output goes under benchmarks/overwatch_eklipse_parity/review/vod_001/contact_sheets/
"""
import json
import subprocess
from pathlib import Path
from typing import Optional

VOD_PATH = Path("benchmarks/overwatch_eklipse_parity/downloads/vod.mp4").resolve()
EXLIPSE_REF_PATH = Path("benchmarks/overwatch_eklipse_parity/references/vod_001_eklipse_gameplay_only.json").resolve()
LOCAL_PLAN_PATH = Path("output/vod_001_local_detection.plan.json").resolve()
REVIEW_DIR = (Path("benchmarks/overwatch_eklipse_parity/review") / "vod_001").resolve()
CONTACT_DIR = REVIEW_DIR / "contact_sheets"
CONTACT_DIR.mkdir(parents=True, exist_ok=True)
BASE_DIR = Path.cwd().resolve()

def local_anchor(lc: dict) -> float:
    return lc.get("highlight_time") or ((lc["start"] + lc["end"]) / 2.0)

def sec_filename(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}-{m:02d}-{s:02d}"

with open(EXLIPSE_REF_PATH) as f:
    eklipse_clips = json.load(f)
with open(LOCAL_PLAN_PATH) as f:
    plan = json.load(f)
local_clips = plan["segments"]

def make_contact_sheet(
    start_sec: float,
    duration_sec: float,
    fps: float,
    label: str,
    suffix: str = "full",
    max_width: int = 640,
    crop_filter: Optional[str] = None,
):
    """Generate a contact sheet (tiled image panel) from a VOD window."""
    out_dir = CONTACT_DIR / "{}_{}".format(label, suffix)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Determine filter chain
    vf_parts = ["fps={}".format(fps)]
    if crop_filter:
        vf_parts.append(crop_filter)
    vf_parts.append("scale={}:-2".format(max_width))
    vf = ",".join(vf_parts)

    tile_fn = "contact_{}_{}_{}_to_{}.jpg".format(
        label, suffix,
        sec_filename(start_sec),
        sec_filename(start_sec + duration_sec),
    )
    tile_path = CONTACT_DIR / tile_fn

    # Generate tiled contact sheet
    args = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", str(VOD_PATH),
        "-t", str(duration_sec),
        "-vf", "{},tile=5x0:padding=2:margin=2".format(vf),
        "-frames:v", "1",
        "-q:v", "4",
        str(tile_path),
    ]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print("  WARNING: contact sheet failed for {}: {}".format(
            tile_fn, result.stderr.strip()[:200]))
    else:
        size_kb = tile_path.stat().st_size / 1024
        print("  Contact sheet: {} ({:.0f} KB)".format(tile_fn, size_kb))

    # Also save individual frames for detailed inspection
    frame_args = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", str(VOD_PATH),
        "-t", str(duration_sec),
        "-vf", "{}".format(vf),
        "-q:v", "5",
        str(out_dir / "frame_%05d.jpg"),
    ]
    subprocess.run(frame_args, capture_output=True, text=True)
    frame_count = len(list(out_dir.glob("*.jpg")))
    print("  Individual frames: {} in {}".format(frame_count, out_dir))


associated_anchors = set()
for ec in eklipse_clips:
    for lc in local_clips:
        anchor = local_anchor(lc)
        if ec["start_sec"] <= anchor <= ec["end_sec"]:
            associated_anchors.add(anchor)

extra_clips = [lc for lc in local_clips if local_anchor(lc) not in associated_anchors]
extra_clips_sorted = sorted(extra_clips, key=lambda lc: lc["score"], reverse=True)

print("=" * 60)
print("Generating Contact Sheets for vod_001")
print("=" * 60)

# Part 1: 1 fps overview for all 9 Eklipse reference clips
print("\n--- A. Eklipse References: 1 fps overview ---")
for i, ec in enumerate(eklipse_clips):
    es = ec["start_sec"]
    ee = ec["end_sec"]
    duration = ee - es
    safe_label = ec.get("label", ec.get("event_type", "event")).replace(" ", "_").replace("-", "_").replace("/", "_")
    label = "eklipse_{:02d}_{}".format(i + 1, safe_label)

    # Full-frame 1 fps
    make_contact_sheet(es, duration, 1.0, label, "full_1fps")

    # Kill-feed crop (top-right region of 640x360)
    make_contact_sheet(es, duration, 1.0, label, "killfeed_1fps",
                       crop_filter="crop=200:60:440:0")

    # HUD/ult status (top-center region)
    make_contact_sheet(es, duration, 1.0, label, "hud_1fps",
                       crop_filter="crop=200:60:220:0")

# Part 2: 1 fps overview for Top 15 extra AutoMakeClip clips
print("\n--- B. Extra AutoMakeClip Clips: 1 fps overview ---")
for rank, lc in enumerate(extra_clips_sorted[:15]):
    start = lc["start"]
    dur = lc["end"] - lc["start"]
    label = "extra_{:02d}_score{:.2f}".format(rank + 1, lc["score"])
    make_contact_sheet(start, dur, 1.0, label, "full_1fps")

# Part 3: 5 fps detailed for missed Eklipse events
print("\n--- C. Missed Eklipse Events: 5 fps detailed ---")
for ec in eklipse_clips:
    es = ec["start_sec"]
    ee = ec["end_sec"]
    duration = ee - es
    safe_label = ec.get("label", ec.get("event_type", "event")).replace(" ", "_").replace("-", "_").replace("/", "_")

    detected = any(es <= local_anchor(lc) <= ee for lc in local_clips)
    if detected:
        continue

    label = "missed_{}_{}_to_{}".format(safe_label, sec_filename(es), sec_filename(ee))
    make_contact_sheet(es, duration, 5.0, label, "detailed_5fps_full")
    make_contact_sheet(es, duration, 5.0, label, "detailed_5fps_hud",
                       crop_filter="crop=200:60:220:0")

# Part 4: 5 fps for detected-but-badly-trimmed
print("\n--- D. Detected-But-Badly-Trimmed: 5 fps detailed ---")
for ec in eklipse_clips:
    es = ec["start_sec"]
    ee = ec["end_sec"]
    e_mid = (es + ee) / 2.0
    safe_label = ec.get("label", ec.get("event_type", "event")).replace(" ", "_").replace("-", "_").replace("/", "_")

    detecting_local = None
    for lc in local_clips:
        anchor = local_anchor(lc)
        if es <= anchor <= ee:
            detecting_local = lc
            break
    if detecting_local is None:
        continue

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
        if iou >= 0.50 or abs(e_mid - l_anchor) <= 2.0:
            strict = True
            break
    if strict:
        continue

    label = "badly_trimmed_{}".format(safe_label)
    make_contact_sheet(es, 10.0, 5.0, label, "eklipse_window_5fps")
    make_contact_sheet(detecting_local["start"],
                       detecting_local["end"] - detecting_local["start"],
                       5.0, label, "local_clip_5fps")

# Part 5: 10 fps close-up for action bursts in top 5 extra clips
print("\n--- E. Top Extras: 10 fps action bursts ---")
for rank, lc in enumerate(extra_clips_sorted[:5]):
    start = lc["start"]
    dur = lc["end"] - lc["start"]
    if dur >= 1.0:
        label = "extra_{:02d}_action_burst".format(rank + 1)
        make_contact_sheet(start, min(dur, 4.0), 10.0, label, "10fps_full")
        make_contact_sheet(start, min(dur, 4.0), 10.0, label, "10fps_killfeed",
                           crop_filter="crop=200:60:440:0")

print("\n" + "=" * 60)
print("Contact sheet generation complete.")
cs_files = list(CONTACT_DIR.rglob("*.jpg"))
total_kb = sum(f.stat().st_size for f in cs_files) / 1024
print("Contact sheet folder: {}".format(CONTACT_DIR))
print("Total contact sheet files: {} ({:.0f} KB)".format(len(cs_files), total_kb))