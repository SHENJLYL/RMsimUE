#include "MujocoVehicleDynamics.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/SecureHash.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

#ifndef RMSIM_WITH_MUJOCO
#define RMSIM_WITH_MUJOCO 0
#endif
#if RMSIM_WITH_MUJOCO
#include <mujoco/mujoco.h>
#endif

namespace
{
FVector PolarToUE(const double* V, double Scale = 1.) { return FVector(V[0], -V[1], V[2]) * Scale; }
FVector AxialToUE(const double* V) { return FVector(-V[0], V[1], -V[2]); }
FQuat QuaternionToUE(const double* Q) { return FQuat(-Q[1], Q[2], -Q[3], Q[0]).GetNormalized(); }

bool ReadContractJson(const FString& Path,TSharedPtr<FJsonObject>& Out)
{
    const int64 Bytes=IFileManager::Get().FileSize(*Path);FString Text;
    return Bytes>0&&Bytes<=8*1024*1024&&FFileHelper::LoadFileToString(Text,*Path)&&
        FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Out)&&Out.IsValid();
}

bool SameContractValue(const TSharedPtr<FJsonValue>& A,const TSharedPtr<FJsonValue>& B)
{
    if(!A||!B||A->Type!=B->Type)return false;
    switch(A->Type)
    {
    case EJson::String:return A->AsString()==B->AsString();
    case EJson::Number:return FMath::IsFinite(A->AsNumber())&&A->AsNumber()==B->AsNumber();
    case EJson::Boolean:return A->AsBool()==B->AsBool();
    case EJson::Null:return true;
    case EJson::Array:
        if(A->AsArray().Num()!=B->AsArray().Num())return false;
        for(int32 I=0;I<A->AsArray().Num();++I)if(!SameContractValue(A->AsArray()[I],B->AsArray()[I]))return false;
        return true;
    case EJson::Object:
        if(A->AsObject()->Values.Num()!=B->AsObject()->Values.Num())return false;
        for(const auto& Field:A->AsObject()->Values)
        {
            const auto* Other=B->AsObject()->Values.Find(Field.Key);
            if(!Other||!SameContractValue(Field.Value,*Other))return false;
        }
        return true;
    default:return false;
    }
}

bool SameContractField(const TSharedPtr<FJsonObject>& A,const TCHAR* AKey,
    const TSharedPtr<FJsonObject>& B,const TCHAR* BKey)
{
    const auto* AV=A?A->Values.Find(AKey):nullptr;const auto* BV=B?B->Values.Find(BKey):nullptr;
    return AV&&BV&&SameContractValue(*AV,*BV);
}

