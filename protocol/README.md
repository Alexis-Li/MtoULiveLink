# MtoU Live Link wire protocol

Protocol version: **11**. This document is the complete wire contract the Maya
component and the Unreal plugin implement; neither host reads the other's
source, configuration or runtime files. Its executable form is
[`conformance.json`](conformance.json), which both hosts' test suites run.

| File | Role |
| --- | --- |
| `README.md` | This contract: transport, identities, messages, workflows, limits and rejection codes |
| `conformance.json` | Language-neutral accepted and rejected cases plus the `limits` block both hosts assert their constants against |
| `generate_unreal_corpus.py` | Validates the corpus and writes the Unreal test-only projection `unreal/MtoULiveLink/Source/MtoULiveLink/Private/Tests/MtoUConformanceCorpus.inl`; `--check` fails when the projection is stale |

None of these files is installed. Earlier protocol versions are kept only in
Git history.

## Version pairing

- Both hosts declare `version: 11`: Maya's `PROTOCOL_VERSION` and the Unreal
  plugin report the same value.
- The Maya client opens with `hello`. A peer that cannot answer with the same
  version returns `error` with `code: "PROTOCOL_VERSION_MISMATCH"` and closes
  the connection **before** it discovers a target, borrows a camera, mutes a
  writer or reads a handoff directory. The same code answers any message whose
  `version` differs, including an `init` from an earlier version.
- A mismatched pair never partially succeeds: no partial target takeover, no
  half-imported reference, no cached pose left behind. Users therefore install
  both components from one release.

## Transport and framing

- TCP on `127.0.0.1`, port 54321. Unreal listens; Maya connects. The test
  switch `-MtoULiveLinkPort=<n>` moves the Unreal listener and
  `MTOU_LIVELINK_PORT` moves the Maya client.
- One client at a time: a second connection is answered with
  `SECOND_CLIENT_REJECTED` and closed.
- Every message is one UTF-8 JSON object preceded by its byte length as an
  unsigned 64-bit big-endian integer. The length counts the JSON only.
- Every message has a string `type`. Receivers ignore unknown fields, so
  additive diagnostic fields need no new version; any structural or semantic
  change does.
