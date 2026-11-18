// Copyright (C) 2026 Alexis-Li
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under the GPL with additional terms; see NOTICE.
#if WITH_DEV_AUTOMATION_TESTS
#include "MtoUGuiAcceptance.h"
#include "MtoULiveLinkActor.h"
#include "MtoULiveLinkBinding.h"
#include "MtoULiveLinkPreview.h"
#include "MtoULinkage.h"

#include "Components/SkeletalMeshComponent.h"
#include "Editor.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Features/IModularFeatures.h"
#include "ILiveLinkClient.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "ScopedTransaction.h"
#include "UObject/UnrealType.h"
#include "UObject/Package.h"
#include "UObject/StrongObjectPtr.h"

/**
 * Opt-in regression of the workflows that existed before camera linkage,
 * driven through a real Maya GUI and the panel's own controls.
 *
 * The test exchanges the explicitly launched /Engine/Maps/Entry map for an
 * unsaved /Temp world, places a transient Binding Actor at the origin with a
 * transient duplicate of the fixture's Binding, and starts Maya through
 * `maya_gui_linkage_driver.py`. It then checks, against Maya's own world
 * pose and Morph weights: real-time preview; 缓存播放 capture, upload, UE
 * playback, seek and the return to real time; Model with Refresh and the
 * original/generated garment comparison; and Animation again after Model.
 * Each stage renders the Details at the fixture widths when the run can
 * render and saves the Maya panel. No project asset is edited or saved.
 */
namespace
{
using MtoUGuiAcceptance::ReadJsonFile;
using MtoUGuiAcceptance::WriteJsonFile;

FString CacheStateName(EMtoUCacheState State)
{
    switch (State)
    {
    case EMtoUCacheState::Idle: return TEXT("Idle");
    case EMtoUCacheState::Entered: return TEXT("Entered");
    case EMtoUCacheState::Receiving: return TEXT("Receiving");
    case EMtoUCacheState::Ready: return TEXT("Ready");
    case EMtoUCacheState::Playing: return TEXT("Playing");
    case EMtoUCacheState::Paused: return TEXT("Paused");
    case EMtoUCacheState::Completed: return TEXT("Completed");
    case EMtoUCacheState::Stopped: return TEXT("Stopped");
    case EMtoUCacheState::Failed: return TEXT("Failed");
    }
    return TEXT("Unknown");
}

class FGuiWorkflowCommand : public IAutomationLatentCommand
{
public:
    FGuiWorkflowCommand(FAutomationTestBase* InTest, FString InFixture)
        : Test(InTest), FixturePath(MoveTemp(InFixture)) {}

    ~FGuiWorkflowCommand() override
    {
        if (!bKeepMaya)
        {
            Driver.Terminate();
        }
    }

    bool Update() override
    {
        if (Steps.IsEmpty() && !Prepare())
        {
            return true;
        }
        if (Step >= Steps.Num())
        {
            Finish();
            return true;
        }
        const double Now = FPlatformTime::Seconds();
        if (StepStarted == 0.0)
        {
            StepStarted = Now;
        }
        MayaResult = Driver.Poll();
        ObserveUnreal();
        if (MayaResult.IsValid())
        {
            const TArray<TSharedPtr<FJsonValue>>* Errors = nullptr;
            if (MayaResult->TryGetArrayField(TEXT("errors"), Errors) && Errors->Num() > ReportedErrors)
            {
                for (int32 Index = ReportedErrors; Index < Errors->Num(); ++Index)
                {
                    Test->AddError(TEXT("Maya: ") + (*Errors)[Index]->AsString());
                }
                ReportedErrors = Errors->Num();
            }
        }
        if (Now - StepStarted > Steps[Step].Timeout)
        {
            const FMtoUCachePlaybackView Cache = Actor.IsValid() ? Actor->GetCachePlaybackView() : FMtoUCachePlaybackView();
            const FString Message = FString::Printf(TEXT("Step '%s' timed out; actor=%s cache=%s frames=%d current=%d; Maya: %s"),
                *Steps[Step].Name, Actor.IsValid() ? *UEnum::GetValueAsString(Actor->GetConnectionState()) : TEXT("none"),
                *CacheStateName(Cache.State), Cache.FrameCount, Cache.CurrentSourceFrame, *MayaPanel());
            Test->AddError(Message);
            Log(TEXT("TIMEOUT ") + Message);
            if (!LastUnrealState.IsEmpty())
            {
                Log(TEXT("Last Unreal observation: ") + LastUnrealState);
            }
            Finish();
            return true;
        }
        if (Steps[Step].Run())
        {
            if (bAborted)
            {
                Finish();
                return true;
            }
            Log(FString::Printf(TEXT("step %d '%s' done in %.2f s"), Step, *Steps[Step].Name, Now - StepStarted));
            ++Step;
            StepStarted = 0.0;
            Pending = -1;
            SettleUntil = 0.0;
        }
        return false;
    }

private:
    struct FStep
    {
        FString Name;
        double Timeout = 60.0;
        TFunction<bool()> Run;
    };

    void Add(const FString& Name, double Timeout, TFunction<bool()> Run)
    {
        Steps.Add({Name, Timeout, MoveTemp(Run)});
    }

    void Log(const FString& Line)
    {
        Test->AddInfo(Line);
        FFileHelper::SaveStringToFile(Line + LINE_TERMINATOR, *FPaths::Combine(EvidenceDir, TEXT("progress.log")),
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM, &IFileManager::Get(), FILEWRITE_Append);
    }

    FString MayaPanel() const
    {
        const TSharedPtr<FJsonObject>* Panel = nullptr;
        if (!MayaResult.IsValid() || !MayaResult->TryGetObjectField(TEXT("panel"), Panel))
        {
            return TEXT("no panel state");
        }
        FString Text;
        FJsonSerializer::Serialize(Panel->ToSharedRef(), TJsonWriterFactory<>::Create(&Text));
        return Text;
    }

    FString PanelField(const TCHAR* Field) const
    {
        const TSharedPtr<FJsonObject>* Panel = nullptr;
        FString Value;
        if (MayaResult.IsValid() && MayaResult->TryGetObjectField(TEXT("panel"), Panel))
        {
            (*Panel)->TryGetStringField(Field, Value);
        }
        return Value;
    }

    bool MayaConnected() const
    {
        bool bConnected = false;
        return MayaResult.IsValid() && MayaResult->TryGetBoolField(TEXT("connected"), bConnected) && bConnected;
    }

    bool Connected() const
    {
        return MayaConnected() && Actor.IsValid() && MtoUActorIsConnected(*Actor);
    }

    /** Sends one command once per step and reports whether Maya finished it. */
    bool SendOnce(const FString& Action, const TSharedPtr<FJsonObject>& Fields = nullptr)
    {
        if (Pending < 0)
        {
            Pending = Driver.Send(Action, Fields);
        }
        return Driver.Acked(Pending);
    }

    TSharedPtr<FJsonObject> Field(const FString& Name, const TSharedPtr<FJsonValue>& Value)
    {
        TSharedPtr<FJsonObject> Fields = MakeShared<FJsonObject>();
        Fields->SetField(Name, Value);
        return Fields;
    }

    bool Settled(double Seconds)
    {
        if (SettleUntil == 0.0)
        {
            SettleUntil = FPlatformTime::Seconds() + Seconds;
        }
        return FPlatformTime::Seconds() >= SettleUntil;
    }

    // ----------------------------------------------------------------- steps