// Match the physical source fields, independent of copied navigation spawns,
// JSON formatting, light settings and material presentation.
bool ValidateSceneIdentity(const TSharedPtr<FJsonObject>& Manifest,
    const TMap<FString,FString>& BoundMapAssets,FString& Error)
{
    auto Fail=[&](const FString& Why){Error=TEXT("MuJoCo scene identity: ")+Why;return false;};
    const TSharedPtr<FJsonObject>* IdentityPtr=nullptr;
    if(!Manifest->TryGetObjectField(TEXT("physics_identity"),IdentityPtr))return Fail(TEXT("missing physics_identity"));
    const auto& Identity=*IdentityPtr;
    FString MapFile;
    FParse::Value(FCommandLine::Get(),TEXT("MapFile="),MapFile);
    if(MapFile.IsEmpty())MapFile=FPaths::Combine(FPaths::ProjectDir(),TEXT("../../assets/maps/rmuc2027/map.json"));
    MapFile=FPaths::ConvertRelativePathToFull(MapFile);
    TSharedPtr<FJsonObject> Map;FString Kind,Units,Coordinates;
    if(!ReadContractJson(MapFile,Map)||!SameContractField(Map,TEXT("id"),Identity,TEXT("map_id"))||
       !SameContractField(Map,TEXT("source_sha256"),Identity,TEXT("map_source_sha256")))return Fail(TEXT("active map source differs from model"));
    if(!Map->TryGetStringField(TEXT("kind"),Kind)||Kind!=TEXT("rm_mesh_map")||
       !Map->TryGetStringField(TEXT("units"),Units)||Units!=TEXT("m")||
       !Map->TryGetStringField(TEXT("coordinate_system"),Coordinates)||Coordinates!=TEXT("ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP"))
        return Fail(TEXT("active map kind/units/coordinate system differs"));
    const TArray<TSharedPtr<FJsonValue>> *Meshes=nullptr,*ExpectedMeshes=nullptr;
    if(!Map->TryGetArrayField(TEXT("meshes"),Meshes)||!Identity->TryGetArrayField(TEXT("collision_assets"),ExpectedMeshes)||
       Meshes->Num()!=ExpectedMeshes->Num()||Meshes->Num()!=BoundMapAssets.Num())return Fail(TEXT("map collision asset set differs"));
    TMap<FString,TSharedPtr<FJsonObject>> ExpectedById;
    for(const auto& Value:*ExpectedMeshes)
    {
        FString Id;const auto Object=Value->AsObject();
        if(!Object||!Object->TryGetStringField(TEXT("id"),Id)||ExpectedById.Contains(Id))return Fail(TEXT("duplicate/missing expected mesh id"));
        ExpectedById.Add(Id,Object);
    }
    TSet<FString> Seen;
    for(const auto& Value:*Meshes)
    {
        FString Id,Asset,ExpectedAsset,Sha;const auto Object=Value->AsObject();
        if(!Object||!Object->TryGetStringField(TEXT("id"),Id)||!Object->TryGetStringField(TEXT("collision_asset"),Asset)||Seen.Contains(Id))
            return Fail(TEXT("active collision mesh lacks a unique id/asset"));
        const auto* Expected=ExpectedById.Find(Id);const auto* BoundSha=BoundMapAssets.Find(Asset);
        if(!Expected||!BoundSha||!(*Expected)->TryGetStringField(TEXT("collision_asset"),ExpectedAsset)||ExpectedAsset!=Asset||
           !(*Expected)->TryGetStringField(TEXT("collision_uasset_sha256"),Sha)||!Sha.Equals(*BoundSha,ESearchCase::IgnoreCase))
            return Fail(TEXT("active collision mesh differs: ")+Id);
        Seen.Add(Id);
    }
    FString ShellFile=FPaths::Combine(FPaths::GetPath(MapFile),TEXT("arena-shell.json"));
    if(!FPaths::FileExists(ShellFile))
    {
        FString Directory=FPaths::GetPath(MapFile);
        for(int32 Depth=0;Depth<8;++Depth)
        {
            const FString Candidate=FPaths::Combine(Directory,TEXT("assets/maps/rmuc2027/arena-shell.json"));
            if(FPaths::FileExists(Candidate)){ShellFile=Candidate;break;}
            const FString Parent=FPaths::GetPath(Directory);if(Parent.IsEmpty()||Parent==Directory)break;Directory=Parent;
        }
    }
    TSharedPtr<FJsonObject> Shell;
    const TSharedPtr<FJsonObject> *Perimeter=nullptr,*ExpectedBoundary=nullptr;
    if(!ReadContractJson(ShellFile,Shell)||!Shell->TryGetObjectField(TEXT("perimeter"),Perimeter)||
       !Identity->TryGetObjectField(TEXT("boundary"),ExpectedBoundary))return Fail(TEXT("active arena boundary unavailable"));
    for(const TCHAR* Field:{TEXT("physics_contract"),TEXT("collision_bottom_m"),TEXT("collision_top_m"),TEXT("collision_thickness_m"),TEXT("outside_clearance_m")})
        if(!SameContractField(*Perimeter,Field,*ExpectedBoundary,Field))return Fail(TEXT("arena boundary differs: ")+FString(Field));
    if(!((*Perimeter)->HasField(TEXT("target_bounds_m"))?
        SameContractField(*Perimeter,TEXT("target_bounds_m"),*ExpectedBoundary,TEXT("bounds_m")):
        SameContractField(Shell,TEXT("bounds_m"),*ExpectedBoundary,TEXT("bounds_m"))))return Fail(TEXT("arena physical bounds differ"));
    return true;
}

