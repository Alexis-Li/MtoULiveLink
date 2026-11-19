# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Stage durations for realtime Maya sampling and delivery."""


class StageTimings:
    def __init__(self):
        self._stages = {}

    def add(self, stage, seconds):
        count, total, _, longest = self._stages.get(stage, (0, 0.0, 0.0, 0.0))
        self._stages[stage] = (count + 1, total + seconds, seconds, max(seconds, longest))

    def snapshot(self):
        return {
            stage: {"count": count, "latest_ms": latest * 1000.0,
                    "mean_ms": total * 1000.0 / count, "max_ms": longest * 1000.0}
            for stage, (count, total, latest, longest) in self._stages.items()
        }
