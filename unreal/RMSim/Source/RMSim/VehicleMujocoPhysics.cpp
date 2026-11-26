#include "VehicleRig.h"
#include "MujocoVehicleDynamics.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformTime.h"
#include "Misc/App.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Physics/Experimental/PhysScene_Chaos.h"
#include "PBDRigidsSolver.h"
#include "PhysicsEngine/BodySetup.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
TArray<TSharedPtr<FJsonValue>> MjVector(const FVector& V)
{ return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),MakeShared<FJsonValueNumber>(V.Z)}; }
TSharedRef<FJsonObject> MjBodyJson(const FMujocoBodyState& S)
{
    auto J=MakeShared<FJsonObject>(); const FQuat Q=S.World.GetRotation();
    J->SetArrayField(TEXT("position_cm"),MjVector(S.World.GetLocation()));
    J->SetArrayField(TEXT("rotation_xyzw"),{MakeShared<FJsonValueNumber>(Q.X),MakeShared<FJsonValueNumber>(Q.Y),MakeShared<FJsonValueNumber>(Q.Z),MakeShared<FJsonValueNumber>(Q.W)});
    J->SetArrayField(TEXT("velocity_cm_s"),MjVector(S.VelocityCmS+FVector::CrossProduct(S.AngularVelocityRadS,S.CentreOfMassCm-S.World.GetLocation())));
    J->SetArrayField(TEXT("origin_velocity_cm_s"),MjVector(S.VelocityCmS));
    J->SetArrayField(TEXT("angular_velocity_rad_s"),MjVector(S.AngularVelocityRadS));
    J->SetArrayField(TEXT("center_of_mass_cm"),MjVector(S.CentreOfMassCm));
    J->SetArrayField(TEXT("inertia_tensor_kg_cm2"),MjVector(S.PrincipalInertiaKgCm2));
    J->SetStringField(TEXT("inertia_basis"),TEXT("MuJoCo principal inertial axes"));
    J->SetNumberField(TEXT("mass_kg"),S.MassKg);J->SetBoolField(TEXT("simulating"),true);J->SetBoolField(TEXT("awake"),true);
    J->SetBoolField(TEXT("chaos_simulating"),false);return J;
}
}

double AVehicleRig::GetSimulationTimeSeconds() const
{
    if(MujocoDynamics)return MujocoDynamics->Time();
    auto* Scene=GetWorld()?GetWorld()->GetPhysicsScene():nullptr;
    return Scene&&Scene->GetSolver()?double(Scene->GetSolver()->GetSolverTime()):0.;
}

double AVehicleRig::GetDynamicsStepSeconds() const
{ return MujocoDynamics?MujocoDynamics->Timestep():FApp::UseFixedTimeStep()?FApp::GetFixedDeltaTime():0.; }

const TArray<FMujocoContactSample>* AVehicleRig::GetNativeContactFrame() const
{
    check(IsInGameThread());
    return MujocoDynamics ? &MujocoDynamics->Contacts() : nullptr;
}

FVector AVehicleRig::GetDynamicsGravityVectorCmS2() const
{ return MujocoDynamics?MujocoDynamics->GravityCmS2():FVector(0,0,GetWorld()?GetWorld()->GetGravityZ():0.); }

double AVehicleRig::GetDynamicsGravityCmS2() const
{ return GetDynamicsGravityVectorCmS2().Z; }

FVector AVehicleRig::GetVelocity() const
{
    if(MujocoDynamics)
    { FMujocoBodyState S; return MujocoDynamics->GetBody(MujocoRootBodyName,S)?S.VelocityCmS:FVector::ZeroVector; }
    return Super::GetVelocity();
}

