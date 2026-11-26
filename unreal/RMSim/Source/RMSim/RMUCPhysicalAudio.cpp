#include "RMUCPhysicalAudio.h"

#include "Audio.h"
#include "Components/AudioComponent.h"
#include "Components/PrimitiveComponent.h"
#include "Engine/HitResult.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "MujocoVehicleDynamics.h"
#include "PhysicalMaterials/PhysicalMaterial.h"
#include "Sound/SoundAttenuation.h"
#include "VehicleRig.h"

namespace
{
constexpr int32 SampleRate = 24000;
constexpr float SampleTime = 1.0f / SampleRate;
enum class ERecording : uint8 { Motor, Rolling, Flywheel, Feeder, Scene, Metal, Glass, Wall, Landing, Count };
constexpr const TCHAR* RecordingNames[] = {TEXT("motor"), TEXT("wheel_ground"), TEXT("flywheel"),
    TEXT("feeder"), TEXT("surface_impact"), TEXT("metal_impact"), TEXT("glass_impact"),
    TEXT("vehicle_wall"), TEXT("vehicle_landing")};
constexpr int32 RecordingCount = static_cast<int32>(ERecording::Count);
static_assert(UE_ARRAY_COUNT(RecordingNames) == RecordingCount);

struct FRecordingBank
{
    TArray<int16> Clips[RecordingCount];
    int32 Loaded = 0;

    FRecordingBank()
    {
        const FString Folder = FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::ProjectDir(),
            TEXT("../../assets/audio/rmuc2027")));
        for (int32 I = 0; I < RecordingCount; ++I)
        {
            const FString Path = FPaths::Combine(Folder, FString(RecordingNames[I]) + TEXT(".wav"));
            const int64 Size = IFileManager::Get().FileSize(*Path);
            FWaveModInfo Wave;
            TArray<uint8> Bytes;
            if (Size < 44 || Size > 1024 * 1024 || !FFileHelper::LoadFileToArray(Bytes, *Path) ||
                !Wave.ReadWaveInfo(Bytes.GetData(), Bytes.Num()) || *Wave.pFormatTag != FWaveModInfo::WAVE_INFO_FORMAT_PCM ||
                *Wave.pChannels != 1 || *Wave.pBitsPerSample != 16 || *Wave.pSamplesPerSec != SampleRate ||
                Wave.SampleDataSize < 64 || Wave.SampleDataSize % sizeof(int16) != 0)
            {
                UE_LOG(LogRMSim, Warning, TEXT("RMUC_AUDIO_RECORDING_UNAVAILABLE %s"), *Path);
                continue;
            }
            Clips[I].SetNumUninitialized(Wave.SampleDataSize / sizeof(int16));
            FMemory::Memcpy(Clips[I].GetData(), Wave.SampleDataStart, Wave.SampleDataSize);
            ++Loaded;
        }
    }

    const TArray<int16>& Get(ERecording Cue) const { return Clips[static_cast<int32>(Cue)]; }
};

const FRecordingBank& Recordings()
{
    static const FRecordingBank Bank;
    return Bank;
}

bool HasAudio(const UWorld* World)
{
    return World && FApp::CanEverRenderAudio() && World->AllowAudioPlayback() &&
        !World->IsNetMode(NM_DedicatedServer) && World->GetAudioDeviceRaw();
}

bool TraceAudio()
{
    static const bool bTrace = FParse::Param(FCommandLine::Get(), TEXT("RefereePhysicalAudioTrace"));
    return bTrace;
}

FSoundAttenuationSettings Attenuation(float NearCm, float FalloffCm)
{
    FSoundAttenuationSettings Settings;
    Settings.bAttenuate = true;
    Settings.bSpatialize = true;
    Settings.DistanceAlgorithm = EAttenuationDistanceModel::Logarithmic;
    Settings.AttenuationShapeExtents = FVector(NearCm, 0.0, 0.0);
    Settings.FalloffDistance = FalloffCm;
    return Settings;
}

