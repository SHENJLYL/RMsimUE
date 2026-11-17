#include "VehicleRig.h"
#include "RosCoordinates.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Engine/World.h"
#include "CollisionQueryParams.h"
#include "CollisionShape.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

DEFINE_LOG_CATEGORY(LogRMSim);

namespace
{
using FObject = TSharedPtr<FJsonObject>;
bool Fail(FString& Error, const FString& Message) { Error = Message; return false; }
bool Text(const FObject& Object, const TCHAR* Key, FString& Out)
{
    return Object.IsValid() && Object->TryGetStringField(Key, Out) && !Out.IsEmpty();
}
bool Number(const FObject& Object, const TCHAR* Key, double& Out)
{
    return Object.IsValid() && Object->TryGetNumberField(Key, Out) && FMath::IsFinite(Out);
}
FObject ObjectAt(const FObject& Object, const TCHAR* Key)
{
    const FObject* Result = nullptr;
    return Object.IsValid() && Object->TryGetObjectField(Key, Result) ? *Result : nullptr;
}
bool VectorAt(const FObject& Object, const TCHAR* Key, FVector& Out)
{
    const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
    if (!Object.IsValid() || !Object->TryGetArrayField(Key, Values) || Values->Num() != 3) return false;
    double Values3[3];
    for (int32 I = 0; I < 3; ++I)
        if (!(*Values)[I]->TryGetNumber(Values3[I]) || !FMath::IsFinite(Values3[I])) return false;
    Out = FVector(Values3[0], Values3[1], Values3[2]);
    return true;
}
bool Origin(const FObject& Object, FTransform& Out)
{
    FVector Xyz, Rpy;
    if (!VectorAt(Object, TEXT("xyz"), Xyz) || !VectorAt(Object, TEXT("rpy"), Rpy)) return false;
    Out = FTransform(RosCoordinates::Rotation(Rpy), RosCoordinates::Position(Xyz));
    return true;
}
bool Geometry(const FObject& Object, FString& MeshPath, FVector& Scale, FString& Error)
{
    FString Type;
    if (!Text(Object, TEXT("type"), Type)) return Fail(Error, TEXT("geometry.type missing"));
    if (Type == TEXT("box"))
    {
        if (!VectorAt(Object, TEXT("size"), Scale) || Scale.GetMin() <= 0.0)
            return Fail(Error, TEXT("box.size must contain positive lengths"));
        MeshPath = TEXT("/Engine/BasicShapes/Cube.Cube"); // 100 cm per side.
    }
    else if (Type == TEXT("cylinder") || Type == TEXT("sphere"))
    {
        double Radius, Length = 0.0;
        if (!Number(Object, TEXT("radius"), Radius) || Radius <= 0.0)
            return Fail(Error, TEXT("geometry.radius must be positive"));
        if (Type == TEXT("cylinder"))
        {
            if (!Number(Object, TEXT("length"), Length) || Length <= 0.0)
                return Fail(Error, TEXT("cylinder.length must be positive"));
            MeshPath = TEXT("/Engine/BasicShapes/Cylinder.Cylinder");
            Scale = FVector(2.0 * Radius, 2.0 * Radius, Length);
        }
        else
        {
            MeshPath = TEXT("/Engine/BasicShapes/Sphere.Sphere");
            Scale = FVector(2.0 * Radius);
        }
    }
    else return Fail(Error, FString::Printf(TEXT("unsupported_geometry: %s (mesh runtime import is not implemented)"), *Type));
    return true;
}
}

AVehicleRig::AVehicleRig()
{
    RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("RigRoot"));
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.TickGroup = TG_PrePhysics;
}

