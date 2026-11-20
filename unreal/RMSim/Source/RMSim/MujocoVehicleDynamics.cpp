#include "MujocoVehicleDynamics.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/SecureHash.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "IPAddress.h"
#include "SocketSubsystem.h"
#include "Sockets.h"

#ifndef RMSIM_WITH_MUJOCO
#define RMSIM_WITH_MUJOCO 0
#endif
#if RMSIM_WITH_MUJOCO
#include <mujoco/mujoco.h>
#endif

DECLARE_LOG_CATEGORY_EXTERN(LogRMSim, Log, All);

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

#if RMSIM_WITH_MUJOCO
// One-way display telemetry. This object never receives packets or writes mjModel/mjData.
class FMujocoViewerPublisher
{
public:
    FMujocoViewerPublisher()=default;

    static TUniquePtr<FMujocoViewerPublisher> CreateIfRequested(
        const TSharedPtr<FJsonObject>& Manifest,const mjModel* Model)
    {
        FString PortText,Token;
        const bool HasPort=FParse::Value(FCommandLine::Get(),TEXT("MujocoViewerPort="),PortText);
        const bool HasToken=FParse::Value(FCommandLine::Get(),TEXT("MujocoViewerToken="),Token);
        if(!HasPort&&!HasToken)return nullptr;
        bool Digits=!PortText.IsEmpty()&&PortText.Len()<=5;
        for(TCHAR C:PortText)Digits=Digits&&C>=TEXT('0')&&C<=TEXT('9');
        const int32 Port=Digits?FCString::Atoi(*PortText):0;
        FString ModelSha,PhysicsSha;
        const int64 Scalars=Model?int64(Model->nq)+Model->nv+Model->na+Model->nu+7*int64(Model->nbody):0;
        if(!HasPort||!HasToken||!Digits||Port<1024||Port>65535||!IsHex(Token,32)||
           !Manifest||!Manifest->TryGetStringField(TEXT("model_sha256"),ModelSha)||!IsHex(ModelSha,64)||
           !Manifest->TryGetStringField(TEXT("physics_identity_sha256"),PhysicsSha)||!IsHex(PhysicsSha,64)||
           !Model||Model->nbody<1||Scalars<1||Scalars>1024)
        {
            UE_LOG(LogRMSim,Warning,TEXT("MUJOCO_VIEWER_DISABLED reason=invalid_config_or_model_identity"));
            return nullptr;
        }
        auto Result=MakeUnique<FMujocoViewerPublisher>();
        Result->Token=Token.ToLower();Result->ModelSha=ModelSha.ToLower();Result->PhysicsSha=PhysicsSha.ToLower();
        Result->Sockets=ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
        if(Result->Sockets)
        {
            Result->Socket=Result->Sockets->CreateSocket(NAME_DGram,TEXT("RMSim read-only MuJoCo viewer"),false);
            Result->Destination=Result->Sockets->CreateInternetAddr();
            auto Local=Result->Sockets->CreateInternetAddr();
            bool DestinationValid=false,LocalValid=false;
            Result->Destination->SetIp(TEXT("127.0.0.1"),DestinationValid);Result->Destination->SetPort(Port);
            Local->SetIp(TEXT("127.0.0.1"),LocalValid);Local->SetPort(0);
            if(Result->Socket&&DestinationValid&&LocalValid&&Result->Socket->SetNonBlocking(true)&&Result->Socket->Bind(*Local))
            {
                int32 Actual=0;Result->Socket->SetSendBufferSize(64*1024,Actual);
                Result->bReady=true;
                UE_LOG(LogRMSim,Display,TEXT("MUJOCO_VIEWER_PUBLISHER_READY address=127.0.0.1 port=%d rate_hz=30 max_packet_bytes=61440 nq=%d nv=%d na=%d nu=%d nbody=%d"),
                    Port,int32(Model->nq),int32(Model->nv),int32(Model->na),int32(Model->nu),int32(Model->nbody));
                return Result;
            }
        }
        UE_LOG(LogRMSim,Warning,TEXT("MUJOCO_VIEWER_DISABLED reason=socket_initialization"));
        return nullptr;
    }

    ~FMujocoViewerPublisher()
    {
        if(Socket){Socket->Close();Sockets->DestroySocket(Socket);}
        if(bReady)
            UE_LOG(LogRMSim,Display,TEXT("MUJOCO_VIEWER_PUBLISHER_STOP sent=%llu errors=%llu skipped_slots=%llu sequence=%llu"),
                Sent,Errors,SkippedSlots,Sequence);
    }