    bool Prepare()
    {
        Fixture = ReadJsonFile(FixturePath);
        if (!Fixture.IsValid())
        {
            Test->AddError(TEXT("Invalid GUI workflow fixture"));
            return false;
        }
        EvidenceDir = Fixture->GetStringField(TEXT("evidence"));
        IFileManager::Get().MakeDirectory(*EvidenceDir, true);
        Fixture->TryGetArrayField(TEXT("details_widths"), DetailsWidths);
        const TSharedPtr<FJsonObject>* ReconnectObject = nullptr;
        if (Fixture->TryGetObjectField(TEXT("reconnect"), ReconnectObject))
        {
            Reconnect = *ReconnectObject;
            if (!ValidateReconnectFixture())
            {
                return false;
            }
        }

        const bool bEditorExit = FParse::Param(FCommandLine::Get(), TEXT("MtoUGuiReconnectEditorExit"));
        const bool bEditorProbe = FParse::Param(FCommandLine::Get(), TEXT("MtoUGuiReconnectEditorProbe"));
        if ((bEditorExit && bEditorProbe) || ((bEditorExit || bEditorProbe) && !Reconnect))
        {
            Test->AddError(TEXT("The external editor exit and probe modes are exclusive and require a reconnect fixture"));
            return false;
        }

        Add(TEXT("create the unsaved world and the Binding Actor"), 120.0, [this]() { return BuildScene(); });
        if (bEditorProbe)
        {
            Add(TEXT("announce the replacement Editor's target"), 30.0, [this]()
            {
                return WriteEditorMarker(TEXT("editor-probe-ready.json"));
            });
            Add(TEXT("external launcher proves Maya did not reconnect"), 90.0, [this]()
            {
                return IFileManager::Get().FileExists(*FPaths::Combine(EvidenceDir, TEXT("editor-probe-done.json")));
            });
            return true;
        }
        Add(TEXT("start Maya through the public entry"), 30.0, [this]()
        {
            return Driver.Start(*Test, Fixture->GetObjectField(TEXT("maya")), EvidenceDir)
                || Abort(TEXT("Maya did not start"));
        });
        Add(TEXT("Maya connects in Animation real time"), 600.0, [this]() { return Connected(); });
        if (bEditorExit)
        {
            Add(TEXT("hand the running Maya to the external launcher before Editor exit"), 30.0, [this]()
            {
                if (MayaSessionId() == 0 || AttemptCount() != 1)
                {
                    return false;
                }
                bKeepMaya = WriteEditorMarker(TEXT("editor-exit-ready.json"));
                return bKeepMaya;
            });
            return true;
        }

        // ---- real-time preview ------------------------------------------
        int32 Index = 0;
        for (const TSharedPtr<FJsonValue>& Frame : Fixture->GetArrayField(TEXT("realtime_frames")))
        {
            AddPoseCheck(FString::Printf(TEXT("real time %d"), Index++), Frame->AsNumber());
        }
        AddShots(TEXT("realtime"));
        if (Reconnect)
        {
            AddVisibilityCases();
        }

        // ---- cached playback ----------------------------------------------
        const TArray<TSharedPtr<FJsonValue>>& Range = Fixture->GetArrayField(TEXT("cache_range"));
        const int32 First = static_cast<int32>(Range[0]->AsNumber());
        const int32 Last = static_cast<int32>(Range[1]->AsNumber());
        const int32 Seek = static_cast<int32>(Fixture->GetNumberField(TEXT("cache_seek")));
        Add(TEXT("Maya selects 缓存播放"), 30.0, [this]()
        {
            return SendOnce(TEXT("mode"), Field(TEXT("target"), MakeShared<FJsonValueString>(TEXT("cached"))))
                && PanelField(TEXT("mode")) == TEXT("cached");
        });
        Add(TEXT("捕获并上传 a custom range"), 30.0, [this, First, Last]()
        {
            return SendOnce(TEXT("capture"), Field(TEXT("range"), MakeShared<FJsonValueArray>(
                TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueNumber>(First), MakeShared<FJsonValueNumber>(Last)})));
        });
        Add(TEXT("Unreal holds the complete cache"), 300.0, [this, First, Last]()
        {
            const FMtoUCachePlaybackView Cache = Actor->GetCachePlaybackView();
            if (Cache.State != EMtoUCacheState::Ready)
            {
                return false;
            }
            Log(FString::Printf(TEXT("cache ready: source %d-%d, %d frames at %.2f fps; Maya: %s"),
                Cache.StartFrame, Cache.EndFrame, Cache.FrameCount, Cache.Fps, *MayaPanel()));
            Test->TestEqual(TEXT("the cache starts at the captured range"), Cache.StartFrame, First);
            Test->TestEqual(TEXT("the cache ends at the captured range"), Cache.EndFrame, Last);
            Test->TestEqual(TEXT("the cache holds every captured frame"), Cache.FrameCount, Last - First + 1);
            return true;
        });
        AddShots(TEXT("cache-ready"));
        Add(TEXT("UE 播放 completes the cache"), 120.0, [this, First, Last]()
        {
            if (!bStarted)
            {
                bStarted = Test->TestTrue(TEXT("播放 starts"), Actor->StartCachedPlayback());
                return !bStarted;
            }
            // Maya may still be loading plug-ins; record what is loaded while the cache plays.
            const bool bSampled = SendOnce(TEXT("sample_plugins"),
                Field(TEXT("name"), MakeShared<FJsonValueString>(TEXT("playback"))));
            const FMtoUCachePlaybackView Cache = Actor->GetCachePlaybackView();
            if (Cache.State != EMtoUCacheState::Completed || !bSampled)
            {
                return false;
            }
            bStarted = false;
            Test->TestTrue(TEXT("Maya sampled its loaded plug-ins during playback"),
                Driver.PreferencesRecord(TEXT("playback"))->GetNumberField(TEXT("loaded_plugins")) >= 0.0);
            Log(FString::Printf(TEXT("cache playback completed: %d frames applied, last source frame %d"),
                Cache.AppliedFrames, Cache.CurrentSourceFrame));
            Test->TestEqual(TEXT("playback applies every frame"), Cache.AppliedFrames, Last - First + 1);
            Test->TestEqual(TEXT("natural completion holds the final frame"), Cache.CurrentSourceFrame, Last);
            return true;
        });
        Add(TEXT("定位 holds the seek frame"), 30.0, [this, Seek]()
        {
            if (!bStarted)
            {
                bStarted = Test->TestTrue(TEXT("定位 is accepted"), Actor->SeekCachedPlayback(Seek));
                return !bStarted;
            }
            const FMtoUCachePlaybackView Cache = Actor->GetCachePlaybackView();
            if (Cache.State != EMtoUCacheState::Paused || Cache.CurrentSourceFrame != Seek || !Cache.bHasAppliedSourceFrame)
            {
                return false;
            }
            bStarted = false;
            return Settled(0.5);
        });
        AddPoseCheck(TEXT("cached seek"), Seek);
        AddShots(TEXT("cache-seek"));
        Add(TEXT("Maya returns to 实时预览 and Unreal clears the cache"), 60.0, [this]()
        {
            if (!SendOnce(TEXT("mode"), Field(TEXT("target"), MakeShared<FJsonValueString>(TEXT("realtime")))))
            {
                return false;
            }
            const FMtoUCachePlaybackView Cache = Actor->GetCachePlaybackView();
            return PanelField(TEXT("mode")) == TEXT("realtime") && Cache.FrameCount == 0 && Connected();
        });
        AddPoseCheck(TEXT("real time after the cache"), Fixture->GetNumberField(TEXT("model_frame")) + 30.0);

