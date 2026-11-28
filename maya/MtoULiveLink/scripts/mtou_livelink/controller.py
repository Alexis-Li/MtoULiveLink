# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Maya operations and their state, independent of windows and Qt."""

from dataclasses import replace
from types import MappingProxyType
from uuid import uuid4

from . import mayaapi
from .cache import CachedPlayback, CachedPlaybackError
from .config import (
    CACHED_MODE,
    CHARACTER_OBJECT_ID,
    CONTROL_MAYA,
    CONTROL_UNREAL,
    ERROR_KIND_SCHEDULING,
    LINKAGE_MODE,
    REALTIME_MODE,
    PROTOCOL_VERSION,
    __version__,
    SECONDARY_OBJECT_ID,
    WORKFLOW_ANIMATION,
    WORKFLOW_MODEL,
    WORKFLOWS,
)
from .diagnostics import (
    DIAGNOSTICS,
    DiagnosticHistory,
    error_reply_diagnostic,
    make_diagnostic,
    linkage_status_labels,
    linkage_status_line,
)
from .linkage import LinkageError, LinkageSession
from .mayaapi import cmds, om
from .protocol import (
    exact_int,
    make_mode_message,
    validate_reply,
)
from .scene import CharacterScene, CharacterSceneError, long_transform, require_maya, selected_root
from .session import StreamingSession, StreamingSessionError, bind_conflict_status
from .space import frames_per_second, validate_frame_rate
from .timeline import MayaTimeline, load_playback_cap, normalize_playback_cap
from .preferences import ScenePreferences, SceneSettings
from .strings import text


class InteractionCancelled(Exception):
    pass