    void Publish(const mjModel* Model,const mjData* Data,uint64 Generation)
    {
        check(IsInGameThread());
        constexpr double Rate=30.,MaxExactInteger=9007199254740991.;
        const double Time=Data->time;
        if(!FMath::IsFinite(Time)||Time<0||Time*Rate>=MaxExactInteger||
           double(Generation)>=MaxExactInteger||double(Sequence)>=MaxExactInteger)
        {ReportError(TEXT("invalid_time_or_counter"));return;}
        // Integer slots avoid accumulated period drift. Only the current completed
        // solver frame is sent; late slots are never backfilled or extrapolated.
        const uint64 Slot=uint64(FMath::FloorToDouble(Time*Rate+1.e-9));
        if(Generation==LastGeneration&&Slot<=LastSlot)return;
        if(Generation==LastGeneration&&Slot>LastSlot+1)SkippedSlots+=Slot-LastSlot-1;
        LastGeneration=Generation;LastSlot=Slot;
        auto State=MakeShared<FJsonObject>();
        State->SetStringField(TEXT("schema"),TEXT("rmsim_mujoco_viewer_state_v1"));
        State->SetStringField(TEXT("session_token"),Token);
        State->SetNumberField(TEXT("generation"),double(Generation));
        State->SetNumberField(TEXT("sequence"),double(++Sequence));
        State->SetNumberField(TEXT("sim_time_s"),Time);
        State->SetStringField(TEXT("model_sha256"),ModelSha);
        State->SetStringField(TEXT("physics_identity_sha256"),PhysicsSha);
        State->SetNumberField(TEXT("nq"),Model->nq);State->SetNumberField(TEXT("nv"),Model->nv);
        State->SetNumberField(TEXT("na"),Model->na);State->SetNumberField(TEXT("nu"),Model->nu);
        State->SetNumberField(TEXT("nbody"),Model->nbody);
        // Native right-handed SI, untouched qpos layout and wxyz quaternions.
        // Every array is copied here on the GT from this same final-forward endpoint.
        if(!AddArray(*State,TEXT("qpos"),Data->qpos,int32(Model->nq),1)||
           !AddArray(*State,TEXT("qvel"),Data->qvel,int32(Model->nv),1)||
           !AddArray(*State,TEXT("act"),Data->act,int32(Model->na),1)||
           !AddArray(*State,TEXT("ctrl"),Data->ctrl,int32(Model->nu),1)||
           !AddArray(*State,TEXT("body_xpos"),Data->xpos,int32(Model->nbody),3)||
           !AddArray(*State,TEXT("body_xquat"),Data->xquat,int32(Model->nbody),4))
        {ReportError(TEXT("nonfinite_snapshot"));return;}
        FString Json;
        if(!FJsonSerializer::Serialize(State,TJsonWriterFactory<TCHAR,TCondensedJsonPrintPolicy<TCHAR>>::Create(&Json)))
        {ReportError(TEXT("serialization"));return;}
        const FTCHARToUTF8 Utf8(*Json);
        if(Utf8.Length()>60*1024){ReportError(TEXT("packet_size"));return;}
        int32 BytesSent=0;
        if(!Socket->SendTo(reinterpret_cast<const uint8*>(Utf8.Get()),Utf8.Length(),BytesSent,*Destination)||
           BytesSent!=Utf8.Length())
        {ReportError(TEXT("send"),int32(Sockets->GetLastErrorCode()));return;}
        ++Sent;
    }

private:
    static bool IsHex(const FString& Text,int32 Length)
    {
        if(Text.Len()!=Length)return false;
        for(TCHAR C:Text)if(!((C>=TEXT('0')&&C<=TEXT('9'))||(C>=TEXT('a')&&C<=TEXT('f'))||(C>=TEXT('A')&&C<=TEXT('F'))))return false;
        return true;
    }

    static bool AddArray(FJsonObject& State,const TCHAR* Name,const mjtNum* Values,int32 Rows,int32 Columns)
    {
        TArray<TSharedPtr<FJsonValue>> Array;Array.Reserve(Rows);
        for(int32 Row=0;Row<Rows;++Row)
        {
            TArray<TSharedPtr<FJsonValue>> Tuple;
            if(Columns>1)Tuple.Reserve(Columns);
            for(int32 Column=0;Column<Columns;++Column)
            {
                const double Value=Values[Row*Columns+Column];
                if(!FMath::IsFinite(Value))return false;
                if(Columns==1)Array.Add(MakeShared<FJsonValueNumber>(Value));
                else Tuple.Add(MakeShared<FJsonValueNumber>(Value));
            }
            if(Columns>1)Array.Add(MakeShared<FJsonValueArray>(MoveTemp(Tuple)));
        }
        State.SetArrayField(Name,MoveTemp(Array));return true;
    }

    void ReportError(const TCHAR* Reason,int32 Code=0)
    {
        ++Errors;const double Wall=FPlatformTime::Seconds();
        if(LastErrorWall<0||Wall-LastErrorWall>=5.)
        {
            LastErrorWall=Wall;
            UE_LOG(LogRMSim,Warning,TEXT("MUJOCO_VIEWER_PUBLISH_ERROR reason=%s code=%d errors=%llu solver_unaffected=1"),Reason,Code,Errors);
        }
    }

    ISocketSubsystem* Sockets=nullptr;
    FSocket* Socket=nullptr;
    TSharedPtr<FInternetAddr> Destination;
    FString Token,ModelSha,PhysicsSha;
    uint64 Sequence=0,Sent=0,Errors=0,SkippedSlots=0,LastGeneration=MAX_uint64,LastSlot=0;
    double LastErrorWall=-1;
    bool bReady=false;
};
#endif
}

