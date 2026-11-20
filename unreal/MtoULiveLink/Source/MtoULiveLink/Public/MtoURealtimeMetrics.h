// Copyright (C) 2026 Alexis-Li
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under the GPL with additional terms; see NOTICE.
#pragma once

#include "CoreMinimal.h"

class AMtoULiveLinkActor;

/** One timing per frame at this stage, in monotonic seconds. */
struct FMtoURealtimeTiming
{
    uint64 Count = 0;
    double TotalSeconds = 0.0;
    double LatestSeconds = 0.0;
    double MaxSeconds = 0.0;

    void Add(double Seconds)
    {
        ++Count;
        TotalSeconds += Seconds;
        LatestSeconds = Seconds;
        MaxSeconds = FMath::Max(MaxSeconds, Seconds);
    }
};

/** Diagnostics for a connected object set under Maya's time authority. */
struct FMtoURealtimeMetrics
{
    FMtoURealtimeTiming Parse;
    FMtoURealtimeTiming Retarget;
    FMtoURealtimeTiming Apply;
    uint64 EditorFrames = 0;
    int32 AppliedHz = 0;
    int32 EditorFPS = 0;
};

/** Snapshot for a participating actor. An absent snapshot has no real-time diagnostics. Game thread only. */
MTOULIVELINK_API TOptional<FMtoURealtimeMetrics> MtoUGetRealtimeMetrics(const AMtoULiveLinkActor& Actor);
