#!/usr/bin/env python3
"""Compare before and after kill-cam plan JSONs."""
import json
import sys

with open("output/vod_001_local_detection_before_killcam.plan.json") as f:
    before = json.load(f)
with open("output/vod_001_local_detection_after_killcam.plan.json") as f:
    after = json.load(f)

print(f"Before segments: {len(before['segments'])}")
print(f"After segments:  {len(after['segments'])}")

before_times = {round(s["highlight_time"], 2): s for s in before["segments"]}
after_times = {round(s["highlight_time"], 2): s for s in after["segments"]}

removed = set(before_times.keys()) - set(after_times.keys())
added = set(after_times.keys()) - set(before_times.keys())
common = set(before_times.keys()) & set(after_times.keys())

print(f"\nRemoved: {len(removed)}")
for t in sorted(removed):
    s = before_times[t]
    print(f"  t={t} score={s['score']:.4f} label={s['label']}")

print(f"\nAdded: {len(added)}")
for t in sorted(added):
    s = after_times[t]
    print(f"  t={t} score={s['score']:.4f} label={s['label']}")

print("\nScore changes:")
changed = []
for t in sorted(common):
    b = before_times[t]["score"]
    a = after_times[t]["score"]
    if abs(b - a) > 1e-9:
        changed.append((t, b, a, a - b))
        print(f"  t={t}: {b:.6f} -> {a:.6f} (delta={a-b:+.6f})")

print(f"\nUnchanged: {len(common) - len(changed)}")
print(f"Total changed scores: {len(changed)}")