# Acceptance fixture templates

Placeholder templates of the fixtures in `<fixtures>`
([testing.md](../testing.md#acceptance-fixtures)). Copy one into
`<fixtures>`, replace every `<…>` with the project's own value and point
`maya.driver` and `maya.scripts` at the checkout under test.

| Template | Test | Placeholders |
| --- | --- | --- |
| [production-sequence.json](production-sequence.json) | `MtoUOptIn.Editor.Linkage.ProductionSequence`, one object | `<Level>`, `<MainSequence>`, `<ShotSequence>` (the same asset when the sequence binds its characters itself), `<OtherSequenceOfTheSameAct>` (for `switch_sequence`), `<MtoUBindingAsset>`, `<SequenceCharacterBinding>` (the character binding the Binding Actor replaces), `<MayaScene.ma>`, `<CharacterRootJoint>`, `<SkinnedJoint>` (skin-cluster influences that move between the oracle frames) and `<CharacterNamespace>` |
| [background-updates.json](background-updates.json) | Ordinary Editor background-update cadence and session exits | `<PrimarySkeletalMesh>`, `<MayaScene.ma>`, `<CharacterRootJoint>`, checkout and host paths, moving oracle bones and explicit measurement limits with their rationale |

The values that are not placeholders are the ones the last acceptance used.
`oracle_frames` must show different poses; `time_control.cache_range` is a
short capture inside the scene's animation. [testing.md](../testing.md#productionsequence)
explains every field and scenario.
