#include "VehicleRig.h"
#include "RosCoordinates.h"
#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Math/RotationMatrix.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Physics/PhysicsInterfaceCore.h"
#include "PhysicsEngine/BodyInstance.h"
#include "PhysicsEngine/BodySetup.h"
#include "PhysicsEngine/PhysicsConstraintComponent.h"
#include "PhysicalMaterials/PhysicalMaterial.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
using FContactObject=TSharedPtr<FJsonObject>;
TArray<TSharedPtr<FJsonValue>> ContactVector(const FVector& V)
{ return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),MakeShared<FJsonValueNumber>(V.Z)}; }
TSharedRef<FJsonObject> ContactTransform(const FTransform& T)
{
    auto J=MakeShared<FJsonObject>();const FQuat Q=T.GetRotation();
    J->SetArrayField(TEXT("position_cm"),ContactVector(T.GetLocation()));
    J->SetArrayField(TEXT("rotation_xyzw"),{MakeShared<FJsonValueNumber>(Q.X),MakeShared<FJsonValueNumber>(Q.Y),MakeShared<FJsonValueNumber>(Q.Z),MakeShared<FJsonValueNumber>(Q.W)});
    return J;
}
bool ContactAxis(const FContactObject& J,const TCHAR* Field,FVector& V)
{
    const TArray<TSharedPtr<FJsonValue>>* A=nullptr;
    if(!J||!J->TryGetArrayField(Field,A)||A->Num()!=3)return false;
    for(int32 I=0;I<3;++I)if(!(*A)[I]->TryGetNumber(V[I])||!FMath::IsFinite(V[I]))return false;
    return FMath::IsNearlyEqual(V.Size(),1.,1.e-4);
}
}

