# MtoU_LiveLink

[中文说明](README_CN.md)

MtoU_LiveLink connects Maya to Unreal Editor on the same computer so you can
preview character animation, BlendShapes, and garment changes in Unreal while
working in Maya. It supports real-time preview and cached animation playback,
follows a Level Sequence's camera and time in Maya, and adds a second skeletal
object beside the main character, without creating animation or preview assets.

## Compatibility

- Windows 64-bit
- Autodesk Maya 2024
- Stock Unreal Editor 5.7.4
- Editor use only; Play In Editor (PIE) is not supported.

Compatibility with modified or third-party Unreal Engine 5.7 builds is not guaranteed.
Install the Maya and Unreal components from the same release.

## Installation

Download both archives of one release from the repository's Releases page:
`MtoULiveLink-<version>.zip` (Maya) and `MtoULiveLink-<version>-UE5.7.zip`
(Unreal source plugin). Component paths below are relative to this
repository's root and match the archives' contents.

**Maya**

1. Put the Maya component folder (`maya/MtoULiveLink/`, or the extracted
   `MtoULiveLink-<version>.zip`) in a location you will keep.
2. Open Maya and drag the folder's `install.mel` into a viewport.
3. Confirm the prompt. An **MtoU** button appears on the shelf (an existing one
   is kept); click it to open the tool.

The installation registers the folder as a Maya module (`MtoULiveLink.mod` in
the `modules` folder of the user Maya directory, usually `Documents/maya`), so
do not move or delete the folder afterwards. Maya cannot load a module from a
path with non-English characters such as Chinese; for such a path the
installation copies the tool to `modules/MtoULiveLink`, and the original folder
can then be deleted. When it finds an earlier version that
`install.mel` did not install, such as `MtoULiveLink.py` in a Maya scripts
directory, the installation lists it and deletes it after you confirm;
cancelling changes nothing. If the current Maya session has already opened an
earlier version, restart Maya when prompted.

**Unreal**

1. Copy `unreal/MtoULiveLink/` (or the `MtoULiveLink` folder of the Unreal
   archive) to `<Project>/Plugins/MtoULiveLink/`.
2. Compile the Unreal project.
3. Enable **Live Link** and **MtoU_LiveLink**, then restart Unreal Editor.

**Upgrade, rollback and removal**

Maya and Unreal components upgrade and roll back as a pair. Protocol v12
development snapshots also require the same build of both components: the same
protocol number alone does not make these unreleased snapshots compatible.
Missing required identity fields stop negotiation and require updating both hosts.
End the current session first, then:

- Upgrade: drag the new Maya component's `install.mel` into Maya, replace the
  Unreal plugin, and reconnect. In the new Maya panel, set the character and
  any second object in **连接** the first time you open each scene. An earlier
  **Follow Scene** transmission limit becomes **60** fps.
- Roll back: drag the previous Maya component's `install.mel` into Maya. If
  the previous release has no `install.mel`, first delete the files listed
  under removal, then install it as its instructions describe. Restore the
  previous Unreal plugin build and reconnect.
- Remove the Maya component: delete `MtoULiveLink.mod` and any
  `MtoULiveLink` folder from the `modules` folder of the user Maya directory,
  delete the MtoU shelf button, then restart Maya.

## Prepare your character

- Maya must contain the bones that deform the enabled Unreal meshes and their
  complete ancestor chains, including intermediate groups. Names and parent
  relationships must match. Duplicated Maya short names may map to imported
  numeric or 32-digit hash suffixes under the same parent; the connection reports
  those mappings. Unused exported branches do not prevent connection, but one
  Unreal bone is driven by exactly one Maya bone: rename a Maya bone when two of
  them publish the same name under the same parent.
