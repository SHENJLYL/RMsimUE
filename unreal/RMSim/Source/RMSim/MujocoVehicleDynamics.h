#pragma once

#include "CoreMinimal.h"

class FJsonObject;

// All published quantities use UE coordinates/centimetres. The owned MuJoCo model
// uses ROS right-handed SI coordinates and is accessed only on the game thread.
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
    FVector PointCm = FVector::ZeroVector;
    FVector IntoObstacleNormal = FVector::ZeroVector;
    FVector PreImpactVelocityCmS = FVector::ZeroVector;
    double ImpulseNs = 0;
    double ForceN = 0;
    double TimeSeconds = 0;
    int32 OtherGeom = INDEX_NONE;
    int32 OtherFlex = INDEX_NONE;
};

/** A single authoritative native MuJoCo model; never advances Chaos bodies. */
class FMujocoVehicleDynamics
{
public:
    FMujocoVehicleDynamics();
    ~FMujocoVehicleDynamics();
    bool Initialize(const FString& Filename, const TArray<FString>& RequiredBodies,
        const FString& RigFilename, const FString& DriveProfileFilename, FString& Error);
    bool Reset(const FTransform& ChassisPose, FString& Error, double InitialYaw = 0, double InitialPitch = 0);
    bool Step(double Seconds, FString& Error);
    bool ConfigureServo(const FString& Part, double Stiffness, double Damping, double ForceLimit, FString& Error);
    bool SetServoTarget(const FString& Part, double Position, double Velocity, bool Wrap);
    bool SetTorque(const FString& Part, double Torque);
    bool SetForceLimit(const FString& Part, double ForceLimit);
    bool GetJoint(const FString& Part, double& Position, double& Velocity) const;
    bool GetBody(const FString& Part, FMujocoBodyState& Out) const;
    const TArray<FString>& BodyNames() const { return OrderedBodyNames; }
    const TArray<FMujocoContactSample>& Contacts() const { return FrameContacts; }
    const TSharedPtr<FJsonObject>& Manifest() const { return ManifestObject; }
    double Time() const;
    double Timestep() const;
    FVector GravityCmS2() const;
    uint64 Steps() const { return TotalSteps; }
    TSharedRef<FJsonObject> Diagnostics() const;

private:
    struct FImplementation;
    TUniquePtr<FImplementation> Impl;
    TSharedPtr<FJsonObject> ManifestObject;
    TArray<FString> OrderedBodyNames;
    TArray<FMujocoContactSample> FrameContacts;
    FString ModelPath;
    FString ModelMd5;
    int32 VerifiedRuntimeBindings = 0;
    bool bPhysicsIdentityVerified = false;
    double RemainderSeconds = 0;
    uint64 TotalSteps = 0;
    uint64 ResetCount = 0;
    int32 LastFrameSteps = 0;
    double MaximumPenetrationM = 0;
    bool ValidateState(FString& Error) const;
    void CollectContacts(const TMap<FString, FVector>& BeforeVelocities);
};
