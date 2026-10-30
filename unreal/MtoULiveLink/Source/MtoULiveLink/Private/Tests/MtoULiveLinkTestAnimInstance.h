// Copyright (C) 2026 Alexis-Li
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under the GPL with additional terms; see NOTICE.
#pragma once

#include "Animation/AnimInstance.h"

#include "MtoULiveLinkTestAnimInstance.generated.h"

UCLASS()
class UMtoULiveLinkConflictingPostProcess : public UAnimInstance
{
    GENERATED_BODY()

public:
    virtual void NativeInitializeAnimation() override;
    virtual void NativeUpdateAnimation(float DeltaSeconds) override;
};
