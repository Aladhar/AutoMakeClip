# AutoMakeClip → Eklipse 1:1 Parity Roadmap
## Overwatch 2 / `MontageMAC` Progress Checklist

**Goal:** Build the local `AutoMakeClip` pipeline until its *visible results and workflow* match Eklipse.gg as closely as possible for your Overwatch clips: selected moments, ranking, start/end timing, vertical layouts, captions/overlays, audio balance, and export convenience.

**Meaning of “1:1”:** Functional/output parity, not a claim that the project uses Eklipse's private backend or proprietary model. The only honest proof of 1:1 quality is testing the same raw VOD through Eklipse and AutoMakeClip and measuring whether the outputs match.

**Audit date:** May 30, 2026  
**Audited target:** GitHub repository `Aladhar/AutoMakeClip`, branch `MontageMAC`

---

## Checkbox Key

- [x] Verified as already implemented in `MontageMAC`.
- [ ] Not completed yet, not verified yet, or needs testing.
- [ ] **REQUIRED** = must be done before claiming near-Eklipse parity.
- [ ] **SUGGESTED** = improvement after core parity is working.
- [ ] **MANUAL INPUT NEEDED** = something you need to provide from Eklipse or your own footage.

---

# 1. Final End Goal

## 1.1 The finished local pipeline should do all of this

- [ ] Automatically accept a raw Overwatch 2 VOD, stream download, or recording folder.
- [ ] Identify the same strong moments Eklipse identifies:
  - kills and multi-kills,
  - ultimates that create a meaningful play,
  - team-fight wins,
  - clutch survivals/escapes,
  - objective or overtime swings,
  - funny fails/chaos where appropriate,
  - strong reaction/hype moments supported by gameplay/audio evidence.
- [ ] Rank highlight clips similarly to Eklipse.
- [ ] Trim clips with similar setup time before the event and aftermath after the event.
- [ ] Create comparable vertical TikTok/Reels/YouTube Shorts exports.
- [ ] Keep or reposition important Overwatch HUD information cleanly.
- [ ] Add comparable captions, titles, stickers, templates and effects where the selected Eklipse style does.
- [ ] Mix gameplay audio and music so important fight audio remains understandable.
- [ ] Let you review/reject/retrim clips and improve future outputs.
- [ ] Produce ready-to-post clips without needing major manual fixes.

## 1.2 What “quality identical to Eklipse” should mean

Do **not** judge this from one clip that looks cool. Judge it through repeatable same-VOD comparisons.

- [ ] **REQUIRED:** Run each reference raw VOD through Eklipse and AutoMakeClip.
- [ ] **REQUIRED:** Save Eklipse's selected timestamps, ordering and exported appearance.
- [ ] **REQUIRED:** Generate a local comparison report after each important code change.
- [ ] **REQUIRED:** Use many different videos, not one cherry-picked example.
- [ ] **REQUIRED:** Keep improving the largest measured gaps.

### Suggested acceptance targets before saying “near 1:1”

These are project goals, not claims about Eklipse's private internal accuracy.

- [ ] Major-highlight recall of **90% or better** versus Eklipse-selected moments.
- [ ] Precision of **85% or better** among local top clips, with little weak filler.
- [ ] Top-3 ranking agreement of **80% or better** on videos with several highlights.
- [ ] Average start-time difference of **1.0 second or less**.
- [ ] Average end-time difference of **1.0 second or less**.
- [ ] Side-by-side vertical results judged equivalent or preferred compared with Eklipse in most tests.
- [ ] Captions/audio/layout no longer create an obvious quality difference.

---

# 2. Visible Eklipse Features That Define the Target

These are public-facing features and Overwatch guidance to reproduce from the user's point of view.

## 2.1 Highlight types to match

Eklipse publicly describes detecting gameplay events such as:

- [ ] Kills.
- [ ] Multi-kills.
- [ ] Clutches.
- [ ] Funny fails.
- [ ] Hype moments.
- [ ] Objectives and victories.
- [ ] Overwatch 2 plays, ultimates and team fights.
- [ ] Play of the Game (POTG) recognition as an optional local validation signal.
  - Note: SteelSeries publicly confirms POTG auto-clipping, but Eklipse does not publicly confirm POTG as a separate Overwatch trigger. Keep it as a useful extra detector, not a requirement for claiming Eklipse parity.
  
## 2.2 Overwatch HUD signals to support

**Important wording note:** These are visible public clues about what Eklipse uses or needs visible. They do not prove its full private ranking/model logic. AutoMakeClip should reproduce the observable behavior through same-VOD testing.

Eklipse's Overwatch guide states that it reads visible HUD elements. Your detector should intentionally support:

- [ ] Kill/Elimination Feed — top right.
- [ ] Health Bar / Armor / Shields — bottom left.
- [ ] Ultimate Charge & Ability HUD — bottom center.
- [ ] Scoreboard / Objective Progress — top center.
- [ ] Default English HUD styling/colors as the primary supported setup.
- [ ] Warnings or fallback behavior when facecam/alerts cover required HUD regions.

## 2.3 Editing/output workflow to eventually match

- [ ] Vertical templates for TikTok, Reels and Shorts.
- [ ] Cropping/trimming workflow.
- [ ] Auto captions with styled/animated text.
- [ ] Channel-name/title stickers or branding overlays.
- [ ] Memes/effects when wanted.
- [ ] High-quality export, aiming at up-to-1440p quality where the source allows.
- [ ] Easy export/share/post flow.

**Official reference pages checked:**
- https://eklipse.gg/features/ai-highlights/
- https://eklipse.gg/help/best-practices-overwatch-2/

---

# 3. Current `MontageMAC` Status

## 3.1 Verified foundation already present

- [x] Accepts local gameplay inputs and supports YouTube URL ingestion via `yt-dlp`.
- [x] Scores broad visual motion.
- [x] Scores top-right kill-feed-region motion.
- [x] Scores HUD-region motion.
- [x] Scores center-screen action motion.
- [x] Uses audio RMS and spectral-flux activity.
- [x] Includes inactive/death/scoreboard-style suppression logic.
- [x] Reads embedded SteelSeries/GameSense `KILL` event metadata when present.
- [x] Produces event-based kill/multi-kill candidate clips.
- [x] Produces generic action-heavy candidates when metadata is absent.
- [x] Suppresses overlap and possible duplicate repeated captures.
- [x] Downranks repetitive practice/training-style kill streams.
- [x] Can select multiple distinct strong clips from the same source video.
- [x] Has review memory for keep/cut choices, manual trims and notes.
- [x] Exports landscape montages.
- [x] Exports vertical Shorts-style video.
- [x] Has a Shorts layout that can pin health and kill-feed crops over gameplay.
- [x] Supports music selection and mixing.
- [x] Detects likely music drops using DSP features.
- [x] Snaps clip lengths toward music beats.
- [x] Aligns highlights with music drop timing.
- [x] Includes unit tests for core analysis/selection/render behavior.

## 3.2 Major blockers before 1:1 parity

- [ ] **REQUIRED:** No Eklipse-vs-local benchmark tool exists yet.
- [ ] **REQUIRED:** If SteelSeries event candidates exist, the current detector returns those instead of also using strong non-kill visual/audio highlight candidates.
- [ ] **REQUIRED:** Metadata parsing currently imports only `KILL` events.
- [ ] **REQUIRED:** Current core detection mostly measures HUD motion rather than understanding semantic gameplay events.
- [ ] **REQUIRED:** No verified ultimate-play detection.
- [ ] **REQUIRED:** No verified health-based clutch/survival detection.
- [ ] **REQUIRED:** No verified objective/overtime swing detection.
- [ ] **REQUIRED:** No verified victory/defeat/match-result context detection.
- [ ] **REQUIRED:** No verified auto-caption/title overlay system matching Eklipse edits.
- [ ] **REQUIRED:** No verified library of visual templates matching your selected Eklipse styles.
- [ ] **REQUIRED:** No automated side-by-side output comparison.
- [ ] **REQUIRED:** No measured proof yet that final outputs match Eklipse quality.
- [ ] Clean the repo: generated/debug music output was committed under `output_music_debug/`.
- [ ] Correct or implement the README's OCR/“ScreenSense” claim; the audited core detector currently shows motion/color/audio analysis rather than a full OCR event-recognition path.

## 3.3 Existing files to build on

| File | Current role | What should be added next |
|---|---|---|
| `src/automakeclip/analysis.py` | Scores/extracts highlight windows | Multi-source candidate fusion and event evidence |
| `src/automakeclip/ffmpeg.py` | Reads video/audio; parses SteelSeries `KILL` metadata | Broader metadata/event parsing helpers |
| `src/automakeclip/config.py` | Current ROIs/weights/render defaults | Health, ultimate, objective and result ROIs |
| `src/automakeclip/selection.py` | Ranks and de-duplicates clips | Event-specific parity ranking |
| `src/automakeclip/memory.py` | Learns from manual review notes/edits | Later use benchmark mismatch feedback |
| `src/automakeclip/review.py` | Human review workflow | Side-by-side Eklipse/local comparison |
| `src/automakeclip/render.py` | Shorts layouts/music mixing | Template/caption/export parity |
| `tests/` | Existing automated checks | Real benchmark/regression fixtures |

