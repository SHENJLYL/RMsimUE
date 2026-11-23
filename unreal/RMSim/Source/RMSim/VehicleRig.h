#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "VehicleRig.generated.h"

class FJsonObject;
class USceneCaptureComponent2D;
class UStaticMeshComponent;
class UPhysicsConstraintComponent;
class UPhysicalMaterial;
class UPrimitiveComponent;
class FMujocoVehicleDynamics;

DECLARE_LOG_CATEGORY_EXTERN(LogRMSim, Log, All);

// Observations for runtime evidence, read from live components rather than command counters.
struct FManualRigState
{
    FTransform Actor;
    FBox WorldBounds = FBox(ForceInit); // World AABB of the collision query box, not animated wheel AABBs.
    FBox VisualWorldBounds = FBox(ForceInit);
    FBox LocalCollisionBounds = FBox(ForceInit);
    TMap<FString, FTransform> JointLocal;
    TMap<FString, FTransform> CameraWorld;
    TArray<FString> WheelNames;
    FString YawName;
    FString PitchName;
    bool bBlockingOverlap = true;
    bool bLastSweepHit = false;
    FString LastBlockingActor;
    FVector LastImpactPoint = FVector::ZeroVector;
    double LastSweepFraction = 1.0;
    bool bTerrainFollowing = false;
    TArray<FVector> GroundContacts;
    FVector GroundNormal = FVector::UpVector;
    FString TerrainBlockReason;
};

/** A single solver contact with one identified obstacle instance; all vectors are UE world-space. */
struct FVehicleObstacleContact
{
    TWeakObjectPtr<UPrimitiveComponent> OtherComponent;
    int32 InstanceIndex=INDEX_NONE;
    FVector PointCm=FVector::ZeroVector;
    FVector IntoObstacleNormal=FVector::ZeroVector;
    double ImpulseNs=0;
    // Linear COM estimate from current velocity minus this collision frame's solver delta.
    // It is not a command, peak-speed cache, or an exact async pre-solve point velocity.
    FVector PreImpactVelocityCmS=FVector::ZeroVector;
    double TimeSeconds=0;
    uint64 Sequence=0;
    uint64 MotionGeneration=0;
};