        // ---- Model, Refresh and garment comparison --------------------------
        AddStage(TEXT("refresh"));
        // A failed Refresh is deterministic for the fixture, so the step
        // refreshes once and ends the run with its diagnostics.
        Add(TEXT("刷新预览 builds the Generated Preview"), 300.0, [this]()
        {
            const FMtoUPreviewReadiness Ready = FMtoUPreviewPreparation::RefreshActor(*Actor);
            Log(TEXT("Refresh: ") + Ready.Diagnostics.Left(400));
            return (Ready.IsUsable() && Ready.GeneratedPreview)
                || Abort(TEXT("Refresh did not reach a usable Generated Preview: ") + Ready.Diagnostics);
        });
        Add(TEXT("Refresh ends the session in Maya"), 60.0, [this]() { return !MayaConnected(); });
        AddStage(TEXT("model"));
        Add(TEXT("Maya selects 模型"), 30.0, [this]()
        {
            return SendOnce(TEXT("workflow"), Field(TEXT("target"), MakeShared<FJsonValueString>(TEXT("model"))))
                && PanelField(TEXT("workflow")) == TEXT("model");
        });
        Add(TEXT("Maya connects in Model"), 300.0, [this]() { return SendOnce(TEXT("connect")) && Connected(); });
        AddPoseCheck(TEXT("model"), Fixture->GetNumberField(TEXT("model_frame")));
        Add(TEXT("Model shows the Generated Preview"), 30.0, [this]()
        {
            return Test->TestEqual(TEXT("Model displays the generated garment"),
                static_cast<int32>(Actor->GetDisplayTarget()), static_cast<int32>(EMtoUDisplayTarget::GeneratedPreview));
        });
        AddShots(TEXT("model-generated"));
        Add(TEXT("显示原始服装"), 30.0, [this]()
        {
            if (!bStarted)
            {
                bStarted = Test->TestTrue(TEXT("the original garment can be shown"), Actor->ShowOriginalGarment());
                return !bStarted;
            }
            bStarted = false;
            Test->TestEqual(TEXT("the original garment is displayed"),
                static_cast<int32>(Actor->GetDisplayTarget()), static_cast<int32>(EMtoUDisplayTarget::OriginalGarment));
            Test->TestTrue(TEXT("the comparison keeps the Model session"), Connected());
            return Settled(0.5);
        });
        AddShots(TEXT("model-original"));
        Add(TEXT("显示生成服装"), 30.0, [this]()
        {
            Test->TestTrue(TEXT("the generated garment can be restored"), Actor->RestoreGeneratedGarment());
            Test->TestEqual(TEXT("the generated garment is displayed again"),
                static_cast<int32>(Actor->GetDisplayTarget()), static_cast<int32>(EMtoUDisplayTarget::GeneratedPreview));
            return Test->TestTrue(TEXT("the comparison keeps the Model session"), Connected());
        });

        // ---- Animation again ------------------------------------------------
        if (Reconnect)
        {
            AddMayaRestart(TEXT("bs-off"), TEXT("blendshapes"),
                Field(TEXT("enabled"), MakeShared<FJsonValueBoolean>(false)), false);
            AddMayaRestart(TEXT("bs-on"), TEXT("blendshapes"),
                Field(TEXT("enabled"), MakeShared<FJsonValueBoolean>(true)));
            AddMayaRestart(TEXT("animation-again"), TEXT("workflow"),
                Field(TEXT("target"), MakeShared<FJsonValueString>(TEXT("animation"))));
            Add(TEXT("Maya shows Animation after automatic negotiation"), 30.0, [this]()
            {
                Test->TestEqual(TEXT("the panel is in Animation"), PanelField(TEXT("workflow")),
                    FString(TEXT("animation")));
                return true;
            });
            AddPartCases();
            AddSubjectCases();
        }
        else
        {
            AddStage(TEXT("animation-again"));
            Add(TEXT("Maya disconnects"), 60.0, [this]() { return SendOnce(TEXT("disconnect")) && !MayaConnected(); });
            Add(TEXT("Maya selects Animation"), 30.0, [this]()
            {
                return SendOnce(TEXT("workflow"), Field(TEXT("target"), MakeShared<FJsonValueString>(TEXT("animation"))))
                    && PanelField(TEXT("workflow")) == TEXT("animation");
            });
            Add(TEXT("Maya connects in Animation again"), 300.0, [this]()
            {
                return SendOnce(TEXT("connect")) && Connected();
            });
        }
        AddPoseCheck(TEXT("animation after Model"), Fixture->GetArrayField(TEXT("realtime_frames"))[0]->AsNumber());
        AddShots(TEXT("animation-again"));
        if (Reconnect)
        {
            AddMayaSceneExit();
        }
        Add(TEXT("quit Maya"), 120.0, [this]()
        {
            if (Pending < 0)
            {
                Pending = Driver.Send(TEXT("quit"));
            }
            return !Driver.IsRunning();
        });
        return true;
    }

    bool WriteEditorMarker(const TCHAR* Filename) const
    {
        TSharedRef<FJsonObject> Marker = MakeShared<FJsonObject>();
        Marker->SetNumberField(TEXT("editor_pid"), FPlatformProcess::GetCurrentProcessId());
        Marker->SetNumberField(TEXT("attempts"), AttemptCount());
        Marker->SetNumberField(TEXT("session_id"), MayaSessionId());
        const FString Path = FPaths::Combine(EvidenceDir, Filename);
        WriteJsonFile(Path, Marker);
        return IFileManager::Get().FileExists(*Path);
    }

    bool ValidateReconnectFixture()
    {
        const TArray<TSharedPtr<FJsonValue>>* Frames = nullptr;
        const TArray<TSharedPtr<FJsonValue>>* Outfits = nullptr;
        const TArray<TSharedPtr<FJsonValue>>* Visibility = nullptr;
        const TSharedPtr<FJsonObject>& Maya = Fixture->GetObjectField(TEXT("maya"));
        const TArray<TSharedPtr<FJsonValue>>* Morphs = nullptr;
        if (!Reconnect->TryGetArrayField(TEXT("oracle_frames"), Frames) || Frames->Num() != 2
            || (*Frames)[0]->AsNumber() == (*Frames)[1]->AsNumber()
            || !Reconnect->TryGetArrayField(TEXT("visibility_cases"), Visibility) || Visibility->Num() < 2
            || !Reconnect->TryGetArrayField(TEXT("outfit_cases"), Outfits) || Outfits->IsEmpty()
            || !Maya->TryGetArrayField(TEXT("keyed_morphs"), Morphs) || Morphs->IsEmpty()
            || !Maya->HasTypedField<EJson::String>(TEXT("app_dir_template"))
            || !Maya->HasTypedField<EJson::String>(TEXT("legacy_display"))
            || !Maya->HasTypedField<EJson::String>(TEXT("legacy_enum"))
            || !Maya->HasTypedField<EJson::String>(TEXT("secondary_root"))
            || !Reconnect->HasTypedField<EJson::String>(TEXT("legacy_outfit"))
            || !Reconnect->HasTypedField<EJson::String>(TEXT("part_name"))
            || !Reconnect->HasTypedField<EJson::String>(TEXT("incompatible_mesh"))
            || !Reconnect->HasTypedField<EJson::String>(TEXT("failure_code")))
        {
            Test->AddError(TEXT("Reconnect fixture requires two distinct oracle_frames, visibility_cases, outfit_cases, "
                "legacy_outfit, part_name, incompatible_mesh, failure_code and Maya app_dir_template, keyed_morphs, "
                "legacy_display, legacy_enum and secondary_root."));
            return false;
        }
        return true;
    }