bool AVehicleRig::InitializeCanonicalMujocoPhysics(const FString& CanonicalFilename,const FString& ModelFilename,FString& Error)
{
    auto Fail=[&Error](const FString& Why){Error=Why;return false;};
    if(bContactPhysics||!GetWorld()||Links.IsEmpty())return Fail(TEXT("Canonical MuJoCo requires an inactive canonical visual rig"));
    TArray<FString> BodyNames;
    for(const auto& Pair:Links)BodyNames.Add(Pair.Key);
    auto Native=MakeShared<FMujocoVehicleDynamics>();
    if(!Native->Initialize(ModelFilename,BodyNames,CanonicalFilename,FString(),Error))return false;
    const auto Manifest=Native->Manifest();
    if(!Manifest)return Fail(TEXT("Canonical MuJoCo manifest unavailable"));
    MujocoRootBodyName=Native->RootBody();
    MujocoYawBodyName=Native->YawBody();
    MujocoPitchBodyName=Native->PitchBody();
    ControlType=TEXT("differential_drive");
    Manifest->TryGetStringField(TEXT("control_type"),ControlType);
    bSwerveContact=ControlType.Equals(TEXT("holonomic"),ESearchCase::IgnoreCase);
    // Initialize default drive parameters without requiring an AIM drive-profile.json.
    auto EmptySpec=MakeShared<FJsonObject>();
    if(!InitializeDriveProfile(EmptySpec,Error))return false;
    const TSharedPtr<FJsonObject>* Limits=nullptr;
    if(Manifest->TryGetObjectField(TEXT("limits"),Limits))
    {
        double Linear=0,Angular=0;
        if((*Limits)->TryGetNumberField(TEXT("linear_mps"),Linear)&&FMath::IsFinite(Linear)&&Linear>0)
        {
            DriveParameters.Add(TEXT("MaxForwardSpeedMps"),Linear);
            DriveParameters.Add(TEXT("MaxReverseSpeedMps"),Linear);
            DriveParameters.Add(TEXT("MaxLateralSpeedMps"),bSwerveContact?Linear:0.0);
            DriveParameters.Add(TEXT("MaxWheelSpeedMps"),FMath::Max(Linear*1.5,4.0));
        }
        if((*Limits)->TryGetNumberField(TEXT("angular_rps"),Angular)&&FMath::IsFinite(Angular)&&Angular>0)
        {
            DriveParameters.Add(TEXT("ManualYawRateRadS"),Angular);
            DriveParameters.Add(TEXT("GyroMaxRadS"),FMath::Min(Angular,5.0));
        }
    }
    ContactMotorDamping=2.0;ContactMotorTorqueNm=8.0;ContactBrakeTorqueNm=12.0;
    const FVector SourceWorldGravity(0,0,GetWorld()->GetGravityZ());
    if(!Native->GravityCmS2().Equals(SourceWorldGravity,1.e-4))
        return Fail(FString::Printf(TEXT("MuJoCo gravity differs from active UE World: native=%s source=%s"),
            *Native->GravityCmS2().ToString(),*SourceWorldGravity.ToString()));
    FTransform SpawnPose=GetActorTransform();
    const TSharedPtr<FJsonObject>* SpawnObj=nullptr;
    const TArray<TSharedPtr<FJsonValue>>* SpawnPosArr=nullptr;
    if(Manifest->TryGetObjectField(TEXT("spawn"),SpawnObj)&&(*SpawnObj)->TryGetArrayField(TEXT("root_position_m"),SpawnPosArr)&&SpawnPosArr->Num()==3)
    {
        FVector Loc=SpawnPose.GetLocation();
        Loc.Z=(*SpawnPosArr)[2]->AsNumber()*100.0;
        SpawnPose.SetLocation(Loc);
    }
    const double InitialYaw=ManualYaw,InitialPitch=ManualPitch;
    if(!Native->Reset(SpawnPose,Error,InitialYaw,InitialPitch))return false;
    for(const auto& Part:Native->BodyNames())
    {
        if(!Native->ConfigureBodyDamping(Part,0.05,0.05,Error))return false;
    }
    const TArray<TSharedPtr<FJsonValue>>* Wheels=nullptr;
    if(Manifest->TryGetArrayField(TEXT("wheels"),Wheels))
    {
        for(const auto& Value:*Wheels)
        {
            const auto WJ=Value->AsObject();
            FContactWheel W;
            const TArray<TSharedPtr<FJsonValue>> *PosArr=nullptr,*AxisArr=nullptr;
            if(!WJ||!WJ->TryGetStringField(TEXT("part"),W.WheelPart)||!WJ->TryGetNumberField(TEXT("radius_m"),W.RadiusMetres)||
               W.RadiusMetres<=0||!WJ->TryGetArrayField(TEXT("position_m"),PosArr)||PosArr->Num()!=3)
                return Fail(TEXT("Canonical MuJoCo wheel entry invalid"));
            W.RestLocalCm=FVector((*PosArr)[0]->AsNumber()*100.,-(*PosArr)[1]->AsNumber()*100.,(*PosArr)[2]->AsNumber()*100.);
            W.LeftOffsetMetres=(*PosArr)[1]->AsNumber();
            W.WheelAxisSign=1.0;
            if(WJ->TryGetArrayField(TEXT("axis_in_root"),AxisArr)&&AxisArr->Num()==3)
            {
                const double Ay=(*AxisArr)[1]->AsNumber();
                W.WheelAxisSign=(Ay<0)?-1.0:1.0;
            }
            W.EffortNm=ContactMotorTorqueNm;
            WJ->TryGetNumberField(TEXT("effort_nm"),W.EffortNm);
            if(!FMath::IsFinite(W.EffortNm)||W.EffortNm<=0)W.EffortNm=ContactMotorTorqueNm;
            if(!Native->ConfigureServo(W.WheelPart,0,ContactMotorDamping,W.EffortNm,Error))return false;
            ContactWheels.Add(W);
        }
    }
    double GimbalKp=80,GimbalKd=8,GimbalTorque=12;
    const TSharedPtr<FJsonObject>* GimbalControl=nullptr;
    if(Manifest->TryGetObjectField(TEXT("gimbal_control"),GimbalControl))
    {
        (*GimbalControl)->TryGetNumberField(TEXT("stiffness_nm_rad"),GimbalKp);
        (*GimbalControl)->TryGetNumberField(TEXT("damping_nm_s"),GimbalKd);
        (*GimbalControl)->TryGetNumberField(TEXT("max_torque_nm"),GimbalTorque);
    }
    double SourceYawDegrees=540.,SourcePitchDegrees=360.;
    GConfig->GetDouble(TEXT("RMSim.ManualControl"),TEXT("GimbalYawSpeedDeg"),SourceYawDegrees,GGameIni);
    GConfig->GetDouble(TEXT("RMSim.ManualControl"),TEXT("GimbalPitchSpeedDeg"),SourcePitchDegrees,GGameIni);
    MujocoYawRateLimit=FMath::DegreesToRadians(SourceYawDegrees);MujocoPitchRateLimit=FMath::DegreesToRadians(SourcePitchDegrees);
    if(!MujocoYawBodyName.IsEmpty()&&!Native->ConfigureServo(MujocoYawBodyName,GimbalKp,GimbalKd,GimbalTorque,Error))return false;
    if(!MujocoPitchBodyName.IsEmpty()&&!Native->ConfigureServo(MujocoPitchBodyName,GimbalKp,GimbalKd,GimbalTorque,Error))return false;
    MujocoYawTarget=InitialYaw;MujocoPitchTarget=InitialPitch;MujocoYawTargetRate=MujocoPitchTargetRate=0;
    if(!MujocoYawBodyName.IsEmpty())Native->SetServoTarget(MujocoYawBodyName,MujocoYawTarget,0,true);
    if(!MujocoPitchBodyName.IsEmpty())Native->SetServoTarget(MujocoPitchBodyName,MujocoPitchTarget,0,false);
    MujocoDynamics=Native;
    bContactPhysics=true;bTerrainFollowing=false;GroundContacts.Reset();TerrainBlockReason.Reset();
    SyncMujocoPose();SetContactDrive(0,0,true);
    UE_LOG(LogRMSim,Display,TEXT("CONTACT_PHYSICS_READY canonical_MuJoCo vehicle=%s control=%s step=%.6f bodies=%d wheels=%d"),
        *VehicleId,*ControlType,Native->Timestep(),Native->BodyNames().Num(),ContactWheels.Num());
    return true;
}

bool AVehicleRig::AttachSharedMujoco(AVehicleRig& Primary,const FString& RigFilename,FString& Error)
{
    if (&Primary == this || GetWorld() != Primary.GetWorld() ||
        !Primary.MujocoDynamics || !Primary.MujocoDynamics->IsMatchWorld() ||
        Primary.bMujocoSharedFollower || Primary.MujocoOpponent.IsValid() ||
        Primary.MujocoDynamics->IsDedicated())
    {
        Error = TEXT("MuJoCo opponent binding requires a ready shared match world before its first step");
        return false;
    }
    if (!InitializeMujocoPhysics(RigFilename,FString(),Error,Primary.MujocoDynamics)) return false;
    bMujocoSharedFollower = true;
    MujocoOpponent = &Primary;
    Primary.MujocoOpponent = this;
    return true;
}