---

# 4. Basic Requirements Before Calling It Eklipse-Like Quality

## 4.1 Reliable input and comparison setup

- [ ] **REQUIRED:** Reliable import of raw local MP4/MKV/MOV gameplay sources.
- [x] YouTube input/download path exists.
- [ ] **REQUIRED:** Lock a known Overwatch test set for repeatable comparison.
- [ ] **REQUIRED:** Keep each paired comparison fair:
  - same raw VOD input,
  - same resolution/FPS,
  - same HUD/facecam/overlay state,
  - default English Overwatch HUD when possible,
  - same intended output mode.

## 4.2 Reference dataset

- [ ] **MANUAL INPUT NEEDED:** Pick at least 10–20 raw Overwatch VODs representative of your content.
- [ ] **MANUAL INPUT NEEDED:** Process those exact VODs with Eklipse.
- [ ] **MANUAL INPUT NEEDED:** Record every selected Eklipse clip timestamp and ranking.
- [ ] **MANUAL INPUT NEEDED:** Export/save Eklipse examples for the vertical template(s) you want.
- [ ] **MANUAL INPUT NEEDED:** Note captions, crop choices, titles, music and effects per example.

Dataset coverage:

- [ ] At least 3 VODs containing multi-kills.
- [ ] At least 3 containing team fights/ults with limited or delayed kills.
- [ ] At least 2 containing funny fails/chaos moments.
- [ ] At least 2 containing menus/deaths/spectating or low-value footage that should be rejected.
- [ ] At least 2 with facecam or overlays if your typical content includes those.
- [ ] At least 2 resolutions/source quality levels if robustness matters.

## 4.3 Detection requirements

- [ ] **REQUIRED:** Kill/elimination signal.
- [ ] **REQUIRED:** Multi-kill grouping.
- [ ] **REQUIRED:** Strong fight detection without metadata.
- [ ] **REQUIRED:** Ultimate/team-fight signal.
- [ ] **REQUIRED:** Health/armor/shield changes for clutch/survival signals.
- [ ] **REQUIRED:** Objective/overtime/progress signal.
- [ ] **REQUIRED:** Victory/defeat/end-state context signal.
- [ ] **REQUIRED:** Death/spectate/menu/scoreboard rejection logic.
- [ ] **REQUIRED:** Candidate fusion: metadata-confirmed kills and non-kill highlights can both survive in the same VOD.
- [ ] **REQUIRED:** Event labels/evidence stored for debugging and evaluation.

## 4.4 Selection/trimming requirements

- [ ] **REQUIRED:** Rank candidates rather than exporting every spike.
- [ ] **REQUIRED:** Prevent duplicate/near-duplicate clips.
- [ ] **REQUIRED:** Prefer the correct strongest clips, not generic motion filler.
- [ ] **REQUIRED:** Tune lead-in timing by event type.
- [ ] **REQUIRED:** Tune aftermath timing by event type.
- [ ] **REQUIRED:** Measure trim timing against Eklipse clips.
- [ ] **REQUIRED:** Avoid filler merely to reach target runtime.

## 4.5 Output/export requirements

- [x] Basic 1080×1920 vertical export exists.
- [ ] **REQUIRED:** Match the exact Eklipse vertical template(s) you personally use.
- [ ] **REQUIRED:** Preserve gameplay/HUD readability after crop/layout.
- [ ] **REQUIRED:** Support HUD-safe facecam placement when applicable.
- [ ] **REQUIRED:** Render captions/titles/stickers that appear in reference output.
- [ ] **REQUIRED:** Export without obvious extra compression artifacts.
- [ ] **REQUIRED:** Save reproducible export presets.

## 4.6 Testing requirements

- [ ] **REQUIRED:** Unit tests for every new event signal.
- [ ] **REQUIRED:** Regression fixtures from real timestamp comparisons.
- [ ] **REQUIRED:** Comparison report after every meaningful detector/ranker/render change.
- [ ] **REQUIRED:** Side-by-side visual/audio review for edited output.
- [ ] **REQUIRED:** Do not call a feature “fixed” based on only one selected clip.

---

# 5. Step-by-Step Implementation Roadmap

## Phase 0 — Protect the current foundation and clean the repo

**Purpose:** Preserve existing working behavior and stop generated artifacts from polluting development.

- [ ] Create a development branch from `MontageMAC`, for example `eklipse-parity`.
- [ ] Add ignore rules for generated output:
  - `output_music_debug/`
  - `music_cache/`
  - local benchmark video exports/raw VOD files unless deliberately tracked.
