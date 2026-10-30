# MtoU Live Link history

Start with the [product manual](../../README.md) for current use and the
[changelog](../../CHANGELOG.md) for version changes. Records below describe
their named acceptance baselines; their test results do not automatically apply
to later code.

Records written before 2026-10-30 come from the
[collection repository](https://github.com/Alexis-Li/Alexis_Script_Plugin_Package),
where this product lived at `composite/MtoULiveLink/`, now this repository's
root. Their commands, paths, commit SHAs and bare issue numbers refer to that
repository: `docs/project-history/mtou-livelink/` is this directory,
`unreal/ToolsLab.uproject` is the collection's Unreal host project and
`tools/package_*.py` became `tools/package.py`.

## Milestones and current work

| Milestone | Accepted scope and source |
| --- | --- |
| 0.4.0, 2026-09-03 | Integrated through [PR #36](https://github.com/Alexis-Li/Alexis_Script_Plugin_Package/pull/36), followed by owner C01/Topia verification. See [stock-engine acceptance](stock-engine-acceptance.md) and [Topia acceptance](topia-engine-acceptance.md). |
| 0.5.0, 2026-09-22 | Owner accepted practical use; tag `v0.5.0`, implementation checkpoint `278e16a001037a08933e8f180c82899a2e567e8e`. No installation packages or additional host-version certification were produced. |
| Preview workflow, 2026-10-13 | [Parent acceptance #47](issue-47-preview-workflow-acceptance.md) covers four delivered workflows and three bounded prototypes on `codex/mtou-preview-workflow`; prototype integration remains a separate task. |
| Preview workflow integration, 2026-10-29 | [INT-01–14 acceptance](preview-workflow-integration-acceptance.md) maps the #56 joint criteria to the I-1, I-2 and I-3 baselines accepted on `codex/mtou-preview-workflow`, merged into `main` through [PR #93](https://github.com/Alexis-Li/Alexis_Script_Plugin_Package/pull/93) at `9830f80ffdc511cc23b4224d0e1f1b54bf876ad3` (2026-10-30). |
| 1.0.0, 2026-10-30 | First release from this repository: the preview workflow integration above, protocol v11, tag `v1.0.0`. The three bounded prototypes were removed from the source tree; their records below link their last commit. |

Current scope, constraints and next-stage candidates are in the
[roadmap](../roadmap.md).

## Architecture and host baselines

| Topic | Record |
| --- | --- |
| Architecture through the 0.4.0 baseline | [Architecture](architecture.md) |
| Stock Unreal production validation | [Stock-engine acceptance](stock-engine-acceptance.md) |
| Topia build and editor loading | [Topia acceptance](topia-engine-acceptance.md) |

## Character mapping and composition

| Topic | Record |
| --- | --- |
| Complete Maya hierarchy and tiny-scale handling (#44) | [Complete-skeleton acceptance](complete-skeleton-acceptance.md) |
| Importer hash-suffix mapping (#46) | [Hash-suffix mapping acceptance](hash-suffix-mapping-acceptance.md) |
| Primary Driver and additional parts (#45) | [Character-part acceptance](character-parts-acceptance.md) |
| Required-bone mapping with real C01 inputs (#55) | [C01 acceptance](issue-55-real-c01-acceptance.md) |

## Preview generation and quality

| Topic | Record |
| --- | --- |
| Transient geometry and lifecycle (#11) | [Skin Preview acceptance](transient-skin-preview-acceptance.md) |
| Cross-topology Morph transfer (#12) | [Morph Transfer acceptance](preview-morph-transfer-acceptance.md) |
| Garment-only quality thresholds (#13) | [Quality calibration](model-preview-quality-calibration.md) |
| Full-character thresholds (#22) | [Full-character recalibration](model-preview-full-character-recalibration.md) |
| Refresh optimization (#34) | [Optimization](preview-refresh-optimization.md) |
| Geometry-scale performance limits (#41) | [Scale measurements](preview-refresh-geometry-scale.md) |

## Playback and editor workflow

| Topic | Record |
| --- | --- |
| Cache session identity and reconnect (#37) | [Reconnect acceptance](cached-playback-reconnect-acceptance.md) |
| Cached exit and explicit refresh (#38–39) | [Exit/refresh acceptance](cache-exit-and-refresh-review-acceptance.md) |
| Unreal Details and diagnostics (#48) | [Details acceptance](issue-48-ue-details-acceptance.md) |
| Range capture and UE-owned playback (#49) | [Cache workflow acceptance](issue-49-cache-workflow-acceptance.md) |
| Pause, seek and loop (#50) | [Interactive playback acceptance](issue-50-interactive-playback-acceptance.md) |
| Original/Generated garment comparison (#51) | [Comparison acceptance](issue-51-garment-comparison-acceptance.md) |

## Bounded prototypes

The [parent acceptance](issue-47-preview-workflow-acceptance.md) owns the
combined #47 conclusion. Each prototype record owns its scope, limits and
evidence; follow those links for raw host results and reproduction inputs.
The prototype sources were removed for 1.0.0 and remain at
[their last commit](https://github.com/Alexis-Li/MtoULiveLink/tree/caf7d45880e0872adfca8f0c179c35e453ae0be0/prototypes).

| Topic | Record |
| --- | --- |
| Official capability sources, 2026-09-22 | [Linkage research](preview-linkage-research.md) |
| Joint integration design (#56) | [Integration plan](preview-workflow-integration-plan.md) |
| UE camera and Sequencer to Maya (#52) | [Camera acceptance](issue-52-camera-sync-acceptance.md) |
| UE level geometry reference in Maya (#53) | [Scene-reference acceptance](issue-53-scene-reference-acceptance.md) |
| Independent prop and second character (#54) | [Multi-subject acceptance](issue-54-multi-subject-acceptance.md) |

## Source preservation

These archives preserve original comments for provenance; current status lives
in the tracker and accepted conclusions in the topic records above.

- [#52 source comments](issue-52-comment-archive.json)
- [#53 source comments](issue-53-comment-archive.json)
- [#54 source comments](issue-54-comment-archive.json)
- [#56 source comments](issue-56-comment-archive.json)
- [#57 source comments](issue-57-comment-archive.json)
- [#58 source comments](issue-58-comment-archive.json)
- [#59 source comments](issue-59-comment-archive.json)
- [#91 source comments](issue-91-comment-archive.json)

Earlier per-change chronology remains in
[the Git version before index consolidation](https://github.com/Alexis-Li/Alexis_Script_Plugin_Package/blob/b0f9f3530def60b3d837d7af07aff766789285c8/docs/project-history/mtou-livelink/README.md).

## Retention

Keep here only what stays useful after a task: important design trade-offs,
migration constraints, calibration and acceptance evidence that maintained
code, tests, documentation or the tracker cannot recover. Requirements and
status belong in Issues, delivery and routine results in PRs and comments,
current usage in the README pair and version changes in the changelog.

- Before adding a record, name the future decision or check that needs it and
  extend an existing topic where possible. Use purpose-based file names and
  keep existing ones that published links use.
- Put the conclusion, accepted scope and material limits first, then the
  baseline, environment and input identity, evidence source and reproduction
  entry. Keep implementation, review, integration and host acceptance distinct.
- Retain raw evidence only when it supports a result that would otherwise be
  lost, and state each file's purpose in its record. Routine logs, debugging
  rounds and repeated reports add no record.
- Keep a historical conclusion tied to its baseline; a later acceptance updates
  the topic without presenting old evidence as a new run. Comment archives stay
  available as source artifacts for published references.

Comment consolidation and scratch cleanup follow the
[workflow](../workflow.md#closure-and-requested-archival).
