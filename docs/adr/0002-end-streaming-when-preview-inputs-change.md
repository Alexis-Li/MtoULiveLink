# End streaming when preview inputs change

An Animation or Model streaming session accepts one coherent input revision:
its skeleton, reference pose, transferred weights, curve manifest and displayed
surface. A change to the Driver Skeletal Mesh, Preview Static Mesh, their relevant
imported data, or the captured Maya inputs ends the old session before a new
complete negotiation can begin. The synchronous publication and sampling gates
reject old live frames, cached commands and linkage groups while resources are
restored; an accepted session never adopts the new inputs in place.

For the same Maya character and Unreal target instances, an input change may
automatically initiate that new negotiation. Maya's controller retains the user's
connection intent; the Unreal Source authorizes one new connection only after
both hosts have prepared and the old writers and resources have been restored.
Concurrent edits coalesce to the latest inputs within the same bounded operation.
The operation's deadlines, identities, one-use permission and failure semantics
are defined in the [protocol contract](../../protocol/README.md). This is a new
Streaming session, not continuation of the old one. It clears the old cache and
does not automatically resume linkage takeover.

Changing workflow or BS transmission, or changing the visible Maya outfit mesh
set, follows this input-change boundary. A label-only or path-only change, a
Template/Reference style change with the same visible set, or a no-effect edit
does not end streaming. Replacing a character, root, scene, World, Actor, Binding
instance, Source or pairing ends connection intent. Manual disconnect, unexpected
transport loss, negotiation refusal, timeout or failed resource restoration also
stops it. Later edits cannot reconnect a stopped or previously disconnected tool;
a fresh explicit Connect is required. A replacement with the same name or path
does not preserve identity.

Model preview requires a successful explicit refresh of the current Preview
revision before connecting. Input-change coordination never builds a Preview:
if readiness is absent, it stops with PREVIEW_NOT_READY. Animation preview does
not depend on Preview readiness. Explicit Preview refresh ends an active session
before releasing or replacing its display, even when the inputs are unchanged.
Success displays the Generated Preview while disconnected; failure keeps Error
readiness and restores the Driver for inspection. Both outcomes require the user
to connect explicitly.

Character composition is separate from the Preview revision. Adding, removing,
enabling, disabling or replacing an Additional Part ends the old session at the
same boundary and may automatically negotiate the new composition for unchanged
subjects. Frames, cached commands and linkage groups from the old composition
cannot reach its new display components. The Primary Driver and Preview Static
Mesh still own garment resolution, weight transfer and Preview Morph transfer,
so an Additional Part change preserves an already generated garment Preview and
its readiness. Only a Primary Driver, Preview Static Mesh, Garment Slot Override
or related imported-data change invalidates the Preview revision and requires an
explicit refresh before Model preview can connect. Renaming a part or reordering
the list changes neither the session nor the Preview.
