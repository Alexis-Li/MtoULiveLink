// Copyright (C) 2026 Alexis-Li
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under the GPL with additional terms; see NOTICE.
#include "MtoUPreviewRemapAsset.h"

#include "Animation/AnimInstance.h"
#include "Components/SkeletalMeshComponent.h"
#include "MtoULiveLinkSource.h"
#include "Roles/LiveLinkAnimationTypes.h"

const FName UMtoUPreviewRemapAsset::RealtimeFrameKey(TEXT("MtoURealtimeFrame"));

void UMtoUPreviewRemapAsset::Initialize()
{
    Super::Initialize();
    const UAnimInstance* Instance = Cast<UAnimInstance>(GetOuter());
    Display = Instance ? Instance->GetSkelMeshComponent() : nullptr;
    if (USkeletalMeshComponent* Component = Display.Get())
    {
        FinalizationHandle = Component->RegisterOnBoneTransformsFinalizedDelegate(
            FOnBoneTransformsFinalizedMultiCast::FDelegate::CreateUObject(
                this, &UMtoUPreviewRemapAsset::OnBoneTransformsFinalized));
    }
}

void UMtoUPreviewRemapAsset::BeginDestroy()
{
    if (USkeletalMeshComponent* Component = Display.Get())
    {
        Component->UnregisterOnBoneTransformsFinalizedDelegate(FinalizationHandle);
    }
    Super::BeginDestroy();
}

void UMtoUPreviewRemapAsset::BuildPoseFromAnimationData(
    float DeltaTime, const FLiveLinkSkeletonStaticData* Skeleton,
    const FLiveLinkAnimationFrameData* Frame, FCompactPose& OutPose)
{
    Generation = 0;
    if (const FString* Stamp = Frame->MetaData.StringMetaData.Find(RealtimeFrameKey))
    {
        LexTryParseString(Generation, **Stamp);
        StartedSeconds = FPlatformTime::Seconds();
    }
    Super::BuildPoseFromAnimationData(DeltaTime, Skeleton, Frame, OutPose);
}

void UMtoUPreviewRemapAsset::OnBoneTransformsFinalized()
{
    const uint64 AppliedGeneration = Exchange(Generation, 0);
    USkeletalMeshComponent* Component = Display.Get();
    if (AppliedGeneration == 0 || !Component || Component->GetAnimInstance() != GetOuter())
    {
        return;
    }
    if (const TSharedPtr<FMtoULiveLinkSource> Source = MtoUGetActiveLinkageSource().Pin())
    {
        Source->NoteRealtimeDisplayApplication(*Component, AppliedGeneration, StartedSeconds);
    }
}