bool AVehicleRig::LoadCanonical(const FString& Filename, FString& Error)
{
    FString Json;
    FObject Data;
    if (!Links.IsEmpty()) return Fail(Error, TEXT("rig_already_loaded"));
    if (!FFileHelper::LoadFileToString(Json, *Filename) || Json.Len() > 16 * 1024 * 1024)
        return Fail(Error, TEXT("canonical file unavailable or exceeds 16 MiB"));
    if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Data) || !Data.IsValid())
        return Fail(Error, TEXT("invalid canonical JSON object"));
    double Version;
    FString Kind, Coordinates, RootName;
    if (!Number(Data, TEXT("schema_version"), Version) || Version != 1.0 ||
        !Text(Data, TEXT("kind"), Kind) || Kind != TEXT("rm_vehicle_canonical") ||
        !Text(Data, TEXT("coordinate_system"), Coordinates) || Coordinates != TEXT("ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP") ||
        !Text(Data, TEXT("id"), VehicleId) || !Text(Data, TEXT("root_link"), RootName))
        return Fail(Error, TEXT("unsupported canonical version/kind/coordinate_system or missing identity"));
    const FObject Units = ObjectAt(Data, TEXT("units"));
    const TMap<FString, FString> ExpectedUnits = {{TEXT("length"), TEXT("m")}, {TEXT("angle"), TEXT("rad")},
        {TEXT("mass"), TEXT("kg")}, {TEXT("inertia"), TEXT("kg*m^2")}};
    for (const auto& Pair : ExpectedUnits)
    {
        FString Unit;
        if (!Text(Units, *Pair.Key, Unit) || Unit != Pair.Value) return Fail(Error, TEXT("unsupported_units"));
    }
    const TArray<TSharedPtr<FJsonValue>> *LinkData, *JointData, *SensorData;
    if (!Data->TryGetArrayField(TEXT("links"), LinkData) || LinkData->IsEmpty() || LinkData->Num() > 512 ||
        !Data->TryGetArrayField(TEXT("joints"), JointData) || JointData->Num() != LinkData->Num() - 1 ||
        !Data->TryGetArrayField(TEXT("sensors"), SensorData) || SensorData->Num() > 64)
        return Fail(Error, TEXT("invalid links/joints/sensors arrays"));

    // Fail before creating components for unsupported visual OR collision geometry.
    for (const auto& Value : *LinkData)
    {
        const FObject Link = Value->AsObject();
        if (!Link.IsValid()) return Fail(Error, TEXT("link must be an object"));
        for (const TCHAR* Field : {TEXT("visuals"), TEXT("collisions")})
        {
            const TArray<TSharedPtr<FJsonValue>>* Shapes;
            if (!Link->TryGetArrayField(Field, Shapes)) return Fail(Error, TEXT("link shapes missing"));
            for (const auto& Shape : *Shapes)
            {
                FString Path; FVector Scale;
                if (!Geometry(ObjectAt(Shape->AsObject(), TEXT("geometry")), Path, Scale, Error)) return false;
            }
        }
    }
    for (const auto& Value : *LinkData)
    {
        const FObject Link = Value->AsObject();
        FString Name;
        if (!Text(Link, TEXT("name"), Name) || Links.Contains(Name)) return Fail(Error, TEXT("invalid/duplicate link name"));
        USceneComponent* Component = NewObject<USceneComponent>(this);
        Component->SetMobility(EComponentMobility::Movable);
        Component->SetupAttachment(RootComponent);
        AddInstanceComponent(Component);
        Component->RegisterComponent();
        Links.Add(Name, Component);
        if (!AddVisuals(Link, Component, Error)) return false;
    }
    if (!Links.Contains(RootName)) return Fail(Error, TEXT("unknown root_link"));
    TSet<FString> Attached;
    Attached.Add(RootName);
    while (Attached.Num() < Links.Num())
    {
        bool Progress = false;
        for (const auto& Value : *JointData)
        {
            const FObject Joint = Value->AsObject();
            FString Name, Parent, Child, Type;
            if (!Text(Joint, TEXT("name"), Name) || !Text(Joint, TEXT("parent"), Parent) ||
                !Text(Joint, TEXT("child"), Child) || !Text(Joint, TEXT("type"), Type))
                return Fail(Error, TEXT("joint identity missing"));
            if (Joints.Contains(Name)) continue;
            if (!Links.Contains(Parent) || !Links.Contains(Child) || Parent == Child)
                return Fail(Error, TEXT("joint link reference invalid"));
            if (!Attached.Contains(Parent)) continue;
            if (Attached.Contains(Child)) return Fail(Error, TEXT("cycle or multiple joint parents"));
            FJointBinding Binding;
            Binding.Child = Links[Child]; Binding.Type = Type;
            if (!Origin(ObjectAt(Joint, TEXT("origin")), Binding.Origin) ||
                !VectorAt(Joint, TEXT("axis"), Binding.RosAxis)) return Fail(Error, TEXT("invalid joint pose/axis"));
            if (Type != TEXT("fixed") && Type != TEXT("continuous") && Type != TEXT("revolute"))
                return Fail(Error, TEXT("unsupported joint type"));
            if (Type != TEXT("fixed") && Binding.RosAxis.SizeSquared() < 1.e-12)
                return Fail(Error, TEXT("zero joint axis"));
            if (Type == TEXT("revolute") && (!Number(ObjectAt(Joint, TEXT("limits")), TEXT("lower"), Binding.Lower) ||
                !Number(ObjectAt(Joint, TEXT("limits")), TEXT("upper"), Binding.Upper) || Binding.Lower > Binding.Upper))
                return Fail(Error, TEXT("invalid revolute limits"));
            Binding.Child->AttachToComponent(Links[Parent], FAttachmentTransformRules::KeepRelativeTransform);
            Binding.Child->SetRelativeTransform(Binding.Origin);
            Joints.Add(Name, Binding); Attached.Add(Child); Progress = true;
        }
        if (!Progress) return Fail(Error, TEXT("disconnected or cyclic joint tree"));
    }
    if (Joints.Num() != JointData->Num()) return Fail(Error, TEXT("duplicate or unprocessed joints"));
    const FObject Control = ObjectAt(Data, TEXT("control"));
    const FObject Gimbal = ObjectAt(Control, TEXT("gimbal"));
    if (Gimbal.IsValid() && (!Text(Gimbal, TEXT("yaw_joint"), YawJoint) || !Text(Gimbal, TEXT("pitch_joint"), PitchJoint)))
        return Fail(Error, TEXT("invalid gimbal mapping"));
    for (const auto& Value : *SensorData)
        if (!AddSensor(Value->AsObject(), Error)) return false;
    UE_LOG(LogRMSim, Display, TEXT("IMPORTED %s: %d links, %d joints, %d cameras; kinematic visualization only"),
        *VehicleId, Links.Num(), Joints.Num(), Cameras.Num());
    UE_LOG(LogRMSim, Warning, TEXT("Inertials/collision metadata are retained in canonical source but not used for dynamics in this lab."));
    return true;
}