class Controller:
    def __init__(self):
        self._session = None
        self._scene = None
        self._pending_root = None
        self._capture_args = None
        self._script_jobs = []
        self._maya_subject_id = uuid4().hex
        self._restart = None
        self._input_revision = 1
        self._restart_dispatch = None
        self._restart_released = None
        self._restart_prepared = None
        self._restart_recovery_failure = None
        self._connection_stop_diagnostic = None
        self._pending_stop_status = None
        self._settings = SceneSettings()
        self._preferences = None
        self._scene_path = ""
        self._cached_playback = None
        self._cached_view = None
        self._cached_retention = None
        self._maya_exit_callback = None
        self._scene_replace_callbacks = []
        self._shown_warning_signatures = set()
        self._linkage = None
        self._linkage_pump = None
        self._linkage_pump_generation = 0
        self._linkage_last_recovery = []
        self._linkage_event = None
        self._reported_linkage_failure = None
        # Unreal errors this connection already reported in a dialog; the
        # same error ending the connection does not open a second one.
        self._reported_errors = set()
        self._secondary_scene = None
        self._object_set_detail = ""
        self._observers = []
        self._interact = None
        self._on_host_close = None
        self.closed = False
        self._started = False
        self._scene_generation = 0
        self._reference_refresh_pending = False
        self._last_warning = {"has_warning": False}
        self._difference = None
        self._status = ""
        self._cache_status = ""
        self._pending_connect_attempt = None
        self._pending_secondary_removal = None
        self._cache_attempt = None
        self._connection = "idle"
        self._fps = None
        self._hello = {}
        self._maya_version = None
        self._diagnostics = DiagnosticHistory()

    @property
    def settings(self):
        return self._settings

    def start(self, interact=None, on_host_close=None):
        """Register Maya callbacks and load the settings of the open scene."""
        if self.closed or self._started:
            return
        require_maya()
        mayaapi._in_batch_mode()
        self._started = True
        self._interact = interact
        self._on_host_close = on_host_close
        self._maya_version = cmds.about(version=True)
        self._preferences = ScenePreferences(cmds.internalVar(userPrefDir=True))
        self._script_jobs.append(cmds.scriptJob(event=["timeUnitChanged", self._on_time_unit_changed]))
        messages = getattr(om, "MSceneMessage", None)
        if messages is not None:
            self._maya_exit_callback = messages.addCallback(messages.kMayaExiting, self._on_maya_exiting)
        for before, after, fallback in (("kBeforeNew", "kAfterNew", "NewSceneOpened"),
                                        ("kBeforeOpen", "kAfterOpen", "SceneOpened")):
            if self._add_scene_replace_callback(before, self._on_before_scene_replaced):
                self._add_scene_replace_callback(after, self._on_after_scene_replaced)
            else:
                self._script_jobs.append(cmds.scriptJob(event=[fallback, self._on_scene_change]))
        for name, callback in (
                ("kAfterSave", self._on_scene_saved),
                ("kAfterLoadReference", self._on_reference_changed),
                ("kAfterUnloadReference", self._on_reference_changed),
                ("kAfterImportReference", self._on_reference_changed),
                ("kAfterRemoveReference", self._on_reference_changed)):
            self._add_scene_replace_callback(name, callback)
        self._on_after_scene_replaced()

    def subscribe(self, observer):
        self._observers.append(observer)

        def unsubscribe():
            if observer in self._observers:
                self._observers.remove(observer)

        return unsubscribe

    def _publish(self):
        if self.closed or not self._observers:
            return
        snapshot = self.snapshot()
        for observer in tuple(self._observers):
            observer(snapshot)

    def metrics(self):
        if self._mode != REALTIME_MODE or self._restart is not None or not self._session_ready():
            return {}
        get_metrics = getattr(self._session, "realtime_metrics", None)
        return (get_metrics() or {}) if callable(get_metrics) else {}

    def snapshot(self):
        """Current read-only state, queried on Maya's main thread."""
        character = None
        if self._scene is not None:
            try:
                character = self._scene.snapshot()
            except CharacterSceneError:
                pass
        objects = self.objects_state()
        scene_frame = None
        frame_range = self.settings.range_start, self.settings.range_end
        if self._started and not self.closed:
            try:
                timeline = MayaTimeline()
                scene_frame = timeline.current_frame()
                if not self.settings.custom_range:
                    frame_range = timeline.playback_range()
            except (RuntimeError, ValueError, TypeError):
                pass
        mode = self._mode
        linkage = self.linkage_status()
        return MappingProxyType({
            "connection": "reconnecting" if self._restart is not None else self._connection,
            "status": self._status, "mode": mode,
            "authority": (CONTROL_UNREAL if mode == CACHED_MODE else
                          linkage["control"] if mode == LINKAGE_MODE else CONTROL_MAYA),
            "fps": self._fps, "frame": scene_frame, "frame_range": frame_range,
            "root": character.root if character else self._pending_root,
            "outfit": character.outfit if character else None,
            "bone_count": len(character.bones) if character else 0,
            "curve_count": len(character.curve_names) if character else 0,
            "mesh_count": len(getattr(character, "mesh_paths", ())) if character else 0,
            "duplicates": tuple(getattr(character, "duplicate_paths", ())),
            "secondary": objects["secondary"], "objects": objects,
            "settings": self.settings, "cache": self._cached_view,
            "cache_status": self._cache_status, "linkage": linkage,
            "diagnostic": self._diagnostics.latest,
            "connection_difference": dict(self._difference) if self._difference else None,
            "versions": {
                "maya_host": self._maya_version,
                "maya_plugin": __version__,
                "maya_protocol": PROTOCOL_VERSION,
                "ue_host": self._hello.get("ue_host_version"),
                "ue_plugin": self._hello.get("ue_plugin_version"),
                "ue_protocol": self._hello.get("version"),
            },
            "metrics": self.metrics(), "actions": self.actions(),
        })

    def actions(self):
        ready = self._session_ready()
        active = self._linkage_active()
        cached_owner = self._cached_owns_session()
        two_objects = self._session_has_secondary()
        held = bool(ready and self._linkage is not None and self._linkage.takeover_held())
        busy = self._session is not None or self._restart is not None
        realtime = self._mode == REALTIME_MODE
        view = self._cached_view
        can_capture = bool(view and view.can_capture)
        can_upload = bool(view and view.can_upload)
        can_cancel = bool(view and view.can_cancel)
        can_leave = bool(view and view.can_leave)
        animation = self.settings.workflow == WORKFLOW_ANIMATION
        cached_reason = (text("model_realtime_only") if not animation else
                         text("role_and_connection_required") if not ready else
                         text("cache_no_secondary") if two_objects else
                         text("cache_leave_linkage") if active else
                         text("cache_wait_capture"))
        state = {
            "connect": (True, ""),
            "set_display": (bool(self._pending_root or self._scene), text("set_role_first")),
            "select_duplicates": (bool(self._duplicate_paths()), text("no_duplicate_bones")),
            "blendshapes": (not animation, text("animation_always_passes_blendshapes")),
            "cap": (realtime and not can_cancel, text("cap_requires_realtime")),
            "realtime": (realtime or can_leave, text("realtime_unavailable")),
            "cached": (animation and ready and not (can_cancel or active or two_objects), cached_reason),
            "range": (animation and not can_cancel, text("range_unavailable")),
            "capture": (not realtime and can_capture, text("capture_requires_cached")),
            "upload": (not realtime and can_upload, text("upload_requires_retention")),
            "cancel": (not realtime and can_cancel, text("cancel_requires_capture")),
            "enter_linkage": (ready and not cached_owner and not active,
                              _enter_linkage_reason(ready, cached_owner, active, self._mode)),
            "leave_linkage": (active, text("leave_requires_linkage")),
            "end_linkage": (active or held, text("end_requires_linkage")),
            "disconnecting": (busy, ""),
        }
        objects = self.object_actions()
        state.update(set_secondary=objects["set"], remove_secondary=objects["remove"])
        return state

    def set_workflow(self, workflow):
        if workflow == self.settings.workflow or workflow not in WORKFLOWS:
            return
        self._save_settings(workflow=workflow)
        self._restart_for_input_change()
        self._publish()

    def set_blendshapes(self, enabled):
        if bool(enabled) != self.settings.blendshapes_enabled:
            self._save_settings(blendshapes_enabled=bool(enabled))
            self._restart_for_input_change()
            self._publish()

    def set_popups(self, enabled):
        self._save_settings(show_popups=bool(enabled))
        self._publish()

    def set_playback_cap(self, value):
        value = normalize_playback_cap(value)
        if value == self.settings.playback_cap:
            return
        self._save_settings(playback_cap=value)
        if self._session is not None and self._mode == REALTIME_MODE:
            self._session.change_cap(value)
        self._publish()

    def set_capture_range(self, custom, start=None, end=None):
        changes = {"custom_range": bool(custom)}
        if start is not None:
            changes["range_start"] = int(start)
        if end is not None:
            changes["range_end"] = int(end)
        self._save_settings(**changes)
        self._publish()

    def _selected_capture_range(self):
        if self.settings.custom_range:
            return self.settings.range_start, self.settings.range_end
        return None

    def _refresh_fps(self):
        try:
            self._fps = frames_per_second(cmds.currentUnit(query=True, time=True))
        except (RuntimeError, ValueError):
            self._fps = None
        self._publish()
        return self._fps

    def _set_connected(self, connected, status):
        if connected:
            self._connection = "connected"
        elif self._connection not in ("connecting", "interrupted") or self._session is None:
            if self._connection != "interrupted":
                self._connection = "idle"
        self._status = status
        self._publish()

    def _choose_enum(self, candidates):
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise ValueError(text("display_no_enum"))
        chosen = self._request("choose_enum", tuple(candidates))
        if chosen not in candidates:
            raise InteractionCancelled()
        return chosen

    def _confirm_large_cache(self, estimated_size, total_frames):
        return bool(self._request("large_cache", (estimated_size, total_frames)))

    def _request(self, kind, data):
        if self._interact is not None and not self.closed:
            return self._interact(kind, data)
        return None

    @property
    def _last_diagnostic(self):
        return self._diagnostics.latest

    @_last_diagnostic.setter
    def _last_diagnostic(self, diagnostic):
        self._diagnostics.record(diagnostic)

    def _operation_succeeded(self, operation, stamp=None):
        self._diagnostics.record({}, operation, "success", stamp)

    def _notify_diagnostic(self, diagnostic, warning=False):
        self._request("warning" if warning else "error", diagnostic)

    def _show_warning(self, warning):
        self._notify_diagnostic(self._warning_diagnostic(warning), warning=True)

    def _show_error(self, diagnostic, session=False, operation=None):
        self._diagnostics.record(diagnostic, operation)
        self._publish()
        if self.settings.show_popups or (not session and operation != "preview"
                                         and self._diagnostics.operation not in ("connect", "reconnect")):
            self._notify_diagnostic(diagnostic)

    def _save_settings(self, *, operation=None, **changes):
        if operation is not None:
            self._operation_succeeded(operation)
        settings = replace(self.settings, **changes)
        if settings != self.settings:
            self._settings = settings
            self._write_scene_settings()

    def _write_scene_settings(self):
        if self._preferences is None or not self._scene_path:
            return
        try:
            self._preferences.save(self._scene_path, self.settings)
        except (OSError, ValueError) as error:
            self._diagnostics.record(make_diagnostic("PREFERENCES_WRITE_FAILED", details=str(error)),
                                     "preferences_save", "warning")
            self._publish()

    def _on_scene_saved(self, *unused):
        if self.closed:
            return
        path = cmds.file(query=True, sceneName=True) or ""
        if ScenePreferences.scene_key(path) != ScenePreferences.scene_key(self._scene_path):
            self._scene_path = path
            self._write_scene_settings()

    def _scene_event_sink(self, callback):
        generation = self._scene_generation

        def event_sink(event):
            if not self.closed and generation == self._scene_generation:
                callback(event)

        return event_sink

    def _on_scene_change(self, *unused):
        self._on_before_scene_replaced()
        if self._preferences is not None:
            self._on_after_scene_replaced()

    def _on_before_scene_replaced(self, *unused):
        if self.closed:
            return
        self._scene_generation += 1
        self._reference_refresh_pending = False
        self._clear_role("Maya scene was opened or replaced")
        self._clear_secondary()

    def _on_after_scene_replaced(self, *unused):
        if self.closed:
            return
        self._scene_path = cmds.file(query=True, sceneName=True) or ""
        defaults = SceneSettings(playback_cap=load_playback_cap())
        self._diagnostics = DiagnosticHistory()
        self._connection = "idle"
        self._difference = None
        self._hello = {}
        self._linkage_last_recovery = []
        self._linkage_event = None
        self._status = ""
        try:
            self._settings = self._preferences.load(self._scene_path, defaults)
        except (OSError, ValueError) as error:
            self._settings = defaults
            self._diagnostics.record(make_diagnostic("PREFERENCES_READ_FAILED", details=str(error)),
                                     "preferences", "warning")
        self._restore_saved_roles()
        self._refresh_fps()
        self._publish()

    @staticmethod
    def _exact_scene_path(path):
        return bool(path and (cmds.ls(path, long=True) or []) == [path])

    def _restore_saved_roles(self, references=False):
        settings = self.settings
        missing = []
        for root, display, attribute, secondary in (
                (settings.root, settings.display, settings.display_attribute, False),
                (settings.secondary_root, settings.secondary_display,
                 settings.secondary_display_attribute, True)):
            if not root:
                continue
            scene = self._secondary_scene if secondary else self._scene
            if references and scene is not None:
                try:
                    current_root = scene.snapshot().root
                except CharacterSceneError:
                    current_root = None
                if current_root == root and self._exact_scene_path(root):
                    if not display or (self._exact_scene_path(display)
                                       and getattr(scene, "_display_path", None) == display
                                       and getattr(scene, "_display_attribute", None) == attribute):
                        continue
                if secondary:
                    self.disconnect(status="Maya secondary object changed", code="SUBJECT_CHANGED")
                    self._clear_secondary()
                else:
                    self._clear_role("Maya character changed")
            if not self._exact_scene_path(root) or (secondary and roots_overlap(self._primary_root(), root)):
                missing.append(root)
                continue
            discover_display = True
            if display:
                try:
                    if not self._exact_scene_path(display):
                        raise ValueError(display)
                    CharacterScene._resolve_display(root, display, attribute)
                except (CharacterSceneError, ValueError):
                    missing.append("{0}.{1}".format(display, attribute))
                    display = attribute = None
                    discover_display = False
            try:
                capture = self._capture_secondary if secondary else self._capture_scene
                scene = capture(root, display or None, attribute or None,
                                restoring=True, discover_display=discover_display)
                if secondary:
                    self._secondary_scene = scene
            except CharacterSceneError as error:
                missing.append("{0}\n{1}".format(root, error.details or error.message))
        if missing:
            self._diagnostics.record(
                make_diagnostic("PREFERENCES_ROLE_MISSING", details="\n".join(missing)),
                "preferences", "warning")
            self._status = self._last_diagnostic["summary"]

    def _on_reference_changed(self, *unused):
        if self.closed or self._reference_refresh_pending:
            return
        self._reference_refresh_pending = True
        generation = self._scene_generation

        def restore():
            if self.closed or generation != self._scene_generation:
                return
            self._reference_refresh_pending = False
            self._restore_saved_roles(references=True)
            self._publish()

        self._defer_ui(restore)

    def _add_scene_replace_callback(self, name, callback=None):
        if om is None:
            return False
        messages = getattr(om, "MSceneMessage", None)
        message = getattr(messages, name, None)
        if message is None:
            return False
        self._scene_replace_callbacks.append(messages.addCallback(
            message, callback or self._on_before_scene_replaced))
        return True

    def _on_maya_exiting(self, *unused):
        host_close = self._on_host_close
        self.close()
        if host_close is not None:
            host_close()

    def close(self, *unused):
        if self.closed:
            return
        self.closed = True
        self._scene_generation += 1
        self._observers.clear()
        self._interact = self._on_host_close = None
        for job in self._script_jobs:
            try:
                if cmds.scriptJob(exists=job):
                    cmds.scriptJob(kill=job, force=True)
            except (RuntimeError, TypeError):
                pass
        self._script_jobs.clear()
        callbacks, self._scene_replace_callbacks = self._scene_replace_callbacks, []
        if self._maya_exit_callback is not None:
            callbacks.append(self._maya_exit_callback)
            self._maya_exit_callback = None
        for callback in callbacks:
            try:
                om.MMessage.removeCallback(callback)
            except (RuntimeError, TypeError):
                pass
        self._clear_role("Maya panel closed")
        self._session = None
        self._clear_secondary()

    @property
    def _mode(self):
        """The state that drives time on the connection, read from its time control."""
        control = self._time_control()
        return control.state if control is not None else REALTIME_MODE

    def _time_control(self):
        return getattr(self._session, "time_control", None)

    @property
    def _linkage_revision(self):
        """Last mode revision of this connection; Unreal keeps counting across
        entries, so every new linkage continues from it."""
        control = self._time_control()
        return control.revision if control is not None else 0

    def _note_linkage_revision(self, revision):
        control = self._time_control()
        if control is not None:
            control.note_revision(revision)

    def _warning_signature(self, warning):
        missing_unreal = tuple(sorted(warning.get("missing_in_unreal") or ()))
        missing_maya = tuple(sorted(warning.get("missing_in_maya") or ()))
        remaps = tuple(sorted(warning.get("bone_name_remaps") or ()))
        return (missing_unreal, missing_maya, remaps)

    def _session_ready(self):
        session = self._session
        if session is None:
            return False
        ready = getattr(session, "is_ready", None)
        if callable(ready):
            try:
                return bool(ready())
            except (AttributeError, RuntimeError, TypeError):
                return False
        if ready is not None:
            return bool(ready)
        return False

    def _ensure_cached_playback(self):
        if self.settings.workflow != WORKFLOW_ANIMATION or self._session is None \
                or self._scene is None or self._cached_playback is None:
            raise CachedPlaybackError(
                "CACHED_PLAYBACK_NOT_READY",
                text("cache_requires_role"))
        return self._cached_playback

    def set_mode(self, mode):
        if mode == CACHED_MODE and self.settings.workflow != WORKFLOW_ANIMATION:
            return
        if mode == CACHED_MODE:
            if self._session_has_secondary():
                self._publish()
                self._show_error(make_diagnostic(
                    "OBJECT_SET_UNSUPPORTED",
                    text("cache_single_character"),
                    solution=text("cache_remove_secondary")))
                return
            if not self._session_ready():
                self._publish()
                self._show_error(make_diagnostic(
                    "CACHED_PLAYBACK_NOT_READY",
                    text("cache_requires_role")))
                return
            try:
                # The connection's time control refuses Cached Playback while
                # linkage drives time.
                cached = self._ensure_cached_playback()
                cached.enter()
                self._operation_succeeded("cached_mode")
                self._set_connected(
                    True,
                    text("cache_entered"))
            except CachedPlaybackError as error:
                self._publish()
                self._show_error(make_diagnostic(
                    error.code if error.code in DIAGNOSTICS else "INTERNAL_ERROR",
                    error.message, details=error.details))
        elif self._mode == LINKAGE_MODE:
            # Linkage leaves through its own 返回实时 and 结束预览.
            self._publish()
            self._show_error(make_diagnostic(
                "MODE_CONFLICT",
                text("mode_conflict_linkage"),
                solution=text("exit_linkage_before_mode")))
        else:
            if self._cached_playback is not None and self._mode == CACHED_MODE:
                self._cached_playback.leave()
                self._operation_succeeded("realtime_mode")
            if self._session_ready():
                self._set_connected(True, text("connected_realtime"))
            else:
                self._set_connected(False, text("disconnected"))
        self._publish()

    def capture_cache(self):
        if self._mode != CACHED_MODE:
            return
        self._diagnostics.begin("cache")
        self._cache_attempt = self._diagnostics.stamp
        try:
            cached = self._ensure_cached_playback()
            fps = validate_frame_rate(self._refresh_fps())
            self._cache_status = text("cache_preparing")
            self._publish()
            cached.capture(fps, self._confirm_large_cache,
                           capture_range=self._selected_capture_range())
        except CachedPlaybackError as error:
            diagnostic = make_diagnostic(
                error.code if error.code in DIAGNOSTICS else "INTERNAL_ERROR",
                error.message, details=error.details)
            self._show_error(diagnostic)
        except (RuntimeError, ValueError) as error:
            diagnostic = make_diagnostic("INTERNAL_ERROR", str(error), details=str(error))
            self._show_error(diagnostic)
        finally:
            self._publish()

    def upload_cache(self):
        if self._mode != CACHED_MODE:
            return
        self._diagnostics.begin("cache")
        self._cache_attempt = self._diagnostics.stamp
        try:
            cached = self._ensure_cached_playback()
            cached.upload_retained_cache()
        except CachedPlaybackError as error:
            diagnostic = make_diagnostic(
                error.code if error.code in DIAGNOSTICS else "INTERNAL_ERROR",
                error.message, details=error.details)
            self._show_error(diagnostic)
        finally:
            self._publish()

    def cancel_capture(self):
        if self._cached_playback is not None:
            self._cached_playback.cancel_capture()

    def _on_cached_playback_view(self, view):
        previous = self._cached_view
        self._cached_view = view
        if self.closed or self._restart is not None:
            return
        state = view.state
        status = {
            CachedPlayback.CAPTURING: text("cache_capturing"),
            CachedPlayback.UPLOADING: text("cache_uploading"),
            CachedPlayback.READY: text("cache_ready"),
            CachedPlayback.REPLAYING: text("cache_replaying"),
            CachedPlayback.PAUSED: text("cache_paused"),
            CachedPlayback.COMPLETED: text("cache_completed"),
            CachedPlayback.STOPPED: text("cache_stopped"),
        }.get(state, "")
        if state == CachedPlayback.REPLAYING and not view.current:
            status = text("cache_local_playback")
        elif state == CachedPlayback.REPLAYING and view.loop_round:
            status = text("cache_loop")
        elif state == CachedPlayback.SEEKED:
            status = text("cache_seek")
        elif state == CachedPlayback.PAUSED and view.source_frame is not None:
            status = text("cache_paused_at")
        elif state == CachedPlayback.COMPLETED and view.segment_start is not None:
            status = text("cache_segment_done")
        self._cache_status = status.format(view.current, view.total, view.loop_round,
                                           view.source_frame, view.segment_start,
                                           view.segment_end)
        if view.diagnostic:
            diagnostic = view.diagnostic
            # A cached-playback object is attached to every eligible live
            # connection. Its detached view mirrors a connection failure.
            if state != CachedPlayback.DETACHED and (
                    previous is None or previous.diagnostic != diagnostic):
                self._diagnostics.record(diagnostic, "cache", "failure", self._cache_attempt)
            self._cache_status = diagnostic["summary"]
            if state == CachedPlayback.REALTIME:
                self._cache_status = text("cache_resumed_realtime").format(self._cache_status)
            if state != CachedPlayback.DETACHED:
                self._set_connected(self._session_ready(), self._cache_status)
        elif state == CachedPlayback.READY and (previous is None or previous.state != state):
            self._operation_succeeded("cache", stamp=self._cache_attempt)
        self._publish()

    def _selected_display(self):
        selected = cmds.ls(selection=True, long=True) or []
        if len(selected) != 1:
            raise ValueError(text("select_one_display"))
        return long_transform(selected[0])

    def _duplicate_paths(self):
        if self._scene is None:
            return ()
        try:
            return self._scene.snapshot().duplicate_paths
        except CharacterSceneError:
            return ()

    def select_duplicate_bones(self):
        # A diagnostic action renders the real connection state; it never
        # falsifies the indicator independently of the Streaming session.
        connected = self._session_ready()
        existing = [path for path in self._duplicate_paths() if cmds.objExists(path)]
        if not existing:
            self._publish()
            self._set_connected(connected, text("duplicates_missing"))
            return
        cmds.select(existing, replace=True)
        self._set_connected(connected, text("duplicates_selected").format(len(existing)))

    def _scene_diagnostic(self, error, default_code="ROLE_SETUP_FAILED"):
        code = error.code if error.code in DIAGNOSTICS else default_code
        diagnostic = make_diagnostic(
            code, error.message, details=error.details)
        if error.duplicate_paths:
            diagnostic["duplicate_paths"] = list(error.duplicate_paths)
        return diagnostic

    def _capture_scene(self, root, display=None, display_attribute=None,
                       saved_display_uuid=None, restoring=False, discover_display=True):
        try:
            scene = CharacterScene.capture(
                root, display=display, display_attribute=display_attribute,
                on_event=self._scene_event_sink(self._on_character_scene_event),
                saved_display_uuid=saved_display_uuid, discover_display=discover_display)
        except CharacterSceneError as error:
            if (error.code == "AMBIGUOUS_DISPLAY_ATTRIBUTE" and display is not None
                    and not restoring):
                attribute = self._choose_enum(
                    error.context.get("attribute_candidates", ()))
                return self._capture_scene(root, display, attribute)
            raise
        self._scene = scene
        self._pending_root = None
        if display is not None:
            saved_display_uuid = cmds.ls(display, uuid=True)[0]
        self._capture_args = (root, saved_display_uuid, display_attribute, discover_display)
        self._publish()
        return scene

    def set_role(self):
        require_maya()
        self._diagnostics.begin("role")
        replaced = self._clear_role("Maya character was explicitly set again")
        try:
            root = selected_root()
            secondary = self._secondary_scene
            if secondary is not None and roots_overlap(secondary.snapshot().root, root):
                raise ValueError(
                    text("primary_overlap"))
            self._pending_root = root
            snapshot = self._capture_scene(root).snapshot()
            status = text("role_captured")
            if snapshot.duplicate_paths:
                status += text("duplicate_bones_found").format(
                    len(snapshot.duplicate_paths))
            status += bind_conflict_status(snapshot)
            if replaced:
                status = self._last_diagnostic.get("summary", status) + " · " + status
            self._save_settings(operation="role", root=snapshot.root, display="", display_attribute="")
            self._set_connected(False, status)
        except CharacterSceneError as error:
            if error.code == "AMBIGUOUS_DISPLAY":
                self._pending_root = root
                self._set_connected(
                    False, error.message + text("choose_display_help"))
            else:
                self._pending_root = None
                self._set_connected(False, error.message)
            self._show_error(self._scene_diagnostic(error))
        except (RuntimeError, ValueError) as exc:
            self._set_connected(False, str(exc))
            code = str(exc) if str(exc) in DIAGNOSTICS else "ROLE_SETUP_FAILED"
            self._show_error(make_diagnostic(code, str(exc), solution=str(exc), details=str(exc)))

    def set_display_controller(self):
        self._diagnostics.begin("display")
        root = self._pending_root
        if root is None and self._scene is not None:
            root = self._scene.snapshot().root
        if not root:
            self._show_error(make_diagnostic(
                "INTERNAL_ERROR", text("root_required")))
            return
        try:
            # Validate the requested selection first: an invalid selection
            # must never tear down the live session or a usable scene.
            display = self._selected_display()
            try:
                display, attribute = CharacterScene._resolve_display(root, display, None)
            except CharacterSceneError as error:
                if error.code != "AMBIGUOUS_DISPLAY_ATTRIBUTE":
                    raise
                attribute = self._choose_enum(error.context.get("attribute_candidates", ()))
                display, attribute = CharacterScene._resolve_display(root, display, attribute)
        except CharacterSceneError as error:
            self._show_error(self._scene_diagnostic(error))
            return
        except InteractionCancelled:
            return
        except (RuntimeError, ValueError) as exc:
            code = str(exc) if str(exc) in DIAGNOSTICS else "ROLE_SETUP_FAILED"
            self._show_error(make_diagnostic(code, str(exc), solution=str(exc), details=str(exc)))
            return
        try:
            if self._scene is not None:
                self._scene.set_display_controller(display, attribute)
                self._capture_args = (root, cmds.ls(display, uuid=True)[0], attribute, True)
                self._save_settings(operation="display", root=root,
                                    display=display, display_attribute=attribute)
                self._set_connected(self._session_ready(), text("display_source_set"))
                return
            # One clean termination before the replacement: the old Streaming
            # session ends while the old Character scene is still usable, so
            # a late old-session event can never close the new capture.
            self.disconnect(status=text("display_changing"))
            self._clear_scene(keep_pending=True)
            self._pending_root = root
            snapshot = self._capture_scene(root, display=display,
                                           display_attribute=attribute).snapshot()
            self._save_settings(operation="display", root=snapshot.root,
                                display=display, display_attribute=attribute)
            status = text("display_captured")
            self._set_connected(False, status + bind_conflict_status(snapshot))
        except CharacterSceneError as error:
            self._show_error(self._scene_diagnostic(error))
    def _on_character_scene_event(self, event):
        if event.kind == "character_change_started":
            self._restart_for_input_change()
        elif event.kind in ("outfit_changed", "display_changed"):
            self._publish()
            if event.kind == "outfit_changed":
                if (self._session is None and self._restart is None
                        and self._connection_stop_diagnostic is None):
                    status = text("outfit_changed").format(event.snapshot.outfit)
                    self._set_connected(False, status + bind_conflict_status(event.snapshot))
                else:
                    self._schedule_restart(self._session)
        elif event.kind == "character_invalidated":
            self._on_character_invalidated(event.error)
            self._scene = None
            self._pending_root = None
            self._capture_args = None
            self._publish()

    def _on_character_invalidated(self, error):
        if error.code == "CHARACTER_ROOT_DESTROYED":
            self._maya_subject_id = uuid4().hex
            self.disconnect(status=error.message, code="SUBJECT_CHANGED")
        else:
            self._restart_for_input_change()
            if self._restart is not None and self._session is not None:
                self._session.cancel_restart(self._scene_diagnostic(error))
            else:
                self.disconnect(status=error.message)

    def _restart_for_input_change(self):
        self._input_revision += 1
        session = self._session
        if session is None:
            self._discard_cached_playback()
            return
        operation = session.request_restart(self._input_revision)
        if operation is None:
            if self._restart is None and session.poll_connection():
                self.disconnect(status=text("input_changed_connect"))
            return
        if self._restart is None:
            self._diagnostics.begin("reconnect")
            self._pending_connect_attempt = self._diagnostics.stamp
        self._restart = operation
        self._set_connected(False, text("reconnecting_peer"))
        self._schedule_restart(session)

    def _schedule_restart(self, session):
        if session is None or self._restart_dispatch is session:
            return
        self._restart_dispatch = session

        def advance():
            if self._restart_dispatch is session:
                self._restart_dispatch = None
            if self._session is session:
                self._advance_restart(session)

        self._defer_ui(advance)

    def _release_restart_resources(self, session):
        if self._restart_released is session:
            return None
        self._restart_released = session
        failures = []
        if session is not None:
            session.release_restart_leases()
        try:
            cache_failure = self._discard_cached_playback(verify_restoration=True)
            if cache_failure:
                failures.append(cache_failure)
        except (RuntimeError, ValueError) as error:
            failures.append(str(error))
        failures.extend(self._shutdown_linkage())
        if failures:
            return make_diagnostic("RECOVERY_REFUSED", details="; ".join(failures))
        return None

    def _advance_restart(self, session):
        operation, phase, token = session.restart_status()
        if operation is None:
            return
        self._restart = operation
        self._set_connected(False, text("reconnecting_peer"))
        self._publish()
        if phase is not None:
            failure = self._release_restart_resources(session)
            if failure:
                self._restart_recovery_failure = failure
                session.cancel_restart(failure)
                return
        if operation.failure is not None:
            return
        if phase is None and session.hello_reply is None:
            return
        try:
            if (self._scene is not session.object_scenes()[session.object_id]
                    or self._secondary_scene is not session.secondary_scene
                    or session.removal_pending):
                session.cancel_restart(make_diagnostic(
                    "SUBJECT_CHANGED", details="Maya character or object set changed"))
                return
            scenes = session.object_scenes().values()
            snapshots = [scene.refresh_for_restart() for scene in scenes]
            if len(snapshots) > 1 and self.settings.workflow != WORKFLOW_ANIMATION:
                raise StreamingSessionError(
                    "OBJECT_SET_UNSUPPORTED", text("model_single_character"))
            fps = validate_frame_rate(frames_per_second(cmds.currentUnit(query=True, time=True)))
            if self._session is not session or operation.failure is not None:
                return
            operation, phase, token = session.restart_status()
            if operation is None:
                return
            if phase == "coordinating":
                prepared = (session, self._input_revision)
                if prepared != self._restart_prepared:
                    session.prepare_restart(self._input_revision)
                    self._restart_prepared = prepared
            elif phase == "permitted":
                source, targets = session.restart_identities()
                permit_session_id = session.hello_reply["session_id"]
                self._session = None
                try:
                    handoff = (operation, token, source, targets, permit_session_id)
                    self._start_connection(fps, handoff,
                                           session.maya_subject_id)
                finally:
                    session.stop()
            else:
                session.prepare_restart_init(snapshots, self.settings.workflow, self.settings.blendshapes_enabled)
        except (CharacterSceneError, StreamingSessionError, RuntimeError, ValueError) as error:
            code = getattr(error, "code", "INTERNAL_ERROR")
            diagnostic = make_diagnostic(code, str(error), details=getattr(error, "details", str(error)))
            session.cancel_restart(diagnostic)

    def _on_time_unit_changed(self, *unused):
        if self.closed:
            return
        fps = self._refresh_fps()
        if fps is None:
            if self._session is not None:
                self._session.change_rate(0.0)
            return
        try:
            validate_frame_rate(fps)
        except ValueError:
            if self._session is not None:
                self._session.change_rate(fps)
            return
        if self._session is not None:
            self._session.change_rate(fps)

    def connect(self):
        require_maya()
        if self._session is not None or self._restart is not None:
            return
        try:
            if self._scene is None:
                if self._capture_args is not None:
                    root, display_uuid, attribute, discover_display = self._capture_args
                    self._capture_scene(root, display_attribute=attribute,
                                        saved_display_uuid=display_uuid,
                                        discover_display=discover_display)
                else:
                    self.set_role()
            if self._scene is None:
                return
            self._diagnostics.begin("connect")
            fps = validate_frame_rate(frames_per_second(cmds.currentUnit(query=True, time=True)))
        except CharacterSceneError as error:
            diagnostic = self._scene_diagnostic(error, "INTERNAL_ERROR")
            self._set_connected(False, diagnostic["summary"])
            self._show_error(diagnostic)
            return
        except (RuntimeError, ValueError) as exc:
            code = str(exc) if str(exc) in DIAGNOSTICS else "INVALID_FRAME_RATE" \
                if "frame rate" in str(exc) or "time unit" in str(exc) else "INTERNAL_ERROR"
            diagnostic = make_diagnostic(code, str(exc), details=str(exc))
            self._set_connected(False, diagnostic["summary"])
            self._show_error(diagnostic)
            return
        self._start_connection(fps)

    def _start_connection(self, fps, automatic_restart=None, maya_subject_id=None):
        self._connection_stop_diagnostic = None
        self._pending_connect_attempt = self._diagnostics.stamp
        secondary = self._secondary_scene
        if secondary is not None and self.settings.workflow != WORKFLOW_ANIMATION:
            diagnostic = make_diagnostic(
                "OBJECT_SET_UNSUPPORTED",
                text("model_single_character"),
                solution=text("model_remove_secondary"))
            self._report_connect_failure(diagnostic, automatic_restart)
            return
        self._shown_warning_signatures = set()
        self._object_set_detail = ""
        self._connection = "connecting"
        self._hello = {}
        self._pending_connect_attempt = self._diagnostics.stamp
        self._status = text("connecting_peer")
        self._publish()
        self._reported_errors = set()
        holder = {}

        def on_event(event):
            self._on_streaming_session_event(holder.get("session"), event)

        def on_statement(reply):
            return self._on_session_statement(holder, reply)

        try:
            # Unreal's statements reach the panel from the first one on,
            # whatever drives time on the connection.
            session = StreamingSession.start(
                self._scene, fps, on_event, playback_cap=self.settings.playback_cap,
                maya_version=cmds.about(version=True),
                workflow=self.settings.workflow,
                blendshapes_enabled=self.settings.blendshapes_enabled,
                secondary_scene=secondary, on_statement=on_statement,
                maya_subject_id=maya_subject_id or self._maya_subject_id,
                automatic_restart=automatic_restart)
        except StreamingSessionError as error:
            diagnostic = make_diagnostic(
                error.code if error.code in DIAGNOSTICS else "INTERNAL_ERROR",
                error.message, details=error.details)
            if error.recapture_scene and automatic_restart is None:
                self._clear_scene(keep_capture=True)
            self._report_connect_failure(diagnostic, automatic_restart)
            return
        except (RuntimeError, ValueError) as error:
            self._report_connect_failure(make_diagnostic(
                "INTERNAL_ERROR", details=str(error)), automatic_restart)
            return
        holder["session"] = session
        self._session = session
        self._publish()

    def _report_connect_failure(self, diagnostic, automatic_restart):
        if automatic_restart is not None:
            diagnostic = automatic_restart[0].stop(diagnostic)
            self._clear_restart()
            self._connection_stop_diagnostic = diagnostic
        self._diagnostics.record(diagnostic, "connect", "failure", self._pending_connect_attempt)
        self._connection = "interrupted"
        self._set_connected(False, diagnostic["summary"])
        if automatic_restart is None and self.settings.show_popups:
            self._notify_diagnostic(diagnostic)

    def _clear_restart(self):
        self._restart = None
        self._restart_released = None
        self._restart_prepared = None
        self._restart_recovery_failure = None

    def _on_streaming_session_event(self, session, event):
        if session is None or self._session is not session:
            return
        if self.closed:
            self._session = None
            return
        if event.kind == "restart":
            self._schedule_restart(session)
            return
        if event.kind == "time_control":
            # A lease was taken or released, or Unreal confirmed a cache clear.
            self._publish()
            return
        if event.kind == "ready":
            self._clear_restart()
            # Cached Playback keeps the single-character contract; a two-object
            # set streams realtime and linkage only.
            if self.settings.workflow == WORKFLOW_ANIMATION and not self._session_has_secondary():
                try:
                    cached_playback = CachedPlayback(
                        self._scene, session, on_change=self._on_cached_playback_view,
                        retention=self._cached_retention)
                except CachedPlaybackError as error:
                    diagnostic = make_diagnostic(
                        error.code if error.code in DIAGNOSTICS else "INTERNAL_ERROR",
                        error.message, details=error.details)
                    self._diagnostics.record(diagnostic, "connect", "failure", self._pending_connect_attempt)
                    self._connection = "interrupted"
                    self._set_connected(False, diagnostic["summary"])
                    if not event.automatic and self.settings.show_popups:
                        self._notify_diagnostic(diagnostic)
                    session.stop()
                    return
                self._cached_retention = None
                self._cached_playback = cached_playback
                self._on_cached_playback_view(cached_playback.view)
            warning = event.warning or {}
            self._last_warning = warning
            self._hello = dict(getattr(session, "hello_reply", None) or {})
            self._connection = "connected"
            self._set_connected(True, text("connected_realtime"))
            if warning.get("has_warning"):
                diagnostic = self._warning_diagnostic(warning)
                self._difference = diagnostic
                self._diagnostics.record(diagnostic, "connect", "warning", self._pending_connect_attempt)
                signature = self._warning_signature(warning)
                if signature not in self._shown_warning_signatures:
                    self._shown_warning_signatures.add(signature)
                    if not event.automatic and self.settings.show_popups:
                        self._show_warning(warning)
            else:
                self._operation_succeeded("connect", stamp=self._pending_connect_attempt)
            self._publish()
            return
        cached_playback = self._cached_playback
        mode = self._mode
        automatic = event.automatic or self._restart is not None
        if automatic:
            recovery = self._restart_recovery_failure or self._release_restart_resources(session)
        elif cached_playback is not None:
            self._cached_retention = cached_playback.detach(event)
            self._cached_view = cached_playback.view
        self._session = None
        # The negotiated session is gone, so linkage can no longer answer
        # targets: release the pump, the session and every borrowed resource.
        self._shutdown_linkage()
        self._clear_restart()
        self._publish()
        if event.kind == "failed":
            diagnostic = event.diagnostic or make_diagnostic("INTERNAL_ERROR")
            if automatic and recovery is not None and diagnostic["code"] != "RECOVERY_REFUSED":
                previous = diagnostic.get("details") or diagnostic["summary"]
                diagnostic = make_diagnostic("RECOVERY_REFUSED", details="{0}\n{1}".format(
                    recovery["details"], previous))
            signature = _error_signature(diagnostic)
            shown = signature in self._reported_errors
            if not shown and signature != _error_signature(self._last_diagnostic):
                runtime_mode = diagnostic.get("runtime_mode")
                failed_mode = runtime_mode or mode
                operation = {CACHED_MODE: "cache", LINKAGE_MODE: "linkage"}.get(
                    failed_mode, "preview" if runtime_mode == REALTIME_MODE else "connect")
                stamp = self._pending_connect_attempt if self._connection == "connecting" else None
                self._diagnostics.record(diagnostic, operation, "failure", stamp)
            self._connection = "interrupted"
            if automatic:
                self._connection_stop_diagnostic = diagnostic
            if event.recapture_scene:
                self._clear_scene(keep_capture=True)
            self._set_connected(False, diagnostic["summary"])
            if not automatic and not shown and self.settings.show_popups:
                # Unreal closing after an error it reported shows that one dialog.
                self._notify_diagnostic(diagnostic)
            return
        if event.kind == "stopped":
            status = self._pending_stop_status or text("disconnected_status")
            self._pending_stop_status = None
            self._connection = "idle"
            self._set_connected(False, status)


    def _warning_diagnostic(self, warning):
        has_blendshape_warning = bool(
            warning["missing_in_unreal"] or warning["missing_in_maya"])
        diagnostic = make_diagnostic(
            "BLENDSHAPE_MISMATCH" if has_blendshape_warning else "BONE_NAME_REMAP")
        only_maya = warning["missing_in_unreal"]
        only_unreal = warning["missing_in_maya"]
        remaps = warning["bone_name_remaps"]
        diagnostic["details"] = text("connection_difference_details").format(
            "、".join(only_maya) or text("none"),
            "、".join(only_unreal) or text("none"), "、".join(remaps) or text("none"))
        summary = []
        if has_blendshape_warning:
            summary.append(text("blendshape_difference_count").format(len(only_maya), len(only_unreal)))
        if remaps:
            summary.append(text("bone_remap_count").format(len(remaps)))
        diagnostic["difference_summary"] = " · ".join(summary)
        if warning["bone_name_remaps"]:
            diagnostic["duplicate_paths"] = list(self._duplicate_paths())
        return diagnostic

    def disconnect(self, keep_error=False, status=text("disconnected_status"), code=None):
        del keep_error
        if code is None and self._session is not None:
            self._operation_succeeded("disconnect")
        session = self._session
        if session is None:
            self._connection = "idle"
            self._discard_cached_playback()
            self._shutdown_linkage()
            self._set_connected(False, status)
            return
        operation, unused_phase, unused_token = session.restart_status()
        diagnostic = make_diagnostic(code or "RESTART_CANCELLED", details=status)
        if operation is not None:
            diagnostic = operation.stop(diagnostic)
        if operation is not None or code is not None:
            session.fence_for_disconnect(diagnostic)
            recovery = self._restart_recovery_failure or self._release_restart_resources(session)
        else:
            self._discard_cached_playback()
            self._shutdown_linkage()
            session.fence_for_disconnect(diagnostic)
            recovery = None
        self._clear_restart()
        if recovery:
            self._last_diagnostic = recovery
            self._connection_stop_diagnostic = recovery
            status = recovery["summary"]
        elif code or operation is not None:
            self._last_diagnostic = diagnostic
            self._connection_stop_diagnostic = diagnostic
            status = diagnostic["summary"]
        self._pending_stop_status = status
        session.stop()

    def _discard_cached_playback(self, verify_restoration=False):
        cached_playback, self._cached_playback = self._cached_playback, None
        retention, self._cached_retention = self._cached_retention, None
        if retention is not None:
            retention._discard()
        recovery = None
        if cached_playback is not None:
            recovery = (cached_playback.discard(verify_restoration=True) if verify_restoration
                        else cached_playback.discard())
        self._cached_view = None
        self._cache_status = ""
        return recovery

    def _clear_scene(self, keep_pending=False, keep_capture=False):
        self._discard_cached_playback()
        scene, self._scene = self._scene, None
        if scene is not None:
            scene.close()
        if not keep_pending:
            self._pending_root = None
        if not keep_capture:
            self._capture_args = None

    def _clear_role(self, replacement=None):
        replaced = replacement is not None and self._session is not None
        self.disconnect(status=replacement if replaced else text("no_character"),
                        code="SUBJECT_CHANGED" if replaced else None)
        self._maya_subject_id = uuid4().hex
        self._clear_scene()
        self._publish()
        return replaced

    # ------------------------------------------------------------- linkage
    def enter_linkage(self):
        """Take exclusive Unreal time and camera control over one character.

        Refuses with an actionable diagnostic whenever the session cannot
        support linkage: no negotiated session, a Maya scene the host cannot
        capture, or a time control that refuses it (Cached Playback still
        holding the connection).
        """
        require_maya()
        if self._linkage_active():
            self._publish()
            return
        self._diagnostics.begin("linkage")
        self._start_linkage()

    def _start_linkage(self, remote_revision=None):
        """Enter linkage for the panel's action or for Unreal's transition.

        ``remote_revision`` is the revision of a transition Unreal started.
        Every refusal of such a transition is announced to Unreal as failed,
        so it hands back the writers it borrowed instead of waiting out its
        own confirmation timeout.
        """
        session = self._session
        remote = remote_revision is not None

        def refuse(diagnostic):
            if remote:
                self._announce_remote_refusal(session, remote_revision, diagnostic)
            self._show_error(diagnostic, operation="linkage")

        if session is None or not self._session_ready():
            refuse(make_diagnostic(
                "STREAM_INTERRUPTED",
                text("linkage_requires_connection"),
                solution=text("linkage_setup_connection")))
            return
        if self._scene is None:
            refuse(make_diagnostic(
                "ROLE_SETUP_FAILED",
                text("linkage_requires_role"),
                solution=text("linkage_setup_root")))
            return
        session_id = self._session_id_of(session)
        if session_id is None:
            refuse(make_diagnostic(
                "STREAM_INTERRUPTED",
                text("linkage_session_unknown"),
                solution=text("linkage_wait_handshake")))
            return
        try:
            fps = validate_frame_rate(
                frames_per_second(cmds.currentUnit(query=True, time=True)))
        except (RuntimeError, ValueError) as exc:
            refuse(make_diagnostic("INVALID_FRAME_RATE", str(exc),
                                   details=str(exc)))
            return
        object_id = getattr(session, "object_id", None) or CHARACTER_OBJECT_ID
        object_ids = list(getattr(session, "set_objects", None) or (object_id,))
        set_revision = getattr(session, "set_revision", None) or 1
        space_revision = getattr(session, "space_revision", None) or 1
        if getattr(session, "removal_pending", False):
            refuse(make_diagnostic(
                "MODE_CONFLICT",
                text("removing_secondary"),
                solution=text("linkage_wait_secondary")))
            return
        try:
            host = self._linkage_host(self._scene, object_ids, fps)
        except ImportError as exc:
            refuse(make_diagnostic("INTERNAL_ERROR", str(exc), details=str(exc)))
            return
        previous = self._linkage
        floor = max(self._linkage_revision,
                    previous.mode_revision() if previous is not None else 0)
        linkage = LinkageSession(
            host, session, on_event=self._on_linkage_event,
            object_id=object_id, set_revision=set_revision,
            space_revision=space_revision, mode_revision=floor,
            object_ids=object_ids,
            on_reply_error=lambda error: self._on_linkage_reply_error(linkage, error))
        revision = remote_revision if remote else floor + 1
        try:
            linkage.set_session_id(session_id)
            # The linkage session takes the time-control lease, or refuses
            # with the reason, before Maya changes anything.
            linkage.enter(revision, remote=remote)
        except StreamingSessionError as error:
            linkage.close()
            refuse(make_diagnostic(
                error.code if error.code in DIAGNOSTICS else "INTERNAL_ERROR",
                error.message, details=error.details))
            return
        except Exception as error:  # the host refused; the previous mode stands
            # The refused entry released its lease; report why linkage never
            # started.
            self._report_linkage_failure(error)
            return
        self._linkage = linkage
        self._note_linkage_revision(linkage.mode_revision())
        self._linkage_event = None
        self._reported_linkage_failure = None
        self._start_linkage_pump()
        self._operation_succeeded("linkage")
        self._set_connected(True, text("linkage_following"))
        self._publish()

    def _announce_remote_refusal(self, session, revision, diagnostic):
        """Tell Unreal that Maya cannot follow the transition it started."""
        session_id = self._session_id_of(session)
        if session is None or session_id is None or not revision:
            return
        try:
            session.submit_linkage(make_mode_message(
                session_id, revision, REALTIME_MODE, CONTROL_MAYA, "failed",
                diagnostic.get("summary") or diagnostic.get("code") or ""))
        except (StreamingSessionError, RuntimeError, TypeError, ValueError,
                AttributeError):
            pass

    def _on_remote_mode_state(self, reply, received_session=None):
        """Follow one Unreal authority statement on Maya's main thread.

        The statement belongs to the connection that received it: a deferred
        statement whose connection ended, or that names another session, never
        acts on the current one.
        """
        session = self._session
        if session is None or getattr(session, "restarting", False):
            return
        if received_session is not None and received_session is not session:
            return
        if exact_int(reply.get("session_id")) != self._session_id_of(session):
            return
        if not self._session_ready():
            return
        revision = exact_int(reply.get("mode_revision"))
        if revision is None:
            return
        linkage = self._linkage
        if linkage is None:
            if reply.get("mode") == LINKAGE_MODE and revision > self._linkage_revision:
                self._start_linkage(remote_revision=revision)
            else:
                self._note_linkage_revision(revision)
            return
        try:
            outcome = linkage.note_mode_state(reply)
        except LinkageError as error:
            if error.code != "RECOVERY_REFUSED":
                self._report_linkage_refusal(error)
                return
            diagnostic = make_diagnostic(error.code, details=error.details)
            refusal = make_mode_message(
                reply["session_id"], revision, REALTIME_MODE, CONTROL_MAYA, "failed",
                "{0}: {1}".format(error.code, error.details))
            self._stop_linkage_pump()
            session.fail_after_mode_refusal(diagnostic, refusal)
            self._publish()
            return
        if outcome == "superseded":
            self._finish_linkage_exit(linkage, text("linkage_remote_entry"))
        if outcome in ("enter", "superseded"):
            self._start_linkage(remote_revision=revision)
            return
        self._note_linkage_revision(linkage.mode_revision())
        if outcome == "left":
            self._finish_linkage_exit(
                linkage, text("linkage_remote_left"))
        self._publish()

    def _finish_linkage_exit(self, linkage, status):
        """Common tail of every exit Maya did not start from its own panel."""
        self._stop_linkage_pump()
        self._note_linkage_revision(linkage.mode_revision())
        records = linkage.recovery_records()
        event = self._linkage_event
        diagnostic = getattr(event, "diagnostic", None)
        detail = getattr(event, "detail", "") or ""
        if detail:
            status = "{0}（{1}）".format(status, detail)
        self._set_connected(self._session_ready(), status)
        if isinstance(diagnostic, dict) and diagnostic.get("code"):
            if records:
                self._report_linkage_failure(diagnostic)
            else:
                self._diagnostics.record(diagnostic, "linkage", "failure")
                self._publish()

    def leave_linkage(self, target):
        """Return to realtime or end the preview; Maya always restores itself.

        ``realtime`` keeps the connection with the Maya timeline paused at the
        restored frame; ``end`` also tells Unreal to restore its original
        writer and keeps the connection realtime.
        """
        if target not in ("realtime", "end"):
            raise ValueError("linkage exit target must be 'realtime' or 'end'")
        linkage = self._linkage
        if linkage is None or not (linkage.is_active or target == "end"):
            self._show_error(make_diagnostic(
                "LINKAGE_NOT_ACTIVE",
                text("linkage_inactive"),
                solution=text("linkage_first")))
            return
        self._stop_linkage_pump()
        try:
            residual = linkage.leave(target, linkage.mode_revision() + 1)
        except (LinkageError, StreamingSessionError) as error:
            self._report_linkage_failure(error)
            self._start_linkage_pump()
            return
        # The dispatcher stays installed for the session's lifetime: Unreal
        # answers a leave with a mode statement. Leaving released the
        # linkage lease when linkage held one; 结束预览 during Cached Playback
        # leaves the cache's lease alone.
        self._note_linkage_revision(linkage.mode_revision())
        status = (text("linkage_returned")
                  if target == "realtime"
                  else text("linkage_ended"))
        if residual:
            status += text("restoration_residual").format("；".join(residual))
        if not residual:
            self._operation_succeeded("linkage_exit")
        self._set_connected(self._session_ready(), status)
        self._publish()

    def _shutdown_linkage(self):
        """Release every linkage resource."""
        self._stop_linkage_pump()
        linkage, self._linkage = self._linkage, None
        if linkage is None:
            return ()
        try:
            residual = linkage.close()
        except (RuntimeError, ValueError, TypeError) as error:
            residual = (str(error),)
        self._linkage_last_recovery = list(linkage.recovery_records())
        return residual or ()

    def _linkage_active(self):
        linkage = self._linkage
        return bool(linkage is not None and linkage.is_active)

    def _cached_owns_session(self):
        """True while Cached Playback holds the connection or its clear is unconfirmed."""
        control = self._time_control()
        return bool(control is not None and control.cache_holds_connection())

    def _linkage_host(self, scene, object_ids, frame_rate):
        """Build the Maya host adapter that applies linkage targets.

        One seam keeps the panel importable and testable wherever the
        Maya-only host module is unavailable.
        """
        from .link_host import MayaLinkageHost

        scenes = getattr(self._session, "object_scenes", None)
        return MayaLinkageHost(scene, object_ids, frame_rate,
                               scenes=scenes() if callable(scenes) else None)

    def _start_linkage_pump(self):
        """Apply waiting targets on Maya's main thread at a bounded rate."""
        if self._linkage is None or self._linkage_pump is not None or om is None:
            return
        self._linkage_pump_generation += 1
        generation = self._linkage_pump_generation

        def on_timer(*unused):
            del unused
            if generation != self._linkage_pump_generation:
                return
            self._pump_linkage()

        try:
            self._linkage_pump = om.MTimerMessage.addTimerCallback(
                LINKAGE_PUMP_INTERVAL, on_timer)
        except (AttributeError, RuntimeError, TypeError):
            self._linkage_pump = None

    def _stop_linkage_pump(self):
        self._linkage_pump_generation += 1
        timer_id, self._linkage_pump = self._linkage_pump, None
        if timer_id is None or om is None:
            return
        try:
            om.MMessage.removeCallback(timer_id)
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _pump_linkage(self):
        """One pump step: apply the waiting target, then refresh the panel."""
        linkage = self._linkage
        if linkage is None:
            return
        session = self._session
        poll = getattr(session, "poll_connection", None)
        if callable(poll) and not poll():
            # Linkage paused live sampling, so only this pump notices that
            # Unreal ended the connection; the session's terminal event then
            # restores Maya on the main thread.
            return
        if getattr(session, "restarting", False):
            return
        if linkage.confirmation_overdue():
            linkage.expire_unconfirmed()
            self._finish_linkage_exit(
                linkage, text("linkage_not_confirmed"))
            self._publish()
            self._publish()
            return
        try:
            linkage.pump()
        except (LinkageError, StreamingSessionError) as error:
            if not getattr(session, "restarting", False):
                self._report_linkage_failure(error, repeat=True)
        except Exception as error:  # the timer must outlive one bad tick
            self._report_linkage_failure(
                make_diagnostic("INTERNAL_ERROR", str(error), details=str(error)),
                repeat=True)

    def _on_session_statement(self, holder, reply):
        """One connection-level Unreal statement, in any mode (sender thread).

        Statements that touch Maya are followed on the main thread, in arrival
        order; the connection they belong to is resolved there. Returns False
        for an error that is routine scheduling rather than a reason.
        """
        kind = reply.get("type")
        if kind == "mode_state":
            self._defer_ui(lambda reply=dict(reply):
                           self._on_remote_mode_state(reply, holder.get("session")))
        elif kind == "object_removed":
            self._on_object_removed(reply, holder)
        elif kind == "error":
            return self._report_runtime_error(reply, holder)
        return None

    def _on_linkage_reply_error(self, source, error):
        """A target or receipt the linkage session refused (sender thread)."""
        stamp = self._diagnostics.stamp

        def report():
            if self.closed or source is not self._linkage or stamp != self._diagnostics.stamp:
                return
            if isinstance(error, LinkageError):
                self._report_linkage_refusal(error)
            else:
                self._report_linkage_failure(
                    make_diagnostic("INVALID_MESSAGE", str(error), details=str(error)))

        self._defer_ui(report)

    def _report_runtime_error(self, reply, holder=None):
        """Report one Unreal error that belongs to no cache operation.

        Returns False when Unreal marked it ``kind: "scheduling"``, which is
        counted instead. The connection stays unless Unreal closes it. An error
        still in flight when Maya ended the connection is not reported.
        """
        holder = holder if holder is not None else {"session": self._session}
        received_session = holder.get("session")
        if (_stopped_by_maya(received_session) or
                (received_session is not None and received_session is not self._session)):
            return False
        if reply.get("kind") == ERROR_KIND_SCHEDULING:
            # Unreal refused a group its own scheduling made obsolete: the
            # target a pause, jump or camera edit cancelled, a group still in
            # flight when linkage ended, or one a mode or set change overtook.
            # That is normal scheduling, counted, never a dialog. Only the
            # field decides: ``code`` and ``details`` do not, so every other
            # error, whatever its code, is reported.
            linkage = self._linkage
            if linkage is not None:
                linkage.note_refused(
                    reply.get("details") or reply.get("message") or "",
                    eval_id=reply.get("eval_id"),
                    sample_serial=exact_int(reply.get("sample_serial")))
            return False
        diagnostic = error_reply_diagnostic(reply)
        stamp = self._diagnostics.stamp
        operation = "linkage" if self._mode == LINKAGE_MODE else "preview"
        self._defer_ui(lambda: self._show_runtime_error(
            diagnostic, holder.get("session"), operation, stamp))
        return True

    def _show_runtime_error(self, diagnostic, session, operation, stamp):
        if (self.closed or session is None or self._session is not session
                or _stopped_by_maya(session) or getattr(session, "restarting", False)):
            return
        signature = _error_signature(diagnostic)
        if signature in self._reported_errors:
            return
        self._reported_errors.add(signature)
        self._diagnostics.record(diagnostic, operation, "failure", stamp)
        self._publish()
        if self.settings.show_popups and stamp == self._diagnostics.stamp:
            self._notify_diagnostic(diagnostic)

    def _on_linkage_event(self, event):
        """Keep the state machine's last event; its diagnostic is the reason."""
        self._linkage_event = event

    def _report_linkage_refusal(self, error):
        """Report one LinkageError refusal and keep the session alive."""
        if getattr(error, "code", None) == "EVAL_STALE" \
                or getattr(error, "stale", False):
            # A cancelled or superseded target is normal while scrubbing, and
            # a message of a mode Maya already left is still in flight after
            # Maya's own transition; both are counted, not reported.
            linkage = self._linkage
            if linkage is not None and getattr(error, "stale", False):
                linkage.note_refused()
            return
        self._report_linkage_failure(error)

    def _report_linkage_failure(self, error, repeat=False):
        """Report one linkage failure through the panel's diagnostic path."""
        code = getattr(error, "code", None)
        message = getattr(error, "message", None) or str(error)
        details = getattr(error, "details", None) or message
        if not (isinstance(code, str) and code in DIAGNOSTICS):
            # A host refusal carries no protocol code; the state machine
            # already described it in its own failed event.
            event = getattr(self, "_linkage_event", None)
            event_diagnostic = getattr(event, "diagnostic", None)
            if isinstance(event_diagnostic, dict) and event_diagnostic.get("code"):
                code = event_diagnostic.get("code")
                message = event_diagnostic.get("message") or message
                details = event_diagnostic.get("details") or details
        diagnostic = make_diagnostic(
            code if code in DIAGNOSTICS else "INTERNAL_ERROR", message,
            details=details)
        if repeat:
            # A failing tick repeats every pump; one report per distinct reason.
            signature = (diagnostic["code"], diagnostic["details"])
            if signature == self._reported_linkage_failure:
                return
            self._reported_linkage_failure = signature
        self._show_error(diagnostic, operation="linkage")

    def _defer_ui(self, action):
        """Run one panel update on Maya's main thread."""
        if cmds is None:
            action()
            return
        try:
            mayaapi.run_deferred(action, cmds.evalDeferred)
        except (AttributeError, RuntimeError, TypeError):
            action()

    def _session_id_of(self, session):
        """Unreal's session id for this connection, from the hello reply."""
        if session is None:
            return None
        reply = getattr(session, "hello_reply", None)
        if callable(reply):
            try:
                reply = reply()
            except (AttributeError, RuntimeError, TypeError):
                reply = None
        if not isinstance(reply, dict):
            return None
        return exact_int(reply.get("session_id"))

    # -------------------------------------------------------- linkage views
    def linkage_active(self):
        """True while linkage holds the exclusive time-control state."""
        return self._linkage_active()

    def linkage_status(self):
        """The authoritative linkage snapshot every window renders."""
        linkage = self._linkage
        status = linkage.status() if linkage is not None else None
        mode = status.mode if status is not None else self._mode
        control = status.control if status is not None else CONTROL_MAYA
        recovery = self._linkage_recovery()
        overdue = bool(linkage.convergence_overdue()) if linkage is not None else False
        wait = _linkage_wait_seconds(linkage) if overdue else 0.0
        held = bool(linkage.takeover_held()) if linkage is not None else False
        active = mode == LINKAGE_MODE
        line, line_tooltip = linkage_status_line(
            mode, status, recovery, overdue, wait, held)
        snapshot = {
            "active": active,
            "mode": mode,
            "control": control,
            "mode_revision": status.mode_revision if status is not None else 0,
            "session_id": status.session_id if status is not None else None,
            "set_revision": status.set_revision if status is not None else None,
            "space_revision": status.space_revision if status is not None else None,
            "target": status.target if status is not None else None,
            "target_display_frame": (
                status.target_display_frame if status is not None else None),
            "applied_eval_id": (
                status.applied_eval_id if status is not None else None),
            "applied_display_frame": (
                status.applied_display_frame if status is not None else None),
            "applied_sample_serial": (
                status.applied_sample_serial if status is not None else None),
            "applied_age": status.applied_age if status is not None else None,
            "lag_seconds": status.lag_seconds if status is not None else None,
            "pending": status.pending if status is not None else 0,
            "issued": status.issued if status is not None else 0,
            "applied_count": status.applied_count if status is not None else 0,
            "refused_count": status.refused_count if status is not None else 0,
            "apply_failure": status.apply_failure if status is not None else None,
            "converged": bool(status.converged) if status is not None else False,
            "overdue": overdue,
            "wait_seconds": wait,
            "takeover_held": held,
            "recovery": recovery,
            "recovery_records": recovery,
            "detail": (status.detail if status is not None else
                       text("linkage_not_started")),
            "labels": linkage_status_labels(
                mode, control, status, recovery, overdue, active),
            "line": line,
            "line_tooltip": line_tooltip,
        }
        return snapshot

    def _linkage_recovery(self):
        linkage = self._linkage
        if linkage is not None:
            return tuple(linkage.recovery_records())
        return tuple(self._linkage_last_recovery)


    # ------------------------------------------------------------ object set
    def _session_has_secondary(self):
        """True while the connection's negotiated set holds the second object."""
        return bool(getattr(self._session, "secondary_scene", None))

    def _primary_root(self):
        snapshot = getattr(self._scene, "snapshot", None)
        if callable(snapshot):
            try:
                return snapshot().root
            except CharacterSceneError:
                return None
        return self._pending_root

    def set_secondary_object(self):
        """Capture the selected root as the second object of the set.

        The set is negotiated when the connection starts, so a change while
        connected ends the connection and the user connects again.
        """
        require_maya()
        self._diagnostics.begin("secondary")
        try:
            root, display = selected_secondary_root()
            primary = self._primary_root()
            if primary and roots_overlap(primary, root):
                raise ValueError(
                    text("secondary_overlap"))
        except (RuntimeError, ValueError) as exc:
            self._show_error(make_diagnostic(
                "ROLE_SETUP_FAILED", str(exc), solution=str(exc), details=str(exc)))
            return
        replaced = self._session is not None
        if replaced:
            self.disconnect(status="Maya secondary object was replaced", code="SUBJECT_CHANGED")
        self._maya_subject_id = uuid4().hex
        self._clear_secondary()
        try:
            scene = self._capture_secondary(root, display)
        except InteractionCancelled:
            return
        except CharacterSceneError as error:
            self._show_error(self._scene_diagnostic(error))
            self._publish()
            return
        self._secondary_scene = scene
        snapshot = scene.snapshot()
        status = text("secondary_captured").format(len(snapshot.bones))
        previous_summary = self._last_diagnostic.get("summary") if replaced else None
        if previous_summary:
            status = previous_summary + " · " + status
        self._save_settings(operation="secondary", secondary_root=snapshot.root,
                            secondary_display=(scene._display_path or "") if display else "",
                            secondary_display_attribute=(scene._display_attribute or "") if display else "")
        self._set_connected(self._session_ready(), status)
        self._publish()

    def _capture_secondary(self, root, display=None, display_attribute=None,
                           restoring=False, discover_display=True):
        try:
            return CharacterScene.capture(
                root, display=display, display_attribute=display_attribute,
                on_event=self._scene_event_sink(self._on_secondary_scene_event),
                display_optional=True, discover_display=discover_display)
        except CharacterSceneError as error:
            if (error.code == "AMBIGUOUS_DISPLAY_ATTRIBUTE" and display is not None
                    and not restoring):
                attribute = self._choose_enum(
                    error.context.get("attribute_candidates", ()))
                return self._capture_secondary(root, display, attribute)
            raise

    def remove_secondary_object(self):
        """Remove the second object; inside a connection Unreal restores it first."""
        if self._secondary_scene is None:
            self._publish()
            return
        session = self._session
        if session is not None and getattr(session, "restarting", False):
            self.disconnect(status="Maya secondary object was removed during negotiation",
                            code="SUBJECT_CHANGED")
            self._maya_subject_id = uuid4().hex
            self._clear_secondary()
            self._save_settings(secondary_root="", secondary_display="", secondary_display_attribute="")
            return
        if session is None or not self._session_has_secondary():
            self._maya_subject_id = uuid4().hex
            self._clear_secondary()
            self._save_settings(operation="secondary_remove", secondary_root="",
                                secondary_display="", secondary_display_attribute="")
            self._set_connected(self._session_ready(), text("secondary_removed"))
            self._publish()
            return
        if getattr(session, "removal_pending", False):
            return
        try:
            self._diagnostics.begin("secondary_remove")
            self._pending_secondary_removal = self._diagnostics.stamp
            session.request_secondary_removal(
                self._session_id_of(session), "removed in the Maya connection page")
        except StreamingSessionError as error:
            self._show_error(make_diagnostic(
                error.code if error.code in DIAGNOSTICS else "INTERNAL_ERROR",
                error.message, details=error.details))
            return
        if self._linkage is not None:
            self._linkage.begin_set_removal()
        self._set_connected(True, text("secondary_wait_restore"))
        self._publish()

    def _on_object_removed(self, reply, holder):
        """Unreal confirmed a removal (Maya's or its own user's); sender thread.

        The linkage state adopts the new set at once, so the next target is
        answered by the new set; the scene and panel follow on Maya's main
        thread, against the connection ``holder`` names by then. The
        confirmation can arrive before ``StreamingSession.start`` returned.
        """
        try:
            validate_reply(reply)
        except ValueError as error:
            self._defer_ui(lambda error=error: self._report_if_current(
                holder.get("session"), make_diagnostic("INVALID_MESSAGE", details=str(error))))
            return
        session = holder.get("session")
        linkage = self._linkage if session is self._session else None
        if (linkage is not None and session is not None
                and exact_int(reply.get("session_id")) == self._session_id_of(session)
                and reply.get("object_id") == SECONDARY_OBJECT_ID):
            try:
                linkage.adopt_set(reply.get("set_revision"), [CHARACTER_OBJECT_ID])
            except LinkageError as error:
                self._defer_ui(lambda error=error: self._report_if_current(session, error))
        self._defer_ui(lambda reply=dict(reply):
                       self._finish_object_removal(reply, holder.get("session")))

    def _report_if_current(self, session, error):
        if not self.closed and session is not None and self._session is session:
            self._report_linkage_failure(error)

    def _finish_object_removal(self, reply, session):
        if (self.closed or session is None or session is not self._session
                or getattr(session, "restarting", False)):
            return
        try:
            removed = session.adopt_removal(reply)
        except StreamingSessionError as error:
            self._show_error(make_diagnostic(
                error.code if error.code in DIAGNOSTICS else "INTERNAL_ERROR",
                error.message, details=error.details))
            return
        if removed is not None and removed is self._secondary_scene:
            self._maya_subject_id = uuid4().hex
            self._clear_secondary()
            self._save_settings(secondary_root="", secondary_display="", secondary_display_attribute="")
        detail = reply.get("detail") or ""
        if reply.get("restored"):
            self._object_set_detail = text("secondary_restored")
            status = text("primary_continues")
            self._operation_succeeded("secondary_remove", stamp=self._pending_secondary_removal)
        else:
            self._object_set_detail = text("secondary_restore_incomplete").format(detail or "—")
            status = self._object_set_detail
            self._diagnostics.record(make_diagnostic("RECOVERY_REFUSED", details=detail),
                                     "secondary_remove", "failure", self._pending_secondary_removal)
        if self.settings.workflow == WORKFLOW_ANIMATION and self._cached_playback is None:
            try:
                cached_playback = CachedPlayback(
                    self._scene, session, on_change=self._on_cached_playback_view,
                    retention=self._cached_retention)
            except CachedPlaybackError:
                cached_playback = None
            if cached_playback is not None:
                self._cached_retention = None
                self._cached_playback = cached_playback
                self._on_cached_playback_view(cached_playback.view)
        self._set_connected(self._session_ready(), status)
        self._publish()

    def _clear_secondary(self):
        scene, self._secondary_scene = self._secondary_scene, None
        if scene is not None:
            scene.close()
        self._publish()

    def _on_secondary_scene_event(self, event):
        """The second object changed: the negotiated set no longer describes it."""
        if event.kind == "character_change_started":
            if self._session_has_secondary():
                self._restart_for_input_change()
            return
        if event.kind in ("outfit_changed", "display_changed"):
            self._publish()
            if event.kind == "outfit_changed":
                self._schedule_restart(self._session)
            return
        if event.kind == "character_invalidated":
            was_connected = self._session_has_secondary()
            error = event.error
            self._secondary_scene = None
            if was_connected:
                self._on_character_invalidated(error)
            self._publish()

    def objects_state(self):
        """The current object set and its negotiation state."""
        session = self._session
        scene = self._secondary_scene
        state = {"primary": self._primary_root(), "secondary": None,
                 "secondary_detail": text("secondary_unset_detail"), "state": text("primary_only"),
                 "state_tooltip": "", "set_revision": getattr(session, "set_revision", None),
                 "members": tuple(getattr(session, "set_objects", None) or ()),
                 "has_secondary": scene is not None, "last_removal": self._object_set_detail}
        if scene is not None:
            try:
                snapshot = scene.snapshot()
                state["secondary"] = snapshot.root
                outfit = snapshot.outfit or text("no_display_source")
                state["secondary_detail"] = text("secondary_details_count").format(
                    len(snapshot.bones), len(snapshot.curve_names), outfit)
            except CharacterSceneError as error:
                state["secondary_detail"] = text("secondary_details").format(error.message)
        negotiated = self._session_has_secondary()
        if self._restart is not None:
            state["state"] = text("reconnecting_peer")
        elif getattr(session, "removal_pending", False):
            state["state"] = text("secondary_removal_pending")
        elif negotiated and self._session_ready():
            state["state"] = text("objects_connected")
            state["state_tooltip"] = text("objects_remove_keep_primary")
        elif negotiated:
            state["state"] = text("objects_connecting")
        elif scene is not None and self.settings.workflow != WORKFLOW_ANIMATION:
            state["state"] = text("objects_model_limit")
            state["state_tooltip"] = text("objects_switch_animation")
        elif scene is not None and session is not None:
            state["state"] = text("secondary_next_session")
        elif scene is not None:
            state["state"] = text("secondary_pending_connect")
        elif self._object_set_detail:
            state["state"] = self._object_set_detail
        return state

    def object_actions(self):
        """Enablement and reasons for the secondary object actions."""
        session = self._session
        pending = bool(getattr(session, "removal_pending", False))
        negotiated = self._session_has_secondary()
        set_tooltip = (text("secondary_select_help")
                       if session is None else
                       text("secondary_replace_help"))
        if negotiated:
            remove_tooltip = text("secondary_remove_live_help")
        elif self._secondary_scene is not None:
            remove_tooltip = text("secondary_remove_help")
        else:
            remove_tooltip = text("secondary_is_unset")
        return {
            "set": (not pending, text("secondary_wait") if pending else set_tooltip),
            "remove": (self._secondary_scene is not None and not pending,
                       text("secondary_wait") if pending else remove_tooltip),
        }