// Imported visual rig and optional uncalibrated contact vehicle. No simulated IMU/LiDAR yet.
UCLASS()
class RMSIM_API AVehicleRig : public AActor
{
    GENERATED_BODY()

public:
    AVehicleRig();
    virtual void Tick(float DeltaSeconds) override;
    virtual FVector GetVelocity() const override;
    bool IsMujocoPhysics() const { return MujocoDynamics.IsValid(); }
    bool StartDedicatedMujoco(double PeriodSeconds, FString& Error);
    double GetDedicatedMujocoPeriod() const { return DedicatedMujocoPeriod; }
    bool SetDedicatedGimbalTrajectory(double Yaw,double Pitch,double YawRate,double PitchRate,double ExpiresWall);
    bool ReadDedicatedMujocoProgress(uint64& Steps,uint64& Failsafes) const;
    void ReplayDedicatedLidarFrames(bool ApplyScenePoses,TFunctionRef<void(double)> Query);
    void SubmitDedicatedMujocoInputs();
    const TCHAR* GetPhysicsBackendName() const { return IsMujocoPhysics()?TEXT("mujoco"):bContactPhysics?TEXT("chaos"):TEXT("kinematic"); }
    double GetSimulationTimeSeconds() const;
    double GetDynamicsStepSeconds() const;
    double GetDynamicsGravityCmS2() const;
    FVector GetDynamicsGravityVectorCmS2() const;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
    virtual void DispatchPhysicsCollisionHit(const FRigidBodyCollisionInfo& MyInfo,
        const FRigidBodyCollisionInfo& OtherInfo,const FCollisionImpactData& CollisionData) override;
    bool LoadCanonical(const FString& Filename, FString& Error);
    bool LoadMeshRig(const FString& Filename, FString& Error);
    bool SetJointAngle(const FString& JointName, double AngleRad, FString& Error);
    bool SetGimbal(double YawRad, double PitchRad, FString& Error);
    void GetGimbalAngles(double& YawRad, double& PitchRad) const { YawRad = ManualYaw; PitchRad = ManualPitch; }
    void NudgeGimbal(double DeltaYawRad, double DeltaPitchRad);
    bool GetRosGimbalGeometry(const USceneCaptureComponent2D* Camera,FString& YawName,FString& PitchName,double& PitchMin,double& PitchMax) const;
    // Query-only kinematic obstacle guard. Returns false if any requested motion was blocked.
    bool MoveManual(const FVector& LocalDeltaCm, double DeltaYawDegrees);
    // Enable only after map collision bodies are ready. Fails without moving on unsupported ground.
    bool SnapToTerrain(FString& Error);
    bool InitializeContactPhysics(const FString& Filename,FString& Error);
    bool IsContactPhysics() const { return bContactPhysics; }
    bool IsCanonicalRig() const { return bCanonicalRig; }
    const FString& GetControlType() const { return ControlType; }
    // SI command: forward metres/sec and ROS yaw radians/sec (positive left).
    void SetContactDrive(double ForwardMetresPerSecond,double YawRadiansPerSecond,bool Brake);
    bool SetContactPlanarDrive(const FVector2D& LinearMetresPerSecond,double YawRadiansPerSecond,bool WorldFrame,bool Brake);
    // ROS base_link targets retain navigation heading and pass the shared vehicle profile shaper.
    bool SetExternalVelocityTarget(const FVector2D& LinearMetresPerSecond,double YawRadiansPerSecond,bool Brake,bool OperatorGyro=false);
    TSharedRef<FJsonObject> GetVelocityCommandState() const;
    double GetDriveParameter(const TCHAR* Name) const { return DriveParameters.FindRef(Name); }
    double GetMeasuredYawRate() const;
    FVector GetCommandOriginVelocity() const;
    // Tight world bounds of the chassis/four-wheel source convex geometry.
    bool GetChassisWheelCollisionEnvelope(FBox& OutWorldBounds,int32& OutVertexCount) const;
    FBox GetFoliageContactBounds() const;
    FVector GetRequestedDriveWorldVelocityCmS() const
    {
        if(bContactBrake)return FVector::ZeroVector;
        const double Heading=-FMath::DegreesToRadians(GetActorRotation().Yaw);
        const FVector2D Forward(FMath::Cos(Heading),FMath::Sin(Heading)),Left(-Forward.Y,Forward.X);
        const FVector2D Target=bDriveWorldFrame?DriveRequestedLinear:Forward*DriveRequestedLinear.X+Left*DriveRequestedLinear.Y;
        return FVector(Target.X,-Target.Y,0)*100.;
    }
    // Returns each fresh instance contact once per caller cursor, in sequence order (150 ms lifetime).
    void GetObstacleContactsSince(uint64& Cursor,TArray<FVehicleObstacleContact>& Out) const;
    // Live Chaos contact from our rigid bodies; caller still checks its own distance and drive intent.
    bool GetRecentHitAgainst(const UPrimitiveComponent* Other,const FVector& NearPointCm,double RadiusCm,double MaxAgeSeconds,double& ImpulseNs) const;
    bool SampleChassisImu(const FTransform& Mount,FTransform& SensorWorld,FVector& PointVelocityCm,FVector& AngularVelocityRad) const;
    bool GetSensorParentWorld(const FString& Parent,FTransform& Pose) const;
    // Native solver-time history, UE world cm. Returns false outside the current motion epoch.
    bool SampleSensorParentWorldAtTime(const FString& ParentFrame,double SolverTime,FTransform& OutPose) const;
    bool SampleSensorMotionAtTime(const FString& ParentFrame,double SolverTime,const FTransform& LocalMount,
        FTransform& OutPose,FVector& OutPointVelocityCmS,FVector& OutAngularVelocityRadS) const;
    bool GetSensorPoseHistoryRange(double& OldestSolverTime,double& NewestSolverTime,int32& SampleCount) const;
    void EnableArticulatedSensorQueries();
    TSharedRef<FJsonObject> GetSensorJointState(int64 Stamp) const;
    uint64 GetMotionGeneration() const { return MotionGeneration; }
    void EnableExternalDriveLease(bool Enabled);
    void RenewExternalDriveLease(double Seconds);
    bool EnableMotorCurrentControl(double WheelNmPerAmp,double SteerNmPerAmp);
    bool SetMotorCurrentCommands(const TArray<double>& Steer,const TArray<double>& Wheel);
    TSharedRef<FJsonObject> GetMotorFeedback() const;
    bool HasSteeringModules() const { return bSwerveContact; }
    void SetFreeRollingDiagnostic(bool Enabled,double SteeringRosRadians);
    int32 GetMujocoWarningCount() const;
    TSharedRef<FJsonObject> GetContactState() const;
    void ResetContactPose(const FTransform& Pose);
    FManualRigState GetManualState() const;
    FBox GetVisualBounds() const;
    FBox GetLocalVisualBounds() const;
    FBox GetCollisionBounds() const;
    FBox GetLocalCollisionBounds() const;
    const TMap<FString, USceneCaptureComponent2D*>& GetCameras() const { return Cameras; }
    const FString& GetVehicleId() const { return VehicleId; }
    const FString& GetTerrainBlockReason() const { return TerrainBlockReason; }

private:
    TSharedPtr<FMujocoVehicleDynamics> MujocoDynamics;
    UPROPERTY() TMap<FString,UStaticMeshComponent*> MujocoQueryBodies;
    bool InitializeMujocoPhysics(const FString& RigFilename,const FString& ModelFilename,FString& Error);
    bool InitializeCanonicalMujocoPhysics(const FString& CanonicalFilename,const FString& ModelFilename,FString& Error);
    FString MujocoRootBodyName=TEXT("body");
    FString MujocoYawBodyName=TEXT("yaw");
    FString MujocoPitchBodyName=TEXT("pitch");
    FString ControlType=TEXT("swerve");
    bool bCanonicalRig=false;
    void TickMujocoPhysics(double Seconds);
    void RefreshMujocoInputs(double Seconds,double Now);
    void SyncMujocoPose();
    void ResetMujocoPose(const FTransform& Pose);
    bool SetMujocoGimbal(double Yaw,double Pitch,FString& Error);
    TSharedRef<FJsonObject> GetMujocoContactState() const;
    double MujocoFrameSeconds=.005;
    double DedicatedMujocoPeriod=0;
    double DedicatedControlSeconds=0;
    double DedicatedGimbalTime=0,DedicatedGimbalExpiry=0;
    double MujocoYawTarget=0,MujocoPitchTarget=0,MujocoYawTargetRate=0,MujocoPitchTargetRate=0;
    double MujocoYawRateLimit=9.42477796076938,MujocoPitchRateLimit=6.283185307179586;
    bool bMujocoFailed=false;

