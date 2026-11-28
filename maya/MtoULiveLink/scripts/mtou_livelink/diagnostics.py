# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Stable diagnostic codes and the diagnostics derived from Unreal replies."""

from time import localtime, strftime

from . import config
from .config import CACHED_MODE, CONTROL_MAYA, CONTROL_UNREAL, LINKAGE_MODE, REALTIME_MODE, WORKFLOW_MODEL
from .strings import DIAGNOSTICS, REALTIME_STAGE_LABELS, text


def realtime_diagnostic_text(metrics):
    if not metrics:
        return ""
    lines = [
        text("realtime_performance_title"),
        text("realtime_sent_frequency").format(metrics["sent_hz"]),
        text("realtime_delivery").format(
            metrics["sent_frames"], metrics["replaced_frames"]),
    ]
    stages = metrics["stages"]
    for stage, label in REALTIME_STAGE_LABELS:
        if stage in stages:
            lines.append(text("realtime_stage").format(
                label, stages[stage]["latest_ms"], stages[stage]["mean_ms"]))
    return "\n".join(lines)


def cache_summary_text(summary):
    if summary is None:
        return ""
    start, end = summary.capture_range
    date = strftime("%Y-%m-%d %H:%M:%S", localtime(summary.capture_time))
    return text("cache_summary").format(
        start, end, summary.frame_count, summary.scene_fps, date)


def make_diagnostic(code, message="", solution="", details=""):
    summary, default_solution = DIAGNOSTICS.get(code, DIAGNOSTICS["INTERNAL_ERROR"])
    return {
        "code": code,
        "summary": summary,
        "solution": solution or default_solution,
        "message": message or summary,
        "details": details or message,
    }


# What a version line shows for an item the peer did not provide.
UNKNOWN_PEER_VALUE = text("peer_value_unknown")


def _peer_text(value):
    return UNKNOWN_PEER_VALUE if value is None else str(value)


def _version_line(side, plugin_version, protocol_version):
    return text("version_line").format(
        side, _peer_text(plugin_version), _peer_text(protocol_version))


def version_mismatch_diagnostic(plugin_version=None, protocol_version=None,
                                message="", details=""):
    """The diagnostic of a refused protocol version.

    Maya's own side is the release that is loaded; Unreal's side is what the
    peer provided, ``None`` for an item it did not provide. The text of the
    peer's ``details`` is kept as it is but never read for a version.
    """
    diagnostic = make_diagnostic(
        "PROTOCOL_VERSION_MISMATCH", message, details=details)
    unreal = {"plugin_version": plugin_version, "protocol_version": protocol_version}
    maya = {"plugin_version": config.__version__,
            "protocol_version": config.PROTOCOL_VERSION}
    diagnostic["versions"] = {"maya": maya, "unreal": unreal}
    diagnostic["version_lines"] = [
        _version_line(text("maya_side"), maya["plugin_version"], maya["protocol_version"]),
        _version_line(text("ue_side"), plugin_version, protocol_version),
    ]
    return diagnostic


def error_reply_diagnostic(reply):
    """The diagnostic of one Unreal ``error``: the same when it is reported and
    when it later ends the connection, so the panel recognises a repeat."""
    message = reply.get("message") or text("peer_error")
    code = reply.get("code") or "STREAM_INTERRUPTED"
    details = reply.get("details") or message
    if code == "PROTOCOL_VERSION_MISMATCH":
        plugin_version = reply.get("plugin_version")
        protocol_version = reply.get("protocol_version")
        return version_mismatch_diagnostic(
            plugin_version if isinstance(plugin_version, str) else None,
            protocol_version if (isinstance(protocol_version, int)
                                 and not isinstance(protocol_version, bool)) else None,
            message, details)
    return make_diagnostic(code, message, details=details)


def diagnostic_dialog_text(diagnostic):
    """The text of a problem dialog: the summary, the version lines when the
    diagnostic has them, then what to do, separated by blank lines."""
    parts = [diagnostic["summary"]]
    lines = diagnostic.get("version_lines")
    if lines:
        parts.append("\n".join(lines))
    parts.append(diagnostic["solution"])
    return "\n\n".join(parts)


