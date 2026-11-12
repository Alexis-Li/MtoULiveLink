# Opt-in test procedure

The default checks and their commands are in [AGENTS.md](../AGENTS.md). This
document is the procedure for the opt-in tests and host checks that the default
run leaves out: what each one covers, the options it needs, and how to read its
result. The constraints that apply to every run are in AGENTS.md: run only
what a task affects, report a missing prerequisite, no unexpected Maya dialogs,
`maya.app_dir_template` with same-hash throughput comparisons, the window check
after panel lifecycle changes, and a separate port for a test editor beside a
person's own. [workflow.md](workflow.md) defines how much verification a change
needs.

Paths are placeholders: `<Maya2024>` is a Maya 2024 install, `<project>` a
UE 5.7 project that loads `unreal/`, `<scratch>` the task scratch directory and
`<fixtures>` the shared acceptance fixtures and `<evidence>` the `evidence`
directory a Maya GUI test's fixture names for its run records.

## Opt-in tests

`MtoUOptIn.*` tests are excluded from the default run because they need a real
Maya process, a production fixture, saved production assets, or a long
measurement. A test started without its required arguments fails and its error
names them.

Before 2026-10-19 these tests used the `MtoULiveLink.` prefix with the same
remaining path, and the `MtoUOptIn.Actor.*` tests kept that prefix until
2026-10-28; acceptance records use the old names.

