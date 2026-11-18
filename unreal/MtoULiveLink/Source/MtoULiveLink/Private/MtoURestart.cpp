// Copyright (C) 2026 Alexis-Li
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under the GPL with additional terms; see NOTICE.
#include "MtoULiveLinkSource.h"
#include "MtoULiveLinkBinding.h"
#include "Engine/World.h"
#include "Misc/ScopeLock.h"
#include "UObject/UObjectIterator.h"

void FMtoULiveLinkSource::CaptureSubjectsOnGameThread()
{
    ProtectedSubjects.Reset();
    for (const TCHAR* Id : {FMtoUObjectIds::Character, FMtoUObjectIds::Secondary})
    {
        const FMtoULinkagePairing* Pairing = LinkageSession.FindPairing(Id);
        AMtoULiveLinkActor* Actor = Pairing ? Pairing->Actor.Get() : nullptr;
        if (!Pairing && FString(Id) == FMtoUObjectIds::Character)
        {
            int32 Count = 0;
            for (TObjectIterator<AMtoULiveLinkActor> It; It; ++It)
            {
                if (!It->HasAnyFlags(RF_ClassDefaultObject) && IsValid(*It)
                    && It->GetWorld() && It->GetWorld()->WorldType == EWorldType::Editor)
                {
                    Actor = *It;
                    ++Count;
                }
            }
            if (Count != 1) { Actor = nullptr; }
        }
        if (Actor)
        {
            FProtectedSubject& Subject = ProtectedSubjects.AddDefaulted_GetRef();
            Subject.ObjectId = Id;
            Subject.Actor = Actor;
            Subject.Binding = Actor->GetBinding();
            Subject.World = Actor->GetWorld();
            Subject.PairingGeneration = Pairing ? Pairing->Generation : FString();
            Subject.InstanceId = FGuid::NewGuid().ToString(EGuidFormats::Digits);
        }
    }
}

bool FMtoULiveLinkSource::ValidateSubjectsOnGameThread() const
{
    for (const FProtectedSubject& Subject : ProtectedSubjects)
    {
        const AMtoULiveLinkActor* Actor = Subject.Actor.Get();
        const FMtoULinkagePairing* Pairing = LinkageSession.FindPairing(Subject.ObjectId);
        if (!Actor || !Subject.World.IsValid() || Actor->GetWorld() != Subject.World.Get()
            || Actor->GetBinding() != Subject.Binding.Get()
            || (Pairing ? Pairing->Generation : FString()) != Subject.PairingGeneration
            || (Pairing && Pairing->Actor.Get() != Actor))
        {
            return false;
        }
    }
    return !ProtectedSubjects.IsEmpty();
}

void FMtoULiveLinkSource::StopRestart(const FString& Code, const FString& Detail)
{
    FScopeLock Lock(&RestartMutex);
    Restart.Stop(Code, Detail);
    bRestartStatusDirty = true;
}

void FMtoULiveLinkSource::ReflectRestartOnGameThread()
{
    FMtoURestartOperation Copy;
    {
        FScopeLock Lock(&RestartMutex);
        if (!bRestartStatusDirty) { return; }
        bRestartStatusDirty = false;
        Copy = Restart;
    }
    const bool bStopped = Copy.Phase == FMtoURestartOperation::EPhase::Stopped;
    const FString Detail = bStopped ? Copy.Code + TEXT(": ") + Copy.Detail
        : TEXT("Reconnecting after inputs changed...");
    SetStatus(Detail);
    for (const FProtectedSubject& Subject : ProtectedSubjects)
    {
        if (AMtoULiveLinkActor* Actor = Subject.Actor.Get())
        {
            Actor->SetConnectionState(bStopped ? EMtoUConnectionState::Error
                : EMtoUConnectionState::Reconnecting, Detail);
        }
    }
}

void FMtoULiveLinkSource::TickRestartOnGameThread()
{
    uint64 Cleanup = 0;
    {
        FScopeLock Lock(&RestartMutex);
        if (Restart.bNeedsCleanup)
        {
            Cleanup = Restart.OldSession;
            Restart.bNeedsCleanup = false;
        }
    }
    if (Cleanup)
    {
        // Finish restoring the old stream before granting a new transport.
        // Timeout does not waive this cleanup, even if the worker closed it.
        EndStreamingSessionOnGameThread(Cleanup);
        const FString Residual = LinkageSession.GetView().RecoveryResidual;
        FScopeLock Lock(&RestartMutex);
        if (Restart.OldSession == Cleanup)
        {
            if (!Residual.IsEmpty()) { Restart.Stop(TEXT("RECOVERY_REFUSED"), Residual); }
            else { Restart.bRecovered = true; }
            bRestartStatusDirty = true;
        }
    }
    bool bActive = false;
    {
        FScopeLock Lock(&RestartMutex);
        bActive = Restart.IsActive();
    }
    if (bActive && !ValidateSubjectsOnGameThread())
    {
        StopRestart(TEXT("SUBJECT_CHANGED"), TEXT("The World, Actor, Binding or pairing instance changed."));
    }
    {
        FScopeLock Lock(&RestartMutex);
        if (Restart.Expire(RestartNow())) { bRestartStatusDirty = true; }
        if (Restart.Phase == FMtoURestartOperation::EPhase::Ending
            && Restart.bRecovered && Restart.PreparedRevision > 0
            && (Restart.SourceRevision == 0 || Restart.SourceRevision == Restart.PreparedRevision))
        {
            Restart.Token = FGuid::NewGuid().ToString(EGuidFormats::Digits);
            Restart.Phase = FMtoURestartOperation::EPhase::Permitted;
            Restart.bStatePending = true;
            bRestartStatusDirty = true;
        }
    }
    ReflectRestartOnGameThread();
}