bool AVehicleRig::InitializeMujocoPhysics(const FString& RigFilename,const FString& ModelFilename,FString& Error,
    const TSharedPtr<FMujocoVehicleDynamics>& SharedWorld)
{
    auto Fail=[&Error](const FString& Why){Error=Why;return false;};
    if(bContactPhysics||!ContactBodies.IsEmpty()||!GetWorld()||Links.IsEmpty())return Fail(TEXT("MuJoCo requires an inactive imported visual rig"));
    FString Text;TSharedPtr<FJsonObject> Spec;
    if(!FFileHelper::LoadFileToString(Text,*RigFilename)||Text.Len()>1024*1024||
       !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Spec)||!Spec)return Fail(TEXT("MuJoCo mesh-rig JSON unavailable"));
    FString ProfileFile,ResolvedProfileFile;
    if(Spec->TryGetStringField(TEXT("drive_profile_file"),ProfileFile))
    {
        if(ProfileFile.IsEmpty()||ProfileFile.Contains(TEXT("/"))||ProfileFile.Contains(TEXT("\\"))||ProfileFile.Contains(TEXT(":"))||ProfileFile.Contains(TEXT("..")))
            return Fail(TEXT("drive profile must stay inside the vehicle package"));
        ResolvedProfileFile=FPaths::Combine(FPaths::GetPath(RigFilename),ProfileFile);
        FString ProfileText;TSharedPtr<FJsonObject> Profile;
        if(!FFileHelper::LoadFileToString(ProfileText,*ResolvedProfileFile)||
           !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(ProfileText),Profile)||!Profile)return Fail(TEXT("MuJoCo vehicle drive profile unavailable"));
        Spec->SetObjectField(TEXT("drive_profile"),Profile);
    }
    const TArray<TSharedPtr<FJsonValue>> *Parts=nullptr,*Wheels=nullptr,*Steering=nullptr,*Suspensions=nullptr;
    if(!Spec->TryGetArrayField(TEXT("parts"),Parts)||Parts->Num()!=15||!Spec->TryGetArrayField(TEXT("wheels"),Wheels)||Wheels->Num()!=4||
       !Spec->TryGetArrayField(TEXT("steering"),Steering)||Steering->Num()!=4||!Spec->TryGetArrayField(TEXT("suspension"),Suspensions)||Suspensions->Num()!=4)
        return Fail(TEXT("MuJoCo AIM adapter requires the complete 15-part four-module rig"));
    bSwerveContact=true;
    if(!InitializeDriveProfile(Spec,Error,bMujocoMatchWorld||SharedWorld.IsValid()))return false;
    TMap<FString,UStaticMesh*> Shapes;TArray<FString> BodyNames;
    for(const auto& Value:*Parts)
    {
        const auto P=Value->AsObject();FString Name,Asset;
        if(!P||!P->TryGetStringField(TEXT("name"),Name)||!Links.Contains(Name)||Shapes.Contains(Name)||
           !P->TryGetStringField(TEXT("collision_asset"),Asset)||!Asset.StartsWith(TEXT("/Game/RMSim/Collision/"))||Asset.Contains(TEXT("..")))
            return Fail(TEXT("MuJoCo source part/query collision binding invalid"));
        auto* Shape=LoadObject<UStaticMesh>(nullptr,*Asset);
        if(!Shape||!Shape->GetBodySetup()||Shape->GetBodySetup()->AggGeom.GetElementCount()==0)return Fail(TEXT("MuJoCo query shape unavailable: ")+Name);
        Shapes.Add(Name,Shape);BodyNames.Add(Name);
    }
    struct FParam{const TCHAR* Name;double Default,Min,Max;};
    for(const FParam& P:{FParam{TEXT("ChassisMassKg"),20,.1,500},{TEXT("WheelMassKg"),1,.01,20},
        {TEXT("SliderMassKg"),.35,.01,20},{TEXT("SteeringMassKg"),.2,.01,20},{TEXT("SuspensionTravelCm"),6.5,.1,30},
        {TEXT("SpringStiffnessNPerM"),1600,1,1000000},{TEXT("SpringDampingNsPerM"),90,0,100000},
        {TEXT("SpringPreloadN"),50,0,10000},{TEXT("SpringForceLimitN"),1000,1,100000},
        {TEXT("WheelFriction"),1.1,0,5},{TEXT("ChassisFriction"),.5,0,5},{TEXT("MotorTorqueNm"),4,.001,1000},
        {TEXT("BrakeTorqueNm"),8,.001,1000},{TEXT("MotorDampingNmS"),2,.001,1000},
        {TEXT("LinearDamping"),.05,0,20},{TEXT("AngularDamping"),.05,0,20}})
    {
        double V=P.Default;GConfig->GetDouble(TEXT("RMSim.ContactPhysics"),P.Name,V,GGameIni);
        if(PhysicsOverrides&&PhysicsOverrides->HasField(P.Name)&&!PhysicsOverrides->TryGetNumberField(P.Name,V))return Fail(TEXT("invalid MuJoCo physics profile value"));
        if(!FMath::IsFinite(V)||V<P.Min||V>P.Max)return Fail(TEXT("invalid MuJoCo profile parameter: ")+FString(P.Name));
        ContactParameters.Add(P.Name,V);
    }
    ContactMotorTorqueNm=ContactParameters[TEXT("MotorTorqueNm")];ContactBrakeTorqueNm=ContactParameters[TEXT("BrakeTorqueNm")];ContactMotorDamping=ContactParameters[TEXT("MotorDampingNmS")];
    const double InitialYaw=ManualYaw,InitialPitch=ManualPitch;
    auto Native=MakeShared<FMujocoVehicleDynamics>();
    if (SharedWorld)
    {
        if (!Native->AttachOpponent(SharedWorld,BodyNames,RigFilename,ResolvedProfileFile,Error)) return false;
    }
    else if (!Native->Initialize(ModelFilename,BodyNames,RigFilename,ResolvedProfileFile,Error,bMujocoMatchWorld))
        return false;
    const FVector SourceWorldGravity(0,0,GetWorld()->GetGravityZ());
    if(!Native->GravityCmS2().Equals(SourceWorldGravity,1.e-4))
        return Fail(FString::Printf(TEXT("MuJoCo gravity differs from active UE World: native=%s cm/s^2 source=%s cm/s^2; rebuild the model from the current world gravity"),
            *Native->GravityCmS2().ToString(),*SourceWorldGravity.ToString()));
    if(!Native->Reset(GetActorTransform(),Error,InitialYaw,InitialPitch))return false;
    TSet<FString> DampingWheelParts;
    for(const auto& Value:*Wheels)
    {
        const auto W=Value->AsObject();FString Name;
        if(!W||!W->TryGetStringField(TEXT("part"),Name))return Fail(TEXT("MuJoCo wheel damping mapping invalid"));
        DampingWheelParts.Add(Name);
    }
    for(const auto& Part:Native->BodyNames())
    {
        // The Chaos source overrides wheel angular damping to .01 after creating each body.
        const double AngularDamping=DampingWheelParts.Contains(Part)?.01:ContactParameters[TEXT("AngularDamping")];
        if(!Native->ConfigureBodyDamping(Part,ContactParameters[TEXT("LinearDamping")],AngularDamping,Error))return false;
    }
    for(const auto& Value:*Wheels)
    {
        const auto WJ=Value->AsObject();FContactWheel W;const TArray<TSharedPtr<FJsonValue>>* Axis=nullptr;
        if(!WJ||!WJ->TryGetStringField(TEXT("part"),W.WheelPart)||!WJ->TryGetStringField(TEXT("suspension_part"),W.SuspensionPart)||
           !WJ->TryGetStringField(TEXT("steering_part"),W.SteeringPart)||!WJ->TryGetNumberField(TEXT("radius_m"),W.RadiusMetres)||
           !WJ->TryGetArrayField(TEXT("axis"),Axis)||Axis->Num()!=3||!Shapes.Contains(W.WheelPart)||!Shapes.Contains(W.SuspensionPart)||!Shapes.Contains(W.SteeringPart)||W.RadiusMetres<=0)
            return Fail(TEXT("MuJoCo wheel module mapping invalid"));
        const FVector RosAxis((*Axis)[0]->AsNumber(),(*Axis)[1]->AsNumber(),(*Axis)[2]->AsNumber());
        if(!RosAxis.Equals(FVector(0,1,0),1.e-6)&&!RosAxis.Equals(FVector(0,-1,0),1.e-6))return Fail(TEXT("MuJoCo wheel axis must match source Y hinge"));
        W.AxisLocal=FVector(-RosAxis.X,RosAxis.Y,-RosAxis.Z);
        W.RestLocalCm=GetActorTransform().InverseTransformPosition(Links[W.WheelPart]->GetComponentLocation());W.LeftOffsetMetres=-W.RestLocalCm.Y/100.;
        if(!Native->ConfigureServo(W.WheelPart,0,ContactMotorDamping,ContactMotorTorqueNm,Error)||
           !Native->ConfigureServo(W.SteeringPart,GetDriveParameter(TEXT("SteeringStiffnessNmRad")),GetDriveParameter(TEXT("SteeringDampingNmS")),GetDriveParameter(TEXT("SteeringTorqueNm")),Error))return false;
        ContactWheels.Add(W);
    }
    double GimbalKp=80,GimbalKd=8,GimbalTorque=8;
    if(const auto Manifest=Native->Manifest())
    {
        const TSharedPtr<FJsonObject>* Control=nullptr;
        if(Manifest->TryGetObjectField(TEXT("gimbal_control"),Control))
        {
            (*Control)->TryGetNumberField(TEXT("stiffness_nm_rad"),GimbalKp);(*Control)->TryGetNumberField(TEXT("damping_nm_s"),GimbalKd);
            (*Control)->TryGetNumberField(TEXT("max_torque_nm"),GimbalTorque);
        }
    }
    // The same active UE configuration supplies manual, ROS aim and native target rate budgets.
    // Manifest rate values are model metadata; changing input configuration must not leave a stale native clamp.
    double SourceYawDegrees=540.,SourcePitchDegrees=360.;
    GConfig->GetDouble(TEXT("RMSim.ManualControl"),TEXT("GimbalYawSpeedDeg"),SourceYawDegrees,GGameIni);
    GConfig->GetDouble(TEXT("RMSim.ManualControl"),TEXT("GimbalPitchSpeedDeg"),SourcePitchDegrees,GGameIni);
    MujocoYawRateLimit=FMath::DegreesToRadians(SourceYawDegrees);MujocoPitchRateLimit=FMath::DegreesToRadians(SourcePitchDegrees);
    if(!FMath::IsFinite(MujocoYawRateLimit)||!FMath::IsFinite(MujocoPitchRateLimit)||MujocoYawRateLimit<=0||MujocoPitchRateLimit<=0)
        return Fail(TEXT("invalid native gimbal rate in RMSim.ManualControl source configuration"));
    if(!Native->ConfigureServo(TEXT("yaw"),GimbalKp,GimbalKd,GimbalTorque,Error)||!Native->ConfigureServo(TEXT("pitch"),GimbalKp,GimbalKd,GimbalTorque,Error))return false;
    MujocoYawTarget=InitialYaw;MujocoPitchTarget=InitialPitch;MujocoYawTargetRate=MujocoPitchTargetRate=0;
    Native->SetServoTarget(TEXT("yaw"),MujocoYawTarget,0,true);Native->SetServoTarget(TEXT("pitch"),MujocoPitchTarget,0,false);
    MujocoDynamics=Native;
    auto* OriginalRoot=GetRootComponent();
    for(const FString& Name:Native->BodyNames())
    {
        auto* B=NewObject<UStaticMeshComponent>(this,*FString(TEXT("MujocoQuery_")+Name));
        B->SetMobility(EComponentMobility::Movable);B->SetStaticMesh(Shapes[Name]);B->SetWorldTransform(Links[Name]->GetComponentTransform());
        B->SetCollisionProfileName(TEXT("PhysicsActor"));B->SetCollisionEnabled(ECollisionEnabled::QueryOnly);B->SetCollisionResponseToAllChannels(ECR_Block);
        B->SetGenerateOverlapEvents(false);B->SetNotifyRigidBodyCollision(false);B->SetCanEverAffectNavigation(false);
        B->SetHiddenInGame(true);B->SetVisibility(false);B->SetCastShadow(false);AddInstanceComponent(B);B->RegisterComponent();B->SetSimulatePhysics(false);
        ContactBodies.Add(B);MujocoQueryBodies.Add(Name,B);
        if(Name==TEXT("body"))
        {ContactChassis=B;SetRootComponent(B);OriginalRoot->AttachToComponent(B,FAttachmentTransformRules::KeepWorldTransform);}
        else B->AttachToComponent(ContactChassis,FAttachmentTransformRules::KeepWorldTransform);
    }
    for(auto& W:ContactWheels)
    {W.Wheel=MujocoQueryBodies.FindRef(W.WheelPart);W.Slider=MujocoQueryBodies.FindRef(W.SuspensionPart);W.SteeringBody=MujocoQueryBodies.FindRef(W.SteeringPart);}
    bContactPhysics=true;bTerrainFollowing=false;GroundContacts.Reset();TerrainBlockReason.Reset();
    SyncMujocoPose();SetContactDrive(0,0,true);
    UE_LOG(LogRMSim,Display,TEXT("CONTACT_PHYSICS_READY native_MuJoCo step=%.6f bodies=%d chaos_simulated=0 physical_gimbal=true calibrated=false"),Native->Timestep(),Native->BodyNames().Num());
    return true;
}

