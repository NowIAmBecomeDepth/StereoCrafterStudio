# Studio v2 checks

Assertions about the running v2 app: most build the real QML window (or the
real Splatting controller and preview worker) offscreen, drive it, and ask
the live objects what happened. The unit tests are one level up
(`studio/tests/test_*.py`, run with `python -m unittest discover -s
studio/tests -t .`); these are the slower, whole-app checks that grew up
in session scratchpads while v2 was built, moved here on 2026-09-26.

```bash
./.venv/Scripts/python.exe studio/tests/checks/run_all.py
./.venv/Scripts/python.exe studio/tests/checks/run_all.py splat_editors fit
./.venv/Scripts/python.exe studio/tests/checks/run_all.py --onscreen titlebar
./.venv/Scripts/python.exe studio/tests/checks/run_all.py unit      # just the unit suite
```

With no filter, `run_all` also runs the unit suite (`studio/tests/test_*.py`,
the generator sync tests included) as one more job; `--no-unit` leaves it out.

Each script also runs on its own and prints one line per assertion. Exit
code 0 = every assertion held AND the real `last_jobs.json` is unchanged
(or still absent: a missing `last_jobs.json` is fine).

A check's own temp folder (its `tmp = mkdtemp()`, with the fixture copies in
it) is deleted after the check exits, by a detached helper that waits for the
process to end (`_fixtures.exit_now`). Set `STUDIO_CHECKS_KEEP_TEMP=1` to keep
it and look at what a check wrote.

To make a stage look busy, use `_fixtures.fake_busy(ctl, True)` -- it sets the
flag and emits `stateChanged`, as a real job does for everything watching.

## They never touch your settings or your clips

- Before anything is built, each check points `studio.settings_store.CONFIG_DIR`
  and `core.ui.job_handoff._RECORD_PATH` at a temp dir, and asserts at the end
  that the real `last_jobs.json` is byte-identical. A v2 check once let a fake
  DepthCrafter batch finish and overwrote the real handoff record — keep this
  up in any new check. v1's settings are READ (to seed v2's), never written.
- The Splatting and Merging preview checks run on synthetic clips from
  `_fixtures.py`, never real footage: v1's preview code auto-saves sidecars
  next to whatever it loads.
  The fixtures are built with ffmpeg on first use into
  `%TEMP%/studio_check_fixtures/` and every check gets its own copy.

## The GPU

The Splatting preview checks load torch and render on CUDA. Don't run them
while a real GPU job is going.

## What is here