bool AVehicleRig::AddVisuals(const FObject& Link, USceneComponent* Parent, FString& Error)
{
    const TArray<TSharedPtr<FJsonValue>>* Shapes;
    if (!Link->TryGetArrayField(TEXT("visuals"), Shapes)) return Fail(Error, TEXT("visuals missing"));
    for (const auto& Value : *Shapes)
    {
        const FObject Shape = Value->AsObject();
        FTransform Pose; FString Path; FVector Scale;
        if (!Origin(ObjectAt(Shape, TEXT("origin")), Pose)) return Fail(Error, TEXT("invalid visual origin"));
        if (!Geometry(ObjectAt(Shape, TEXT("geometry")), Path, Scale, Error)) return false;
        UStaticMesh* Mesh = LoadObject<UStaticMesh>(nullptr, *Path);
        if (!Mesh) return Fail(Error, FString::Printf(TEXT("engine primitive missing: %s"), *Path));
        UStaticMeshComponent* Visual = NewObject<UStaticMeshComponent>(this);
        Visual->SetMobility(EComponentMobility::Movable);
        Visual->SetupAttachment(Parent);
        Visual->SetStaticMesh(Mesh);
        Visual->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Pose.SetScale3D(Scale);
        Visual->SetRelativeTransform(Pose);
        AddInstanceComponent(Visual); Visual->RegisterComponent();
        VisualMeshes.Add(Visual);
        // BasicShapeMaterial uses Color; runtime asset parameter availability requires visual QA.
        if (UMaterialInterface* Base = LoadObject<UMaterialInterface>(nullptr, TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")))
        {
            UMaterialInstanceDynamic* Material = UMaterialInstanceDynamic::Create(Base, this);
            FLinearColor Color(0.22f, 0.42f, 0.70f, 1.0f);
            const FObject SourceMaterial = ObjectAt(Shape, TEXT("material"));
            const TArray<TSharedPtr<FJsonValue>>* Rgba;
            if (SourceMaterial.IsValid() && SourceMaterial->TryGetArrayField(TEXT("rgba"), Rgba) && Rgba->Num() == 4)
            {
                double C[4];
                for (int32 I = 0; I < 4; ++I)
                    if (!(*Rgba)[I]->TryGetNumber(C[I]) || !FMath::IsFinite(C[I])) return Fail(Error, TEXT("invalid material rgba"));
                Color = FLinearColor(C[0], C[1], C[2], C[3]);
            }
            Material->SetVectorParameterValue(TEXT("Color"), Color);
            Visual->SetMaterial(0, Material);
        }
    }
    return true;
}

bool AVehicleRig::AddSensor(const FObject& Sensor, FString& Error)
{
    FString Id, Type, Parent;
    FTransform Pose;
    if (!Text(Sensor, TEXT("id"), Id) || !Text(Sensor, TEXT("type"), Type) ||
        !Text(Sensor, TEXT("parent_link"), Parent) || !Links.Contains(Parent) ||
        !Origin(ObjectAt(Sensor, TEXT("origin")), Pose)) return Fail(Error, TEXT("invalid sensor identity/pose"));
    if (Type != TEXT("camera"))
    {
        if (Type != TEXT("imu") && Type != TEXT("lidar")) return Fail(Error, TEXT("unsupported sensor type"));
        UE_LOG(LogRMSim, Warning, TEXT("SENSOR_NOT_IMPLEMENTED %s (%s): no synthetic messages produced"), *Id, *Type);
        return true;
    }
    if (Cameras.Contains(Id)) return Fail(Error, TEXT("duplicate camera id"));
    double Width, Height, Fov;
    const FObject Params = ObjectAt(Sensor, TEXT("parameters"));
    if (!Number(Params, TEXT("width"), Width) || !Number(Params, TEXT("height"), Height) ||
        !Number(Params, TEXT("horizontal_fov_rad"), Fov) || Width < 1 || Height < 1 ||
        Width > 4096 || Height > 4096 || Width != FMath::FloorToDouble(Width) || Height != FMath::FloorToDouble(Height) ||
        Fov <= 0 || Fov >= PI) return Fail(Error, TEXT("invalid camera dimensions/FOV (lab maximum 4096)"));
    USceneCaptureComponent2D* Capture = NewObject<USceneCaptureComponent2D>(this);
    Capture->SetupAttachment(Links[Parent]);
    Capture->SetRelativeTransform(Pose);
    Capture->FOVAngle = FMath::RadiansToDegrees(Fov);
    Capture->CaptureSource = ESceneCaptureSource::SCS_FinalColorLDR;
    Capture->bCaptureEveryFrame = true; // Preview warm-up only; not a scheduled/calibrated sensor stream.
    Capture->bAlwaysPersistRenderingState = true;
    Capture->bCaptureOnMovement = false;
    UTextureRenderTarget2D* Target = NewObject<UTextureRenderTarget2D>(this);
    Target->RenderTargetFormat = ETextureRenderTargetFormat::RTF_RGBA8_SRGB;
    Target->InitAutoFormat(static_cast<uint32>(Width), static_cast<uint32>(Height));
    Target->UpdateResourceImmediate(true);
    Capture->TextureTarget = Target;
    AddInstanceComponent(Capture); Capture->RegisterComponent();
    Cameras.Add(Id, Capture);
    return true;
}

bool AVehicleRig::SetJointAngle(const FString& JointName, double AngleRad, FString& Error)
{
    FJointBinding* Joint = Joints.Find(JointName);
    if (!Joint || Joint->Type == TEXT("fixed") || !FMath::IsFinite(AngleRad))
        return Fail(Error, TEXT("unknown/fixed joint or nonfinite angle"));
    if (Joint->Type == TEXT("revolute") && (AngleRad < Joint->Lower || AngleRad > Joint->Upper))
        return Fail(Error, TEXT("joint angle outside URDF limits"));
    FTransform Pose = Joint->Origin;
    Pose.SetRotation((Joint->Origin.GetRotation() * RosCoordinates::JointMotion(Joint->RosAxis, AngleRad)).GetNormalized());
    Joint->Child->SetRelativeTransform(Pose);
    return true;
}

bool AVehicleRig::SetGimbal(double YawRad, double PitchRad, FString& Error)
{
    if (YawJoint.IsEmpty() && PitchJoint.IsEmpty())
        return (YawRad == 0.0 && PitchRad == 0.0) || Fail(Error, TEXT("vehicle has no gimbal mapping"));
    if (!SetJointAngle(YawJoint, YawRad, Error) || !SetJointAngle(PitchJoint, PitchRad, Error)) return false;
    ManualYaw = YawRad; ManualPitch = PitchRad;
    return true;
}

bool AVehicleRig::GetRosGimbalGeometry(const USceneCaptureComponent2D* Camera,FString& YawName,FString& PitchName,double& PitchMin,double& PitchMax) const
{
    const auto* Y=Joints.Find(YawJoint);const auto* P=Joints.Find(PitchJoint);
    if(!Camera||!Y||!P||Y->Type!=TEXT("continuous")||P->Type!=TEXT("revolute")||
        !Y->RosAxis.GetSafeNormal().Equals(FVector::UpVector,1.e-6)||!P->RosAxis.GetSafeNormal().Equals(FVector(0,1,0),1.e-6)||
        !Y->Origin.GetRotation().Equals(FQuat::Identity,1.e-6)||!P->Origin.GetRotation().Equals(FQuat::Identity,1.e-6)||
        !Camera->GetComponentTransform().GetRelativeTransform(P->Child->GetComponentTransform()).GetRotation().Equals(FQuat::Identity,1.e-6))return false;
    YawName=YawJoint;PitchName=PitchJoint;PitchMin=P->Lower;PitchMax=P->Upper;return true;
}

void AVehicleRig::NudgeGimbal(double DeltaYawRad, double DeltaPitchRad)
{
    auto Apply = [this](const FString& Name, double Delta, double& Current)
    {
        const FJointBinding* Joint = Joints.Find(Name);
        if (!Joint) return;
        double Next = Current + Delta;
        if (Joint->Type == TEXT("revolute")) Next = FMath::Clamp(Next, Joint->Lower, Joint->Upper);
        FString Error;
        if (SetJointAngle(Name, Next, Error))
        {
            if (!IsContactPhysics() && OverlapsWorld(GetActorTransform(), GetLocalCollisionBounds()))
                SetJointAngle(Name, Current, Error);
            else Current = Next;
        }
    };
    Apply(YawJoint, DeltaYawRad, ManualYaw);
    Apply(PitchJoint, DeltaPitchRad, ManualPitch);
}

bool AVehicleRig::OverlapsWorld(const FTransform& Pose, const FBox& LocalBounds, double PlanarPaddingCm) const
{
    if(bTerrainFollowing)return OverlapsTerrainParts(Pose);
    if (!GetWorld() || !LocalBounds.IsValid) return true;
    const FVector Extent = LocalBounds.GetExtent() * Pose.GetScale3D().GetAbs()
        + FVector(PlanarPaddingCm, PlanarPaddingCm, 0.0);
    if (Extent.GetMin() <= 0.0) return true;
    FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimManualOverlap), false, this);
    return GetWorld()->OverlapBlockingTestByChannel(Pose.TransformPosition(LocalBounds.GetCenter()),
        Pose.GetRotation(), ECC_Pawn, FCollisionShape::MakeBox(Extent), Query,
        FCollisionResponseParams::DefaultResponseParam);
}