    TSharedPtr<FJsonObject> LatestMayaConnection() const
    {
        const TArray<TSharedPtr<FJsonValue>>* Connections = nullptr;
        return MayaResult && MayaResult->TryGetArrayField(TEXT("connections"), Connections) && !Connections->IsEmpty()
            ? Connections->Last()->AsObject() : nullptr;
    }

    int32 AttemptCount() const
    {
        const TArray<TSharedPtr<FJsonValue>>* Attempts = nullptr;
        return MayaResult && MayaResult->TryGetArrayField(TEXT("connection_attempts"), Attempts) ? Attempts->Num() : 0;
    }

    int32 MayaSessionId() const
    {
        const TSharedPtr<FJsonObject> Connection = LatestMayaConnection();
        int32 Id = 0;
        if (Connection)
        {
            Connection->TryGetNumberField(TEXT("session_id"), Id);
        }
        return Id;
    }

    FString SourceStatus() const
    {
        IModularFeatures& Features = IModularFeatures::Get();
        if (Features.IsModularFeatureAvailable(ILiveLinkClient::ModularFeatureName))
        {
            const ILiveLinkClient& Client = Features.GetModularFeature<ILiveLinkClient>(ILiveLinkClient::ModularFeatureName);
            for (const FGuid& Source : Client.GetSources())
            {
                if (Client.GetSourceType(Source).ToString() == TEXT("MtoU_LiveLink"))
                {
                    return Client.GetSourceStatus(Source).ToString();
                }
            }
        }
        return {};
    }

    void ObserveUnreal()
    {
        if (MonitoredStage.IsEmpty())
        {
            return;
        }
        const FMtoULinkageView Linkage = MtoUGetLinkageView();
        const FString State = Actor.IsValid() ? UEnum::GetValueAsString(Actor->GetConnectionState()) : TEXT("no actor");
        const FString Detail = Actor.IsValid() ? Actor->GetConnectionDetail() : FString();
        const bool bReconnecting = Actor.IsValid()
            && Actor->GetConnectionState() == EMtoUConnectionState::Reconnecting;
        bUnrealReconnecting |= bReconnecting;
        const FString Source = SourceStatus();
        const FString Observation = FString::Printf(TEXT("%s | %d | %s | %s | %s"), *State,
            Linkage.Identity.SessionId, *Detail, *Linkage.ErrorCode, *Source);
        if (Observation == LastUnrealState)
        {
            return;
        }
        LastUnrealState = Observation;
        TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
        Row->SetStringField(TEXT("stage"), MonitoredStage);
        Row->SetNumberField(TEXT("seconds"), FPlatformTime::Seconds());
        Row->SetNumberField(TEXT("session_id"), Linkage.Identity.SessionId);
        Row->SetStringField(TEXT("state"), State);
        Row->SetStringField(TEXT("source_status"), Source);
        Row->SetStringField(TEXT("detail"), Detail);
        Row->SetStringField(TEXT("linkage_code"), Linkage.ErrorCode);
        UnrealHistory.Add(MakeShared<FJsonValueObject>(Row));
    }

    void BeginProbe(const FString& Stage)
    {
        AddStage(Stage);
        Add(Stage + TEXT(": record both connected hosts"), 30.0, [this, Stage]()
        {
            OldSession = MayaSessionId();
            if (!Connected() || OldSession == 0 || MtoUGetLinkageView().Identity.SessionId != OldSession)
            {
                return false;
            }
            OldAttempts = AttemptCount();
            const TSharedPtr<FJsonObject>* Capture = nullptr;
            OldCapturePaths.Reset();
            if (MayaResult->TryGetObjectField(TEXT("capture"), Capture) && *Capture)
            {
                OldCapturePaths = CapturePaths(*Capture);
            }
            MonitoredStage = Stage;
            LastUnrealState.Reset();
            bUnrealReconnecting = false;
            ObserveUnreal();
            return true;
        });
    }

    bool MayaSawReconnecting(const FString& Stage) const
    {
        const TArray<TSharedPtr<FJsonValue>>* Connections = nullptr;
        if (!MayaResult->TryGetArrayField(TEXT("connections"), Connections))
        {
            return false;
        }
        return Connections->ContainsByPredicate([&Stage](const TSharedPtr<FJsonValue>& Value)
        {
            const TSharedPtr<FJsonObject> Event = Value->AsObject();
            FString Status;
            return Event->GetStringField(TEXT("stage")) == Stage
                && Event->GetBoolField(TEXT("reconnecting"))
                && Event->TryGetStringField(TEXT("status"), Status)
                && Status.Contains(TEXT("正在重新连接"));
        });
    }

    void AddRestartCheck(const FString& Stage, bool bMorphs = true)
    {
        Add(Stage + TEXT(": both hosts negotiate a new session"), 40.0, [this, Stage]()
        {
            const int32 NewSession = MayaSessionId();
            if (!Connected() || NewSession == 0 || NewSession == OldSession
                || MtoUGetLinkageView().Identity.SessionId != NewSession)
            {
                return false;
            }
            Test->TestEqual(*(Stage + TEXT(": exactly one new Maya connection attempt")), AttemptCount(), OldAttempts + 1);
            Test->TestTrue(*(Stage + TEXT(": Maya shows reconnecting")), MayaSawReconnecting(Stage));
            Test->TestTrue(*(Stage + TEXT(": Unreal shows reconnecting")), bUnrealReconnecting);
            Test->TestEqual(*(Stage + TEXT(": Maya returns to realtime")), PanelField(TEXT("mode")),
                FString(TEXT("realtime")));
            Log(FString::Printf(TEXT("%s: session %d -> %d, attempts %d -> %d"),
                *Stage, OldSession, NewSession, OldAttempts, AttemptCount()));
            return true;
        });
        AddDistinctOracle(Stage, bMorphs);
    }

    void AddMayaRestart(const FString& Stage, const FString& Action,
        const TSharedPtr<FJsonObject>& Fields = nullptr, bool bMorphs = true)
    {
        BeginProbe(Stage);
        Add(Stage + TEXT(": Maya applies the input change"), 30.0,
            [this, Action, Fields]() { return SendOnce(Action, Fields); });
        AddRestartCheck(Stage, bMorphs);
    }

    TArray<FString> CapturePaths(const TSharedPtr<FJsonObject>& Capture) const
    {
        TArray<FString> Paths;
        if (Capture)
        {
            for (const TSharedPtr<FJsonValue>& Path : Capture->GetArrayField(TEXT("mesh_paths")))
            {
                Paths.Add(Path->AsString());
            }
        }
        Paths.Sort();
        return Paths;
    }

    void AddCaptureCheck(const FString& Stage, const FString& Present, const FString& Absent, bool bChanges = true)
    {
        Add(Stage + TEXT(": verify captured mesh paths"), 20.0, [this, Stage, Present, Absent, bChanges]()
        {
            const TArray<FString> Paths = CapturePaths(MayaResult->GetObjectField(TEXT("capture")));
            Test->TestEqual(*(Stage + TEXT(": mesh membership changes only when expected")), Paths != OldCapturePaths, bChanges);
            Test->TestTrue(*(Stage + TEXT(": expected visible skinned mesh is captured")), Paths.Contains(Present));
            Test->TestFalse(*(Stage + TEXT(": expected hidden skinned mesh is omitted")), Paths.Contains(Absent));
            Log(FString::Printf(TEXT("%s: %d captured meshes"), *Stage, Paths.Num()));
            return true;
        });
    }