struct FMujocoVehicleDynamics::FImplementation
{
#if RMSIM_WITH_MUJOCO
    mjModel* Model = nullptr;
    mjData* Data = nullptr;
    TUniquePtr<FMujocoViewerPublisher> ViewerPublisher;
    int32 RootJoint = -1;
    TMap<FString, int32> Bodies;
    TArray<int32> OrderedBodyIds;
    TArray<FString> BodyNamesById;
    TArray<mjtNum> JacobianP, JacobianR;
    TArray<FVector> BeforeVelocities;
    TArray<FVector2D> BodyDamping;
    TArray<mjtNum> SavedAppliedWrenches;
    double LastDampingPowerW = 0;
    TArray<FMujocoBodyState> BodyCache;
    TArray<uint64> BodyCacheGeneration;
    uint64 BodyStateGeneration = 1;
    struct FSensorMotion
    {
        FTransform World = FTransform::Identity;
        FVector VelocityCmS = FVector::ZeroVector, AngularVelocityRadS = FVector::ZeroVector;
    };
    struct FSensorHistorySample { double Time = 0; FSensorMotion Parent[2]; };
    TArray<FSensorHistorySample> SensorHistory;
    int32 SensorHistoryCount = 0, SensorHistoryWrite = 0, CachedHistoryLower = INDEX_NONE;
    double CachedHistoryTime = -1;
    uint64 HistoryQueries = 0, HistoryRangeMisses = 0, HistoryParentMisses = 0, HistoryInvalidTimeMisses = 0, HistoryInvalidMountMisses = 0, HistoryOverwrites = 0;
    struct FTiming { double Seconds = 0; uint64 Calls = 0; };
    FTiming Timings[int32(EMujocoTimingScope::Count)];
    double TimingWindowStart = 0;
    struct FServo { int32 Joint = -1, Actuator = -1; double Kp = 0, Kd = 0, Limit = 0; };
    TMap<FString, FServo> Servos;
    ~FImplementation() { ViewerPublisher.Reset(); if (Data) mj_deleteData(Data); if (Model) mj_deleteModel(Model); }
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
    bTimingEnabled = FParse::Param(FCommandLine::Get(), TEXT("ProfileMujocoTiming"));
    Impl->BodyNamesById.SetNum(M->nbody);
    Impl->BeforeVelocities.SetNumZeroed(M->nbody);
    Impl->BodyDamping.Init(FVector2D::ZeroVector, M->nbody);
    Impl->SavedAppliedWrenches.SetNumUninitialized(6 * M->nbody);
    Impl->BodyCache.SetNum(M->nbody);
    Impl->BodyCacheGeneration.SetNumZeroed(M->nbody);
    Impl->JacobianP.SetNumUninitialized(3 * M->nv);
    Impl->JacobianR.SetNumUninitialized(3 * M->nv);
    // At least half a second plus both interpolation endpoints at the actual native step.
    Impl->SensorHistory.SetNum(FMath::CeilToInt(.5 / double(M->opt.timestep)) + 2);
    TSet<FString> Required;
    for (const FString& Name : RequiredBodies) Required.Add(Name);
    for (int32 Body = 1; Body < M->nbody; ++Body)
    {
        const char* Name = mj_id2name(M, mjOBJ_BODY, Body);
        if (!Name) continue;
        const FString Part(UTF8_TO_TCHAR(Name));
        if (Required.Contains(Part))
        {
            Impl->Bodies.Add(Part, Body); OrderedBodyNames.Add(Part);
            Impl->OrderedBodyIds.Add(Body); Impl->BodyNamesById[Body] = Part;
        }
    }
    if (Impl->Bodies.Num() != Required.Num() || !Impl->Bodies.Contains(TEXT("body")) || !Impl->Bodies.Contains(TEXT("yaw")))
    { Error = TEXT("MuJoCo body names do not cover the imported vehicle rig"); return false; }
    if (M->jnt_bodyid[Impl->RootJoint] != Impl->Bodies[TEXT("body")])
    { Error = TEXT("MuJoCo free joint is not on the body root"); return false; }
    ModelMd5 = LexToString(FMD5Hash::HashFile(*ModelPath));
    mj_forward(M, Impl->Data);
    if(!ValidateState(Error)||!CaptureSensorHistory(Error))return false;
    Impl->ViewerPublisher=FMujocoViewerPublisher::CreateIfRequested(ManifestObject,M);
    return true;
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
    Impl->SensorHistoryCount = Impl->SensorHistoryWrite = 0;
    Impl->CachedHistoryLower = INDEX_NONE; Impl->CachedHistoryTime = -1;
    ++Impl->BodyStateGeneration;
    for (auto& Timing : Impl->Timings) Timing = {};
    Impl->TimingWindowStart = 0; Impl->LastDampingPowerW = 0;
    mj_forward(M, D);
    return ValidateState(Error) && CaptureSensorHistory(Error);
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


bool FMujocoVehicleDynamics::ConfigureBodyDamping(const FString& Part, double LinearPerSecond, double AngularPerSecond, FString& Error)
{
#if RMSIM_WITH_MUJOCO
    const int32* Body = Impl->Bodies.Find(Part);
    if (!Body || !FMath::IsFinite(LinearPerSecond) || !FMath::IsFinite(AngularPerSecond) ||
        LinearPerSecond < 0 || AngularPerSecond < 0 || LinearPerSecond > 20 || AngularPerSecond > 20)
    { Error = TEXT("invalid MuJoCo source body damping: ") + Part; return false; }
    Impl->BodyDamping[*Body] = FVector2D(LinearPerSecond, AngularPerSecond);
    return true;
#else
    Error = TEXT("MuJoCo SDK unavailable"); return false;
#endif
}

void FMujocoVehicleDynamics::ApplyBodyDamping(int32 Body, const FMujocoBodyState& State)
{
#if RMSIM_WITH_MUJOCO
    FScopedMujocoTiming Timing(*this, EMujocoTimingScope::Damping);
    const FVector2D Coefficients = Impl->BodyDamping[Body];
    if (Coefficients.X == 0 && Coefficients.Y == 0) return;
    const auto* M = Impl->Model; auto* D = Impl->Data;
    // UE 5.8 PBDRigidsEvolutionGBF applies max(0,1-c*dt) to free-body
    // velocity. F=-m*c*v and tau=-c*I_world*omega preserve those 1/s
    // coefficients as physical inputs at each native step. Other force and
    // constraint integration phases remain owned by the chosen solver.
    const FVector VelocityUE = Impl->BeforeVelocities[Body] / 100.;
    const FVector AngularUE = State.AngularVelocityRadS;
    const mjtNum Velocity[3] = {VelocityUE.X, -VelocityUE.Y, VelocityUE.Z};
    const mjtNum Angular[3] = {-AngularUE.X, AngularUE.Y, -AngularUE.Z};
    mjtNum PrincipalAngular[3], PrincipalMomentum[3], WorldMomentum[3];
    const mjtNum* InertialRotation = D->ximat + 9 * Body;
    mju_mulMatTVec3(PrincipalAngular, InertialRotation, Angular);
    for (int32 Axis = 0; Axis < 3; ++Axis)
        PrincipalMomentum[Axis] = M->body_inertia[3 * Body + Axis] * PrincipalAngular[Axis];
    mju_mulMatVec3(WorldMomentum, InertialRotation, PrincipalMomentum);
    for (int32 Axis = 0; Axis < 3; ++Axis)
    {
        const mjtNum Force = -M->body_mass[Body] * Coefficients.X * Velocity[Axis];
        const mjtNum Torque = -Coefficients.Y * WorldMomentum[Axis];
        // MuJoCo xfrc_applied is a world-space wrench at this body's COM.
        D->xfrc_applied[6 * Body + Axis] += Force;
        D->xfrc_applied[6 * Body + 3 + Axis] += Torque;
        Impl->LastDampingPowerW -= Force * Velocity[Axis] + Torque * Angular[Axis];
    }
#endif
}

bool FMujocoVehicleDynamics::PrepareBodyDamping(FString& Error)
{
#if RMSIM_WITH_MUJOCO
    // Preserve caller-owned input exactly, including nonzero external forces.
    FMemory::Memcpy(Impl->SavedAppliedWrenches.GetData(), Impl->Data->xfrc_applied,
        sizeof(mjtNum) * 6 * Impl->Model->nbody);
    Impl->LastDampingPowerW = 0;
    for (int32 PartIndex = 0; PartIndex < OrderedBodyNames.Num(); ++PartIndex)
    {
        FMujocoBodyState State;
        if (!GetBody(OrderedBodyNames[PartIndex], State))
        {
            FMemory::Memcpy(Impl->Data->xfrc_applied, Impl->SavedAppliedWrenches.GetData(), sizeof(mjtNum) * 6 * Impl->Model->nbody);
            Error = TEXT("MuJoCo body state unavailable for damping/contact"); return false;
        }
        const int32 Body = Impl->OrderedBodyIds[PartIndex];
        Impl->BeforeVelocities[Body] = State.VelocityCmS +
            FVector::CrossProduct(State.AngularVelocityRadS, State.CentreOfMassCm - State.World.GetLocation());
        ApplyBodyDamping(Body, State);
    }
    return true;
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
    check(IsInGameThread());
    FScopedMujocoTiming Timing(*this, EMujocoTimingScope::GetBody);
#if RMSIM_WITH_MUJOCO
    const int32* Id = Impl->Bodies.Find(Part);
    if (!Id || !Impl->Data) return false;
    const auto* M = Impl->Model; auto* D = Impl->Data;
    const int32 Body = *Id;
    if (Impl->BodyCacheGeneration[Body] == Impl->BodyStateGeneration)
    { Out = Impl->BodyCache[Body]; return true; }
    Out.World = FTransform(QuaternionToUE(D->xquat + 4 * Body), PolarToUE(D->xpos + 3 * Body, 100.));
    {
        FScopedMujocoTiming JacobianTiming(*this, EMujocoTimingScope::Jacobian);
        // Body-origin velocity at this completed qpos, using the latest qvel.
        // cvel/mj_objectVelocity may belong to the preceding integration phase.
        mj_jacBody(M, D, Impl->JacobianP.GetData(), Impl->JacobianR.GetData(), Body);
        mjtNum Velocity[3], Angular[3];
        mju_mulMatVec(Velocity, Impl->JacobianP.GetData(), D->qvel, 3, M->nv);
        mju_mulMatVec(Angular, Impl->JacobianR.GetData(), D->qvel, 3, M->nv);
        Out.VelocityCmS = PolarToUE(Velocity, 100.); Out.AngularVelocityRadS = AxialToUE(Angular);
    }
    Out.CentreOfMassCm = PolarToUE(D->xipos + 3 * Body, 100.);
    Out.MassKg = M->body_mass[Body];
    Out.PrincipalInertiaKgCm2 = FVector(M->body_inertia[3 * Body], M->body_inertia[3 * Body + 1], M->body_inertia[3 * Body + 2]) * 10000.;
    if (Out.World.ContainsNaN() || Out.VelocityCmS.ContainsNaN() || Out.AngularVelocityRadS.ContainsNaN()) return false;
    Impl->BodyCache[Body] = Out; Impl->BodyCacheGeneration[Body] = Impl->BodyStateGeneration;
    return true;
#else
    return false;
#endif
}


FScopedMujocoTiming::FScopedMujocoTiming(const FMujocoVehicleDynamics& InOwner, EMujocoTimingScope InScope)
    : Owner(InOwner), Scope(InScope)
{
    if (Owner.IsTimingEnabled()) StartedSeconds = FPlatformTime::Seconds();
}

FScopedMujocoTiming::~FScopedMujocoTiming()
{
    if (StartedSeconds >= 0) Owner.RecordTiming(Scope, FPlatformTime::Seconds() - StartedSeconds);
}

void FMujocoVehicleDynamics::RecordTiming(EMujocoTimingScope Scope, double WallSeconds) const
{
#if RMSIM_WITH_MUJOCO
    if (!bTimingEnabled) return;
    auto& Timing = Impl->Timings[int32(Scope)];
    Timing.Seconds += WallSeconds; ++Timing.Calls;
#endif
}

void FMujocoVehicleDynamics::FlushTiming()
{
#if RMSIM_WITH_MUJOCO
    if (!bTimingEnabled || !Impl->Data) return;
    const double Elapsed = Time() - Impl->TimingWindowStart;
    if (Elapsed < 5.) return;
    const auto Ms = [this](EMujocoTimingScope Scope) { return Impl->Timings[int32(Scope)].Seconds * 1000.; };
    UE_LOG(LogRMSim, Display, TEXT("RMSIM_MUJOCO_TIMING sim_elapsed=%.6f native_steps=%llu ")
        TEXT("step_total_ms=%.3f solver_ms=%.3f get_body_ms=%.3f jacobian_ms=%.3f ")
        TEXT("preimpact_ms=%.3f damping_ms=%.3f contacts_ms=%.3f derived_history_ms=%.3f final_forward_ms=%.3f ")
        TEXT("sync_total_ms=%.3f contact_identity_ms=%.3f get_body_calls=%llu jacobian_calls=%llu scopes=overlapping_elapsed"),
        Elapsed, Impl->Timings[int32(EMujocoTimingScope::Solver)].Calls,
        Ms(EMujocoTimingScope::Step), Ms(EMujocoTimingScope::Solver),
        Ms(EMujocoTimingScope::GetBody), Ms(EMujocoTimingScope::Jacobian),
        Ms(EMujocoTimingScope::PreImpact), Ms(EMujocoTimingScope::Damping), Ms(EMujocoTimingScope::Contacts),
        Ms(EMujocoTimingScope::DerivedHistory), Ms(EMujocoTimingScope::FinalForward),
        Ms(EMujocoTimingScope::Sync), Ms(EMujocoTimingScope::ContactIdentity),
        Impl->Timings[int32(EMujocoTimingScope::GetBody)].Calls,
        Impl->Timings[int32(EMujocoTimingScope::Jacobian)].Calls);
    for (auto& Timing : Impl->Timings) Timing = {};
    Impl->TimingWindowStart = Time();
#endif
}

bool FMujocoVehicleDynamics::CaptureSensorHistory(FString& Error)
{
#if RMSIM_WITH_MUJOCO
    if (!Impl->Data || Impl->SensorHistory.IsEmpty())
    { Error = TEXT("MuJoCo sensor history unavailable"); return false; }
    const double Now = Time();
    if (Impl->SensorHistoryCount > 0)
    {
        const int32 Latest = (Impl->SensorHistoryWrite + Impl->SensorHistory.Num() - 1) % Impl->SensorHistory.Num();
        if (Now <= Impl->SensorHistory[Latest].Time)
        { Error = TEXT("MuJoCo sensor history time must advance within its reset epoch"); return false; }
    }
    FImplementation::FSensorHistorySample Sample;
    Sample.Time = Now;
    for (int32 Parent = 0; Parent < 2; ++Parent)
    {
        FMujocoBodyState State;
        if (!GetBody(Parent == 0 ? TEXT("body") : TEXT("yaw"), State))
        { Error = TEXT("MuJoCo sensor-parent state unavailable"); return false; }
        Sample.Parent[Parent].World = State.World;
        Sample.Parent[Parent].VelocityCmS = State.VelocityCmS;
        Sample.Parent[Parent].AngularVelocityRadS = State.AngularVelocityRadS;
    }
    if (Impl->SensorHistoryCount == Impl->SensorHistory.Num()) ++Impl->HistoryOverwrites;
    Impl->SensorHistory[Impl->SensorHistoryWrite] = Sample;
    Impl->SensorHistoryWrite = (Impl->SensorHistoryWrite + 1) % Impl->SensorHistory.Num();
    Impl->SensorHistoryCount = FMath::Min(Impl->SensorHistoryCount + 1, Impl->SensorHistory.Num());
    Impl->CachedHistoryLower = INDEX_NONE; Impl->CachedHistoryTime = -1;
    return true;
#else
    Error = TEXT("MuJoCo SDK unavailable"); return false;
#endif
}

bool FMujocoVehicleDynamics::GetSensorPoseHistoryRange(double& OldestSolverTime, double& NewestSolverTime, int32& SampleCount) const
{
    check(IsInGameThread());
    OldestSolverTime = NewestSolverTime = 0; SampleCount = 0;
#if RMSIM_WITH_MUJOCO
    if (Impl->SensorHistoryCount < 1) return false;
    const int32 Capacity = Impl->SensorHistory.Num();
    const int32 First = (Impl->SensorHistoryWrite + Capacity - Impl->SensorHistoryCount) % Capacity;
    const int32 Last = (Impl->SensorHistoryWrite + Capacity - 1) % Capacity;
    OldestSolverTime = Impl->SensorHistory[First].Time;
    NewestSolverTime = Impl->SensorHistory[Last].Time;
    SampleCount = Impl->SensorHistoryCount;
    return true;
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::FindSensorHistoryBracket(double SolverTime, int32& Lower, int32& Upper, double& Alpha) const
{
#if RMSIM_WITH_MUJOCO
    if (!FMath::IsFinite(SolverTime)) { ++Impl->HistoryInvalidTimeMisses; return false; }
    const int32 Count = Impl->SensorHistoryCount, Capacity = Impl->SensorHistory.Num();
    if (Count < 1) { ++Impl->HistoryRangeMisses; return false; }
    const int32 First = (Impl->SensorHistoryWrite + Capacity - Count) % Capacity;
    const auto At = [this, First, Capacity](int32 Logical) -> const FImplementation::FSensorHistorySample&
        { return Impl->SensorHistory[(First + Logical) % Capacity]; };
    if (SolverTime < At(0).Time || SolverTime > At(Count - 1).Time)
    { ++Impl->HistoryRangeMisses; return false; }

    int32 LogicalLower = 0;
    if (Impl->CachedHistoryLower != INDEX_NONE && SolverTime >= Impl->CachedHistoryTime &&
        Impl->CachedHistoryLower < Count && SolverTime >= At(Impl->CachedHistoryLower).Time)
    {
        // Rays arrive in emission-time order: after one binary search, traverse
        // only the few native substep boundaries crossed by this ray batch.
        LogicalLower = Impl->CachedHistoryLower;
        while (LogicalLower + 1 < Count && At(LogicalLower + 1).Time <= SolverTime) ++LogicalLower;
    }
    else
    {
        int32 Low = 0, High = Count - 1;
        while (Low < High)
        {
            const int32 Mid = Low + (High - Low + 1) / 2;
            if (At(Mid).Time <= SolverTime) Low = Mid; else High = Mid - 1;
        }
        LogicalLower = Low;
    }
    const int32 LogicalUpper = FMath::Min(LogicalLower + 1, Count - 1);
    Lower = (First + LogicalLower) % Capacity; Upper = (First + LogicalUpper) % Capacity;
    const double Span = At(LogicalUpper).Time - At(LogicalLower).Time;
    Alpha = Span > 0 ? (SolverTime - At(LogicalLower).Time) / Span : 0;
    Impl->CachedHistoryLower = LogicalLower; Impl->CachedHistoryTime = SolverTime;
    return true;
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::SampleSensorParentWorldAtTime(const FString& ParentFrame, double SolverTime, FTransform& OutPose) const
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    ++Impl->HistoryQueries;
    const int32 Parent = ParentFrame == TEXT("base_link") ? 0 : ParentFrame == TEXT("yaw_link") ? 1 : INDEX_NONE;
    if (Parent == INDEX_NONE) { ++Impl->HistoryParentMisses; return false; }
    int32 Lower = 0, Upper = 0; double Alpha = 0;
    if (!FindSensorHistoryBracket(SolverTime, Lower, Upper, Alpha)) return false;
    const auto& A = Impl->SensorHistory[Lower].Parent[Parent];
    const auto& B = Impl->SensorHistory[Upper].Parent[Parent];
    OutPose = FTransform(FQuat::Slerp(A.World.GetRotation(), B.World.GetRotation(), Alpha).GetNormalized(),
        FMath::Lerp(A.World.GetLocation(), B.World.GetLocation(), Alpha));
    return !OutPose.ContainsNaN();
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::SampleSensorMotionAtTime(const FString& ParentFrame, double SolverTime,
    const FTransform& LocalMount, FTransform& OutPose, FVector& OutPointVelocityCmS, FVector& OutAngularVelocityRadS) const
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    ++Impl->HistoryQueries;
    const int32 Parent = ParentFrame == TEXT("base_link") ? 0 : ParentFrame == TEXT("yaw_link") ? 1 : INDEX_NONE;
    if (Parent == INDEX_NONE) { ++Impl->HistoryParentMisses; return false; }
    if (LocalMount.ContainsNaN()) { ++Impl->HistoryInvalidMountMisses; return false; }
    int32 Lower = 0, Upper = 0; double Alpha = 0;
    if (!FindSensorHistoryBracket(SolverTime, Lower, Upper, Alpha)) return false;
    const auto& A = Impl->SensorHistory[Lower].Parent[Parent];
    const auto& B = Impl->SensorHistory[Upper].Parent[Parent];
    const FTransform ParentPose(FQuat::Slerp(A.World.GetRotation(), B.World.GetRotation(), Alpha).GetNormalized(),
        FMath::Lerp(A.World.GetLocation(), B.World.GetLocation(), Alpha));
    OutPose = LocalMount * ParentPose;
    OutAngularVelocityRadS = FMath::Lerp(A.AngularVelocityRadS, B.AngularVelocityRadS, Alpha);
    OutPointVelocityCmS = FMath::Lerp(A.VelocityCmS, B.VelocityCmS, Alpha) +
        FVector::CrossProduct(OutAngularVelocityRadS, OutPose.GetLocation() - ParentPose.GetLocation());
    return !OutPose.ContainsNaN() && !OutPointVelocityCmS.ContainsNaN() && !OutAngularVelocityRadS.ContainsNaN();
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

void FMujocoVehicleDynamics::CollectContacts(const TArray<FVector>& BeforeVelocities)
{
#if RMSIM_WITH_MUJOCO
    const auto* M = Impl->Model; const auto* D = Impl->Data;
    for (int32 I = 0; I < D->ncon; ++I)
    {
        const mjContact& C = D->contact[I];
        if (C.efc_address < 0) continue;
        const int32 Body0 = C.geom[0] >= 0 ? M->geom_bodyid[C.geom[0]] : 0;
        const int32 Body1 = C.geom[1] >= 0 ? M->geom_bodyid[C.geom[1]] : 0;
        int32 VehicleBody = INDEX_NONE, Side = -1;
        for (int32 Candidate = 0; Candidate < 2; ++Candidate)
        {
            const int32 Body = Candidate == 0 ? Body0 : Body1;
            if (Body > 0 && !Impl->BodyNamesById[Body].IsEmpty()) { VehicleBody = Body; Side = Candidate; break; }
        }
        if (Side < 0) continue;
        const int32 OtherBody = Side == 0 ? Body1 : Body0;
        if (OtherBody > 0 && !Impl->BodyNamesById[OtherBody].IsEmpty()) continue;
        const FString& Part = Impl->BodyNamesById[VehicleBody];
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
        Sample.PreImpactVelocityCmS = BeforeVelocities[VehicleBody];
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
    FScopedMujocoTiming Timing(*this, EMujocoTimingScope::Step);
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
        {
            FScopedMujocoTiming BeforeTiming(*this, EMujocoTimingScope::PreImpact);
            if (!PrepareBodyDamping(Error)) return false;
        }
        const double Previous = Impl->Data->time;
        {
            FScopedMujocoTiming SolverTiming(*this, EMujocoTimingScope::Solver);
            mj_step(Impl->Model, Impl->Data);
        }
        FMemory::Memcpy(Impl->Data->xfrc_applied, Impl->SavedAppliedWrenches.GetData(),
            sizeof(mjtNum) * 6 * Impl->Model->nbody);
        if (!ValidateState(Error) || Impl->Data->time <= Previous)
        { if (Error.IsEmpty()) Error = TEXT("MuJoCo time failed to advance"); return false; }
        {
            // Capture the solved contact before refreshing any derived positions.
            FScopedMujocoTiming ContactTiming(*this, EMujocoTimingScope::Contacts);
            CollectContacts(Impl->BeforeVelocities);
        }
        ++TotalSteps;
        {
            FScopedMujocoTiming HistoryTiming(*this, EMujocoTimingScope::DerivedHistory);
            // mj_step ends after integration. These minimal stages refresh xpos,
            // xquat, xipos and cdof for the completed qpos, without a new solve.
            mj_kinematics(Impl->Model, Impl->Data);
            mj_comPos(Impl->Model, Impl->Data);
            ++Impl->BodyStateGeneration;
            if (!CaptureSensorHistory(Error)) return false;
        }
    }
    RemainderSeconds -= Count * Dt;
    if (FMath::Abs(RemainderSeconds) < Dt * 1.e-7) RemainderSeconds = 0;
    // Preserve the final acceleration/contact phase, with drag evaluated at
    // the completed state. mj_forward changes no qpos/qvel, so the body-origin
    // kinematic cache from that same state remains valid for visual/ROS readers.
    if (!PrepareBodyDamping(Error)) return false;
    {
        FScopedMujocoTiming ForwardTiming(*this, EMujocoTimingScope::FinalForward);
        mj_forward(Impl->Model, Impl->Data);
    }
    FMemory::Memcpy(Impl->Data->xfrc_applied, Impl->SavedAppliedWrenches.GetData(),
        sizeof(mjtNum) * 6 * Impl->Model->nbody);
    if(!ValidateState(Error))return false;
    if(Impl->ViewerPublisher)Impl->ViewerPublisher->Publish(Impl->Model,Impl->Data,ResetCount);
    return true;
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
    J->SetBoolField(TEXT("native_timing_enabled"), bTimingEnabled);
    double HistoryOldest=0, HistoryNewest=0; int32 HistoryCount=0;
    if (GetSensorPoseHistoryRange(HistoryOldest, HistoryNewest, HistoryCount))
    {
        auto History=MakeShared<FJsonObject>();
        History->SetStringField(TEXT("model"),TEXT("native_substep_kinematics_comPos; translation_lerp_quaternion_slerp; origin_velocity_lerp"));
        History->SetNumberField(TEXT("oldest_solver_time_s"),HistoryOldest);
        History->SetNumberField(TEXT("newest_solver_time_s"),HistoryNewest);
        History->SetNumberField(TEXT("samples"),HistoryCount);
        History->SetNumberField(TEXT("step_s"),Timestep());
        History->SetNumberField(TEXT("reset_count"),double(ResetCount));
#if RMSIM_WITH_MUJOCO
        History->SetNumberField(TEXT("query_count"),double(Impl->HistoryQueries));
        History->SetNumberField(TEXT("range_misses"),double(Impl->HistoryRangeMisses));
        History->SetNumberField(TEXT("unsupported_parent_misses"),double(Impl->HistoryParentMisses));
        History->SetNumberField(TEXT("invalid_time_misses"),double(Impl->HistoryInvalidTimeMisses));
        History->SetNumberField(TEXT("invalid_mount_misses"),double(Impl->HistoryInvalidMountMisses));
        History->SetNumberField(TEXT("ring_overwrites"),double(Impl->HistoryOverwrites));
#endif
        J->SetObjectField(TEXT("sensor_pose_history"),History);
    }
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
        J->SetStringField(TEXT("body_damping_model"),TEXT("source_per_second_COM_force_and_world_inertia_torque; native_force_integration"));
        J->SetNumberField(TEXT("body_damping_dissipation_w"),Impl->LastDampingPowerW);
        J->SetNumberField(TEXT("wheel_angular_damping_per_s"),.01);
        if(bTimingEnabled&&Impl->Data)
        {
            const auto* M=Impl->Model;const auto* D=Impl->Data;
            J->SetStringField(TEXT("actuator_sampling_phase"),TEXT("final_forward_endpoint; current solver-time force, not a prior integrated-substep force sample"));
            TArray<TSharedPtr<FJsonValue>> States;States.Reserve(Impl->Servos.Num());
            for(const auto& Entry:Impl->Servos)
            {
                const auto& S=Entry.Value;
                auto State=MakeShared<FJsonObject>();
                State->SetStringField(TEXT("part"),Entry.Key);
                State->SetNumberField(TEXT("actuator_index"),S.Actuator);
                State->SetNumberField(TEXT("joint_index"),S.Joint);
                const bool IndicesValid=S.Actuator>=0&&S.Actuator<M->nu&&S.Joint>=0&&S.Joint<M->njnt;
                State->SetBoolField(TEXT("indices_valid"),IndicesValid);
                if(IndicesValid)
                {
                    const int32 A=S.Actuator,K=S.Joint,Dof=M->jnt_dofadr[K],Qpos=M->jnt_qposadr[K];
                    const char* ActuatorName=mj_id2name(M,mjOBJ_ACTUATOR,A);
                    const char* JointName=mj_id2name(M,mjOBJ_JOINT,K);
                    State->SetStringField(TEXT("actuator_name"),ActuatorName?UTF8_TO_TCHAR(ActuatorName):TEXT(""));
                    State->SetStringField(TEXT("joint_name"),JointName?UTF8_TO_TCHAR(JointName):TEXT(""));
                    State->SetNumberField(TEXT("dof_index"),Dof);
                    State->SetNumberField(TEXT("qpos_index"),Qpos);
                    State->SetNumberField(TEXT("joint_type"),M->jnt_type[K]);
                    State->SetNumberField(TEXT("transmission_type"),M->actuator_trntype[A]);
                    State->SetNumberField(TEXT("transmission_joint_index"),M->actuator_trnid[2*A]);
                    TArray<TSharedPtr<FJsonValue>> Gear;
                    for(int32 I=0;I<6;++I)Gear.Add(MakeShared<FJsonValueNumber>(M->actuator_gear[6*A+I]));
                    State->SetArrayField(TEXT("gear"),Gear);
                    const bool UnitHingeMapping=Dof>=0&&Dof<M->nv&&Qpos>=0&&Qpos<M->nq&&
                        M->jnt_type[K]==mjJNT_HINGE&&M->actuator_trntype[A]==mjTRN_JOINT&&
                        M->actuator_trnid[2*A]==K&&FMath::IsNearlyEqual(double(M->actuator_gear[6*A]),1.,1.e-9);
                    State->SetBoolField(TEXT("unit_hinge_mapping_verified"),UnitHingeMapping);
                    if(UnitHingeMapping)
                    {
                        // ConfigureServo verifies this hinge/joint/gear mapping.
                        // These generalized coordinates are radians; forces are N*m.
                        State->SetStringField(TEXT("force_unit"),TEXT("N*m"));
                        State->SetNumberField(TEXT("actuator_force"),D->actuator_force[A]);
                        State->SetNumberField(TEXT("qfrc_actuator"),D->qfrc_actuator[Dof]);
                        State->SetNumberField(TEXT("qfrc_constraint"),D->qfrc_constraint[Dof]);
                        State->SetNumberField(TEXT("qfrc_passive"),D->qfrc_passive[Dof]);
                        State->SetNumberField(TEXT("qfrc_applied"),D->qfrc_applied[Dof]);
                        State->SetNumberField(TEXT("qfrc_bias"),D->qfrc_bias[Dof]);
                        State->SetNumberField(TEXT("qfrc_smooth"),D->qfrc_smooth[Dof]);
                        State->SetNumberField(TEXT("qpos_rad"),D->qpos[Qpos]);
                        State->SetNumberField(TEXT("qvel_rad_s"),D->qvel[Dof]);
                        State->SetNumberField(TEXT("qacc_rad_s2"),D->qacc[Dof]);
                        State->SetNumberField(TEXT("servo_force_limit_nm"),S.Limit);
                        State->SetBoolField(TEXT("model_force_limited"),M->actuator_forcelimited[A]!=0);
                        State->SetArrayField(TEXT("model_force_range_nm"),{MakeShared<FJsonValueNumber>(M->actuator_forcerange[2*A]),
                            MakeShared<FJsonValueNumber>(M->actuator_forcerange[2*A+1])});
                    }
                }
                States.Add(MakeShared<FJsonValueObject>(State));
            }
            J->SetArrayField(TEXT("actuator_state"),States);
        }
    }
#endif
    return J;
}