bool AVehicleRig::MoveManual(const FVector& LocalDeltaCm, double DeltaYawDegrees)
{
    if(bContactPhysics)return false; // Native bodies must move through forces/constraints, never this preview teleport path.
    bLastSweepHit = false; LastBlockingActor.Reset(); LastImpactPoint = FVector::ZeroVector; LastSweepFraction = 1.0;
    if (!GetWorld() || LocalDeltaCm.ContainsNaN() || !FMath::IsFinite(DeltaYawDegrees) ||
        FMath::Abs(DeltaYawDegrees) > 180.0 || !FMath::IsNearlyZero(LocalDeltaCm.Z)) return false;
    if (bTerrainFollowing) return MoveOnTerrain(LocalDeltaCm, DeltaYawDegrees);
    const FBox Bounds = GetLocalCollisionBounds();
    const FTransform Before = GetActorTransform();
    if (!Bounds.IsValid || OverlapsWorld(Before, Bounds)) return false; // No implicit depenetration.
    const FVector Extent = Bounds.GetExtent() * Before.GetScale3D().GetAbs();
    TArray<FVector> WheelLocalPositions;
    for (const FWheelVisual& Wheel : WheelVisuals)
        WheelLocalPositions.Add(Before.InverseTransformPosition(Joints[Wheel.JointName].Child->GetComponentLocation()));

    bool bBlocked = false;
    const FVector WorldDelta = Before.TransformVectorNoScale(LocalDeltaCm);
    if (!WorldDelta.IsNearlyZero())
    {
        const FVector Start = Before.TransformPosition(Bounds.GetCenter());
        FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimManualSweep), false, this);
        Query.bFindInitialOverlaps = true;
        FHitResult Hit;
        double Fraction = 1.0;
        if (GetWorld()->SweepSingleByChannel(Hit, Start, Start + WorldDelta, Before.GetRotation(),
            ECC_Pawn, FCollisionShape::MakeBox(Extent), Query, FCollisionResponseParams::DefaultResponseParam))
        {
            bLastSweepHit = true;
            LastBlockingActor = Hit.GetActor() ? Hit.GetActor()->GetName() : TEXT("unknown");
            LastImpactPoint = Hit.ImpactPoint;
            LastSweepFraction = Hit.Time;
            // Keep a small 1 mm gap from the blocking surface; stop rather than fake a slide solve.
            Fraction = Hit.bStartPenetrating ? 0.0 : FMath::Max(0.0, Hit.Time - 0.1 / WorldDelta.Size());
            bBlocked = true;
        }
        SetActorLocation(Before.GetLocation() + WorldDelta * Fraction, false, nullptr, ETeleportType::TeleportPhysics);
    }

    // A sweep's orientation is constant. Guard yaw separately in <=2-degree steps.
    const int32 Steps = FMath::Max(1, FMath::CeilToInt(FMath::Abs(DeltaYawDegrees) / 2.0));
    const double StepRad = FMath::DegreesToRadians(DeltaYawDegrees / Steps);
    const double Radius = (Bounds.GetCenter() * Before.GetScale3D()).Size2D() + Extent.Size2D();
    for (int32 I = 0; I < Steps && !FMath::IsNearlyZero(StepRad); ++I)
    {
        FTransform Candidate = GetActorTransform();
        FTransform Midpoint = Candidate;
        Midpoint.SetRotation((Candidate.GetRotation() * FQuat(FVector::UpVector, StepRad * 0.5)).GetNormalized());
        Candidate.SetRotation((Candidate.GetRotation() * FQuat(FVector::UpVector, StepRad)).GetNormalized());
        // Every point in the rotated box remains within this inflated midpoint box for the step.
        // Inflation is XY-only: it must not turn the explicit 2 cm floor gap into an initial overlap.
        const double Padding = 2.0 * Radius * FMath::Sin(FMath::Abs(StepRad) * 0.25);
        if (OverlapsWorld(Midpoint, Bounds, Padding) || OverlapsWorld(Candidate, Bounds))
        {
            bBlocked = true;
            break;
        }
        SetActorRotation(Candidate.GetRotation(), ETeleportType::TeleportPhysics);
    }

    RollWheels(Before, WheelLocalPositions);
    return !bBlocked;
}

