# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Coordinated restart through the actual sender and loopback TCP."""

import pathlib
import select
import socket
import sys
import threading
import time
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mtou_livelink import config, protocol, session  # noqa: E402


BIND = [0, 0, 0, 0, 0, 0, 1, 1, 1, 1]
SOURCE = "source-instance"
TARGET = "actor-binding-instance"
SUBJECT = "maya-scene-character"


def description(revision=1, **inputs):
    return protocol.make_init_message([["root", -1, BIND]], ["Smile"], revision, **inputs)


def greeting(session_id, source=SOURCE):
    return {
        "type": "hello_ack", "session_id": session_id,
        "version": config.PROTOCOL_VERSION, "source_instance_id": source,
        "capabilities": dict(protocol.DEFAULT_CAPABILITIES), "targets": [],
        "space": {"space_revision": 1, "anchor": {
            "translation": [0, 0, 0], "rotation": [0, 0, 0, 1], "scale": 1}},
    }


def ready(session_id, init, target=TARGET):
    return {
        "type": "ready", "session_id": session_id, "target_instance_id": target,
        "object_id": init["object_id"], "set_revision": 1, "space_revision": 1,
        "revision": init["revision"], "workflow": init["workflow"],
        "missing_in_unreal": [], "missing_in_maya": [], "bone_name_remaps": [],
        "target_morph_count": 1, "accepted_morph_count": 1,
    }


def state(session_id, stage, **fields):
    reply = {"type": "restart_state", "session_id": session_id, "state": stage,
             "code": "", "detail": ""}
    reply.update(fields)
    return reply


