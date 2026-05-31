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

## Required Workflow

* Inspect relevant files before editing.
* Do not guess existing functionality.
* State which roadmap checkbox item is being worked on.
* State exact files to change before editing.
* Make minimal, focused edits.
* Do not remove existing working features without explicit approval.
* Add or update tests for implemented behavior.
* Run the smallest relevant test suite after changes.
* Update roadmap checkboxes only after implementation and verification.
* Commit and push completed changes with a clear explanation.

## Important Restrictions

* Do not claim Eklipse parity without benchmark evidence.
* Do not let SteelSeries kill metadata exclude non-kill highlight candidates.
* Do not prioritize cosmetic polish or music improvements over missing detector/parity requirements.
* Do not commit generated videos, cached music, downloaded media, or debug-output artifacts unless explicitly requested.