- Numbers must be finite. Transforms are
  `[tx, ty, tz, qx, qy, qz, qw, sx, sy, sz]`: translation in centimetres, a
  normalised rotation quaternion and scale, already converted to Unreal axes
  (see [Space](#space)). A root bone carries its world transform relative to the
  Maya origin; every other bone is relative to its transmitted parent.

## Limits

These budgets are frozen; the `limits` block of the corpus is their single
source of truth and both hosts assert their constants against it. A budget
changes only with a recorded reason, never to make a failing run pass.

| Limit | Value | Enforced where |
| --- | --- | --- |
| `max_message_bytes` | 33,554,432 (32 MiB) | both hosts at the length header, before any JSON parse or allocation; a longer frame closes the connection |
| `max_linkage_group_bytes` | 33,554,432 (32 MiB) measured over the **whole** group message, not per object | Unreal rejects an oversized group with `RESOURCE_LIMIT` and keeps the last complete display |
| `max_control_queue` | 16 undelivered control messages per direction | both hosts; overflow is an error, never an unbounded backlog |
| `max_set_objects` | 2 (one primary character + one independent object) | both hosts at negotiation |
| `max_cache_frame_count` | 20,000 frames | Maya before capture, Unreal at `cache_begin` |
| `max_cache_payload_bytes` | 1,073,741,824 (1 GiB) of encoded `cache_frame` JSON | Maya during capture, Unreal metered at the framing boundary |
| `max_reference_bytes` | 268,435,456 (256 MiB) | Maya importer before staging |
| `max_reference_objects` | 20,000 imported roots | Maya importer before staging |
| `max_reference_triangles` | 20,000,000 | Maya importer before staging |

Unreal also holds fixed budgets that are not wire values: at most one linkage
target in flight plus one replaceable pending target, 1,536 MiB of predicted
parsed cache memory (preflighted from the `init` transform and curve
counts before allocation), a captured cache rate of 1–60 fps, and a bounded
intake queue between its network worker and game thread that applies TCP
backpressure instead of growing. There is no fixed bone or curve count.

## Roles and authority

- **Maya** owns the object scenes, the evaluated pose sample and its own
  timeline. It never commands Unreal time or camera.
- **Unreal** owns the targets, their writers, the Level Sequence time, the
  evaluated camera, the cache playback clock and the output gate. It never
  edits Maya scene data.
- Exactly one host controls time in each mode. `mode` is the authority
  statement; both hosts display it and refuse work that contradicts it. The
  whole object set follows that one authority.

## Identity

| Field | Owner | Meaning |
| --- | --- | --- |
| `revision` | Maya | the character snapshot revision `init` establishes and `ready` echoes; cache uploads must name it |
| `session_id` | Unreal | positive integer for one TCP connection; every later linkage, removal and reference message repeats it |
| `set_revision` | Maya | the object set's revision: 1 for the set a connection negotiates, +1 for every accepted removal |
| `set_objects` | Maya | the ordered object ids of the negotiated set, primary first |
| `object_id` | both | stable object identifier, never a display name, bone short name or list position: `character` for the primary character, `secondary` for the independent object |
| `upload_id`, `play_id`, `clear_id` | Maya (`upload_id`, `clear_id`), Unreal (`play_id`) | positive, increasing per connection; every cache outcome echoes the identity it belongs to, and reused or older identities are refused or ignored. A new connection starts again from 1 |
| `mode_revision` | Unreal | increments per accepted mode transition |
| `space_revision` | Unreal | binds the common anchor, unit and mapping; a reference and a pose group must match the current one |
| `eval_id` | Unreal | opaque evaluation identity: time plus cut plus camera content, at 1/1000 tick precision, plus the editor's discrete-event count, so a pause or edit that keeps the time names new work |
| `transport_serial` | Unreal | monotonically increasing send counter; ordering only |
| `sample_serial` | Maya | increments per new sample of one `eval_id`; a paused edit produces a new sample |
| `frame_serial` | Unreal | counts publications of one connection, including heartbeats |

A message naming an old session, set, space, mode, cancelled target or repeated
publication is refused with its specific code and changes no host state.

## Connection and negotiation

1. Maya sends `hello` with its scene units, rate and capabilities. Unreal
   answers `hello_ack` with the `session_id`, its capabilities, the explicitly
   paired targets and the common space.
2. Maya sends one `init` per object in `set_objects` order. Each carries the
   selected `workflow` (`animation` or `model`), whether BlendShapes are
   transmitted, the parent-first bone list with bind-local transforms and the
   ordered curve (BlendShape) names.
3. Unreal resolves the placed Binding Actor, the workflow's readiness and the
   skeleton and Morph pairing, then answers each `init` with `ready` (accepted
   counts, missing names on either side, bone name remaps) or with an `error`
   that closes the connection: a failed object ends the whole set. Streaming
   starts only after every object of the set is ready.

`blendshapes_enabled: false` still sends the complete `curves` manifest, while
`ready.accepted_morph_count` and the published curve list stay empty. In the
Model workflow the accepted Morph set is the intersection of the Maya manifest
with the union of Morph names on the Generated Preview, the Primary Driver and
the enabled Additional Parts, so a name only the Driver or a part owns is
accepted and drives that mesh
([ADR 0011](../docs/adr/0011-activate-only-current-outfit-morphs.md)). A
current outfit that declares no BlendShapes negotiates with an empty accepted
Morph set. With BlendShape transfer enabled, a non-empty manifest whose
intersection with that union is empty is refused with
`PREVIEW_MORPH_MISMATCH`, and a partial intersection is accepted with a
warning.

With one object, a single placed Binding Actor needs no pairing; more than one
needs an explicit pairing (`MULTIPLE_BINDING_ACTORS`) and none is
`NO_BINDING_ACTOR`. Model additionally needs a ready generated preview
(`PREVIEW_NOT_READY`).

## Realtime preview

`frame` carries one evaluated sample at one Maya time: `transforms` for every
bone of that object's `init` list and `curves` for every name of its `init`
curve list, each complete and in the `init` order. The accepted bone and Morph
sets, the bone remaps and a disabled BlendShape transfer only decide what
Unreal publishes from those lists: Maya never trims or reorders them, and
`blendshapes_enabled: false` keeps the full curve count on the wire. The same
rule holds for every object row of a `frame` or `group` and for `cache_frame`.
A two-object set adds `objects[{object_id, transforms, curves}]` with the other
object's row in the same message. The transform and curve counts must equal
the object's `init` counts; a wrong count or transform width, or an unnormalised
quaternion, is a structural error that closes the connection. A frame with a
non-finite value or a number outside the Unreal float range, or one naming an
object the set no longer holds, is dropped, the exact bone or curve is
reported and the connection stays open.

## Object set

- A connection negotiates its whole set at the start. Every `init` repeats the
  same `set_objects`, `set_revision: 1` and `space_revision`. The first object
  is always `character`; a second one is always `secondary`.
- Each object is paired with its own placed Binding Actor, negotiates its own
  skeleton and Morph set and is published as its own Live Link subject
  (`MtoU_Character` for the primary, `MtoU_Secondary` for the second object).
  Same-name bones and Morph Targets of two objects never route to each other;
  Additional Parts belong to the character composition of their own Binding.
- A target serves one object (`TARGET_ALREADY_PAIRED`), and the second object
  always needs an explicit pairing (`PAIRING_REQUIRED`).
- Both targets share one placement (see [Space](#space)); otherwise the second
  object is refused with `ANCHOR_MISMATCH`.
- A two-object set is an Animation workflow set. The Model workflow and Cached
  Playback keep the single-character contract and refuse a two-object set with
  `OBJECT_SET_UNSUPPORTED` (Model at negotiation, Cached Playback at
  `cache_enter`, which does not close the connection).
- Adding an object, removing the primary, a destroyed target, a changed
  skeleton, Binding or composition, or a replaced object (for example
  full-body for arms) ends the whole connection; the user connects again with
  the new set. Only the explicit removal of the second object rebuilds the set
  inside the connection.

### Removing the second object

The removal is a set transition inside one connection, in any mode:

1. The side that removes closes the old set's submission: Maya stops sending
   frames and groups of set N once it sends `remove_object`; Unreal refuses any
   later group of set N with `SET_REVISION_STALE` (scheduling, not a conflict)
   and cancels a published group that is not confirmed yet, returning the
   primary to its last complete display.
2. Unreal restores the removed object: while it holds the object's writers it
   hands back exactly that target's writers and re-evaluates the sequence; it
   removes the object's subject so its displays leave the stream.
3. Unreal raises its set revision to N + 1 and answers `object_removed` N + 1.
   When the Unreal user removed the object, the same `object_removed` is
   unsolicited and Maya adopts it.
4. Maya adopts set N + 1 with the primary only. In linkage Unreal issues the
   current target again so the next group already belongs to the new set; the
   primary's takeover is never interrupted.

A removal that cannot restore the object ends the whole connection with
`RECOVERY_REFUSED` and keeps the residual in both panels; no new set is
confirmed. `remove_object` naming `character`, an object outside the set, or a
revision other than current + 1 is refused (`INVALID_MESSAGE`,
`SET_REVISION_STALE`) and changes nothing.

## Cached playback

Cached Playback separates transfer from playback; the decision and its
playback-segment semantics are in
[ADR 0013](../docs/adr/0013-upload-the-cache-before-local-replay.md).

1. `cache_enter` gives the connection to the cache: Unreal holds the current
   pose and drops live frames.
2. Maya captures an inclusive integer range at a rate frozen at capture start,
   then uploads it without a real-time deadline: `cache_begin` declares
   `upload_id`, `revision`, `fps`, `start_frame`, `end_frame`, `frame_count` and
   `payload_size` (the exact encoded byte count of all `cache_frame` messages);
   `cache_frame` messages carry a zero-based `index` with `transforms` and
   `curves`; `cache_end` finishes the upload.
3. Unreal validates the whole upload atomically and answers `cache_ready`. A
   partial or rejected upload is discarded entirely and never becomes playable;
   cache validation errors keep the connection.
4. The user plays, pauses, resumes, seeks, loops and stops in Unreal. Unreal
   applies at most one pose per game-thread update on its own monotonic clock,
   never bursts overdue frames, and stops with `CACHED_PLAYBACK_PERFORMANCE`
   instead of skipping or stretching. It reports `cache_playing` before an
   attempt's progress, pause, seek, loop, completion or stop, and every later
   outcome carries the same `play_id`.
5. `cache_clear` with a new `clear_id` releases the cache; Unreal echoes the
   identities it dropped in `cache_cleared`, and Maya waits for that exact
   acknowledgement before it samples a new capture or returns to realtime.

Cache errors are routed by identity like the other outcomes. Only a
recoverable cache error that belongs to an existing upload or play attempt
carries an identity, and only a positive one:

- a refused `cache_begin` with a valid `upload_id` (`CACHE_METADATA_INVALID`,
  `CACHE_PAYLOAD_TOO_LARGE`, `CACHE_REVISION_MISMATCH`) and the semantic
  `cache_frame` and `cache_end` errors of the upload being received carry that
  `upload_id`; after a refused `cache_begin`, the frames and end of that upload
  are dropped without further answers;
- a refused play start and `CACHED_PLAYBACK_PERFORMANCE` carry the attempt's
  `play_id` and the `upload_id` of the cache it plays;
- a refused pause, resume or seek carries the active `play_id` when an attempt
  exists; `cache_stop` is never refused;
- a `cache_clear` with an old or repeated `clear_id` is refused with
  `CACHE_METADATA_INVALID` and carries **no** identity, because it belongs to
  no current operation (only an accepted clear reports identities, in
  `cache_cleared`).

Structural cache errors, such as a `cache_begin` without a valid `upload_id`
or a `cache_frame` without an integer `index`, are `INVALID_MESSAGE` without an
identity and close the connection. While Maya uploads it acts only on an error
whose `upload_id` equals the current upload; while a cache is ready, playing or
stopping only on one whose `play_id` equals the current attempt; in its other
cache states it acts on no cache error. Any other cache error, with an older
identity or none, is ignored, so a stale `cache_clear` refusal arriving during
a new upload never fails or clears that upload.

`cache_complete.scope` is `cache` only when one unbroken segment applied every
pose once, in order, from the first frame within the duration bound; after a
seek or a completed loop round it is `segment` with that segment's range and
count. Unreal still accepts `cache_play` and `cache_stop` from Maya; the
product drives playback from the Unreal Binding Actor and Maya does not send
them.

## Camera linkage

### Mode transitions

| From | To | Preconditions | Failure and recovery |
| --- | --- | --- | --- |
| realtime | linkage | every object of the set paired and writer-isolatable, both targets at one placement; Maya has no cached playback active; either host may start it | Maya keeps realtime, Unreal restores every writer it took; both report the code (see below) |
| cached | linkage | the user cleared the cache and Maya saw the matching `cache_cleared` | no new mode; the old cache is never revived (`CACHE_NOT_CLEARED`) |
| linkage | realtime | linkage active | Maya restores time/view/gate and stays paused; Unreal keeps its takeover of every object; a failed restore ends the session and reports the residual |
| linkage | end | linkage active or already left | every borrowed resource of every object is restored per resource, idempotently; the original writers return; an exit that already happened is confirmed with `mode_state`, not refused |
| linkage | realtime | the takeover's Level Sequence was closed or switched, its world closed, a paired target destroyed, or the targets no longer share one placement | Unreal restores the writers, drops the last synchronisation and sends `mode_state` realtime at a new revision with `TARGET_UNKNOWN` (`ANCHOR_MISMATCH` for a moved target); Maya restores itself |
| any | disconnected | socket loss | gate first, then restore; no automatic reconnect |

`mode_revision` increments on every accepted transition and is echoed by both
sides. During a transition the pose gate is closed: no `group` and no `applied`
is accepted for the previous revision.

### Starting and ending from either host

- Unreal starts: it takes every target of the set over, sends `mode_state`
  linkage at the new revision N and keeps the pose gate closed until Maya
  answers `mode` linkage N `ready`, which Unreal echoes with `mode_state`
  linkage N. Maya refuses with `mode` realtime N `failed` and Unreal restores
  its writers at once; without an answer Unreal fails the transition after
  5 s, and Maya rolls back when the echo does not arrive within 5 s. A target
  that cannot be taken over hands back every target taken before it; no
  object is left half-entered.
- Maya starts: its `mode` linkage revision must be newer than every revision of
  the connection. Unreal confirms with `mode_state` linkage N; a refusal is a
  `mode_state` realtime at Unreal's current revision, and Maya restores itself
  and adopts that revision. Without a confirmation within 5 s Maya restores
  itself and sends `mode` realtime N `failed`.
- Unreal leaves: `mode_state` realtime at a newer revision. Maya restores its
  time, view and gate, stays paused and does not answer with `leave_linkage`.
- A `mode_state` is followed only for the connection that received it and only
  when it names that connection's `session_id`. Once linkage is confirmed, a
  realtime statement at the same or an older revision is stale and ignored; it
  rolls Maya back only while Maya's entry is still unconfirmed.

### Targets, groups and application

Unreal sends `camera_time` for the target it wants answered; heartbeats repeat
an unchanged target. Maya applies the time and camera, samples every object of
the set at that time and answers one `group` naming the target's `eval_id`.
`group.objects` carries one row per object of the current set, each with the
complete transform and curve lists of that object's `init`, in its order. A
partial set is never sent; a duplicated or unknown `object_id` is
`INVALID_MESSAGE`.

Unreal publishes a group only when every display of every object of the set
(each primary display and each enabled Additional Part) can show it. Every
object's subject receives the group under one generation; once all of them
hold it, every display evaluates it into a scratch pose and verifies its bones
and Morph values against its own object; only when all displays hold the group
do they commit it together in one game-thread step before the frame renders.
While linkage owns time the displays change only through this barrier.
`applied` reports the outcome for the **whole set** with one component row per
object; `false` names the missing or disagreeing display, or a subject that did
not receive the group within 2 s, and then no display of any object has
changed. A refused, failed or cancelled group returns every object to the last
complete group and never replaces the reported latest complete application.
One group is published at a time.

### Scheduling (Unreal side)

- `desired` is the newest target the editor wants displayed, `issued` is the
  target Maya is answering, `applied` is the last group whose components were
  read back on the game thread.
- At most one `issued` and one replaceable `desired`. Continuous playback
  replaces only the pending target; an advancing `desired` never cancels an
  `issued` target. Pause, cut, camera edit and explicit discontinuity (a jump or
  scrub, also within one shot, and a loop wrap) cancel the issued and the waiting
  target and issue the new one at once. The editor marks such a target as
  discontinuous on a pause (even when the time stays), any change while the
  sequence is not playing, a backward step, a step during playback that the
  elapsed playback time does not explain, and an edit of the evaluated camera,
  its actor or the open sequence. Such an event cancels the issued target even
  when it names the same `eval_id`, and the target is issued again as new work.
  Before a published group is resolved Unreal asks the editor for the current
  target, so an event that happened after the publication cancels the group
  before any display commits it; such a group is never confirmed. An ordinary
  camera move along continuous time does not cancel the target.
- A `group` whose `eval_id` is no longer current is refused with `EVAL_STALE`
  and changes nothing; the last complete display stays.
- After the last desired target, Unreal must observe an applied group within
  2 s and only then report 已同步; otherwise it reports 未收敛 with the last
  target and the elapsed time.

### Time mapping

```
maya_time = maya_origin + (ue_display_frame - playback_range.start)
            * maya_fps / display_rate
```

`display_rate` is a rational (`numerator`/`denominator`); frames are never
rounded to integers. `ue_display_frame` is always the root Level Sequence's
time; a subsequence contributes no second offset. `maya_origin` is captured when
linkage starts, so returning to realtime restores the frame the artist had.

## Space

- Common space: centimetres, `C(x, y, z) = (x, z, y)` from Maya to Unreal, with
  one static anchor `A` (translation and rotation, unit scale). `A = identity`
  is the realtime and cached workflow without a paired target.
- `A` is the placement of the primary target. Every object of the set is
  published in that one anchor: the second target must stand at the same
  placement (translation within 0.01 cm, rotation within 0.01°), so each
  object's Maya root world pose, which already holds its own placement and any
  reference-parent motion, reaches the Unreal world exactly once as
  `A * maya_world_pose`. A target parented at a socket is refused
  (`MOVING_SOCKET`); a target placed elsewhere is refused (`ANCHOR_MISMATCH`),
  at negotiation and at the linkage entry, and a target moved while linkage
  holds it ends the linkage.
- Camera and reference geometry travel back through the inverse mapping; the
  camera basis is transferred as basis vectors, never as Euler angles.
  `camera_time.camera` is expressed relative to the static anchor in Unreal
  axes, and `gate.pixel_aspect_ratio` is the shape of one pixel (1 for Unreal),
  never the image aspect.
- `space_revision` changes when `A`, the unit or the mapping changes. A
  reference or an in-flight group from an older revision is refused
  (`SPACE_REVISION_STALE`); re-labelling an old reference is not an update.

## Scene reference handoff

Unreal exports an explicit level scope with its FBX level exporter into a local
handoff directory holding the FBX file and a `mtou-scene-ref-manifest/1`
manifest of the engine-evaluated world transforms, then sends
`reference_offer` with the directory, the manifest digest, the scope and its
completeness, the `space_revision` and the counts. The user imports it in Maya;
Maya answers `reference_result`, and `reference_removed` when the user removes
the container.

- Maya checks the digest (`REFERENCE_DIGEST_MISMATCH`), refuses image data in
  the delivered files or inside the FBX (`REFERENCE_IMAGE_DATA`) and enforces
  the reference budgets (`REFERENCE_OVER_BUDGET`) before it touches the scene.
  Material assignments and external image path references are allowed.
- Maya stages the import in its own namespace, verifies it against the
  manifest and only then lets it replace the previous tool-owned container,
  whose ownership mark it checks (`CONTAINER_NOT_OWNED`). A failure before the
  takeover restores the previous container (`RECOVERY_REFUSED` when it cannot).
- Reference geometry is delivered in Unreal world space. The manifest's
  optional `world.anchor` (`location_cm[3]`, `rotation_deg{roll, pitch, yaw}`)
  is the primary target's placement when the export ran, identity when nothing
  was paired, and the digest covers it. Maya places the container by the
  inverse anchor so it shares the frame of the objects and the camera; a
  manifest without `world.anchor` means identity. A target moved after the
  export, or a changed `space_revision`, needs a new export and import: an
  imported container keeps the anchor it was placed with and records it. When
  a linkage `mode_state` names an anchor that differs from the recorded one
  (translation above 0.01 cm or rotation above 0.01°), or the container records
  none, Maya fails the transition with `REFERENCE_STALE` and keeps the old
  reference; with no tool-owned container there is nothing to check.

## Message reference

### Maya → Unreal

| `type` | Payload | Notes |
| --- | --- | --- |
| `hello` | `version`, `host`, `maya_version`, `scene{linear_unit, unit_scale_cm, fps}`, `capabilities{linkage, reference, cache, model}` | first message of a connection |
| `init` | `version`, `revision`, `workflow`, `blendshapes_enabled`, `bones[[name, parent_index, transform]]` parent first (root parent `-1`), `curves[name]`, `object_id`, `set_revision`, `space_revision`, `set_objects` | one per object of the set, in `set_objects` order; an existing pairing must name the placed Binding Actor |
| `frame` | `transforms`, `curves` for the primary, plus `objects[{object_id, transforms, curves}]` for the other object of a two-object set | one realtime sample of the whole set at one Maya time |
| `cache_enter` | — | the connection enters cached ownership before capture |
| `cache_begin` | `upload_id`, `revision`, `fps`, `start_frame`, `end_frame`, `frame_count`, `payload_size` | starts one upload |
| `cache_frame` | `index`, `transforms`, `curves` | one captured pose, zero-based index |
| `cache_end` | — | finishes the upload |
| `cache_play` | `play_id` | accepted; not sent by the product |
| `cache_stop` | — | accepted; not sent by the product |
| `cache_clear` | `clear_id` | releases the cache; answered by `cache_cleared` |
| `mode` | `session_id`, `mode_revision`, `mode`, `control`, `transition`, `detail` | Maya's authority statement after a transition; `transition` is `ready` or `failed` |
| `group` | `session_id`, `set_revision`, `mode_revision`, `space_revision`, `eval_id`, `transport_serial`, `sample_serial`, `objects[{object_id, transforms, curves}]`, `camera{...}`, `time{maya_frame, ue_display_frame}` | one complete sample of the whole set answering one `camera_time` target |
| `leave_linkage` | `session_id`, `mode_revision`, `target` (`realtime` or `end`), `detail` | Maya confirms it restored its own state |
| `remove_object` | `session_id`, `object_id`, `set_revision`, `detail` | the user removes the second object; `set_revision` is the new set's revision (current + 1) and `object_id` must be `secondary` |
| `reference_result` | `session_id`, `transfer_id`, `accepted`, `detail`, `counts{objects, triangles, bytes}`, `container` | outcome of a local handoff import, swap or removal |
| `reference_removed` | `session_id`, `detail` | the user removed the tool-owned container |
| `reference_query` | `session_id`, `transfer_id`, `scope` | reserved: Maya may ask for a handoff of a scope; the product ships the one-way flow (Unreal exports and offers) and does not send it |

### Unreal → Maya

| `type` | Payload | Notes |
| --- | --- | --- |
| `hello_ack` | `version`, `session_id`, `capabilities`, `targets[{object_id, kind, binding_path, generation}]`, `space{space_revision, anchor}` | `targets` lists the explicitly paired targets, primary first; empty until the user pairs |
| `ready` | `revision`, `workflow`, `missing_in_unreal`, `missing_in_maya`, `bone_name_remaps`, `target_morph_count`, `accepted_morph_count`, `session_id`, `object_id`, `set_revision`, `space_revision` | negotiation result for one object |
| `error` | `code`, `message`, `details`; `upload_id` and/or `play_id` for a cache error | a stable code from the table below; text is diagnostic only. A recoverable cache error carries the positive identity of the upload or play attempt it belongs to; each field is omitted when the error has none, including a refused stale `cache_clear` and structural errors (see [Cached playback](#cached-playback)) |
| `cache_ready` | `upload_id`, `revision`, `frame_count` | the complete upload was accepted |
| `cache_playing` | `upload_id`, `play_id`, `loop` | an attempt started; precedes its other outcomes |
| `cache_progress` | `play_id`, `applied` | bounded progress of applied poses |
| `cache_paused`, `cache_resumed` | `play_id`, `applied_frames`, `source_frame` (omitted before the first applied pose) | |
| `cache_seeked` | `play_id`, `source_frame`, `applied_frames` | a sampled source frame is shown and held paused |
| `cache_looped` | `play_id`, `round`, `source_frame` | a loop round wrapped |
| `cache_loop_changed` | `play_id`, `loop` | the loop selection changed |
| `cache_complete` | `play_id`, `applied_frame_count`, `elapsed_seconds`, `scope` (`cache` or `segment`), `start_frame`, `end_frame` | |
| `cache_stopped` | `play_id` | the attempt stopped; cache and held pose remain |
| `cache_cleared` | `upload_id`, `play_id`, `clear_id` | echoes the identities that were dropped |
| `camera_time` | `session_id`, `set_revision`, `mode_revision`, `space_revision`, `eval_id`, `eval_serial`, `transport_serial`, `frame_serial`, `time{display_frame, source_frame, seconds, tick, display_rate{n,d}, tick_resolution{n,d}, playback_range{start,end}}`, `camera{...}`, `gate{...}`, `cut{stage, name}` | the current linkage target |
| `applied` | `session_id`, `mode_revision`, `eval_id`, `sample_serial`, `applied`, `applied_display_frame`, `lag_seconds`, `components[{object_id, bones, morphs, parts}]`, `reason` | completion evidence for the whole set |
| `mode_state` | `session_id`, `mode_revision`, `mode`, `control`, `detail`; `anchor{location_cm[3], rotation_deg{roll, pitch, yaw}}` when `mode` is `linkage` | Unreal's authority statement; a linkage statement names the takeover's static anchor in the shape of the manifest's `world.anchor` |
| `object_removed` | `session_id`, `object_id`, `set_revision`, `restored`, `detail` | Unreal removed the second object (asked by Maya or by the Unreal user) and confirms the new set revision; `restored: false` names the residual and is followed by `RECOVERY_REFUSED`, which ends the connection |
| `reference_offer` | `session_id`, `transfer_id`, `directory`, `manifest_digest`, `scope{level, sublevels, completeness, unloaded_sublevels}`, `space_revision`, `counts{objects, triangles, bytes}` | a finished local handoff the user may import |

## Rejection codes

Codes are stable; the message text is diagnostic only. Unless a row says
otherwise, a negotiation or structural failure closes the connection, while
linkage, removal, reference and cache refusals answer that one message and keep
it.

| Code | Trigger |
| --- | --- |
| `PROTOCOL_VERSION_MISMATCH` | version field or `hello` version differs |
| `INVALID_MESSAGE` | malformed shape or framing, invalid UTF-8, non-finite number, wrong length, duplicate id, partial or unknown object set; a dropped realtime frame keeps the connection |
| `SECOND_CLIENT_REJECTED` | a second Maya connects while one is connected |
| `NO_BINDING_ACTOR`, `MULTIPLE_BINDING_ACTORS` | no placed Binding Actor, or several without an explicit pairing |
| `SKELETON_MISMATCH` | the Maya bones cannot drive the required Unreal bones of every enabled display |
| `BIND_POSE_INVALID` | a bind or pose transform needed for retargeting cannot be inverted |
| `PREVIEW_NOT_READY`, `PREVIEW_MORPH_MISMATCH` | Model needs a ready generated preview; a non-empty BlendShape manifest shares no name with the Morphs of the Generated Preview, Primary Driver and enabled Additional Parts together |
| `PAIRING_REQUIRED` | linkage requested (Unreal entry or Maya `mode`) for an object the user has not paired, or a second object without a pairing |
| `TARGET_UNKNOWN` | paired target no longer resolves (destroyed, level unloaded) |
| `TARGET_ALREADY_PAIRED` | a second object claims one target |
| `WRITER_NOT_ISOLATABLE` | the target's writers cannot be muted without touching unrelated tracks |
| `SHARED_WRITER` | a writer drives the target and another object, also the other object of the set |
| `MOVING_SOCKET` | the target is parented to a moving socket |
| `ANCHOR_MISMATCH` | the targets of one set do not share one placement, or one moved while linkage held it |
| `OBJECT_SET_UNSUPPORTED` | the set is larger than `max_set_objects`, or the workflow (Model, Cached Playback) keeps the single-character contract |
| `MODE_CONFLICT` | the requested transition contradicts the current mode or revision |
| `CACHE_NOT_CLEARED` | linkage requested while a cache is still owned |
| `SET_REVISION_STALE` | message names an older object set |
| `SPACE_REVISION_STALE` | message names an older common space |
| `EVAL_STALE` | group answers a cancelled or superseded target |
| `LINKAGE_APPLY_FAILED` | Unreal could not display a group on every display of the set |
| `LINKAGE_NOT_ACTIVE` | a linkage-only message arrived in another mode |
| `RESOURCE_LIMIT` | message or group exceeds a frozen budget |
| `REFERENCE_STALE` | the offered manifest belongs to an older space revision, or the imported reference was placed for another anchor than the linkage target's |
| `REFERENCE_DIGEST_MISMATCH` | the directory content does not match the offered digest |
| `REFERENCE_IMAGE_DATA` | the handoff delivers image data |
| `REFERENCE_OVER_BUDGET` | file, object or triangle budget exceeded |
| `CONTAINER_NOT_OWNED` | a node under the container name lacks the ownership mark |
| `RECOVERY_REFUSED` | a previous reference or writer state cannot be restored safely; for a removal it ends the connection |
| `CACHE_METADATA_INVALID` | `cache_begin` declares an invalid range, rate, count or size |
| `CACHE_PAYLOAD_TOO_LARGE` | declared or metered bytes, or predicted parsed memory, exceed the budget |
| `CACHE_FRAME_INDEX_INVALID` | a `cache_frame` index is negative or not the next expected index |
| `CACHE_FRAME_CONTENTS_INVALID` | a `cache_frame` has wrong counts or non-finite values |
| `CACHE_INVALID_STATE` | a cache command arrives in a state that cannot accept it |
| `CACHE_NOT_READY` | playback requested without a complete cache |
| `CACHE_REVISION_MISMATCH` | the upload names another character revision |
| `CACHE_SEEK_INVALID`, `CACHE_SEEK_FAILED` | the requested source frame was not sampled; the sampled frame could not be displayed |
| `CACHED_PLAYBACK_PERFORMANCE` | a pose missed its window or the run exceeded its duration bound |
| `INTERNAL_ERROR` | the Unreal Live Link client is unavailable |

## Conformance corpus

`conformance.json` (`schema_version` 1, `protocol_version` 11) is executed by
the Maya unit tests and, through the generated `.inl`, by the Unreal
Automation tests. Each case names its `operation`, the hosts it `applies_to`,
exactly one input (`payload`, `payloads`, `raw_utf8` or `raw_hex`), any
pre-state it runs against (`linkage`, `negotiated_set`, `negotiated_revision`,
`session`) and the `expected` outcome: `accepted`, `close`, the stable
`error_code` of a rejection and diagnostic `keywords`. A resource case names
one `limits` key and a measured size above it instead of carrying the
oversized payload. The corpus covers framing, negotiation, realtime frames,
the cache upload and its outcomes, identity staleness, the object set and its
removal, linkage transitions, groups and applied evidence, resources, an
earlier-version `init`, `frame` and `cache_begin`, and the reference handoff.
The cancelled-target and paused-resample refusals are pinned by the linkage
unit tests on both hosts rather than by a corpus case.

## Changing the protocol

A change to any message shape, meaning, identity rule, limit or code is a new
protocol version: update this document, the corpus and `protocol_version`,
both hosts' version constants and their tests together, then run
`python protocol/generate_unreal_corpus.py` and commit the regenerated `.inl`.
The pull request adds an `Upgrade notes` entry to the changelog telling users
to update both components together.