bool AVehicleRig::SetMujocoGimbal(double Yaw,double Pitch,FString& Error)
{
    const auto* PitchBinding=Joints.Find(PitchJoint);
    if(!MujocoDynamics||!PitchBinding||!FMath::IsFinite(Yaw)||!FMath::IsFinite(Pitch)||Pitch<PitchBinding->Lower||Pitch>PitchBinding->Upper)
    {Error=TEXT("invalid MuJoCo gimbal target");return false;}
    MujocoYawTarget=FMath::UnwindRadians(Yaw);MujocoPitchTarget=Pitch;
    DedicatedGimbalExpiry=0;
    // Pure critically-damped position servo for manual/step targets (zero derivative-kick twitch).
    // Analytical velocity feedforward is reserved for SetDedicatedGimbalTrajectory().
    MujocoYawTargetRate=0.0;
    MujocoPitchTargetRate=0.0;
    return true;
}

bool AVehicleRig::SetDedicatedGimbalTrajectory(double Yaw,double Pitch,double YawRate,double PitchRate,double ExpiresWall)
{
    FString Error;
    if(DedicatedMujocoPeriod<=0||!SetMujocoGimbal(Yaw,Pitch,Error))return false;
    MujocoYawTargetRate=FMath::Clamp(YawRate,-MujocoYawRateLimit,MujocoYawRateLimit);
    MujocoPitchTargetRate=FMath::Clamp(PitchRate,-MujocoPitchRateLimit,MujocoPitchRateLimit);
    DedicatedGimbalTime=MujocoDynamics->Time();
    DedicatedGimbalExpiry=ExpiresWall;
    return true;
}

