// Copyright (C) 2026 Alexis-Li
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under the GPL with additional terms; see NOTICE.
#pragma once

#include "LiveLinkRemapAsset.h"
#include "MtoUPreviewRemapAsset.generated.h"

class USkeletalMeshComponent;

/** The default Live Link remap, with a stamp for the frame actually evaluated by the display. */
UCLASS(Transient, NotBlueprintable)
class UMtoUPreviewRemapAsset final : public ULiveLinkRemapAsset
{
    GENERATED_BODY()

public:
    void Initialize() override;
    void BeginDestroy() override;
    void BuildPoseFromAnimationData(float DeltaTime, const FLiveLinkSkeletonStaticData* Skeleton,
        const FLiveLinkAnimationFrameData* Frame, FCompactPose& OutPose) override;

    static const FName RealtimeFrameKey;

private:
    void OnBoneTransformsFinalized();

    TWeakObjectPtr<USkeletalMeshComponent> Display;
    FDelegateHandle FinalizationHandle;
    // Evaluation can run on a worker. Unreal joins the evaluation task before finalizing bones.
    uint64 Generation = 0;
    double StartedSeconds = 0.0;
};
