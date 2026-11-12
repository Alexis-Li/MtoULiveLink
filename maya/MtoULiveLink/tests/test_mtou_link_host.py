# ruff: noqa: E402
"""Maya linkage host adapter tests (fake Maya bindings, no Maya session)."""

import pathlib
import sys
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import test_mtou_camera as camera_fixtures  # noqa: E402
from mtou_livelink import camera as mtou_camera  # noqa: E402
from mtou_livelink import config as mtou_config  # noqa: E402
from mtou_livelink import link_host as mtou_link_host  # noqa: E402
from mtou_livelink import linkage as mtou_linkage  # noqa: E402
from mtou_livelink import timecontrol as mtou_timecontrol  # noqa: E402


class FakeScene(object):
    def __init__(self, transforms=None, curves=None):
        self.sampled = 0
        self._transforms = transforms or [[0, 0, 0, 0, 0, 0, 1, 1, 1, 1]]
        self._curves = curves or [0.25]

    def sample(self):
        self.sampled += 1

        class Frame(object):
            transforms = self._transforms
            curves = self._curves

        return Frame()


class FakeCamera(object):
    def __init__(self, name=None):
        self.name = name
        self.applied = []
        self.readback_value = {"world_matrix": [1.0], "focal_length": 35.0}
        self.captured = 0
        self.restored = []

    def apply(self, camera_payload, gate_payload):
        self.applied.append((camera_payload, gate_payload))

    def readback(self):
        return dict(self.readback_value)

    def capture_state(self):
        self.captured += 1
        return {"look_through": "persp"}

    def restore_state(self, state):
        self.restored.append(state)


class FakeCameraModule(object):
    def __init__(self, camera):
        self.LinkCamera = lambda name=None: camera

    @staticmethod
    def maya_time_for_frame(display_frame, playback_start, display_rate,
                            maya_fps, maya_origin):
        return maya_origin + (display_frame - playback_start) * maya_fps / (
            float(display_rate["numerator"]) / float(display_rate["denominator"]))


def fake_cmds(time_value=1.0, playing=False):
    state = {"time": float(time_value), "playing": playing, "width": 1920,
             "height": 1080, "deviceAspectRatio": 1.7777777777777777,
             "pixelAspect": 1.0}
    commands = mock.MagicMock()

    def current_time(*args, **kwargs):
        if kwargs.get("query"):
            return state["time"]
        state["time"] = float(args[0])
        return state["time"]

    def get_attr(name, *args, **kwargs):
        key = name.split(".")[-1]
        return state[key]

    def set_attr(name, value, *args, **kwargs):
        state[name.split(".")[-1]] = value

    def play(*args, **kwargs):
        if kwargs.get("query"):
            return state["playing"]
        if "state" in kwargs:
            state["playing"] = bool(kwargs["state"])
        return state["playing"]

    def playback_options(*args, **kwargs):
        return 1.0 if kwargs.get("minTime") else 24.0

    commands.currentTime.side_effect = current_time
    commands.getAttr.side_effect = get_attr
    commands.setAttr.side_effect = set_attr
    commands.play.side_effect = play
    commands.playbackOptions.side_effect = playback_options
    return commands, state


def target(display_frame=5.0):
    return {
        "display_frame": display_frame,
        "playback_range": {"start": 1, "end": 24},
        "display_rate": {"numerator": 24, "denominator": 1},
    }


def build(fps=24.0, time_value=1.0):
    camera = FakeCamera()
    commands, state = fake_cmds(time_value)
    host = mtou_link_host.MayaLinkageHost(FakeScene(), ["character"], fps)
    patchers = [
        mock.patch.object(mtou_link_host, "cmds", commands),
        mock.patch.object(mtou_link_host, "camera_module",
                          return_value=FakeCameraModule(camera)),
        mock.patch.object(mtou_link_host, "require_maya", return_value=None),
    ]
    return host, camera, commands, state, patchers


