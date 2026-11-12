# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Time control: which state drives time on one connection (ADR 0014).

Realtime preview, Cached Playback and linkage are exclusive. Cached Playback
and linkage each take a lease; while one is held, live sampling is paused and
delivery is ordered, and only the holder of the current lease can end it.
Whether an entry is allowed is decided here and nowhere else:

- linkage is refused with ``CACHE_NOT_CLEARED`` while the cache holds the
  connection: during Cached Playback, or until Unreal confirmed the clear that
  released the cache lease;
- Cached Playback is refused with ``MODE_CONFLICT`` during linkage.

Unreal answers every increasing clear id, and the connection's end resets this
state with it, so the wait for the confirmation has no timeout. The clear is
queued before the lease is released, so its confirmation can arrive first; the
newest confirmed id is kept, and a release whose clear it covers waits for
nothing.

A lease also names where the results of its state go: the connection routes
cache outcomes to the Cached Playback lease and linkage targets and receipts
to the linkage lease, and drops them while nobody holds that state.

The sender thread reports clears and Maya's main thread takes, releases and
reads leases, so the state is guarded by one lock. The pause and resume hooks
run on the thread that took or released the lease (Maya's main thread); the
change listener is called outside the lock and defers its own work.
"""

import threading

from .config import CACHED_MODE, LINKAGE_MODE, REALTIME_MODE


class TimeControlRefused(Exception):
    """An entry the current time-control state does not allow."""

    def __init__(self, code, message, details=""):
        self._code = code
        self._message = message
        self._details = details or message
        super(TimeControlRefused, self).__init__(message)

    @property
    def code(self):
        return self._code

    @property
    def message(self):
        return self._message

    @property
    def details(self):
        return self._details


class TimeControlLease(object):
    """One holder's claim on a time-control state."""

    def __init__(self, control, state, owner, receive=None):
        self._control = control
        self._state = state
        self._owner = owner
        self._receive = receive

    @property
    def state(self):
        return self._state

    @property
    def owner(self):
        return self._owner

    @property
    def receive(self):
        """Where the connection delivers this state's results, or None."""
        return self._receive

    @property
    def active(self):
        """True while this lease still drives time on its connection."""
        return self._control.holds(self)

    def release(self, clear_id=None):
        """Return to realtime; ``clear_id`` is the cache clear sent with it."""
        return self._control.release(self, clear_id)


class TimeControl(object):
    """The time-control state of one connection, created with its session."""

    def __init__(self, pause=None, resume=None, on_change=None):
        self._pause = pause
        self._resume = resume
        self._on_change = on_change
        self._lock = threading.Lock()
        self._lease = None
        self._pending_clear = None
        self._cleared_through = 0
        self._revision = 0
        self._terminated = False

    @property
    def state(self):
        with self._lock:
            return self._lease.state if self._lease is not None else REALTIME_MODE

    @property
    def revision(self):
        """The newest mode revision of this connection."""
        with self._lock:
            return self._revision

    def note_revision(self, revision):
        """Keep ``revision`` when it is newer; returns the connection's revision."""
        with self._lock:
            if isinstance(revision, int) and not isinstance(revision, bool):
                self._revision = max(self._revision, revision)
            return self._revision

    def cache_holds_connection(self):
        """True during Cached Playback and until Unreal confirmed its clear."""
        with self._lock:
            return self._cache_holds_locked()

    def pending_clear(self):
        """The clear id whose ``cache_cleared`` is still awaited, or None."""
        with self._lock:
            return self._pending_clear

    def holds(self, lease):
        with self._lock:
            return lease is not None and lease is self._lease

    def _cache_holds_locked(self):
        return (self._pending_clear is not None
                or (self._lease is not None and self._lease.state == CACHED_MODE))

    def acquire(self, state, owner, receive=None):
        """Take ``state`` for ``owner``; raises :class:`TimeControlRefused`.

        ``receive(reply)`` gets the results of ``state`` while the lease is
        current, on the sender thread. The same owner asking again for the
        state it holds gets its lease back without pausing twice.
        """
        if state not in (CACHED_MODE, LINKAGE_MODE):
            raise ValueError("time control state must be cached or linkage")
        with self._lock:
            current = self._lease
            if self._terminated:
                raise TimeControlRefused(
                    "STREAM_INTERRUPTED", "连接已结束，无法切换预览模式。")
            if current is not None and current.owner is owner \
                    and current.state == state:
                return current
            if state == LINKAGE_MODE and self._cache_holds_locked():
                raise TimeControlRefused(
                    "CACHE_NOT_CLEARED",
                    "缓存播放仍在使用该连接。" if current is not None
                    and current.state == CACHED_MODE
                    else "Unreal 尚未确认清除缓存。",
                    "time control: {0}, pending clear {1!r}".format(
                        current.state if current is not None else REALTIME_MODE,
                        self._pending_clear))
            if current is not None:
                raise TimeControlRefused(
                    "MODE_CONFLICT",
                    "镜头联动进行中，无法进入缓存播放。"
                    if current.state == LINKAGE_MODE
                    else "缓存播放进行中，无法再次进入。",
                    "time control: {0}, requested {1}".format(current.state, state))
            lease = TimeControlLease(self, state, owner, receive)
            self._lease = lease
        if self._pause is not None:
            self._pause()
        self._notify()
        return lease

    def release(self, lease, clear_id=None):
        """End ``lease`` when it is the current one; False changes nothing."""
        with self._lock:
            if lease is None or lease is not self._lease:
                return False
            self._lease = None
            if (lease.state == CACHED_MODE and clear_id is not None
                    and clear_id > self._cleared_through):
                self._pending_clear = clear_id
        if self._resume is not None:
            self._resume()
        self._notify()
        return True

    def cache_cleared(self, clear_id):
        """Unreal confirmed ``clear_id``; any thread. True when it ended the wait."""
        with self._lock:
            if not isinstance(clear_id, int) or isinstance(clear_id, bool):
                return False
            self._cleared_through = max(self._cleared_through, clear_id)
            pending = self._pending_clear
            if pending is None or clear_id < pending:
                return False
            self._pending_clear = None
        self._notify()
        return True

    def deliver(self, state, reply):
        """Hand ``reply`` to the current holder of ``state``; False drops it."""
        with self._lock:
            lease = self._lease
            receive = (lease.receive if lease is not None and lease.state == state
                       else None)
        if receive is None:
            return False
        receive(reply)
        return True

    def terminate(self):
        """The connection ended: every lease is void and nothing resumes."""
        with self._lock:
            self._terminated = True
            self._lease = None
            self._pending_clear = None

    def _notify(self):
        listener = self._on_change
        if listener is not None:
            listener()
