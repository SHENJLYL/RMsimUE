#pragma once

#include "CoreMinimal.h"

class FJsonObject;

enum class EMujocoTimingScope : uint8
{
    Step, Solver, GetBody, Jacobian, PreImpact, Damping, Contacts, DerivedHistory,
    FinalForward, Sync, ContactIdentity, Count
};

// All published quantities use UE coordinates/centimetres. The owned MuJoCo model
// uses ROS right-handed SI coordinates. Readers consume an immutable frame in dedicated mode.
struct FMujocoBodyState
{
    FTransform World = FTransform::Identity;
    FVector VelocityCmS = FVector::ZeroVector;
    FVector AngularVelocityRadS = FVector::ZeroVector;
    FVector CentreOfMassCm = FVector::ZeroVector;
    FVector PrincipalInertiaKgCm2 = FVector::ZeroVector;
    double MassKg = 0;
};

struct FMujocoContactSample
{
    FString BodyName;
    FString OtherName;
    FString OtherBodyName;
    FVector PointCm = FVector::ZeroVector;
    FVector IntoObstacleNormal = FVector::ZeroVector;
    FVector PreImpactVelocityCmS = FVector::ZeroVector;
    double ImpulseNs = 0;
    double ForceN = 0;
    double TimeSeconds = 0;
    int32 OtherGeom = INDEX_NONE;
    int32 OtherFlex = INDEX_NONE;
    int32 OtherVehicleSlot = INDEX_NONE;
};

/** A single authoritative native MuJoCo model; never advances Chaos bodies. */
class FMujocoVehicleDynamics
{
public:
    FMujocoVehicleDynamics();
    ~FMujocoVehicleDynamics();
    bool Initialize(const FString& Filename, const TArray<FString>& RequiredBodies,
        const FString& RigFilename, const FString& DriveProfileFilename, FString& Error, bool bMatchWorld = false);
    bool AttachOpponent(const TSharedPtr<FMujocoVehicleDynamics>& World, const TArray<FString>& RequiredBodies,
        const FString& RigFilename, const FString& DriveProfileFilename, FString& Error);
    bool Reset(const FTransform& ChassisPose, FString& Error, double InitialYaw = 0, double InitialPitch = 0);
    bool Step(double Seconds, FString& Error);
    bool StartDedicated(double PeriodSeconds, FString& Error);
    void StopDedicated();
    bool IsDedicated() const { return World().Dedicated.IsValid(); }
    bool IsMatchWorld() const { return World().bDualRig; }
    void SetDedicatedPaused(bool Paused);
    void SubmitDedicatedControls(double DeadlineWallSeconds, double OpponentDeadlineWallSeconds = 0);
    bool ConsumeDedicatedFrame(bool& Updated, FString& Error);
    bool ReadDedicatedProgress(uint64& Steps, uint64& Failsafes) const;
    void VisitDedicatedQueryFrames(TFunctionRef<void(double,const TArray<FTransform>&,bool)> Visitor);
    void ClearDedicatedQueryFrames();
    bool ConfigureServo(const FString& Part, double Stiffness, double Damping, double ForceLimit, FString& Error);
    bool SetServoTarget(const FString& Part, double Position, double Velocity, bool Wrap);
    bool SetServoTrajectory(const FString& Part, double Position, double Velocity,
        bool Wrap, double AtSolverTime, double ExpiresWall);
    bool SetTorque(const FString& Part, double Torque);
    bool SetForceLimit(const FString& Part, double ForceLimit);
    bool SetRootExternalWrench(const FVector& ForceWorldRosN, const FVector& TorqueWorldRosNm);
    bool ConfigureBodyDamping(const FString& Part, double LinearPerSecond, double AngularPerSecond, FString& Error);
    const FString& RootBody() const { return RootBodyName; }
    const FString& YawBody() const { return YawBodyName; }
    const FString& PitchBody() const { return PitchBodyName; }
    bool GetJoint(const FString& Part, double& Position, double& Velocity) const;
    bool GetBody(const FString& Part, FMujocoBodyState& Out) const;
    // Absolute native solver seconds; UE world centimetres. GT only, no extrapolation.
    bool SampleSensorParentWorldAtTime(const FString& ParentFrame, double SolverTime, FTransform& OutPose) const;
    bool SampleSensorMotionAtTime(const FString& ParentFrame, double SolverTime,
        const FTransform& LocalMount, FTransform& OutPose, FVector& OutPointVelocityCmS,
        FVector& OutAngularVelocityRadS) const;
    bool GetSensorPoseHistoryRange(double& OldestSolverTime, double& NewestSolverTime, int32& SampleCount) const;
    bool IsTimingEnabled() const { return World().bTimingEnabled; }
    void RecordTiming(EMujocoTimingScope Scope, double WallSeconds) const;
    void FlushTiming();
    const TArray<FString>& BodyNames() const { return OrderedBodyNames; }
    const TArray<FMujocoContactSample>& Contacts() const;
    const TSharedPtr<FJsonObject>& Manifest() const { return ManifestObject; }
    int32 WarningCount() const;
    double Time() const;
    double Timestep() const;
    FVector GravityCmS2() const;
    uint64 Steps() const;
    TSharedRef<FJsonObject> Diagnostics() const;

private:
    struct FImplementation;
    struct FDedicated;
    TUniquePtr<FImplementation> Impl;
    TUniquePtr<FDedicated> Dedicated;
    TSharedPtr<FMujocoVehicleDynamics> SharedWorld;
    FMujocoVehicleDynamics& World() { return SharedWorld ? *SharedWorld : *this; }
    const FMujocoVehicleDynamics& World() const { return SharedWorld ? *SharedWorld : *this; }
    int32 VehicleSlot() const { return SharedWorld ? 1 : 0; }
    TSharedPtr<FJsonObject> ManifestObject;
    TArray<FString> OrderedBodyNames;
    TArray<FMujocoContactSample> FrameContacts[2];
    FString ModelPath;
    FString ModelMd5;
    FString SourceRigPath;
    FString SourceDriveProfilePath;
    FString RootBodyName = TEXT("body");
    FString YawBodyName = TEXT("yaw");
    FString PitchBodyName = TEXT("pitch");
    int32 VerifiedRuntimeBindings = 0;
    bool bPhysicsIdentityVerified = false;
    double RemainderSeconds = 0;
    uint64 TotalSteps = 0;
    uint64 ResetCount[2] = {};
    int32 LastFrameSteps = 0;
    double MaximumPenetrationM[2] = {};
    bool bDualRig = false;
    bool ValidateState(FString& Error) const;
    void CollectContacts(const TArray<FVector>& BeforeVelocities);
    bool CaptureSensorHistory(FString& Error, int32 Slot);
    bool FindSensorHistoryBracket(double SolverTime, int32& Lower, int32& Upper, double& Alpha) const;
    void ApplyBodyDamping(int32 Body, const FMujocoBodyState& State);
    bool PrepareBodyDamping(FString& Error);
    bool bTimingEnabled = false;
};

// Disabled by default. Nested scopes report elapsed wall time, including waits.
class FScopedMujocoTiming
{
public:
    FScopedMujocoTiming(const FMujocoVehicleDynamics& InOwner, EMujocoTimingScope InScope);
    ~FScopedMujocoTiming();
private:
    const FMujocoVehicleDynamics& Owner;
    EMujocoTimingScope Scope;
    double StartedSeconds = -1;
};
