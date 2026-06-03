# AutoMakeClip Agent Instructions

Read `EKLIPSE_1_TO_1_PARITY_ROADMAP.md` before any work related to highlight detection, clip ranking, trimming, vertical output, captions, audio, or Eklipse parity.

## Primary Product Goal

Build AutoMakeClip toward functional/output parity with Eklipse.gg for Overwatch gameplay. “1:1” means matching visible results and workflow as closely as possible through same-VOD testing. It does not mean claiming access to Eklipse's private backend.

## Work Priority

Follow this order unless the user directly changes it:

1. Build and maintain same-VOD Eklipse comparison benchmarks.
2. Fix highlight detection and candidate fusion.
3. Add Overwatch event understanding: kills, multi-kills, ult/team-fight plays, health/clutch moments, objective/overtime/result context.
4. Improve ranking and clip trimming based on benchmark results.
5. Improve vertical layouts, captions, overlays, and export quality.
6. Polish music/audio and workflow features only after detection quality is measured and strong.


## Temporary Current Benchmark Guardrails

- Treat `benchmarks/overwatch_eklipse_parity/references/vod_001_eklipse_gameplay_only.json` as the primary VOD 001 product-quality benchmark.
- Treat `vod_001_eklipse_exports_raw.json` as literal Eklipse behavior, including Training Range selections.
- Treat `vod_001_eklipse_session_summary.json` as session-summary/coaching reference only; never use it as clip-window benchmark input.
- Current reproducible VOD 001 baseline: 57 selected clips, 53 extras, 2/9 unique Eklipse targets detected, and 2/9 strict matches.
- The dark-bottom-HUD kill-cam rejection idea was tested and rejected as non-discriminative. Do not reintroduce it without new visual evidence.
- Do not expand clip timing/windows until target-moment detection and false-positive behavior are measured and improved.
- Keep local VODs, generated plans, preview MP4s, contact sheets, caches, and diagnostic reports uncommitted.