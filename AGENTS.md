# MtoULiveLink development rules

This independent repository owns the MtoU Live Link product: a Maya component
and an Unreal Editor plugin that install separately and release together. It
can be cloned alone or mounted as a registered submodule in a collection. Run
Git and `gh` here for product work; the parent owns only its registration and
pinned commit. Do not change sibling projects.

## Contracts and layout

- Read CONTEXT.md for terminology and docs/adr/ for architecture decisions.
  [docs/workflow.md](docs/workflow.md) defines the design, fix,
  verification-scope and Issue/PR rules, and where work before 2026-10-30 is
  tracked.
- `maya/MtoULiveLink/` is the Maya component (`scripts/`, `install.mel`,
  `tests/`) and `unreal/MtoULiveLink/` the Unreal plugin (one `.uplugin`,
  `Source/` with its Automation tests). They cooperate only through the wire
  contract in `protocol/` (`protocol/README.md` and its corpus); neither
  loads source, configuration or runtime files from the other. `protocol/`,
  `docs/`, `tools/` and `tests/` are never installed.
- Maya: `MtoULiveLink.py` is the user-run entry; internals use snake_case
  modules with one `__version__`. Use Maya's bundled Python and Qt and guard
  version-specific APIs. Repeated launches must not duplicate windows or
  callbacks; closing removes the nodes, callbacks, scriptJobs and UI the tool
  created, and importing runs no Maya commands. `install.mel` stays ASCII
  (Maya 2022 misreads UTF-8 MEL) and handles Maya ignoring a `.mod` path with
  non-ASCII characters.
- Unreal: runtime behavior lives in the runtime module and editor-only
  behavior in the editor module; public headers expose intentional APIs only.
  Declare plugin and module dependencies in `.uplugin` and `.Build.cs`.
- Change user scenes and assets only as the product documents. Do not check in
  caches, `Binaries/`, `Intermediate/`, `Saved/`, `DerivedDataCache/`, `.vs/`,
  generated IDE projects, archives or production assets. Keep the GPL license
  and assess any third-party reuse separately.

## Documentation and delivery

- README.md and README_CN.md are aligned current manuals for first-time users:
  compatibility, installation, workflows, troubleshooting and limits. Update
  the affected section in place; leave out implementation, test counts and
  development narratives. Keep vocabulary in CONTEXT.md, decisions in
  docs/adr/, current scope, constraints and next-stage candidates in
  docs/roadmap.md, version changes in CHANGELOG.md and selected evidence in
  docs/history/ under its retention rules. Write documents, issues and
  comments as the current state; rewrite an outdated decision in place once
  the user agrees.
- Follow docs/workflow.md for Issue/PR work: `feature/<topic>` or
  `fix/<topic>` branches with a PR to `main`, `Refs #<issue>` (never closing
  keywords), full base/head SHAs, AC and R- finding IDs, a published review of
  the exact head SHA before every merge, `--match-head-commit` merges, and
  issue comments that each end with a `当前状态` footer, generated and checked
  with `tools/issue_report.py` for status comments. Small documentation and
  rule edits go on the current branch as docs/workflow.md defines.
- Never publish machine-specific paths, credentials or private asset contents
  in files, Issues or PRs; use placeholders such as `<Maya2024>`,
  `<Project.uproject>` or `<scratch>`. Report the state reached precisely:
  implemented, reviewed, merged, host-verified or released.
- Keep scratch under the actual Git root's parent `.tmp/`; fall back to the
  ignored `.tmp/` inside this repository. Remove task-owned scratch when it is
  no longer needed.

Commands run from the repository root. `<mayapy>` is Maya 2024
`bin/mayapy.exe`; also use Maya 2022.4 when a change touches Maya API usage or
Python syntax that 2022 could reject.

## Default checks

Run the suites of the hosts a change touches. Run both hosts when the protocol,
shared fixtures, or cross-host behavior changes.

| Scope | Command |
| --- | --- |
| Maya, pure Python | `python -m unittest discover -s maya/MtoULiveLink/tests -p "test_*.py"` |
| Maya, host | `<mayapy> -m unittest discover -s maya/MtoULiveLink/tests -p maya_host_tests.py` |
| Protocol corpus | `python protocol/generate_unreal_corpus.py --check` (when `protocol/` or the corpus changes) |
| Unreal | Build stock UE 5.7.4, then `UnrealEditor-Cmd <project> -ExecCmds="Automation RunTests MtoULiveLink; Quit" -unattended -nopause -nosplash -NullRHI -ReportExportPath=<scratch>` |
| Project files | `python tools/validate_project.py` and `python -m unittest discover -s tests` (tools, versions, protocol, CI or repository layout) |

`<project>` is a UE 5.7 project that loads `unreal/` through
`AdditionalPluginDirectories` or holds a copy in `Plugins/MtoULiveLink`.