def blendshape_warning_from_reply(reply):
    missing_in_unreal = list(reply.get("missing_in_unreal")
                             or reply.get("missing_curves") or [])
    missing_in_maya = list(reply.get("missing_in_maya") or [])
    bone_name_remaps = list(reply.get("bone_name_remaps") or [])
    return {
        "missing_in_unreal": missing_in_unreal,
        "missing_in_maya": missing_in_maya,
        "bone_name_remaps": bone_name_remaps,
        "has_warning": bool(missing_in_unreal or missing_in_maya or bone_name_remaps),
    }


def filter_bone_driven_differences(init_message, reply):
    """Drop UE-only Morph differences for outfits that declare no BlendShapes.

    A bone-driven outfit declares zero BlendShape names, so generated Morph
    Targets missing in Maya are expected and are not an expression-coverage
    warning.
    """
    if (init_message.get("workflow") == WORKFLOW_MODEL
            and init_message.get("blendshapes_enabled")
            and not init_message.get("curves")):
        reply = dict(reply)
        reply["missing_in_maya"] = []
    return reply


class DiagnosticHistory:
    """The latest operation result. Earlier async replies cannot replace it."""

    def __init__(self):
        self.stamp = 0
        self.operation = None
        self._latest = {}

    @property
    def latest(self):
        return dict(self._latest)

    def begin(self, operation):
        self.stamp += 1
        self.operation = operation
        return self.stamp

    def record(self, diagnostic, operation=None, outcome="failure", stamp=None):
        if stamp is not None:
            if stamp != self.stamp:
                return
        elif operation != self.operation or operation is not None:
            self.begin(operation or self.operation)
        self._latest = dict(diagnostic, operation=operation or self.operation, outcome=outcome)


LINKAGE_MODE_LABELS = {
    REALTIME_MODE: text("realtime_preview"),
    CACHED_MODE: text("cached_playback"),
    LINKAGE_MODE: text("linkage_label"),
}

LINKAGE_CONTROL_LABELS = {CONTROL_MAYA: "Maya", CONTROL_UNREAL: "Unreal"}

# The applied lag the status line reports: below it Maya keeps up and the line
# says 同步正常. Display only; the protocol's convergence window is unchanged.
LINKAGE_LAG_NOTICE_SECONDS = 0.5


OPERATIONS = {
    "role": text("set_role"), "display": text("set_display"),
    "preferences": text("restore_preferences"), "preferences_save": text("save_preferences"),
    "connect": text("connect"), "reconnect": text("reconnect"), "disconnect": text("disconnect"),
    "cached_mode": text("enter_cached"), "realtime_mode": text("return_realtime"),
    "cache": text("cache_operation"), "linkage": text("sequence_linkage"), "linkage_exit": text("exit_linkage"),
    "secondary": text("set_secondary_operation"), "secondary_remove": text("remove_secondary"), "preview": text("realtime_preview"),
}
OUTCOMES = {"failure": text("result_failed"), "warning": text("result_warning"), "success": text("result_success")}


def diagnostic_parts(result):
    if not result:
        return text("no_operation_result"), ""
    title = " · ".join(filter(None, (OPERATIONS.get(result.get("operation")),
                                     OUTCOMES.get(result.get("outcome")), result.get("code"))))
    body = []
    for value in (result.get("summary"), result.get("message"), result.get("details")):
        if value and value not in body:
            body.append(value)
    if result.get("solution"):
        body.append(text("result_solution").format(result["solution"]))
    return title, "\n".join(body)


def diagnostic_result(result):
    return "\n".join(filter(None, diagnostic_parts(result)))

def linkage_diagnostic_text(status):
    """镜头联动 read-back for 诊断详情: everything the status line leaves out."""
    labels = status.get("labels") or {}

    def value(key):
        item = status.get(key)
        return "—" if item is None else item

    age = status.get("applied_age")
    records = status.get("recovery_records") or ()
    lines = [
        text("linkage_details"),
        text("diagnostic_state").format(status.get("line") or "—"),
        labels.get("control") or text("unset_control"),
        labels.get("mode") or text("unset_mode"),
        "mode_revision：{0} · session：{1} · set：{2} · space：{3}".format(
            value("mode_revision"), value("session_id"),
            value("set_revision"), value("space_revision")),
        labels.get("target") or text("unset_target"),
        text("target_eval").format(value("target")),
        labels.get("applied") or text("no_applied"),
        text("applied_eval").format(value("applied_eval_id")),
        text("sample_serial").format(value("applied_sample_serial")),
        text("application_age").format("—" if age is None else text("seconds_one").format(age)),
        labels.get("lag") or text("unset_lag"),
        text("applied_refused").format(
            status.get("applied_count") or 0, status.get("refused_count") or 0),
        text("last_application_failure").format(
            text("none") if status.get("apply_failure") is None
            else status.get("apply_failure") or text("peer_reason_missing")),
        text("targets_in_flight").format(
            (status.get("issued") or 0) + (status.get("pending") or 0)),
        text("detail_line").format(status.get("detail") or "—"),
        text("resource_recovery").format(text("none") if not records else text("item_count").format(len(records))),
    ]
    lines.extend("  {0}".format(record) for record in records)
    return "\n".join(lines)