class LinkageHostTests(unittest.TestCase):
    def test_capture_and_restore_cover_every_resource(self):
        host, camera, commands, state, patchers = build(time_value=7.0)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        captured = host.capture_state()
        self.assertEqual(7.0, captured["time"])
        self.assertEqual({"look_through": "persp"}, captured["camera"])
        self.assertNotIn("gate", captured)
        self.assertEqual(1, camera.captured)

        state["time"] = 20.0
        residual = host.restore_state(captured)
        self.assertEqual([], residual)
        self.assertEqual(7.0, state["time"])
        self.assertEqual([{"look_through": "persp"}], camera.restored)
        # The resolution gate belongs to the camera adapter alone.
        commands.setAttr.assert_not_called()

    def test_restore_reports_a_resource_it_cannot_put_back(self):
        host, camera, commands, state, patchers = build()
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        captured = host.capture_state()
        camera.restore_state = mock.Mock(side_effect=RuntimeError("panel is gone"))
        residual = host.restore_state(captured)
        self.assertEqual(1, len(residual))
        self.assertTrue(residual[0].startswith("camera:"))
        self.assertIn("panel is gone", residual[0])

    def test_restore_merges_the_camera_residual_list(self):
        # LinkCamera reports a node it could not delete in its returned list
        # instead of raising; the host must not drop that evidence.
        host, camera, commands, state, patchers = build()
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        captured = host.capture_state()
        camera.restore_state = mock.Mock(return_value=[
            "camera MtoU_LinkCamera: Cannot delete locked node"])
        state["time"] = 20.0
        residual = host.restore_state(captured)
        self.assertEqual(
            ["camera: camera MtoU_LinkCamera: Cannot delete locked node"], residual)
        # The other resources are still restored.
        self.assertEqual(1.0, state["time"])

    def test_restore_writes_nothing_once_the_scene_was_replaced(self):
        host, camera, commands, state, patchers = build(time_value=7.0)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        captured = host.capture_state()
        host.note_scene_replaced()
        state["time"] = 48.0
        state["width"] = 2048
        residual = host.restore_state(captured)
        self.assertEqual(1, len(residual))
        self.assertTrue(residual[0].startswith("scene:"))
        self.assertEqual(48.0, state["time"])
        self.assertEqual(2048, state["width"])
        self.assertEqual([], camera.restored)

    def test_restore_writes_nothing_into_a_scene_with_another_identity(self):
        host, camera, commands, state, patchers = build(time_value=7.0)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        commands.ls.side_effect = lambda *a, **k: ["scene-a"]
        captured = host.capture_state()
        self.assertEqual("scene-a", captured["scene"])
        commands.ls.side_effect = lambda *a, **k: ["scene-b"]
        state["width"] = 2048
        residual = host.restore_state(captured)
        self.assertTrue(residual[0].startswith("scene:"))
        self.assertEqual(2048, state["width"])
        self.assertEqual([], camera.restored)
        self.assertTrue(host.scene_replaced())

    def test_scene_callbacks_are_registered_at_capture_and_released(self):
        host, camera, commands, state, patchers = build()
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        fake_om = mock.MagicMock()
        fake_om.MSceneMessage.addCallback.side_effect = [11, 12]
        with mock.patch.object(mtou_link_host, "om", fake_om):
            captured = host.capture_state()
            self.assertEqual(2, fake_om.MSceneMessage.addCallback.call_count)
            host.restore_state(captured)
        removed = [call[0][0] for call in fake_om.MMessage.removeCallback.call_args_list]
        self.assertEqual([11, 12], removed)

    def test_apply_maps_time_applies_camera_and_reports_readback(self):
        host, camera, commands, state, patchers = build(time_value=3.0)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        applied_time, readback = host.apply_target(
            target(5.0), {"focal_length_mm": 35.0}, {"output_width": 1920})
        # origin 3.0 + (5.0 - 1) * 24 / 24
        self.assertEqual(7.0, applied_time["maya_frame"])
        self.assertEqual(7.0, state["time"])
        self.assertEqual(5.0, applied_time["ue_display_frame"])
        self.assertEqual([({"focal_length_mm": 35.0}, {"output_width": 1920})],
                         camera.applied)
        self.assertEqual(35.0, readback["focal_length"])

    def test_apply_uses_the_captured_origin_not_the_current_frame(self):
        host, camera, commands, state, patchers = build(time_value=3.0)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        captured = host.capture_state()
        host.set_origin_frame(captured["time"])
        state["time"] = 11.0
        applied_time, unused_readback = host.apply_target(
            target(5.0), {}, {})
        self.assertEqual(7.0, applied_time["maya_frame"])

    def test_linkage_entry_captures_the_origin_for_every_later_target(self):
        # Each target moves Maya's clock; the next one must still map from the
        # frame linkage started on, not from where the previous sample left it.
        host, camera, commands, state, patchers = build(time_value=3.0)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        host.capture_state()
        first, unused_readback = host.apply_target(target(5.0), {}, {})
        second, unused_readback = host.apply_target(target(9.0), {}, {})
        self.assertEqual(7.0, first["maya_frame"])
        self.assertEqual(11.0, second["maya_frame"])

    def test_restore_forgets_the_origin_of_the_ended_linkage(self):
        host, camera, commands, state, patchers = build(time_value=3.0)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        captured = host.capture_state()
        host.restore_state(captured)
        state["time"] = 20.0
        host.capture_state()
        applied_time, unused_readback = host.apply_target(target(5.0), {}, {})
        # origin 20.0 + (5.0 - 1) * 24 / 24
        self.assertEqual(24.0, applied_time["maya_frame"])

    def test_sample_group_returns_one_complete_row_per_object(self):
        scene = FakeScene()
        camera = FakeCamera()
        host = mtou_link_host.MayaLinkageHost(scene, ["character", "prop"], 24.0)
        with mock.patch.object(mtou_link_host, "camera_module",
                               return_value=FakeCameraModule(camera)):
            rows = host.sample_group(["character", "prop"])
        self.assertEqual(["character", "prop"],
                         [row["object_id"] for row in rows])
        for row in rows:
            self.assertEqual([[0, 0, 0, 0, 0, 0, 1, 1, 1, 1]],
                             [list(transform) for transform in row["transforms"]])
        self.assertEqual([0.25], list(rows[0]["curves"]))
        self.assertEqual(2, scene.sampled)

    def test_camera_module_is_imported_only_when_linkage_starts(self):
        host = mtou_link_host.MayaLinkageHost(FakeScene(), ["character"], 24.0)
        with mock.patch.object(mtou_link_host, "camera_module") as loader:
            loader.return_value = FakeCameraModule(FakeCamera())
            host.sample_group(["character"])
            loader.assert_not_called()
            host._camera_adapter()
            loader.assert_called_once_with()


