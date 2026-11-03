"""The time-control transition table, through its own interface."""

import pathlib
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mtou_livelink.config import CACHED_MODE, LINKAGE_MODE, REALTIME_MODE  # noqa: E402
from mtou_livelink.timecontrol import TimeControl, TimeControlRefused  # noqa: E402


class Recorder(object):
    """The session hooks: what the connection's sampling path was told."""

    def __init__(self):
        self.calls = []

    def control(self):
        return TimeControl(pause=lambda: self.calls.append("pause"),
                           resume=lambda: self.calls.append("resume"),
                           on_change=lambda: self.calls.append("change"))


class TransitionTableTests(unittest.TestCase):
    def setUp(self):
        self.recorder = Recorder()
        self.control = self.recorder.control()
        self.cache = object()
        self.linkage = object()

    def refusal(self, state, owner):
        with self.assertRaises(TimeControlRefused) as caught:
            self.control.acquire(state, owner)
        return caught.exception.code

    def test_realtime_to_cached_pauses_sampling(self):
        lease = self.control.acquire(CACHED_MODE, self.cache)

        self.assertEqual(CACHED_MODE, self.control.state)
        self.assertTrue(lease.active)
        self.assertTrue(self.control.cache_holds_connection())
        self.assertEqual(["pause", "change"], self.recorder.calls)

    def test_realtime_to_linkage_pauses_sampling(self):
        lease = self.control.acquire(LINKAGE_MODE, self.linkage)

        self.assertEqual(LINKAGE_MODE, self.control.state)
        self.assertTrue(lease.active)
        self.assertFalse(self.control.cache_holds_connection())
        self.assertEqual(["pause", "change"], self.recorder.calls)

    def test_linkage_is_refused_until_the_released_cache_clear_is_confirmed(self):
        self.control.acquire(CACHED_MODE, self.cache).release(clear_id=3)

        self.assertEqual(REALTIME_MODE, self.control.state)
        self.assertEqual("CACHE_NOT_CLEARED", self.refusal(LINKAGE_MODE, self.linkage))
        # An older clear does not answer the one the release sent.
        self.assertFalse(self.control.cache_cleared(2))
        self.assertTrue(self.control.cache_holds_connection())
        self.assertTrue(self.control.cache_cleared(3))
        self.assertFalse(self.control.cache_holds_connection())
        self.assertEqual(LINKAGE_MODE,
                         self.control.acquire(LINKAGE_MODE, self.linkage).state)

    def test_cached_refuses_linkage(self):
        self.control.acquire(CACHED_MODE, self.cache)
        self.recorder.calls[:] = []

        self.assertEqual("CACHE_NOT_CLEARED", self.refusal(LINKAGE_MODE, self.linkage))
        self.assertEqual(CACHED_MODE, self.control.state)
        self.assertEqual([], self.recorder.calls)

    def test_linkage_refuses_cached(self):
        self.control.acquire(LINKAGE_MODE, self.linkage)
        self.recorder.calls[:] = []

        self.assertEqual("MODE_CONFLICT", self.refusal(CACHED_MODE, self.cache))
        self.assertEqual(LINKAGE_MODE, self.control.state)
        self.assertEqual([], self.recorder.calls)

    def test_the_same_holder_gets_its_lease_back_without_pausing_twice(self):
        for state, owner in ((CACHED_MODE, self.cache), (LINKAGE_MODE, self.linkage)):
            with self.subTest(state=state):
                control = Recorder().control()
                first = control.acquire(state, owner)
                self.assertIs(first, control.acquire(state, owner))
                self.assertEqual(state, control.state)

        self.control.acquire(CACHED_MODE, self.cache)
        self.control.acquire(CACHED_MODE, self.cache)
        self.assertEqual(["pause", "change"], self.recorder.calls)

    def test_another_holder_of_the_same_state_is_refused(self):
        self.control.acquire(LINKAGE_MODE, self.linkage)
        self.assertEqual("MODE_CONFLICT", self.refusal(LINKAGE_MODE, object()))

    def test_the_holder_releases_back_to_realtime(self):
        for state, owner in ((CACHED_MODE, self.cache), (LINKAGE_MODE, self.linkage)):
            with self.subTest(state=state):
                recorder = Recorder()
                control = recorder.control()
                lease = control.acquire(state, owner)
                recorder.calls[:] = []

                self.assertTrue(lease.release())
                self.assertEqual(REALTIME_MODE, control.state)
                self.assertFalse(lease.active)
                self.assertEqual(["resume", "change"], recorder.calls)

    def test_a_cache_release_without_a_clear_leaves_nothing_to_confirm(self):
        self.control.acquire(CACHED_MODE, self.cache).release()

        self.assertFalse(self.control.cache_holds_connection())
        self.assertIsNone(self.control.pending_clear())

    def test_old_and_repeated_releases_change_nothing(self):
        old = self.control.acquire(LINKAGE_MODE, self.linkage)
        old.release()
        current = self.control.acquire(CACHED_MODE, self.cache)
        self.recorder.calls[:] = []

        self.assertFalse(old.release())
        self.assertFalse(old.release())
        self.assertFalse(self.control.release(None))
        self.assertEqual(CACHED_MODE, self.control.state)
        self.assertTrue(current.active)
        self.assertEqual([], self.recorder.calls)

        self.assertTrue(current.release(clear_id=1))
        self.assertFalse(current.release(clear_id=2))
        self.assertEqual(1, self.control.pending_clear())

    def test_termination_voids_every_lease_and_resumes_nothing(self):
        lease = self.control.acquire(CACHED_MODE, self.cache)
        self.recorder.calls[:] = []

        self.control.terminate()

        self.assertFalse(lease.active)
        self.assertEqual(REALTIME_MODE, self.control.state)
        self.assertFalse(self.control.cache_holds_connection())
        self.assertFalse(lease.release(clear_id=4))
        self.assertIsNone(self.control.pending_clear())
        self.assertEqual("STREAM_INTERRUPTED", self.refusal(LINKAGE_MODE, self.linkage))
        self.assertEqual([], self.recorder.calls)

    def test_the_connection_keeps_its_newest_mode_revision(self):
        self.assertEqual(0, self.control.revision)
        self.assertEqual(4, self.control.note_revision(4))
        self.assertEqual(4, self.control.note_revision(2))
        self.assertEqual(4, self.control.note_revision(None))
        self.assertEqual(4, self.control.revision)


if __name__ == "__main__":
    unittest.main()