- [ ] Remove generated debug `.mp3`/`.wav` files from active tracked work.
- [ ] Run all existing tests.
- [ ] Save one baseline `.plan.json` and output render before changing detection.
- [ ] Create a parity changelog or use this file to record metric changes.

**Do not do first:**

- [ ] Do not rewrite the whole project.
- [ ] Do not remove review-memory/render/music functionality that already works.
- [ ] Do not prioritize more music polish before you measure highlight-selection parity.

**Exit gate:**

- [ ] Existing tests pass.
- [ ] Baseline output is saved.
- [ ] Working branch is clean of unneeded generated binary output.

---

## Phase 1 — Build the Eklipse reference set

**Purpose:** Establish what your target actually selects on your gameplay.

### For each VOD

- [ ] Keep an unchanged raw input named consistently, such as `vod_001_source.mp4`.
- [ ] Run that same file through Eklipse.
- [ ] Record every Eklipse clip's:
  - rank/order,
  - start timestamp,
  - end timestamp,
  - apparent event type,
  - crop/layout/template,
  - captions/title/effects,
  - music/audio behavior if used.
- [ ] Run current AutoMakeClip against the same input.
- [ ] Save local `.plan.json`.
- [ ] Save local and Eklipse vertical renders together for comparison.

### Recommended folder structure

```text
benchmarks/
  overwatch_eklipse_parity/
    README.md
    vod_001/
      source_reference.json
      eklipse_clips.json
      local_plan.json
      notes.md
      exports/
        eklipse_vertical.mp4
        local_vertical.mp4
    vod_002/
      ...
```

### Suggested `eklipse_clips.json` format

```json
{
  "source_video": "vod_001_source.mp4",
  "game": "Overwatch 2",
  "hud_style": "default English HUD",
  "clips": [
    {
      "rank": 1,
      "start": 312.4,
      "end": 327.8,
      "event_type": "multi_kill",
      "notes": "Team fight ending in three eliminations"
    }
  ]
}
```

**Exit gate:**

- [ ] At least 10 reference VODs captured.
- [ ] At least 30 total Eklipse-selected clip examples recorded.
- [ ] Examples include non-simple-kill moments such as ult/team fight/clutch/fail.

---

## Phase 2 — Implement a parity benchmark/report tool

**Purpose:** Make progress measurable instead of subjective.

### New implementation

- [ ] Add `src/automakeclip/parity.py`.
- [ ] Add a command such as:

```bash
automakeclip-parity \
  --benchmark benchmarks/overwatch_eklipse_parity \
  --report output/parity_report.md
```

- [ ] Add `tests/test_parity.py`.

### Report metrics

- [ ] **Moment recall:** Eklipse moments matched by local output.
- [ ] **Moment precision:** Local selected clips that match Eklipse selections.
- [ ] **Missed moments:** Eklipse clips not locally detected.
- [ ] **Weak extras:** Local clips not present in Eklipse choices.
- [ ] **Rank agreement:** Whether the best moments are prioritized similarly.
- [ ] **Start-time error.**
- [ ] **End-time error.**
- [ ] **Duration difference.**
- [ ] **Event-type mismatch:** e.g. Eklipse selected an ult play but local treated it as generic motion.

### Repeatable clip matching rule

- [ ] Count a local clip as matching an Eklipse clip when either:
  - clip time intersection-over-union is at least `0.50`, or
  - highlight anchor timestamps are within `2.0` seconds.
- [ ] Allow one-to-one matching only; do not let one local clip satisfy several unrelated Eklipse clips.
- [ ] Output exact timestamps for every miss and extra.

### Example report summary

```text
VOD: vod_001
Eklipse clips: 5
Local clips: 5
Matched: 4
Missed: 1 (clutch_survival at 08:35)
Extra weak local clip: 1 (generic fight at 02:10)
Top-3 rank agreement: 2/3
Mean start error: +1.4 sec
Mean end error: +0.8 sec
Next fix: add health swing / survival detector
```

**Exit gate:**

- [ ] One command evaluates every relevant VOD.
- [ ] Baseline report is saved before detector refactoring.
- [ ] Every future feature can be evaluated against the same reference.

---

## Phase 3 — Fix detector candidate fusion

**Purpose:** Stop losing highlights just because kill metadata exists.

### Current design issue

The audited `analysis.py` path returns event candidates when SteelSeries kill metadata exists and otherwise uses generic visual peaks. That can skip:

- an ultimate that wins a fight without immediate personal kills,
- a clutch survival,
- an objective swing,
- a funny failure,
- a reaction/hype moment.

### Required refactor

