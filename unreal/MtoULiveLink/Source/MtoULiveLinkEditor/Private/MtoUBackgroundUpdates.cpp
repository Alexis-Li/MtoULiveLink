// Copyright (C) 2026 Alexis-Li
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under the GPL with additional terms; see NOTICE.
#include "MtoUBackgroundUpdates.h"

#include "MtoULinkage.h"
#include "Editor.h"
#include "LevelEditorViewport.h"
#include "Misc/CoreDelegates.h"

FMtoUBackgroundUpdates& FMtoUBackgroundUpdates::Get()
{
    static FMtoUBackgroundUpdates Instance;
    return Instance;
}

void FMtoUBackgroundUpdates::Start()
{
    if (!FrameHandle.IsValid())
    {
        FrameHandle = FCoreDelegates::OnBeginFrame.AddRaw(this, &FMtoUBackgroundUpdates::BeginFrame);
        PreExitHandle = FCoreDelegates::OnEnginePreExit.AddRaw(this, &FMtoUBackgroundUpdates::Stop);
    }
}

void FMtoUBackgroundUpdates::Stop()
{
    FCoreDelegates::OnBeginFrame.Remove(FrameHandle);
    FCoreDelegates::OnEnginePreExit.Remove(PreExitHandle);
    FrameHandle.Reset();
    PreExitHandle.Reset();
    RemoveCPUDelegate();
}

bool FMtoUBackgroundUpdates::ShouldDisableCPUThrottling() const
{
    return MtoUGetTimeControlView().bConnected;
}

void FMtoUBackgroundUpdates::RemoveCPUDelegate()
{
    if (UEditorEngine* Editor = RegisteringEditor.Get())
    {
        Editor->ShouldDisableCPUThrottlingDelegates.RemoveAll(
            [this](const UEditorEngine::FShouldDisableCPUThrottling& Delegate)
            {
                return Delegate.GetHandle() == CPUHandle;
            });
    }
    CPUHandle.Reset();
    RegisteringEditor.Reset();
}

void FMtoUBackgroundUpdates::BeginFrame()
{
    if (!GEditor || !ShouldDisableCPUThrottling())
    {
        RemoveCPUDelegate();
        return;
    }
    if (RegisteringEditor.Get() != GEditor)
    {
        RemoveCPUDelegate();
    }
    if (!CPUHandle.IsValid())
    {
        UEditorEngine::FShouldDisableCPUThrottling Delegate =
            UEditorEngine::FShouldDisableCPUThrottling::CreateRaw(
                this, &FMtoUBackgroundUpdates::ShouldDisableCPUThrottling);
        CPUHandle = Delegate.GetHandle();
        RegisteringEditor = GEditor;
        GEditor->ShouldDisableCPUThrottlingDelegates.Add(MoveTemp(Delegate));
    }
    for (FLevelEditorViewportClient* Viewport : GEditor->GetLevelViewportClients())
    {
        if (Viewport)
        {
            Viewport->RequestRealTimeFrames(1);
        }
    }
}