- Select the deformation root and click **设置角色** on the **连接** page. A
  Display controller is optional. The outfit is the current visible set of
  meshes skinned to that
  skeleton. Hidden meshes, including those hidden by an ancestor, LOD visibility
  or a visibility override, do not contribute BlendShapes. Visible Template and
  Reference meshes do. A unique recognised Display enum keeps its outfit label;
  otherwise the panel uses sorted mesh names. **选择 Display 控制器** optionally
  chooses a label; it does not choose the participating meshes.
- For garment preview, prepare a **Preview Static Mesh** aligned with the
  Primary Driver garment. Keep garment and non-garment geometry in separate
  imported material slots.
- To preview BlendShapes, use matching Maya BlendShape and Unreal Morph Target
  names. **动画** always sends BlendShapes. For **模型**, enable **传递 BS** in
  Maya's **连接** page.

### Separate character parts

When one character is delivered as several Skeletal Meshes, assign the garment
source as the Primary Driver and add the other meshes under **Additional Parts**
on the Binding. All enabled parts pose and display under one connection:

- Only the Primary Driver supplies garment resolution, weight transfer, and
  **Preview Morph transfer**.
- Give each part a name, its Skeletal Mesh, and an **Enabled** flag. All meshes
  must share one **Skeleton** asset. Shared bones needed for skinning across
  any LOD, including their ancestors, must agree in hierarchy and reference pose.
  A part's own secondary bones receive Maya animation even when the Primary
  does not contain them. Disabled parts add no bone requirements.
- After adding clothing and secondary bones, import the new clothing, update
  the enabled parts. A connected session renegotiates automatically for the same
  character and targets. Update older meshes only if their own bind
  pose or weights changed. A missing required Maya bone or an incompatible
  shared bone blocks connection and names the affected part and bone.
- A Morph Target that only a part owns still streams, and a name owned by
  several parts receives the same value on each of them.
- Adding, removing, enabling, disabling, or replacing a part ends the old
  session and automatically negotiates a new one for the same character and
  targets. A generated garment preview survives. Run **Refresh Preview** after
  changing the Primary Driver, the Preview Static Mesh, the **Driver Garment Slot Override**, or
  relevant imported data.
- Renaming a part or reordering the list needs no reconnect.

## First connection

1. In Unreal, create an **MtoU_LiveLink Binding** asset, assign its
   **Primary Driver Skeletal Mesh**, and add any **Additional Parts**.
2. Drag the Binding asset from the Content Browser into the level to create
   its Binding Actor. Its Details panel opens **MtoU · 运行状态** (separate Preview
   and connection states plus the next step), **角色组成** (Primary and expandable
   Additional Parts), and **预览控制** (Binding and generated mesh asset fields,
   Refresh, Delete, and cache playback). Expand **高级设置与诊断** for the full
   selectable report; **复制完整诊断** copies it, including the linkage
   details, for a problem report. An empty diagnostic does not
   occupy the daily view.
   The connection reads 未连接 before the first session, 已连接 while streaming,
   正在重新连接 during input-change negotiation, and 连接已中断 after stopping.
   Read the reason and click **连接** in Maya after a stop.
3. In Maya, click the **MtoU** shelf button. On the **连接** page, select the
   deformation root joint and click **设置角色**. Use **重设** when choosing a new
   root. This page shows the character, outfit, root path, and bone, BlendShape
   and outfit mesh counts. **选择 Display 控制器**, **选中重名骨骼** when available,
   and the second-object controls are on this page. Hover a shortened name or
   path to read it in full.
4. Switch the workflow to **动画** and click **连接** in the header. Pose, scrub,
   or play in Maya to preview the result in Unreal.

The header stays visible on all four pages (**连接**, **预览**, **相机**, **诊断**).
It shows the connection state, who controls time, the character and outfit,
and the scene frame rate or current frame. In live preview, the link from M
(Maya) to U (Unreal) shows how many frames were sent in the last second.
During Cached Playback and Sequence linkage, look for **UE 播放** and
**UE 控制时间**. Use **连接** or **断开** from any page. Hover a disabled button
to see why it is unavailable.

### Settings per scene