    struct FJointBinding
    {
        USceneComponent* Child = nullptr;
        FTransform Origin;
        FVector RosAxis = FVector(1.0, 0.0, 0.0);
        FString Type;
        double Lower = 0.0;
        double Upper = 0.0;
    };

    struct FWheelVisual
    {
        FString JointName;
        double RadiusMetres = 0.0;
        double AngleRad = 0.0;
    };

    UPROPERTY() TMap<FString, USceneComponent*> Links;
    UPROPERTY() TMap<FString, USceneCaptureComponent2D*> Cameras;
    // Explicit imported geometry only; SceneCapture editor frusta also contribute generic actor bounds.
    UPROPERTY() TArray<UStaticMeshComponent*> VisualMeshes;
    TMap<FString, FJointBinding> Joints;
    TArray<FWheelVisual> WheelVisuals;
    FString VehicleId;
    FString YawJoint;
    FString PitchJoint;
    double ManualYaw = 0.0;
    double ManualPitch = 0.0;
    bool bLastSweepHit = false;
    FString LastBlockingActor;
    FVector LastImpactPoint = FVector::ZeroVector;
    double LastSweepFraction = 1.0;
    bool bTerrainFollowing = false;
    TArray<FVector> GroundContacts;
    FVector GroundNormal = FVector::UpVector;
    FString TerrainBlockReason;