bool AVehicleRig::StartDedicatedMujoco(double PeriodSeconds, FString& Error)
{
    if (!MujocoDynamics || bMujocoSharedFollower || (MujocoDynamics->IsMatchWorld() && !MujocoOpponent.IsValid()))
    {
        Error=TEXT("dedicated MuJoCo requires the ready owner rig and its match opponent");
        return false;
    }
    if(!MujocoDynamics->StartDedicated(PeriodSeconds,Error))return false;
    DedicatedMujocoPeriod=PeriodSeconds;
    if (auto* Opponent = MujocoOpponent.Get()) Opponent->DedicatedMujocoPeriod = PeriodSeconds;
    PrimaryActorTick.bTickEvenWhenPaused=true;
    return true;
}

bool AVehicleRig::ReadDedicatedMujocoProgress(uint64& Steps,uint64& Failsafes) const
{
    return MujocoDynamics&&MujocoDynamics->ReadDedicatedProgress(Steps,Failsafes);
}

void AVehicleRig::ReplayDedicatedLidarFrames(bool ApplyScenePoses,TFunctionRef<void(double)> Query)
{
    check(IsInGameThread());
    if(!MujocoDynamics)return;
    AVehicleRig* Primary = bMujocoSharedFollower ? MujocoOpponent.Get() : this;
    if (!Primary || !Primary->MujocoDynamics) return;
    AVehicleRig* Opponent = Primary->MujocoOpponent.Get();
    bool Reposed=false;
    Primary->MujocoDynamics->VisitDedicatedQueryFrames([&](double Time,const TArray<FTransform>& Parts,bool Latest)
    {
        // Both self-occlusion and arena rays use the original UE collision channels.
        // The arena is fixed. Replay the exact native robot poses, then leave the
        // latest frame restored before ROS, gameplay or the viewport reads it.
        if(ApplyScenePoses&&(!Latest||Reposed))
        {
            Primary->ApplyMujocoQueryPose(Parts,0);
            if (Opponent)
                Opponent->ApplyMujocoQueryPose(Parts,Primary->MujocoDynamics->BodyNames().Num());
            Reposed=true;
        }
        Query(Time);
    });
}

void AVehicleRig::ApplyMujocoQueryPose(const TArray<FTransform>& Parts,int32 Offset)
{
    const auto& Names = MujocoDynamics->BodyNames();
    for (int32 I = 0; I < Names.Num(); ++I)
        if (Names[I] == MujocoRootBodyName)
            SetActorTransform(Parts[Offset + I],false,nullptr,ETeleportType::None);
    for (int32 I = 0; I < Names.Num(); ++I)
    {
        const FTransform& Pose = Parts[Offset + I];
        if (auto* const* Link = Links.Find(Names[I])) (*Link)->SetWorldTransform(Pose,false,nullptr,ETeleportType::None);
        if (auto* Body = MujocoQueryBodies.FindRef(Names[I])) Body->SetWorldTransform(Pose,false,nullptr,ETeleportType::None);
    }
}

void AVehicleRig::TickMujocoPhysics(double Seconds)
{
    if (!MujocoDynamics || bMujocoFailed || bMujocoSharedFollower) return;
    AVehicleRig* Opponent = MujocoOpponent.Get();
    if (MujocoDynamics->IsMatchWorld() && !Opponent) return;
    const bool Dedicated=MujocoDynamics->IsDedicated();
    const auto Fail=[this](const FString& Error)
    {
        bMujocoFailed=true;
        SetContactDrive(0,0,true);
        UE_LOG(LogRMSim,Error,TEXT("MUJOCO_STEP_FAILED %s"),*Error);
        FPlatformMisc::RequestExitWithStatus(false,2);
    };
    if(Dedicated)
    {
        const bool Paused=GetWorld()->IsPaused();
        MujocoDynamics->SetDedicatedPaused(Paused);
        if(Paused)return;
        const double Before=MujocoDynamics->Time();
        bool Updated=false;
        FString Error;
        if(!MujocoDynamics->ConsumeDedicatedFrame(Updated,Error)){Fail(Error);return;}
        // With no new completed batch, leave the worker's prior lease intact.
        // Republishing old staged actuator targets could extend their lifetime.
        if(!Updated)return;
        SyncMujocoPose();
        DedicatedControlSeconds=MujocoDynamics->Time()-Before;
        if(DedicatedControlSeconds>0)MujocoFrameSeconds=DedicatedControlSeconds;
        if (Opponent)
        {
            Opponent->MujocoFrameSeconds = MujocoFrameSeconds;
            Opponent->SyncMujocoPose();
        }
        return;
    }
    // The fixed-step source is double precision; AActor::Tick narrows it to
    // float, which otherwise turns the first 5 ms frame into four 1 ms steps.
    if(FApp::UseFixedTimeStep())Seconds=FApp::GetFixedDeltaTime();
    if(Seconds>0)MujocoFrameSeconds=Seconds;
    const double Now = FPlatformTime::Seconds();
    RefreshMujocoInputs(Seconds,Now);
    if (Opponent)
    {
        Opponent->MujocoFrameSeconds = MujocoFrameSeconds;
        Opponent->RefreshMujocoInputs(Seconds,Now);
    }
    FString Error;
    if(!MujocoDynamics->Step(Seconds,Error))
    {Fail(Error);return;}
    SyncMujocoPose();
    if (Opponent) Opponent->SyncMujocoPose();
    MujocoDynamics->FlushTiming();
}