Maya remembers the workflow, **传递 BS**, **出错时弹窗提示**, transmission limit,
capture range and its start/end frames, and the roots and selected Display
controllers for the character and second object. These settings are stored in
your Maya preferences by scene file path. An unnamed scene keeps them for the
current panel session, and the first save or Save As carries them to the new
path. Opening a scene or reopening the panel or Maya restores and checks its
settings. If updating a reference changes a recorded path, check **诊断** and
set the missing root or Display controller again. Saved settings leave the
scene file untouched.

### Diagnostics and performance

Open **诊断** to see **最近一次操作**, with the error code, full message and
recovery steps when available. **此前的连接差异** shows earlier BlendShape
differences or bone-name mappings. **完整诊断** opens a
resizable window with the same latest result at the top and the connection,
character, performance, camera and linkage details below. It refreshes as
results arrive. Scroll long messages and select text to copy it with Ctrl+C.
**出错时弹窗提示** controls popups for connection warnings, failed connections
and unexpected disconnections. Repeated warnings appear once per connection,
and the results are always recorded in **诊断**.

Real-time preview sends new poses when the evaluated pose changes and skips
identical samples while the character is still. Under **性能**, Maya shows
sampling and measured **UE 应用** time beside the scene's time budget per frame.
It also shows the actual send rate, the number of pending frames replaced by
newer ones, and Unreal's editor frame rate. A dash means a reading is
unavailable, for example when a compatible older Unreal component supplies
no metrics.
**完整诊断** shows the host, loaded plugin and protocol versions for each side.
A version or host reading the peer does not supply also appears as a dash.

In the Unreal Binding Actor's **高级设置与诊断**, **实时预览性能** shows parse,
retarget and application times, how often a new pose finishes updating the
displayed skeletons, and the editor frame rate. Use **复制完整诊断** there while
connected to capture those readings for a report.

While connected, Unreal keeps updating in the background, including when Maya
is in front. The running status shows **连接期间 UE 在后台保持更新**. Disconnecting
restores normal background throttling and viewport realtime behavior. Your
**Use Less CPU when in Background** preference and viewport Realtime setting
stay unchanged. A minimized editor does not draw its viewport.

## Changes while connected

For the same captured Maya character and Unreal targets, changing the visible
outfit mesh set, **动画** / **模型**, **传递 BS**, or **Additional Parts**
ends the old session and automatically negotiates the latest inputs. This
also applies to Undo/Redo and relevant Driver or Preview input/reimport changes.
The old cache is cleared and camera linkage resources are restored; a successful
new session returns to realtime preview, so enter linkage again when needed.
A label-only rename, a part reorder, a Template/Reference style change with the
same visible mesh set, and garment comparison keep the current session.

Both hosts show **正在重新连接**. **连接** is disabled during this step, and
**断开** cancels the operation. Usable differences and failures appear in status and
diagnostics without a Maya confirmation dialog. Old-session preparation has a
5-second limit and the whole automatic operation has a 32-second limit; ongoing
edits do not extend them. A failure or timeout stops the operation without retries.

The targets must remain the same instances. Setting another Maya character,
deleting or unloading its root, opening or creating a scene, closing the panel,
replacing/removing the Unreal Source, changing or closing the level, deleting or
replacing a participating Actor, changing its Binding instance, or changing
pairing stops the connection. Recreating an object with the same name does not
resume it. Manual disconnect, transport loss, refused negotiation and resource
restoration failure also stop it; later edits do not connect a stopped tool.
Inspect the reason on the surviving Maya panel, Unreal Source or Actor Details,
correct the input and click **连接** explicitly. An Unreal restart requires
that explicit connection too.

Model still needs **Refresh Preview** after a Primary Driver, Preview Static Mesh,
**Driver Garment Slot Override** or relevant imported-data change. Automatic
negotiation stops if Preview is not ready; it never refreshes it for you. An
explicit refresh also ends streaming and requires **连接** afterwards.
Animation does not require a ready garment Preview.