void AVehicleRig::RollWheels(const FTransform& Before, const TArray<FVector>& WheelLocalPositions)
{
    // Roll only for accepted displacement, including each wheel centre's yaw arc.
    // Lateral slip and chassis forces are deliberately not modeled.
    const FTransform After = GetActorTransform();
    for (int32 I = 0; I < WheelVisuals.Num(); ++I)
    {
        FWheelVisual& Wheel = WheelVisuals[I];
        const FJointBinding& Joint = Joints[Wheel.JointName];
        const FVector DeltaCm = Before.InverseTransformPosition(After.TransformPosition(WheelLocalPositions[I])) - WheelLocalPositions[I];
        const FVector DeltaRosMetres(DeltaCm.X / 100.0, -DeltaCm.Y / 100.0, DeltaCm.Z / 100.0);
        const FVector RollDirection = FVector::CrossProduct(Joint.RosAxis, FVector::UpVector).GetSafeNormal();
        Wheel.AngleRad += FVector::DotProduct(DeltaRosMetres, RollDirection) / Wheel.RadiusMetres;
        FString Error;
        SetJointAngle(Wheel.JointName, Wheel.AngleRad, Error);
    }
}

FManualRigState AVehicleRig::GetManualState() const
{
    FManualRigState State;
    State.Actor = GetActorTransform();
    State.LocalCollisionBounds = GetLocalCollisionBounds();
    State.WorldBounds = State.LocalCollisionBounds.TransformBy(State.Actor);
    State.VisualWorldBounds = GetVisualBounds();
    for (const auto& Joint : Joints) State.JointLocal.Add(Joint.Key, Joint.Value.Child->GetRelativeTransform());
    for (const auto& Camera : Cameras) State.CameraWorld.Add(Camera.Key, Camera.Value->GetComponentTransform());
    for (const FWheelVisual& Wheel : WheelVisuals) State.WheelNames.Add(Wheel.JointName);
    State.YawName = YawJoint;
    State.PitchName = PitchJoint;
    State.bBlockingOverlap = OverlapsWorld(State.Actor, State.LocalCollisionBounds);
    State.bLastSweepHit = bLastSweepHit;
    State.LastBlockingActor = LastBlockingActor;
    State.LastImpactPoint = LastImpactPoint;
    State.LastSweepFraction = LastSweepFraction;
    State.bTerrainFollowing = bTerrainFollowing;
    State.GroundContacts = GroundContacts;
    State.GroundNormal = GroundNormal;
    State.TerrainBlockReason = TerrainBlockReason;
    return State;
}