    void AddVisibilityCases()
    {
        Add(TEXT("legacy label and root-only setup capture the same actual meshes"), 20.0, [this]()
        {
            const TSharedPtr<FJsonObject> Legacy = MayaResult->GetObjectField(TEXT("legacy_capture"));
            const TSharedPtr<FJsonObject> RootOnly = MayaResult->GetObjectField(TEXT("capture"));
            if (!Legacy || !RootOnly)
            {
                return Abort(TEXT("Maya did not report legacy and root-only captures"));
            }
            FString Display;
            Test->TestTrue(TEXT("the old enum determines the legacy label"),
                Legacy->GetStringField(TEXT("outfit")) == Reconnect->GetStringField(TEXT("legacy_outfit")));
            Test->TestFalse(TEXT("the root-only capture resolved no Display"), RootOnly->TryGetStringField(TEXT("display"), Display));
            Test->TestTrue(TEXT("legacy display label does not change mesh membership"),
                !CapturePaths(RootOnly).IsEmpty() && CapturePaths(Legacy) == CapturePaths(RootOnly));
            return true;
        });
        AddDistinctOracle(TEXT("root-only Template"));

        for (const TSharedPtr<FJsonValue>& Value : Reconnect->GetArrayField(TEXT("visibility_cases")))
        {
            const TSharedPtr<FJsonObject> Case = Value->AsObject();
            const FString Name = Case->GetStringField(TEXT("name"));
            const auto Fields = Field(TEXT("edits"), MakeShared<FJsonValueArray>(Case->GetArrayField(TEXT("edits"))));
            if (Case->GetBoolField(TEXT("restarts")))
            {
                AddMayaRestart(Name, TEXT("set_attrs"), Fields);
            }
            else
            {
                BeginProbe(Name);
                Add(Name + TEXT(": Maya applies display type"), 30.0,
                    [this, Fields]() { return SendOnce(TEXT("set_attrs"), Fields); });
                AddDistinctOracle(Name);
                Add(Name + TEXT(": the same session keeps streaming"), 20.0, [this, Name]()
                {
                    Test->TestTrue(*(Name + TEXT(": session and connection attempts unchanged")),
                        Connected() && MayaSessionId() == OldSession && AttemptCount() == OldAttempts
                        && !bUnrealReconnecting && !MayaSawReconnecting(Name));
                    return true;
                });
            }
            AddCaptureCheck(Name, Case->GetStringField(TEXT("present")), Case->GetStringField(TEXT("absent")),
                Case->GetBoolField(TEXT("restarts")));
        }

        for (const TSharedPtr<FJsonValue>& Value : Reconnect->GetArrayField(TEXT("outfit_cases")))
        {
            const TSharedPtr<FJsonObject> Case = Value->AsObject();
            const FString Name = Case->GetStringField(TEXT("name"));
            const FString Shown = Case->GetStringField(TEXT("present"));
            const FString Hidden = Case->GetStringField(TEXT("absent"));
            AddMayaRestart(Name, TEXT("set_attrs"),
                Field(TEXT("edits"), MakeShared<FJsonValueArray>(Case->GetArrayField(TEXT("edits")))));
            AddCaptureCheck(Name, Shown, Hidden);
            AddMayaRestart(Name + TEXT("-undo"), TEXT("undo"));
            AddCaptureCheck(Name + TEXT("-undo"), Hidden, Shown);
            AddMayaRestart(Name + TEXT("-redo"), TEXT("redo"));
            AddCaptureCheck(Name + TEXT("-redo"), Shown, Hidden);
        }
    }

    bool EditPart(const FString& PartName, bool bEnable, USkeletalMesh* Replacement = nullptr)
    {
        FMtoUCharacterPart* Part = Binding->AdditionalParts.FindByPredicate(
            [&PartName](const FMtoUCharacterPart& Item) { return Item.PartName == PartName; });
        if (!Part)
        {
            return Abort(TEXT("Missing fixture part ") + PartName);
        }
        const FScopedTransaction Transaction(NSLOCTEXT("MtoUGuiWorkflow", "EditPart", "Edit MtoU acceptance part"));
        FProperty* Property = FindFProperty<FProperty>(UMtoULiveLinkBinding::StaticClass(),
            GET_MEMBER_NAME_CHECKED(UMtoULiveLinkBinding, AdditionalParts));
        Binding->Modify();
        Binding->PreEditChange(Property);
        Part->bEnabled = bEnable;
        if (Replacement)
        {
            Part->SkeletalMesh = Replacement;
        }
        FPropertyChangedEvent Change(Property, EPropertyChangeType::ValueSet);
        Binding->PostEditChangeProperty(Change);
        return true;
    }

    void AddPartCase(const FString& Stage, TFunction<bool()> Apply, bool bShown)
    {
        BeginProbe(Stage);
        Add(Stage + TEXT(": transact the part edit"), 30.0, MoveTemp(Apply));
        AddRestartCheck(Stage);
        Add(Stage + TEXT(": verify the actual part component"), 30.0, [this, Stage, bShown]()
        {
            const FString Name = Reconnect->GetStringField(TEXT("part_name"));
            const FMtoUCharacterPart* Part = Binding->AdditionalParts.FindByPredicate(
                [&Name](const FMtoUCharacterPart& Item) { return Item.PartName == Name; });
            if (!Part)
            {
                return Abort(TEXT("Missing fixture part ") + Name);
            }
            const bool bDisplayed = Actor->GetCharacterPartComponents().ContainsByPredicate(
                [Part](const UMtoUCharacterPartComponent* Component)
                {
                    return Component && Component->IsRegistered() && Component->IsVisible()
                        && Component->GetSkeletalMeshAsset() == Part->SkeletalMesh;
                });
            Test->TestEqual(*(Stage + TEXT(": part presence matches the transaction")), bDisplayed, bShown);
            return true;
        });
    }

    void AddPartCases()
    {
        const FString Part = Reconnect->GetStringField(TEXT("part_name"));
        AddPartCase(TEXT("part-off"), [this, Part]() { return EditPart(Part, false); }, false);
        AddPartCase(TEXT("part-undo"), [this]()
        {
            return GEditor->UndoTransaction() || Abort(TEXT("Unreal could not undo the part transaction"));
        }, true);
        AddPartCase(TEXT("part-redo"), [this]()
        {
            return GEditor->RedoTransaction() || Abort(TEXT("Unreal could not redo the part transaction"));
        }, false);

        BeginProbe(TEXT("part-incompatible"));
        Add(TEXT("enable a part from an incompatible Skeleton"), 30.0, [this, Part]()
        {
            USkeletalMesh* Mesh = LoadObject<USkeletalMesh>(nullptr, *Reconnect->GetStringField(TEXT("incompatible_mesh")));
            if (!Mesh || Mesh->GetSkeleton() == Binding->SkeletalMesh->GetSkeleton())
            {
                return Abort(TEXT("Fixture requires a real part from an incompatible Skeleton"));
            }
            return EditPart(Part, true, Mesh);
        });
        AddStopped(TEXT("part-incompatible"), Reconnect->GetStringField(TEXT("failure_code")), 1, true, true);
        Add(TEXT("undo the incompatible part edit while stopped"), 30.0, [this]()
        {
            return GEditor->UndoTransaction() || Abort(TEXT("Unreal could not undo the incompatible part transaction"));
        });
        AddManualConnect(TEXT("part repaired"), 1);
    }