bool PlayRecording(UWorld* World, const FVector& Position, ERecording Cue, float Strength, bool bFixedPitch = false)
{
    if (!HasAudio(World)) return false;
    const auto& Clip = Recordings().Get(Cue);
    if (Clip.IsEmpty()) return false;
    auto* Wave = NewObject<USoundWaveProcedural>(World);
    Wave->SetSampleRate(SampleRate);
    Wave->NumChannels = 1;
    Wave->Duration = float(Clip.Num()) / SampleRate;
    Wave->QueueAudio(reinterpret_cast<const uint8*>(Clip.GetData()), Clip.Num() * sizeof(int16));
    auto* Spatial = NewObject<USoundAttenuation>(Wave);
    const bool bFeeder = Cue == ERecording::Feeder;
    Spatial->Attenuation = Attenuation(bFeeder ? 110.0f : 155.0f, bFeeder ? 850.0f : 1550.0f);
    Wave->AttenuationSettings = Spatial;
    const bool bVehicle = Cue == ERecording::Wall || Cue == ERecording::Landing;
    const float Pitch = bFixedPitch || bVehicle ? 1.0f : FMath::FRandRange(.95f, 1.05f);
    if (UAudioComponent* Emitter = UGameplayStatics::SpawnSoundAtLocation(World, Wave, Position,
        FRotator::ZeroRotator, Strength, Pitch, 0.0f, Spatial))
    {
        Emitter->StopDelayed(Wave->Duration / Pitch + .06f);
        return true;
    }
    return false;
}

struct FImpactCue
{
    FVector Point = FVector::ZeroVector;
    double Score = 0.0;
    double Impulse = 0.0;
    double NormalSpeed = 0.0;
};

void SelectImpact(FImpactCue& Wall, FImpactCue& Ground, const FVector& Point,
    const FVector& IntoObstacle, const FVector& PreImpactVelocity, double Impulse)
{
    if (Point.ContainsNaN() || IntoObstacle.ContainsNaN() || PreImpactVelocity.ContainsNaN() ||
        !FMath::IsFinite(Impulse)) return;
    const bool bGround = IntoObstacle.Z <= -.60;
    const double NormalSpeed = FVector::DotProduct(PreImpactVelocity, IntoObstacle);
    if (NormalSpeed < (bGround ? 30. : 16.) || Impulse < (bGround ? .11 : .15)) return;
    FImpactCue& Cue = bGround ? Ground : Wall;
    const double Score = .01 * Impulse * NormalSpeed;
    if (Score < (bGround ? .14 : .10)) return;
    if (Score > Cue.Score) Cue = {Point, Score, Impulse, NormalSpeed};
}

ERecording VehicleContactCue(const FImpactCue& Wall, const FImpactCue& Ground, bool bWallPressure, double Now,
    double& LastWall, double& LastGround, double LastPlayback)
{
    const bool bWallEpisode = LastWall >= 0. && Now - LastWall < .16;
    const bool bWallOnset = Wall.Score > 0. && !bWallEpisode;
    const bool bGroundOnset = Ground.Score > 0. && Now - LastGround >= .10;
    if (Wall.Score > 0. || (bWallEpisode && bWallPressure)) LastWall = Now;
    if (Ground.Score > 0.) LastGround = Now;
    if (Now < .18) return ERecording::Count;
    // One dominant cue for a chassis impact, even if the same impulse shakes the wheels.
    if (bWallOnset && Now - LastPlayback >= .10) return ERecording::Wall;
    if (bGroundOnset && Now - LastWall >= .20 && Now - LastPlayback >= .23) return ERecording::Landing;
    return ERecording::Count;
}