FBox AVehicleRig::GetVisualBounds() const
{
    FBox Bounds(ForceInit);
    for (const UStaticMeshComponent* Visual : VisualMeshes)
        if (IsValid(Visual) && Visual->GetStaticMesh())
            Bounds += Visual->CalcBounds(Visual->GetComponentTransform()).GetBox();
    return Bounds;
}

FBox AVehicleRig::GetLocalVisualBounds() const
{
    FBox Bounds(ForceInit);
    const FTransform ActorTransform = GetActorTransform();
    for (const UStaticMeshComponent* Visual : VisualMeshes)
        if (IsValid(Visual) && Visual->GetStaticMesh())
            Bounds += Visual->CalcBounds(Visual->GetComponentTransform().GetRelativeTransform(ActorTransform)).GetBox();
    return Bounds;
}

FTransform AVehicleRig::GetCollisionVisualTransform(const UStaticMeshComponent* Visual) const
{
    for (const FWheelVisual& Wheel : WheelVisuals)
    {
        const FJointBinding* Joint = Joints.Find(Wheel.JointName);
        if (Joint && Visual->GetAttachParent() == Joint->Child)
        {
            const USceneComponent* Parent = Joint->Child->GetAttachParent();
            if (Parent)
            {
                // Wheel spin is visual only. Keep its reference-pose proxy rigidly attached to the body.
                // Rotating a cylinder's mesh AABB would incorrectly move its corners below the floor.
                return Visual->GetRelativeTransform() * Joint->Origin * Parent->GetComponentTransform();
            }
        }
    }
    return Visual->GetComponentTransform(); // Body and gimbal retain their current poses.
}

FBox AVehicleRig::GetLocalCollisionBounds() const
{
    FBox Bounds(ForceInit);
    const FTransform ActorTransform = GetActorTransform();
    for (const UStaticMeshComponent* Visual : VisualMeshes)
        if (IsValid(Visual) && Visual->GetStaticMesh())
            Bounds += Visual->CalcBounds(GetCollisionVisualTransform(Visual).GetRelativeTransform(ActorTransform)).GetBox();
    return Bounds;
}

