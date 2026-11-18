// Copyright (C) 2026 Alexis-Li
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under the GPL with additional terms; see NOTICE.
#pragma once

#include "CoreMinimal.h"

/** One bounded automatic connection intent. The Source holds RestartMutex for
 * every access, including while the game thread is unavailable. Times are
 * absolute local monotonic deadlines; no peer activity can renew them. */
struct FMtoURestartOperation
{
    enum class EPhase : uint8 { Idle, Ending, Permitted, Reconnecting, Ready, Stopped };
    EPhase Phase = EPhase::Idle;
    uint64 OldSession = 0;
    uint64 ActiveSession = 0;
    int32 SourceRevision = 0;
    int32 PreparedRevision = 0;
    double CoordinationDeadline = 0;
    double OperationDeadline = 0;
    bool bRecovered = false;
    bool bPermitDelivered = false;
    bool bNeedsCleanup = false;
    bool bStatePending = false;
    FString Token;
    FString Code;
    FString Detail;
    bool IsActive() const
    {
        return Phase == EPhase::Ending || Phase == EPhase::Permitted || Phase == EPhase::Reconnecting;
    }
    bool Begin(uint64 Session, double Now)
    {
        if (Phase == EPhase::Stopped && ActiveSession == Session) { return false; }
        if (!IsActive())
        {
            *this = FMtoURestartOperation();
            CoordinationDeadline = Now + 5.0;
            OperationDeadline = Now + 32.0;
        }
        else if (Phase == EPhase::Ending && ActiveSession == Session) { return true; }
        OldSession = Session;
        ActiveSession = Session;
        Phase = EPhase::Ending;
        PreparedRevision = 0;
        bRecovered = false;
        bPermitDelivered = false;
        bNeedsCleanup = true;
        bStatePending = true;
        Token.Reset();
        return true;
    }
    bool RequestFromMaya(uint64 Session, int32 Revision, double Now)
    {
        // The operation carries the covered revision across its automatic
        // transports. Repeats must leave the preparation and permit intact.
        if ((ActiveSession == Session && Revision <= SourceRevision) || !Begin(Session, Now)) { return false; }
        SourceRevision = Revision;
        PreparedRevision = 0;
        return true;
    }
    void Stop(const FString& InCode, const FString& InDetail)
    {
        // Preserve the first observed concrete reason; timeout and EOF cannot
        // replace a subject, recovery, negotiation or operator refusal.
        if (Phase == EPhase::Stopped && !Code.IsEmpty()) { return; }
        Phase = EPhase::Stopped;
        Code = InCode;
        Detail = InDetail;
        Token.Reset();
        bStatePending = true;
    }
    bool Expire(double Now)
    {
        if (!IsActive()) { return false; }
        if (Now >= OperationDeadline || ((Phase == EPhase::Ending || (Phase == EPhase::Permitted && !bPermitDelivered)) && Now >= CoordinationDeadline))
        {
            Stop(TEXT("RESTART_TIMEOUT"), Phase == EPhase::Ending
                ? (bRecovered ? TEXT("Timed out waiting for Maya restart_prepared.")
                    : TEXT("Timed out waiting for Unreal resource recovery."))
                : TEXT("Timed out waiting for the complete reconnected object set to become Ready."));
            return true;
        }
        return false;
    }
};
