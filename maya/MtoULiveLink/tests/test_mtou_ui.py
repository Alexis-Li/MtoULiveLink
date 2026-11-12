"""Panel, controller and window tests for the linkage workflow.

Everything runs against fake ``cmds``/``om`` bindings and a fake streaming
session, so no timer, socket or Unreal peer is involved: the pump is driven by
invoking the recorded timer callback and deferred panel work is flushed by
hand. The controller's linkage state comes from the real ``LinkageSession``
state machine over a fake host, which is what the panel wires in production.
"""

import contextlib
import itertools
import pathlib
import sys
import types
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mtou_livelink import cache as mtou_cache  # noqa: E402
from mtou_livelink import config as mtou_config  # noqa: E402
from mtou_livelink import controller as mtou_controller  # noqa: E402
from mtou_livelink import diagnostics as mtou_diagnostics  # noqa: E402
from mtou_livelink import linkage as mtou_linkage  # noqa: E402
from mtou_livelink import mayaapi as mtou_mayaapi  # noqa: E402
from mtou_livelink import protocol as mtou_protocol  # noqa: E402
from mtou_livelink import scene as mtou_scene  # noqa: E402
from mtou_livelink import session as mtou_session  # noqa: E402
from mtou_livelink import space as mtou_space  # noqa: E402
from mtou_livelink import timeline as mtou_timeline  # noqa: E402
from mtou_livelink import timecontrol as mtou_timecontrol  # noqa: E402

# Every module that binds a Maya global at import time; the detail windows read
# theirs through `mayaapi`, so patching that module covers them too.
_PACKAGE_MODULES = (mtou_cache, mtou_config, mtou_controller, mtou_diagnostics,
                    mtou_linkage, mtou_mayaapi, mtou_protocol, mtou_scene,
                    mtou_session, mtou_space, mtou_timeline)


class _Bindings(object):
    """Patch the Maya bindings on every package module for one block."""

    def __init__(self, cmds=None, om=None):
        self._cmds = cmds
        self._om = om
        self._stack = None

    def __enter__(self):
        self._stack = contextlib.ExitStack()
        for name, value in (("cmds", self._cmds), ("om", self._om)):
            if value is None:
                continue
            for module in _PACKAGE_MODULES:
                if hasattr(module, name):
                    self._stack.enter_context(
                        mock.patch.object(module, name, value))
        return self

    def __exit__(self, *exc_info):
        self._stack.close()
        self._stack = None
        return False


def camera_time(eval_id="seq@10+0/cam#c1", eval_serial=1, transport_serial=1,
                frame_serial=1, display_frame=10.0, session_id=7,
                mode_revision=1, set_revision=1, space_revision=1):
    return {
        "type": "camera_time",
        "session_id": session_id,
        "set_revision": set_revision,
        "mode_revision": mode_revision,
        "space_revision": space_revision,
        "eval_id": eval_id,
        "eval_serial": eval_serial,
        "transport_serial": transport_serial,
        "frame_serial": frame_serial,
        "time": {
            "display_frame": display_frame,
            "source_frame": int(display_frame),
            "seconds": 0.0,
            "tick": 0,
            "display_rate": {"numerator": 24, "denominator": 1},
            "tick_resolution": {"numerator": 24000, "denominator": 1},
            "playback_range": {"start": 0, "end": 100},
        },
        "camera": {"focal_length": 35.0},
        "gate": {"width": 1920, "height": 1080},
        "cut": {"stage": "root", "name": "shotA"},
    }


def applied_message(eval_id="seq@10+0/cam#c1", sample_serial=1, applied=True,
                    mode_revision=1, session_id=7, reason=""):
    return {
        "type": "applied",
        "session_id": session_id,
        "mode_revision": mode_revision,
        "eval_id": eval_id,
        "sample_serial": sample_serial,
        "applied": applied,
        "applied_display_frame": 10.0,
        "lag_seconds": 0.02,
        "components": [{"object_id": "character", "bones": 3, "morphs": 1,
                        "parts": 0}],
        "reason": reason,
    }


IDENTITY_ANCHOR = {"location_cm": [0.0, 0.0, 0.0],
                   "rotation_deg": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}}


class FakeLinkageHost(object):
    """The Maya host adapter's contract, without Maya."""

    def __init__(self, residual=(), capture_error=None):
        self.captured = 0
        self.restored = []
        self.applied = []
        self.restore_residual = list(residual)
        self.capture_error = capture_error

    def capture_state(self):
        self.captured += 1
        if self.capture_error is not None:
            raise self.capture_error
        return {"time": 42.0, "camera": "persp"}

    def restore_state(self, state):
        self.restored.append(state)
        return list(self.restore_residual)

    def apply_target(self, time_payload, camera, gate):
        self.applied.append((time_payload, camera, gate))
        return ({"maya_frame": time_payload["display_frame"],
                 "ue_display_frame": time_payload["display_frame"]},
                {"world_matrix": [1.0]})

    def sample_group(self, object_ids):
        return [{"object_id": object_ids[0],
                 "transforms": [[0.0] * 10], "curves": [0.25]}]


class FakeStreamingSession(object):
    """The streaming session surface the linkage paths use."""

    def __init__(self, ready=True, session_id=7, set_revision=1,
                 space_revision=1):
        self._ready = ready
        self.hello = {"session_id": session_id,
                      "version": mtou_config.PROTOCOL_VERSION}
        self.messages = []
        self.paused = 0
        self.resumed = 0
        self.reasons = []
        # The panel's statement handler, as connect() would register it.
        self.statement_handler = None
        self.time_control = mtou_timecontrol.TimeControl(
            pause=self._pause, resume=self._resume)
        self.object_id = mtou_config.CHARACTER_OBJECT_ID
        self.set_revision = set_revision
        self.space_revision = space_revision

    def is_ready(self):
        return self._ready

    @property
    def hello_reply(self):
        return dict(self.hello)

    def _pause(self):
        self.paused += 1

    def submit_linkage(self, message):
        self.messages.append(message)
        return True

    def _resume(self):
        self.resumed += 1

    def stop(self):
        self.stopped = True

    def route(self, reply):
        """Deliver one Unreal message the way the connection's sender does."""
        return mtou_session.route_reply(
            reply, self.time_control, self.statement_handler, self.reasons.append)


class FakeCharacterScene(object):
    """One sampleable character scene."""

    def sample(self):
        return types.SimpleNamespace(
            transforms=((0.0,) * 10,), curves=(0.0,))

    def close(self):
        self.closed = True