    struct FContactWheel
    {
        FString WheelPart,SuspensionPart,SteeringPart;
        UStaticMeshComponent* Wheel=nullptr;
        UStaticMeshComponent* Slider=nullptr;
        UStaticMeshComponent* SteeringBody=nullptr;
        UPhysicsConstraintComponent* Spring=nullptr;
        UPhysicsConstraintComponent* Axle=nullptr;
        UPhysicsConstraintComponent* SteerJoint=nullptr;
        FVector RestLocalCm=FVector::ZeroVector;
        FVector AxisLocal=FVector(0,1,0);
        double RadiusMetres=0,LeftOffsetMetres=0,RequestedRevolutionsPerSecond=0;
        double WheelAxisSign=1.0,EffortNm=8.0;
        double SteeringTargetRad=0,SteeringMeasuredRad=0;
        double WheelIntegralBiasRadS=0,PreviousWheelTargetRadS=0;
    };
    UPROPERTY() UStaticMeshComponent* ContactChassis=nullptr;
    UPROPERTY() TArray<UStaticMeshComponent*> ContactBodies;
    UPROPERTY() TArray<UPhysicsConstraintComponent*> ContactConstraints;
    UPROPERTY() TArray<UPhysicalMaterial*> ContactMaterials;
    TArray<FContactWheel> ContactWheels;
    TMap<UStaticMeshComponent*,FTransform> ContactRestTransforms;
    TMap<FString,double> ContactParameters;
    TMap<FString,double> DriveParameters;
    TSharedPtr<FJsonObject> PhysicsOverrides;
    bool InitializeDriveProfile(const TSharedPtr<FJsonObject>& Spec,FString& Error);
    void UpdateContactDrive(double Seconds);
    bool bSwerveContact=false,bDriveWorldFrame=false;
    bool bFreeRollingDiagnostic=false;
    bool bExternalDriveLease=false,bExternalVelocityTracking=false,bAppliedExternalVelocityTracking=false;
    bool bExternalOperatorGyro=false;
    FVector2D DriveAppliedRequestedWorld=FVector2D::ZeroVector;
    double DriveAppliedRequestedYaw=0;
    bool bMotorCurrentControl=false,bApplyingMotorCurrent=false,bMotorCommandValid=false;
    double WheelTorqueNmPerAmp=.14,SteerTorqueNmPerAmp=.14;
    TArray<double> SteerCurrentCommands,WheelCurrentCommands;
    void ApplyMotorCurrentDrive();
    void SetMotorServosEnabled(bool Enabled);
    double ExternalDriveDeadline=0;
    FVector2D DriveRequestedLinear=FVector2D::ZeroVector,DriveAcceleration=FVector2D::ZeroVector;
    FVector2D DriveFilteredWorld=FVector2D::ZeroVector;
    double DriveFilteredYaw=0,DriveSpeedAcceleration=0,DriveDirectionRate=0;
    bool bContactPhysics=false;
    uint64 MotionGeneration=0;
    double ContactMotorDamping=2,ContactMotorTorqueNm=4,ContactBrakeTorqueNm=8;
    double ContactDriveForward=0,ContactDriveYaw=0;
    bool bContactBrake=true;
    struct FContactObservation { FVector Point,Normal,Impulse; double Time=0; FString Other; TWeakObjectPtr<UPrimitiveComponent> OtherComponent; };
    TMap<FString,FContactObservation> ContactObservations;
    // Game-thread solver notification ring; existing per-body telemetry remains independent.
    static constexpr int32 ObstacleContactCapacity=256;
    TArray<FVehicleObstacleContact> ObstacleContacts;
    int32 ObstacleContactWriteIndex=0;
    uint64 ObstacleContactSequence=0;
    UFUNCTION() void ObserveContact(UPrimitiveComponent* Component,AActor* OtherActor,
        UPrimitiveComponent* OtherComponent,FVector NormalImpulse,const FHitResult& Hit);

    bool AddVisuals(const TSharedPtr<FJsonObject>& Link, USceneComponent* Parent, FString& Error);
    bool AddSensor(const TSharedPtr<FJsonObject>& Sensor, FString& Error);
    bool OverlapsWorld(const FTransform& Pose, const FBox& LocalBounds, double PlanarPaddingCm = 0.0) const;
    FTransform GetCollisionVisualTransform(const UStaticMeshComponent* Visual) const;
    bool TerrainPose(const FTransform& Candidate, bool Initial, FTransform& OutPose,
        TArray<FVector>& Contacts, FVector& Normal, FString& Error) const;
    bool MoveOnTerrain(const FVector& LocalDeltaCm, double DeltaYawDegrees);
    void RollWheels(const FTransform& Before, const TArray<FVector>& WheelLocalPositions);
    struct FPartCollider { FBox Bounds; FTransform Relative; };
    TArray<FPartCollider> GetPartColliders() const;
    bool OverlapsTerrainParts(const FTransform& Pose) const;
};