class GateCmds(camera_fixtures.FakeCmds):
    """The camera tests' fake Maya, plus the playback queries the host makes."""

    def play(self, query=False, state=None):
        return False

    def playbackOptions(self, query=False, minTime=False, maxTime=False):
        return 1.0 if minTime else 120.0


class LinkageSessionStub(object):
    def __init__(self):
        self.messages = []
        self.time_control = mtou_timecontrol.TimeControl()

    def submit_linkage(self, message):
        self.messages.append(message)
        return True

    def is_ready(self):
        return True


class ResolutionGateExitTests(unittest.TestCase):
    """The camera adapter alone captures and restores the resolution gate.

    A real ``MayaLinkageHost`` and ``LinkCamera`` over one fake Maya, driven
    through the real ``LinkageSession`` exits.
    """

    START_GATE = (1024, 768, 1.1, 1024 * 1.1 / 768)

    def setUp(self):
        self.cmds = GateCmds(start_gate=self.START_GATE)
        patcher = mock.patch.multiple(
            mtou_camera, cmds=self.cmds, om=camera_fixtures.FakeOpenMaya(self.cmds))
        patcher.start()
        self.addCleanup(patcher.stop)
        for patcher in (mock.patch.object(mtou_link_host, "cmds", self.cmds),
                        mock.patch.object(mtou_link_host, "require_maya",
                                          return_value=None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.host = mtou_link_host.MayaLinkageHost(
            FakeScene(), ["character"], 24.0, camera=mtou_camera.LinkCamera())
        self.clock = {"now": 0.0}
        self.linkage = mtou_linkage.LinkageSession(
            self.host, LinkageSessionStub(), clock=lambda: self.clock["now"])
        self.linkage.set_session_id(5)

    def enter_and_apply(self):
        self.linkage.enter(1)
        self.host.apply_target(target(5.0), camera_fixtures.camera_payload(),
                               camera_fixtures.gate_payload())
        self.assertEqual([1920, 1080], self.cmds.gate[:2])

    def gate_writes(self, since):
        return [write[1] for write in self.cmds.writes[since:]
                if write[0] == "setAttr"
                and write[1] in mtou_camera.RESOLUTION_PLUGS]

    def leave_realtime(self):
        return self.linkage.leave("realtime", 2)

    def leave_end(self):
        return self.linkage.leave("end", 2)

    def expire_unconfirmed(self):
        self.clock["now"] = mtou_config.LINKAGE_MODE_CONFIRMATION_SECONDS + 0.1
        return self.linkage.expire_unconfirmed()

    def disconnect(self):
        return self.linkage.close()

    def test_every_exit_restores_the_gate_once(self):
        exits = (("返回实时", self.leave_realtime), ("结束预览", self.leave_end),
                 ("确认超时", self.expire_unconfirmed), ("断线", self.disconnect))
        for label, leave in exits:
            with self.subTest(label):
                self.setUp()
                self.enter_and_apply()
                before = len(self.cmds.writes)
                self.assertEqual((), leave())
                self.assertEqual(list(self.START_GATE), self.cmds.gate)
                # One write per changed plug: no second pass over the gate.
                self.assertEqual(sorted(mtou_camera.RESOLUTION_PLUGS),
                                 sorted(self.gate_writes(before)))

    def test_a_locked_plug_that_still_holds_its_value_is_not_a_residual(self):
        self.linkage.enter(1)
        self.cmds.locked.add("defaultResolution.width")
        with self.assertRaises(mtou_camera.CameraSyncRefused):
            self.host.apply_target(target(5.0), camera_fixtures.camera_payload(),
                                   camera_fixtures.gate_payload())
        self.assertEqual((), self.leave_end())
        self.assertEqual(list(self.START_GATE), self.cmds.gate)
        self.assertEqual((), tuple(self.linkage.recovery_records()))

    def test_a_locked_plug_that_was_changed_is_reported_and_the_rest_restored(self):
        self.enter_and_apply()
        self.cmds.locked.add("defaultResolution.width")
        residual = self.leave_end()
        self.assertEqual(1, len(residual))
        self.assertIn("defaultResolution.width", residual[0])
        self.assertEqual([1920, 768, 1.1, 1024 * 1.1 / 768], self.cmds.gate)


if __name__ == "__main__":
    unittest.main()