class MainThreadDeferralTests(unittest.TestCase):
    """Unreal statements arrive on the sender thread and must reach the panel.

    An interactive Maya silently drops ``cmds.evalDeferred`` called from a
    worker thread, so inside Maya the thread-safe ``maya.utils`` queue is used.
    """

    def test_statements_use_the_thread_safe_queue_inside_maya(self):
        queued = []
        fake_cmds = mock.MagicMock()
        fake_utils = types.SimpleNamespace(executeDeferred=queued.append)
        with mock.patch.object(mtou_mayaapi, "utils", fake_utils), \
                mock.patch.object(mtou_controller, "cmds", fake_cmds):
            controller = mtou_controller.Controller()

            def action():
                return None

            controller._defer_ui(action)
        self.assertEqual([action], queued)
        fake_cmds.evalDeferred.assert_not_called()

    def test_without_maya_utils_the_cmds_queue_is_used(self):
        fake_cmds = mock.MagicMock()
        with mock.patch.object(mtou_mayaapi, "utils", None), \
                mock.patch.object(mtou_controller, "cmds", fake_cmds):
            controller = mtou_controller.Controller()

            def action():
                return None

            controller._defer_ui(action)
        fake_cmds.evalDeferred.assert_called_once_with(action)

    def test_a_late_group_refusal_is_counted_not_shown(self):
        # A group still in flight when linkage ended is refused by Unreal; the
        # panel counts it like a cancelled target instead of an error dialog.
        fake_cmds = mock.MagicMock()
        with mock.patch.object(mtou_mayaapi, "utils", None), \
                mock.patch.object(mtou_controller, "cmds", fake_cmds):
            controller = mtou_controller.Controller()
            controller._linkage = mock.Mock()
            controller._report_runtime_error({
                "type": "error", "code": "LINKAGE_NOT_ACTIVE",
                "message": "A group arrived while another host owns time.",
                "kind": "scheduling", "eval_id": "seq@10+0/cam#c1",
                "sample_serial": 2})
        controller._linkage.note_refused.assert_called_once_with(
            "A group arrived while another host owns time.",
            eval_id="seq@10+0/cam#c1", sample_serial=2)
        fake_cmds.evalDeferred.assert_not_called()

    def test_a_scheduling_refusal_without_a_linkage_session_is_not_shown(self):
        # The kind alone makes it scheduling; there is nothing to count it on.
        fake_cmds = mock.MagicMock()
        with mock.patch.object(mtou_mayaapi, "utils", None),                 mock.patch.object(mtou_controller, "cmds", fake_cmds):
            controller = mtou_controller.Controller()
            handled = controller._report_runtime_error({
                "type": "error", "code": "EVAL_STALE", "message": "stale",
                "kind": "scheduling"})
        self.assertFalse(handled)
        fake_cmds.evalDeferred.assert_not_called()