## Animation preview

Use **动画** for real-time character preview. On Maya's **预览** page, choose
**实时** and set **传输上限** to 15, 20, 24, 30, or 60 fps. During playback, Maya
uses the lower of this limit and the scene frame rate.

To capture part of an animation, choose **缓存播放** on the same page. Under
**捕获范围**, choose **播放范围** to use Maya's current playback range, or choose
**自定义** and enter integer start and end frames. Click **捕获并上传** and wait
for the cache to show 缓存已就绪. **取消捕获** is available while capture is in
progress. The range includes both endpoints and the scene frame rate is fixed
when capture starts. Capture leaves Maya's playback range as it was. Single
frames and negative start frames are supported.

In the Unreal Binding Actor's **预览控制**, check the source range, rate, current
applied source frame, and cache state. **播放** starts from the first frame, and
**再次播放** replays a cache retained by a stop or a completed run. **暂停** holds
the current pose and the cache for as long as you need, and **继续** carries on
from that pose without replaying the paused time. **停止** ends the attempt and
keeps both the cache and the held pose. To inspect one frame, type an integer
Maya source frame beside **定位** and click it: Unreal displays that frame and
holds it paused. Invalid or overflowing integers are rejected in the row.
Only frames this capture sampled are accepted; an unsampled frame is
refused, and the row names the requested frame and the sampled range. **循环**
wraps from the last frame back to the first and shows the current round; turning
it off completes the current round. Locating and looping reuse the uploaded
cache. Natural completion holds the final frame. To prepare a new range in Maya,
click **捕获并上传** again; the old UE playback ends before sampling begins.
On Maya's **预览** page, switch back to **实时** to clear the Unreal cache and
resume live poses.
After a connection loss, reconnect. If a compatible complete local cache
remains, choose **缓存播放** and click **上传保留缓存**. The cache status on Maya's
**预览** page shows capture, upload, ready, playback, pause, seek, loop, stop,
and failure. A run completed after a seek or a loop round is reported as a
playback segment.

A capture is limited to 20,000 frames or 1 GiB; use a shorter range if you reach
that limit. Invalid ranges fail before sampling. Sampling or upload failures
return to real-time preview and show the reason. A playback failure retains
the complete UE cache for another attempt.

## Camera linkage preview

Use this workflow to check Maya animation under the Unreal sequence camera and
its timeline. It is optional: realtime preview, Cached Playback and garment
comparison work without it.

1. In Unreal, select the Binding Actor, click **配对为主角色** under **镜头联动**
   (the row then reads 已配对为主角色), and open the Level Sequence you want to
   follow.
2. In Maya, connect the character as usual. Open **相机** and click **开始联动**
   under **Sequence 联动**, or click **进入联动** in the Unreal Details panel.
   Maya pauses its own playback, follows the evaluated Unreal camera and
   Sequence time, and sends back the character's complete pose. The Maya
   header and **相机** page show **UE 控制时间** during linkage.