FBox AVehicleRig::GetCollisionBounds() const
{
    return GetLocalCollisionBounds().TransformBy(GetActorTransform());
}

bool AVehicleRig::LoadMeshRig(const FString& Filename, FString& Error)
{
    FString Json, Kind;
    FObject Data;
    double Version;
    if (!Links.IsEmpty()) return Fail(Error, TEXT("rig_already_loaded"));
    if (!FFileHelper::LoadFileToString(Json, *Filename) || Json.Len() > 1024 * 1024 ||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Data) || !Data.IsValid())
        return Fail(Error, TEXT("invalid mesh rig JSON"));
    if (!Number(Data, TEXT("schema_version"), Version) || Version != 1 ||
        !Text(Data, TEXT("kind"), Kind) || Kind != TEXT("rm_ue_mesh_rig") || !Text(Data, TEXT("id"), VehicleId))
        return Fail(Error, TEXT("unsupported mesh rig format"));
    const TArray<TSharedPtr<FJsonValue>>* Parts;
    if (!Data->TryGetArrayField(TEXT("parts"), Parts) || Parts->IsEmpty() || Parts->Num() > 512)
        return Fail(Error, TEXT("mesh rig parts missing"));
    int32 Roots = 0;
    TSet<FString> Attached;
    while (Attached.Num() < Parts->Num())
    {
        bool Progress = false;
        for (const auto& Value : *Parts)
        {
            const FObject Part = Value->AsObject();
            FString Name, Parent, Asset;
            if (!Text(Part, TEXT("name"), Name) || !Text(Part, TEXT("asset"), Asset))
                return Fail(Error, TEXT("mesh part name/asset missing"));
            if (Attached.Contains(Name)) continue;
            Part->TryGetStringField(TEXT("parent"), Parent);
            if (!Parent.IsEmpty() && !Attached.Contains(Parent)) continue;
            if (!Asset.StartsWith(TEXT("/Game/RMSim/Imported/")))
                return Fail(Error, TEXT("mesh asset must be inside /Game/RMSim/Imported/"));
            FTransform Pose;
            if (!Origin(ObjectAt(Part, TEXT("origin")), Pose)) return Fail(Error, TEXT("mesh part origin missing"));
            UStaticMesh* Mesh = LoadObject<UStaticMesh>(nullptr, *Asset);
            if (!Mesh) return Fail(Error, FString::Printf(TEXT("mesh asset unavailable: %s; run FBX import first"), *Asset));
            USceneComponent* Link = NewObject<USceneComponent>(this);
            Link->SetupAttachment(Parent.IsEmpty() ? GetRootComponent() : Links[Parent]);
            Link->SetRelativeTransform(Pose);
            AddInstanceComponent(Link); Link->RegisterComponent();
            UStaticMeshComponent* Visual = NewObject<UStaticMeshComponent>(this);
            Visual->SetMobility(EComponentMobility::Movable);
            Visual->SetupAttachment(Link); Visual->SetStaticMesh(Mesh);
            Visual->SetCollisionEnabled(ECollisionEnabled::NoCollision);
            AddInstanceComponent(Visual); Visual->RegisterComponent();
            VisualMeshes.Add(Visual);
            Links.Add(Name, Link); Attached.Add(Name); Progress = true;
            if (Parent.IsEmpty()) ++Roots;
        }
        if (!Progress) return Fail(Error, TEXT("mesh part hierarchy has duplicate names, cycle or missing parent"));
    }
    if (Roots != 1) return Fail(Error, TEXT("mesh rig must have one root"));
    const FObject Gimbal = ObjectAt(Data, TEXT("gimbal"));
    for (const TCHAR* JointRole : {TEXT("yaw"), TEXT("pitch")})
    {
        const FObject Spec = ObjectAt(Gimbal, JointRole);
        FString PartName;
        if (!Text(Spec, TEXT("part"), PartName) || !Links.Contains(PartName))
            return Fail(Error, TEXT("mesh rig gimbal part missing"));
        FJointBinding Binding;
        Binding.Child = Links[PartName]; Binding.Origin = Binding.Child->GetRelativeTransform();
        if (!VectorAt(Spec, TEXT("axis"), Binding.RosAxis) || Binding.RosAxis.SizeSquared() < 1.e-12)
            return Fail(Error, TEXT("invalid mesh rig gimbal axis"));
        Binding.Type = FString(JointRole) == TEXT("pitch") ? TEXT("revolute") : TEXT("continuous");
        if (Binding.Type == TEXT("revolute") && (!Number(Spec, TEXT("lower"), Binding.Lower) ||
            !Number(Spec, TEXT("upper"), Binding.Upper) || Binding.Lower >= Binding.Upper))
            return Fail(Error, TEXT("mesh rig pitch needs explicit visualization limits"));
        Joints.Add(JointRole, Binding);
    }
    YawJoint = TEXT("yaw"); PitchJoint = TEXT("pitch");
    const TArray<TSharedPtr<FJsonValue>>* Wheels = nullptr;
    if (Data->TryGetArrayField(TEXT("wheels"), Wheels))
    {
        for (const auto& Value : *Wheels)
        {
            const FObject Wheel = Value->AsObject();
            FString PartName;
            FJointBinding Binding;
            FWheelVisual Visual;
            if (!Text(Wheel, TEXT("part"), PartName) || !Links.Contains(PartName) || Joints.Contains(PartName) ||
                !Number(Wheel, TEXT("radius_m"), Visual.RadiusMetres) || Visual.RadiusMetres <= 0.0 ||
                !VectorAt(Wheel, TEXT("axis"), Binding.RosAxis) || Binding.RosAxis.SizeSquared() < 1.e-12 ||
                FMath::Abs(Binding.RosAxis.GetSafeNormal().Z) > 1.e-6)
                return Fail(Error, TEXT("wheel visual needs a unique part, horizontal measured axis and positive radius"));
            Binding.Child = Links[PartName]; Binding.Origin = Binding.Child->GetRelativeTransform();
            Binding.Type = TEXT("continuous"); Visual.JointName = PartName;
            Joints.Add(PartName, Binding); WheelVisuals.Add(Visual);
        }
    }
    else UE_LOG(LogRMSim, Warning, TEXT("WHEEL_VISUAL_NOT_CONFIGURED: no axes/radii; no guessed wheel rotation"));
    const FObject Camera = ObjectAt(Data, TEXT("camera"));
    if (!Camera.IsValid() || !AddSensor(Camera, Error)) return false;
    UE_LOG(LogRMSim, Display, TEXT("IMPORTED_MESH_RIG %s: %d parts. Manual kinematic preview, no vehicle dynamics."), *VehicleId, Links.Num());
    UE_LOG(LogRMSim, Display, TEXT("MESH_RIG_BOUNDS_CM %s"), *GetVisualBounds().GetSize().ToString());
    return true;
}