| check | pins down |
|---|---|
| `check_fit.py` (+ `measure_v2_width.py`) | the window fits each stage the way v1 does; no clipping; buttons don't overlap; UI scale 0.85; a height-only drag is the user's (kept, grown only where a page needs more) |
| `check_hover_zones.py` | a setting's help shows only over its label's text or its control, not on the empty rest of the row's line (rows and checkboxes) |
| `check_hovertip.py` | v1-style hover tooltips |
| `check_stages_fake_run.py` | DepthCrafter + Inpainting controllers through their real worker paths with fake engines; menus; field rejection; handoff recorded to temp only |
| `check_splat_preview_engine.py` | the generated v1 preview engine over the fixture: load, views, seek, clip nav |
| `check_splat_preview_sidecar.py` | every Border mode × clip with / without a sidecar |
| `check_splat_preview_3b.py` | sidecar create/update, v1's traces, rescan, ≈, playback pacing/loop, SINGLE render, Splat Test PNGs, auto-converge |
| `check_splat_framebreak_multimap.py` | Frame Break letterbox presets, Multi-Map |
| `check_splat_keyframes_menus.py` | keyframes, sidecar moves, Help, Fusion error path, encoding choices |
| `check_splat_keyed_dials.py` | Splatting's dials follow the keyframes as the Depth Map Editor's do: the ramped value between keys (the setting left alone), frame by frame while playing; turned on a keyframe it takes the value (sidecar too), off one nothing changes and a hint says so once |
| `check_splat_single.py` | a real SINGLE render of the clip on screen, preview restored after |
| `check_splat_editors.py` | Hole Shift through the preview worker, sidecar round trip |
| `check_splat_editors_qml.py` | Hole Shift by mouse in the real app; Edit Depth Map in Vertical Warp Fix's old place |
| `check_splat_detect_text.py` | Detect Text (subtitle mask) by mouse and keys: rectangle, zoom, Save → mask video + sidecar, cancel; cross-window shortcuts |
| `check_splat_fast_decode.py` | the persistent-ffmpeg decode is bit-exact vs v1's per-frame decode (HD 10-bit fixture) |
| `check_splat_fast_depth.py` | GPU depth pre-processing within 1 unit of v1's CPU path (the GPU path is what every render uses since 2026-09-29; compared against process_depth_batch_cpu) |
| `check_inpainting_v2.py` | Inpainting V2 (Wan VACE): fit, MMGP rule, Start by mouse with fakes, validation, the V1/V2 guard |
| `check_merging.py` | Merging: fit with every option open, show/hide rules, presets, Reset, a real CPU merge by mouse, the early return |
| `check_merging_preview.py` | the Merging preview (`merge` fixture): load by mouse, Clip Info, a slider re-render, views, keys, playback, next clip, released for Start and reloaded, fits 1080p at 0.85, writes nothing |
| `check_merging_phase4.py` | Save Preview Frame / as SBS, the Grain Match Preview (refusal, pictures, σ, scan, scrub, sliders, Auto-Match, σ→Amount, Save to Sidecar, close), Process Current Clip, Move to Finish (question, then the move) |
| `check_merging_flip.py` | Merging's preview flip card (click the picture: it turns over edge-on, preview_source swaps between the Front / Back dropdowns' sources, lands flat on the new frame; a drag still pans; not before a load) and the mask dials (3 in Mask Processing, 5 in the chevron Shadow fold, remembered) |
| `check_preview_autofit.py` | Live preview auto-fit (v1 preview_autofit) on Merging and Splatting: on load Preview Scale is the largest 1% step that fits the frame (10..100%), a taller window refits larger, a hand pick stands until the next resize |
| `check_preview_autofit_first_load.py` | The first preview after a fresh start is fitted, not left at the saved 100%: a synthetic 1920x1080 clip, the load's end delayed past its first frame (the race that left it unfitted until a double-click) |
| `check_job_stats.py` | Live job numbers: chunk count == Inpainting's loop; step bars give DC's window + s/it (it/s turned round), one sample per step; Inpainting's chunk hook reports and restores (and steps aside when it cannot hook); the rows on DC / Inpainting; the VRAM pill publishes only on change; tabs numbered 1-4, Extra not |
| `check_scenecut.py` | Scene Cut (Extra): the engine loads on first view, each detector's own fields, fit, and a REAL AutoShot run by mouse over a synthetic three-shot clip (CSV, 3 split clips, v1's Done! + Success, handoff to temp), the model cache + Release GPU |
| `check_clip_select.py` | Upcoming Jobs' tick boxes (Splatting, Merging): the box is its own click, Select All / Deselect All, Process Selected runs the ticked clips only (real runs), Move to Finished moves what a batch would and the clips leave the list |
| `check_queue_row_menu.py` | Upcoming Jobs' right-click (2026-10-10): Splat This Clip Now (Single on that clip, loaded first) and Rerun in DepthCrafter (its input the clip, its output the map's folder, the page shown); Merge This Clip Now and Move to Finished on one clip; the preview card turned over by dragging an edge (past halfway turns, short of it falls back); the Play items (the default player stood in for) |
| `check_dc_inp_queue.py` | Upcoming Jobs on DepthCrafter and Inpainting V1 (2026-10-10), the models stood in for: the list right of the settings and as tall, Process Selected and Process / Inpaint This Clip Now run only those clips, Move to Finished (Inpainting: with its .spsidecar and Hi-Res splat), the batch's moved clips leave the list, Play Source Clip / Lo-Res / Hi-Res, Post-Processing's checkboxes in one column |
| `check_console_window.py` | the console is its own tabbed window (v1's): opens on the stage's tab, each stage's lines on its tab and All, shared code follows the running job, close hides, size and text size kept |
| `check_depthedit.py` | the Depth Map Editor (Extra), end to end without SAM 3 as v1's verify_depthedit does: Start Tracking on every page, one window size across pages, depth auto-pairing, Load Clip by mouse, every view, a slider edit + right-click reset, Render by mouse (bit-exact outside the mask, +20% inside, project files, handoff), v1's Replace / Delete questions asked in the page; the Layers list keeps its scroll, a new layer scrolled into view |
| `check_depthedit_sam_settings.py` | SAM 3 set up: the Depth Map Editor's SAM 3 section hidden, its settings (RAM frames, Unload, Uninstall) in Settings; Uninstall bins the checkpoints and the section comes back. Temp dummy weights, the Recycle Bin faked |
| `check_depthedit_3d.py` | the Depth Map Editor's 3D view: through Splatting's own preview (no edit == Splatting's Dubois frame from disk), an edit changes it, Right Eye + Holes, a note for a clip Splatting doesn't hold (needs CUDA) |
| `check_depthedit_despeck.py` | The Depth Map Editor's Despeck layer (2026-10-12): on a synthetic clip of two boxes with a speck on each left edge, the covered speck comes down to its box on every frame, the other and everything else untouched, Opacity half way; Specks tints them orange (covered) and cyan (not), in RGB too, and off restores the picture; the page fits. No SAM 3 (`test_despeck.py`: the speck finder itself) |
| `check_depthedit_edges.py` | The Depth Map Editor's one-sided edge moves (Top edge / Bottom edge): on a synthetic beam, -10 pulls only the top in, +8 pushes only the top out, the other edge and everything outside the mask untouched; old projects load at 0 |
- `check_depthedit_dials.py` -- the editor's controls are dials; Top/Bottom edge, Grow, Feather and Smooth read in VIDEO pixels (half-pixel steps) and store fractional depth-map pixels; a fraction renders between its whole neighbours; reset; readouts. No SAM 3, no GPU.
| `check_update_dialog.py` | Settings > Studio > Check for Updates: asks for the repo, lists new commits, refuses over uncommitted changes or a running job, Install starts the helper (recorded, never run) and closes Studio; the result toasts once at the next start |
| `check_film_grain.py` | the window's film grain: each theme's saved grain (defaults where none, none at 0), one grain per screen pixel, the Splatting picture kept clean, the theme popout's Film Grain switch |
| `check_merging_mixed_heights.py` | a real Merging batch over 16:9 and 2.39:1 clips: each output at its own height (Full SBS and Double SBS) -- every clip after the first used to take the first clip's height |
| `check_run_outcomes.py` | How each stage's run ends (audit #3), through its real Start: a model that never loads, every clip failing, an empty folder, a partial run and a clean one -- the toast (error / warning "N done, M failed" / success) and whether the handoff is recorded, on DepthCrafter, Splatting (real core batch), Inpainting V1 / V2, Merging (real CPU merges) and Scene Cut (a real AutoShot run, splits failed by a stand-in ffmpeg). Plus Inpainting V2's own handoff and Merging's "Import from Inpainting V2" button by mouse (fills Inpainted / Mask / Original). `check_run_outcomes.py dc sp` runs only some |
| `check_partial_outputs.py` | No partial output under a final name (audit #5): Splatting, Merging and Scene Cut each run cleanly, then again with a Stop / failure mid-clip -- the previous outputs byte-identical, no `.part` / `.partial` left, Merging's batch goes on past the failed clip ("1 done, 1 failed"). `check_partial_outputs.py sp` runs only some |
| `check_scenecut_rerun.py` | Scene Cut re-run into the same folder (audit #11): a clip cut into three scenes, then re-cut into two -- the old `-Scene-003` goes to the Recycle Bin (stubbed), the Success message says so, look-alike files and other clips' scenes untouched |
| `check_depthedit_keep_edits.py` | Depth Map Editor keeps unsaved layers (audit #12): Edit Depth Map on another clip, Load Clip, and closing each save the open clip's project first; reopening brings the layers back; an untouched clip writes no project file |
| `check_stop_prompt.py` | Stop and Quit ask first (audit #13): every stage's Stop offers Stop now / its safe stop (after this clip / chunk / segment / these scenes; Enter picks it) / Keep running and each answer reaches the right stop; Splatting for real (Keep running carries on, the safe choice arms Stop After Clip, Stop now ends the run, no partial); Quit during a real Merging run asks (Quit now / Keep running) and Quit now ends the job and its ffmpeg at once. `check_stop_prompt.py sp q` runs only some |
| `check_submask_during_job.py` | Detect Text open through a Splatting batch (audit #29): the batch moves both source clips to finished/ (the editor lets go of its clip when the job starts) and the editor shows frames again afterwards, read from finished/ |
| `check_quit_with_tool_window.py` | Closing the main window quits Studio even with Detect Text open and saying "rendering" (Qt's quit asks every window first; one refusing kept Studio running hidden); a window hidden after a Save no longer says "rendering". |
| `check_studio_tooltips.py` | No QML uses Qt's attached ToolTip (black, unwrapped, could run off the window); hovering -- never clicking -- the chapter picker and DepthCrafter's Start shows Studio's HoverTip, themed, at most 340 px wide, inside the window. |
| `check_popup_grain.py` | The window's film grain on every popup card (PopupGrain): nothing built for a closed popup; Settings, the theme popout, a prompt and a dropdown list show it while open; Film Grain off removes it. |
| `check_import_project.py` | Import buttons and the picked project: Merging's Import from Inpainting fills Inpainted, Original, Mask and Output from the job's own chapter (not an earlier stage's other project); a job from another project is not offered. |
| `check_shortcuts.py` | The app-wide shortcuts pressed for real: Ctrl+1-5 stages, Ctrl+P the project list (Esc closes), Ctrl+= / Ctrl+- UI Scale within 75-100%, F1 the manual at the stage showing (the browser is stubbed), Settings > Keyboard Shortcuts lists them. |

The fixtures: `run_all` builds every kind (`small`, `hd`, `merge`) before the checks start; `_fixtures.path()` builds into a private folder and renames it into place, so checks run by hand side by side can't build over each other (audit #30, `test_check_fixtures.py`).
| `check_stop_after_clip.py` | Stop After Clip on Splatting (real render), Inpainting V1 / V2 (model stood in for) and Merging (real CPU merge): hidden when idle; shown beside Stop while a folder runs and fitting the action bar (fitted and minimum width); clicked by mouse mid clip 1 -> clip 1 finishes, clip 2 never starts, "<stage> stopped after the clip" / "1 done", handed on; a second click carries on (2 done); disarmed after. `check_stop_after_clip.py sp` runs only some |
| `check_gpu_handover.py` | The GPU hand-over (audit #14 / #15): a claimant's job waits until Splatting's preview has REALLY released -- with a render in flight, with the first load still importing, with a failed preview job ahead of the release; Scene Cut and the Depth Map Editor (Track / Find) claim and are refused while another stage renders; "Segmenting" counts as busy |
| `check_splat_resplat.py` | two real SINGLE runs of one clip: its H / L bars start empty each time and fill as the run renders (a re-splat used to leave them full) |
| `check_splat_depthedit_roundtrip.py` | Splatting's Edit Depth opens the Depth Map Editor on the clip showing; a render asks "Back to Splatting?"; Use in Splatting swaps the map in (original kept as edit_history/<name>.original.mp4), returns and reloads the clip; Edit Depth again reads the kept original; a second trip keeps the first edit as edit-01 and makes no other folder |
| `check_tips_fit.py` | Every stage's typed tips fit their line at the stage's fitted width (none cut off) |
| `check_beginner.py` | Beginner Mode (switch by the gear): v1's rules hide / rename / pin on every stage and come back, originals restored and persisted, the V2 sub-tab, Merging's Auto-match grain switch, plain hover text, re-pin on activation, refused while running, no stage taller |
| `check_drop_zones.py` | Drag and drop onto the path boxes: real drag events from a TEMP copy of the fixtures on every stage (DepthCrafter, Splatting, Inpainting V1 and V2, Merging, Edge Fill, Scene Cut, the Depth Map Editor's clip and Output) and Encoding Options' Merge All. The zones show over the section without changing its height and hide after; a drop sets the box; a drop the box can't take leaves it; while a dialog is open only its zones show. No GPU. `DZ_GRAB=<dir>` saves each mid-drag window |
| `check_merge_all.py` | Merging's Merge All SBS to 1 Video on a made-up project: the button left of View Output Folder, clear of the other buttons at three widths; the dialog lists the scenes with no merged output and the stage each needs; Send to Splatting loads that scene there and opens Splatting; Merge Anyway writes the whole movie (black for the missing scenes, at their length) with Merging busy; all merged: Merge offered; Encoding Options' Merge All tab |
| `check_merging_queue.py` | Merging Upcoming Jobs: under Options, a row per clip (suffix trimmed), Batch Progress above it and gone from Clip Info; idle click loads the clip; running: per-row bars (done / render % / waiting), clicks ignored; one clip without its left eye is left out (a warning), the rest load; picking it is refused |
| `check_merging_split_format.py` | Merging Output Format "Split L/R Output": listed, hides the Split L/R / Delete SBS boxes, the engine is handed Full SBS + both on, the setting stays itself, the tooltip explains it |
| `check_change_folders.py` | Change Folders / Hide Folders on all four stages: on Manual paths too (remembered in studio.json), and a picked chapter still folds the fields by itself |
| `check_chapter_paths.py` | Chapter inputs follow the clips down re-run "finished" folders (nearest level holding clips, deeper on a tie; empty levels skipped; outputs untouched); same_place keeps a moved chapter field in chapter mode |
| `check_splat_dials.py` | Splatting knobs: the named sliders are DialFields, a double-click resets to the default, Border Settings hides / shows Border Width and Bias (values kept) and is remembered |
| `check_splat_presets.py` | Splatting's Low Res presets: DepthCrafter's preset button, a column per shape at 480p / 720p / 1080p (multiples of 64) plus `config/splatting_presets.json`'s own; picking fills Width / Height; DepthCrafter's button unchanged |
| `check_project_picker.py` | The caption's project button: Settings' Projects Folder listed in the popup (natural order, shows marked, current marked); a click switches the project and the caption; the Settings row |
| `check_spin_dropdown.py` | SpinField emptied / below `from` and left shows (and writes) the clamped setting; Dropdown marks no row for a stored value its list lacks |
| `check_dropdown_list_width.py` | A dropdown's open list is as wide as its longest option however narrow the box (Splatting's Front at 130 px): every row's text fits, the list stays inside the window; a box wide enough keeps a list of its own width |
| `check_prompt_queue.py` | a question asked while another prompt is open waits its turn; both askers are answered, in order |
| `check_settings_dialog.py` | Settings in place of the menu bar: no strip, the gear left of the VRAM pill opens it; a swatch per palette switches the theme; Studio / GPU / stage sections are the old menus as rows (switch flips the setting, a button closes Settings then acts); the stage section follows the stage; dials grey on dark palettes; Save All Settings writes every stage's settings; Release GPU Memory lets go of Splatting's preview |
| `check_tips.py` | Every tip in one list (tips.js): each stage's tip fader has its own tips, the new ones too; Settings > Tips, after Keyboard Shortcuts, shows every tip once under its stage's heading, in pipeline order |
| `check_reopen_tool.py` | Studio run twice on one temp config: closed in the Depth Map Editor, in Scene Cut or on Splatting, it reopens there (the Extra tab's tool kept, not just the tab) |
| `check_glass_opacity.py` | Settings > Glass Opacity chips: Auto + 90..30%, a pick reaches Theme.groundKeep at once and is saved, stale values snap, Auto = the palette default |
| `check_calm.py` | While a job runs the window redraws only a few times a second (Theme.calm: stepped spinner / indeterminate bar, no sheen) -- continuous redraws cost Inpainting 8% on the shared GPU; idle ~0/s, busy under 15/s, uncalmed far more |
| `check_gpu_jobs.py` | v1's request_job (gpu_jobs.py): refused while another stage renders (v1's text); granted, every other GPU holder released (not the claimant, not its V1/V2 sibling); every real Start claims after its validation; a real Splatting preview lets go and comes back after the job; a refused Merging start gives its preview back |
| `check_section_headers.py` | No section header runs past its section's edge at each stage's minimum width and at 1900 px, with a chapter picked and the Import buttons showing (the chapter dropdown gives way) |
| `check_section_outlines.py` | View > Show Section Outlines: on by default; every Section / FoldSection / preview panel 1px in the palette's v1 outline colour (True Black grey, Graphite white); off draws none; saved; back on |

Not run by `run_all`:

- `onscreen/` — real windows on your screen (`--onscreen` runs the
  `check_*` ones): `check_titlebar.py` (custom title bar hit-testing,
| `onscreen/check_glass_refit.py` | The acrylic is re-seated (Backdrop.refresh: accent off and on) once a resize settles -- once per drag, for height too, never with glass off. Its snapshot is sized when set; grown past it, the window showed stretched pixels. Programmatic, no capture |
  maximise/restore), `probe_glass.py` (window styles/backdrop dump),
  `grab_stage.py` (our window rendered by Qt, `grabWindow`) and
  `grab_glass.py` (the glass needs the screen's pixels: it refuses to run
  without `STUDIO_ALLOW_SCREEN_GRAB=1`, set only with the user's OK). Both read
  a COPY of your `config`, removed when they exit.
- `measure_v1_fit.py` (v1's fitted window size per stage) and
  `profile_splat_preview.py` (per-stage timings of a preview render) —
  measurements, not pass/fail.

Old scratchpad names, for reading older handoff notes: `check_v2b` →
`check_stages_fake_run`, `check_4a` → `check_splat_keyframes_menus`,
`check_4b` → `check_splat_editors`, `check_4b_qml` →
`check_splat_editors_qml`, `check_4c` → `check_splat_detect_text`,
`check_fbmm` → `check_splat_framebreak_multimap`, `check_vwf_row` →
`check_splat_vwf_row` (removed with Vertical Warp Fix, 2026-09-28), `check_preview_*` / `check_fast_*` / `check_single`
gained a `splat_` prefix, `profile_preview` → `profile_splat_preview`.