## Opt-in tests

`MtoUOptIn.*` tests are excluded from the default run because they need a real
Maya process, a production fixture, saved production assets, or a long
measurement. Run only those the task affects. A test started without its
required arguments fails and its error names them.

| Test | Use |
| --- | --- |
| `MtoUOptIn.Actor.CharacterPartsAsset`, `.LoadedCharacterParts`, `.LoadedCharacterPartTransactions` | Split-character composition of a production Binding, and part transactions in a saved, loaded map |
| `MtoUOptIn.Source.MayaCacheReconnect`, `.MayaInteractivePlayback`, `.MayaCharacterPartsHost`, `.MayaLinkageHost` | Cross-host acceptance of cache, playback, character parts, and linkage |
| `MtoUOptIn.Editor.Preview.MayaRefreshPeer`, `.CharacterAcceptance` | Real Maya Preview refresh and complete-character acceptance |
| `MtoUOptIn.Editor.Linkage.ProductionSequence` | Linkage in a production Level Sequence copy through a real Maya GUI (`maya_gui_linkage_driver.py`): writer takeover, anchor world/screen, bone-rotation and Morph oracle, playback under lag and every exit. The oracle frames must show different keyed poses: their Maya times must not lie a whole number of `2 * maya.key_step` apart (the driver default `key_step` 42 suits oracle frames 300/900/1500). With a `secondary` block it accepts a two-object set: both targets at one anchor, each object's oracle, the set refusals, the injections (an incomplete group, and through the automation apply probe a delayed object and a display failing after staging; every display of both objects is recorded at each frame end until the set converges again, and each frame must show exactly the last confirmed group), removal from either host (scenarios `remove_secondary`, `ue_remove_secondary`, `destroy_secondary`), `composition_swaps` (full-body or parts replacements that must end the set), the `arms` scenario (after `realtime`; the fixture's `arms.chains` cut a synthetic arms target from the second object's full body, which replaces it, renegotiates only the arms' required bones and must show Maya's arm bones and Morphs; `arms.unstreamed` names bones it must not require) and, with `reference_probe`, a scene reference exported and imported in the set's common space. A scene with its own animation can drop `keyed_joints` and use `maya.morph_namespace`; pick oracle frames inside that animation and oracle bones the target meshes skin, since unskinned bones are not streamed. `sustained{seconds, delays_ms[]}` measures INT-10 after the first scenario's playback: for each delay the looping sequence plays `seconds` while every published group is held that long at the set completion barrier, then a pause and a jump must converge within 2 s; `sustained-<delay>ms.json` holds the scheduling record and percentiles. The scenarios `lifecycle` (with `lifecycle{cycles}`) and `lifecycle_check` count what Maya holds (OpenMaya callbacks at registration, scriptJobs, threads, sockets, MtoU nodes and windows) over connect, linkage, 结束预览 and disconnect cycles, a closed and reopened panel, a replaced scene, the panel closed or the scene replaced in the middle of linkage (Unreal must hand every writer back; with the panel closed Maya must restore time, range, gate, cameras and undo) and a restore that cannot delete the tool camera, and compare it with the warmed-up baseline; put `close_world` last so the final census after it is compared with the disconnected one |
| `MtoUOptIn.Editor.Workflow.GuiRegression` | Real-time, cached playback, Model/Refresh/garment comparison and Animation after Model through the same Maya GUI driver; start the editor on `/Engine/Maps/Entry` |
| `MtoUOptIn.Editor.Preview.RefreshBenchmark` | Preview refresh performance changes; `-MtoUBenchmarkCase=<case>` measures one case in a fresh process |

The two Maya GUI tests render the Binding Actor's Details at the fixture's
`details_widths` only when the editor can render: use `-RenderOffscreen`
instead of `-NullRHI`, plus `-nocef` for a production level whose content
starts the web browser (CEF asserts under the offscreen renderer). Every MtoU group is expanded and the render grows until
all rows fit, so collapsed groups and rows below the fold are checked too.

Both tests also fail on any Maya dialog the scenario did not expect. The driver
records each dialog with the stage (`stage` command) that was current; the test
lists what each stage may show: `BLENDSHAPE_MISMATCH` on every connect of the
fixture character, and `STREAM_INTERRUPTED` where the exit ends the session
(`destroy_target`, `close_world`, and Refresh Preview in `GuiRegression`). A new
expected dialog is added to that list, not skipped.

Maya's linkage throughput depends on the plug-ins its preferences load
automatically, so give both tests `maya.app_dir_template` instead of a reused
`maya.app_dir`. The test copies the template to `<evidence>/mayaapp` for every
run and records in its log the source, the SHA-256 and autoload count of the
`pluginPrefs.mel` in the running Maya's own preference folder, and the plug-ins
loaded during playback (`ProductionSequence` also in `ue-run.json` as
`maya_prefs`). The driver's `sample_plugins` command takes that sample a
quarter into `ProductionSequence` playback and during `GuiRegression` cached
playback; a later stage never replaces it. Build the templates once per machine outside the repository with
a host Python:

```powershell
python maya/MtoULiveLink/tests/maya_prefs_template.py --maya <Maya2024>/bin/maya.exe --kind minimal --out <scratch>/maya-prefs-minimal
```

`minimal` (only `modelingToolkit`) is the comparable baseline; `maya-default`
(the list a first Maya start saves) measures a default Maya install. A template
holds `pluginPrefs.mel` and a `userPrefs.mel` with only Maya's one-time
plug-in load markers; without those markers Maya loads its default set
whatever `pluginPrefs.mel` says. Compare throughput only between runs with the
same SHA-256; with `maya-default` the loaded count varies between runs because
Maya is still autoloading while it plays.

`maya/MtoULiveLink/tests/maya_gui_install_check.py`, run with a host Python,
checks the packaged `install.mel` in real Maya GUI sessions: upgrade from the
0.9.0 single file with a saved shelf button, Chinese and ASCII install paths,
reinstall, an update while the tool is open, a broken and a partial package,
missing parts, rollback and a clean install. Pass
`--old-entry` the 0.9.0 `MtoULiveLink.py` (`git show 6512f3e^:maya/MtoULiveLink/scripts/MtoULiveLink.py`).

`maya/MtoULiveLink/tests/maya_gui_window_check.py`, also run with a host
Python, closes the panel, the scene-reference and the 对象配对 windows from their title bars
(`WM_CLOSE`), repeats close and reopen, switches workflow and scenes, and
quits Maya in each state; a scenario passes only when Maya exits with code 0,
in time, without a crash log and without a Python traceback in its Script
Editor history. Run it after changing window or panel
lifecycle, once with the default quit and once with `--quit title-bar`
(closing Maya's main window). Pass `--app-dir-template` a Maya 2024 preference directory a person
has started Maya with once and whose first-start dialogs they answered; the
check copies it for every process and never answers those dialogs. Without
such a template, report it as a missing prerequisite instead of starting Maya
with an empty preference directory.

Before 2026-10-19 these tests used the `MtoULiveLink.` prefix with the same
remaining path, and the `MtoUOptIn.Actor.*` tests kept that prefix until
2026-10-28; acceptance records use the old names.

A test editor can run beside a person's own: `-MtoULiveLinkPort=<n>` moves
the Unreal listener off 54321, the two Maya GUI tests pass it to the Maya they
start, and `MTOU_LIVELINK_PORT` does the same for any other Maya.

mayapy has no idle loop: the tool queues its main-thread work in batch mode,
and a host loop (the `maya_*_peer.py` scripts) must call
`mayaapi.drain_deferred()` on its own thread.

## Iteration

- After a failure, rerun only the failing Unreal test (for example
  `Automation RunTests MtoULiveLink.Linkage.Session`) or Python module. Run each
  affected default suite once more after the change stabilizes.
- Inspect new Unreal warnings. A warning a test triggers on purpose is declared
  with `AddExpectedMessagePlain` so that only unexpected warnings appear in the
  report.
- Wait on the reply or state a test needs, not on a fixed read timeout per
  step; only a protocol window that is itself under test may set the duration.

## Versions

The product version (Maya `__version__`, Unreal `VersionName` and `Version`)
changes only in the release change, which sets it to the version being
released for both components together; other PRs leave it alone. It is
1.0.0.
The protocol version both hosts negotiate still changes with the wire
contract; the PR that changes it adds an `Upgrade notes` entry under
`Unreleased` instead of bumping the product version.

The release change sets the version with `python tools/version.py <version>
--apply`. `python tools/package.py --apply` writes `MtoULiveLink-<version>.zip`
(Maya) and `MtoULiveLink-<version>-UE<engine>.zip` (Unreal) to `releases/`;
verify an installation from those archives before publishing. Release tags use
`v<version>`. A collection updates its pin only to a published, reviewed
commit; it does not track the moving tip.

## Changelog

Write `CHANGELOG.md` for users upgrading. Every PR with a user-visible change
adds its entry under `## Unreleased`; the release change only renames that
heading to the version and date. Group each version's entries under
`### Upgrade notes`, `### Added`, `### Changed`, `### Fixed` and `### Removed`,
in that order, omitting empty groups. Upgrade notes say what the user must do:
update both hosts together, reinstall, redo a setting or replace a removed
control. Each entry is one or two lines in the panels' own terms and ends with
its issue:

```markdown
- Long root and outfit names no longer widen the Maya panel; the full name is in the tooltip. (#71)
```

Leave out implementation, review finding IDs, tests, tooling and prototypes;
the issue, PR and project history hold them. Rewriting a released version
in this format keeps what it shipped and adds nothing it did not ship.