3. Watch the linkage status on the **相机** page and in Unreal's **镜头联动**.
   When Maya cannot keep up, it shows Maya 跟不上 with the lag. Pause Sequencer
   to let Maya catch up, or close programs that load the machine. If it
   happens often, see [Common problems](#common-problems). If Unreal cannot
   display a complete pose, Maya shows 应用失败 and retries. For a persistent
   failure, open **诊断** > **完整诊断** in Maya. Linkage readings also appear in
   Unreal's **高级设置与诊断**, where **复制完整诊断** copies the full report.
4. **返回实时** restores Maya's timeline, view and camera gate and leaves Maya
   paused while Unreal keeps its takeover. Resume Maya playback when ready.
   To return the original animation tracks to Sequencer, use **结束联动** in
   Maya or **结束预览** in Unreal. This remains available after **返回实时**
   until Unreal returns the tracks.

The **UE 相机** section has placeholders for the camera list, **更新相机** and
**从文件导入相机…**. Camera import and updates, the current level and sublevels,
stand-in details, and Maya control of Sequence time are still pending. You
can already follow Unreal's camera and time using **开始联动**.

While linkage is active, a camera edit, a pause, a cut or an explicit jump
cancels the old target. During continuous camera movement, poses already on
their way can finish. Unreal names the reason if it cannot safely take over
a target's tracks. During Cached Playback, **开始联动** in Maya and
**进入联动** in Unreal are unavailable. Choose **实时** on Maya's **预览** page
and wait for Unreal to confirm that it cleared the cache. If restoration
fails, Maya shows 恢复失败 and Unreal shows 恢复未完成. Open Maya's **诊断** >
**完整诊断** or Unreal's **高级设置与诊断** to see what needs restoring. Follow the
report, for example by unlocking the camera or resetting the time range in
Maya, checking the track in Sequencer, or reopening the level. If Maya cannot
restore its previous state when Unreal starts linkage again, the connection
ends. Resolve the items in **完整诊断** and then reconnect.

Closing or switching the Level Sequence, closing its level or deleting the
paired actor ends linkage: Unreal returns the writers and Maya restores itself.
Opening or creating another Maya scene ends the connection after linkage
restored the scene it was started in.
If the Maya camera linkage drives is deleted or its camera shape replaced while
linkage runs, Maya refuses the next target and writes no other camera; end the
preview and start linkage again to use the current camera.

## Second object

Preview one independent skeletal prop or a second character together with the
main character, in realtime preview and in camera linkage. Without a second
object, every workflow stays as described above.

1. Place a second Binding Actor for the object at exactly the same location
   and rotation as the main character's Binding Actor; the object's own
   placement comes from its root in Maya. In its Details, click
   **配对为副对象** under **镜头联动**. The main character's actor must be paired
   with **配对为主角色**.
2. In Maya, select the object's root joint. In **连接**, click **设为副对象**
   under **副对象**. You can select its Display controller with the root to
   supply an outfit label.
3. Connect as usual. Unreal shows both objects from the same Maya time; in
   camera linkage it shows a new pose only when every display of both objects
   holds it.
4. To continue with the main character only, click **移除副对象** in Maya's
   **连接** page or in the second actor's Details. Unreal returns the object's
   tracks to the sequence, and the main character continues without reconnecting.

Setting a different second object, replacing either Actor's Binding instance,
pairing or unpairing an Actor of the connected set, or deleting a paired Actor
stops the connection; connect again. Input changes on the same objects end the
old set and may automatically negotiate a new realtime session as described
under [Changes while connected](#changes-while-connected). Other Binding Actors
in the level do not follow Maya. A second actor at another placement, or one
moved during linkage, is
refused with 副对象与主角色在 UE 中的放置不一致. Cached Playback and the Model
workflow support one character: remove the second object first. If removing
the object cannot restore its tracks, the connection ends and both hosts name
what to restore by hand.

## Garment model preview

1. On the Unreal Binding, assign the garment **Preview Static Mesh**.
2. Click **刷新预览** on the Binding Actor and wait for the Preview status to
   show ready, warning, or failed. A failure names the cause in Chinese while
   connection remains a separate status. Expand **高级设置与诊断** to inspect and
   copy the original error; the rest of the Driver character remains visible.
3. On Maya's **连接** page, select **模型** and check the outfit and **传递 BS**.
   Click **连接** in the header.
4. Pose the character in Maya to inspect garment deformation in Unreal.
5. In the Binding Actor's **预览控制**, click **显示原始服装** and **显示生成服装**
   to compare the same pose under the same actor transform, camera, and light.
   The body and enabled Additional Parts remain visible. This control requires
   a ready Generated Preview; if disabled, read the Preview status or hover the
   button for the next step. 当前显示：原始角色 after a failed or invalidated
   Preview is for inspection and is not a Model comparison result.

## Common problems

| Problem | What to do |
| --- | --- |
| Does Unreal keep updating when Maya is in front? | During a connection, Unreal keeps background editor and viewport updates active. You can leave **Use Less CPU when in Background** enabled. Disconnecting restores your normal background behavior without changing that preference or viewport Realtime. A minimized editor does not draw its viewport. |
| Maya reports an incomplete MtoU_LiveLink install | The message names the missing file. Extract the whole Maya component again, drag its `install.mel` into a Maya viewport, then restart Maya. |
| The MtoU button still opens the earlier version after installation | The current Maya session had already loaded the earlier version; restart Maya. |
| Maya reports 连接失败 · 插件协议版本不匹配 | Open **诊断** > **完整诊断** or read the popup, if enabled, for the loaded Maya and Unreal versions. A missing value appears as a dash in the report and as 未知（对端未提供） in the popup. Install both components again from one release, restart Maya and Unreal, then connect. If Maya only shows the earlier text "Maya 与 UE 插件版本不匹配", its Maya component is older: search Unreal's Output Log for "another protocol version", where one warning names both sides' plugin and protocol versions. |
| Skeleton mismatch | Start from the reported root cause: the first unmapped Maya path and parent, the blocked Maya and unreached Unreal counts, and any suggested import rename. When the details report that one required bone is claimed by two Maya bones, rename one of them in Maya. Then check the selected Maya root, the hierarchy, and whether the Unreal Driver mesh is up to date. |
| An Additional Part is rejected | The diagnostics name the part and the reason. Check the shared **Skeleton** asset, the reported required bones and their parent chains in Maya, and the shared reference poses. If skin weights cannot be read, rebuild or reimport the mesh with CPU skin data available. |
| Preview refresh fails | Check mesh alignment and separate garment/body material slots. Correct the reported issue and refresh again. |
| Auto garment selection rejects an intentionally reduced mesh | Inspect the source for duplicates or mixed garment/body slots. If the reduction is intentional, set **Driver Garment Slot Override** on the Binding to the garment's separate slots, then refresh and inspect the result. |
| BlendShapes do not appear | Check **传递 BS** on Maya's **连接** page, matching names, the selected outfit, and **诊断**. Refresh the Model preview after changing its source assets. |
| Camera linkage often shows Maya 跟不上 with a lag | In Maya open **Windows > Settings/Preferences > Plug-in Manager** (Chinese UI: **窗口 > 设置/首选项 > 插件管理器**), clear **Auto load** for the plug-ins you do not use, for example MASH, XGen (`xgenToolkit`), GPU Cache, Scene Assembly, LookdevX and Type, then restart Maya. Plug-ins a scene needs are still loaded when you open it. |
| Connection stops after a change | Same-subject input changes normally negotiate automatically; read the stopped reason if they fail. Model requires a ready Preview: refresh after changing or reimporting its inputs, then click **连接**. Explicit Refresh, subject replacement, manual disconnect and failures require **连接**; later edits do not resume them. See [Changes while connected](#changes-while-connected). |

Keep the Binding Actor that takes part in the connection and its level loaded
while previewing; deleting that actor or unloading its level ends the connection.
Placing, refreshing or deleting a Binding Actor that does not take part leaves
the connected targets unchanged. A new negotiation never substitutes a different
Actor for a target that was replaced or removed.

## License

Copyright (C) 2026 Alexis-Li. MtoU Live Link is free software under the
[GNU General Public License, version 3 only](LICENSE), with the additional
permission and terms in [NOTICE](NOTICE), and comes with no warranty. You may
use and modify it freely, commercially or inside a company. If you give a copy
to anyone else, keep the notices, provide the source, mark your changes and do
not present a modified version as the original. Both archives contain `LICENSE`
and `NOTICE`. The notice is also under **关于** on Maya's **诊断** page and in
the plugin's entry in Unreal's **Edit > Plugins**.

## Further reading

- [Changelog](CHANGELOG.md)
- [Development and acceptance records](docs/history/README.md)
