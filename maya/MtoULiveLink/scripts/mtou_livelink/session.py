# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Real-time streaming session, sender worker and negotiation events."""

import errno
import queue
import select
import socket
import threading
import time
from uuid import uuid4

from . import config, mayaapi
from .config import (
    CACHED_MODE,
    CHARACTER_OBJECT_ID,
    DEFAULT_PLAYBACK_CAP,
    ERROR_CLOSE_WINDOW_SECONDS,
    LINKAGE_MODE,
    REALTIME_MODE,
    SECONDARY_OBJECT_ID,
    WORKFLOW_ANIMATION,
    WORKFLOWS,
)
from .diagnostics import (
    DIAGNOSTICS,
    blendshape_warning_from_reply,
    error_reply_diagnostic,
    filter_bone_driven_differences,
    make_diagnostic,
    version_mismatch_diagnostic,
)
from .mayaapi import cmds, om
from .protocol import (
    ProtocolVersionMismatch,
    encode_message,
    exact_int,
    make_frame_message,
    make_hello_message,
    make_init_message,
    make_remove_object_message,
    make_restart_cancel_message,
    make_restart_prepared_message,
    make_restart_request_message,
    recv_message,
    RESTART_STOP_CODES,
    validate_ready_identities,
    validate_reply,
)
from .scene import CharacterSceneError
from .space import validate_frame_rate
from .timecontrol import TimeControl
from .timeline import maya_is_playing, normalize_playback_cap, playback_cap_fps


class LatestFrame(object):
    def __init__(self):
        self._lock = threading.Lock()
        self._value = None

    def put(self, value):
        with self._lock:
            self._value = value

    def take(self):
        with self._lock:
            value, self._value = self._value, None
            return value


# Where each Unreal message goes after the handshake, fixed for the life of the
# connection. Statements reach the session's statement handler in every mode;
# results reach the holder of the time-control state they belong to and are
# dropped while nobody holds it. An ``error`` is routed by its identity (see
# StreamingSession._route_error); any other type ends the connection.
STATEMENTS = "statements"
REPLY_ROUTES = {
    "mode_state": STATEMENTS,
    "object_removed": STATEMENTS,
    "camera_time": LINKAGE_MODE,
    "applied": LINKAGE_MODE,
    "cache_ready": CACHED_MODE,
    "cache_playing": CACHED_MODE,
    "cache_progress": CACHED_MODE,
    "cache_complete": CACHED_MODE,
    "cache_paused": CACHED_MODE,
    "cache_resumed": CACHED_MODE,
    "cache_seeked": CACHED_MODE,
    "cache_looped": CACHED_MODE,
    "cache_loop_changed": CACHED_MODE,
    "cache_stopped": CACHED_MODE,
    "cache_cleared": CACHED_MODE,
}


def route_reply(reply, time_control, on_statement=None, note_reason=None):
    """Deliver one post-ready Unreal message by REPLY_ROUTES (sender thread).

    An error with an upload or play identity belongs to Cached Playback. Any
    other error goes to ``on_statement`` to be reported, except the ``CACHE_``
    codes the protocol ignores without an identity, and ``note_reason`` keeps
    it unless ``on_statement`` returned False (routine scheduling). Whether the
    connection ends is Unreal's decision: when it closes the socket right
    after the noted error, that error is the reason. Returns False for a
    message the connection does not accept.
    """
    kind = reply.get("type")
    if kind == "error":
        if (exact_int(reply.get("upload_id")) is not None
                or exact_int(reply.get("play_id")) is not None):
            time_control.deliver(CACHED_MODE, reply)
        elif not str(reply.get("code") or "").startswith("CACHE_"):
            reported = on_statement(reply) if on_statement is not None else None
            if reported is not False and note_reason is not None:
                note_reason(error_reply_diagnostic(reply))
        return True
    route = REPLY_ROUTES.get(kind)
    if route is None:
        return False
    if kind == "cache_cleared":
        time_control.cache_cleared(exact_int(reply.get("clear_id")))
    if route == STATEMENTS:
        if on_statement is not None:
            on_statement(reply)
    else:
        time_control.deliver(route, reply)
    return True

# A second object's negotiation differences are listed with this prefix, so
# the warning dialog tells them apart from the primary character's.
SECONDARY_WARNING_PREFIX = "副对象 · "


def merge_object_warnings(warnings):
    """One warning for the whole set; the second object's entries are prefixed."""
    merged = {"missing_in_unreal": [], "missing_in_maya": [],
              "bone_name_remaps": [], "has_warning": False}
    for index, warning in enumerate(warnings):
        prefix = "" if index == 0 else SECONDARY_WARNING_PREFIX
        for key in ("missing_in_unreal", "missing_in_maya", "bone_name_remaps"):
            merged[key].extend(prefix + str(value) for value in warning.get(key) or ())
        merged["has_warning"] = merged["has_warning"] or bool(warning.get("has_warning"))
    return merged


def invalid_reply_diagnostic(error):
    """The diagnostic of a reply the protocol refuses.

    A reply of another protocol version is a version mismatch, which names the
    version it declared; every other refusal is an invalid message.
    """
    if isinstance(error, ProtocolVersionMismatch):
        return version_mismatch_diagnostic(
            protocol_version=error.peer_protocol_version,
            message=str(error), details=str(error))
    return make_diagnostic("INVALID_MESSAGE", str(error), details=str(error))


RESTART_COORDINATION_SECONDS = 5.0
RESTART_TOTAL_SECONDS = 32.0
HANDSHAKE_PHASE_SECONDS = 5.0
NETWORK_POLL_SECONDS = 0.05


class RestartWindow(object):
    """Immutable deadlines and the first terminal result across TCP connections."""

    def __init__(self, worker=None):
        now = time.monotonic()
        self.coordination_deadline = now + RESTART_COORDINATION_SECONDS
        self.deadline = now + RESTART_TOTAL_SECONDS
        self._lock = threading.Lock()
        self._failure = None
        self._worker = worker

    @property
    def failure(self):
        with self._lock:
            return self._failure

    def stop(self, diagnostic):
        with self._lock:
            if self._failure is None:
                self._failure = diagnostic
            return self._failure

    def handoff(self, worker):
        with self._lock:
            self._worker = worker

    def remaining(self, worker, coordinating, awaiting):
        with self._lock:
            if self._worker is not worker or self._failure is not None:
                raise _RestartInterrupted()
            deadline = self.coordination_deadline if coordinating else self.deadline
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                self._failure = make_diagnostic("RESTART_TIMEOUT", details=awaiting)
                raise _RestartInterrupted()
            return remaining


class _RestartInterrupted(Exception):
    pass