- [ ] Modify `src/automakeclip/analysis.py`.
- [ ] Always generate and combine:
  - metadata candidates,
  - visual kill-feed candidates,
  - team-fight candidates,
  - ult/ability candidates,
  - health/clutch candidates,
  - objective/victory candidates,
  - funny/fail candidates.
- [ ] Merge/deduplicate overlapping candidates while retaining combined evidence.
- [ ] Rank only after fusion.

### Data model improvement

- [ ] Extend `Segment` or add an evidence object containing:
  - `event_type`,
  - `confidence`,
  - `evidence`,
  - `anchor_time`,
  - source signal scores,
  - reject reason for discarded segments.

Example:

```json
{
  "event_type": "ultimate_teamfight",
  "confidence": 0.88,
  "evidence": [
    "ultimate HUD transition",
    "kill-feed burst",
    "center action spike",
    "objective progress change"
  ]
}
```

**Exit gate:**

- [ ] Kill metadata no longer blocks valid non-kill candidates.
- [ ] Parity recall increases without a large precision drop.
- [ ] Debug output explains why every selected clip survived.

---

## Phase 4 — Add Overwatch-specific event understanding

**Purpose:** Move from “pixels moved” to “this gameplay event likely happened.”

## 4.1 Expand region-of-interest configuration

Modify `src/automakeclip/config.py`:

- [ ] Add `health_roi` for bottom-left health/armor/shields.
- [ ] Add `ultimate_roi` for bottom-center ultimate/ability state.
- [ ] Add `objective_roi` for top-center scoreboard/objective progress.
- [ ] Add `result_roi` for victory/defeat/end-state recognition.
- [x] Retain current `killfeed_roi`, `hud_roi` and `center_roi`.

## 4.2 Suggested new modules

- [ ] Add `src/automakeclip/overwatch_signals.py`.
- [ ] Add `src/automakeclip/overwatch_events.py`.
- [ ] Keep raw frame/audio extraction within the existing analysis pipeline.

## 4.3 Implement event signals in this order

### A. Kill-feed recognition

- [ ] Detect new elimination-feed entries, not only top-right movement.
- [ ] Distinguish true feed entries from unrelated alerts/animation.
- [ ] Group repeated entries into multi-kill/team-fight evidence.
- [ ] Test against multiple heroes, maps and overlay states.

### B. Ultimate/team-fight recognition

- [ ] Detect meaningful ultimate/ability HUD changes.
- [ ] Combine ult transitions with action/audio/kill-feed/objective evidence.
- [ ] Produce `ultimate_play` / `ultimate_teamfight` candidates even without kill metadata.

### C. Health/clutch recognition

- [ ] Detect sharp health loss.
- [ ] Detect survival/recovery after low-health danger.
- [ ] Detect low health followed by kills or objective success.
- [ ] Produce `clutch_survival` candidates.

### D. Objective/overtime/victory context

- [ ] Detect major objective/progress state changes.
- [ ] Detect overtime or final-point fight context when visible.
- [ ] Detect victory/defeat/end-of-round screens.
- [ ] Use end-state screens as context for the prior fight rather than selecting boring menu/end animation footage.

### E. Rejection states

- [x] Some inactive/death/scoreboard suppression already exists.
- [ ] Expand for respawn, hero select, loading, pause, spectating-only and training-practice footage.
- [ ] Save rejection reasons for debugging.

### F. POTG / post-match validation signal

- [ ] Detect the Play of the Game title/screen when visible.
- [ ] Use POTG as a supporting label or validation signal, not as the only way to choose the best clip.
- [ ] Prefer the original fight moment over only clipping the later POTG replay.

## 4.4 Recommended technology order

Start simple and reliable:

- [ ] Color/state/template detection for fixed HUD indicators.
- [ ] Temporal signal rules across multiple frames.
- [ ] OCR or template recognition for elimination/result text where necessary.
- [ ] Only consider a learned model later if benchmark results stop improving.

**Exit gate:**

- [ ] The benchmark confirms improved catch rate on ult/team-fight/clutch/objective examples.
- [ ] False positives from menu/spectator/overlay motion remain controlled.

---

## Phase 5 — Match ranking and clip timing

**Purpose:** Catching the moment is not enough; the system should pick and cut it like Eklipse.

## 5.1 Ranking

- [ ] Create explicit ranking behavior by event category based on benchmark evidence.
- [ ] Prefer high-confidence multi-kills, meaningful ult plays, clutches and objective wins over generic activity.
- [ ] Determine whether funny/fail clips should be default or optional based on Eklipse outputs you like.
- [ ] Add hook-first sequencing only if reference outputs consistently do it.
- [ ] Tune weights through measured rank errors, not visual guessing.

## 5.2 Event-specific trimming