bool AVehicleRig::GetSensorParentWorld(const FString& Parent,FTransform& Pose) const
{
    if(Parent==TEXT("base_link")){Pose=GetActorTransform();return true;}
    if(Parent!=TEXT("yaw_link"))return false;
    const auto* Joint=Joints.Find(YawJoint);
    if(!Joint||!IsValid(Joint->Child))return false;
    Pose=Joint->Child->GetComponentTransform();return !Pose.ContainsNaN();
}

void AVehicleRig::EnableArticulatedSensorQueries()
{
    // Dedicated query-only surface follows each actual visual joint. Physical contact
    // bodies and their response to vehicles/maps retain their existing settings.
    for(auto* Body:ContactBodies)if(IsValid(Body))Body->SetCollisionResponseToChannel(ECC_GameTraceChannel1,ECR_Ignore);
    for(auto* Visual:VisualMeshes)if(IsValid(Visual))
    {
        Visual->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
        Visual->SetCollisionResponseToAllChannels(ECR_Ignore);
        Visual->SetCollisionResponseToChannel(ECC_GameTraceChannel1,ECR_Block);
        Visual->RecreatePhysicsState();
    }
}

TSharedRef<FJsonObject> AVehicleRig::GetSensorJointState(int64 Stamp) const
{
    auto J=MakeShared<FJsonObject>();FTransform World;
    if(!GetSensorParentWorld(TEXT("yaw_link"),World))return J;
    const FTransform Local=World.GetRelativeTransform(GetActorTransform());
    const FVector P=Local.GetLocation()/100;const FQuat Q=Local.GetRotation();
    J->SetStringField(TEXT("parent_frame_id"),VehicleId+TEXT("/base_link"));
    J->SetStringField(TEXT("frame_id"),VehicleId+TEXT("/yaw_link"));J->SetNumberField(TEXT("stamp_ns"),double(Stamp));
    auto Pose=MakeShared<FJsonObject>();
    Pose->SetArrayField(TEXT("position_m"),{MakeShared<FJsonValueNumber>(P.X),MakeShared<FJsonValueNumber>(-P.Y),MakeShared<FJsonValueNumber>(P.Z)});
    Pose->SetArrayField(TEXT("rotation_xyzw"),{MakeShared<FJsonValueNumber>(-Q.X),MakeShared<FJsonValueNumber>(Q.Y),MakeShared<FJsonValueNumber>(-Q.Z),MakeShared<FJsonValueNumber>(Q.W)});
    J->SetObjectField(TEXT("pose_in_parent"),Pose);return J;
}