class _DeadlineReader(object):
    """Keep the framing reader inside one phase budget, including partial reads."""

    def __init__(self, worker, sock, deadline, awaiting):
        self._worker = worker
        self._socket = sock
        self._deadline = deadline
        self._awaiting = awaiting

    def recv(self, size):
        while True:
            timeout = self._worker._remaining(self._deadline, self._awaiting)
            try:
                return self._socket.recv(size)
            except (BlockingIOError, socket.timeout):
                select.select([self._socket], [], [], timeout)


class SenderWorker(threading.Thread):
    def __init__(self, init_message, hello_message=None, on_reply=None,
                 restart=None, expected_source=None, expected_targets=None,
                 permit_session_id=None):
        threading.Thread.__init__(self, name="MtoULiveLinkSender")
        self.daemon = True
        # One init per object of the set, primary first; a single dict is the
        # one-character set.
        if isinstance(init_message, (list, tuple)):
            self._init_messages = [dict(message) for message in init_message]
        else:
            self._init_messages = [init_message]
        self._hello_message = hello_message
        self._expected_source = expected_source
        self._expected_targets = expected_targets
        self._permit_session_id = permit_session_id
        self._target_identities = {}
        self._hello_reply = None
        self._restart = restart
        self._restart_phase = None
        self._restart_token = None
        self._restart_packets = []
        self._prepared_revision = None
        self._init_started = False
        self._init_available = restart is None
        self._message_in_flight = False
        self._cancel_attempted = False
        self._peer_stopped = False
        self._latest = LatestFrame()
        # Unbounded by design: the cached upload streams hundreds of frames
        # through the same ordered path. Linkage controls keep their own bound
        # in the linkage session instead of constraining this queue.
        self._ordered = queue.Queue()
        # Controls queued in ordered mode survive the switch back to Latest
        # mode here, so a mode transition can never strand queued work.
        self._carryover = queue.Queue()
        self._ordered_mode = False
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._socket = None
        self._state = "connecting"
        self._detail = "Connecting to Unreal..."
        self._warning = {"missing_in_unreal": [], "missing_in_maya": [],
                         "bone_name_remaps": [],
                         "has_warning": False}
        self._diagnostic = None
        # Routes every post-ready Unreal message on this thread; False for a
        # message the connection does not accept, which ends it.
        self._on_reply = on_reply
        # The Unreal error received last, with the time it arrived: the
        # reason shown when Unreal closes the connection right after it.
        self._reason = None
        self._reason_at = None

    def submit(self, frame_message):
        packet = encode_message(frame_message)
        with self._lock:
            if self._restart_phase is None:
                if self._ordered_mode:
                    self._ordered.put(packet)
                else:
                    self._latest.put(packet)

    def begin_ordered(self):
        with self._send_lock:
            self._begin_ordered_locked()

    def _begin_ordered_locked(self):
        with self._lock:
            self._ordered_mode = True
            latest = self._latest.take()
        while True:
            try:
                self._ordered.get_nowait()
                self._ordered.task_done()
            except queue.Empty:
                break
        if latest is not None:
            self._ordered.put(latest)

    def submit_ordered(self, frame_message):
        packet = encode_message(frame_message)
        with self._lock:
            if self._restart_phase is None:
                self._ordered.put(packet)

    def submit_control(self, message):
        """Queue one control message in order, whichever delivery mode is on.

        Latest mode never drains the ordered queue, so a control statement
        made while live frames stream (结束预览 after 返回实时, a refused
        entry) travels through the carryover queue that precedes them.
        """
        packet = encode_message(message)
        with self._send_lock:
            with self._lock:
                if self._restart_phase is None:
                    if self._ordered_mode:
                        self._ordered.put(packet)
                    else:
                        self._carryover.put(packet)

    def ordered_pending(self):
        """Approximate count of queued packets not yet sent to Unreal."""
        return self._ordered.unfinished_tasks

    def end_ordered(self, discard_pending=True):
        with self._send_lock:
            with self._lock:
                self._ordered_mode = False
            if discard_pending:
                while True:
                    try:
                        self._ordered.get_nowait()
                        self._ordered.task_done()
                    except queue.Empty:
                        break
            else:
                # Lossless transition: queued ordered packets keep their place
                # ahead of any subsequent Latest-mode live frames.
                while True:
                    try:
                        packet = self._ordered.get_nowait()
                    except queue.Empty:
                        break
                    self._carryover.put(packet)
                    self._ordered.task_done()

    def status(self):
        with self._lock:
            return (self._state, self._detail, dict(self._warning),
                    dict(self._diagnostic) if self._diagnostic else None)

    def note_reason(self, diagnostic):
        """Keep ``diagnostic`` as the reason if Unreal closes the connection next."""
        with self._lock:
            self._reason = dict(diagnostic)
            self._reason_at = time.monotonic()

    def _close_reason(self):
        """The noted error when Unreal closed right after it, else None."""
        with self._lock:
            reason, noted_at = self._reason, self._reason_at
        if reason is None or noted_at is None:
            return None
        if time.monotonic() - noted_at > ERROR_CLOSE_WINDOW_SECONDS:
            return None
        return reason

    def _set_status(self, state, detail, warning=None, diagnostic=None):
        with self._lock:
            self._state = state
            self._detail = detail
            self._warning = dict(warning or {
                "missing_in_unreal": [], "missing_in_maya": [],
                "bone_name_remaps": [], "has_warning": False})
            self._diagnostic = dict(diagnostic) if diagnostic else None

    def stop(self):
        with self._lock:
            self._stop_event.set()
            sock = self._socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def stop_with_reason(self, diagnostic):
        with self._lock:
            self._stop_event.set()
            self._state = "disconnected"
            if self._restart is not None:
                diagnostic = self._restart.stop(diagnostic)
                self._retire_restart_locked()
            self._send_cancel_locked(diagnostic)
        self.stop()

    def restart_status(self):
        with self._lock:
            if self._restart_phase == "permitted" and self._socket is not None:
                return self._restart, "coordinating", None
            return self._restart, self._restart_phase, self._restart_token

    def restart_identities(self):
        with self._lock:
            targets = self._target_identities or self._expected_targets
            return self._hello_reply["source_instance_id"], dict(targets)

    def forget_target(self, object_id):
        with self._lock:
            self._target_identities.pop(object_id, None)

    def _begin_coordination_locked(self):
        if self._restart_phase is None:
            self._restart = self._restart or RestartWindow(self)
            self._restart_phase = "coordinating"
            self._state = "restarting"
            self._latest.take()

    def request_restart(self, revision):
        """Fence the connection synchronously, before Maya captures or restores."""
        with self._lock:
            if self._state == "error":
                return self._restart
            if self._state == "disconnected" or self._stop_event.is_set():
                return None
            if self._restart_phase == "permitted":
                return self._restart
            if self._state != "ready" and self._restart is None:
                return None
            if not self._init_started:
                self._init_available = False
                if self._hello_reply:
                    self._state = "preparing_init"
                return self._restart
            self._begin_coordination_locked()
            self._restart_packets[:] = [make_restart_request_message(
                self._hello_reply["session_id"], revision)]
            return self._restart

    def prepare_restart(self, revision):
        with self._lock:
            if self._restart_phase != "coordinating" or self._restart.failure:
                return
            self._restart_packets.append(make_restart_prepared_message(
                self._hello_reply["session_id"], revision))

    def prepare_init(self, messages):
        with self._lock:
            if self._restart_phase is None and not self._init_started:
                self._init_messages = messages
                self._init_available = True

    def cancel_restart(self, diagnostic):
        with self._lock:
            if self._restart is not None:
                self._restart.stop(diagnostic)

    def _expire_restart(self, awaiting):
        with self._lock:
            operation = self._restart
            coordinating = self._restart_phase == "coordinating"
            prepared = self._prepared_revision is not None
        if operation is None:
            return float("inf")
        if coordinating and not awaiting.startswith("sending restart_"):
            awaiting = ("waiting for Unreal restoration and restart permission" if prepared
                        else "waiting for Maya capture and resource restoration")
        return operation.remaining(self, coordinating, awaiting)

    def _remaining(self, deadline, awaiting):
        operation_remaining = self._expire_restart(awaiting)
        if self._stop_event.is_set():
            raise _RestartInterrupted()
        timeout = deadline - time.monotonic()
        if timeout <= 0.0:
            with self._lock:
                operation = self._restart
            if operation is not None:
                operation.stop(make_diagnostic("RESTART_TIMEOUT", details=awaiting))
                raise _RestartInterrupted()
            raise socket.timeout("timed out " + awaiting)
        return min(NETWORK_POLL_SECONDS, timeout, operation_remaining)

    def _connect(self):
        self._expire_restart("connecting to Unreal")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with self._lock:
            self._socket = sock
        if self._stop_event.is_set():
            return None
        sock.setblocking(False)
        # Read the endpoint at call time so a peer or a test can point the
        # worker at another port without reloading the module.
        result = sock.connect_ex((config.HOST, config.PORT))
        pending = (errno.EINPROGRESS, errno.EWOULDBLOCK)
        if result not in (0,) + pending:
            raise OSError(result, "connection failed")
        deadline = time.monotonic() + 2.0
        while result:
            timeout = self._remaining(deadline, "connecting to Unreal")
            _, writable, errors = select.select([], [sock], [sock], timeout)
            if writable or errors:
                result = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if result:
                    raise OSError(result, "connection failed")
        return sock

    def _send_packet(self, sock, packet, deadline, awaiting, stream=False, initial=None):
        packet = memoryview(packet)
        started = False
        while packet:
            timeout = self._remaining(deadline, awaiting)
            with self._lock:
                # Finish an in-flight message to preserve framing on the
                # coordination TCP. A fenced connection cannot start a pose,
                # cached upload, group, or another init message.
                if self._stop_event.is_set():
                    raise _RestartInterrupted()
                if not started and ((stream or initial) and self._restart_phase):
                    return False
                if initial is not None and not started and (
                        not self._init_available or initial is not self._init_messages):
                    return False
                try:
                    sent = sock.send(packet)
                except (BlockingIOError, socket.timeout):
                    sent = None
                if sent:
                    started = True
                    self._message_in_flight = sent < len(packet)
                    if initial is not None:
                        self._init_started = True
                        if self._restart_phase is None:
                            self._state = "connecting"
            if sent is None:
                select.select([], [sock], [], timeout)
            elif not sent:
                raise EOFError("connection closed " + awaiting)
            else:
                packet = packet[sent:]
        return True

    def _send_initial(self, sock):
        deadline = self._init_deadline
        with self._lock:
            messages = self._init_messages
        for message in messages:
            if not self._send_packet(sock, encode_message(message), deadline,
                                     "sending init message", initial=messages):
                return False
        return True

    def _receive(self, sock, deadline, awaiting):
        return validate_reply(recv_message(_DeadlineReader(self, sock, deadline, awaiting)))

    def _read_handshake_reply(self, sock, expected):
        deadline = time.monotonic() + HANDSHAKE_PHASE_SECONDS
        awaiting = "waiting for " + expected
        while not self._stop_event.is_set():
            if expected == "ready" and self._restart is not None:
                # Poll before starting a framed read so a local fence can
                # switch to coordination even if Unreal never replies Ready.
                with self._lock:
                    if self._restart_phase is not None:
                        return None
                timeout = self._remaining(deadline, awaiting)
                if not select.select([sock], [], [], timeout)[0]:
                    continue
            reply = self._receive(sock, deadline, awaiting)
            kind = reply["type"]
            if kind == "restart_state":
                self._receive_restart_state(reply)
                if self._restart_phase is not None:
                    return None
                continue
            if kind == "mode_state" and expected == "ready":
                continue
            if kind == "error":
                self._fail(error_reply_diagnostic(reply))
                return None
            if kind != expected:
                raise ValueError("expected {0}, received {1}".format(expected, kind))
            return reply
        return None

    def _greet(self, sock):
        deadline = time.monotonic() + HANDSHAKE_PHASE_SECONDS
        self._send_packet(sock, encode_message(self._hello_message), deadline, "sending hello")
        reply = self._read_handshake_reply(sock, "hello_ack")
        if reply is None:
            return False
        session_id = exact_int(reply["session_id"])
        if session_id is None or not 0 < session_id <= 2147483647:
            raise ValueError("hello_ack session_id must be a positive int32")
        if self._expected_source is not None and reply["source_instance_id"] != self._expected_source:
            self._fail(make_diagnostic("SUBJECT_CHANGED", details="Unreal Source instance changed"))
            return False
        with self._lock:
            self._hello_reply = reply
            if self._restart is not None:
                self._init_available = False
                self._state = "preparing_init"
        return True

    def _wait_for_init(self, sock):
        deadline = self._init_deadline
        awaiting = "waiting for the latest Maya snapshot after hello_ack"
        while True:
            timeout = self._remaining(deadline, awaiting)
            with self._lock:
                if self._restart_phase or self._init_available:
                    return
            if select.select([sock], [], [], timeout)[0]:
                reply = self._receive(sock, deadline, awaiting)
                if reply["type"] == "restart_state":
                    self._receive_restart_state(reply)
                elif reply["type"] == "error":
                    self._fail(error_reply_diagnostic(reply))
                    raise _RestartInterrupted()
                elif reply["type"] != "mode_state":
                    raise ValueError("expected no reply before init, received " + reply["type"])

    def _identity_mismatch(self, reply, init_message):
        """Describe the first field the Ready reply fails to echo."""
        for field, source in (("object_id", init_message), ("session_id", self._hello_reply or {}),
                              ("set_revision", init_message), ("space_revision", init_message),
                              ("revision", init_message), ("workflow", init_message)):
            expected = source.get(field)
            actual = reply.get(field)
            if field in ("session_id", "set_revision", "space_revision", "revision"):
                actual = exact_int(actual)
            if expected is not None and actual != expected:
                return "{0}: expected {1!r}, ready reported {2!r}".format(
                    field, expected, reply.get(field))
        return None

    def _negotiate_set(self, sock):
        warnings, replies = [], []
        for init_message in self._init_messages:
            reply = self._read_handshake_reply(sock, "ready")
            if reply is None:
                return None
            identity_error = self._identity_mismatch(reply, init_message)
            if identity_error is not None:
                raise ValueError("The Ready reply does not echo the negotiated identity. "
                                 + identity_error)
            warnings.append(blendshape_warning_from_reply(
                filter_bone_driven_differences(init_message, reply)))
            replies.append(reply)
        objects = self._init_messages[0].get("set_objects", [CHARACTER_OBJECT_ID])
        targets = validate_ready_identities(replies, objects)
        if self._expected_targets is not None and targets != self._expected_targets:
            self._fail(make_diagnostic("SUBJECT_CHANGED", details="Unreal actual target set changed"))
            return None
        self._expire_restart("waiting for all objects to become Ready")
        with self._lock:
            if self._restart_phase is not None:
                return None
            if self._restart is not None and self._restart.failure is not None:
                raise _RestartInterrupted()
            self._target_identities = targets
            self._restart = None
            self._init_started = True
            self._state = "ready"
            self._detail = "Connected"
            self._warning = merge_object_warnings(warnings)
        return self._warning

    def hello_reply(self):
        with self._lock:
            return dict(self._hello_reply) if self._hello_reply else None

    def _receive_restart_state(self, reply):
        with self._lock:
            phase = reply["state"]
            current = (self._hello_reply or {}).get("session_id")
            issued_permit = (self._restart is not None and phase == "stopped"
                             and reply["session_id"] == self._permit_session_id)
            if reply["session_id"] != current and not issued_permit:
                return
            if phase == "ending":
                if self._state == "ready" or self._restart is not None:
                    self._begin_coordination_locked()
            elif phase == "permitted":
                if (self._restart_phase == "coordinating" and self._prepared_revision is not None
                        and self._restart.failure is None):
                    # Permission itself must arrive inside the coordination window.
                    if time.monotonic() >= self._restart.coordination_deadline:
                        self._restart.stop(make_diagnostic(
                            "RESTART_TIMEOUT", details="waiting for Unreal restart permission"))
                        raise _RestartInterrupted()
                    self._restart_phase = "permitted"
                    self._restart_token = reply["restart_token"]
            elif self._restart_phase != "permitted":
                # A stopped state may be the first observable event if Unreal
                # already knows a changed subject cannot be reused.
                self._begin_coordination_locked()
                self._restart.stop(make_diagnostic(reply["code"], details=reply["detail"]))
                self._peer_stopped = True
                raise _RestartInterrupted()

    def _pop_stream_packet(self):
        with self._lock:
            for pending in (self._carryover, self._ordered if self._ordered_mode else None):
                if pending is not None:
                    try:
                        return pending.get_nowait(), pending
                    except queue.Empty:
                        pass
            return self._latest.take(), None

    def _run_connected(self, sock):
        while not self._stop_event.is_set():
            remaining = self._expire_restart("waiting for Maya to use the restart permit")
            with self._lock:
                phase = self._restart_phase
                message = self._restart_packets.pop(0) if self._restart_packets else None
            if phase == "permitted":
                # Release the old TCP now, and keep the window enforced while
                # Maya's main thread may be blocked before starting the new TCP.
                self._close_socket()
                while not self._stop_event.wait(NETWORK_POLL_SECONDS):
                    self._expire_restart("waiting for Maya to use the restart permit")
                return
            if phase == "coordinating":
                if message is not None:
                    self._send_packet(sock, encode_message(message), float("inf"),
                                      "sending " + message["type"])
                    with self._lock:
                        if message["type"] == "restart_prepared":
                            self._prepared_revision = message["source_revision"]
            else:
                packet, pending = self._pop_stream_packet()
                if packet is not None:
                    try:
                        self._send_packet(sock, packet, time.monotonic() + 0.25,
                                          "sending streaming data", stream=True)
                    finally:
                        if pending is not None:
                            pending.task_done()
            if not select.select([sock], [], [], min(0.01, remaining))[0]:
                continue
            reply = self._receive(sock, time.monotonic() + 0.25, "receiving streaming data")
            if reply["type"] == "restart_state":
                self._receive_restart_state(reply)
                continue
            with self._lock:
                restarting = self._restart_phase is not None
                self._reason = None
            if restarting:
                # Poses and mode replies already in flight belong to the old
                # stream. A failure from that same negotiation still stops it.
                if reply["type"] == "error" and reply.get("kind") != "scheduling":
                    self._fail(error_reply_diagnostic(reply))
                    return
                continue
            if self._on_reply is None or not self._on_reply(reply):
                raise ValueError("unexpected runtime reply: " + reply["type"])

    def _fail(self, diagnostic):
        with self._lock:
            if self._restart is not None:
                diagnostic = self._restart.stop(diagnostic)
                self._retire_restart_locked()
        self._set_status("error", diagnostic["summary"], diagnostic=diagnostic)

    def _retire_restart_locked(self):
        self._restart_phase = "stopped"
        self._restart_token = None
        self._restart_packets.clear()
        self._prepared_revision = None
        self._init_available = False

    def _close_socket(self):
        with self._lock:
            sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _report_restart_failure(self):
        with self._lock:
            operation = self._restart
        diagnostic = operation.failure if operation is not None else None
        if diagnostic is not None:
            self._fail(diagnostic)
            with self._lock:
                self._send_cancel_locked(diagnostic)

    def _send_cancel_locked(self, diagnostic):
        if (self._socket is None or not self._hello_reply or self._message_in_flight
                or self._peer_stopped or self._cancel_attempted):
            return
        self._cancel_attempted = True
        code = diagnostic["code"]
        cancel = make_restart_cancel_message(
            self._hello_reply["session_id"],
            code if code in RESTART_STOP_CODES else "INTERNAL_ERROR",
            diagnostic["details"])
        try:
            self._socket.send(encode_message(cancel))
        except OSError:
            pass

    def run(self):
        connected = False
        try:
            if self._restart is not None:
                self._restart.handoff(self)
            sock = self._connect()
            if sock is None or self._stop_event.is_set():
                return
            if self._hello_message is not None and not self._greet(sock):
                return
            self._init_deadline = time.monotonic() + HANDSHAKE_PHASE_SECONDS
            while not self._restart_phase:
                if self._restart is not None:
                    self._wait_for_init(sock)
                if self._send_initial(sock):
                    break
            if not self._restart_phase:
                ready = self._negotiate_set(sock)
                if ready is None and not self._restart_phase:
                    return
                connected = ready is not None
            self._run_connected(sock)
        except _RestartInterrupted:
            pass
        except Exception as exc:
            if not self._stop_event.is_set():
                reason = self._close_reason()
                if isinstance(exc, (ValueError, UnicodeError)):
                    diagnostic = invalid_reply_diagnostic(exc)
                elif connected and reason is not None:
                    diagnostic = reason
                else:
                    code = ("STREAM_INTERRUPTED" if connected or self._restart_phase
                            else "UNREAL_NOT_REACHABLE")
                    diagnostic = make_diagnostic(code, str(exc), details=str(exc))
                self._fail(diagnostic)
        finally:
            try:
                if not self._stop_event.is_set():
                    self._report_restart_failure()
            finally:
                self._close_socket()
            if self._stop_event.is_set():
                self._set_status("disconnected", "Disconnected")


