// Copyright (C) 2026 Alexis-Li
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under the GPL with additional terms; see NOTICE.
#pragma once

#include "CoreMinimal.h"

class UEditorEngine;

/** Connection-scoped editor updates. Never changes a saved editor preference. */
class FMtoUBackgroundUpdates
{
public:
    static FMtoUBackgroundUpdates& Get();
    void Start();
    void Stop();

private:
    friend class FMtoUBackgroundUpdatesTestAccess;
    void BeginFrame();
    bool ShouldDisableCPUThrottling() const;
    void RemoveCPUDelegate();

    FDelegateHandle FrameHandle;
    FDelegateHandle PreExitHandle;
    FDelegateHandle CPUHandle;
    TWeakObjectPtr<UEditorEngine> RegisteringEditor;
};