bool AVehicleRig::InitializeContactPhysics(const FString& Filename,FString& Error)
{
    auto Fail=[&Error](const FString& Why){Error=Why;return false;};
    if(bContactPhysics||!ContactBodies.IsEmpty()||!GetWorld()||Links.IsEmpty())return Fail(TEXT("contact rig already active or visual rig unavailable"));
    FString Text;FContactObject Spec;
    if(!FFileHelper::LoadFileToString(Text,*Filename)||Text.Len()>1024*1024||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Spec)||!Spec)
        return Fail(TEXT("contact mesh-rig JSON unavailable"));
    FString ProfileFile;
    if(Spec->TryGetStringField(TEXT("drive_profile_file"),ProfileFile))
    {
        if(ProfileFile.IsEmpty()||ProfileFile.Contains(TEXT("/"))||ProfileFile.Contains(TEXT("\\"))||
            ProfileFile.Contains(TEXT(":"))||ProfileFile.Contains(TEXT("..")))
            return Fail(TEXT("drive profile must be a filename inside the vehicle package"));
        FString ProfileText;TSharedPtr<FJsonObject> Profile;
        if(!FFileHelper::LoadFileToString(ProfileText,*FPaths::Combine(FPaths::GetPath(Filename),ProfileFile))||ProfileText.Len()>65536||
            !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(ProfileText),Profile)||!Profile)
            return Fail(TEXT("vehicle drive profile unavailable or invalid"));
        Spec->SetObjectField(TEXT("drive_profile"),Profile);
    }
    const TArray<TSharedPtr<FJsonValue>> *Parts=nullptr,*Wheels=nullptr,*Suspensions=nullptr;
    if(!Spec->TryGetArrayField(TEXT("parts"),Parts)||!Spec->TryGetArrayField(TEXT("wheels"),Wheels)||Wheels->Num()!=4||
        !Spec->TryGetArrayField(TEXT("suspension"),Suspensions)||Suspensions->Num()!=4)
        return Fail(TEXT("contact rig requires four declared wheel/suspension pairs"));

    TMap<FString,UStaticMesh*> Shapes;TMap<FString,FTransform> InitialWorld;TMap<FString,FString> Parents;
    for(const auto& Value:*Parts)
    {
        const auto Part=Value->AsObject();FString Name,Asset,Parent;
        if(!Part||!Part->TryGetStringField(TEXT("name"),Name)||!Links.Contains(Name)||Shapes.Contains(Name)||
            !Part->TryGetStringField(TEXT("collision_asset"),Asset)||!Asset.StartsWith(TEXT("/Game/RMSim/Collision/"))||Asset.Contains(TEXT("..")))
            return Fail(TEXT("every contact part needs an imported /Game/RMSim/Collision simple asset"));
        Part->TryGetStringField(TEXT("parent"),Parent);
        auto* Mesh=LoadObject<UStaticMesh>(nullptr,*Asset);
        if(!Mesh||!Mesh->GetBodySetup()||Mesh->GetBodySetup()->CollisionTraceFlag==CTF_UseComplexAsSimple||
            Mesh->GetBodySetup()->AggGeom.GetElementCount()==0)
            return Fail(TEXT("contact collision asset has no native simple convex/primitive shapes: ")+Name);
        Shapes.Add(Name,Mesh);InitialWorld.Add(Name,Links[Name]->GetComponentTransform());Parents.Add(Name,Parent);
    }
    if(!Shapes.Contains(TEXT("body"))||!Parents[TEXT("body")].IsEmpty())return Fail(TEXT("contact chassis must be the body root"));
    TSet<FString> SliderParts;
    for(const auto& Value:*Suspensions)
    {
        const auto Item=Value->AsObject();FString Name;FVector Axis;
        if(!Item||!Item->TryGetStringField(TEXT("part"),Name)||!Shapes.Contains(Name)||SliderParts.Contains(Name)||
            Parents[Name]!=TEXT("body")||!ContactAxis(Item,TEXT("axis"),Axis)||!Axis.Equals(FVector::UpVector,1.e-4))
            return Fail(TEXT("suspension must be unique body-parented Z slides"));
        SliderParts.Add(Name);
    }
    const TArray<TSharedPtr<FJsonValue>>* Steering=nullptr;TSet<FString> SteeringParts,UsedSteering;
    if(Spec->TryGetArrayField(TEXT("steering"),Steering)&&!Steering->IsEmpty())
    {
        if(Steering->Num()!=4)return Fail(TEXT("swerve requires four steering modules"));
        for(const auto& Value:*Steering)
        {
            const auto Item=Value->AsObject();FString Name,Slider;FVector Axis;
            if(!Item||!Item->TryGetStringField(TEXT("part"),Name)||!Shapes.Contains(Name)||SteeringParts.Contains(Name)||
                !Item->TryGetStringField(TEXT("suspension_part"),Slider)||!SliderParts.Contains(Slider)||Parents[Name]!=Slider||
                !ContactAxis(Item,TEXT("axis"),Axis)||!Axis.Equals(FVector::UpVector,1.e-4))
                return Fail(TEXT("steering carriers require unique Z hinges under the sliding modules"));
            SteeringParts.Add(Name);
        }
        bSwerveContact=true;
    }
    TSet<FString> WheelNames,UsedSliders;
    for(const auto& Value:*Wheels)
    {
        const auto Item=Value->AsObject();FContactWheel Wheel;FVector RosAxis;
        if(!Item||!Item->TryGetStringField(TEXT("part"),Wheel.WheelPart)||!Shapes.Contains(Wheel.WheelPart)||WheelNames.Contains(Wheel.WheelPart)||
            !Item->TryGetStringField(TEXT("suspension_part"),Wheel.SuspensionPart)||!SliderParts.Contains(Wheel.SuspensionPart)||UsedSliders.Contains(Wheel.SuspensionPart)||
            !ContactAxis(Item,TEXT("axis"),RosAxis)||FMath::Abs(RosAxis.Y)<.9999||
            !Item->TryGetNumberField(TEXT("radius_m"),Wheel.RadiusMetres)||!FMath::IsFinite(Wheel.RadiusMetres)||Wheel.RadiusMetres<=0||Wheel.RadiusMetres>1.)
            return Fail(TEXT("contact wheels need distinct sliding parents, Y axes and measured radii"));
        if(bSwerveContact)
        {
            if(!Item->TryGetStringField(TEXT("steering_part"),Wheel.SteeringPart)||!SteeringParts.Contains(Wheel.SteeringPart)||
                UsedSteering.Contains(Wheel.SteeringPart)||Parents[Wheel.WheelPart]!=Wheel.SteeringPart||Parents[Wheel.SteeringPart]!=Wheel.SuspensionPart)
                return Fail(TEXT("wheel must belong to its own steering carrier and suspension"));
            UsedSteering.Add(Wheel.SteeringPart);
        }
        else if(Parents[Wheel.WheelPart]!=Wheel.SuspensionPart)return Fail(TEXT("legacy wheel must belong to suspension"));
        Wheel.AxisLocal=FVector(-RosAxis.X,RosAxis.Y,-RosAxis.Z); // Axial ROS vector -> UE, not positional Y reflection.
        Wheel.RestLocalCm=GetActorTransform().InverseTransformPosition(InitialWorld[Wheel.WheelPart].GetLocation());
        Wheel.LeftOffsetMetres=-Wheel.RestLocalCm.Y/100.;
        WheelNames.Add(Wheel.WheelPart);UsedSliders.Add(Wheel.SuspensionPart);ContactWheels.Add(Wheel);
    }
    if(!Shapes.Contains(TEXT("yaw"))||!Shapes.Contains(TEXT("pitch"))||Shapes.Num()!=(bSwerveContact?15:11))
        return Fail(TEXT("contact rig part count does not match its steering configuration"));
    if(!InitializeDriveProfile(Spec,Error))return false;

    struct FParam { const TCHAR* Name;double Default,Min,Max; };
    for(const FParam& P : {FParam{TEXT("ChassisMassKg"),20,.1,500},{TEXT("WheelMassKg"),1,.01,20},
        {TEXT("SliderMassKg"),.35,.01,20},{TEXT("SteeringMassKg"),.2,.01,20},{TEXT("SuspensionTravelCm"),6.5,.1,30},
        {TEXT("SpringStiffnessNPerM"),1600,1,1000000},{TEXT("SpringDampingNsPerM"),90,0,100000},
        {TEXT("SpringPreloadN"),50,0,10000},
        {TEXT("SpringForceLimitN"),1000,1,100000},{TEXT("WheelFriction"),1.1,0,5},
        {TEXT("ChassisFriction"),.5,0,5},{TEXT("MotorTorqueNm"),4,.001,1000},
        {TEXT("BrakeTorqueNm"),8,.001,1000},{TEXT("MotorDampingNmS"),2,.001,1000},
        {TEXT("LinearDamping"),.05,0,20},{TEXT("AngularDamping"),.05,0,20}})
    {
        double V=P.Default;GConfig->GetDouble(TEXT("RMSim.ContactPhysics"),P.Name,V,GGameIni);
        if(PhysicsOverrides&&PhysicsOverrides->HasField(P.Name)&&!PhysicsOverrides->TryGetNumberField(P.Name,V))
            return Fail(TEXT("invalid vehicle physics override: ")+FString(P.Name));
        if(!FMath::IsFinite(V)||V<P.Min||V>P.Max)return Fail(TEXT("invalid contact demo parameter: ")+FString(P.Name));
        ContactParameters.Add(P.Name,V);
    }
    ContactMotorTorqueNm=ContactParameters[TEXT("MotorTorqueNm")];ContactBrakeTorqueNm=ContactParameters[TEXT("BrakeTorqueNm")];
    ContactMotorDamping=ContactParameters[TEXT("MotorDampingNmS")];
    auto MakeMaterial=[&](double Friction)
    {
        auto* M=NewObject<UPhysicalMaterial>(this);M->Friction=Friction;M->Restitution=0;
        M->bOverrideFrictionCombineMode=true;M->FrictionCombineMode=EFrictionCombineMode::Average;
        M->bOverrideRestitutionCombineMode=true;M->RestitutionCombineMode=EFrictionCombineMode::Min;
        ContactMaterials.Add(M);return M;
    };
    UPhysicalMaterial* WheelMaterial=MakeMaterial(ContactParameters[TEXT("WheelFriction")]);
    UPhysicalMaterial* BodyMaterial=MakeMaterial(ContactParameters[TEXT("ChassisFriction")]);
    auto MakeBody=[&](const FString& Part,double Mass,UPhysicalMaterial* Material)
    {
        auto* B=NewObject<UStaticMeshComponent>(this,*FString(TEXT("Contact_")+Part));
        B->SetMobility(EComponentMobility::Movable);B->SetStaticMesh(Shapes[Part]);B->SetWorldTransform(InitialWorld[Part]);
        B->SetCollisionProfileName(TEXT("PhysicsActor"));B->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        B->SetCollisionResponseToAllChannels(ECR_Block);B->SetCanEverAffectNavigation(false);
        // Contact observations use OnComponentHit; no overlap-volume events are consumed.
        // Skip redundant scene overlap queries when Chaos synchronizes each moving body.
        B->SetGenerateOverlapEvents(false);
        B->SetNotifyRigidBodyCollision(true);B->OnComponentHit.AddDynamic(this,&AVehicleRig::ObserveContact);
        B->SetHiddenInGame(true);B->SetVisibility(false);B->SetCastShadow(false);
        B->SetPhysMaterialOverride(Material);B->SetMassOverrideInKg(NAME_None,Mass);
        B->SetLinearDamping(ContactParameters[TEXT("LinearDamping")]);B->SetAngularDamping(ContactParameters[TEXT("AngularDamping")]);
        B->BodyInstance.SetPositionSolverIterationCount(32);B->BodyInstance.SetVelocitySolverIterationCount(8);
        B->SetUseCCD(true);AddInstanceComponent(B);B->RegisterComponent();ContactBodies.Add(B);return B;
    };
    USceneComponent* OriginalRoot=GetRootComponent();
    ContactChassis=MakeBody(TEXT("body"),ContactParameters[TEXT("ChassisMassKg")],BodyMaterial);
    SetRootComponent(ContactChassis);OriginalRoot->AttachToComponent(ContactChassis,FAttachmentTransformRules::KeepWorldTransform);
    ContactChassis->SetSimulatePhysics(true);
    // Neutral gimbal convex shapes join the chassis body. Their visual joints still aim;
    // dynamically articulated turret inertia/collision remains a separately recorded limit.
    for(const FString Part:{FString(TEXT("yaw")),FString(TEXT("pitch"))})
    {
        auto* Fixed=MakeBody(Part,.1,BodyMaterial);
        Fixed->AttachToComponent(ContactChassis,FAttachmentTransformRules::KeepWorldTransform);
        if(!Fixed->WeldToImplementation(ContactChassis,NAME_None,true))return Fail(TEXT("cannot weld fixed gimbal collision proxy"));
    }
    ContactChassis->SetMassOverrideInKg(NAME_None,ContactParameters[TEXT("ChassisMassKg")]);
    auto Constraint=[&](const FString& Name,const FVector& Position,const FQuat& Rotation)
    {
        auto* C=NewObject<UPhysicsConstraintComponent>(this,*Name);C->SetupAttachment(ContactChassis);
        C->SetWorldLocationAndRotation(Position,Rotation);C->SetDisableCollision(true);
        C->SetProjectionEnabled(false); // Do not use nonphysical pose teleports to make ramps passable.
        // Default joint mass conditioning rescales the 20kg chassis / 0.35kg slide
        // only inside joints, not ground contacts; that creates spurious support torques.
        C->ConstraintInstance.DisableMassConditioning();
        AddInstanceComponent(C);C->RegisterComponent();ContactConstraints.Add(C);return C;
    };
    const FQuat ChassisRotation=ContactChassis->GetComponentQuat();
    for(FContactWheel& W:ContactWheels)
    {
        W.Slider=MakeBody(W.SuspensionPart,ContactParameters[TEXT("SliderMassKg")],BodyMaterial);
        if(bSwerveContact)W.SteeringBody=MakeBody(W.SteeringPart,ContactParameters[TEXT("SteeringMassKg")],BodyMaterial);
        W.Wheel=MakeBody(W.WheelPart,ContactParameters[TEXT("WheelMassKg")],WheelMaterial);
        Links[W.SuspensionPart]->AttachToComponent(W.Slider,FAttachmentTransformRules::KeepWorldTransform);
        Links[W.WheelPart]->AttachToComponent(W.Wheel,FAttachmentTransformRules::KeepWorldTransform);
        if(W.SteeringBody)
        {Links[W.SteeringPart]->AttachToComponent(W.SteeringBody,FAttachmentTransformRules::KeepWorldTransform);W.SteeringBody->SetSimulatePhysics(true);}
        W.Slider->SetSimulatePhysics(true);W.Wheel->SetSimulatePhysics(true);
        W.Wheel->SetAngularDamping(.01f);W.Wheel->SetPhysicsMaxAngularVelocityInRadians(300);

        W.Spring=Constraint(TEXT("ContactSpring_")+W.SuspensionPart,W.Slider->GetComponentLocation(),ChassisRotation);
        W.Spring->SetLinearXLimit(LCM_Locked,0);W.Spring->SetLinearYLimit(LCM_Locked,0);
        W.Spring->SetLinearZLimit(LCM_Limited,ContactParameters[TEXT("SuspensionTravelCm")]*.5);
        W.Spring->SetAngularSwing1Limit(ACM_Locked,0);W.Spring->SetAngularSwing2Limit(ACM_Locked,0);W.Spring->SetAngularTwistLimit(ACM_Locked,0);
        W.Spring->SetLinearPositionDrive(false,false,true);W.Spring->SetLinearVelocityDrive(false,false,true);
        W.Spring->SetLinearDriveAccelerationMode(false);
        // A spring's free length and the CAD assembly position are distinct. The demo
        // preload supports approximately one quarter of the chassis weight at neutral.
        const double PreloadOffsetCm=100*ContactParameters[TEXT("SpringPreloadN")]/ContactParameters[TEXT("SpringStiffnessNPerM")];
        W.Spring->SetLinearPositionTarget(FVector(0,0,PreloadOffsetCm));W.Spring->SetLinearVelocityTarget(FVector::ZeroVector);
        W.Spring->SetLinearDriveParams(ContactParameters[TEXT("SpringStiffnessNPerM")],ContactParameters[TEXT("SpringDampingNsPerM")],
            ContactParameters[TEXT("SpringForceLimitN")]*100); // N -> kg cm/s²; stiffness/damping coefficients are unchanged.
        W.Spring->SetConstrainedComponents(ContactChassis,NAME_None,W.Slider,NAME_None);

        if(W.SteeringBody)
        {
            const FQuat SteeringFrame=FRotationMatrix::MakeFromXY(ChassisRotation.GetUpVector(),ChassisRotation.GetForwardVector()).ToQuat();
            W.SteerJoint=Constraint(TEXT("ContactSteering_")+W.SteeringPart,W.SteeringBody->GetComponentLocation(),SteeringFrame);
            W.SteerJoint->SetLinearXLimit(LCM_Locked,0);W.SteerJoint->SetLinearYLimit(LCM_Locked,0);W.SteerJoint->SetLinearZLimit(LCM_Locked,0);
            W.SteerJoint->SetAngularSwing1Limit(ACM_Locked,0);W.SteerJoint->SetAngularSwing2Limit(ACM_Locked,0);W.SteerJoint->SetAngularTwistLimit(ACM_Free,0);
            W.SteerJoint->SetAngularDriveMode(EAngularDriveMode::TwistAndSwing);
            W.SteerJoint->SetOrientationDriveTwistAndSwing(true,false);W.SteerJoint->SetAngularVelocityDriveTwistAndSwing(true,false);
            W.SteerJoint->SetAngularDriveAccelerationMode(false);
            W.SteerJoint->SetAngularDriveParams(GetDriveParameter(TEXT("SteeringStiffnessNmRad"))*10000,
                GetDriveParameter(TEXT("SteeringDampingNmS"))*10000,GetDriveParameter(TEXT("SteeringTorqueNm"))*10000);
            W.SteerJoint->SetConstrainedComponents(W.Slider,NAME_None,W.SteeringBody,NAME_None);
        }

        const FVector Axis=ChassisRotation.RotateVector(W.AxisLocal);
        const FQuat HingeRotation=FRotationMatrix::MakeFromXY(Axis,ChassisRotation.GetUpVector()).ToQuat();
        W.Axle=Constraint(TEXT("ContactAxle_")+W.WheelPart,W.Wheel->GetComponentLocation(),HingeRotation);
        W.Axle->SetLinearXLimit(LCM_Locked,0);W.Axle->SetLinearYLimit(LCM_Locked,0);W.Axle->SetLinearZLimit(LCM_Locked,0);
        W.Axle->SetAngularSwing1Limit(ACM_Locked,0);W.Axle->SetAngularSwing2Limit(ACM_Locked,0);W.Axle->SetAngularTwistLimit(ACM_Free,0);
        W.Axle->SetAngularDriveMode(EAngularDriveMode::TwistAndSwing);
        W.Axle->SetOrientationDriveTwistAndSwing(false,false);
        W.Axle->SetAngularVelocityDriveTwistAndSwing(true,false);W.Axle->SetAngularDriveAccelerationMode(false);
        W.Axle->SetConstrainedComponents(W.SteeringBody?W.SteeringBody:W.Slider,NAME_None,W.Wheel,NAME_None);
    }
    // Disable only pairs within THIS rig. Other vehicles keep physical contact; no global channel ignore.
    TMap<FPhysicsActorHandle,TArray<FPhysicsActorHandle>> DisabledPairs;TArray<FPhysicsActorHandle> Handles;
    for(auto* B:ContactBodies)if(FBodyInstance* BI=B->GetBodyInstance())
        if(const FPhysicsActorHandle Handle=BI->GetPhysicsActor())Handles.AddUnique(Handle);
    if(Handles.Num()!=(bSwerveContact?13:9))return Fail(FString::Printf(TEXT("independent contact body count mismatch: %d"),Handles.Num()));
    for(int32 I=0;I<Handles.Num();++I)for(int32 J=I+1;J<Handles.Num();++J)DisabledPairs.FindOrAdd(Handles[I]).Add(Handles[J]);
    FPhysicsCommand::ExecuteWrite(GetWorld()->GetPhysicsScene(),[&](){FChaosEngineInterface::AddDisabledCollisionsFor_AssumesLocked(DisabledPairs);});
    bTerrainFollowing=false;bContactPhysics=true;TerrainBlockReason.Reset();GroundContacts.Reset();
    ContactRestTransforms.Add(ContactChassis,FTransform::Identity);
    for(const auto& W:ContactWheels)for(auto* B:{W.Slider,W.SteeringBody,W.Wheel})if(B)
        ContactRestTransforms.Add(B,B->GetComponentTransform().GetRelativeTransform(ContactChassis->GetComponentTransform()));
    SetContactDrive(0,0,true);
    UE_LOG(LogRMSim,Display,TEXT("CONTACT_PHYSICS_READY native_Chaos bodies=%d springs=4 steering=%d axles=4 calibrated=false"),Handles.Num(),bSwerveContact?4:0);
    return true;
}