- [ ] `multi_kill`: include setup before first elimination and aftermath after final elimination.
- [ ] `ultimate_play`: include ability activation and fight consequence.
- [ ] `clutch_survival`: include danger/low-health point and resolution.
- [ ] `objective_swing`: include fight lead-in and outcome proof.
- [ ] `funny_fail`: include context needed for it to make sense.

### Track trim errors

- [ ] Start too early.
- [ ] Start too late.
- [ ] End too early.
- [ ] End too late.
- [ ] Key action clipped off.
- [ ] Too much dead air.

**Exit gate:**

- [ ] Major-highlight recall is strong.
- [ ] Weak filler is controlled.
- [ ] Trim timing difference versus Eklipse is near target.

---

## Phase 6 — Match vertical layout and visual export

**Purpose:** Reproduce the Eklipse style you personally want in the final short.

## 6.1 Collect visual references

- [ ] **MANUAL INPUT NEEDED:** Export/screenshoot the exact Eklipse vertical layouts you prefer.
- [ ] Record:
  - gameplay crop position,
  - background/blur treatment,
  - facecam location,
  - HUD visibility,
  - caption style/location,
  - title/channel sticker layout,
  - watermark behavior,
  - output resolution/FPS.

## 6.2 Build layout presets

- [ ] Add `src/automakeclip/layouts.py`.
- [ ] Implement presets only after real reference capture, for example:
  - `eklipse_full_gameplay_blur_bg`,
  - `eklipse_hud_pinned`,
  - `eklipse_facecam_gameplay_stack`.
- [ ] Add a CLI option:

```bash
automakeclip --shorts --layout-preset eklipse_hud_pinned ...
```

## 6.3 Export fidelity checks

- [ ] Match output dimensions/frame rate to the chosen reference.
- [ ] Preserve sharpness and avoid extra compression.
- [ ] Compare identical frames side by side.
- [ ] View on phone-size playback to verify HUD/caption readability.

**Exit gate:**

- [ ] Crop/layout differences are no longer obvious in side-by-side inspection.
- [ ] Local output is not visibly blurrier or more artifacted than the Eklipse reference.

---

## Phase 7 — Match captions, overlays, effects and audio

**Purpose:** Match finishing polish only after detection and layout are accurate.

## 7.1 Captions/titles/stickers

- [ ] Add actual caption rendering.
- [ ] Determine whether captions come from speech transcription, event labels, or both by inspecting Eklipse references.
- [ ] Match font, animation, placement and timing to target template.
- [ ] Add channel/title stickers only when wanted in the selected target style.

## 7.2 Memes/effects

- [ ] **SUGGESTED:** Add memes/effects only after you provide target Eklipse examples.
- [ ] **SUGGESTED:** Apply effects by event type, not randomly.
- [ ] **SUGGESTED:** Keep gameplay clear; effects should not obscure important UI/action.

## 7.3 Music/audio

Music support is already relatively developed in this branch; tune it after selection is measured.

- [x] Music selection system exists.
- [x] Drop detection exists.
- [x] Beat snapping exists.
- [x] Game/music mixing exists.
- [ ] Compare game-audio/music balance against actual Eklipse outputs.
- [ ] Keep elimination/ult/reaction sounds audible.
- [ ] Keep a no-music/raw mode for fair moment-selection evaluation.
- [ ] Use only music you have permission to publish.

**Exit gate:**

- [ ] Captions/overlays match target references closely.
- [ ] Audio comparisons do not clearly favor Eklipse.

---

## Phase 8 — Match workflow convenience

**Purpose:** Make the local tool feel as effortless as the target product.

- [ ] One-command VOD import/analyze workflow.
- [ ] Dashboard/review UI listing selected clips.
- [ ] Easy keep/reject/retrim controls.
- [ ] Side-by-side Eklipse/local evaluation mode during development.
- [ ] Quick vertical-template selection.
- [ ] Export-ready Shorts/Reels/TikTok presets.
- [ ] **SUGGESTED:** Scheduling/posting integration later.
- [ ] **SUGGESTED:** Voice-command/live marker support later.
- [ ] **SUGGESTED:** Mobile interface only after desktop/local quality is stable.

**Exit gate:**

- [ ] Normal usage no longer requires manual scripting or timestamp editing to get good outputs.

---

## Phase 9 — Lock quality with regression tests

**Purpose:** Ensure new improvements do not destroy previous wins.

- [ ] Freeze a representative benchmark set.
- [ ] Produce a parity report for every important detector/ranker/render change.
- [ ] Alert or fail regression checks when recall, precision, ranking or trim timing worsens.
- [ ] Keep visual comparison examples:
  - Eklipse output,
  - local output before a change,
  - local output after a change,
  - reason it improved or regressed.