#if WITH_DEV_AUTOMATION_TESTS
bool CheckMechanicalPcm(UObject* Outer)
{
    const FRecordingBank& Bank = Recordings();
    if (Bank.Loaded != RecordingCount) return false;
    for (int32 Layer = 0; Layer < 4; ++Layer)
    {
        auto* Probe = NewObject<URMUCMechanicalWave>(Outer);
        Probe->SetRecordings(Bank.Get(ERecording::Motor), Bank.Get(ERecording::Rolling), Bank.Get(ERecording::Flywheel));
        Probe->SetMotion(Layer == 1 ? 2.2f : 0.0f, Layer == 2 ? 4.8f : 0.0f, Layer == 1);
        Probe->SetFlywheelsRequested(Layer == 3);
        TArray<uint8> Audio;
        constexpr int32 Count = 8192;
        if (Probe->OnGeneratePCMAudio(Audio, Count) != Count || Audio.Num() != Count * sizeof(int16)) return false;
        const auto* Data = reinterpret_cast<const int16*>(Audio.GetData());
        int32 Peak = 0;
        double SumSquares = 0.;
        for (int32 Index = 0; Index < Count; ++Index)
        {
            Peak = FMath::Max(Peak, FMath::Abs(static_cast<int32>(Data[Index])));
            SumSquares += FMath::Square(double(Data[Index]) / 32768.);
        }
        const double Rms = FMath::Sqrt(SumSquares / Count);
        UE_LOG(LogRMSim, Display, TEXT("RMUC_VEHICLE_AUDIO_MIX_TEST layer=%d peak_pcm=%d rms=%.5f"), Layer, Peak, Rms);
        if ((Layer == 0 ? Peak != 0 : Peak < 30) || Rms > .18) return false;
    }
    FImpactCue Wall, Ground;
    SelectImpact(Wall, Ground, FVector::ZeroVector, FVector::ForwardVector, FVector(100., 0., 0.), 1.);
    SelectImpact(Wall, Ground, FVector::ZeroVector, -FVector::UpVector, FVector(100., 0., -60.), .5);
    SelectImpact(Wall, Ground, FVector::ZeroVector, -FVector::UpVector, FVector(100., 0., -26.), 15.);
    double LastWall = -1., LastGround = -1.;
    const bool bSingleLanding = VehicleContactCue({}, Ground, false, 1., LastWall, LastGround, -1.) == ERecording::Landing;
    const bool bWallDominates = VehicleContactCue(Wall, Ground, true, 2., LastWall, LastGround, 1.) == ERecording::Wall;
    const bool bSustainedSuppressed =
        VehicleContactCue({}, Ground, true, 2.12, LastWall, LastGround, 2.) == ERecording::Count &&
        VehicleContactCue(Wall, Ground, true, 2.25, LastWall, LastGround, 2.) == ERecording::Count &&
        VehicleContactCue({}, Ground, false, 2.39, LastWall, LastGround, 2.) == ERecording::Count;
    const bool bCanLandAgain = VehicleContactCue({}, Ground, false, 2.53, LastWall, LastGround, 2.) == ERecording::Landing;
    return Wall.NormalSpeed == 100. && Ground.NormalSpeed == 60. &&
        bSingleLanding && bWallDominates && bSustainedSuppressed && bCanLandAgain;
}
#endif
}

void URMUCMechanicalWave::SetRecordings(const TArray<int16>& Motor, const TArray<int16>& Rolling, const TArray<int16>& Flywheel)
{
    MotorLoop.Pcm = Motor;
    RollingLoop.Pcm = Rolling;
    FlywheelLoop.Pcm = Flywheel;
}

void URMUCMechanicalWave::SetMotion(float SpeedMps, float YawRadS, bool bWheelGrounded)
{
    Drive.store(FMath::Clamp((SpeedMps - .12f) / 4.38f, 0.0f, 1.0f), std::memory_order_relaxed);
    Gyro.store(FMath::Clamp((FMath::Abs(YawRadS) - .18f) / 4.82f, 0.0f, 1.0f), std::memory_order_relaxed);
    WheelGrounded.store(bWheelGrounded, std::memory_order_relaxed);
}

