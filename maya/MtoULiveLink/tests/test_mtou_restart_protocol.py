# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Pure wire regressions for required subject identity and restart messages."""

import json
import pathlib
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from mtou_livelink import protocol  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
CORPUS = json.loads((ROOT / "protocol" / "conformance.json").read_text(encoding="utf-8"))
READY = next(case["payload"] for case in CORPUS["cases"] if case["id"] == "ready-basic")


class RestartProtocolTests(unittest.TestCase):
    def test_greeting_requires_explicit_subject_and_only_automatic_greeting_has_token(self):
        for value in (None, "", False, 1, []):
            with self.subTest(subject=value), self.assertRaisesRegex(ValueError, "maya_subject_id"):
                protocol.make_hello_message("2024", "cm", 1, 24, maya_subject_id=value)
        ordinary = protocol.make_hello_message("2024", "cm", 1, 24, maya_subject_id="scene-character")
        self.assertEqual("scene-character", ordinary["maya_subject_id"])
        self.assertNotIn("restart_token", ordinary)
        automatic = protocol.make_hello_message("2024", "cm", 1, 24,
                                                maya_subject_id="scene-character", restart_token="permit")
        self.assertEqual("permit", automatic["restart_token"])

    def test_builders_preserve_the_operation_identity_and_latest_snapshot_revision(self):
        request = protocol.make_restart_request_message(41, 7)
        prepared = protocol.make_restart_prepared_message(41, 8)
        cancel = protocol.make_restart_cancel_message(41, "RECOVERY_REFUSED", "Camera restoration failed.")
        self.assertEqual("input_changed", request["reason"])
        self.assertEqual(41, prepared["session_id"])
        self.assertEqual(8, prepared["source_revision"])
        self.assertEqual("RECOVERY_REFUSED", cancel["code"])
        self.assertEqual("Camera restoration failed.", cancel["detail"])
        for message in (request, prepared, cancel):
            self.assertEqual(message, protocol.validate_restart_message(message))
            self.assertTrue(protocol.encode_message(message))

    def test_builders_do_not_coerce_invalid_wire_values(self):
        for value in (True, "1", 0, -1, 1.5, 2147483648, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "session_id"):
                protocol.make_restart_prepared_message(value, 8)
        with self.assertRaisesRegex(ValueError, "reason"):
            protocol.make_restart_request_message(41, 7, "subject_changed")
        with self.assertRaisesRegex(ValueError, "code"):
            protocol.make_restart_cancel_message(41, "unknown")
        with self.assertRaisesRegex(ValueError, "detail"):
            protocol.make_restart_cancel_message(41, "RESTART_CANCELLED", None)

    def test_ready_commit_requires_complete_distinct_actual_targets(self):
        primary = dict(READY, object_id="character", target_instance_id="target-A")
        secondary = dict(READY, object_id="secondary", target_instance_id="target-B")
        self.assertEqual({"character": "target-A", "secondary": "target-B"},
                         protocol.validate_ready_identities([primary, secondary], ["character", "secondary"]))
        cases = [([primary], "complete"), ([primary, primary], "duplicate"),
                 ([primary, dict(secondary, target_instance_id="target-A")], "duplicate"),
                 ([primary, dict(secondary, object_id="unknown")], "unknown")]
        for replies, diagnostic in cases:
            with self.subTest(replies=replies), self.assertRaisesRegex(ValueError, diagnostic):
                protocol.validate_ready_identities(replies, ["character", "secondary"])

    def test_stop_code_controls_classification_and_detail_is_only_diagnostic(self):
        stopped = {"type": "restart_state", "session_id": 41, "state": "stopped",
                   "code": "SUBJECT_CHANGED", "detail": "RESTART_TIMEOUT is only text here."}
        self.assertEqual(stopped, protocol.validate_reply(stopped))
        with self.assertRaisesRegex(ValueError, "code"):
            protocol.validate_reply(dict(stopped, code="FUTURE_CODE", detail="SUBJECT_CHANGED"))


if __name__ == "__main__":
    unittest.main()