    void AddStopped(const FString& Stage, const FString& Code, int32 NewAttempts,
        bool bActorReason, bool bObserveFullWindow = false)
    {
        Add(Stage + TEXT(": both hosts stop with their reason and no retries"), 65.0,
            [this, Stage, Code, NewAttempts, bActorReason, bObserveFullWindow]()
        {
            if (AttemptCount() > OldAttempts + NewAttempts)
            {
                return Abort(Stage + TEXT(": Maya attempted an unauthorized reconnect"));
            }
            const FMtoULinkageView View = MtoUGetLinkageView();
            const TSharedPtr<FJsonObject> Connection = LatestMayaConnection();
            const bool bMayaStopped = Connection && !Connection->GetBoolField(TEXT("open"))
                && !Connection->GetBoolField(TEXT("reconnecting")) && !MayaConnected();
            if (!bMayaStopped || View.Identity.SessionId != 0 || AttemptCount() != OldAttempts + NewAttempts
                || (Actor.IsValid() && MtoUActorIsConnected(*Actor)))
            {
                return false;
            }
            FString MayaCode, MayaStatus;
            Connection->TryGetStringField(TEXT("code"), MayaCode);
            Connection->TryGetStringField(TEXT("status"), MayaStatus);
            const FString ActorDetail = Actor.IsValid() ? Actor->GetConnectionDetail() : FString();
            if (MayaCode != Code || MayaStatus.IsEmpty() || !SourceStatus().Contains(Code)
                || (bActorReason && !ActorDetail.Contains(Code)))
            {
                return false;
            }
            // For a rejected negotiation, observe the whole protocol restart window from stop.
            if (bObserveFullWindow && !Settled(32.0))
            {
                return false;
            }
            Log(FString::Printf(TEXT("%s: Maya %s (%s), Source '%s', Actor '%s', attempts %d -> %d"),
                *Stage, *MayaCode, *MayaStatus, *SourceStatus(), *ActorDetail, OldAttempts, AttemptCount()));
            return true;
        });
    }

    void AddManualConnect(const FString& Reason, int32 PriorAttempts = 0)
    {
        AddStage(TEXT("manual-connect"));
        Add(Reason + TEXT(": explicit Connect starts one attempt"), 90.0, [this, Reason, PriorAttempts]()
        {
            if (Pending < 0 && AttemptCount() != OldAttempts + PriorAttempts)
            {
                return Abort(Reason + TEXT(": another automatic attempt occurred before explicit Connect"));
            }
            if (!SendOnce(TEXT("connect")) || !Connected() || MayaSessionId() == OldSession)
            {
                return false;
            }
            Test->TestEqual(*(Reason + TEXT(": one manual connection attempt")), AttemptCount(),
                OldAttempts + PriorAttempts + 1);
            return true;
        });
    }

    void AddSubjectCases()
    {
        const FString Changed(TEXT("SUBJECT_CHANGED"));
        BeginProbe(TEXT("binding-replaced"));
        Add(TEXT("replace the Binding with another instance"), 30.0, [this]()
        {
            const FName Name = MakeUniqueObjectName(GetTransientPackage(), UMtoULiveLinkBinding::StaticClass());
            Binding.Reset(DuplicateObject<UMtoULiveLinkBinding>(Binding.Get(), GetTransientPackage(), Name));
            Actor->SetBinding(Binding.Get());
            return true;
        });
        AddStopped(TEXT("binding-replaced"), Changed, 0, true);
        AddManualConnect(TEXT("new Binding"));

        BeginProbe(TEXT("actor-replaced"));
        Add(TEXT("destroy the live Actor and place a new Actor"), 30.0, [this]()
        {
            UWorld* World = Actor->GetWorld();
            if (!World->DestroyActor(Actor.Get()))
            {
                return Abort(TEXT("Could not destroy the fixture Actor"));
            }
            return SpawnTarget(World);
        });
        AddStopped(TEXT("actor-replaced"), Changed, 0, false);
        AddManualConnect(TEXT("new Actor"));

        BeginProbe(TEXT("world-replaced"));
        Add(TEXT("open an unsaved empty level and place its Actor"), 90.0, [this]()
        {
            return SpawnTarget(GEditor->NewMap());
        });
        AddStopped(TEXT("world-replaced"), Changed, 0, false);
        AddManualConnect(TEXT("new level"));

        BeginProbe(TEXT("maya-role-replaced"));
        Add(TEXT("Maya sets the alternate skinned root"), 30.0, [this]()
        {
            return SendOnce(TEXT("set_role"), Field(TEXT("target"), MakeShared<FJsonValueString>(TEXT("alternate"))));
        });
        AddStopped(TEXT("maya-role-replaced"), Changed, 0, true);
        Add(TEXT("Maya sets the original skinned root"), 30.0, [this]()
        {
            return SendOnce(TEXT("set_role"), Field(TEXT("target"), MakeShared<FJsonValueString>(TEXT("primary"))));
        });
        AddManualConnect(TEXT("original Maya role"));
    }

    void AddMayaSceneExit()
    {
        BeginProbe(TEXT("maya-scene-replaced"));
        Add(TEXT("Maya opens a new unsaved scene"), 30.0, [this]() { return SendOnce(TEXT("new_scene")); });
        AddStopped(TEXT("maya-scene-replaced"), TEXT("SUBJECT_CHANGED"), 0, true);
    }

    /** Names the stage Maya's dialogs are recorded under from here on. */
    void AddStage(const FString& Stage)
    {
        Add(TEXT("dialog stage ") + Stage, 30.0, [this, Stage]()
        {
            return SendOnce(TEXT("stage"), Field(TEXT("name"), MakeShared<FJsonValueString>(Stage)));
        });
    }

    /** Maya's dialogs each stage expects; any other dialog fails the test. */
    TArray<MtoUGuiAcceptance::FExpectedDialog> ExpectedDialogs() const
    {
        TArray<MtoUGuiAcceptance::FExpectedDialog> Allowed = {
            // The fixture's Morph names differ between Maya and Unreal.
            {TEXT("startup"), TEXT("BLENDSHAPE_MISMATCH")},
            {TEXT("model"), TEXT("BLENDSHAPE_MISMATCH")},
            // Refresh Preview ends the session Maya was connected through.
            {TEXT("refresh"), TEXT("STREAM_INTERRUPTED")}};
        Allowed.Add({Reconnect ? TEXT("manual-connect") : TEXT("animation-again"),
            TEXT("BLENDSHAPE_MISMATCH")});
        return Allowed;
    }

    /** Maya moves to one frame; Unreal shows Maya's world pose and Morph weights there. */
    void AddPoseCheck(const FString& Name, double Frame)
    {
        Add(Name + TEXT(": Maya moves to the frame"), 30.0, [this, Frame]()
        {
            return SendOnce(TEXT("time"), Field(TEXT("frame"), MakeShared<FJsonValueNumber>(Frame))) && Settled(1.0);
        });
        Add(Name + TEXT(": compare"), 30.0, [this, Name]()
        {
            if (!SendOnce(TEXT("snapshot")))
            {
                return false;
            }
            ComparePose(Name);
            return true;
        });
    }