void URMUCMechanicalWave::SetFlywheelsRequested(bool bRequested)
{
    FlywheelsRequested.store(bRequested, std::memory_order_relaxed);
}

float URMUCMechanicalWave::FLoop::Read(float Rate, float Target, float Blend)
{
    if (Pcm.IsEmpty()) return 0.0f;
    Level = FMath::Lerp(Level, Target, Blend);
    PlaybackRate = FMath::Lerp(PlaybackRate, Rate, SampleTime / .16f);
    const int32 Index = static_cast<int32>(Phase);
    const int32 Next = Index + 1 == Pcm.Num() ? 0 : Index + 1;
    const float Value = FMath::Lerp(float(Pcm[Index]), float(Pcm[Next]), Phase - Index) * Level;
    Phase += PlaybackRate;
    if (Phase >= Pcm.Num()) Phase -= Pcm.Num();
    return Value;
}

int32 URMUCMechanicalWave::OnGeneratePCMAudio(TArray<uint8>& OutAudio, int32 NumSamples)
{
    OutAudio.SetNumUninitialized(NumSamples * sizeof(int16));
    auto* Output = reinterpret_cast<int16*>(OutAudio.GetData());
    const float Linear = Drive.load(std::memory_order_relaxed);
    const float Turning = Gyro.load(std::memory_order_relaxed);
    const float Motor = FMath::Max(Linear, .44f * Turning);
    const bool bGrounded = WheelGrounded.load(std::memory_order_relaxed);
    const float Rolling = bGrounded ? FMath::Max(Linear, .20f * Turning) : 0.0f;
    const bool bFlywheels = FlywheelsRequested.load(std::memory_order_relaxed);
    for (int32 Index = 0; Index < NumSamples; ++Index)
    {
        const float MotorPcm = MotorLoop.Read(.94f + .12f * Motor, .40f * Motor, SampleTime / .12f);
        const float RoadPcm = RollingLoop.Read(.92f + .17f * Rolling, .45f * Rolling, SampleTime / .15f);
        const float FlywheelPcm = FlywheelLoop.Read(.90f + .50f * FlywheelLoop.Level, bFlywheels ? .20f : 0.f,
            SampleTime / (bFlywheels ? .22f : .30f));
        Output[Index] = static_cast<int16>(FMath::Clamp(MotorPcm + RoadPcm + FlywheelPcm, -32767.f, 32767.f));
    }
    return NumSamples;
}

URMUCPhysicalAudio::URMUCPhysicalAudio()
{
    PrimaryComponentTick.bCanEverTick = true;
    PrimaryComponentTick.TickGroup = TG_PostPhysics;
}

void URMUCPhysicalAudio::AttachTo(AVehicleRig* Rig)
{
    if (!IsValid(Rig) || !Rig->GetRootComponent()) return;
#if WITH_DEV_AUTOMATION_TESTS
    if (FParse::Param(FCommandLine::Get(), TEXT("RefereePhysicalAudioSelfTest")))
    {
        UE_LOG(LogRMSim, Display, TEXT("RMUC_PHYSICAL_AUDIO_PCM_TEST status=%s source=recorded layers=idle,drive,gyro,flywheels contacts=classified"),
            CheckMechanicalPcm(Rig) ? TEXT("passed") : TEXT("failed"));
    }
#endif
    if (!HasAudio(Rig->GetWorld())) return;
    const auto& Bank = Recordings();
    auto* Component = NewObject<URMUCPhysicalAudio>(Rig);
    auto* Wave = NewObject<URMUCMechanicalWave>(Component);
    Wave->SetSampleRate(SampleRate);
    Wave->NumChannels = 1;
    Wave->Duration = INDEFINITELY_LOOPING_DURATION;
    Wave->bLooping = true;
    Wave->VirtualizationMode = EVirtualizationMode::PlayWhenSilent;
    Wave->SetRecordings(Bank.Get(ERecording::Motor), Bank.Get(ERecording::Rolling), Bank.Get(ERecording::Flywheel));
    auto* Spatial = NewObject<USoundAttenuation>(Wave);
    Spatial->Attenuation = Attenuation(160.0f, 1300.0f);
    Wave->AttenuationSettings = Spatial;
    Component->MechanicalEmitter = UGameplayStatics::SpawnSoundAttached(Wave, Rig->GetRootComponent(),
        NAME_None, FVector::ZeroVector, EAttachLocation::KeepRelativeOffset,
        true, .52f, 1.0f, 0.0f, Spatial, nullptr, false);
    Component->MechanicalWave = Wave;
    Rig->AddInstanceComponent(Component);
    Component->RegisterComponent();
    UE_LOG(LogRMSim, Display, TEXT("RMUC_PHYSICAL_AUDIO_READY recordings=%d/%d source=recorded sample_hz=%d"),
        Bank.Loaded, RecordingCount, SampleRate);
}