bool ValidateRuntimeBindings(const TSharedPtr<FJsonObject>& Manifest,const FString& XmlPath,
    const FString& RigFile,const FString& ProfileFile,bool DiagnosticPlane,int32& Count,FString& Error)
{
    auto Fail=[&](const FString& Why){Error=TEXT("MuJoCo runtime binding: ")+Why;return false;};
    const TArray<TSharedPtr<FJsonValue>>* Bindings=nullptr;
    if(!Manifest->TryGetArrayField(TEXT("runtime_bindings"),Bindings)||Bindings->Num()<4||Bindings->Num()>1024)
        return Fail(TEXT("missing/bounded source bindings; regenerate model"));
    TSet<FString> Seen,VehicleAssets;TMap<FString,FString> MapAssets;TMap<FString,int32> Roles;
    for(const auto& Value:*Bindings)
    {
        const auto Binding=Value->AsObject();FString Role,Path,ExpectedMd5,Sha,Asset;double Bytes=0;
        if(!Binding||!Binding->TryGetStringField(TEXT("role"),Role)||!Binding->TryGetStringField(TEXT("path"),Path)||
           !Binding->TryGetStringField(TEXT("md5"),ExpectedMd5)||ExpectedMd5.Len()!=32||
           !Binding->TryGetStringField(TEXT("sha256"),Sha)||Sha.Len()!=64||
           !Binding->TryGetNumberField(TEXT("bytes"),Bytes)||!FMath::IsFinite(Bytes)||Bytes<=0)return Fail(TEXT("malformed entry"));
        if(Role==TEXT("vehicle_rig"))Path=RigFile.IsEmpty()?FString():FPaths::ConvertRelativePathToFull(RigFile);
        else if(Role==TEXT("drive_profile"))Path=ProfileFile.IsEmpty()?FString():FPaths::ConvertRelativePathToFull(ProfileFile);
        else if(Role==TEXT("model_xml"))Path=XmlPath;
        else if(Role==TEXT("vehicle_collision")||Role==TEXT("map_collision"))
        {
            if(!Binding->TryGetStringField(TEXT("asset"),Asset)||!Asset.StartsWith(TEXT("/Game/RMSim/"))||Asset.Contains(TEXT("..")))
                return Fail(TEXT("invalid native collision asset"));
            FString Package=Asset;int32 Dot=INDEX_NONE;if(Package.FindChar(TCHAR('.'),Dot))Package.LeftInline(Dot);
            Path=FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::ProjectContentDir(),Package.Mid(6)+TEXT(".uasset")));
            if(Role==TEXT("vehicle_collision"))VehicleAssets.Add(Asset);else MapAssets.Add(Asset,Sha);
        }
        else if(Role!=TEXT("mujoco_backend")&&Role!=TEXT("model_mesh"))return Fail(TEXT("unsupported role: ")+Role);
        const FString Key=Role+TEXT("|")+(Asset.IsEmpty()?(Role==TEXT("model_mesh")?Path:TEXT("")):Asset);
        if(Seen.Contains(Key))return Fail(TEXT("duplicate role/asset: ")+Key);Seen.Add(Key);
        if(Path.IsEmpty())return Fail(TEXT("actual source path absent: ")+Role);
        if(FPaths::IsRelative(Path))Path=FPaths::Combine(FPaths::GetPath(XmlPath),Path);
        Path=FPaths::ConvertRelativePathToFull(Path);
        if(double(IFileManager::Get().FileSize(*Path))!=Bytes)return Fail(TEXT("source byte length changed: ")+Role+TEXT(" ")+Path);
        const FMD5Hash Hash=FMD5Hash::HashFile(*Path);
        if(!Hash.IsValid()||!LexToString(Hash).Equals(ExpectedMd5,ESearchCase::IgnoreCase))return Fail(TEXT("source content changed: ")+Role+TEXT(" ")+Path);
        ++Roles.FindOrAdd(Role);
    }
    for(const TCHAR* Role:{TEXT("vehicle_rig"),TEXT("drive_profile"),TEXT("mujoco_backend"),TEXT("model_xml")})
        if(Roles.FindRef(Role)!=1)return Fail(TEXT("required role absent: ")+FString(Role));
    TSharedPtr<FJsonObject> Rig;const TArray<TSharedPtr<FJsonValue>>* Parts=nullptr;
    if(!ReadContractJson(RigFile,Rig)||!Rig->TryGetArrayField(TEXT("parts"),Parts)||Parts->Num()!=VehicleAssets.Num())
        return Fail(TEXT("vehicle collision binding set differs from active rig"));
    for(const auto& Value:*Parts)
    {
        FString Asset;const auto Part=Value->AsObject();
        if(!Part||!Part->TryGetStringField(TEXT("collision_asset"),Asset)||!VehicleAssets.Contains(Asset))
            return Fail(TEXT("active vehicle collision asset has no binding"));
    }
    if(!DiagnosticPlane&&!ValidateSceneIdentity(Manifest,MapAssets,Error))return false;
    Count=Bindings->Num();return true;
}
}

struct FMujocoVehicleDynamics::FImplementation
{
#if RMSIM_WITH_MUJOCO
    mjModel* Model = nullptr;
    mjData* Data = nullptr;
    int32 RootJoint = -1;
    TMap<FString, int32> Bodies;
    struct FServo { int32 Joint = -1, Actuator = -1; double Kp = 0, Kd = 0, Limit = 0; };
    TMap<FString, FServo> Servos;
    ~FImplementation() { if (Data) mj_deleteData(Data); if (Model) mj_deleteModel(Model); }
#endif
};

FMujocoVehicleDynamics::FMujocoVehicleDynamics() : Impl(MakeUnique<FImplementation>()) {}
FMujocoVehicleDynamics::~FMujocoVehicleDynamics() = default;