    void AddDistinctOracle(const FString& Stage, bool bMorphs = true)
    {
        int32 Index = 0;
        for (const TSharedPtr<FJsonValue>& FrameValue : Reconnect->GetArrayField(TEXT("oracle_frames")))
        {
            const int32 Pose = Index++;
            const FString Name = Stage + FString::Printf(TEXT(" oracle %d"), Pose + 1);
            const double Frame = FrameValue->AsNumber();
            Add(Name + TEXT(": Maya evaluates the frame"), 30.0, [this, Frame]()
            {
                const auto Fields = Field(TEXT("frame"), MakeShared<FJsonValueNumber>(Frame));
                Fields->SetBoolField(TEXT("preserve_undo"), true);
                return SendOnce(TEXT("time"), Fields);
            });
            Add(Name + TEXT(": Unreal displays the evaluated pose"), 30.0, [this, Name, Pose, bMorphs]()
            {
                return SendOnce(TEXT("snapshot")) && ComparePose(Name, Pose, bMorphs);
            });
        }
    }

    struct FOracleRotation
    {
        FQuat Maya;
        FQuat Shown;
    };

    bool ComparePose(const FString& Name, int32 DistinctPose = INDEX_NONE, bool bMorphs = true)
    {
        const TSharedPtr<FJsonObject> Snapshot = MayaResult->GetObjectField(TEXT("snapshot"));
        USkeletalMeshComponent* Primary = Actor->GetSkeletalMeshComponent();
        const FTransform Placement = Actor->GetActorTransform();
        double MaxWorld = 0.0;
        int32 Bones = 0;
        TMap<FName, FOracleRotation> DisplayedPose;
        for (const TPair<FString, TSharedPtr<FJsonValue>>& Joint : Snapshot->GetObjectField(TEXT("joints"))->Values)
        {
            const FName Bone(*Joint.Key);
            if (!Primary || Primary->GetBoneIndex(Bone) == INDEX_NONE)
            {
                continue;
            }
            const TArray<TSharedPtr<FJsonValue>>& World = Joint.Value->AsObject()->GetArrayField(TEXT("world"));
            // Common space: Maya (x, y, z) is Unreal (x, z, y), placed by the actor.
            const FVector Expected = Placement.TransformPosition(
                FVector(World[0]->AsNumber(), World[2]->AsNumber(), World[1]->AsNumber()));
            const FVector Shown = Primary->GetSocketLocation(Bone);
            MaxWorld = FMath::Max(MaxWorld, FVector::Distance(Expected, Shown));
            if (DistinctPose != INDEX_NONE)
            {
                FQuat Rotation;
                if (MtoUGuiAcceptance::MayaWorldRotation(Joint.Value->AsObject(), Placement.GetRotation(), Rotation))
                {
                    DisplayedPose.Add(Bone, {Rotation, Primary->GetSocketQuaternion(Bone)});
                }
            }
            ++Bones;
        }
        double MaxMorph = 0.0;
        double MaxExpected = 0.0;
        int32 Morphs = 0;
        const TSharedPtr<FJsonObject>* MorphValues = nullptr;
        if (Snapshot->TryGetObjectField(TEXT("morphs"), MorphValues))
        {
            for (const TPair<FString, TSharedPtr<FJsonValue>>& Morph : (*MorphValues)->Values)
            {
                const TArray<double> Shown = MtoUGuiAcceptance::DisplayedMorphWeights(*Actor, FName(*Morph.Key));
                for (const double Weight : Shown)
                {
                    const double Expected = bMorphs ? Morph.Value->AsNumber() : 0.0;
                    MaxMorph = FMath::Max(MaxMorph, FMath::Abs(Weight - Expected));
                }
                if (Shown.Num() > 0)
                {
                    ++Morphs;
                    MaxExpected = FMath::Max(MaxExpected, FMath::Abs(Morph.Value->AsNumber()));
                }
            }
        }
        const double WorldTolerance = Fixture->GetNumberField(TEXT("world_tolerance_cm"));
        const double MorphTolerance = Fixture->GetNumberField(TEXT("morph_tolerance"));
        double ExpectedRotation = 0.0, DisplayedRotation = 0.0, RotationError = 0.0;
        if (DistinctPose != INDEX_NONE)
        {
            if (DisplayedPose.Num() < 3 || MaxWorld >= WorldTolerance || Morphs == 0
                || MaxExpected <= 0.05 || MaxMorph >= MorphTolerance)
            {
                return false;
            }
            if (DistinctPose == 0)
            {
                FirstOraclePose = DisplayedPose;
            }
            else
            {
                for (const TPair<FName, FOracleRotation>& Pair : DisplayedPose)
                {
                    if (const FOracleRotation* First = FirstOraclePose.Find(Pair.Key))
                    {
                        const FQuat MayaChange = Pair.Value.Maya * First->Maya.Inverse();
                        const FQuat ShownChange = Pair.Value.Shown * First->Shown.Inverse();
                        ExpectedRotation = FMath::Max(ExpectedRotation,
                            FMath::RadiansToDegrees(MayaChange.AngularDistance(FQuat::Identity)));
                        DisplayedRotation = FMath::Max(DisplayedRotation,
                            FMath::RadiansToDegrees(ShownChange.AngularDistance(FQuat::Identity)));
                        RotationError = FMath::Max(RotationError,
                            FMath::RadiansToDegrees(MayaChange.AngularDistance(ShownChange)));
                    }
                }
                constexpr double RotationTolerance = 0.5;
                if (RotationError >= RotationTolerance)
                {
                    return false;
                }
                Test->TestTrue(*(Name + TEXT(": the real display changes between oracle frames")),
                    ExpectedRotation > RotationTolerance && DisplayedRotation > RotationTolerance);
                Log(FString::Printf(TEXT("%s: bone rotation Maya %.4f deg, displayed %.4f deg, max error %.4f deg"),
                    *Name, ExpectedRotation, DisplayedRotation, RotationError));
            }
        }
        double MayaTime = 0.0;
        Snapshot->TryGetNumberField(TEXT("time"), MayaTime);
        Log(FString::Printf(TEXT("%s at Maya frame %.1f: %d bones, max world %.4f cm; %d morphs, max Maya weight %.3f, max delta %.5f"),
            *Name, MayaTime, Bones, MaxWorld, Morphs, MaxExpected, MaxMorph));
        TSharedRef<FJsonObject> Record = MakeShared<FJsonObject>();
        Record->SetStringField(TEXT("stage"), Name);
        Record->SetNumberField(TEXT("max_world_cm"), MaxWorld);
        Record->SetNumberField(TEXT("bones"), Bones);
        Record->SetNumberField(TEXT("max_morph"), MaxMorph);
        Record->SetNumberField(TEXT("morphs"), Morphs);
        Record->SetObjectField(TEXT("maya_snapshot"), Snapshot);
        if (DistinctPose == 1)
        {
            Record->SetNumberField(TEXT("expected_rotation_deg"), ExpectedRotation);
            Record->SetNumberField(TEXT("displayed_rotation_deg"), DisplayedRotation);
            Record->SetNumberField(TEXT("max_rotation_error_deg"), RotationError);
        }
        WriteJsonFile(FPaths::Combine(EvidenceDir, FString::Printf(TEXT("pose-%02d.json"), PoseIndex++)), Record);
        Test->TestTrue(*(Name + TEXT(": the primary display has the oracle bones")), Bones >= 3);
        Test->TestTrue(*(Name + TEXT(": Unreal shows Maya's world pose")),
            MaxWorld < WorldTolerance);
        const TArray<TSharedPtr<FJsonValue>>* Keyed = nullptr;
        if (Fixture->GetObjectField(TEXT("maya"))->TryGetArrayField(TEXT("keyed_morphs"), Keyed) && Keyed->Num() > 0)
        {
            Test->TestTrue(*(Name + TEXT(": a display shows the keyed Morphs")), Morphs > 0);
            Test->TestTrue(*(Name + TEXT(": the oracle Morph weights are non-zero")), MaxExpected > 0.05);
            Test->TestTrue(*(Name + (bMorphs ? TEXT(": the displays show Maya's Morph weights")
                                            : TEXT(": the displays clear Morph weights while BS is disabled"))),
                MaxMorph < MorphTolerance);
        }
        return true;
    }