void AVehicleRig::ResetContactPose(const FTransform& Pose)
{
    if(!bContactPhysics||Pose.ContainsNaN())return;
    ++MotionGeneration;
    // Explicit respawn only, never used by wheel driving or terrain traversal.
    ContactChassis->SetWorldTransform(Pose,false,nullptr,ETeleportType::TeleportPhysics);
    for(const auto& Entry:ContactRestTransforms)
    {
        Entry.Key->SetWorldTransform(Entry.Value*Pose,false,nullptr,ETeleportType::TeleportPhysics);
        Entry.Key->SetPhysicsLinearVelocity(FVector::ZeroVector);
        Entry.Key->SetPhysicsAngularVelocityInRadians(FVector::ZeroVector);
        Entry.Key->WakeAllRigidBodies();
    }
    ContactObservations.Reset();bFreeRollingDiagnostic=false;DriveAcceleration=FVector2D::ZeroVector;DriveFilteredWorld=FVector2D::ZeroVector;DriveFilteredYaw=0;DriveSpeedAcceleration=0;DriveDirectionRate=0;
    for(auto& W:ContactWheels){W.SteeringTargetRad=0;W.SteeringMeasuredRad=0;W.WheelIntegralBiasRadS=0;W.PreviousWheelTargetRadS=0;}
    SetContactDrive(0,0,true);
}

