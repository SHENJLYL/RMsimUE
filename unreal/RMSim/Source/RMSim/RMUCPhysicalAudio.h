#pragma once

#include "Components/ActorComponent.h"
#include "Sound/SoundWaveProcedural.h"
#include <atomic>
#include "RMUCPhysicalAudio.generated.h"

class AVehicleRig;
class UAudioComponent;
class UWorld;
struct FHitResult;

UCLASS()
class URMUCMechanicalWave : public USoundWaveProcedural
{
    GENERATED_BODY()

public:
    void SetRecordings(const TArray<int16>& Motor, const TArray<int16>& Rolling, const TArray<int16>& Flywheel);
    void SetMotion(float SpeedMps, float YawRadS, bool bWheelGrounded);
    void SetFlywheelsRequested(bool bRequested);
    virtual int32 OnGeneratePCMAudio(TArray<uint8>& OutAudio, int32 NumSamples) override;

private:
    std::atomic<float> Drive{0.0f};
    std::atomic<float> Gyro{0.0f};
    std::atomic<bool> WheelGrounded{false};
    std::atomic<bool> FlywheelsRequested{false};
    // Recordings are assigned before the emitter starts, then only read by the audio thread.
    struct FLoop
    {
        TArray<int16> Pcm;
        float Phase = 0.0f, PlaybackRate = 1.0f;
        float Level = 0.0f;
        float Read(float Rate, float Target, float Blend);
    };
    FLoop MotorLoop, RollingLoop, FlywheelLoop;
};

UCLASS()
class URMUCPhysicalAudio : public UActorComponent
{
    GENERATED_BODY()

public:
    static void AttachTo(AVehicleRig* Rig);
    static void SetFlywheelsRequested(AVehicleRig* Rig, bool bRequested);
    static void PlayFeeder(AVehicleRig* Rig, const FVector& Location);
    static void PlayImpact(UWorld* World, const FHitResult& Hit, double NormalSpeedCmS, bool bArmorHit);

    URMUCPhysicalAudio();
    virtual void TickComponent(float DeltaTime, ELevelTick TickType,
                               FActorComponentTickFunction* ThisTickFunction) override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
    bool PlayVehicleContacts(const AVehicleRig& Rig);
    UPROPERTY() TObjectPtr<URMUCMechanicalWave> MechanicalWave;
    UPROPERTY() TObjectPtr<UAudioComponent> MechanicalEmitter;
    uint64 MotionGeneration = 0;
    uint64 ChaosContactsCursor = 0;
    double LastNativeContactSeconds = -1.0;
    double LastWheelContactSeconds = -1.0;
    double LastWallContactSeconds = -1.0;
    double LastGroundContactSeconds = -1.0;
    double LastVehicleSoundSeconds = -1.0;
};