    /** The Details at the fixture widths and the Maya panel at one stage. */
    void AddShots(const FString& State)
    {
        Add(State + TEXT(": Details and Maya panel"), 60.0, [this, State]()
        {
            if (!SendOnce(TEXT("panel_shot"), Field(TEXT("name"), MakeShared<FJsonValueString>(State))))
            {
                return false;
            }
            if (DetailsWidths && DetailsWidths->Num() > 0)
            {
                MtoUGuiAcceptance::RenderDetails(*Test, *Actor, *DetailsWidths,
                    Fixture->HasField(TEXT("details_height")) ? Fixture->GetNumberField(TEXT("details_height")) : 1600.0,
                    EvidenceDir, State, [this](const FString& Line) { Log(Line); });
            }
            return true;
        });
    }

    bool BuildScene()
    {
        UWorld* World = GEditor->GetEditorWorldContext().World();
        // Never replace a user's map: only the engine startup map or an
        // unsaved /Temp world is exchanged for the test's own world.
        const FString Package = World ? World->GetOutermost()->GetName() : FString();
        if (!Package.StartsWith(TEXT("/Engine/")) && !Package.StartsWith(TEXT("/Temp/")))
        {
            return Abort(TEXT("Run the GUI workflow test in an editor launched on /Engine/Maps/Entry, not on ") + Package);
        }
        UMtoULiveLinkBinding* Original = LoadObject<UMtoULiveLinkBinding>(nullptr, *Fixture->GetStringField(TEXT("binding")));
        if (!Original)
        {
            return Abort(TEXT("Fixture Binding did not load"));
        }
        // Every override stays on this transient duplicate.
        Binding.Reset(DuplicateObject<UMtoULiveLinkBinding>(Original, GetTransientPackage()));
        Binding->SkeletalMesh = LoadObject<USkeletalMesh>(nullptr, *Fixture->GetStringField(TEXT("primary_mesh")));
        Binding->PreviewStaticMesh = LoadObject<UStaticMesh>(nullptr, *Fixture->GetStringField(TEXT("preview_mesh")));
        Binding->DriverGarmentSlotOverride.Reset();
        for (const TSharedPtr<FJsonValue>& Slot : Fixture->GetArrayField(TEXT("driver_garment_slots")))
        {
            Binding->DriverGarmentSlotOverride.AddUnique(FName(*Slot->AsString()));
        }
        Binding->AdditionalParts.Reset();
        for (const TSharedPtr<FJsonValue>& Value : Fixture->GetArrayField(TEXT("parts")))
        {
            FMtoUCharacterPart& Part = Binding->AdditionalParts.AddDefaulted_GetRef();
            Part.PartName = Value->AsObject()->GetStringField(TEXT("name"));
            Part.SkeletalMesh = LoadObject<USkeletalMesh>(nullptr, *Value->AsObject()->GetStringField(TEXT("mesh")));
            Part.bEnabled = true;
            if (!Part.SkeletalMesh)
            {
                return Abort(TEXT("Fixture part did not load"));
            }
        }
        Binding->EnsureCharacterPartIds();
        Binding->SetFlags(RF_Transactional);
        if (!Binding->SkeletalMesh || !Binding->PreviewStaticMesh)
        {
            return Abort(TEXT("Fixture Primary Driver or Preview Static Mesh did not load"));
        }
        return SpawnTarget(GEditor->NewMap());
    }

    bool SpawnTarget(UWorld* World)
    {
        if (!World)
        {
            return Abort(TEXT("The fixture's unsaved world did not open"));
        }
        FActorSpawnParameters Params;
        Params.ObjectFlags |= RF_Transient;
        Actor = World->SpawnActor<AMtoULiveLinkActor>(FVector::ZeroVector, FRotator::ZeroRotator, Params);
        if (!Actor.IsValid())
        {
            return Abort(TEXT("The fixture Binding Actor did not spawn"));
        }
        Actor->SetActorLabel(TEXT("MtoU GUI Workflow (unsaved)"));
        Actor->SetBinding(Binding.Get());
        return true;
    }

    /** Ends the run after a setup failure instead of retrying the step. */
    bool Abort(const FString& Reason)
    {
        Test->AddError(Reason);
        bAborted = true;
        return true;
    }

    void Finish()
    {
        if (MayaResult.IsValid())
        {
            MtoUGuiAcceptance::CheckMayaDialogs(*Test, Driver.Poll(), ExpectedDialogs(),
                [this](const FString& Line) { Log(Line); });
            Log(Driver.DescribePreferences(TEXT("playback")));
            WriteJsonFile(FPaths::Combine(EvidenceDir, TEXT("maya-final.json")), MayaResult.ToSharedRef());
        }
        if (Reconnect)
        {
            TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
            Evidence->SetArrayField(TEXT("unreal"), UnrealHistory);
            WriteJsonFile(FPaths::Combine(EvidenceDir, TEXT("reconnect-events.json")), Evidence);
        }
        if (!bKeepMaya)
        {
            Driver.Terminate();
        }
    }

    FAutomationTestBase* Test;
    FString FixturePath, EvidenceDir;
    TSharedPtr<FJsonObject> Fixture, MayaResult, Reconnect;
    FString MonitoredStage, LastUnrealState;
    int32 OldSession = 0, OldAttempts = 0;
    bool bUnrealReconnecting = false;
    TArray<TSharedPtr<FJsonValue>> UnrealHistory;
    TMap<FName, FOracleRotation> FirstOraclePose;
    TArray<FString> OldCapturePaths;
    const TArray<TSharedPtr<FJsonValue>>* DetailsWidths = nullptr;
    TArray<FStep> Steps;
    int32 Step = 0, Pending = -1, ReportedErrors = 0, PoseIndex = 0;
    double StepStarted = 0.0, SettleUntil = 0.0;
    bool bStarted = false;
    bool bAborted = false;
    bool bKeepMaya = false;
    MtoUGuiAcceptance::FMayaGuiDriver Driver;
    TStrongObjectPtr<UMtoULiveLinkBinding> Binding;
    TWeakObjectPtr<AMtoULiveLinkActor> Actor;
};
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FMtoUGuiWorkflowTest,
    "MtoUOptIn.Editor.Workflow.GuiRegression",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FMtoUGuiWorkflowTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FString Fixture;
    if (!FParse::Value(FCommandLine::Get(), TEXT("MtoUGuiWorkflowFixture="), Fixture))
    {
        AddError(TEXT("GUI workflow regression requires /Engine/Maps/Entry and -MtoUGuiWorkflowFixture=<fixture.json>."));
        return false;
    }
    FAutomationTestFramework::Get().EnqueueLatentCommand(MakeShared<FGuiWorkflowCommand>(this, Fixture));
    return true;
}
#endif
