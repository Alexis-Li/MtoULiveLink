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
  generated IDE projects, archives or production assets. Keep the GPL license,
  `NOTICE` and the legal header in every shipped source file (ADR 0015), add no
  check that blocks use over them, and assess any third-party reuse separately.

## Session handoff

A session can end without warning: the user's usage allowance runs out, and
no session can see how much of it is left. Resuming or compacting a long
conversation afterwards is expensive. So the work is kept resumable at all
times by a fresh session that has only the repository and `HANDOFF.md`.

- Keep `HANDOFF.md` in the root of the working directory you are working in;
  each worktree has its own. Git ignores it: it is a note to the next
  session, not a project document. Knowledge that must last still goes to the
  Issue, docs/roadmap.md or an ADR.
- Update it at every checkpoint, without being asked and without waiting for
  the task to end: a step finished and verified, a decision made, an approach
  tried and ruled out, and before starting anything long (a full suite, a
  benchmark, a subagent, a review round). Do not try to guess when the
  allowance will run out; a note that is always current is the only
  protection.
- Save the work at the same checkpoints: commit verified work to the branch
  allowed by docs/workflow.md (the integration branch for small rule or
  documentation edits, the task branch for PR work), so the note points at a
  commit. Anything left uncommitted is listed
  in the note with its state.
- Write it in Chinese, as the current state, rewritten in place and never
  appended to. Keep it within about 60 lines. Sections:
  - `任务`: the goal, Issue number, branch, last commit SHA, update date.
  - `已完成`: what is done and how it was verified.
  - `进行中`: what is half-done, exactly where it stopped, uncommitted files.
  - `下一步`: ordered actions, concrete enough to start on directly.
  - `关键文件与决定`: the files that matter and why; decisions taken and
    approaches ruled out, each with its reason.
  - `未验证`: checks not yet run and assumptions not yet confirmed.

  Point to commits, Issues and documents; do not copy code, logs or anything
  the repository already records.
- At the start of a session, if `HANDOFF.md` exists, read it before anything
  else and compare it with `git status` and the recent log. Where they
  disagree, the repository is right: correct the note. Then continue from
  `下一步`; do not redo investigation the note already records.
- When the user asks to continue interrupted work, continue from
  `HANDOFF.md` in the current session; do not ask them to reopen the earlier
  conversation.
- When the task is delivered, delete the note or cut it down to the one line
  of state that still matters. A stale note misleads the next session.

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
  `fix/<topic>` branches cut from the integration branch in progress (`main`
  when none is) with a PR back to it, `Refs #<issue>` (never closing
  keywords), full base/head SHAs, AC and R- finding IDs, a published review of
  the exact head SHA before every merge, `--match-head-commit` merges, and
  issue comments that each end with a `当前状态` footer, generated and checked
  with `tools/issue_report.py` for status comments. A PR branch holds only
  its PR's change; small documentation and rule edits go directly on the
  integration branch as docs/workflow.md defines.
- Never publish machine-specific paths, credentials or private asset contents
  in files, Issues or PRs; use placeholders such as `<Maya2024>`,
  `<Project.uproject>` or `<scratch>`. Report the state reached precisely:
  implemented, reviewed, merged, host-verified or released.
- Keep scratch under the actual Git root's parent `.tmp/`; fall back to the
  ignored `.tmp/` inside this repository. Remove task-owned scratch when it is
  no longer needed.
- Opt-in acceptance fixtures (the JSON files the `MtoUOptIn.*` tests take, the
  Maya preference templates and their README) are shared test inputs, not
  scratch. Keep them in `<fixtures>`, the parent's `.tmp/mtou-acceptance-fixtures/`,
  and never remove them with task scratch or on issue closure. Fixtures are
  built when a change needs host acceptance that no existing fixture covers,
  not ahead of need: build the one the case requires in `<fixtures>` and add
  its placeholder template under `docs/acceptance-fixtures/` in the same
  change. Update a template when its fixture's fields change.

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
measurement. [docs/testing.md](docs/testing.md) lists them with their options
and procedures. Run only those the task affects.

- A test started without its required arguments fails and its error names them.
  A missing prerequisite (a real Maya, a fixture, a preference template) is
  reported as missing; never substitute a stand-in (such as an empty
  preference directory), or skip the test and claim it passed.
- The two Maya GUI tests (`MtoUOptIn.Editor.Linkage.ProductionSequence`,
  `MtoUOptIn.Editor.Workflow.GuiRegression`) fail on any Maya dialog the
  scenario did not expect. Add a new expected dialog to the test's per-stage
  list; do not skip it.
- Give them `maya.app_dir_template`, not a reused `maya.app_dir`, and compare
  throughput only between runs with the same `pluginPrefs.mel` SHA-256 (recorded
  in the test log).
- After changing window or panel lifecycle, run `maya_gui_window_check.py`
  twice: with the default quit and with `--quit title-bar`.
- A test editor running beside a person's own uses `-MtoULiveLinkPort=<n>`, so
  it never takes 54321; any other Maya used with that editor gets the same port
  through `MTOU_LIVELINK_PORT`.

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

Only the release change sets the product version; other PRs never change it.
See [Release](docs/workflow.md#release).

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