- [ ] Retest after Overwatch HUD/game updates or new overlay styles.
- [ ] Claim near-Eklipse parity only after repeated benchmark proof.

---

# 6. Immediate Coding Queue: What to Implement First

## Sprint 1 — Measurement and detector architecture

- [ ] Create `eklipse-parity` branch from `MontageMAC`.
- [ ] Clean generated/debug output tracking.
- [ ] Create benchmark folder and Eklipse timestamp JSON format.
- [ ] Implement `automakeclip-parity` report generation.
- [ ] Add parity tests.
- [ ] Run current branch against initial paired reference VODs and record baseline metrics.
- [ ] Refactor `analysis.py` so metadata clips and non-metadata visual/audio candidates are combined.
- [ ] Save evidence explaining why each selected clip was chosen.

**Why Sprint 1 first:** You cannot improve toward 1:1 without measuring the gap, and the current metadata path can discard valid non-kill moments.

## Sprint 2 — Event coverage

- [ ] Add health, ultimate, objective and result-screen ROIs.
- [ ] Add event categories/evidence.
- [ ] Implement ultimate/team-fight candidates.
- [ ] Implement health/clutch candidates.
- [ ] Implement objective/victory context candidates.
- [ ] Re-run benchmark and tune based on exact misses/extras.

## Sprint 3 — Ranking and trim matching

- [ ] Tune event-specific pre-roll/post-roll.
- [ ] Correct final ranking based on reference order.
- [ ] Reduce generic filler.
- [ ] Add regression thresholds.

## Sprint 4 — Visual parity

- [ ] Provide target Eklipse layout exports/screenshots.
- [ ] Add matching layout presets.
- [ ] Match crop/HUD/facecam presentation.
- [ ] Add target caption/title behavior.
- [ ] Side-by-side visual validation.

## Sprint 5 — Workflow polish

- [ ] Improve review/edit interface.
- [ ] Add final export presets.
- [ ] Only then expand music polish, memes, posting/scheduling or voice commands.

---

# 7. Suggestions

## High-value suggestions

- [ ] Save signal/evidence details for each selected clip in `.plan.json`.
- [ ] Store mismatch reasons in benchmark results:
  - missed ultimate,
  - missed objective,
  - missed clutch,
  - weak motion false positive,
  - trim too early/late,
  - bad crop,
  - poor audio balance.
- [ ] Keep a raw detection comparison mode with no added edit polish.
- [ ] Keep a final edited comparison mode for visual/audio parity.
- [ ] Use different heroes/maps/modes in the benchmark to avoid overfitting.
- [ ] Warn when facecam/overlay blocks required Overwatch HUD regions.

## Later suggestions

- [ ] **SUGGESTED:** Train a lightweight ranking model from your accepted/rejected and Eklipse-matched clips after collecting enough examples.
- [ ] **SUGGESTED:** Add facecam detection and smart layout placement.
- [ ] **SUGGESTED:** Add speech/transcript reaction detection.
- [ ] **SUGGESTED:** Add scheduling/posting connections.
- [ ] **SUGGESTED:** Expand to other games only after Overwatch parity stabilizes.

## Avoid these mistakes

- [ ] Do not call the pipeline identical because one montage looked good.
- [ ] Do not spend the next development sprint mostly tuning music while moment selection remains unmeasured.
- [ ] Do not let kill metadata exclude ult/clutch/objective/fail candidates.
- [ ] Do not treat arbitrary HUD movement as proof of a highlight.
- [ ] Do not build large UI/publishing features before benchmark and detector quality.

---

# 8. How to Get Quality as Close as Possible to Eklipse.gg

## 8.1 Treat Eklipse as a black-box reference

You cannot honestly know or duplicate its private internals. You **can** reproduce visible behavior by using identical inputs and repeatedly measuring visible outputs.

For every comparison:

- [ ] Submit the exact same VOD to Eklipse and AutoMakeClip.
- [ ] Keep the same HUD/overlay/source quality conditions.
- [ ] Compare detected moments first.
- [ ] Compare final edited outputs second.

Separate these problems:

1. [ ] **Moment selection parity:** Did your tool find the same important clip?
2. [ ] **Editing/export parity:** Did it render that clip in the same quality/style?

## 8.2 Improve in the correct order

- [ ] Step 1: Moment recall — catch what Eklipse catches.
- [ ] Step 2: Precision — stop selecting weak clips Eklipse ignores.
- [ ] Step 3: Ranking — prioritize the same best moments.
- [ ] Step 4: Trim timing — cut setup/aftermath similarly.
- [ ] Step 5: Crop/layout — visually frame clips similarly.
- [ ] Step 6: Captions/titles/effects — match visible polish.
- [ ] Step 7: Audio/music — match finishing feel.
- [ ] Step 8: Workflow/export speed — match ease of use.