void AVehicleRig::RefreshMujocoInputs(double Seconds,double Now)
{
    const bool LeaseExpired=bExternalDriveLease&&Now>ExternalDriveDeadline;
    if(LeaseExpired)SetContactDrive(0,0,true);
    const bool CurrentActive=bMotorCurrentControl&&bMotorCommandValid&&!bContactBrake&&bExternalDriveLease&&!LeaseExpired;
    if(CurrentActive!=bApplyingMotorCurrent)SetMotorServosEnabled(!CurrentActive);
    if(CurrentActive)ApplyMotorCurrentDrive();else UpdateContactDrive(Seconds);
    if(MujocoDynamics->IsDedicated()&&DedicatedGimbalExpiry>0)
    {
        if(!MujocoYawBodyName.IsEmpty())MujocoDynamics->SetServoTrajectory(MujocoYawBodyName,MujocoYawTarget,MujocoYawTargetRate,true,DedicatedGimbalTime,DedicatedGimbalExpiry);
        if(!MujocoPitchBodyName.IsEmpty())MujocoDynamics->SetServoTrajectory(MujocoPitchBodyName,MujocoPitchTarget,MujocoPitchTargetRate,false,DedicatedGimbalTime,DedicatedGimbalExpiry);
    }
    else
    {
        if(!MujocoYawBodyName.IsEmpty())MujocoDynamics->SetServoTarget(MujocoYawBodyName,MujocoYawTarget,MujocoYawTargetRate,true);
        if(!MujocoPitchBodyName.IsEmpty())MujocoDynamics->SetServoTarget(MujocoPitchBodyName,MujocoPitchTarget,MujocoPitchTargetRate,false);
        MujocoYawTargetRate=0;MujocoPitchTargetRate=0;
    }
}

void AVehicleRig::SubmitDedicatedMujocoInputs()
{
    if (bMujocoSharedFollower || !MujocoDynamics || !MujocoDynamics->IsDedicated() ||
        bMujocoFailed || DedicatedControlSeconds <= 0) return;
    const double Now=FPlatformTime::Seconds();
    const double Seconds=DedicatedControlSeconds;
    DedicatedControlSeconds=0;
    const double Deadline = StageDedicatedMujocoInputs(Seconds,Now);
    auto* Opponent = MujocoOpponent.Get();
    const double OpponentDeadline = Opponent ? Opponent->StageDedicatedMujocoInputs(Seconds,Now) : 0.;
    MujocoDynamics->SubmitDedicatedControls(Deadline,OpponentDeadline);
}

double AVehicleRig::StageDedicatedMujocoInputs(double Seconds,double Now)
{
    if (bMujocoFailed) return 0.;
    RefreshMujocoInputs(Seconds,Now);
    // The worker also enforces the wall lease with the game thread stalled.
    return bExternalDriveLease&&!bContactBrake?FMath::Min(Now+.25,ExternalDriveDeadline):Now+.25;
}

bool AVehicleRig::SampleSensorParentWorldAtTime(const FString& ParentFrame,double SolverTime,FTransform& OutPose) const
{
    return MujocoDynamics && MujocoDynamics->SampleSensorParentWorldAtTime(ParentFrame,SolverTime,OutPose);
}

bool AVehicleRig::SampleSensorMotionAtTime(const FString& ParentFrame,double SolverTime,const FTransform& LocalMount,
    FTransform& OutPose,FVector& OutPointVelocityCmS,FVector& OutAngularVelocityRadS) const
{
    return MujocoDynamics && MujocoDynamics->SampleSensorMotionAtTime(ParentFrame,SolverTime,LocalMount,
        OutPose,OutPointVelocityCmS,OutAngularVelocityRadS);
}

bool AVehicleRig::GetSensorPoseHistoryRange(double& OldestSolverTime,double& NewestSolverTime,int32& SampleCount) const
{
    OldestSolverTime=NewestSolverTime=0;SampleCount=0;
    return MujocoDynamics && MujocoDynamics->GetSensorPoseHistoryRange(OldestSolverTime,NewestSolverTime,SampleCount);
}

void AVehicleRig::SyncMujocoPose()
{
    if(!MujocoDynamics)return;
    FScopedMujocoTiming SyncTiming(*MujocoDynamics,EMujocoTimingScope::Sync);
    FMujocoBodyState Root;
    if(!MujocoDynamics->GetBody(MujocoRootBodyName,Root))return;
    SetActorTransform(Root.World,false,nullptr,ETeleportType::None);
    for(const FString& Part:MujocoDynamics->BodyNames())
    {
        FMujocoBodyState State;if(!MujocoDynamics->GetBody(Part,State))continue;
        if(auto* const* Link=Links.Find(Part))(*Link)->SetWorldTransform(State.World,false,nullptr,ETeleportType::None);
        if(auto* Query=MujocoQueryBodies.FindRef(Part))
        {Query->SetWorldTransform(State.World,false,nullptr,ETeleportType::None);Query->ComponentVelocity=State.VelocityCmS;}
    }
    double Rate=0;
    if(!MujocoYawBodyName.IsEmpty()){MujocoDynamics->GetJoint(MujocoYawBodyName,ManualYaw,Rate);ManualYaw=FMath::UnwindRadians(ManualYaw);}
    if(!MujocoPitchBodyName.IsEmpty()){MujocoDynamics->GetJoint(MujocoPitchBodyName,ManualPitch,Rate);}
    const double Now=GetWorld()->GetTimeSeconds();
    FScopedMujocoTiming ContactTiming(*MujocoDynamics,EMujocoTimingScope::ContactIdentity);
    for(const auto& C:MujocoDynamics->Contacts())
    {
        UPrimitiveComponent* Other = nullptr;
        FHitResult Hit;
        if (C.OtherVehicleSlot != INDEX_NONE)
        {
            if (const auto* Opponent = MujocoOpponent.Get()) Other = Opponent->MujocoQueryBodies.FindRef(C.OtherBodyName);
        }
        else
        {
            // Resolve the static surface's UE identity using the native solved contact.
            FCollisionQueryParams Query(SCENE_QUERY_STAT(MujocoContactVisualMapping),true,this);
            if (const auto* Opponent = MujocoOpponent.Get()) Query.AddIgnoredActor(Opponent);
            if (GetWorld()->LineTraceSingleByChannel(Hit,C.PointCm-C.IntoObstacleNormal*2.,
                C.PointCm+C.IntoObstacleNormal*2.,ECC_Visibility,Query) && FVector::Dist(Hit.ImpactPoint,C.PointCm)<=2.05)
                Other = Hit.GetComponent();
        }
        if (!Other) continue;
        auto& Obs=ContactObservations.FindOrAdd(C.BodyName);
        const FVector Impulse=-C.IntoObstacleNormal*(C.ImpulseNs*100.);
        if(Obs.Time==Now&&Obs.OtherComponent==Other)Obs.Impulse+=Impulse;
        else Obs={C.PointCm,-C.IntoObstacleNormal,Impulse,Now,Other->GetName(),Other};
        FVehicleObstacleContact Snapshot;Snapshot.OtherComponent=Other;Snapshot.InstanceIndex=FMath::Max(0,Hit.Item);
        Snapshot.PointCm=C.PointCm;Snapshot.IntoObstacleNormal=C.IntoObstacleNormal;Snapshot.ImpulseNs=C.ImpulseNs;
        Snapshot.PreImpactVelocityCmS=C.PreImpactVelocityCmS;Snapshot.TimeSeconds=Now;Snapshot.Sequence=++ObstacleContactSequence;Snapshot.MotionGeneration=MotionGeneration;
        if(ObstacleContacts.Num()<ObstacleContactCapacity)ObstacleContacts.Add(Snapshot);else ObstacleContacts[ObstacleContactWriteIndex]=Snapshot;
        ObstacleContactWriteIndex=(ObstacleContactWriteIndex+1)%ObstacleContactCapacity;
    }
}