bool FMujocoVehicleDynamics::Initialize(const FString& Filename, const TArray<FString>& RequiredBodies,
    const FString& RigFilename, const FString& DriveProfileFilename, FString& Error)
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    if (mj_version() != mjVERSION_HEADER) { Error = TEXT("MuJoCo runtime/header version mismatch"); return false; }
    if (Impl->Model) { Error = TEXT("MuJoCo model already initialized"); return false; }
    ModelPath = FPaths::ConvertRelativePathToFull(Filename);
    FString RequestedXml;
    if(FPaths::GetExtension(ModelPath).Equals(TEXT("xml"),ESearchCase::IgnoreCase))
    {RequestedXml=ModelPath;ModelPath=FPaths::Combine(FPaths::GetPath(ModelPath),TEXT("manifest.json"));}
    if (FPaths::GetExtension(ModelPath).Equals(TEXT("json"), ESearchCase::IgnoreCase))
    {
        FString Text, Xml, Schema;
        if (!FFileHelper::LoadFileToString(Text, *ModelPath) || Text.Len() > 8 * 1024 * 1024 ||
            !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), ManifestObject) || !ManifestObject ||
            !ManifestObject->TryGetStringField(TEXT("schema"), Schema) || Schema != TEXT("rmsim_mujoco_scene_v1") ||
            !ManifestObject->TryGetStringField(TEXT("model_xml"), Xml) || Xml.IsEmpty() ||
            !FPaths::IsRelative(Xml) || Xml.Contains(TEXT("..")) || Xml.Contains(TEXT(":")))
        { Error = TEXT("invalid MuJoCo scene manifest or model path"); return false; }
        ModelPath = FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::GetPath(ModelPath), Xml));
    }
    if(!ManifestObject){Error=TEXT("MuJoCo model needs its validated scene manifest");return false;}
    if(!RequestedXml.IsEmpty()&&!RequestedXml.Equals(ModelPath,ESearchCase::IgnoreCase))
    {Error=TEXT("requested MuJoCo XML differs from its scene manifest");return false;}
    bool DiagnosticPlane=false;
    if(!ManifestObject->TryGetBoolField(TEXT("diagnostic_plane_only"),DiagnosticPlane))
    {Error=TEXT("MuJoCo manifest must declare diagnostic_plane_only");return false;}
    if(DiagnosticPlane&&!FParse::Param(FCommandLine::Get(),TEXT("MujocoAllowDiagnosticPlane")))
    {Error=TEXT("diagnostic MuJoCo plane requires explicit -MujocoAllowDiagnosticPlane");return false;}
    if(!ValidateRuntimeBindings(ManifestObject,ModelPath,RigFilename,DriveProfileFilename,
        DiagnosticPlane,VerifiedRuntimeBindings,Error))return false;
    bPhysicsIdentityVerified=!DiagnosticPlane;
    if (!FPaths::FileExists(ModelPath)) { Error = TEXT("MuJoCo model XML missing: ") + ModelPath; return false; }
    char Buffer[2048] = {};
    Impl->Model = mj_loadXML(TCHAR_TO_UTF8(*ModelPath), nullptr, Buffer, UE_ARRAY_COUNT(Buffer));
    if (!Impl->Model) { Error = TEXT("MuJoCo model compilation failed: ") + FString(UTF8_TO_TCHAR(Buffer)); return false; }
    if(Impl->Model->nflex>0&&FString(UTF8_TO_TCHAR(mj_versionString()))!=TEXT("4.0.1+rmsim.static-world-flex.3"))
    {
        Error=TEXT("MuJoCo static flex scenes require runtime 4.0.1+rmsim.static-world-flex.3; active runtime: ")+
            FString(UTF8_TO_TCHAR(mj_versionString()));return false;
    }
    Impl->Data = mj_makeData(Impl->Model);
    if (!Impl->Data) { Error = TEXT("MuJoCo data allocation failed"); return false; }
    auto* M = Impl->Model;
    if (!FMath::IsFinite(double(M->opt.timestep)) || M->opt.timestep < .0001 || M->opt.timestep > .005)
    { Error = TEXT("MuJoCo step must be between 0.1 and 5 milliseconds"); return false; }
    Impl->RootJoint = mj_name2id(M, mjOBJ_JOINT, "chassis_free");
    if (Impl->RootJoint < 0 || M->jnt_type[Impl->RootJoint] != mjJNT_FREE)
    { Error = TEXT("MuJoCo vehicle must have a chassis_free free joint"); return false; }
    TSet<FString> Required;
    for (const FString& Name : RequiredBodies) Required.Add(Name);
    for (int32 Body = 1; Body < M->nbody; ++Body)
    {
        const char* Name = mj_id2name(M, mjOBJ_BODY, Body);
        if (!Name) continue;
        const FString Part(UTF8_TO_TCHAR(Name));
        if (Required.Contains(Part)) { Impl->Bodies.Add(Part, Body); OrderedBodyNames.Add(Part); }
    }
    if (Impl->Bodies.Num() != Required.Num() || !Impl->Bodies.Contains(TEXT("body")))
    { Error = TEXT("MuJoCo body names do not cover the imported vehicle rig"); return false; }
    if (M->jnt_bodyid[Impl->RootJoint] != Impl->Bodies[TEXT("body")])
    { Error = TEXT("MuJoCo free joint is not on the body root"); return false; }
    ModelMd5 = LexToString(FMD5Hash::HashFile(*ModelPath));
    mj_forward(M, Impl->Data);
    return ValidateState(Error);