class RestartTransportTests(unittest.TestCase):
    def setUp(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(2)
        self.listener.settimeout(3)
        self.addCleanup(self.listener.close)
        self.use_patch(mock.patch.multiple(
            config, HOST="127.0.0.1", PORT=self.listener.getsockname()[1]))

    def use_patch(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def send(peer, message):
        peer.sendall(protocol.encode_message(message))

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            self.assertLess(time.monotonic(), deadline, "sender did not reach the expected state")
            threading.Event().wait(0.005)

    def open_connection(self, operation=None, token=None):
        hello = protocol.make_hello_message("2024", "cm", 1, 24, maya_subject_id=SUBJECT,
                                            restart_token=token)
        sender = session.SenderWorker(
            description(), hello, on_reply=lambda unused: True, restart=operation,
            expected_source=SOURCE if operation else None,
            expected_targets={"character": TARGET} if operation else None,
            permit_session_id=11 if operation else None)
        sender.start()

        def join_sender():
            sender.stop()
            sender.join(1)
            self.assertFalse(sender.is_alive())
        self.addCleanup(join_sender)

        peer, unused_address = self.listener.accept()
        peer.settimeout(3)
        self.addCleanup(peer.close)
        self.assertEqual(hello, protocol.recv_message(peer))
        return sender, peer

    def open_ready(self):
        sender, peer = self.open_connection()
        self.assertFalse(select.select([peer], [], [], 0.03)[0], "init before hello_ack")
        self.send(peer, greeting(11))
        init = protocol.recv_message(peer)
        self.assertEqual(description(), init)
        self.send(peer, ready(11, init))
        self.wait_for(lambda: sender.status()[0] == "ready")
        return sender, peer

    def grant(self, sender, peer):
        self.send(peer, state(11, "permitted", restart_token="permission"))
        self.assertEqual(b"", peer.recv(1))
        self.wait_for(lambda: sender.restart_status()[1] == "permitted")

    def test_prepared_fences_old_stream_and_fresh_init_uses_latest_inputs(self):
        original, peer = self.open_ready()
        operation = original.request_restart(2)
        original.submit({"type": "frame", "stale": True})
        original.submit_ordered({"type": "cache_end", "stale": True})
        self.assertEqual(protocol.make_restart_request_message(11, 2), protocol.recv_message(peer))
        self.send(peer, state(99, "stopped", code="SUBJECT_CHANGED"))
        self.send(peer, state(11, "ending"))
        original.prepare_restart(2)
        self.assertEqual(protocol.make_restart_prepared_message(11, 2), protocol.recv_message(peer))
        self.grant(original, peer)

        fresh, new_peer = self.open_connection(operation, "permission")
        self.send(new_peer, greeting(12))
        self.wait_for(lambda: fresh.status()[0] == "preparing_init")
        self.assertFalse(select.select([new_peer], [], [], 0.03)[0], "init before latest capture")
        fresh.request_restart(3)
        latest = description(8, workflow="model", blendshapes_enabled=False)
        fresh.prepare_init([latest])
        self.assertEqual(latest, protocol.recv_message(new_peer))
        self.send(new_peer, ready(12, latest))
        self.wait_for(lambda: fresh.status()[0] == "ready")
        fresh.submit({"type": "frame", "new": True})
        self.assertEqual({"type": "frame", "new": True}, protocol.recv_message(new_peer))
        self.assertIsNone(fresh.restart_status()[0])
        self.assertFalse(select.select([self.listener], [], [], 0)[0])

    def test_unreal_ending_closes_the_socket_while_main_is_blocked(self):
        self.use_patch(mock.patch.object(session, "RESTART_COORDINATION_SECONDS", 0.20))
        sender, peer = self.open_ready()
        self.send(peer, state(11, "ending"))
        # The main thread waits in recv and never prepares, restores or polls Maya.
        cancel = protocol.recv_message(peer)
        self.assertEqual("restart_cancel", cancel["type"])
        self.assertEqual("RESTART_TIMEOUT", cancel["code"])
        self.assertEqual(b"", peer.recv(1))
        sender.join(1)
        self.assertFalse(sender.is_alive())
        self.assertEqual("RESTART_TIMEOUT", sender.status()[3]["code"])
        sender.prepare_restart(2)
        self.assertIsNone(sender.restart_status()[2])

    def test_input_after_fresh_init_fences_before_ready_on_the_original_budget(self):
        original, old_peer = self.open_ready()
        operation = original.request_restart(2)
        self.assertEqual("restart_request", protocol.recv_message(old_peer)["type"])
        original.prepare_restart(2)
        self.assertEqual("restart_prepared", protocol.recv_message(old_peer)["type"])
        self.grant(original, old_peer)

        fresh, peer = self.open_connection(operation, "permission")
        self.send(peer, greeting(12))
        self.wait_for(lambda: fresh.status()[0] == "preparing_init")
        fresh.prepare_init([description()])
        init = protocol.recv_message(peer)
        self.assertEqual(description(), init)
        self.assertIs(operation, fresh.request_restart(3))
        self.assertEqual(protocol.make_restart_request_message(12, 3), protocol.recv_message(peer))
        self.send(peer, ready(12, init))  # Ready became stale before it arrived.
        fresh.submit({"type": "frame", "stale": True})
        fresh.prepare_restart(3)
        self.assertEqual(protocol.make_restart_prepared_message(12, 3), protocol.recv_message(peer))
        self.assertEqual("restarting", fresh.status()[0])
        self.assertIs(operation, fresh.restart_status()[0])

    def test_unused_permission_expires_without_an_active_socket(self):
        self.use_patch(mock.patch.object(session, "RESTART_COORDINATION_SECONDS", 0.35))
        self.use_patch(mock.patch.object(session, "RESTART_TOTAL_SECONDS", 0.45))
        sender, peer = self.open_ready()
        operation = sender.request_restart(2)
        self.assertEqual("restart_request", protocol.recv_message(peer)["type"])
        sender.prepare_restart(2)
        self.assertEqual("restart_prepared", protocol.recv_message(peer)["type"])
        self.grant(sender, peer)
        sender.join(1)
        self.assertFalse(sender.is_alive())
        self.assertEqual("RESTART_TIMEOUT", operation.failure["code"])
        self.assertIn("permit", operation.failure["details"])
        self.assertIsNone(sender.restart_status()[2])
        self.assertFalse(select.select([self.listener], [], [], 0)[0])

    def test_fresh_handshake_preserves_a_stop_from_the_issuing_session(self):
        fresh, peer = self.open_connection(session.RestartWindow(), "permission")
        self.send(peer, state(10, "stopped", code="SUBJECT_CHANGED"))
        self.send(peer, state(11, "stopped", code="PREVIEW_NOT_READY", detail="Refresh required"))
        self.assertEqual(b"", peer.recv(1))
        fresh.join(1)
        self.assertEqual("PREVIEW_NOT_READY", fresh.status()[3]["code"])
        self.assertEqual("Refresh required", fresh.status()[3]["details"])

    def test_different_source_cannot_receive_init(self):
        fresh, peer = self.open_connection(session.RestartWindow(), "permission")
        self.send(peer, greeting(12, source="replacement-source"))
        self.assertEqual(b"", peer.recv(1))
        fresh.join(1)
        self.assertEqual("SUBJECT_CHANGED", fresh.status()[3]["code"])

    def test_different_actual_target_cannot_receive_frames(self):
        fresh, peer = self.open_connection(session.RestartWindow(), "permission")
        self.send(peer, greeting(12))
        self.wait_for(lambda: fresh.status()[0] == "preparing_init")
        fresh.prepare_init([description()])
        init = protocol.recv_message(peer)
        self.send(peer, ready(12, init, target="replacement-binding"))
        self.assertEqual("restart_cancel", protocol.recv_message(peer)["type"])
        self.assertEqual(b"", peer.recv(1))
        fresh.join(1)
        self.assertEqual("SUBJECT_CHANGED", fresh.status()[3]["code"])