void AVehicleRig::ResetMujocoPose(const FTransform& Pose)
{
    if(!MujocoDynamics||Pose.ContainsNaN())return;
    FString Error;
    if(!MujocoDynamics->Reset(Pose,Error))
    {bMujocoFailed=true;UE_LOG(LogRMSim,Error,TEXT("MUJOCO_RESET_FAILED %s"),*Error);return;}
    ++MotionGeneration;ObstacleContacts.Reset();ObstacleContactWriteIndex=0;ContactObservations.Reset();
    bFreeRollingDiagnostic=false;DriveAcceleration=FVector2D::ZeroVector;DriveFilteredWorld=FVector2D::ZeroVector;
    DriveFilteredYaw=0;DriveSpeedAcceleration=0;DriveDirectionRate=0;MujocoYawTarget=MujocoPitchTarget=MujocoYawTargetRate=MujocoPitchTargetRate=0;
    DedicatedGimbalExpiry=0;
    DedicatedControlSeconds=0;
    for(auto& W:ContactWheels){W.SteeringTargetRad=0;W.SteeringMeasuredRad=0;W.WheelIntegralBiasRadS=0;W.PreviousWheelTargetRadS=0;}
    SyncMujocoPose();SetContactDrive(0,0,true);ExternalDriveDeadline=0;
    if(DedicatedMujocoPeriod>0&&!MujocoDynamics->IsDedicated()&&!StartDedicatedMujoco(DedicatedMujocoPeriod,Error))
    {
        bMujocoFailed=true;
        UE_LOG(LogRMSim,Error,TEXT("MUJOCO_DEDICATED_RESET_FAILED %s"),*Error);
        FPlatformMisc::RequestExitWithStatus(false,2);
    }
}

int32 AVehicleRig::GetMujocoWarningCount() const
{
    return MujocoDynamics ? MujocoDynamics->WarningCount() : 0;
}