## 8.3 Use an error-driven development loop

After each parity report:

- [ ] Find the largest repeated failure category.
- [ ] Implement one targeted improvement.
- [ ] Rerun the entire benchmark.
- [ ] Keep the change only when total quality improves without breaking earlier cases.
- [ ] Log before/after metrics below.

Example:

```text
Failure: Local misses ult-based team fights without kills.
Fix: Add ult HUD transition + center-action + objective evidence candidate.
Result: Recall 68% -> 82%; precision 84% -> 78%.
Next fix: Require ult candidate to include objective or killfeed/audio support.
```

## 8.4 Acceptance test for near-identical visible quality

- [ ] Consistently selects the same strong moments across a multi-video benchmark.
- [ ] Rarely misses Eklipse-picked clips.
- [ ] Rarely selects clips you consider worse than Eklipse selections.
- [ ] Produces comparable trims and vertical layouts.
- [ ] Blind side-by-side tests often make it difficult to tell which finished clip is Eklipse versus local, or you prefer the local output.

---

# 9. Development Progress Log Template

Copy this block for each major implementation change.

## Change Entry

**Date:**  
**Branch/Commit:**  
**Task completed:**  
**Files changed:**  
**Reason for change:**  
**Benchmark videos used:**  

### Metrics

| Metric | Before | After | Improved? |
|---|---:|---:|---|
| Moment recall |  |  |  |
| Moment precision |  |  |  |
| Top-3 rank agreement |  |  |  |
| Mean start-time error |  |  |  |
| Mean end-time error |  |  |  |
| False-positive clips |  |  |  |
| Missed Eklipse moments |  |  |  |

### Visual/Audio Checks

- [ ] Crop/layout closer to Eklipse.
- [ ] Caption/title behavior closer.
- [ ] Music/game-audio balance closer.
- [ ] No new compression/render artifacts.
- [ ] No regression on previously good VODs.

**Keep this change?** Yes / No  
**Next largest gap:**  

---

# 10. Your Immediate Todo List

- [ ] Create a new `eklipse-parity` development branch from `MontageMAC`.
- [ ] Remove/ignore generated audio/debug artifacts.
- [ ] Add benchmark folder and JSON format.
- [ ] Choose your first 3 raw Overwatch VODs.
- [ ] Run those same 3 VODs through Eklipse.
- [ ] Record Eklipse timestamps, order and moment types.
- [ ] Save Eklipse vertical export examples you want to match.
- [ ] Run current AutoMakeClip against the same VODs and save baseline results.
- [ ] Implement parity report tool.
- [ ] Record baseline metrics.
- [ ] Refactor detector candidate fusion so metadata does not block non-kill moments.
- [ ] Rerun parity report.
- [ ] Begin health/ultimate/objective signal support.

---

# 11. Rule for Every Future Feature

Before implementing anything new, ask:

> Does this help AutoMakeClip choose, rank, trim, render or deliver clips more like the clips Eklipse produces on my real gameplay?

- [ ] If yes: implement it and measure the result.
- [ ] If it only adds polish while core highlight selection is still wrong: postpone it.
- [ ] If there is no way to compare it against a reference output: create the comparison first.

---

# 12. Audit Sources

## Repository files reviewed from `MontageMAC`

- `README.md`
- `src/automakeclip/analysis.py`
- `src/automakeclip/ffmpeg.py`
- `src/automakeclip/config.py`
- `src/automakeclip/selection.py`
- `src/automakeclip/memory.py`
- `src/automakeclip/review.py`
- `src/automakeclip/render.py`
- `tests/test_analysis.py`
- `tests/test_render_sync.py`
- `.gitignore`
- Branch diff relative to `main`

## Eklipse public target pages reviewed

- https://eklipse.gg/features/ai-highlights/
- https://eklipse.gg/help/best-practices-overwatch-2/

---

# Bottom Line

- [x] A serious automatic Overwatch clipping/montage foundation already exists in your branch.
- [ ] It is not yet proven to have Eklipse 1:1 output quality.
- [ ] The next required move is a paired Eklipse benchmark plus the candidate-fusion refactor.
- [ ] After that, implement real Overwatch event understanding: ultimates, health/clutches, objectives and match-result context.
- [ ] Only then prioritize Eklipse-like caption/template/publishing polish.
- [ ] The output becomes “near identical” only when repeated same-VOD comparisons show the gap is small and stable.