class StreamingSessionError(RuntimeError):
    def __init__(self, code, message="", details="", recapture_scene=False):
        self._code = code
        self._message = message or code
        self._details = details or self._message
        self._recapture_scene = bool(recapture_scene)
        super(StreamingSessionError, self).__init__(self._message)

    @property
    def code(self):
        return self._code

    @property
    def message(self):
        return self._message

    @property
    def details(self):
        return self._details

    @property
    def recapture_scene(self):
        return self._recapture_scene


def copy_session_payload(value):
    if isinstance(value, dict):
        return {key: copy_session_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(copy_session_payload(item) for item in value)
    return value


class StreamingSessionEvent(object):
    def __init__(self, kind, warning=None, diagnostic=None, recapture_scene=False,
                 automatic=False):
        self._kind = kind
        self._warning = copy_session_payload(warning) if warning else None
        self._diagnostic = copy_session_payload(diagnostic) if diagnostic else None
        self._recapture_scene = bool(recapture_scene)
        self.automatic = automatic

    @property
    def kind(self):
        return self._kind

    @property
    def warning(self):
        return copy_session_payload(self._warning) if self._warning else None

    @property
    def diagnostic(self):
        return copy_session_payload(self._diagnostic) if self._diagnostic else None

    @property
    def recapture_scene(self):
        return self._recapture_scene


class StreamingSession(object):
    """Owns one Maya-to-Unreal streaming connection lifecycle."""

    def __init__(self, scene, fps, on_event, playback_cap=DEFAULT_PLAYBACK_CAP,
                 playback_state=None, workflow=WORKFLOW_ANIMATION,
                 blendshapes_enabled=True, object_id=CHARACTER_OBJECT_ID,
                 set_revision=1, space_revision=1, scene_units=None,
                 maya_version=None, secondary_scene=None, on_statement=None,
                 maya_subject_id=None, automatic_restart=None):
        if workflow not in WORKFLOWS:
            raise StreamingSessionError(
                "INVALID_MESSAGE",
                "workflow must be 'animation' or 'model'.",
                "requested workflow: {0}".format(workflow))
        if secondary_scene is not None and workflow != WORKFLOW_ANIMATION:
            raise StreamingSessionError(
                "OBJECT_SET_UNSUPPORTED",
                "模型工作流只支持单个角色。",
                "a second object streams only in the Animation workflow")
        self._scene = scene
        # The independent object beside the primary character; None for the
        # one-character set. Removal inside the connection clears it.
        self._secondary_scene = secondary_scene
        self._secondary_revision = None
        # The new set revision a requested removal waits for; frames pause
        # until Unreal confirms it.
        self._removal_pending = None
        self._scene_fps = fps
        self._playback_cap = normalize_playback_cap(playback_cap)
        self._playback_state = playback_state or maya_is_playing
        self._is_playing = self._read_playback_state()
        self._on_event = on_event
        # Unreal's connection-level statements, on the sender thread: mode
        # statements, object removals and errors that belong to no cache
        # operation. Returns False for an error that is routine work, not a
        # reason the connection could end for.
        self._on_statement = on_statement
        self._workflow = workflow
        self._blendshapes_enabled = bool(blendshapes_enabled)
        self._object_id = str(object_id)
        self._set_revision = int(set_revision)
        self._space_revision = int(space_revision)
        self._scene_units = tuple(scene_units or ("cm", 1.0))
        self._maya_version = maya_version
        self._maya_subject_id = maya_subject_id or uuid4().hex
        self._automatic_restart = automatic_restart
        self._reported_restart_state = None
        self._worker = None
        self._worker_started = False
        self._callback_ids = []
        self._timer_id = None
        self._timer_generation = 0
        self._revision = None
        self._last_sample_time = None
        self._phase = "starting"
        self._outcome = None
        self._terminal_event = None
        self._cleanup_pending = False
        self._cleaned = False
        self._cleanup_error = None
        # Who drives time on this connection; Cached Playback and linkage
        # take leases from it, which pause live sampling while they hold one.
        self._time_control = TimeControl(
            pause=self._pause_sampling, resume=self._resume_sampling,
            on_change=self._on_time_control_changed)

    @property
    def object_id(self):
        return self._object_id

    @property
    def set_objects(self):
        """The ordered object ids of the current set, primary first."""
        if self._secondary_scene is not None:
            return (self._object_id, SECONDARY_OBJECT_ID)
        return (self._object_id,)

    @property
    def secondary_scene(self):
        return self._secondary_scene

    @property
    def time_control(self):
        return self._time_control

    @property
    def _sampling_paused(self):
        return self._time_control.state != REALTIME_MODE

    @property
    def removal_pending(self):
        return self._removal_pending is not None

    def object_scenes(self):
        """Map of object id to its scene for the current set."""
        scenes = {self._object_id: self._scene}
        if self._secondary_scene is not None:
            scenes[SECONDARY_OBJECT_ID] = self._secondary_scene
        return scenes

    @property
    def set_revision(self):
        return self._set_revision

    @property
    def space_revision(self):
        return self._space_revision

    @property
    def hello_reply(self):
        if self._worker is None:
            return None
        return self._worker.hello_reply()

    @property
    def maya_subject_id(self):
        return self._maya_subject_id

    @property
    def automatic(self):
        return self._automatic_restart is not None

    def restart_status(self):
        if self._worker is None:
            return None, None, None
        return self._worker.restart_status()

    @property
    def restarting(self):
        return self.restart_status()[0] is not None

    def request_restart(self, revision):
        if self._worker is None or self._outcome is not None:
            return None
        return self._worker.request_restart(revision)

    def prepare_restart(self, revision):
        self._worker.prepare_restart(revision)

    def prepare_restart_init(self, snapshots, workflow, blendshapes_enabled):
        if len(snapshots) > 1 and workflow != WORKFLOW_ANIMATION:
            raise StreamingSessionError(
                "OBJECT_SET_UNSUPPORTED", "模型工作流只支持单个角色。")
        self._workflow = workflow
        self._blendshapes_enabled = blendshapes_enabled
        self._revision = snapshots[0].revision
        if len(snapshots) > 1:
            self._secondary_revision = snapshots[1].revision
        self._worker.prepare_init([self._object_init(snapshot, object_id)
                                  for snapshot, object_id in zip(snapshots, self.set_objects)])

    def cancel_restart(self, diagnostic):
        if self._worker is not None:
            self._worker.cancel_restart(diagnostic)

    def restart_identities(self):
        return self._worker.restart_identities()

    def release_restart_leases(self):
        self._time_control.terminate()
        if self._worker is not None:
            self._worker.end_ordered(discard_pending=True)

    def fence_for_disconnect(self, diagnostic):
        if self._worker is not None:
            self._worker.stop_with_reason(diagnostic)
        self.release_restart_leases()

    @property
    def revision(self):
        return self._revision

    @property
    def workflow(self):
        return self._workflow

    @property
    def blendshapes_enabled(self):
        return self._blendshapes_enabled

    @property
    def stopped(self):
        """True once Maya itself ended the session (disconnect, scene change)."""
        return self._outcome == "stopped"

    @property
    def is_ready(self):
        if self._outcome is not None or self._worker is None:
            return False
        try:
            return self._worker.status()[0] == "ready"
        except (AttributeError, IndexError, TypeError):
            return self._phase == "ready"

    @classmethod
    def start(cls, scene, fps, on_event=None, playback_cap=DEFAULT_PLAYBACK_CAP,
              playback_state=None, workflow=WORKFLOW_ANIMATION,
              blendshapes_enabled=True, object_id=CHARACTER_OBJECT_ID,
              set_revision=1, space_revision=1, scene_units=None,
              maya_version=None, secondary_scene=None, on_statement=None,
              maya_subject_id=None, automatic_restart=None):
        try:
            fps = validate_frame_rate(fps)
            snapshot = scene.snapshot()
        except CharacterSceneError as error:
            raise StreamingSessionError(
                error.code, error.message, error.details, recapture_scene=True)
        except (RuntimeError, ValueError) as exc:
            raise StreamingSessionError(
                "INVALID_FRAME_RATE", str(exc), details=str(exc))
        secondary_snapshot = None
        if secondary_scene is not None:
            try:
                secondary_snapshot = secondary_scene.snapshot()
            except CharacterSceneError as error:
                raise StreamingSessionError(
                    error.code, "副对象：" + error.message, error.details)
        session = cls(scene, fps, on_event, playback_cap, playback_state,
                      workflow, blendshapes_enabled, object_id, set_revision,
                      space_revision, scene_units, maya_version, secondary_scene,
                      on_statement, maya_subject_id, automatic_restart)
        try:
            session._start(snapshot, secondary_snapshot)
        except Exception as exc:
            session._cleanup()
            if isinstance(exc, StreamingSessionError):
                raise
            raise StreamingSessionError(
                "INTERNAL_ERROR", str(exc), details=str(exc))
        return session

    def _object_init(self, snapshot, object_id):
        if len(snapshot.bind_local_transforms) != len(snapshot.bones):
            raise StreamingSessionError(
                "BIND_POSE_INVALID",
                "The captured bind pose does not match the captured skeleton.")
        bones = [
            [name, parent, list(snapshot.bind_local_transforms[index])]
            for index, (name, parent) in enumerate(snapshot.bones)
        ]
        return make_init_message(
            bones, list(snapshot.curve_names), snapshot.revision,
            workflow=self._workflow,
            blendshapes_enabled=self._blendshapes_enabled,
            object_id=object_id,
            set_revision=self._set_revision,
            space_revision=self._space_revision,
            set_objects=list(self.set_objects))

    def _start(self, snapshot, secondary_snapshot=None):
        init_messages = [self._object_init(snapshot, self._object_id)]
        if secondary_snapshot is not None:
            init_messages.append(
                self._object_init(secondary_snapshot, SECONDARY_OBJECT_ID))
            self._secondary_revision = secondary_snapshot.revision
        restart, token, expected_source, expected_targets, permit_session_id = (
            self._automatic_restart or (None, None, None, None, None))
        hello_message = make_hello_message(
            self._maya_version or "unknown",
            self._scene_units[0], self._scene_units[1], self._scene_fps,
            maya_subject_id=self._maya_subject_id, restart_token=token)
        self._maya_subject_id = hello_message["maya_subject_id"]
        restart_options = ({"restart": restart, "expected_source": expected_source,
                            "expected_targets": expected_targets,
                            "permit_session_id": permit_session_id} if restart else {})
        self._worker = SenderWorker(
            init_messages, hello_message, on_reply=self._route_reply, **restart_options)
        self._revision = snapshot.revision
        self._worker.start()
        self._worker_started = True
        for message in (
                om.MSceneMessage.kBeforeNew,
                om.MSceneMessage.kBeforeOpen,
                om.MSceneMessage.kMayaExiting):
            self._callback_ids.append(
                om.MSceneMessage.addCallback(message, self._on_scene_change))
        self._callback_ids.append(
            om.MEventMessage.addEventCallback(
                "timeChanged", self._on_time_changed))
        condition_message = getattr(om, "MConditionMessage", None)
        if condition_message is not None:
            self._callback_ids.append(
                condition_message.addConditionCallback(
                    "playingBack", self._on_playback_condition_changed))
        self._timer_id = self._add_timer(self._effective_rate())
        self._phase = "connecting"

    def _add_timer(self, fps):
        self._timer_generation += 1
        generation = self._timer_generation

        def on_timer(*args):
            self._on_timer(generation, *args)

        return om.MTimerMessage.addTimerCallback(1.0 / fps, on_timer)

    def _replace_timer(self, fps):
        old_timer = self._timer_id
        try:
            new_timer = self._add_timer(fps)
        except (RuntimeError, ValueError) as exc:
            self._timer_generation += 1
            self._request_failure(
                make_diagnostic("INTERNAL_ERROR", str(exc), details=str(exc)),
                recapture_scene=False)
            return False
        if old_timer is not None:
            try:
                om.MMessage.removeCallback(old_timer)
            except RuntimeError as exc:
                self._remove_callback(new_timer)
                self._timer_generation += 1
                self._request_failure(
                    make_diagnostic("INTERNAL_ERROR", str(exc), details=str(exc)),
                    recapture_scene=False)
                return False
        self._timer_id = new_timer
        self._last_sample_time = None
        return True

    def _pause_sampling(self):
        """Time-control hook: stop live sampling and deliver in order.

        The timer stays: while paused it only watches the connection, so a
        connection Unreal ends still reaches the terminal event with its reason.
        """
        if self._worker is not None:
            self._worker.begin_ordered()
        self._last_sample_time = None

    def _resume_sampling(self):
        """Time-control hook: queued ordered messages, then live samples."""
        if self._worker is not None:
            # Lossless: what the lease holder queued precedes the live frames.
            self._worker.end_ordered(discard_pending=False)
        if self._outcome is not None or self._worker is None:
            return
        # A rate or cap changed while paused reaches the timer only now.
        if not self._replace_timer(self._effective_rate()):
            return
        self._sample_and_submit(force=True)

    def _route_reply(self, reply):
        return route_reply(reply, self._time_control, self._deliver_statement,
                           self._note_reason)

    def _note_reason(self, diagnostic):
        worker = self._worker
        if worker is not None:
            worker.note_reason(diagnostic)

    def _deliver_statement(self, reply):
        handler = self._on_statement
        if handler is None:
            return None
        try:
            return handler(reply)
        except Exception as exc:  # never end the sender thread
            if cmds is not None:
                try:
                    cmds.warning(
                        "MtoU_LiveLink statement handler failed: {0}".format(exc))
                except Exception:
                    pass
            return None

    def _on_time_control_changed(self):
        """Tell the panel, on Maya's main thread, that the time control changed."""
        def notify():
            self._emit(StreamingSessionEvent("time_control"))

        if cmds is None:
            notify()
            return
        mayaapi.run_deferred(notify, cmds.evalDeferred)

    def cached_delivery_drained(self):
        """True once every ordered cached message has reached the socket.

        Meaningful while Cached Playback holds its time-control lease.
        An empty backlog proves wire delivery, never Unreal-side parsing;
        only an identity-matched cache_ready outcome confirms acceptance.
        Terminal or unstarted sessions are vacuously drained so a drain
        gate can never wedge.
        """
        worker = self._worker
        if worker is None:
            return True
        return worker.ordered_pending() == 0

    def submit_cached(self, frame_message):
        if not self.is_ready:
            raise StreamingSessionError(
                "CACHED_PLAYBACK_NOT_READY",
                "The streaming session is not ready for cached messages.")
        self._worker.submit_ordered(frame_message)

    def submit_linkage(self, message):
        """Queue one ordered linkage message; False when the worker is gone."""
        if not self.is_ready or self._worker is None:
            raise StreamingSessionError(
                "STREAM_INTERRUPTED",
                "镜头联动需要已连接的 Unreal 会话。")
        self._worker.submit_control(message)
        return True

    def ordered_pending(self):
        if self._worker is None:
            return 0
        return self._worker.ordered_pending()

    def request_secondary_removal(self, session_id, detail=""):
        """Ask Unreal to remove the second object; returns the new set revision.

        Frames of the old set stop here: realtime sampling pauses until Unreal
        confirms the new set with ``object_removed``.
        """
        if not self.is_ready or self._worker is None:
            raise StreamingSessionError(
                "STREAM_INTERRUPTED", "移除副对象需要已连接的 Unreal 会话。")
        if self._secondary_scene is None:
            raise StreamingSessionError(
                "INVALID_MESSAGE", "当前对象集合中没有副对象。")
        if self._removal_pending is not None:
            return self._removal_pending
        revision = self._set_revision + 1
        self._worker.submit_control(make_remove_object_message(
            session_id, SECONDARY_OBJECT_ID, revision, detail))
        self._removal_pending = revision
        return revision

    def adopt_removal(self, message):
        """Adopt Unreal's ``object_removed``: the set holds the primary only.

        Accepts Maya's own removal and one the Unreal user started. Returns the
        removed scene, which the caller owns, or None for a confirmation of a
        set this session already has.
        """
        revision = exact_int(message.get("set_revision"))
        if message.get("object_id") != SECONDARY_OBJECT_ID or revision is None:
            raise StreamingSessionError(
                "INVALID_MESSAGE", "object_removed names no removable object.",
                "object_removed {0!r} at set revision {1!r}".format(
                    message.get("object_id"), message.get("set_revision")))
        if revision <= self._set_revision:
            return None
        removed, self._secondary_scene = self._secondary_scene, None
        self._secondary_revision = None
        self._set_revision = revision
        self._removal_pending = None
        self._worker.forget_target(SECONDARY_OBJECT_ID)
        if not self._sampling_paused:
            self._sample_and_submit(force=True)
        return removed

    def change_rate(self, fps):
        if self._outcome is not None:
            return
        try:
            fps = validate_frame_rate(fps)
        except (RuntimeError, ValueError) as exc:
            self._request_failure(
                make_diagnostic("INVALID_FRAME_RATE", str(exc), details=str(exc)),
                recapture_scene=False)
            return
        self._scene_fps = fps
        if not self._sampling_paused:
            self._replace_timer(self._effective_rate())

    def change_cap(self, playback_cap):
        if self._outcome is not None:
            return
        self._playback_cap = normalize_playback_cap(playback_cap)
        if self._sampling_paused:
            return
        if not self._replace_timer(self._effective_rate()):
            return
        self._sample_and_submit(force=True)

    def stop(self):
        if self._outcome is None:
            self._outcome = "stopped"
            self._terminal_event = StreamingSessionEvent("stopped")
            self._phase = "terminal"
        elif self._cleaned:
            return
        self._finish_terminal()

    def _on_scene_change(self, *unused):
        self.stop()

    def _on_timer(self, generation, *unused):
        if generation != self._timer_generation:
            return
        if self._sampling_paused:
            # A lease holder drives time; the timer only watches the connection.
            self.poll_connection()
            return
        self._update_playback_state()
        self._sample_and_submit()

    def _on_time_changed(self, *unused):
        del unused
        if self._sampling_paused:
            return
        self._update_playback_state()
        self._sample_and_submit()

    def _on_playback_condition_changed(self, *args):
        if self._sampling_paused:
            return
        playing = None
        for value in args:
            if isinstance(value, bool):
                playing = value
                break
        self._update_playback_state(playing)

    def _read_playback_state(self):
        try:
            return bool(self._playback_state())
        except (AttributeError, RuntimeError, TypeError):
            return False

    def _effective_rate(self):
        cap = playback_cap_fps(self._playback_cap)
        if not self._is_playing or cap is None:
            return self._scene_fps
        return min(self._scene_fps, cap)

    def _update_playback_state(self, playing=None):
        if self._outcome is not None:
            return False
        next_state = self._read_playback_state() if playing is None else bool(playing)
        if next_state == self._is_playing:
            return False
        was_playing = self._is_playing
        self._is_playing = next_state
        self._last_sample_time = None
        if not self._replace_timer(self._effective_rate()):
            return True
        if was_playing and not next_state:
            self._sample_and_submit(force=True)
        return True

    def poll_connection(self):
        """Notice a worker that failed or disconnected; True while it is usable.

        The session's timer calls this while sampling is paused. Cached
        Playback calls it when it finds the connection unusable, and a linkage
        driver without Maya's idle loop (mayapy) calls it explicitly, so the
        terminal event carries the worker's own reason.
        """
        if self._outcome is not None or self._worker is None:
            return False
        state, detail, unused_warning, diagnostic = self._worker.status()
        if self._fail_on_worker_state(state, detail, diagnostic):
            return False
        self._notice_restart(state)
        return True

    def _notice_restart(self, state):
        if state not in ("restarting", "preparing_init"):
            return
        operation, phase, unused_token = self.restart_status()
        signature = (state, operation, phase)
        if self._reported_restart_state != signature:
            self._reported_restart_state = signature
            self._emit(StreamingSessionEvent("restart"))

    def _fail_on_worker_state(self, state, detail, diagnostic):
        """Request the terminal failure for a failed worker; True when it did."""
        if state == "error":
            operation, unused_phase, unused_token = self.restart_status()
            self._request_failure(
                diagnostic or make_diagnostic("INTERNAL_ERROR", "Streaming failed."),
                recapture_scene=False, automatic=self.automatic or operation is not None)
            return True
        if state == "disconnected":
            self._request_failure(
                make_diagnostic(
                    "STREAM_INTERRUPTED", detail or "Live Link connection ended.",
                    details=detail or "The streaming worker disconnected unexpectedly."),
                recapture_scene=False)
            return True
        return False

    def _sample_and_submit(self, force=False):
        if self._outcome is not None or self._worker is None:
            return
        state, detail, warning, diagnostic = self._worker.status()
        if self._fail_on_worker_state(state, detail, diagnostic):
            return
        if state != "ready":
            self._notice_restart(state)
            return
        if self._phase == "connecting":
            self._phase = "ready"
            automatic = self.automatic
            self._automatic_restart = None
            self._emit(StreamingSessionEvent("ready", warning=warning, automatic=automatic))
            if not self.is_ready:
                return
        if self._removal_pending is not None:
            # The old set is closed; the new one starts with Unreal's answer.
            return
        now = time.time()
        sample_interval = 1.0 / self._effective_rate()
        if (not force and self._last_sample_time is not None
                and now >= self._last_sample_time
                and now - self._last_sample_time < sample_interval):
            return
        self._last_sample_time = now
        try:
            frame = self._scene.sample()
        except CharacterSceneError as error:
            if not self.is_ready:
                return
            self._request_failure(
                make_diagnostic(
                    error.code if error.code in DIAGNOSTICS else "SAMPLING_FAILED",
                    error.message, details=error.details),
                recapture_scene=True)
            return
        if frame.revision != self._revision:
            self._request_failure(
                make_diagnostic(
                    "SAMPLING_FAILED",
                    "The character description changed during this session.",
                    details="character scene revision changed from {0} to {1}".format(
                        self._revision, frame.revision)),
                recapture_scene=True)
            return
        rows = None
        if self._secondary_scene is not None:
            # Both objects come from the same evaluation: the set is one
            # message, so Unreal never shows them at two Maya times.
            try:
                secondary = self._secondary_scene.sample()
            except CharacterSceneError as error:
                if not self.is_ready:
                    return
                self._request_failure(
                    make_diagnostic(
                        error.code if error.code in DIAGNOSTICS else "SAMPLING_FAILED",
                        "副对象：" + error.message, details=error.details),
                    recapture_scene=False)
                return
            if secondary.revision != self._secondary_revision:
                self._request_failure(
                    make_diagnostic(
                        "SAMPLING_FAILED",
                        "副对象的描述在本次连接中发生了变化。",
                        details="secondary scene revision changed from {0} to {1}".format(
                            self._secondary_revision, secondary.revision)),
                    recapture_scene=False)
                return
            rows = [{"object_id": SECONDARY_OBJECT_ID,
                     "transforms": list(secondary.transforms),
                     "curves": list(secondary.curves)}]
        self._worker.submit(make_frame_message(
            list(frame.transforms), list(frame.curves), rows))

    def _request_failure(self, diagnostic, recapture_scene, automatic=False):
        if self._outcome is not None:
            return
        self._outcome = "failed"
        self._phase = "terminal"
        self._terminal_event = StreamingSessionEvent(
            "failed", diagnostic=diagnostic, recapture_scene=recapture_scene,
            automatic=automatic)
        if not self._cleanup_pending:
            self._cleanup_pending = True
            mayaapi.run_deferred(self._finish_terminal, cmds.evalDeferred)

    def _finish_terminal(self):
        if self._cleaned:
            return
        self._cleanup_pending = False
        self._cleanup()
        self._cleaned = True
        event, self._terminal_event = self._terminal_event, None
        if event is not None:
            self._emit(event)

    def _cleanup(self):
        if self._cleaned:
            return
        self._timer_generation += 1
        self._time_control.terminate()
        if self._worker is not None:
            self._worker.end_ordered(discard_pending=True)
        callback_ids, self._callback_ids = self._callback_ids, []
        timer_id, self._timer_id = self._timer_id, None
        for callback_id in callback_ids:
            self._remove_callback(callback_id)
        if timer_id is not None:
            self._remove_callback(timer_id)
        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                worker.stop()
            except Exception as exc:
                self._record_cleanup_error(exc)
            if self._worker_started:
                try:
                    worker.join(1.0)
                except Exception as exc:
                    self._record_cleanup_error(exc)
        self._worker_started = False

    def _remove_callback(self, callback_id):
        try:
            om.MMessage.removeCallback(callback_id)
        except RuntimeError as exc:
            self._record_cleanup_error(exc)

    def _record_cleanup_error(self, error):
        if self._cleanup_error is None:
            self._cleanup_error = error

    def _emit(self, event):
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception as exc:
            if cmds is not None:
                try:
                    cmds.warning(
                        "MtoU_LiveLink session event callback failed: {0}".format(exc))
                except Exception:
                    pass


def bind_conflict_status(snapshot):
    if not snapshot.bind_conflict_count:
        return ""
    return "（已按绑定姿势解析 {0} 处蒙皮绑定矩阵冲突）".format(
        snapshot.bind_conflict_count)