TSharedRef<FJsonObject> AVehicleRig::GetContactState() const
{
    auto State=MakeShared<FJsonObject>();State->SetBoolField(TEXT("enabled"),bContactPhysics);
    State->SetStringField(TEXT("scope"),TEXT("native_Chaos_contact_and_constraints; demo masses/friction/springs; fixed_neutral_gimbal_collision"));
    auto Params=MakeShared<FJsonObject>();for(const auto& Item:ContactParameters)Params->SetNumberField(Item.Key,Item.Value);
    State->SetObjectField(TEXT("parameters"),Params);
    State->SetBoolField(TEXT("joint_mass_conditioning"),false);
    State->SetStringField(TEXT("constraint_output_semantics"),TEXT("Chaos returns last-substep impulses, not forces; legacy spring/axle solver fields retain their names only"));
    State->SetBoolField(TEXT("swerve"),bSwerveContact);
    State->SetBoolField(TEXT("free_rolling_diagnostic"),bFreeRollingDiagnostic);
    State->SetStringField(TEXT("command_frame"),bDriveWorldFrame?TEXT("ROS_world_xy"):TEXT("ROS_body_xy"));
    auto Control=MakeShared<FJsonObject>();for(const auto& Item:DriveParameters)Control->SetNumberField(Item.Key,Item.Value);
    State->SetObjectField(TEXT("drive_parameters"),Control);
    State->SetArrayField(TEXT("requested_linear_m_s"),ContactVector(FVector(DriveRequestedLinear.X,DriveRequestedLinear.Y,0)));
    const double BodyYaw=-FMath::DegreesToRadians(GetActorRotation().Yaw);
    const FVector2D Forward(FMath::Cos(BodyYaw),FMath::Sin(BodyYaw)),Left(-Forward.Y,Forward.X);
    const FVector2D FilteredInFrame=bDriveWorldFrame?DriveFilteredWorld:FVector2D(FVector2D::DotProduct(DriveFilteredWorld,Forward),FVector2D::DotProduct(DriveFilteredWorld,Left));
    State->SetArrayField(TEXT("filtered_linear_m_s"),ContactVector(FVector(FilteredInFrame.X,FilteredInFrame.Y,0)));
    State->SetNumberField(TEXT("speed_acceleration_m_s2"),DriveSpeedAcceleration);
    State->SetStringField(TEXT("velocity_command_mode"),bExternalVelocityTracking?TEXT("external_shaped_velocity"):TEXT("manual_input_shaper"));
    State->SetNumberField(TEXT("direction_rate_rad_s"),DriveDirectionRate);
    State->SetArrayField(TEXT("filtered_world_linear_m_s"),ContactVector(FVector(DriveFilteredWorld.X,DriveFilteredWorld.Y,0)));
    State->SetNumberField(TEXT("filtered_yaw_rad_s"),DriveFilteredYaw);State->SetNumberField(TEXT("measured_yaw_rad_s"),GetMeasuredYawRate());
    TArray<TSharedPtr<FJsonValue>> Contacts;
    for(const auto& Item:ContactObservations)
    {
        auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("body"),Item.Key);
        J->SetStringField(TEXT("other"),Item.Value.Other);
        J->SetNumberField(TEXT("age_seconds"),GetWorld()->GetTimeSeconds()-Item.Value.Time);
        J->SetArrayField(TEXT("point_cm"),ContactVector(Item.Value.Point));
        J->SetArrayField(TEXT("normal"),ContactVector(Item.Value.Normal));
        J->SetArrayField(TEXT("impulse_kg_cm_s"),ContactVector(Item.Value.Impulse));
        Contacts.Add(MakeShared<FJsonValueObject>(J));
    }
    State->SetArrayField(TEXT("last_contact_per_body"),Contacts);
    State->SetNumberField(TEXT("command_forward_m_s"),ContactDriveForward);State->SetNumberField(TEXT("command_yaw_rad_s"),ContactDriveYaw);State->SetBoolField(TEXT("brake"),bContactBrake);
    auto Body=[](const UStaticMeshComponent* B)
    {
        auto J=ContactTransform(B->GetComponentTransform());
        J->SetArrayField(TEXT("velocity_cm_s"),ContactVector(B->GetPhysicsLinearVelocity()));
        J->SetArrayField(TEXT("origin_velocity_cm_s"),ContactVector(B->GetPhysicsLinearVelocityAtPoint(B->GetComponentLocation())));
        J->SetArrayField(TEXT("angular_velocity_rad_s"),ContactVector(B->GetPhysicsAngularVelocityInRadians()));
        J->SetNumberField(TEXT("mass_kg"),B->GetMass());J->SetBoolField(TEXT("simulating"),B->IsSimulatingPhysics());
        J->SetBoolField(TEXT("awake"),B->RigidBodyIsAwake());
        J->SetArrayField(TEXT("center_of_mass_cm"),ContactVector(B->GetCenterOfMass()));
        J->SetArrayField(TEXT("inertia_tensor_kg_cm2"),ContactVector(B->GetInertiaTensor()));
        return J;
    };
    if(ContactChassis)State->SetObjectField(TEXT("chassis"),Body(ContactChassis));
    TArray<TSharedPtr<FJsonValue>> Wheels;
    for(const FContactWheel& W:ContactWheels)if(W.Wheel&&W.Slider&&ContactChassis)
    {
        auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("part"),W.WheelPart);J->SetStringField(TEXT("suspension_part"),W.SuspensionPart);
        J->SetObjectField(TEXT("wheel"),Body(W.Wheel));J->SetObjectField(TEXT("slider"),Body(W.Slider));
        if(W.SteeringBody)J->SetObjectField(TEXT("steering"),Body(W.SteeringBody));
        J->SetNumberField(TEXT("steering_target_rad"),W.SteeringTargetRad);
        double ObservedSteering=W.SteeringMeasuredRad;
        if(W.SteeringBody)
        {const FVector V=GetActorTransform().InverseTransformVectorNoScale(W.SteeringBody->GetForwardVector());ObservedSteering=-FMath::Atan2(V.Y,V.X);}
        J->SetNumberField(TEXT("steering_measured_rad"),ObservedSteering);
        J->SetNumberField(TEXT("steering_control_feedback_rad"),W.SteeringMeasuredRad);
        J->SetNumberField(TEXT("radius_m"),W.RadiusMetres);
        const FVector Local=ContactChassis->GetComponentTransform().InverseTransformPosition(W.Slider->GetComponentLocation());
        J->SetArrayField(TEXT("slider_displacement_body_cm"),ContactVector(Local-W.RestLocalCm));
        J->SetNumberField(TEXT("motor_target_rev_s"),W.RequestedRevolutionsPerSecond);
        J->SetNumberField(TEXT("motor_integral_bias_rad_s"),W.WheelIntegralBiasRadS);
        J->SetNumberField(TEXT("wheel_feedforward_rad_s"),W.PreviousWheelTargetRadS);
        const auto* WheelParent=W.SteeringBody?W.SteeringBody:W.Slider;
        const FVector WheelAxis=WheelParent->GetComponentQuat().RotateVector(W.AxisLocal);
        J->SetNumberField(TEXT("wheel_measured_rad_s"),FVector::DotProduct(
            W.Wheel->GetPhysicsAngularVelocityInRadians()-WheelParent->GetPhysicsAngularVelocityInRadians(),WheelAxis));
        J->SetArrayField(TEXT("wheel_rolling_direction_world"),ContactVector(WheelParent->GetForwardVector()));
        J->SetArrayField(TEXT("rest_position_body_cm"),ContactVector(W.RestLocalCm));
        FVector Force,Torque;W.Spring->GetConstraintForce(Force,Torque);
        J->SetArrayField(TEXT("spring_solver_force_kg_cm_s2"),ContactVector(Force));
        W.Axle->GetConstraintForce(Force,Torque);J->SetArrayField(TEXT("axle_solver_torque_kg_cm2_s2"),ContactVector(Torque));
        if(W.SteerJoint&&W.SteeringBody)
        {
            J->SetNumberField(TEXT("steering_target_rate_rad_s"),W.SteerJoint->ConstraintInstance.GetAngularVelocityTarget().X*2*PI);
            J->SetNumberField(TEXT("steering_measured_rate_rad_s"),-FVector::DotProduct(
                W.SteeringBody->GetPhysicsAngularVelocityInRadians()-W.Slider->GetPhysicsAngularVelocityInRadians(),W.Slider->GetUpVector()));
            W.SteerJoint->GetConstraintForce(Force,Torque);
            J->SetNumberField(TEXT("steering_solver_angular_impulse_nms"),FVector::DotProduct(Torque,W.Slider->GetUpVector())/10000);
        }
        Wheels.Add(MakeShared<FJsonValueObject>(J));
    }
    State->SetArrayField(TEXT("wheels"),Wheels);return State;
}

void AVehicleRig::ObserveContact(UPrimitiveComponent* Component,AActor* OtherActor,
    UPrimitiveComponent* OtherComponent,FVector NormalImpulse,const FHitResult& Hit)
{
    if(!Component||!OtherActor||OtherActor==this)return;
    ContactObservations.Add(Component->GetName(),{Hit.ImpactPoint,Hit.ImpactNormal,NormalImpulse,
        GetWorld()->GetTimeSeconds(),OtherComponent?OtherComponent->GetName():OtherActor->GetName()});
}

void AVehicleRig::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    if(GetWorld()&&!ContactBodies.IsEmpty())
    {
        TArray<FPhysicsActorHandle> Handles;
        for(auto* B:ContactBodies)if(IsValid(B))if(auto* BI=B->GetBodyInstance())
            if(const FPhysicsActorHandle Handle=BI->GetPhysicsActor())Handles.AddUnique(Handle);
        FPhysicsCommand::ExecuteWrite(GetWorld()->GetPhysicsScene(),[&](){FChaosEngineInterface::RemoveDisabledCollisionsFor_AssumesLocked(Handles);});
    }
    bContactPhysics=false;
    Super::EndPlay(EndPlayReason); // Actor-owned constraints and components follow normal UE teardown.
}