# The pump applies one waiting target per tick and refreshes the panel; the
# rate is a ceiling, not a frame-rate target, and the callback always runs on
# Maya's main thread.
LINKAGE_PUMP_INTERVAL = 1.0 / 60.0

def selected_secondary_root():
    """Return ``(root, display)`` for the second object from Maya's selection.

    One deformation root joint, optionally with its Display controller when
    the object has several; a prop without one is fine.
    """
    selected = cmds.ls(selection=True, long=True) or []
    joints = [path for path in selected if cmds.nodeType(path) == "joint"]
    others = [path for path in selected if path not in joints]
    if len(joints) != 1 or len(others) > 1:
        raise ValueError(
            text("secondary_select_root"))
    display = long_transform(others[0]) if others else None
    return joints[0], display


def roots_overlap(first, second):
    """True when one DAG root is the other or lies below it."""
    first = str(first or "")
    second = str(second or "")
    if not first or not second:
        return False
    return (first == second or first.startswith(second + "|")
            or second.startswith(first + "|"))


def _linkage_wait_seconds(linkage):
    wait = getattr(linkage, "wait_seconds", None)
    try:
        return float(wait()) if callable(wait) else 0.0
    except (RuntimeError, TypeError, ValueError):
        return 0.0








def _error_signature(diagnostic):
    """What makes two reports of an Unreal error the same one."""
    return (diagnostic.get("code"), diagnostic.get("details"))


def _stopped_by_maya(session):
    """True when Maya itself ended ``session``; doubles without it never are."""
    return getattr(session, "stopped", False) is True


def _enter_linkage_reason(ready, cached_owner, active, mode=REALTIME_MODE):
    """Why 进入联动 is disabled, in the tooltip the button carries."""
    if active:
        return text("linkage_already_active")
    if cached_owner and mode == CACHED_MODE:
        return text("linkage_cache_busy")
    if cached_owner:
        return text("linkage_wait_cache_clear")
    if not ready:
        return text("linkage_setup_target")
    return ""