| Test | Use |
| --- | --- |
| `MtoUOptIn.Actor.CharacterPartsAsset`, `.LoadedCharacterParts`, `.LoadedCharacterPartTransactions` | Split-character composition of a production Binding, and part transactions in a saved, loaded map |
| `MtoUOptIn.Source.MayaCacheReconnect`, `.MayaInteractivePlayback`, `.MayaCharacterPartsHost`, `.MayaLinkageHost` | Cross-host acceptance of cache, playback, character parts, and linkage |
| `MtoUOptIn.Editor.Preview.MayaRefreshPeer`, `.CharacterAcceptance` | Real Maya Preview refresh and complete-character acceptance |
| `MtoUOptIn.Editor.Linkage.ProductionSequence` | Linkage in a production Level Sequence copy through a real Maya GUI; see [ProductionSequence](#productionsequence) |
| `MtoUOptIn.Editor.Workflow.GuiRegression` | Real-time, cached playback, Model/Refresh/garment comparison and Animation after Model through the same Maya GUI driver; start the editor on `/Engine/Maps/Entry` |
| `MtoUOptIn.Editor.Preview.RefreshBenchmark` | Preview refresh performance changes; `-MtoUBenchmarkCase=<case>` measures one case in a fresh process |

Run one with `Automation RunTests <name>` in the Unreal command shown in
AGENTS.md.

### ProductionSequence

`MtoUOptIn.Editor.Linkage.ProductionSequence` drives a production Level
Sequence copy through a real Maya GUI (`maya_gui_linkage_driver.py`) and
checks writer takeover, anchor world and screen position, the bone-rotation
and Morph oracle, playback under lag, and every exit.

**Oracle frames.** The oracle frames must show different keyed poses: their
Maya times must not lie a whole number of `2 * maya.key_step` apart. The
driver default `key_step` of 42 suits oracle frames 300/900/1500.

**Scenes with their own animation.** Such a scene can drop `keyed_joints` and
use `maya.morph_namespace`. Pick oracle frames inside that animation and
oracle bones the target meshes skin, since unskinned bones are not streamed.

**Two-object set (`secondary`).** With a `secondary` block the test accepts a
set of two objects and checks:

- both targets at one anchor, and each object's oracle;
- the set refusals;
- the injections: an incomplete group, and, through the automation apply
  probe, a delayed object and a display that fails after staging. Every
  display of both objects is recorded at each frame end until the set
  converges again, and each frame must show exactly the last confirmed group;
- removal from either host, with the scenarios `remove_secondary`,
  `ue_remove_secondary` and `destroy_secondary`;
- `composition_swaps`: full-body or parts replacements that must end the set;
- the `arms` scenario, which runs after `realtime`. The fixture's
  `arms.chains` cut a synthetic arms target from the second object's full
  body, which replaces it, renegotiates only the arms' required bones and must
  show Maya's arm bones and Morphs. `arms.unstreamed` names bones it must not
  require.

**Sustained load (`sustained{seconds, delays_ms[]}`).** It measures INT-10
after the first scenario's playback. For each delay the looping sequence plays
`seconds` while every published group is held that long at the set completion
barrier; then a pause and a jump must converge within 2 s.
`sustained-<delay>ms.json` holds the scheduling record and percentiles.

**Lifecycle (`lifecycle`, `lifecycle_check`).** The scenarios `lifecycle`
(with `lifecycle{cycles}`) and `lifecycle_check` count what Maya holds
(OpenMaya callbacks at registration, scriptJobs, threads, sockets, MtoU nodes
and windows) over:

- connect, linkage, 结束预览 and disconnect cycles;
- a closed and reopened panel, and a replaced scene;
- the panel closed or the scene replaced in the middle of linkage. Unreal must
  hand every writer back; with the panel closed Maya must restore time, range,
  gate, cameras and undo;
- a restore that cannot delete the tool camera.

The counts are compared with the warmed-up baseline, and the role and the
pairing must stay set from one step to the next. Put `close_world` last so
the final census after it is compared with the disconnected one.

**Time control (`time_control{cache_range, live_frames?, paths?}`).** One
object only. Each path is a list of clicks: `maya_enter`, `ue_enter`
(Unreal's Details), `leave_realtime`, `leave_end`, `cached` (缓存播放, then
捕获并上传 `cache_range`) and `realtime`. Without `paths` it runs D-1 (进入联动,
返回实时, 缓存播放, 结束预览, 实时预览), D-2 (Unreal's 进入联动 during Cached
Playback) and a path through every mode control while 返回实时 keeps the
takeover. For every click the test:

- waits until the control is offered or withheld as the state allows, and
  fails if it never is. The rules are the
  [Mode transitions](../protocol/README.md#mode-transitions) table and the
  panel's own: no mode switch during linkage, 实时预览 stays offered in real
  time, and 缓存播放 is offered while 返回实时 keeps the takeover;
- clicks an offered control and waits until both hosts reach the state it
  leads to: who drives time, the takeover, the panel mode and the writers. An
  Unreal 进入联动 that is withheld is still requested, as a stale click would
  be, and must be refused with `CACHE_NOT_CLEARED` during a cache or
  `MODE_CONFLICT` during linkage, without borrowing a writer;
- then checks the preview: while Maya drives time, Unreal's Live Link subject
  must show Maya's pose at the next `live_frames` frame (default
  `oracle_frames`), and that pose must differ beyond `world_tolerance_cm` from
  the one the subject showed before, so a frozen preview cannot pass; linkage
  must converge on that sequence frame; a cache must be Ready with the
  captured range.

`cache_range` must be two increasing frames, and there must be at least two
live frames, each different from the next; a malformed `time_control` fails
the test with the reason. Every path must end in real time without a
takeover.

A new order of the existing clicks only needs a `paths` entry; a new control
or state extends the step vocabulary and the availability rules in
`MtoULinkageProductionTests.cpp`. This scenario replaces a manual D-1/D-2
reproduction in the real GUI.

## Test editor ports

A test editor can run beside a person's own. `-MtoULiveLinkPort=<n>` moves the
Unreal listener off 54321; the two Maya GUI tests pass it to the Maya they
start, and `MTOU_LIVELINK_PORT` does the same for any other Maya.

## Ordinary Editor background updates

`tools/background_updates_acceptance.py` runs a rendered `UnrealEditor.exe`
and real Maya GUI through the existing driver. Use a disposable host whose
Content contains copies of the fixture assets and whose
`AdditionalPluginDirectories` loads the checkout's `unreal/`. Enable LiveLink
and PythonScriptPlugin and build that host with stock UE 5.7.4 first. Keep the
production project and assets separate from the disposable host.

Copy [background-updates.json](acceptance-fixtures/background-updates.json)
to `<fixtures>` and fill its paths and assets. Its `display_bones` maps each
visible display mesh to moving, streamed bones that it skins. Every display
needs an oracle. `oracle_frames` must contain distinct poses inside the Maya
animation. `motion_frames` defines the consecutive frames used for the cadence
measurement. One primary mesh with moving streamed bones is sufficient. Add
`parts` only when those displays are needed and their assets are available.
`thresholds` requires explicit numeric limits and a `rationale` explaining their
AC04 and same-run reference basis before measurement. The template leaves the
numbers unset. `connected_min_hz` must exceed both the engine's 3 Hz throttle
cap and `baseline_max_hz`. `reference_ratio_min` expresses the accepted frame
cadence relative to the foreground reference. `producer_min_hz` establishes a
useful controlled input cadence. `pose_ratio_min` checks every display against
the slower of observed input and engine cadence, and against its same-run
foreground capture fraction. `recovery_ratio_max` bounds recovered cadence
relative to the measured disconnected baseline. Record the reason for each
tolerance and keep the same fixture and preference-template hash when comparing
runs. These limits belong to that measurement setup.

Run from the repository root with a system Python. The output directory must
be new. `--seconds` defaults to 12 and must be at least 8.

```powershell
python tools/background_updates_acceptance.py --editor '<UE>/Engine/Binaries/Win64/UnrealEditor.exe' --project '<scratch-host>/<Project.uproject>' --fixture '<fixtures>/background-updates.json' --evidence '<scratch>/background-exits' --port 55444
python tools/background_updates_acceptance.py --editor '<UE>/Engine/Binaries/Win64/UnrealEditor.exe' --project '<scratch-host>/<Project.uproject>' --fixture '<fixtures>/background-updates.json' --evidence '<scratch>/background-editor-exit' --port 55444 --scenario editor_exit
```

The launcher supplies the process-level background CPU preference override,
copies `maya.app_dir_template` for every peer and requests a non-activating
normal-size startup through Windows `STARTUPINFO`. Host-created windows can
still activate during startup, so arrange the run where those launches can
occur without interrupting desktop work. The tool writes the next required
foreground PID to `summary.json` and waits passively for it, with
`--foreground-timeout` defaulting to 90 seconds. It has no foreground-switching
or input-injection action. Each measured sample must name the required
foreground PID, and the Editor window must remain visible and restored.
The C++ snapshots also require matching real engine focus/foreground flags.
No Automation command or unattended flag runs in this editor.

The other GUI acceptance tests reuse file-driven Maya commands and hidden
process startup. Their offscreen Details rendering can run without occupying
the desktop. Ordinary background-update acceptance additionally requires the
real Maya-foreground and Editor-foreground reference stages. A hidden process
or an inactive private desktop alone does not establish those states. Arrange
actual foreground transitions externally, or validate a separate session's
foreground semantics before using it for this procedure. An unmet foreground
condition fails with partial evidence. The launcher leaves the current
desktop's focus selection to its operator.

`summary.json` records thresholds, the product SHA and input-file hashes, frame and pose-change Hz,
actual sample durations, producer acknowledgements, settings-file hashes,
stable-pose errors in cm and process exit codes. Per-stage files retain the
frame-deduplicated component transforms and C++ snapshots. Snapshots require
the ordinary-editor flags, unchanged enabled preference, the exact owned CPU
registration count, the expected throttle decision and viewport realtime
recovery. Exit code 0 and `ok=true` require all checks. A failed run exits 1
and records its error alongside the partial evidence. Inspect the editor and
Maya logs for startup or shutdown failures.

The default scenario disconnects and reconnects around a malformed protocol
frame, participating Actor deletion, world replacement, real Maya peer exit
and source removal through an empty Live Link preset. The second scenario
exits the Editor while connected. Its final internal cleanup is covered by the
default owner tests, since a terminated process cannot answer a snapshot.
The Maya driver validates every dialog against the stage's expected codes.

Inspect the visible viewport and minimize/restore behavior separately in the
real GUI and record the observation method. The scripted window flags establish
restored state during cadence measurement. They do not establish whether a
minimized viewport submits drawing work.

## Maya GUI tests

`ProductionSequence` and `GuiRegression` are the two Maya GUI tests.

### Rendering

They render the Binding Actor's Details at the fixture's `details_widths` only
when the editor can render: use `-RenderOffscreen` instead of `-NullRHI`, plus
`-nocef` for a production level whose content starts the web browser (CEF
asserts under the offscreen renderer). Every MtoU group is expanded and the
render grows until all rows fit, so collapsed groups and rows below the fold
are checked too.

### Expected dialogs

Both tests fail on any Maya dialog the scenario did not expect. The driver
records each dialog with the stage (`stage` command) that was current; the
test lists what each stage may show:

- `BLENDSHAPE_MISMATCH` on every connect of the fixture character;
- `STREAM_INTERRUPTED` where the exit ends the session: `destroy_target`,
  `close_world`, and Refresh Preview in `GuiRegression`.

A new expected dialog is added to that list, not skipped.

### Maya preferences

Maya's linkage throughput depends on the plug-ins its preferences load
automatically, so give both tests `maya.app_dir_template` instead of a reused
`maya.app_dir`. For every run the test copies the template to
`<evidence>/mayaapp` and records in its log:

- the source, the SHA-256 and the autoload count of the `pluginPrefs.mel` in
  the running Maya's own preference folder;
- the plug-ins loaded during playback (`ProductionSequence` also writes this
  to `ue-run.json` as `maya_prefs`).

The driver's `sample_plugins` command takes that sample a quarter into
`ProductionSequence` playback and during `GuiRegression` cached playback; a
later stage never replaces it.

Build the templates once per machine, outside the repository, with a host
Python:

```powershell
python maya/MtoULiveLink/tests/maya_prefs_template.py --maya <Maya2024>/bin/maya.exe --kind minimal --out <scratch>/maya-prefs-minimal
```

- `minimal` (only `modelingToolkit`) is the comparable baseline.
- `maya-default` (the list a first Maya start saves) measures a default Maya
  install.

A template holds `pluginPrefs.mel` and a `userPrefs.mel` with only Maya's
one-time plug-in load markers; without those markers Maya loads its default
set whatever `pluginPrefs.mel` says. Compare throughput only between runs with
the same SHA-256. With `maya-default` the loaded count varies between runs
because Maya is still autoloading while it plays.

## Maya GUI checks

Both scripts run with a host Python, not `mayapy`, and start real Maya GUI
sessions.

### Install check

`maya/MtoULiveLink/tests/maya_gui_install_check.py` checks the packaged
`install.mel` in real Maya GUI sessions:

- upgrade from the 0.9.0 single file with a saved shelf button;
- Chinese and ASCII install paths;
- reinstall, and an update while the tool is open;
- a broken and a partial package, and missing parts;
- rollback and a clean install.

Pass `--old-entry` the 0.9.0 `MtoULiveLink.py`, available with
`git show 6512f3e^:maya/MtoULiveLink/scripts/MtoULiveLink.py`. The script exits
1 and prints `FAIL <step>: ...` when a step misses its expected result;
`summary.json` keeps every step's observations and failures.

### Window check

`maya/MtoULiveLink/tests/maya_gui_window_check.py` closes the panel and
the 对象配对 window from their title bars (`WM_CLOSE`) and their own buttons,
repeats close and reopen, switches workflow, makes a new scene and opens a
scene, and quits Maya in each state. Throughout, an open panel must hold one
window, its scriptJob
and its two scene-replacement callbacks. A scenario passes only when Maya
exits with code 0, in time, without a crash log and without a Python traceback
in its Script Editor history.

Run it after changing window or panel lifecycle, once with the default quit
and once with `--quit title-bar` (closing Maya's main window).

Pass `--app-dir-template` a Maya 2024 preference directory that a person has
started Maya with once and whose first-start dialogs they answered; the check
copies it for every process and never answers those dialogs. Without such a
template, report it as a missing prerequisite instead of starting Maya with an
empty preference directory.

## Acceptance fixtures

The JSON files the `MtoUOptIn.*` tests take, the Maya preference templates and
their README are shared test inputs kept in `<fixtures>`, not scratch.

Fixtures are built when a change needs host acceptance that no existing
fixture covers, not ahead of need. That change builds the fixture the case
requires in `<fixtures>` and adds its placeholder template, with every machine
path and private asset replaced by a placeholder, under
`docs/acceptance-fixtures/` (created with the first template). A template is
updated when its fixture's fields change.

The [issue 54 acceptance record](history/issue-54-multi-subject-acceptance.md)
keeps the C02 fixture template of the removed multi-subject prototype with its
evidence; it is history, not a maintained template.

## mayapy and peers

mayapy has no idle loop: the tool queues its main-thread work in batch mode,
and a host loop (the `maya_*_peer.py` scripts) must call
`mayaapi.drain_deferred()` on its own thread.