def object_set_diagnostic_text(state):
    """对象集合 for 诊断详情: the negotiated set, its revision and the removal.

    Scene paths stay in the pairing window; the diagnostics name only the
    set identity the two hosts negotiated.
    """
    lines = [
        text("set_details"),
        text("secondary_details").format(text("configured") if state["has_secondary"] else text("not_configured")),
        text("diagnostic_state").format(state.get("state") or "—"),
        text("set_revision_details").format(
            "、".join(state["members"]) or "—",
            "—" if state.get("set_revision") is None else state.get("set_revision")),
    ]
    if state["last_removal"]:
        lines.append(text("last_secondary_removal").format(state["last_removal"]))
    return "\n".join(lines)

def linkage_status_line(mode, status, recovery, overdue, wait, held):
    """Return ``(line, tooltip)``: the linkage card's one status line.

    Only the state, a lag Maya cannot keep up with, and a failed restore are
    shown; the evaluation, sample and revision read-back stays in 诊断详情.
    """
    hints = []
    if mode == LINKAGE_MODE:
        parts = [text("linkage_active_label"), text("unreal_control")]
        lag = status.lag_seconds if status is not None else None
        applied = status is not None and status.applied_eval_id
        # An overdue target is the lag in effect: it has waited longer than
        # the last complete application took.
        behind = float(wait) if overdue else (
            float(lag) if lag is not None else 0.0)
        failure = status.apply_failure if status is not None else None
        if failure is not None:
            # Unreal could not complete the latest group: Maya is not the cause,
            # and the last complete application stays what Unreal shows.
            parts.append(text("linkage_retry_for").format(_seconds_label(behind))
                         if overdue else text("linkage_retry"))
            hints.append(text("linkage_retry_help").format(
                             "（{0}）".format(failure) if failure else ""))
        elif overdue or behind >= LINKAGE_LAG_NOTICE_SECONDS:
            parts.append(text("maya_lag").format(_seconds_label(behind)))
            hints.append(text("maya_lag_help"))
        elif not applied:
            parts.append(text("linkage_synchronizing"))
        else:
            parts.append(text("linkage_in_sync"))
    else:
        parts = [text("linkage_not_entered")]
        if mode == CACHED_MODE:
            parts.append(text("cache_in_progress"))
        if held:
            parts.append(text("unreal_takeover"))
            hints.append(text("return_unreal_writers"))
    if recovery:
        parts.append(text("recovery_failures").format(len(recovery)))
        hints.append(text("recovery_help"))
    line = " · ".join(parts)
    tooltip = "\n".join([line] + hints)
    return line, tooltip

def _seconds_label(value):
    return "{0:.1f}".format(value) if value < 10 else "{0:.0f}".format(value)

def _frame_label(value):
    if value is None:
        return "—"
    return "{0:.2f}".format(float(value))

def linkage_status_labels(mode, control, status, recovery, overdue, active):
    """The status row's labels; one source for the card and the window."""
    target, applied, lag_label = "—", "—", "—"
    if active:
        target = _frame_label(
            status.target_display_frame if status is not None else None)
        applied = _frame_label(
            status.applied_display_frame if status is not None else None)
        lag = status.lag_seconds if status is not None else None
        if lag is not None:
            lag_label = text("seconds_two").format(float(lag))
            if overdue:
                lag_label += text("not_converged")
    return {
        "control": text("control_label").format(
            LINKAGE_CONTROL_LABELS.get(control, "—")),
        "mode": text("mode_label").format(LINKAGE_MODE_LABELS.get(mode, "—")),
        "target": text("target_time").format(target),
        "applied": text("latest_applied").format(applied),
        "lag": text("lag_label").format(lag_label),
        "recovery": text("recovery_label").format(
            text("none") if not recovery else "；".join(recovery)),
    }