#else
    Error = TEXT("This build has no native MuJoCo SDK; run the pinned dependency setup and rebuild");
    return false;
#endif
}

bool FMujocoVehicleDynamics::Reset(const FTransform& Pose, FString& Error, double InitialYaw, double InitialPitch)
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    if (!Impl->Model || !Impl->Data || Pose.ContainsNaN() || !FMath::IsFinite(InitialYaw) || !FMath::IsFinite(InitialPitch))
    { Error = TEXT("invalid MuJoCo reset"); return false; }
    auto* M = Impl->Model; auto* D = Impl->Data;
    const int32 YawJoint=mj_name2id(M,mjOBJ_JOINT,"j_yaw"),PitchJoint=mj_name2id(M,mjOBJ_JOINT,"j_pitch");
    if(YawJoint<0||PitchJoint<0||M->jnt_type[YawJoint]!=mjJNT_HINGE||M->jnt_type[PitchJoint]!=mjJNT_HINGE||
       (M->jnt_limited[PitchJoint]&&(InitialPitch<M->jnt_range[2*PitchJoint]||InitialPitch>M->jnt_range[2*PitchJoint+1])))
    { Error = TEXT("invalid MuJoCo reset gimbal joint/limit"); return false; }
    mj_resetData(M, D);
    const int32 Address = M->jnt_qposadr[Impl->RootJoint];
    const FVector P = Pose.GetLocation() / 100.; const FQuat Q = Pose.GetRotation().GetNormalized();
    D->qpos[Address] = P.X; D->qpos[Address + 1] = -P.Y; D->qpos[Address + 2] = P.Z;
    D->qpos[Address + 3] = Q.W; D->qpos[Address + 4] = -Q.X; D->qpos[Address + 5] = Q.Y; D->qpos[Address + 6] = -Q.Z;
    // Initial placement and explicit resets own qpos. Ordinary gimbal motion
    // uses actuator targets exclusively; the default R-reset angles remain zero.
    D->qpos[M->jnt_qposadr[YawJoint]]=FMath::UnwindRadians(InitialYaw);
    D->qpos[M->jnt_qposadr[PitchJoint]]=InitialPitch;
    RemainderSeconds = 0; FrameContacts.Reset(); MaximumPenetrationM = 0; LastFrameSteps = 0; ++ResetCount;
    mj_forward(M, D);
    return ValidateState(Error);
#else
    Error = TEXT("MuJoCo SDK unavailable"); return false;
#endif
}

bool FMujocoVehicleDynamics::ConfigureServo(const FString& Part, double Kp, double Kd, double Limit, FString& Error)
{
#if RMSIM_WITH_MUJOCO
    auto* M = Impl->Model;
    if (!M || !FMath::IsFinite(Kp) || !FMath::IsFinite(Kd) || !FMath::IsFinite(Limit) || Kp < 0 || Kd < 0 || Limit <= 0)
    { Error = TEXT("invalid MuJoCo servo configuration"); return false; }
    const int32 Joint = mj_name2id(M, mjOBJ_JOINT, TCHAR_TO_UTF8(*(TEXT("j_") + Part)));
    const int32 Actuator = mj_name2id(M, mjOBJ_ACTUATOR, TCHAR_TO_UTF8(*(TEXT("a_") + Part)));
    if (Joint < 0 || Actuator < 0 || M->jnt_type[Joint] != mjJNT_HINGE ||
        M->actuator_trntype[Actuator] != mjTRN_JOINT || M->actuator_trnid[2 * Actuator] != Joint ||
        !FMath::IsNearlyEqual(double(M->actuator_gear[6 * Actuator]), 1., 1.e-9))
    { Error = TEXT("MuJoCo hinge/actuator/gear mapping invalid: ") + Part; return false; }
    FImplementation::FServo Servo; Servo.Joint = Joint; Servo.Actuator = Actuator; Servo.Kp = Kp; Servo.Kd = Kd; Servo.Limit = Limit;
    Impl->Servos.Add(Part, Servo);
    M->actuator_gaintype[Actuator] = mjGAIN_FIXED; M->actuator_biastype[Actuator] = mjBIAS_AFFINE;
    M->actuator_gainprm[mjNGAIN * Actuator] = 1;
    M->actuator_ctrllimited[Actuator] = 0; M->actuator_forcelimited[Actuator] = 1;
    M->actuator_forcerange[2 * Actuator] = -Limit; M->actuator_forcerange[2 * Actuator + 1] = Limit;
    return SetServoTarget(Part, 0, 0, true);
#else
    Error = TEXT("MuJoCo SDK unavailable"); return false;
#endif
}