class LinkagePanelTests(unittest.TestCase):
    """Shared harness: one controller, fake bindings and no Unreal peer."""

    def setUp(self):
        self.timers = []
        self.removed_callbacks = []
        self.deferred = []
        self.host = FakeLinkageHost()
        self.session = FakeStreamingSession()
        self.controller = mtou_controller.Controller()
        self.controller._session = self.session
        self.session.statement_handler = (
            lambda reply: self.controller._on_session_statement(
                {"session": self.session}, reply))
        self.controller._scene = FakeCharacterScene()
        self.fake_cmds = self._fake_cmds()
        self.fake_om = self._fake_om()
        harness = self
        host_patch = mock.patch.object(
            mtou_controller.Controller, "_linkage_host",
            lambda controller, scene, object_ids, frame_rate: harness.host)
        host_patch.start()
        self.addCleanup(host_patch.stop)
        error_patch = mock.patch.object(
            mtou_controller.Controller, "_show_error")
        error_patch.start()
        self.addCleanup(error_patch.stop)
        self.show_error = mtou_controller.Controller._show_error

    # ----------------------------------------------------------- fake Maya
    def _fake_cmds(self):
        fake = mock.MagicMock()
        fake.control.return_value = True
        fake.currentUnit.return_value = "film"
        fake.confirmDialog.return_value = "确定"
        windows = set()

        def window(*args, **kwargs):
            name = args[0] if args else kwargs.get("name")
            if kwargs.get("exists"):
                return name in windows
            if kwargs.get("query"):
                return None
            windows.add(name)
            return name

        def delete_ui(name, *args, **kwargs):
            windows.discard(name)

        def eval_deferred(action, *args, **kwargs):
            self.deferred.append(action)

        fake.window.side_effect = window
        fake.deleteUI.side_effect = delete_ui
        fake.evalDeferred.side_effect = eval_deferred
        counter = itertools.count()
        for name in ("text", "button", "checkBox", "optionMenu", "menuItem",
                     "intField", "frameLayout", "rowLayout", "columnLayout",
                     "formLayout", "scriptJob", "separator"):
            getattr(fake, name).side_effect = (
                lambda *args, __name=name, **kwargs:
                    "{0}-{1}".format(__name, next(counter)))
        return fake

    def _fake_om(self):
        class _Timer(object):
            @staticmethod
            def addTimerCallback(interval, callback):
                self.timers.append((interval, callback))
                return len(self.timers)

        class _Message(object):
            @staticmethod
            def removeCallback(callback_id):
                self.removed_callbacks.append(callback_id)

        return type("FakeOpenMaya", (), {
            "MTimerMessage": _Timer,
            "MMessage": _Message,
        })

    # -------------------------------------------------------------- helpers
    @property
    def bindings(self):
        return _Bindings(self.fake_cmds, self.fake_om)

    def enter_linkage(self):
        with self.bindings:
            self.controller.enter_linkage()

    def pump(self):
        """Run the one pump tick the controller's timer would run."""
        with self.bindings:
            self.controller._pump_linkage()

    def run_timer(self):
        interval, callback = self.timers[-1]
        with self.bindings:
            callback()

    def flush(self):
        """Run the panel work the controller deferred to Maya's main thread."""
        while self.deferred:
            self.deferred.pop(0)()

    def labels(self):
        return [call[1].get("label") for call in self.fake_cmds.text.call_args_list
                if call[1].get("label")]

    def build_ui(self):
        with self.bindings:
            self.controller.build_ui()

    # --------------------------------------------------------------- tests
    def test_about_button_shows_the_legal_notice(self):
        self.build_ui()

        button = next(call for call in self.fake_cmds.button.call_args_list
                      if call[1].get("label") == "关于")
        with self.bindings:
            button[1]["command"]()

        message = self.fake_cmds.confirmDialog.call_args[1]["message"]
        for expected in ("版权所有 (C) 2026 Alexis-Li", "不提供任何担保", "再分发和修改",
                         "LICENSE", "https://github.com/Alexis-Li/MtoULiveLink"):
            self.assertIn(expected, message)

    def test_panel_offers_the_linkage_card_with_its_actions(self):
        self.build_ui()

        buttons = [call[1].get("label")
                   for call in self.fake_cmds.button.call_args_list
                   if call[1].get("label")]
        for label in ("进入联动", "返回实时", "结束预览", "打开对象配对窗口"):
            self.assertIn(label, buttons)
        # The read-back window is gone: its values live in 诊断详情.
        self.assertNotIn("打开镜头联动窗口", buttons)
        cards = [call[1].get("label")
                 for call in self.fake_cmds.frameLayout.call_args_list
                 if call[1].get("label")]
        self.assertIn("镜头联动", cards)
        self.assertEqual("未进入联动", self.controller.linkage_status()["line"])
        self.assertIn("未进入联动", self.labels())
        for internal in ("控制方：", "模式：", "目标时间：", "最新完整应用：",
                         "滞后：", "恢复结果："):
            self.assertFalse(any(label.startswith(internal) for label in self.labels()),
                             internal)
        created = [call[1] for call in self.fake_cmds.text.call_args_list
                   if call[1].get("label") == "未进入联动" and not call[1].get("edit")]
        self.assertEqual(mtou_controller.LINK_STATUS_WIDTH, created[0].get("width"))
        # The disabled actions keep a reason exactly like the cache actions.
        tooltips = [call[1].get("annotation")
                    for call in self.fake_cmds.control.call_args_list
                    if call[1].get("annotation")]
        self.assertTrue(any("连接 Unreal" in tooltip for tooltip in tooltips))

    def test_scene_labels_keep_their_column_width_with_the_full_text_as_tooltip(self):
        # R-020: a long root path or outfit name is shortened inside its column
        # instead of widening the panel; the tooltip keeps the whole value.
        self.build_ui()
        for prefix in ("角色根骨骼：", "当前衣服：", "场景帧率："):
            created = [call[1] for call in self.fake_cmds.text.call_args_list
                       if str(call[1].get("label", "")).startswith(prefix)
                       and not call[1].get("edit")]
            self.assertEqual(1, len(created), prefix)
            self.assertEqual(mtou_controller.SCENE_TEXT_WIDTH, created[0].get("width"))
        for label in ("骨骼数：0", "BlendShape 数：0"):
            created = [call[1] for call in self.fake_cmds.text.call_args_list
                       if call[1].get("label") == label and not call[1].get("edit")]
            self.assertEqual(mtou_controller.CACHE_TEXT_WIDTH, created[0].get("width"))
        self.assertLessEqual(mtou_controller.SCENE_TEXT_WIDTH, mtou_controller.CARD_HALF)

        root = "|C01_111_MetaHuman_Body_Rig_Group|DHIbody_root_with_a_very_long_name|root"
        outfit = "Clothes13_LongGarmentVariantName_WithExtraSuffix_v027_服装变体名称"
        snapshot = types.SimpleNamespace(root=root, outfit=outfit, bones=[1] * 214,
                                         curve_names=["c"] * 312, duplicate_paths=())
        self.controller._scene = types.SimpleNamespace(snapshot=lambda: snapshot)
        self.fake_cmds.text.reset_mock()
        with self.bindings:
            self.controller._render_snapshot(snapshot)
        edits = {call[1]["label"]: call[1]["annotation"]
                 for call in self.fake_cmds.text.call_args_list if call[1].get("edit")}
        for prefix, value in (("角色根骨骼：", root), ("当前衣服：", outfit)):
            label = [item for item in edits if item.startswith(prefix)][0]
            self.assertLessEqual(mtou_controller._text_units(label),
                                 mtou_controller.SCENE_LABEL_UNITS, label)
            self.assertIn("…", label)
            self.assertEqual(prefix + value, edits[label])
        root_label = [item for item in edits if item.startswith("角色根骨骼：")][0]
        self.assertTrue(root_label.endswith("|root"), root_label)
        self.assertIn("骨骼数：214", edits)
        self.assertIn("BlendShape 数：312", edits)
        # A short value is shown unchanged.
        self.assertEqual(("当前衣服：Clothes13", "当前衣服：Clothes13"),
                         mtou_controller.scene_label_text("当前衣服：", "Clothes13"))

    def test_linkage_status_line_shows_only_state_and_problems(self):
        def line(mode, lag=None, applied="seq@1", overdue=False, wait=0.0,
                 held=False, recovery=(), failure=None):
            status = types.SimpleNamespace(lag_seconds=lag, applied_eval_id=applied,
                                           apply_failure=failure)
            text, tooltip = mtou_controller.linkage_status_line(
                mode, status, recovery, overdue, wait, held)
            self.assertLessEqual(mtou_controller._text_units(text),
                                 mtou_controller.LINK_STATUS_UNITS, text)
            return text, tooltip

        linkage = mtou_config.LINKAGE_MODE
        self.assertEqual("镜头联动中 · Unreal 控制时间 · 同步正常",
                         line(linkage, lag=0.13)[0])
        self.assertEqual("镜头联动中 · Unreal 控制时间 · 正在同步",
                         line(linkage, applied=None)[0])
        # A refused group is never 同步正常, whether or not one applied before,
        # and is not blamed on Maya when the retry runs past convergence.
        for applied in (None, "seq@1"):
            text, tooltip = line(linkage, applied=applied, failure="display busy")
            self.assertEqual("镜头联动中 · Unreal 控制时间 · 应用失败，重试中", text)
            self.assertIn("display busy", tooltip)
        self.assertEqual("镜头联动中 · Unreal 控制时间 · 应用失败，重试 5.0 秒",
                         line(linkage, lag=0.1, failure="", overdue=True, wait=5.0)[0])
        text, tooltip = line(linkage, lag=0.8)
        self.assertEqual("镜头联动中 · Unreal 控制时间 · Maya 跟不上，滞后 0.8 秒", text)
        self.assertIn("暂停 Sequencer", tooltip)
        self.assertIn("Maya 跟不上，滞后 2.6 秒",
                      line(linkage, lag=0.1, overdue=True, wait=2.6)[0])
        self.assertEqual("镜头联动中 · Unreal 控制时间 · Maya 跟不上，滞后 13 秒",
                         line(linkage, lag=0.1, overdue=True, wait=12.6)[0])
        self.assertEqual("未进入联动", line(mtou_config.REALTIME_MODE)[0])
        self.assertEqual("未进入联动 · 缓存播放中", line(mtou_config.CACHED_MODE)[0])
        text, tooltip = line(mtou_config.REALTIME_MODE, held=True)
        self.assertEqual("未进入联动 · Unreal 仍接管目标", text)
        self.assertIn("结束预览", tooltip)
        text, tooltip = line(mtou_config.REALTIME_MODE, held=True,
                             recovery=("residual: camera persp is locked",
                                       "restore: timeline"))
        self.assertIn("恢复失败 2 项", tooltip.splitlines()[0])
        self.assertIn("手动恢复", tooltip)
        for internal in ("eval", "revision", "样本", "seq@1"):
            self.assertNotIn(internal, text)

    def test_diagnostic_details_keep_the_linkage_read_back(self):
        self.enter_linkage()
        with self.bindings:
            self.session.route(camera_time(display_frame=12.0))
        self.run_timer()
        with self.bindings:
            self.session.route(
                applied_message(eval_id="seq@10+0/cam#c1", sample_serial=1))

        text = self.controller._diagnostic_text()
        for expected in ("镜头联动：", "状态：镜头联动中", "控制方：Unreal",
                         "模式：镜头联动", "mode_revision：1 · session：7",
                         "目标时间：", "已应用求值：seq@10+0/cam#c1", "样本序号：1",
                         "应用年龄：", "滞后：", "已应用 / 已拒绝：1 / 0",
                         "在途目标：", "逐资源恢复：无"):
            self.assertIn(expected, text)

    def test_cache_summary_stays_inside_its_column_with_the_full_text_as_tooltip(self):
        self.build_ui()
        created = [call[1] for call in self.fake_cmds.text.call_args_list
                   if call[1].get("label") == "缓存：无" and not call[1].get("edit")]
        self.assertEqual(1, len(created))
        self.assertEqual(mtou_controller.CACHE_TEXT_WIDTH, created[0].get("width"))
        self.assertLessEqual(mtou_controller.CACHE_TEXT_WIDTH
                             + mtou_controller.CACHE_TEXT_INDENT,
                             mtou_controller.CARD_HALF)

        def summary(start, end, fps):
            return types.SimpleNamespace(capture_range=(start, end),
                                         frame_count=end - start + 1,
                                         scene_fps=fps, capture_time=0.0)

        def render(value):
            self.fake_cmds.text.reset_mock()
            with self.bindings:
                self.controller._update_cache_text(value)
            edit = self.fake_cmds.text.call_args_list[-1][1]
            self.assertTrue(edit.get("edit"))
            return edit["label"], edit["annotation"]

        label, tooltip = render(summary(100, 160, 30.0))
        self.assertEqual("缓存：100-160（61 帧）", label)
        self.assertRegex(tooltip, r"^缓存：100-160，61 帧，30 fps，捕获于 \d{4}-\d\d-\d\d ")
        for start, end in ((-9999, 10000), (-123456789, -123436790),
                           (12345678901234, 12345678921233)):
            label, tooltip = render(summary(start, end, 23.976))
            self.assertLessEqual(mtou_controller._text_units(label),
                                 mtou_controller.CACHE_LABEL_UNITS, label)
            self.assertTrue(label.startswith("缓存："), label)
            self.assertIn("{0}-{1}，{2} 帧，23.976 fps".format(
                start, end, end - start + 1), tooltip)
        self.assertEqual(("缓存：无", "缓存：无"), render(None))

    def test_enter_linkage_refuses_without_a_ready_session(self):
        self.session._ready = False
        self.enter_linkage()

        diagnostic = self.show_error.call_args[0][0]
        self.assertEqual("STREAM_INTERRUPTED", diagnostic["code"])
        self.assertIn("连接", diagnostic["solution"])
        self.assertIsNone(self.controller._linkage)
        self.assertEqual([], self.timers)
        self.assertEqual(0, self.host.captured)
        self.assertEqual(0, self.session.paused)
        self.assertEqual(mtou_config.REALTIME_MODE, self.controller._mode)

    def test_enter_linkage_refuses_while_cached_playback_owns_the_session(self):
        self.session.time_control.acquire(mtou_config.CACHED_MODE, object())
        self.controller._cached_view = mtou_cache.CachedPlaybackView(
            mtou_cache.CachedPlayback.REPLAYING)
        self.enter_linkage()

        diagnostic = self.show_error.call_args[0][0]
        self.assertEqual("CACHE_NOT_CLEARED", diagnostic["code"])
        self.assertIn("实时预览", diagnostic["solution"])
        self.assertIsNone(self.controller._linkage)
        self.assertEqual([], self.timers)
        self.assertEqual(0, self.host.captured)

    def test_enter_linkage_reports_a_host_capture_refusal(self):
        self.host.capture_error = RuntimeError("panel camera missing")
        self.enter_linkage()

        self.assertIsNone(self.controller._linkage)
        self.assertEqual(mtou_config.REALTIME_MODE, self.controller._mode)
        self.assertEqual([], self.timers)
        diagnostic = self.show_error.call_args[0][0]
        self.assertIn("panel camera missing", diagnostic["details"])
        # The refused attempt leaves the session's sampling path untouched.
        self.assertEqual([], self.session.messages)

    def test_enter_linkage_starts_the_pump_and_announces_the_mode(self):
        self.enter_linkage()

        linkage = self.controller._linkage
        self.assertIsNotNone(linkage)
        self.assertTrue(linkage.is_active)
        self.assertEqual(mtou_config.LINKAGE_MODE, self.controller._mode)
        self.assertEqual(1, linkage.mode_revision())
        self.assertEqual(1, self.host.captured)
        self.assertEqual(1, self.session.paused)
        self.assertEqual(7, linkage.status().session_id)
        self.assertEqual(1, len(self.timers))
        self.assertLessEqual(self.timers[0][0], 1.0 / 60.0)
        modes = [message for message in self.session.messages
                 if message["type"] == "mode"]
        self.assertEqual(1, len(modes))
        self.assertEqual("linkage", modes[0]["mode"])
        self.assertEqual("unreal", modes[0]["control"])
        self.assertEqual("ready", modes[0]["transition"])
        self.assertEqual(7, modes[0]["session_id"])
        self.assertEqual("控制方：Unreal",
                         self.controller.linkage_status()["labels"]["control"])
        self.assertEqual("模式：镜头联动",
                         self.controller.linkage_status()["labels"]["mode"])

    def test_pump_applies_the_waiting_target_and_submits_one_group(self):
        self.enter_linkage()

        with self.bindings:
            self.session.route(camera_time(display_frame=12.0))
        self.assertEqual(1, self.controller._linkage.pending_count())

        self.run_timer()

        groups = [message for message in self.session.messages
                  if message["type"] == "group"]
        self.assertEqual(1, len(groups))
        self.assertEqual("seq@10+0/cam#c1", groups[0]["eval_id"])
        self.assertEqual(1, groups[0]["sample_serial"])
        self.assertEqual(1, len(self.host.applied))
        status = self.controller.linkage_status()
        self.assertEqual(1, status["issued"])
        self.assertEqual(0, status["pending"])
        self.assertIn("目标时间：12.00",
                      self.controller.linkage_status()["labels"]["target"])
        self.assertEqual("镜头联动中 · Unreal 控制时间 · 正在同步",
                         self.controller.linkage_status()["line"])

    def test_runtime_reply_routes_every_linkage_message_type(self):
        self.enter_linkage()

        with self.bindings:
            self.session.route(camera_time(eval_serial=3))
        # A routed target waits for the pump; the status keeps it honest.
        self.assertEqual(1, self.controller._linkage.pending_count())

        with self.bindings:
            self.session.route(
                applied_message(eval_id="seq@10+0/cam#c1", sample_serial=1))
        status = self.controller.linkage_status()
        self.assertEqual(1, status["applied_count"])
        self.assertEqual("seq@10+0/cam#c1", status["applied_eval_id"])
        self.assertEqual(0.02, status["lag_seconds"])

        with self.bindings:
            self.session.route({
                "type": "mode_state", "session_id": 7, "mode_revision": 1,
                "mode": "linkage", "control": "unreal", "detail": "UE 已接管",
                "anchor": IDENTITY_ANCHOR})
            # Mode statements are followed on Maya's main thread.
            self.flush()
        self.assertEqual("UE 已接管", self.controller.linkage_status()["detail"])

    def test_messages_of_a_mode_maya_already_left_are_counted_not_reported(self):
        self.enter_linkage()
        self.show_error.reset_mock()
        current = self.controller._linkage.mode_revision()

        with self.bindings:
            self.session.route(
                camera_time(mode_revision=current - 1))
            self.session.route(
                applied_message(mode_revision=current - 1))
            self.flush()

        self.show_error.assert_not_called()
        self.assertEqual(2, self.controller.linkage_status()["refused_count"])
        self.assertEqual(0, self.controller._linkage.pending_count())

    def _send_one_group(self):
        with self.bindings:
            self.session.route(camera_time())
        self.run_timer()

    def _scheduling(self, code, **extra):
        reply = {"type": "error", "code": code, "kind": "scheduling",
                 "message": "refused", "details": "free text"}
        reply.update(extra)
        return reply

    def _assert_counted(self, reply):
        self.show_error.reset_mock()
        refused = self.controller.linkage_status()["refused_count"]
        self._route(reply)
        self.flush()
        self.show_error.assert_not_called()
        self.assertEqual(refused + 1, self.controller.linkage_status()["refused_count"])

    def _assert_reported(self, reply):
        self.show_error.reset_mock()
        refused = self.controller.linkage_status()["refused_count"]
        self._route(reply)
        self.flush()
        self.assertEqual(reply["code"], self.show_error.call_args[0][0]["code"])
        self.assertEqual(refused, self.controller.linkage_status()["refused_count"])

    def test_only_the_kind_decides_a_scheduling_refusal(self):
        # Every code Unreal can mark as scheduling is counted, whatever its
        # details say; the free text is only displayed.
        self.enter_linkage()
        self._send_one_group()
        for code in ("EVAL_STALE", "LINKAGE_NOT_ACTIVE", "SET_REVISION_STALE",
                     "MODE_CONFLICT"):
            with self.subTest(code=code):
                self._assert_counted(self._scheduling(code, details="not a pattern"))

    def test_the_same_codes_without_the_kind_are_reported(self):
        # R-005: a newer set revision or a refused remove_object is a real
        # conflict that used to be counted by its code alone.
        self.enter_linkage()
        self._send_one_group()
        for code in ("EVAL_STALE", "LINKAGE_NOT_ACTIVE", "SET_REVISION_STALE",
                     "MODE_CONFLICT"):
            with self.subTest(code=code):
                self._assert_reported({
                    "type": "error", "code": code, "message": "refused",
                    "details": "group set revision 9, current 3"})
                self._assert_reported({
                    "type": "error", "code": code, "kind": "failure",
                    "message": "refused", "details": "target mode revision 4"})

    def test_a_scheduling_refusal_of_the_issued_sample_frees_the_slot(self):
        self.enter_linkage()
        self._send_one_group()
        linkage = self.controller._linkage
        self.assertEqual(1, linkage.status().issued)
        self._assert_counted(self._scheduling(
            "EVAL_STALE", eval_id="seq@10+0/cam#c1", sample_serial=1))
        self.assertEqual(0, linkage.status().issued)

    def test_a_scheduling_refusal_of_another_sample_keeps_the_slot(self):
        self.enter_linkage()
        self._send_one_group()
        linkage = self.controller._linkage
        for extra in ({"eval_id": "seq@11+0/cam#c1", "sample_serial": 1},
                      {"eval_id": "seq@10+0/cam#c1", "sample_serial": 2},
                      {"eval_id": "seq@10+0/cam#c1"},
                      {"sample_serial": 1},
                      {}):
            with self.subTest(extra=extra):
                self._assert_counted(self._scheduling("EVAL_STALE", **extra))
                self.assertEqual(1, linkage.status().issued)

    def test_the_issued_slot_is_not_freed_by_text(self):
        # The refusal text used to name the issued sample; it no longer counts.
        self.enter_linkage()
        self._send_one_group()
        self._assert_counted(self._scheduling(
            "EVAL_STALE",
            details="group eval seq@10+0/cam#c1, current target none"))
        self.assertEqual(1, self.controller._linkage.status().issued)

    def test_a_missing_kind_reads_as_a_failure(self):
        self.enter_linkage()
        self._assert_reported({"type": "error", "code": "MODE_CONFLICT",
                               "message": "Linkage is already active.",
                               "details": ""})

    def test_a_group_of_a_newer_revision_is_a_failure(self):
        # R-022: Unreal marks a group ahead of its revision as a failure.
        self.enter_linkage()
        self._send_one_group()
        self._assert_reported({
            "type": "error", "code": "MODE_CONFLICT", "kind": "failure",
            "message": "A group named a different mode revision.",
            "details": "group mode revision 9, current 3",
            "eval_id": "seq@10+0/cam#c1", "sample_serial": 1})

    def test_a_refused_mode_message_is_reported_while_a_group_is_in_flight(self):
        # R-022: a conflict about Maya's own transition is never scheduling,
        # however many groups are still on their way.
        self.enter_linkage()
        self._send_one_group()
        self._assert_reported({
            "type": "error", "code": "MODE_CONFLICT", "kind": "failure",
            "message": "A leave_linkage message named an older or repeated mode revision.",
            "details": "current mode revision 3, message 2"})
        self._assert_reported({
            "type": "error", "code": "MODE_CONFLICT", "kind": "failure",
            "message": "A failed transition named a mode revision this session never entered.",
            "details": "message mode revision 5, current 3"})

    def test_a_message_from_a_newer_mode_is_still_a_conflict(self):
        self.enter_linkage()
        self.show_error.reset_mock()
        current = self.controller._linkage.mode_revision()

        with self.bindings:
            self.session.route(
                camera_time(mode_revision=current + 1))
            self.flush()

        self.assertEqual("MODE_CONFLICT", self.show_error.call_args[0][0]["code"])

    def _mode_state(self, revision, mode="linkage", detail=""):
        message = {"type": "mode_state", "session_id": 7, "mode_revision": revision,
                   "mode": mode, "control": "unreal" if mode == "linkage" else "maya",
                   "detail": detail}
        if mode == "linkage":
            message["anchor"] = IDENTITY_ANCHOR
        return message

    def _route(self, reply):
        with self.bindings:
            handled = self.session.route(reply)
            self.flush()
        return handled

    def test_unreal_started_linkage_enters_from_realtime(self):
        self._route(self._mode_state(1))

        linkage = self.controller._linkage
        self.assertIsNotNone(linkage)
        self.assertTrue(linkage.is_active)
        self.assertEqual(mtou_config.LINKAGE_MODE, self.controller._mode)
        self.assertEqual(1, self.host.captured)
        self.assertEqual(1, len(self.timers))
        modes = [(m["mode"], m["mode_revision"], m["transition"])
                 for m in self.session.messages if m["type"] == "mode"]
        self.assertEqual([("linkage", 1, "ready")], modes)

    def test_unreal_started_linkage_is_refused_without_a_role(self):
        self.controller._scene = None
        self._route(self._mode_state(1))

        self.assertIsNone(self.controller._linkage)
        self.assertEqual(0, self.host.captured)
        modes = [(m["mode"], m["mode_revision"], m["transition"])
                 for m in self.session.messages if m["type"] == "mode"]
        # Unreal hears the refusal and hands its writers back at once.
        self.assertEqual([("realtime", 1, "failed")], modes)
        self.assertEqual("ROLE_SETUP_FAILED", self.show_error.call_args[0][0]["code"])

    def test_unreal_leave_restores_maya_and_resumes_realtime(self):
        self.enter_linkage()
        self._route(self._mode_state(1))
        self._route(self._mode_state(2, mode="realtime", detail="UE 返回实时"))

        linkage = self.controller._linkage
        self.assertFalse(linkage.is_active)
        self.assertEqual(mtou_config.REALTIME_MODE, self.controller._mode)
        self.assertEqual(1, len(self.host.restored))
        self.assertEqual(1, self.session.resumed)
        self.assertEqual(2, self.controller._linkage_revision)
        # The pump no longer drives a linkage that ended.
        self.assertIn(1, self.removed_callbacks)
        # A later Maya entry continues the connection's revision.
        self.enter_linkage()
        self.assertEqual(3, self.controller._linkage.mode_revision())

    def test_unreal_refusing_maya_entry_rolls_the_panel_back(self):
        self.enter_linkage()
        self._route(self._mode_state(0, mode="realtime", detail="PAIRING_REQUIRED"))

        self.assertFalse(self.controller._linkage.is_active)
        self.assertEqual(mtou_config.REALTIME_MODE, self.controller._mode)
        self.assertEqual(1, len(self.host.restored))
        self.assertFalse(self.controller._linkage.takeover_held())

    def test_unconfirmed_entry_rolls_back_on_the_pump(self):
        self.enter_linkage()
        linkage = self.controller._linkage
        linkage._clock = lambda: 1e9
        self.pump()

        self.assertFalse(linkage.is_active)
        self.assertEqual(mtou_config.REALTIME_MODE, self.controller._mode)
        self.assertEqual(1, len(self.host.restored))
        failed = [m for m in self.session.messages
                  if m["type"] == "mode" and m["transition"] == "failed"]
        self.assertEqual(1, len(failed))

    def test_end_preview_stays_available_after_returning_to_realtime(self):
        self.enter_linkage()
        with self.bindings:
            self.controller.leave_linkage("realtime")
        enabled = {}
        self.controller._end_linkage_button = "end"
        self.controller._leave_linkage_button = "leave"
        with mock.patch.object(
                self.controller, "_set_enabled",
                side_effect=lambda control, value: enabled.__setitem__(control, value)):
            with self.bindings:
                self.controller._update_linkage_controls()
        self.assertTrue(enabled["end"])
        self.assertFalse(enabled["leave"])

        with self.bindings:
            self.controller.leave_linkage("end")
        exits = [m["target"] for m in self.session.messages
                 if m["type"] == "leave_linkage"]
        self.assertEqual(["realtime", "end"], exits)
        with mock.patch.object(
                self.controller, "_set_enabled",
                side_effect=lambda control, value: enabled.__setitem__(control, value)):
            with self.bindings:
                self.controller._update_linkage_controls()
        self.assertFalse(enabled["end"])

    def test_a_statement_for_another_session_starts_nothing(self):
        stale = self._mode_state(4)
        stale["session_id"] = 999
        self._route(stale)
        self.assertIsNone(self.controller._linkage)
        self.assertEqual(0, self.host.captured)
        self.assertEqual([], self.session.messages)

    def test_a_deferred_statement_of_an_ended_connection_is_dropped(self):
        with self.bindings:
            self.session.route(self._mode_state(1))
        # The connection ends and a new one (session 8) is ready before the
        # main thread runs the deferred statement.
        replacement = FakeStreamingSession(session_id=8)
        self.controller._session = replacement
        with self.bindings:
            self.flush()
        self.assertIsNone(self.controller._linkage)
        self.assertEqual(0, self.host.captured)
        self.assertEqual([], replacement.messages)

    def test_a_stale_exit_does_not_end_the_confirmed_linkage(self):
        self.enter_linkage()
        self._route(self._mode_state(1))
        self._route(self._mode_state(0, mode="realtime", detail="old"))
        self.assertTrue(self.controller._linkage.is_active)
        self.assertEqual(mtou_config.LINKAGE_MODE, self.controller._mode)
        self.assertEqual([], self.host.restored)

    def test_an_error_is_reported_the_same_way_before_and_during_linkage(self):
        # Unreal refusing one message keeps the connection in every mode; the
        # report is also the reason if Unreal closes the connection next.
        error = {"type": "error", "code": "INVALID_MESSAGE",
                 "message": "bad frame", "details": "frame 3"}
        self.assertTrue(self._route(dict(error)))
        self.assertEqual("INVALID_MESSAGE", self.show_error.call_args[0][0]["code"])
        self.assertEqual(["INVALID_MESSAGE"],
                         [reason["code"] for reason in self.session.reasons])
        self.enter_linkage()
        self.show_error.reset_mock()
        self.assertTrue(self._route(dict(error)))
        self.assertEqual("INVALID_MESSAGE", self.show_error.call_args[0][0]["code"])
        self.assertEqual(2, len(self.session.reasons))

    def test_runtime_reply_reports_an_error_reply_without_dropping_the_session(self):
        self.enter_linkage()

        with self.bindings:
            self.session.route({
                "type": "error", "code": "PAIRING_REQUIRED",
                "message": "target is not paired", "details": "no target"})
        self.flush()

        diagnostic = self.controller._last_diagnostic
        self.assertEqual("PAIRING_REQUIRED", diagnostic["code"])
        self.assertTrue(self.controller._linkage.is_active)

    def test_runtime_reply_swallows_a_stale_target_refusal(self):
        self.enter_linkage()

        with self.bindings:
            self.session.route(camera_time(eval_serial=5))
            # An older target than the one already waiting is refused.
            self.session.route(camera_time(eval_serial=4))
        self.flush()

        self.show_error.assert_not_called()
        self.assertTrue(self.controller._linkage.is_active)
        # The refused older target changed nothing; the newer one still waits.
        self.assertEqual(1, self.controller._linkage.pending_count())
        self.run_timer()
        groups = [message for message in self.session.messages
                  if message["type"] == "group"]
        self.assertEqual("seq@10+0/cam#c1", groups[-1]["eval_id"])

    def test_runtime_reply_reports_a_real_refusal(self):
        self.enter_linkage()

        with self.bindings:
            # A message for another session fails the identity check.
            self.session.route(applied_message(session_id=99))
        self.flush()

        self.show_error.assert_called()
        self.assertTrue(self.controller._linkage.is_active)

    def test_leave_linkage_restores_and_resumes_the_session(self):
        self.enter_linkage()

        with self.bindings:
            self.controller.leave_linkage("realtime")

        linkage = self.controller._linkage
        self.assertFalse(linkage.is_active)
        self.assertEqual([{"time": 42.0, "camera": "persp"}], self.host.restored)
        self.assertEqual(1, self.session.resumed)
        # Unreal answers a leave with a mode statement; statements keep
        # reaching the panel and do not end the connection.
        self.assertTrue(self.session.route(self._mode_state(2, mode="realtime")))
        self.assertEqual(mtou_config.REALTIME_MODE, self.controller._mode)
        self.assertEqual(1, len(self.removed_callbacks))
        left = [message for message in self.session.messages
                if message["type"] == "leave_linkage"]
        self.assertEqual(1, len(left))
        self.assertEqual("realtime", left[0]["target"])
        self.assertEqual(2, left[0]["mode_revision"])

    def test_leave_linkage_end_asks_unreal_to_restore_its_writer(self):
        self.enter_linkage()

        with self.bindings:
            self.controller.leave_linkage("end")

        left = [message for message in self.session.messages
                if message["type"] == "leave_linkage"]
        self.assertEqual("end", left[-1]["target"])
        self.assertEqual(1, self.session.resumed)
        self.assertEqual(mtou_config.REALTIME_MODE, self.controller._mode)

    def test_a_failed_restore_is_reported_in_the_status(self):
        self.host.restore_residual = ["camera: persp is locked"]
        self.enter_linkage()
        connected = mock.Mock()
        self.controller._set_connected = connected

        with self.bindings:
            self.controller.leave_linkage("realtime")

        status = connected.call_args[0][1]
        self.assertIn("恢复残留：camera: persp is locked", status)
        self.assertIn("camera: persp is locked",
                      self.controller.linkage_status()["labels"]["recovery"])
        snapshot = self.controller.linkage_status()
        self.assertIn("恢复失败 1 项", snapshot["line"])
        self.assertIn("诊断详情", snapshot["line_tooltip"])
        self.assertTrue(any("camera: persp is locked" in record for record
                            in self.controller.linkage_status()["recovery_records"]))

    def test_leaving_without_linkage_reports_the_refusal(self):
        with self.bindings:
            self.controller.leave_linkage("realtime")

        self.assertEqual("LINKAGE_NOT_ACTIVE", self.show_error.call_args[0][0]["code"])
        self.assertEqual([], self.host.restored)

    def test_disconnect_stops_the_pump_and_restores_every_resource(self):
        self.enter_linkage()

        with self.bindings:
            self.controller.disconnect()

        self.assertIsNone(self.controller._linkage)
        self.assertEqual([{"time": 42.0, "camera": "persp"}], self.host.restored)
        self.assertEqual([1], self.removed_callbacks)
        self.assertIn("camera: persp" if self.host.restore_residual else "无",
                      self.controller.linkage_status()["labels"]["recovery"])

    def test_scene_change_releases_linkage(self):
        self.enter_linkage()

        with self.bindings:
            self.controller._on_scene_change()

        self.assertIsNone(self.controller._linkage)
        self.assertEqual(1, len(self.removed_callbacks))

    def scene_replacement_harness(self):
        """Build the panel with Maya's scene messages; return them by name."""
        registered = {}

        class _SceneMessage(object):
            kMayaExiting, kBeforeNew, kBeforeOpen = "exiting", "before_new", "before_open"

            @staticmethod
            def addCallback(message, callback):
                registered[message] = callback
                return len(registered) + 100

        self.fake_om.MSceneMessage = _SceneMessage
        self.build_ui()
        return registered

    def after_open_notices(self):
        return [call[1]["event"][1] for call in self.fake_cmds.scriptJob.call_args_list
                if call[1].get("event", [None])[0] in ("SceneOpened", "NewSceneOpened")]

    def test_a_late_scene_notice_keeps_the_role_set_in_the_new_scene(self):
        callbacks = self.scene_replacement_harness()
        self.enter_linkage()

        with self.bindings:
            callbacks["before_open"]()
        self.assertIsNone(self.controller._linkage)
        self.assertIsNone(self.controller._scene)
        # The artist sets the role in the opened scene and connects before
        # Maya's idle queue delivers anything about the open.
        new_scene, new_session = FakeCharacterScene(), FakeStreamingSession()
        self.controller._scene, self.controller._session = new_scene, new_session
        with self.bindings:
            for notice in self.after_open_notices():
                notice()

        self.assertIs(new_scene, self.controller._scene)
        self.assertIs(new_session, self.controller._session)
        self.assertFalse(getattr(new_scene, "closed", False))

    def test_a_new_scene_still_releases_linkage_before_it_replaces_the_old(self):
        callbacks = self.scene_replacement_harness()
        self.enter_linkage()

        with self.bindings:
            callbacks["before_new"]()

        self.assertIsNone(self.controller._linkage)
        self.assertIsNone(self.controller._scene)
        self.assertEqual(1, len(self.removed_callbacks))

    def test_without_scene_messages_the_after_open_notice_releases_the_role(self):
        self.build_ui()  # the fake OpenMaya has no MSceneMessage
        self.enter_linkage()
        notices = self.after_open_notices()

        with self.bindings:
            for notice in notices:
                notice()

        self.assertEqual(2, len(notices))
        self.assertIsNone(self.controller._linkage)
        self.assertIsNone(self.controller._scene)

    def test_maya_exit_releases_linkage(self):
        self.enter_linkage()

        with self.bindings:
            self.controller._on_maya_exiting()

        self.assertIsNone(self.controller._linkage)

    def test_a_target_after_leaving_is_dropped_without_a_diagnostic(self):
        self.enter_linkage()
        with self.bindings:
            self.controller.leave_linkage("realtime")
        reported = len(self.show_error.call_args_list)

        with self.bindings:
            # Unreal may still be answering the transition it already saw.
            self.session.route(camera_time(eval_serial=9))
        self.flush()

        self.assertEqual(reported, len(self.show_error.call_args_list))
        self.assertFalse(self.controller._linkage_active())

    def test_cached_mode_is_refused_while_linkage_holds_the_session(self):
        self.enter_linkage()
        cached = mock.Mock()

        def enter():
            # Cached Playback takes its lease from the connection's time control.
            try:
                self.session.time_control.acquire(mtou_config.CACHED_MODE, cached)
            except mtou_timecontrol.TimeControlRefused as refusal:
                raise mtou_cache.CachedPlaybackError(refusal.code, refusal.message)

        cached.enter.side_effect = enter
        self.controller._cached_playback = cached

        with self.bindings:
            self.controller._on_mode_changed(mtou_config.CACHED_MODE)

        self.assertEqual("MODE_CONFLICT", self.show_error.call_args[0][0]["code"])
        self.assertEqual(mtou_config.LINKAGE_MODE, self.controller._mode)
        self.assertTrue(self.controller._linkage.is_active)