void URMUCPhysicalAudio::SetFlywheelsRequested(AVehicleRig* Rig, bool bRequested)
{
    if (!IsValid(Rig)) return;
    if (auto* Component = Rig->FindComponentByClass<URMUCPhysicalAudio>())
        Component->MechanicalWave->SetFlywheelsRequested(bRequested);
}

void URMUCPhysicalAudio::PlayFeeder(AVehicleRig* Rig, const FVector& Location)
{
    if (IsValid(Rig) && PlayRecording(Rig->GetWorld(), Location, ERecording::Feeder, .55f) && TraceAudio())
        UE_LOG(LogRMSim, Display, TEXT("RMUC_PHYSICAL_AUDIO_FEEDER source=recorded"));
}

void URMUCPhysicalAudio::PlayImpact(UWorld* World, const FHitResult& Hit, double NormalSpeedCmS, bool bArmorHit)
{
    if (!FMath::IsFinite(NormalSpeedCmS) || NormalSpeedCmS < 35.0) return;
    if (bArmorHit)
    {
        if (PlayRecording(World, Hit.ImpactPoint, ERecording::Metal, .62f, true) && TraceAudio())
            UE_LOG(LogRMSim, Display, TEXT("RMUC_PHYSICAL_AUDIO_IMPACT sound=armor clip=metal_impact component=%s source=recorded"),
                *GetNameSafe(Hit.GetComponent()));
        return;
    }
    const auto* Component = Hit.GetComponent();
    const FString Material = Hit.PhysMaterial.IsValid() ? Hit.PhysMaterial->GetName() : FString();
    const bool bGlass = (Component && Component->ComponentHasTag(TEXT("projectile_glass"))) || Material.Contains(TEXT("Glass"));
    const bool bMetal = Cast<AVehicleRig>(Hit.GetActor()) ||
        (Component && Component->ComponentHasTag(TEXT("projectile_metal"))) || Material.Contains(TEXT("Metal"));
    const ERecording Cue = bGlass ? ERecording::Glass : bMetal ? ERecording::Metal : ERecording::Scene;
    const float Gain = static_cast<float>(FMath::Clamp(.07 + .55 * NormalSpeedCmS / 2400.0, .07, .62));
    if (PlayRecording(World, Hit.ImpactPoint, Cue, Gain) && TraceAudio())
        UE_LOG(LogRMSim, Display, TEXT("RMUC_PHYSICAL_AUDIO_IMPACT normal_cm_s=%.2f sound=%s source=recorded"),
            NormalSpeedCmS, bGlass ? TEXT("glass") : bMetal ? TEXT("metal") : TEXT("scene"));
}