TSharedRef<FJsonObject> AVehicleRig::GetMujocoContactState() const
{
    auto State=MujocoDynamics->Diagnostics();State->SetBoolField(TEXT("enabled"),bContactPhysics&&!bMujocoFailed);
    State->SetStringField(TEXT("physics_backend"),TEXT("mujoco"));State->SetNumberField(TEXT("sim_time_s"),MujocoDynamics->Time());
    const FVector SourceGravity(0,0,GetWorld()->GetGravityZ());
    auto Gravity=MakeShared<FJsonObject>();
    Gravity->SetStringField(TEXT("source"),TEXT("active UE World GetGravityZ"));
    Gravity->SetArrayField(TEXT("source_world_cm_s2"),MjVector(SourceGravity));
    Gravity->SetArrayField(TEXT("native_world_cm_s2"),MjVector(MujocoDynamics->GravityCmS2()));
    Gravity->SetBoolField(TEXT("matches_source"),MujocoDynamics->GravityCmS2().Equals(SourceGravity,1.e-4));
    State->SetObjectField(TEXT("gravity_mapping"),Gravity);
    auto GimbalRates=MakeShared<FJsonObject>();
    GimbalRates->SetStringField(TEXT("source"),TEXT("GGameIni [RMSim.ManualControl] GimbalYawSpeedDeg/GimbalPitchSpeedDeg"));
    GimbalRates->SetStringField(TEXT("semantics"),TEXT("source target rate budget and native feed-forward clamp; physical rate remains solver-driven"));
    GimbalRates->SetNumberField(TEXT("source_yaw_rate_rad_s"),MujocoYawRateLimit);GimbalRates->SetNumberField(TEXT("source_pitch_rate_rad_s"),MujocoPitchRateLimit);
    bool HasManifestRates=false;
    if(const auto Manifest=MujocoDynamics->Manifest())
    {
        const TSharedPtr<FJsonObject>* Control=nullptr;double Yaw=0,Pitch=0;
        if(Manifest->TryGetObjectField(TEXT("gimbal_control"),Control)&&
           (*Control)->TryGetNumberField(TEXT("yaw_rate_rad_s"),Yaw)&&(*Control)->TryGetNumberField(TEXT("pitch_rate_rad_s"),Pitch))
        {
            HasManifestRates=true;
            GimbalRates->SetNumberField(TEXT("manifest_yaw_rate_rad_s"),Yaw);GimbalRates->SetNumberField(TEXT("manifest_pitch_rate_rad_s"),Pitch);
            GimbalRates->SetBoolField(TEXT("matches_manifest"),FMath::IsNearlyEqual(Yaw,MujocoYawRateLimit,1.e-9)&&FMath::IsNearlyEqual(Pitch,MujocoPitchRateLimit,1.e-9));
        }
    }
    GimbalRates->SetBoolField(TEXT("manifest_rates_present"),HasManifestRates);State->SetObjectField(TEXT("gimbal_rate_mapping"),GimbalRates);
    State->SetStringField(TEXT("scope"),TEXT("native_MuJoCo_contact_and_joint_dynamics; original_UE_visuals; calibrated=false"));
    auto Params=MakeShared<FJsonObject>();for(const auto& P:ContactParameters)Params->SetNumberField(P.Key,P.Value);State->SetObjectField(TEXT("parameters"),Params);
    auto Controls=MakeShared<FJsonObject>();for(const auto& P:DriveParameters)Controls->SetNumberField(P.Key,P.Value);State->SetObjectField(TEXT("drive_parameters"),Controls);
    State->SetBoolField(TEXT("swerve"),bSwerveContact);State->SetBoolField(TEXT("brake"),bContactBrake);State->SetBoolField(TEXT("free_rolling_diagnostic"),bFreeRollingDiagnostic);
    State->SetObjectField(TEXT("drive"),GetVelocityCommandState());State->SetArrayField(TEXT("filtered_world_linear_m_s"),MjVector(FVector(DriveFilteredWorld.X,DriveFilteredWorld.Y,0)));
    State->SetNumberField(TEXT("filtered_yaw_rad_s"),DriveFilteredYaw);State->SetNumberField(TEXT("measured_yaw_rad_s"),GetMeasuredYawRate());
    int32 ChaosBodies=0;for(const auto* B:ContactBodies)if(IsValid(B)&&B->IsSimulatingPhysics())++ChaosBodies;
    State->SetNumberField(TEXT("chaos_simulating_body_count"),ChaosBodies);State->SetNumberField(TEXT("query_body_count"),ContactBodies.Num());
    State->SetNumberField(TEXT("chaos_simulating_bodies"),ChaosBodies);
    TArray<TSharedPtr<FJsonValue>> Bodies;double SyncError=0;
    for(const FString& Part:MujocoDynamics->BodyNames())
    {
        FMujocoBodyState B;if(!MujocoDynamics->GetBody(Part,B))continue;auto J=MjBodyJson(B);J->SetStringField(TEXT("part"),Part);
        if(auto* const* Link=Links.Find(Part))SyncError=FMath::Max(SyncError,FVector::Dist((*Link)->GetComponentLocation(),B.World.GetLocation()));
        if(Part==MujocoRootBodyName)State->SetObjectField(TEXT("chassis"),J);
        double Position=0,Velocity=0;if(MujocoDynamics->GetJoint(Part,Position,Velocity)){J->SetNumberField(TEXT("joint_position"),Position);J->SetNumberField(TEXT("joint_velocity"),Velocity);}
        Bodies.Add(MakeShared<FJsonValueObject>(J));
    }
    State->SetArrayField(TEXT("bodies_state"),Bodies);State->SetNumberField(TEXT("max_visual_body_position_error_cm"),SyncError);
    TArray<TSharedPtr<FJsonValue>> Wheels;
    for(const auto& W:ContactWheels)
    {
        auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("part"),W.WheelPart);J->SetStringField(TEXT("suspension_part"),W.SuspensionPart);
        FMujocoBodyState B;
        for(const auto& Entry:TMap<FString,FString>{{TEXT("wheel"),W.WheelPart},{TEXT("slider"),W.SuspensionPart},{TEXT("steering"),W.SteeringPart}})
            if(MujocoDynamics->GetBody(Entry.Value,B))J->SetObjectField(Entry.Key,MjBodyJson(B));
        double P=0,V=0;MujocoDynamics->GetJoint(W.WheelPart,P,V);J->SetNumberField(TEXT("wheel_measured_rad_s"),V);J->SetNumberField(TEXT("wheel_position_rad"),P);
        MujocoDynamics->GetJoint(W.SteeringPart,P,V);J->SetNumberField(TEXT("steering_measured_rad"),FMath::UnwindRadians(P));J->SetNumberField(TEXT("steering_measured_rate_rad_s"),V);
        MujocoDynamics->GetJoint(W.SuspensionPart,P,V);J->SetNumberField(TEXT("suspension_displacement_m"),P);J->SetNumberField(TEXT("suspension_velocity_m_s"),V);
        J->SetNumberField(TEXT("steering_target_rad"),W.SteeringTargetRad);J->SetNumberField(TEXT("steering_control_feedback_rad"),W.SteeringMeasuredRad);
        J->SetNumberField(TEXT("radius_m"),W.RadiusMetres);J->SetNumberField(TEXT("motor_target_rev_s"),W.RequestedRevolutionsPerSecond);
        J->SetNumberField(TEXT("motor_integral_bias_rad_s"),W.WheelIntegralBiasRadS);J->SetNumberField(TEXT("wheel_feedforward_rad_s"),W.PreviousWheelTargetRadS);
        Wheels.Add(MakeShared<FJsonValueObject>(J));
    }
    State->SetArrayField(TEXT("wheels"),Wheels);
    TArray<TSharedPtr<FJsonValue>> Contacts;
    for(const auto& C:MujocoDynamics->Contacts())
    {
        auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("body"),C.BodyName);J->SetStringField(TEXT("other"),C.OtherName);
        J->SetNumberField(TEXT("other_geom"),C.OtherGeom);J->SetNumberField(TEXT("other_flex"),C.OtherFlex);
        if (C.OtherVehicleSlot != INDEX_NONE)
        {
            J->SetNumberField(TEXT("other_vehicle_slot"),C.OtherVehicleSlot);
            J->SetStringField(TEXT("other_vehicle_body"),C.OtherBodyName);
        }
        J->SetArrayField(TEXT("point_cm"),MjVector(C.PointCm));J->SetArrayField(TEXT("into_obstacle_normal"),MjVector(C.IntoObstacleNormal));
        J->SetNumberField(TEXT("normal_force_n"),C.ForceN);J->SetNumberField(TEXT("normal_impulse_ns"),C.ImpulseNs);J->SetNumberField(TEXT("time_s"),C.TimeSeconds);
        Contacts.Add(MakeShared<FJsonValueObject>(J));
    }
    State->SetArrayField(TEXT("mujoco_contacts"),Contacts);
    TArray<TSharedPtr<FJsonValue>> Observations;
    for(const auto& Item:ContactObservations)
    {
        auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("body"),Item.Key);J->SetStringField(TEXT("other"),Item.Value.Other);
        J->SetNumberField(TEXT("age_seconds"),GetWorld()->GetTimeSeconds()-Item.Value.Time);
        J->SetArrayField(TEXT("point_cm"),MjVector(Item.Value.Point));J->SetArrayField(TEXT("normal"),MjVector(Item.Value.Normal));
        J->SetArrayField(TEXT("impulse_kg_cm_s"),MjVector(Item.Value.Impulse));Observations.Add(MakeShared<FJsonValueObject>(J));
    }
    State->SetArrayField(TEXT("last_contact_per_body"),Observations);
    State->SetStringField(TEXT("contact_observation_source"),TEXT("native MuJoCo solved contact force/impulse; UE query identifies the existing surface component only"));
    return State;
}