class PanelThemeTests(unittest.TestCase):
    """Closing the panel and exiting Maya release the panel theme."""

    def setUp(self):
        self.timers = []
        self.removed_callbacks = []
        self.deferred = []
        self.host = FakeLinkageHost()
        self.session = FakeStreamingSession()
        self.controller = mtou_controller.Controller()
        self.controller._session = self.session
        self.session.statement_handler = (
            lambda reply: self.controller._on_session_statement(
                {"session": self.session}, reply))
        self.controller._scene = FakeCharacterScene()
        self.fake_cmds = self._fake_cmds()
        self.fake_om = self._fake_om()
        self.bindings = _Bindings(self.fake_cmds, self.fake_om)

    def _fake_cmds(self):
        fake = mock.MagicMock()
        fake.control.return_value = True
        fake.currentUnit.return_value = "film"
        fake.confirmDialog.return_value = "确定"
        windows = self.windows = set()

        def window(*args, **kwargs):
            name = args[0] if args else kwargs.get("name")
            if kwargs.get("exists") or kwargs.get("query"):
                return name in windows if kwargs.get("exists") else None
            windows.add(name)
            return name

        def delete_ui(name, *args, **kwargs):
            windows.discard(name)

        fake.window.side_effect = window
        fake.deleteUI.side_effect = delete_ui
        fake.evalDeferred.side_effect = lambda action, *a, **k: self.deferred.append(action)
        return fake

    def _fake_om(self):
        class _Message(object):
            @staticmethod
            def removeCallback(callback_id):
                self.removed_callbacks.append(callback_id)

        return type("FakeOpenMaya", (), {"MMessage": _Message})

    def test_panel_close_and_maya_exit_release_the_panel_theme(self):
        released = []
        with mock.patch.object(mtou_controller, "release_panel_theme",
                               side_effect=released.append):
            self.controller._card_decorations = ["card"]
            self.controller._styled_controls = {"button": "widget"}
            with self.bindings:
                self.controller.close()
            # Qt deletes the decorations with the panel it is closing.
            self.assertEqual([], released)
            self.assertEqual(([], {}), (self.controller._card_decorations,
                                        self.controller._styled_controls))

            self.controller._card_decorations = ["card"]
            with self.bindings:
                self.controller._on_maya_exiting()
                self.controller._on_maya_exiting()

        self.assertEqual([["card"], []], released)
        self.assertEqual([], self.controller._card_decorations)


if __name__ == "__main__":
    unittest.main()