bool URMUCPhysicalAudio::PlayVehicleContacts(const AVehicleRig& Rig)
{
    if (MotionGeneration != Rig.GetMotionGeneration())
    {
        MotionGeneration = Rig.GetMotionGeneration();
        LastNativeContactSeconds = LastWheelContactSeconds = LastWallContactSeconds = LastGroundContactSeconds =
            LastVehicleSoundSeconds = -1.;
    }
    const double Now = Rig.GetSimulationTimeSeconds();
    FImpactCue Wall, Ground;
    bool bWallPressure = false;
    const auto Observe = [&](const auto& Hit)
    {
        bWallPressure |= FMath::Abs(Hit.IntoObstacleNormal.Z) < .60 && Hit.ImpulseNs >= .025;
        SelectImpact(Wall, Ground, Hit.PointCm, Hit.IntoObstacleNormal, Hit.PreImpactVelocityCmS, Hit.ImpulseNs);
    };
    if (const auto* Contacts = Rig.GetNativeContactFrame())
    {
        const double PreviousFrame = LastNativeContactSeconds;
        for (const FMujocoContactSample& Hit : *Contacts)
        {
            if (Hit.TimeSeconds <= PreviousFrame) continue;
            LastNativeContactSeconds = FMath::Max(LastNativeContactSeconds, Hit.TimeSeconds);
            if (Hit.IntoObstacleNormal.Z <= -.60 && Hit.BodyName.Contains(TEXT("wheel")))
                LastWheelContactSeconds = FMath::Max(LastWheelContactSeconds, Hit.TimeSeconds);
            Observe(Hit);
        }
    }
    else
    {
        TArray<FVehicleObstacleContact> FrameContacts;
        Rig.GetObstacleContactsSince(ChaosContactsCursor, FrameContacts);
        for (const auto& Hit : FrameContacts)
        {
            if (Hit.IntoObstacleNormal.Z <= -.60) LastWheelContactSeconds = Now;
            Observe(Hit);
        }
    }

    const ERecording Cue = VehicleContactCue(Wall, Ground, bWallPressure, Now, LastWallContactSeconds, LastGroundContactSeconds,
        LastVehicleSoundSeconds);
    if (Cue != ERecording::Count)
    {
        const bool bWall = Cue == ERecording::Wall;
        const FImpactCue& Hit = bWall ? Wall : Ground;
        const double Reference = bWall ? 3.5 : .85;
        const float Gain = float((bWall ? .74 : .53) * FMath::Sqrt(Hit.Score / (Hit.Score + Reference)));
        if (PlayRecording(GetWorld(), Hit.Point, Cue, Gain))
        {
            LastVehicleSoundSeconds = Now;
            if (TraceAudio())
                UE_LOG(LogRMSim, Display, TEXT("RMUC_VEHICLE_CONTACT_AUDIO surface=%s impulse_ns=%.3f normal_cm_s=%.2f source=recorded sim_s=%.3f gain=%.3f"),
                    bWall ? TEXT("wall") : TEXT("ground"), Hit.Impulse, Hit.NormalSpeed, Now, Gain);
        }
    }
    return Now - LastWheelContactSeconds <= .075;
}

void URMUCPhysicalAudio::TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
    Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
    const auto* Rig = Cast<AVehicleRig>(GetOwner());
    if (!IsValid(Rig) || !MechanicalWave) return;
    const double Speed = Rig->GetCommandOriginVelocity().Size2D() / 100.0;
    const double Yaw = Rig->GetMeasuredYawRate();
    const bool bGrounded = PlayVehicleContacts(*Rig);
    MechanicalWave->SetMotion(FMath::IsFinite(Speed) ? float(Speed) : 0.f, FMath::IsFinite(Yaw) ? float(Yaw) : 0.f, bGrounded);
}

void URMUCPhysicalAudio::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    if (IsValid(MechanicalEmitter))
    {
        MechanicalEmitter->Stop();
        MechanicalEmitter->DestroyComponent();
    }
    Super::EndPlay(EndPlayReason);
}