bool FMujocoVehicleDynamics::SetServoTarget(const FString& Part, double Position, double Velocity, bool Wrap)
{
#if RMSIM_WITH_MUJOCO
    const auto* S = Impl->Servos.Find(Part);
    if (!S || !FMath::IsFinite(Position) || !FMath::IsFinite(Velocity)) return false;
    auto* M = Impl->Model; auto* D = Impl->Data;
    const double Current = D->qpos[M->jnt_qposadr[S->Joint]];
    const double Target = Wrap ? Current + FMath::FindDeltaAngleRadians(Current, Position) : Position;
    M->actuator_biastype[S->Actuator] = mjBIAS_AFFINE;
    auto* Bias = M->actuator_biasprm + mjNBIAS * S->Actuator;
    Bias[0] = 0; Bias[1] = -S->Kp; Bias[2] = -S->Kd;
    D->ctrl[S->Actuator] = S->Kp * Target + S->Kd * Velocity;
    return true;
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::SetTorque(const FString& Part, double Torque)
{
#if RMSIM_WITH_MUJOCO
    const auto* S = Impl->Servos.Find(Part);
    if (!S || !FMath::IsFinite(Torque)) return false;
    Impl->Model->actuator_biastype[S->Actuator] = mjBIAS_NONE;
    Impl->Data->ctrl[S->Actuator] = FMath::Clamp(Torque, -S->Limit, S->Limit);
    return true;
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::SetForceLimit(const FString& Part, double Limit)
{
#if RMSIM_WITH_MUJOCO
    auto* S = Impl->Servos.Find(Part);
    if (!S || !FMath::IsFinite(Limit) || Limit <= 0) return false;
    S->Limit = Limit;
    Impl->Model->actuator_forcerange[2 * S->Actuator] = -Limit;
    Impl->Model->actuator_forcerange[2 * S->Actuator + 1] = Limit;
    return true;
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::GetJoint(const FString& Part, double& Position, double& Velocity) const
{
    Position = Velocity = 0;
#if RMSIM_WITH_MUJOCO
    if (!Impl->Model || !Impl->Data) return false;
    const auto* M = Impl->Model; const auto* D = Impl->Data;
    const int32 Joint = mj_name2id(M, mjOBJ_JOINT, TCHAR_TO_UTF8(*(TEXT("j_") + Part)));
    if (Joint < 0 || (M->jnt_type[Joint] != mjJNT_HINGE && M->jnt_type[Joint] != mjJNT_SLIDE)) return false;
    Position = D->qpos[M->jnt_qposadr[Joint]]; Velocity = D->qvel[M->jnt_dofadr[Joint]];
    return FMath::IsFinite(Position) && FMath::IsFinite(Velocity);
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::GetBody(const FString& Part, FMujocoBodyState& Out) const
{
#if RMSIM_WITH_MUJOCO
    const int32* Id = Impl->Bodies.Find(Part);
    if (!Id || !Impl->Data) return false;
    const auto* M = Impl->Model; auto* D = Impl->Data;
    const int32 Body = *Id;
    Out.World = FTransform(QuaternionToUE(D->xquat + 4 * Body), PolarToUE(D->xpos + 3 * Body, 100.));
    TArray<mjtNum> JacobianP, JacobianR; JacobianP.SetNumUninitialized(3 * M->nv); JacobianR.SetNumUninitialized(3 * M->nv);
    mj_jacBody(M, D, JacobianP.GetData(), JacobianR.GetData(), Body);
    mjtNum Velocity[3], Angular[3];
    mju_mulMatVec(Velocity, JacobianP.GetData(), D->qvel, 3, M->nv);
    mju_mulMatVec(Angular, JacobianR.GetData(), D->qvel, 3, M->nv);
    Out.VelocityCmS = PolarToUE(Velocity, 100.); Out.AngularVelocityRadS = AxialToUE(Angular);
    Out.CentreOfMassCm = PolarToUE(D->xipos + 3 * Body, 100.);
    Out.MassKg = M->body_mass[Body];
    Out.PrincipalInertiaKgCm2 = FVector(M->body_inertia[3 * Body], M->body_inertia[3 * Body + 1], M->body_inertia[3 * Body + 2]) * 10000.;
    return !Out.World.ContainsNaN() && !Out.VelocityCmS.ContainsNaN() && !Out.AngularVelocityRadS.ContainsNaN();
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::ValidateState(FString& Error) const
{
#if RMSIM_WITH_MUJOCO
    if (!Impl->Model || !Impl->Data) { Error = TEXT("MuJoCo model/data unavailable"); return false; }
    const auto* M = Impl->Model; const auto* D = Impl->Data;
    for (int32 I = 0; I < M->nq; ++I) if (!FMath::IsFinite(double(D->qpos[I]))) { Error = TEXT("nonfinite MuJoCo position"); return false; }
    for (int32 I = 0; I < M->nv; ++I) if (!FMath::IsFinite(double(D->qvel[I])) || !FMath::IsFinite(double(D->qacc[I]))) { Error = TEXT("nonfinite MuJoCo velocity/acceleration"); return false; }
    for (int32 Warning : {int32(mjWARN_BADQPOS), int32(mjWARN_BADQVEL), int32(mjWARN_BADQACC), int32(mjWARN_BADCTRL)})
        if (D->warning[Warning].number) { Error = FString::Printf(TEXT("MuJoCo numerical warning %d"), Warning); return false; }
    return true;
#else
    Error = TEXT("MuJoCo SDK unavailable"); return false;
#endif
}

void FMujocoVehicleDynamics::CollectContacts(const TMap<FString, FVector>& BeforeVelocities)
{
#if RMSIM_WITH_MUJOCO
    const auto* M = Impl->Model; const auto* D = Impl->Data;
    for (int32 I = 0; I < D->ncon; ++I)
    {
        const mjContact& C = D->contact[I];
        if (C.efc_address < 0) continue;
        const int32 Body0 = C.geom[0] >= 0 ? M->geom_bodyid[C.geom[0]] : 0;
        const int32 Body1 = C.geom[1] >= 0 ? M->geom_bodyid[C.geom[1]] : 0;
        FString Part; int32 Side = -1;
        for (int32 Candidate = 0; Candidate < 2; ++Candidate)
        {
            const int32 Body = Candidate == 0 ? Body0 : Body1;
            const char* Name = Body > 0 ? mj_id2name(M, mjOBJ_BODY, Body) : nullptr;
            if (Name && Impl->Bodies.Contains(FString(UTF8_TO_TCHAR(Name)))) { Part = UTF8_TO_TCHAR(Name); Side = Candidate; break; }
        }
        if (Side < 0) continue;
        const int32 OtherBody = Side == 0 ? Body1 : Body0;
        const char* OtherBodyName = OtherBody > 0 ? mj_id2name(M, mjOBJ_BODY, OtherBody) : nullptr;
        if (OtherBodyName && Impl->Bodies.Contains(FString(UTF8_TO_TCHAR(OtherBodyName)))) continue;
        mjtNum Force[6] = {}; mj_contactForce(M, D, I, Force);
        if (!FMath::IsFinite(double(Force[0])) || FMath::Abs(double(Force[0])) < 1.e-8) continue;
        FMujocoContactSample Sample; Sample.BodyName = Part;
        Sample.OtherGeom = C.geom[1 - Side]; Sample.OtherFlex = C.flex[1 - Side];
        const char* OtherName = Sample.OtherGeom >= 0 ? mj_id2name(M, mjOBJ_GEOM, Sample.OtherGeom) :
            Sample.OtherFlex >= 0 ? mj_id2name(M, mjOBJ_FLEX, Sample.OtherFlex) : nullptr;
        Sample.OtherName = OtherName ? UTF8_TO_TCHAR(OtherName) : FString::Printf(TEXT("geom_%d_flex_%d"), Sample.OtherGeom, Sample.OtherFlex);
        Sample.PointCm = PolarToUE(C.pos, 100.);
        // MuJoCo defines the contact normal from geom[0] toward geom[1].
        // The owned vehicle side determines the direction even when a compound
        // body's centre of mass lies beyond this local contact surface.
        Sample.IntoObstacleNormal = PolarToUE(C.frame).GetSafeNormal() * (Side == 0 ? 1. : -1.);
        Sample.PreImpactVelocityCmS = BeforeVelocities.FindRef(Part);
        Sample.ForceN = FMath::Abs(double(Force[0])); Sample.ImpulseNs = Sample.ForceN * M->opt.timestep;
        Sample.TimeSeconds = D->time;
        MaximumPenetrationM = FMath::Max(MaximumPenetrationM, FMath::Max(0., -double(C.dist)));
        FrameContacts.Add(MoveTemp(Sample));
    }
#endif
}

bool FMujocoVehicleDynamics::Step(double Seconds, FString& Error)
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    if (!Impl->Model || !Impl->Data || !FMath::IsFinite(Seconds) || Seconds < 0 || Seconds > 1.)
    { Error = TEXT("invalid MuJoCo elapsed time; no elapsed simulation time discarded"); return false; }
    const double Dt = Impl->Model->opt.timestep;
    RemainderSeconds += Seconds;
    const int32 Count = FMath::FloorToInt((RemainderSeconds + Dt * 1.e-7) / Dt);
    if (Count > 10000) { Error = TEXT("MuJoCo substep budget exceeded"); return false; }
    FrameContacts.Reset(); LastFrameSteps = Count;
    for (int32 I = 0; I < Count; ++I)
    {
        TMap<FString, FVector> Before;
        for (const FString& Part : OrderedBodyNames)
        {
            FMujocoBodyState State;
            if (GetBody(Part, State)) Before.Add(Part, State.VelocityCmS + FVector::CrossProduct(State.AngularVelocityRadS, State.CentreOfMassCm - State.World.GetLocation()));
        }
        const double Previous = Impl->Data->time;
        mj_step(Impl->Model, Impl->Data);
        if (!ValidateState(Error) || Impl->Data->time <= Previous)
        { if (Error.IsEmpty()) Error = TEXT("MuJoCo time failed to advance"); return false; }
        CollectContacts(Before); ++TotalSteps;
    }
    RemainderSeconds -= Count * Dt;
    if (FMath::Abs(RemainderSeconds) < Dt * 1.e-7) RemainderSeconds = 0;
    // mj_step integrates qpos after computing derived body transforms. Refresh
    // once so visuals, queries and sensors all observe the same completed state.
    mj_forward(Impl->Model, Impl->Data);
    return ValidateState(Error);
#else
    Error = TEXT("MuJoCo SDK unavailable"); return false;
#endif
}

double FMujocoVehicleDynamics::Time() const
{
#if RMSIM_WITH_MUJOCO
    return Impl->Data ? Impl->Data->time : 0;
#else
    return 0;
#endif
}
double FMujocoVehicleDynamics::Timestep() const
{
#if RMSIM_WITH_MUJOCO
    return Impl->Model ? Impl->Model->opt.timestep : 0;
#else
    return 0;
#endif
}

FVector FMujocoVehicleDynamics::GravityCmS2() const
{
#if RMSIM_WITH_MUJOCO
    return Impl->Model?PolarToUE(Impl->Model->opt.gravity,100.):FVector::ZeroVector;
#else
    return FVector::ZeroVector;
#endif
}

TSharedRef<FJsonObject> FMujocoVehicleDynamics::Diagnostics() const
{
    auto J = MakeShared<FJsonObject>();
    J->SetStringField(TEXT("backend"), TEXT("mujoco")); J->SetStringField(TEXT("model_xml"), ModelPath); J->SetStringField(TEXT("model_md5"), ModelMd5);
    J->SetNumberField(TEXT("runtime_binding_count"),VerifiedRuntimeBindings);
    J->SetBoolField(TEXT("runtime_bindings_verified"),VerifiedRuntimeBindings>0);
    J->SetBoolField(TEXT("physics_identity_verified"),bPhysicsIdentityVerified);
    J->SetStringField(TEXT("runtime_binding_method"),TEXT("active-source MD5; physical map fields; SHA256 provenance retained"));
    J->SetStringField(TEXT("authority"), TEXT("native_mj_step; UE_query_only_visual_followers"));
    J->SetNumberField(TEXT("time_s"), Time()); J->SetNumberField(TEXT("step_s"), Timestep());
    J->SetNumberField(TEXT("steps"), double(TotalSteps)); J->SetNumberField(TEXT("reset_count"), double(ResetCount));
    J->SetNumberField(TEXT("step_count"),double(TotalSteps));J->SetNumberField(TEXT("pose_override_count"),double(ResetCount>0?ResetCount-1:0));
    J->SetNumberField(TEXT("contact_count"),FrameContacts.Num());
    double PeakNormalForce=0;for(const auto& C:FrameContacts)PeakNormalForce=FMath::Max(PeakNormalForce,C.ForceN);
    J->SetNumberField(TEXT("normal_force_N"),PeakNormalForce);
    J->SetNumberField(TEXT("last_frame_substeps"), LastFrameSteps); J->SetNumberField(TEXT("unconsumed_time_s"), RemainderSeconds);
    J->SetNumberField(TEXT("maximum_contact_penetration_m"), MaximumPenetrationM); J->SetNumberField(TEXT("last_frame_contact_samples"), FrameContacts.Num());
#if RMSIM_WITH_MUJOCO
    if (Impl->Model)
    {
        J->SetStringField(TEXT("version"), UTF8_TO_TCHAR(mj_versionString()));
        J->SetNumberField(TEXT("nq"), Impl->Model->nq); J->SetNumberField(TEXT("nv"), Impl->Model->nv);
        J->SetNumberField(TEXT("bodies"), Impl->Model->nbody); J->SetNumberField(TEXT("geoms"), Impl->Model->ngeom); J->SetNumberField(TEXT("flexes"), Impl->Model->nflex);
        J->SetNumberField(TEXT("active_contacts"), Impl->Data->ncon);
        TArray<TSharedPtr<FJsonValue>> Warnings;
        int32 WarningCount=0;
        for (int32 I = 0; I < mjNWARNING; ++I) {Warnings.Add(MakeShared<FJsonValueNumber>(Impl->Data->warning[I].number));WarningCount+=Impl->Data->warning[I].number;}
        J->SetArrayField(TEXT("warning_counts"), Warnings);
        J->SetNumberField(TEXT("warning_count"),WarningCount);
        double Mass = 0; for (const auto& Entry : Impl->Bodies) Mass += Impl->Model->body_mass[Entry.Value];
        J->SetNumberField(TEXT("vehicle_total_mass_kg"), Mass);
    }
#endif
    return J;
}
