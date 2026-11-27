#include "MujocoVehicleDynamics.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformTime.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformProcess.h"
#include "HAL/Runnable.h"
#include "HAL/RunnableThread.h"
#include "Misc/ScopeLock.h"
#include <atomic>
#if PLATFORM_WINDOWS
#include "Timing/WindowsDeadlineWait.h"
#endif
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
constexpr int32 kMaxMatchVehicles = FMujocoVehicleDynamics::MaxMatchVehicles;

FString SlotPrefix(int32 Slot)
{
    if (Slot <= 0) return FString();
    if (Slot == 1) return TEXT("opponent_");
    return FString::Printf(TEXT("rig%d_"), Slot);
}

#if RMSIM_WITH_MUJOCO
FString RigElementName(const FString& Part, const TCHAR* Prefix, int32 MatchVehicleCount)
{
    if (MatchVehicleCount > 1)
    {
        for (int32 Slot = 1; Slot < MatchVehicleCount; ++Slot)
        {
            const FString SP = SlotPrefix(Slot);
            if (Part.StartsWith(SP))
                return SP + Prefix + Part.Mid(SP.Len());
        }
    }
    return Prefix + Part;
}

mjModel* LoadMatchModel(const FString& Path, const TSharedPtr<FJsonObject>& Manifest, int32 PartCount, int32 VehicleCount, FString& Error)
{
    char Buffer[2048] = {};
    mjSpec* Spec = mj_parseXML(TCHAR_TO_UTF8(*Path), nullptr, Buffer, UE_ARRAY_COUNT(Buffer));
    if (!Spec)
    {
        Error = TEXT("MuJoCo match XML parsing failed: ") + FString(UTF8_TO_TCHAR(Buffer));
        return nullptr;
    }
    mjsBody* Vehicle = mjs_findBody(Spec, "body");
    mjsBody* WorldBody = mjs_findBody(Spec, "world");
    mjModel* Model = nullptr;
    // Deep-copy the vehicle articulation (joints, actuators, self-exclusions) for slots 1..VehicleCount-1.
    if (Vehicle && WorldBody && mjs_setDeepCopy(Spec, 1) == 0)
    {
        bool bAttachedAll = true;
        for (int32 Slot = 1; Slot < VehicleCount; ++Slot)
        {
            mjsFrame* Frame = mjs_addFrame(WorldBody, nullptr);
            const FString Prefix = SlotPrefix(Slot);
            auto* Attached = Frame ? mjs_attach(Frame->element, Vehicle->element, TCHAR_TO_UTF8(*Prefix), "") : nullptr;
            auto* Cloned = Attached ? mjs_asBody(Attached) : nullptr;
            if (!Cloned)
            {
                bAttachedAll = false;
                break;
            }
            // Lift each clone apart vertically until its real spawn pose is bound.
            Cloned->pos[2] += 10. * double(Slot);

            // Same-spec attachment copies every world-bound flex. Discard this
            // slot's copies before another attachment can copy them again.
            for (auto* Flex = mjs_firstElement(Spec, mjOBJ_FLEX); Flex;)
            {
                auto* Next = mjs_nextElement(Spec, Flex);
                const char* Name = mjs_getString(mjs_getName(Flex));
                if (Name && FString(UTF8_TO_TCHAR(Name)).StartsWith(Prefix) && mjs_delete(Spec, Flex) != 0)
                {
                    bAttachedAll = false;
                    break;
                }
                Flex = Next;
            }
            if (!bAttachedAll) break;
        }
        if (bAttachedAll) Model = mj_compile(Spec, nullptr);
    }
    if (!Model)
    {
        const char* Detail = mjs_getError(Spec);
        Error = TEXT("MuJoCo match model compilation failed: ") + FString(UTF8_TO_TCHAR(Detail ? Detail : ""));
    }
    mj_deleteSpec(Spec);
    if (!Model) return nullptr;

    const TSharedPtr<FJsonObject>* SourceCounts = nullptr;
    double Bodies = 0, Joints = 0, Actuators = 0, Flexes = 0, FlexVertices = 0, FlexElements = 0;
    const int32 ExpectedExcludes = (VehicleCount * PartCount * (PartCount - 1)) / 2;
    if (!Manifest->TryGetObjectField(TEXT("model_counts"), SourceCounts) ||
        !(*SourceCounts)->TryGetNumberField(TEXT("nbody"), Bodies) ||
        !(*SourceCounts)->TryGetNumberField(TEXT("njnt"), Joints) ||
        !(*SourceCounts)->TryGetNumberField(TEXT("nu"), Actuators) ||
        !(*SourceCounts)->TryGetNumberField(TEXT("nflex"), Flexes) ||
        !(*SourceCounts)->TryGetNumberField(TEXT("nflexvert"), FlexVertices) ||
        !(*SourceCounts)->TryGetNumberField(TEXT("nflexelem"), FlexElements) ||
        Model->nbody != Bodies + (VehicleCount - 1) * PartCount || Model->njnt != VehicleCount * Joints ||
        Model->nu != VehicleCount * Actuators || Model->nflex != Flexes ||
        Model->nflexvert != FlexVertices || Model->nflexelem != FlexElements ||
        Model->nexclude != ExpectedExcludes)
    {
        mj_deleteModel(Model);
        Error = TEXT("MuJoCo match tree, actuators, self-exclusions or static flex count differ from the AIM source contract");
        return nullptr;
    }
    return Model;
}
#endif

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
#if !WITH_EDITOR
        // Packaged builds cook .uasset files into .pak archives (different byte size/hash than uncooked assets);
        // verify the cooked asset is present while keeping strict disk hashes for all external XML/mesh/JSON files.
        const bool bCookedUAsset = (Role == TEXT("vehicle_collision") || Role == TEXT("map_collision"))
            && Path.EndsWith(TEXT(".uasset"), ESearchCase::IgnoreCase);
        if (bCookedUAsset)
        {
            if(IFileManager::Get().FileSize(*Path)<=0)return Fail(TEXT("cooked asset missing: ")+Role+TEXT(" ")+Path);
        }
        else
#endif
        {
            if(double(IFileManager::Get().FileSize(*Path))!=Bytes)return Fail(TEXT("source byte length changed: ")+Role+TEXT(" ")+Path);
            const FMD5Hash Hash=FMD5Hash::HashFile(*Path);
            if(!Hash.IsValid()||!LexToString(Hash).Equals(ExpectedMd5,ESearchCase::IgnoreCase))return Fail(TEXT("source content changed: ")+Role+TEXT(" ")+Path);
        }
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

bool ValidateGenericRuntimeBindings(const TSharedPtr<FJsonObject>& Manifest,const FString& XmlPath,
    const FString& RigFile,int32& Count,FString& Error)
{
    auto Fail=[&](const FString& Why){Error=TEXT("MuJoCo v2 runtime binding: ")+Why;return false;};
    const TArray<TSharedPtr<FJsonValue>>* Bindings=nullptr;
    if(!Manifest->TryGetArrayField(TEXT("runtime_bindings"),Bindings)||Bindings->Num()<3||Bindings->Num()>1024)
        return Fail(TEXT("missing/bounded source bindings; regenerate composed scene"));
    TMap<FString,int32> Roles;
    for(const auto& Value:*Bindings)
    {
        const auto Binding=Value->AsObject();FString Role,Path,ExpectedMd5,Sha;double Bytes=0;
        if(!Binding||!Binding->TryGetStringField(TEXT("role"),Role)||!Binding->TryGetStringField(TEXT("path"),Path)||
           !Binding->TryGetStringField(TEXT("md5"),ExpectedMd5)||ExpectedMd5.Len()!=32||
           !Binding->TryGetStringField(TEXT("sha256"),Sha)||Sha.Len()!=64||
           !Binding->TryGetNumberField(TEXT("bytes"),Bytes)||!FMath::IsFinite(Bytes)||Bytes<=0)return Fail(TEXT("malformed entry"));
        if(Role==TEXT("canonical_vehicle")&&!RigFile.IsEmpty())Path=FPaths::ConvertRelativePathToFull(RigFile);
        else if(Role==TEXT("model_xml"))Path=XmlPath;
        else if(FPaths::IsRelative(Path))Path=FPaths::Combine(FPaths::GetPath(XmlPath),Path);
        Path=FPaths::ConvertRelativePathToFull(Path);
#if !WITH_EDITOR
        // Packaged builds cook .uasset files into .pak archives (different byte size/hash than uncooked assets);
        // verify the cooked asset is present while keeping strict disk hashes for all external XML/mesh/JSON files.
        const bool bCookedUAsset = (Role == TEXT("vehicle_collision") || Role == TEXT("map_collision"))
            && Path.EndsWith(TEXT(".uasset"), ESearchCase::IgnoreCase);
        if (bCookedUAsset)
        {
            if(IFileManager::Get().FileSize(*Path)<=0)return Fail(TEXT("cooked asset missing: ")+Role+TEXT(" ")+Path);
        }
        else
#endif
        {
            if(double(IFileManager::Get().FileSize(*Path))!=Bytes)return Fail(TEXT("source byte length changed: ")+Role+TEXT(" ")+Path);
            const FMD5Hash Hash=FMD5Hash::HashFile(*Path);
            if(!Hash.IsValid()||!LexToString(Hash).Equals(ExpectedMd5,ESearchCase::IgnoreCase))return Fail(TEXT("source content changed: ")+Role+TEXT(" ")+Path);
        }
        ++Roles.FindOrAdd(Role);
    }
    for(const TCHAR* Role:{TEXT("canonical_vehicle"),TEXT("map_manifest"),TEXT("model_xml"),TEXT("vehicle_xml"),TEXT("map_xml")})
        if(Roles.FindRef(Role)!=1)return Fail(TEXT("required role absent: ")+FString(Role));
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
        // The native solver owns this publisher, including in dedicated mode.
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
    int32 RootJoints[kMaxMatchVehicles] = {-1, -1, -1, -1, -1, -1};
    TMap<FString, int32> Bodies;
    TArray<FString> OrderedNativeNames;
    TArray<int32> OrderedBodyIds;
    TArray<FString> BodyNamesById;
    TArray<int32> BodySlots;
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
    struct FSensorHistorySample { double Time = 0; FSensorMotion Parent[kMaxMatchVehicles]; };
    struct FSensorTimeline
    {
        TArray<FSensorHistorySample> Samples;
        int32 Count = 0, Write = 0, CachedLower = INDEX_NONE;
        double CachedTime = -1;
        uint64 Queries = 0, RangeMisses = 0, ParentMisses = 0, InvalidTimeMisses = 0, InvalidMountMisses = 0, Overwrites = 0;

        void Append(const FSensorHistorySample& Sample)
        {
            if (Count == Samples.Num()) ++Overwrites;
            Samples[Write] = Sample;
            Write = (Write + 1) % Samples.Num();
            Count = FMath::Min(Count + 1, Samples.Num());
            CachedLower = INDEX_NONE;
            CachedTime = -1;
        }
    };
    FSensorTimeline SolverHistory[kMaxMatchVehicles];
    TOptional<FSensorTimeline> PresentedHistory[kMaxMatchVehicles];
    FSensorTimeline& ReadHistory(int32 Slot)
    { return PresentedHistory[Slot].IsSet() ? PresentedHistory[Slot].GetValue() : SolverHistory[Slot]; }
    struct FTiming { double Seconds = 0; uint64 Calls = 0; };
    FTiming Timings[int32(EMujocoTimingScope::Count)];
    double TimingWindowStart = 0;
    struct FServo { int32 Joint = -1, Actuator = -1, Slot = 0; double Kp = 0, Kd = 0, Limit = 0; };
    TMap<FString, FServo> Servos;
    struct FImpactContact
    {
        FVector Point = FVector::ZeroVector, IntoObstacle = FVector::ZeroVector, ForceOnVehicle = FVector::ZeroVector;
        double NormalForce = 0., LocalYawTorque = 0., Distance = 0.;
        int32 VehicleGeom = -1, OtherGeom = -1, OtherFlex = -1, OtherElement = -1, OtherVertex = -1;
    };
    struct FImpactStep
    {
        double SolverTime = 0.;
        uint64 SolverStep = 0;
        FVector Position = FVector::ZeroVector, BeforeVelocity = FVector::ZeroVector, AfterVelocity = FVector::ZeroVector;
        FVector BeforeOmega = FVector::ZeroVector, AfterOmega = FVector::ZeroVector, NetForce = FVector::ZeroVector;
        int32 ContactCount = 0;
        double NetYawTorque = 0.;
        FImpactContact Peaks[3];  // Normal load, upward force, yaw moment about the chassis COM.
    };
    TArray<FImpactStep> ImpactHistory;
    FImpactStep ImpactCurrent, ImpactTrigger;
    int32 ImpactWrite = 0, ImpactCount = 0, ImpactEvents = 0;
    double NextImpactTime = .5;

    double ImpactStrength(const FImpactContact& Contact, int32 Kind) const
    {
        if (Kind == 0) return Contact.NormalForce;
        if (Kind == 1) return Contact.ForceOnVehicle.Z;
        return FMath::Abs(Contact.LocalYawTorque);
    }

    void RecordImpactContact(const mjContact& Contact, const mjtNum Force[6], int32 Side)
    {
        if (ImpactHistory.IsEmpty()) return;
        const int32 Root = Model->jnt_bodyid[RootJoints[0]];
        const mjtNum* RootRotation = Data->xmat + 9 * Root;
        const mjtNum* Centre = Data->xipos + 3 * Root;
        mjtNum WorldForce[3];
        mju_mulMatTVec3(WorldForce, Contact.frame, Force);
        const double Sign = Side == 0 ? -1. : 1.;
        const FVector Applied = FVector(WorldForce[0], WorldForce[1], WorldForce[2]) * Sign;
        const FVector Normal = FVector(Contact.frame[0], Contact.frame[1], Contact.frame[2]) * -Sign;
        const FVector Point(Contact.pos[0], Contact.pos[1], Contact.pos[2]);
        const FVector Arm = Point - FVector(Centre[0], Centre[1], Centre[2]);
        const FVector RootUp(RootRotation[2], RootRotation[5], RootRotation[8]);
        // The native world is right-handed SI. The torque includes the friction moment arm.
        const double YawMoment = FVector::DotProduct(FVector::CrossProduct(Arm, Applied), RootUp);
        FImpactContact Sample;
        Sample.Point = Point;
        Sample.IntoObstacle = Normal;
        Sample.ForceOnVehicle = Applied;
        Sample.NormalForce = FMath::Abs(double(Force[0]));
        Sample.LocalYawTorque = YawMoment;
        Sample.Distance = Contact.dist;
        Sample.VehicleGeom = Contact.geom[Side];
        Sample.OtherGeom = Contact.geom[1-Side];
        Sample.OtherFlex = Contact.flex[1-Side];
        Sample.OtherElement = Contact.elem[1-Side];
        Sample.OtherVertex = Contact.vert[1-Side];
        ++ImpactCurrent.ContactCount;
        ImpactCurrent.NetForce += Applied;
        ImpactCurrent.NetYawTorque += YawMoment;
        for (int32 Kind = 0; Kind < 3; ++Kind)
            if (ImpactStrength(Sample, Kind) > ImpactStrength(ImpactCurrent.Peaks[Kind], Kind))
                ImpactCurrent.Peaks[Kind] = Sample;
    }

    void FinishImpactStep(uint64 SolverStep, uint64 Epoch)
    {
        if (ImpactHistory.IsEmpty() || ImpactEvents >= 6) return;
        const int32 Dof = Model->jnt_dofadr[RootJoints[0]];
        const mjtNum* Qvel = Data->qvel + Dof;
        ImpactCurrent.SolverTime = Data->time;
        ImpactCurrent.SolverStep = SolverStep;
        ImpactCurrent.AfterVelocity = FVector(Qvel[0], Qvel[1], Qvel[2]);
        ImpactCurrent.AfterOmega = FVector(Qvel[3], Qvel[4], Qvel[5]);
        ImpactHistory[ImpactWrite] = ImpactCurrent;
        ImpactWrite = (ImpactWrite + 1) % ImpactHistory.Num();
        ImpactCount = FMath::Min(ImpactCount+1, ImpactHistory.Num());
        const FVector DeltaVelocity = ImpactCurrent.AfterVelocity - ImpactCurrent.BeforeVelocity;
        const FVector DeltaOmega = ImpactCurrent.AfterOmega - ImpactCurrent.BeforeOmega;
        if (ImpactTrigger.SolverStep == 0 && Data->time >= NextImpactTime &&
            ((DeltaVelocity.SizeSquared() >= .50*.50 && DeltaOmega.SizeSquared() >= 2.0*2.0) ||
             FMath::Abs(ImpactCurrent.AfterVelocity.Z) >= 1.8 || FMath::Abs(ImpactCurrent.AfterOmega.Z) >= 10.))
            ImpactTrigger = ImpactCurrent;
        // Delay output until the post-impact segment is already in the history.
        if (ImpactTrigger.SolverStep == 0 || Data->time < ImpactTrigger.SolverTime + .040) return;
        const int32 Event = ++ImpactEvents;
        const FImpactStep& Trigger = ImpactTrigger;
        UE_LOG(LogRMSim, Display, TEXT("MUJOCO_IMPACT_TRIGGER event=%d epoch=%llu step=%llu solver_s=%.6f pos_m=%s before_v_mps=%s after_v_mps=%s before_local_omega=%s after_local_omega=%s contacts=%d net_force_n=%s net_yaw_nm=%.4f"),
            Event, static_cast<unsigned long long>(Epoch), static_cast<unsigned long long>(Trigger.SolverStep),
            Trigger.SolverTime, *Trigger.Position.ToCompactString(), *Trigger.BeforeVelocity.ToCompactString(),
            *Trigger.AfterVelocity.ToCompactString(), *Trigger.BeforeOmega.ToCompactString(),
            *Trigger.AfterOmega.ToCompactString(), Trigger.ContactCount, *Trigger.NetForce.ToCompactString(), Trigger.NetYawTorque);
        for (int32 Band = 0; Band < 2; ++Band)
        {
            const FImpactStep* Peaks[3] = {};
            for (int32 Offset = 0; Offset < ImpactCount; ++Offset)
            {
                const int32 Index = (ImpactWrite + ImpactHistory.Num() - 1 - Offset) % ImpactHistory.Num();
                const FImpactStep& Step = ImpactHistory[Index];
                const double Relative = Step.SolverTime - Trigger.SolverTime;
                if (Band == 0 ? (Relative < -.120 || Relative >= 0.) : (Relative < 0. || Relative > .041)) continue;
                for (int32 Kind = 0; Kind < 3; ++Kind)
                {
                    const double Strength = ImpactStrength(Step.Peaks[Kind], Kind);
                    if (Strength > 0. && (!Peaks[Kind] || Strength > ImpactStrength(Peaks[Kind]->Peaks[Kind], Kind)))
                        Peaks[Kind] = &Step;
                }
            }
            for (int32 Kind = 0; Kind < 3; ++Kind)
            {
                const FImpactStep* Step = Peaks[Kind];
                if (!Step) continue;
                const FImpactContact& Peak = Step->Peaks[Kind];
                const char* Vehicle = mj_id2name(Model, mjOBJ_GEOM, Peak.VehicleGeom);
                const char* Other = Peak.OtherGeom >= 0 ? mj_id2name(Model, mjOBJ_GEOM, Peak.OtherGeom) :
                    Peak.OtherFlex >= 0 ? mj_id2name(Model, mjOBJ_FLEX, Peak.OtherFlex) : nullptr;
                UE_LOG(LogRMSim, Display, TEXT("MUJOCO_IMPACT_CONTACT event=%d phase=%s peak=%s step=%llu solver_s=%.6f vehicle=%s vehicle_geom=%d other=%s other_geom=%d flex=%d element=%d vertex=%d p_m=(%.6f,%.6f,%.6f) into_obstacle=(%.6f,%.6f,%.6f) force_vehicle_n=(%.4f,%.4f,%.4f) normal_n=%.4f yaw_nm=%.4f distance_m=%.6f"),
                    Event, Band == 0 ? TEXT("before") : TEXT("during"),
                    Kind == 0 ? TEXT("normal") : Kind == 1 ? TEXT("upward") : TEXT("yaw"),
                    static_cast<unsigned long long>(Step->SolverStep), Step->SolverTime,
                    UTF8_TO_TCHAR(Vehicle ? Vehicle : "unnamed"), Peak.VehicleGeom,
                    UTF8_TO_TCHAR(Other ? Other : "unnamed"), Peak.OtherGeom, Peak.OtherFlex, Peak.OtherElement, Peak.OtherVertex,
                    Peak.Point.X, Peak.Point.Y, Peak.Point.Z, Peak.IntoObstacle.X, Peak.IntoObstacle.Y, Peak.IntoObstacle.Z,
                    Peak.ForceOnVehicle.X, Peak.ForceOnVehicle.Y, Peak.ForceOnVehicle.Z,
                    Peak.NormalForce, Peak.LocalYawTorque, Peak.Distance);
            }
        }
        NextImpactTime = Data->time + .75;
        ImpactTrigger = FImpactStep{};
    }
    ~FImplementation() { ViewerPublisher.Reset(); if (Data) mj_deleteData(Data); if (Model) mj_deleteModel(Model); }
#endif
};

struct FMujocoVehicleDynamics::FDedicated : FRunnable
{
#if RMSIM_WITH_MUJOCO
    struct FControl
    {
        double Position = 0, Velocity = 0, Limit = 0;
        double SolverTime = 0, ExpiresWall = 0;
        bool bTorque = false, bWrap = true;
    };
    struct FBodyFrame
    {
        FMujocoBodyState Body;
        FVector2D Joint = FVector2D::ZeroVector;
        bool bHasJoint = false;
    };
    struct FFrame
    {
        TArray<FBodyFrame> Bodies;
        TArray<int32> Warnings;
        double Time = 0, Remainder = 0, Penetration[kMaxMatchVehicles] = {}, DampingPower = 0;
        double PublishedWall = 0;
        uint64 Steps = 0, ResetCount[kMaxMatchVehicles] = {}, Sequence = 0, FailsafeTransitions[kMaxMatchVehicles] = {};
        int32 Substeps = 0, ContactCount = 0;
        bool bFailsafe[kMaxMatchVehicles] = {};
    };
    struct FQueryFrame
    {
        double Time = 0;
        TArray<FTransform> Parts;
    };

    FDedicated(FMujocoVehicleDynamics& InOwner, double InPeriod)
        : Owner(InOwner), Period(InPeriod), bKeepQueryFrames(FParse::Param(FCommandLine::Get(),TEXT("RosLidar"))) {}

    bool Capture(FFrame& Frame, FString& Error)
    {
        const auto& Native = *Owner.Impl;
        Frame.Bodies.SetNum(Native.Model->nbody);
        for (const auto& Entry : Native.Bodies)
        {
            auto& Part = Frame.Bodies[Entry.Value];
            if (!Owner.GetBody(Entry.Key, Part.Body))
            {
                Error = TEXT("MuJoCo cannot publish a complete body snapshot: ") + Entry.Key;
                return false;
            }
            Part.bHasJoint = Owner.GetJoint(Entry.Key, Part.Joint.X, Part.Joint.Y);
        }
        Frame.Warnings.SetNum(mjNWARNING);
        for (int32 I = 0; I < mjNWARNING; ++I) Frame.Warnings[I] = Native.Data->warning[I].number;
        Frame.Time = Native.Data->time;
        Frame.Steps = Owner.TotalSteps;
        for (int32 Slot = 0; Slot < Owner.MatchVehicleCount; ++Slot)
        {
            Frame.ResetCount[Slot] = Owner.ResetCount[Slot];
            Frame.Penetration[Slot] = Owner.MaximumPenetrationM[Slot];
        }
        Frame.Remainder = Owner.RemainderSeconds;
        Frame.DampingPower = Native.LastDampingPowerW;
        Frame.Substeps = Owner.LastFrameSteps;
        Frame.ContactCount = Native.Data->ncon;
        Frame.PublishedWall = FPlatformTime::Seconds();
        return true;
    }

    uint32 Run() override
    {
        double Deadline = FPlatformTime::Seconds();
        uint64 Sequence = 0, FailsafeTransitions[kMaxMatchVehicles] = {};
        double QueryWaitStarted = -1.0;
        bool bFailsafe[kMaxMatchVehicles] = {};
        FFrame Next;
#if PLATFORM_WINDOWS
        FRMSimWindowsDeadlineWait Wait;
#endif
        while (!bStop.load())
        {
            if (bPaused.load())
            {
                FPlatformProcess::SleepNoStats(.005f);
                Deadline = FPlatformTime::Seconds();
                continue;
            }
            const double Now = FPlatformTime::Seconds();
            if (Now < Deadline)
            {
#if PLATFORM_WINDOWS
                Wait.WaitUntil(Deadline, [] { return FPlatformTime::Seconds(); });
#else
                FPlatformProcess::SleepNoStats(float(Deadline - Now));
#endif
                continue;
            }
            if (bKeepQueryFrames)
            {
                bool bBacklogFull = false;
                {
                    FScopeLock Lock(&Exchange);
                    bBacklogFull = PendingQueries.Num() >= FMath::CeilToInt(0.075 / Period);
                }
                if (bBacklogFull)
                {
                    if (QueryWaitStarted < 0.) QueryWaitStarted = Now;
                    FPlatformProcess::SleepNoStats(.001f);
                    Deadline = FPlatformTime::Seconds();
                    continue;
                }
                if (QueryWaitStarted >= 0.)
                {
                    if (Now - QueryWaitStarted >= .05)
                        UE_LOG(LogRMSim, Display, TEXT("MUJOCO_SENSOR_BACKPRESSURE wait_ms=%.2f"), (Now-QueryWaitStarted)*1000.);
                    QueryWaitStarted = -1.;
                }
            }
            if (Now - Deadline > 0.05)
            {
                UE_LOG(LogRMSim, Display, TEXT("MUJOCO_DEDICATED_PACE_REALIGN debt_s=%.6f solver_s=%.6f"), Now-Deadline, Owner.Time());
                Deadline = Now;
            }
            FScopeLock StepLock(&SolverExchange);
            const double ControlNow = FPlatformTime::Seconds();
            const int32 VehicleCount = Owner.MatchVehicleCount;
            bool bNewControls = false;
            {
                FScopeLock Lock(&Exchange);
                if (bControlsReady)
                {
                    Swap(Active, Mailbox);
                    for (int32 Slot = 0; Slot < VehicleCount; ++Slot)
                        ControlDeadlines[Slot] = MailboxDeadlines[Slot];
                    bControlsReady = false;
                    bNewControls = true;
                }
            }
            for (int32 Slot = 0; Slot < VehicleCount; ++Slot)
            {
                const bool bExpired = ControlNow >= ControlDeadlines[Slot];
                if (bExpired && !bFailsafe[Slot])
                {
                    ++FailsafeTransitions[Slot];
                    for (const auto& Entry : Owner.Impl->Servos)
                    {
                        if (Entry.Value.Slot != Slot) continue;
                        double Position = 0, Velocity = 0;
                        Owner.GetJoint(Entry.Key, Position, Velocity);
                        Owner.SetServoTarget(Entry.Key, Position, 0, true);
                    }
                }
                bFailsafe[Slot] = bExpired;
            }
            for (auto& Entry : Active)
            {
                if (bFailsafe[Owner.Impl->Servos[Entry.Key].Slot]) continue;
                auto& Target = Entry.Value;
                if (bNewControls && Target.Limit > 0) Owner.SetForceLimit(Entry.Key, Target.Limit);
                if (!bNewControls && Target.ExpiresWall <= 0) continue;
                if (Target.bTorque)
                {
                    Owner.SetTorque(Entry.Key, Target.Position);
                    continue;
                }
                double Position = Target.Position;
                double Velocity = Target.Velocity;
                if (Target.ExpiresWall > 0)
                {
                    if (ControlNow >= Target.ExpiresWall)
                    {
                        double ActualRate = 0;
                        Owner.GetJoint(Entry.Key, Position, ActualRate);
                        Target.Position = Position;
                        Target.Velocity = Velocity = 0;
                        Target.ExpiresWall = 0;
                    }
                    else
                    {
                        Position += Velocity * FMath::Max(0., Owner.Time() - Target.SolverTime);
                        const int32 Joint = Owner.Impl->Servos[Entry.Key].Joint;
                        const mjModel* Model = Owner.Impl->Model;
                        if (Model->jnt_limited[Joint])
                        {
                            const double Clamped = FMath::Clamp(Position, Model->jnt_range[2 * Joint], Model->jnt_range[2 * Joint + 1]);
                            if (Clamped != Position) Velocity = 0;
                            Position = Clamped;
                        }
                    }
                }
                Owner.SetServoTarget(Entry.Key, Position, Velocity, Target.bWrap);
            }
            FString StepError;
            if (!Owner.Step(Period, StepError) || !Capture(Next, StepError))
            {
                FScopeLock Lock(&Exchange);
                ErrorMessage = StepError;
                return 2;
            }
            Owner.FlushTiming();
            Next.Sequence = ++Sequence;
            for (int32 Slot = 0; Slot < VehicleCount; ++Slot)
            {
                Next.bFailsafe[Slot] = bFailsafe[Slot];
                Next.FailsafeTransitions[Slot] = FailsafeTransitions[Slot];
            }
            FQueryFrame QueryFrame;
            if (bKeepQueryFrames)
            {
                QueryFrame.Time = Next.Time;
                QueryFrame.Parts.Reserve(Owner.Impl->OrderedBodyIds.Num());
                for (const int32 Body : Owner.Impl->OrderedBodyIds)
                    QueryFrame.Parts.Add(Next.Bodies[Body].Body.World);
            }
            {
                FScopeLock Lock(&Exchange);
                for (int32 Slot = 0; Slot < VehicleCount; ++Slot)
                {
                    const auto& History = Owner.Impl->SolverHistory[Slot];
                    auto& Pending = PendingHistory[Slot];
                    const int32 Capacity = History.Samples.Num();
                    const int32 Excess = FMath::Max(0, Pending.Num() + Next.Substeps - Capacity);
                    if (Excess > 0)
                    {
                        Pending.RemoveAt(0, Excess, EAllowShrinking::No);
                        LostHistory += Excess;
                    }
                    const int32 First = (History.Write + Capacity - Next.Substeps) % Capacity;
                    for (int32 I = 0; I < Next.Substeps; ++I)
                        Pending.Add(History.Samples[(First + I) % Capacity]);

                    auto& Contacts = PendingContacts[Slot];
                    const auto& Incoming = Owner.FrameContacts[Slot];
                    const int32 Take = FMath::Min(FMath::Max(0, 8192 - Contacts.Num()), Incoming.Num());
                    if (Take > 0) Contacts.Append(Incoming.GetData(), Take);
                    LostContacts += Incoming.Num() - Take;
                }
                if (bKeepQueryFrames)
                {
                    // Keep only endpoints for which their native substep history can still exist.
                    const int32 Maximum = FMath::CeilToInt(.5 / Period);
                    if (PendingQueries.Num() == Maximum)
                    {
                        PendingQueries.RemoveAt(0, 1, EAllowShrinking::No);
                        ++LostQueries;
                    }
                    PendingQueries.Add(MoveTemp(QueryFrame));
                }
                Swap(Next, Published);
            }
            // Keep small scheduling jitter on the fixed-step wall cadence.
            Deadline += Period;
        }
        return 0;
    }

    void Stop() override { bStop.store(true); }

    FMujocoVehicleDynamics& Owner;
    const double Period;
    const bool bKeepQueryFrames;
    TUniquePtr<FRunnableThread> Thread;
    std::atomic<bool> bStop{false}, bPaused{false};
    FCriticalSection SolverExchange;
    FCriticalSection Exchange;
    TMap<FString, FControl> Staged, Mailbox, Active;
    bool bControlsReady = false;
    bool bResettingOnGameThread = false;
    double MailboxDeadlines[kMaxMatchVehicles] = {}, ControlDeadlines[kMaxMatchVehicles] = {};
    FFrame Published, Presented;
    TArray<FImplementation::FSensorHistorySample> PendingHistory[kMaxMatchVehicles];
    TArray<FMujocoContactSample> PendingContacts[kMaxMatchVehicles], PresentedContacts[kMaxMatchVehicles];
    TArray<FQueryFrame> PendingQueries, PresentedQueries;
    uint64 LostHistory = 0, LostContacts = 0, PresentedLostHistory = 0, PresentedLostContacts = 0;
    uint64 LostQueries = 0, PresentedLostQueries = 0;
    FString ErrorMessage;
#else
    uint32 Run() override { return 2; }
#endif
};

FMujocoVehicleDynamics::FMujocoVehicleDynamics() : Impl(MakeUnique<FImplementation>()) {}
FMujocoVehicleDynamics::~FMujocoVehicleDynamics() { StopDedicated(); }

bool FMujocoVehicleDynamics::StartDedicated(double PeriodSeconds, FString& Error)
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    if (Dedicated || !Impl->Data || !FMath::IsFinite(PeriodSeconds) || PeriodSeconds < Timestep() || PeriodSeconds > .005)
    {
        Error = TEXT("invalid dedicated MuJoCo period or solver lifecycle");
        return false;
    }
    auto State = MakeUnique<FDedicated>(*this, PeriodSeconds);
    if (!State->Capture(State->Presented, Error)) return false;
    for (const auto& Entry : Impl->Servos)
    {
        auto& Control = State->Staged.Add(Entry.Key);
        Control.Limit = Entry.Value.Limit;
        double Rate = 0;
        GetJoint(Entry.Key, Control.Position, Rate);
    }
    for (int32 Slot = 0; Slot < MatchVehicleCount; ++Slot)
        Impl->PresentedHistory[Slot] = Impl->SolverHistory[Slot];
    Dedicated = MoveTemp(State);
    Dedicated->Thread.Reset(FRunnableThread::Create(Dedicated.Get(), TEXT("RMSim MuJoCo physics")));
    if (!Dedicated->Thread)
    {
        StopDedicated();
        Error = TEXT("could not start the dedicated MuJoCo physics thread");
        return false;
    }
    UE_LOG(LogRMSim, Display, TEXT("MUJOCO_DEDICATED_READY period_s=%.6f native_step_s=%.6f thread_id=%u"),
        PeriodSeconds, Timestep(), Dedicated->Thread->GetThreadID());
    return true;
#else
    Error = TEXT("MuJoCo SDK unavailable"); return false;
#endif
}

void FMujocoVehicleDynamics::StopDedicated()
{
#if RMSIM_WITH_MUJOCO
    if (!Dedicated) return;
    Dedicated->Stop();
    if (Dedicated->Thread) Dedicated->Thread->WaitForCompletion();
    UE_LOG(LogRMSim, Display, TEXT("MUJOCO_DEDICATED_STOP completed_batches=%llu steps=%llu history_overflow=%llu contact_overflow=%llu query_overflow=%llu"),
        Dedicated->Published.Sequence, TotalSteps, Dedicated->LostHistory, Dedicated->LostContacts, Dedicated->LostQueries);
    Dedicated.Reset();
    for (auto& Timeline : Impl->PresentedHistory) Timeline.Reset();
#endif
}

void FMujocoVehicleDynamics::SetDedicatedPaused(bool Paused)
{
#if RMSIM_WITH_MUJOCO
    if (Dedicated) Dedicated->bPaused.store(Paused);
#endif
}

void FMujocoVehicleDynamics::SubmitDedicatedControls(const TArray<double>& Deadlines)
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    if (!Dedicated) return;
    FScopeLock Lock(&Dedicated->Exchange);
    Dedicated->Mailbox = Dedicated->Staged;
    for (int32 Slot = 0; Slot < MatchVehicleCount; ++Slot)
        Dedicated->MailboxDeadlines[Slot] = Deadlines.IsValidIndex(Slot) ? Deadlines[Slot] : 0.0;
    Dedicated->bControlsReady = true;
#endif
}

void FMujocoVehicleDynamics::SubmitDedicatedControls(double DeadlineWallSeconds, double OpponentDeadlineWallSeconds)
{
    SubmitDedicatedControls(TArray<double>{DeadlineWallSeconds, OpponentDeadlineWallSeconds});
}

bool FMujocoVehicleDynamics::ConsumeDedicatedFrame(bool& Updated, FString& Error)
{
    check(IsInGameThread());
    Updated = false;
#if RMSIM_WITH_MUJOCO
    if (!Dedicated) return true;
    auto& State = *Dedicated;
    FScopeLock Lock(&State.Exchange);
    if (!State.ErrorMessage.IsEmpty()) { Error = State.ErrorMessage; return false; }
    if (State.Published.Sequence == State.Presented.Sequence) return true;
    State.Presented = State.Published;
    for (int32 Slot = 0; Slot < MatchVehicleCount; ++Slot)
    {
        State.PresentedContacts[Slot].Reset();
        Swap(State.PresentedContacts[Slot], State.PendingContacts[Slot]);
        auto& History = Impl->PresentedHistory[Slot].GetValue();
        for (const auto& Sample : State.PendingHistory[Slot]) History.Append(Sample);
        State.PendingHistory[Slot].Reset();
    }
    State.PresentedQueries.Reset();
    Swap(State.PresentedQueries, State.PendingQueries);
    State.PresentedLostHistory = State.LostHistory;
    State.PresentedLostContacts = State.LostContacts;
    State.PresentedLostQueries = State.LostQueries;
    Updated = true;
#endif
    return true;
}

void FMujocoVehicleDynamics::VisitDedicatedQueryFrames(TFunctionRef<void(double,const TArray<FTransform>&,bool)> Visitor)
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    if (!Dedicated) return;
    auto& Frames = Dedicated->PresentedQueries;
    for (int32 Index = 0; Index < Frames.Num(); ++Index)
        Visitor(Frames[Index].Time, Frames[Index].Parts, Index == Frames.Num() - 1);
#endif
}

void FMujocoVehicleDynamics::ClearDedicatedQueryFrames()
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    if (Dedicated) Dedicated->PresentedQueries.Reset();
#endif
}

bool FMujocoVehicleDynamics::ReadDedicatedProgress(uint64& Steps, uint64& Failsafes) const
{
#if RMSIM_WITH_MUJOCO
    const auto& State = World().Dedicated;
    if (!State) return false;
    FScopeLock Lock(&State->Exchange);
    const auto& Frame = State->Published;
    Steps = Frame.Steps;
    Failsafes = Frame.FailsafeTransitions[VehicleSlot()];
    return Frame.Sequence > 0 && State->ErrorMessage.IsEmpty();
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::Initialize(const FString& Filename, const TArray<FString>& RequiredBodies,
    const FString& RigFilename, const FString& DriveProfileFilename, FString& Error,
    bool bMatchWorld, int32 InMatchVehicleCount)
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    if (mj_version() != mjVERSION_HEADER) { Error = TEXT("MuJoCo runtime/header version mismatch"); return false; }
    if (Impl->Model) { Error = TEXT("MuJoCo model already initialized"); return false; }
    ModelPath = FPaths::ConvertRelativePathToFull(Filename);
    FString RequestedXml;
    if(FPaths::GetExtension(ModelPath).Equals(TEXT("xml"),ESearchCase::IgnoreCase))
    {RequestedXml=ModelPath;ModelPath=FPaths::Combine(FPaths::GetPath(ModelPath),TEXT("manifest.json"));}
    bool bGenericScene = false;
    if (FPaths::GetExtension(ModelPath).Equals(TEXT("json"), ESearchCase::IgnoreCase))
    {
        FString Text, Xml, Schema;
        if (!FFileHelper::LoadFileToString(Text, *ModelPath) || Text.Len() > 8 * 1024 * 1024 ||
            !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), ManifestObject) || !ManifestObject ||
            !ManifestObject->TryGetStringField(TEXT("schema"), Schema) ||
            (Schema != TEXT("rmsim_mujoco_scene_v1") && Schema != TEXT("rmsim_mujoco_scene_v2")) ||
            !ManifestObject->TryGetStringField(TEXT("model_xml"), Xml) || Xml.IsEmpty() ||
            !FPaths::IsRelative(Xml) || Xml.Contains(TEXT("..")) || Xml.Contains(TEXT(":")))
        { Error = TEXT("invalid MuJoCo scene manifest or model path"); return false; }
        bGenericScene = (Schema == TEXT("rmsim_mujoco_scene_v2"));
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
    if (bGenericScene)
    {
        if (!ValidateGenericRuntimeBindings(ManifestObject, ModelPath, RigFilename, VerifiedRuntimeBindings, Error))
            return false;
        ManifestObject->TryGetStringField(TEXT("root_body"), RootBodyName);
        if (RootBodyName.IsEmpty()) RootBodyName = TEXT("base_link");
        YawBodyName.Reset();
        PitchBodyName.Reset();
        ManifestObject->TryGetStringField(TEXT("yaw_body"), YawBodyName);
        ManifestObject->TryGetStringField(TEXT("pitch_body"), PitchBodyName);
    }
    else
    {
        RootBodyName = TEXT("body");
        YawBodyName = TEXT("yaw");
        PitchBodyName = TEXT("pitch");
        if(!ValidateRuntimeBindings(ManifestObject,ModelPath,RigFilename,DriveProfileFilename,
            DiagnosticPlane,VerifiedRuntimeBindings,Error))return false;
    }
    bPhysicsIdentityVerified=!DiagnosticPlane;
    if (!FPaths::FileExists(ModelPath)) { Error = TEXT("MuJoCo model XML missing: ") + ModelPath; return false; }
    if (bMatchWorld && (bGenericScene || DiagnosticPlane || RequiredBodies.Num() != 15 ||
        InMatchVehicleCount < 2 || InMatchVehicleCount > MaxMatchVehicles))
    { Error = TEXT("Shared MuJoCo match requires the source-verified RMUC2027 AIM scene and 2..6 vehicles"); return false; }
    MatchVehicleCount = bMatchWorld ? InMatchVehicleCount : 1;
    AssignedVehicleSlot = 0;
    SourceRigPath = FPaths::ConvertRelativePathToFull(RigFilename);
    SourceDriveProfilePath = FPaths::ConvertRelativePathToFull(DriveProfileFilename);
    char Buffer[2048] = {};
    Impl->Model = (MatchVehicleCount > 1) ? LoadMatchModel(ModelPath, ManifestObject, RequiredBodies.Num(), MatchVehicleCount, Error)
        : mj_loadXML(TCHAR_TO_UTF8(*ModelPath), nullptr, Buffer, UE_ARRAY_COUNT(Buffer));
    if (!Impl->Model)
    {
        if (MatchVehicleCount <= 1) Error = TEXT("MuJoCo model compilation failed: ") + FString(UTF8_TO_TCHAR(Buffer));
        return false;
    }
    if(Impl->Model->nflex>0&&FString(UTF8_TO_TCHAR(mj_versionString()))!=TEXT("4.0.1+rmsim.static-world-flex.3"))
    {
        Error=TEXT("MuJoCo static flex scenes require runtime 4.0.1+rmsim.static-world-flex.3; active runtime: ")+
            FString(UTF8_TO_TCHAR(mj_versionString()));return false;
    }
    Impl->Data = mj_makeData(Impl->Model);
    if (!Impl->Data) { Error = TEXT("MuJoCo data allocation failed"); return false; }
    auto* M = Impl->Model;
    if (MatchVehicleCount > 2 && M->opt.timestep < .0025)
    {
        // 400 Hz (exact 2 substeps per 5ms frame) gives 2.5x solver speedup for 3..6 vehicle matches
        // while keeping 4-wheel swerve suspension and contact dynamics well within stability margins.
        M->opt.timestep = .0025;
    }
    if (!FMath::IsFinite(double(M->opt.timestep)) || M->opt.timestep < .0001 || M->opt.timestep > .005)
    { Error = TEXT("MuJoCo step must be between 0.1 and 5 milliseconds"); return false; }
    bTimingEnabled = FParse::Param(FCommandLine::Get(), TEXT("ProfileMujocoTiming"));
    if (FParse::Param(FCommandLine::Get(), TEXT("MujocoImpactTrace")) ||
        FPlatformMisc::GetEnvironmentVariable(TEXT("RMSIM_MUJOCO_IMPACT_TRACE")) == TEXT("1"))
        Impl->ImpactHistory.SetNum(FMath::CeilToInt(.180 / M->opt.timestep));
    Impl->BodyNamesById.SetNum(M->nbody);
    Impl->BodySlots.Init(INDEX_NONE, M->nbody);
    Impl->BeforeVelocities.SetNumZeroed(M->nbody);
    Impl->BodyDamping.Init(FVector2D::ZeroVector, M->nbody);
    Impl->SavedAppliedWrenches.SetNumUninitialized(6 * M->nbody);
    Impl->BodyCache.SetNum(M->nbody);
    Impl->BodyCacheGeneration.SetNumZeroed(M->nbody);
    Impl->JacobianP.SetNumUninitialized(3 * M->nv);
    Impl->JacobianR.SetNumUninitialized(3 * M->nv);
    // At least half a second plus both interpolation endpoints at the actual native step.
    TSet<FString> Required;
    for (const FString& Name : RequiredBodies) Required.Add(Name);
    for (int32 Slot = 0; Slot < MatchVehicleCount; ++Slot)
    {
        const FString Prefix = SlotPrefix(Slot);
        const int32 Root = mj_name2id(M, mjOBJ_JOINT, TCHAR_TO_UTF8(*(Prefix + TEXT("chassis_free"))));
        if (Root < 0 || M->jnt_type[Root] != mjJNT_FREE)
        { Error = TEXT("MuJoCo vehicle must have a chassis_free free joint"); return false; }
        Impl->RootJoints[Slot] = Root;
        Impl->SolverHistory[Slot].Samples.SetNum(FMath::CeilToInt(.5 / double(M->opt.timestep)) + 2);
        int32 Mapped = 0;
        for (int32 Body = 1; Body < M->nbody; ++Body)
        {
            const char* Name = mj_id2name(M, mjOBJ_BODY, Body);
            if (!Name) continue;
            const FString NativePart(UTF8_TO_TCHAR(Name));
            if (Slot > 0 && !NativePart.StartsWith(Prefix)) continue;
            const FString Part = NativePart.Mid(Prefix.Len());
            if (!Required.Contains(Part)) continue;
            if (MatchVehicleCount > 1 && M->body_rootid[Body] != M->jnt_bodyid[Root])
            { Error = TEXT("MuJoCo part is outside its chassis articulation: ") + NativePart; return false; }
            if (Slot > 0 && (Mapped >= OrderedBodyNames.Num() || OrderedBodyNames[Mapped] != Part))
            { Error = TEXT("MuJoCo follower snapshot body order differs from its source rig"); return false; }
            ++Mapped;
            if (Slot == 0) OrderedBodyNames.Add(Part);
            Impl->Bodies.Add(NativePart, Body);
            Impl->OrderedNativeNames.Add(NativePart);
            Impl->OrderedBodyIds.Add(Body);
            Impl->BodyNamesById[Body] = Part;
            Impl->BodySlots[Body] = Slot;
        }
        const int32* RootBody = Impl->Bodies.Find(Prefix + RootBodyName);
        if (Mapped != Required.Num() || !RootBody || *RootBody != M->jnt_bodyid[Root] ||
            (!YawBodyName.IsEmpty() && !Impl->Bodies.Contains(Prefix + YawBodyName)) ||
            (!PitchBodyName.IsEmpty() && !Impl->Bodies.Contains(Prefix + PitchBodyName)))
        { Error = TEXT("MuJoCo body names do not cover the imported vehicle rig"); return false; }
    }
    ModelMd5 = LexToString(FMD5Hash::HashFile(*ModelPath));
    mj_forward(M, Impl->Data);
    if (!ValidateState(Error)) return false;
    for (int32 Slot = 0; Slot < MatchVehicleCount; ++Slot)
        if (!CaptureSensorHistory(Error, Slot)) return false;
    if (MatchVehicleCount <= 1)
        Impl->ViewerPublisher = FMujocoViewerPublisher::CreateIfRequested(ManifestObject, M);
    else
    {
        FString ViewerPort;
        if (FParse::Value(FCommandLine::Get(), TEXT("MujocoViewerPort="), ViewerPort))
            UE_LOG(LogRMSim, Warning, TEXT("MUJOCO_VIEWER_DISABLED reason=match_model_requires_multi_viewer"));
        UE_LOG(LogRMSim, Display, TEXT("MUJOCO_MATCH_WORLD_READY vehicles=%d bodies=%d actuators=%d flex=%d exclusions=%d"),
            MatchVehicleCount, int32(M->nbody), int32(M->nu), int32(M->nflex), int32(M->nexclude));
    }
    return true;
#else
    Error = TEXT("This build has no native MuJoCo SDK; run the pinned dependency setup and rebuild");
    return false;
#endif
}

bool FMujocoVehicleDynamics::AttachFollower(const TSharedPtr<FMujocoVehicleDynamics>& Primary, int32 Slot,
    const TArray<FString>& RequiredBodies, const FString& RigFilename, const FString& DriveProfileFilename, FString& Error)
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    if (!Primary || Primary->SharedWorld || Primary->MatchVehicleCount <= 1 ||
        Slot < 1 || Slot >= Primary->MatchVehicleCount || Primary->IsDedicated() ||
        Primary->Steps() != 0 || SharedWorld || Impl->Model ||
        Primary->SourceRigPath != FPaths::ConvertRelativePathToFull(RigFilename) ||
        Primary->SourceDriveProfilePath != FPaths::ConvertRelativePathToFull(DriveProfileFilename) ||
        RequiredBodies.Num() != Primary->BodyNames().Num())
    { Error = TEXT("MuJoCo follower must use the same sealed AIM rig and drive profile before the world starts"); return false; }
    const FString Prefix = SlotPrefix(Slot);
    for (const FString& Part : RequiredBodies)
        if (!Primary->Impl->Bodies.Contains(Prefix + Part))
        { Error = TEXT("MuJoCo follower body binding missing: ") + Prefix + Part; return false; }
    SharedWorld = Primary;
    AssignedVehicleSlot = Slot;
    MatchVehicleCount = Primary->MatchVehicleCount;
    ManifestObject = Primary->Manifest();
    OrderedBodyNames = Primary->BodyNames();
    Impl.Reset();
    return true;
#else
    Error = TEXT("MuJoCo SDK unavailable"); return false;
#endif
}

bool FMujocoVehicleDynamics::AttachOpponent(const TSharedPtr<FMujocoVehicleDynamics>& Primary,
    const TArray<FString>& RequiredBodies, const FString& RigFilename, const FString& DriveProfileFilename, FString& Error)
{
    return AttachFollower(Primary, 1, RequiredBodies, RigFilename, DriveProfileFilename, Error);
}

bool FMujocoVehicleDynamics::Reset(const FTransform& Pose, FString& Error, double InitialYaw, double InitialPitch)
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    auto& Owner = World();
    auto& Native = *Owner.Impl;
    if (!Native.Model || !Native.Data || Pose.ContainsNaN() || !FMath::IsFinite(InitialYaw) || !FMath::IsFinite(InitialPitch))
    { Error = TEXT("invalid MuJoCo reset"); return false; }
    auto* M = Native.Model; auto* D = Native.Data;
    const int32 Slot = VehicleSlot();
    const FString Prefix = SlotPrefix(Slot);
    const int32 RootJoint = Native.RootJoints[Slot];
    const int32 YawJoint = YawBodyName.IsEmpty() ? -1 : mj_name2id(M, mjOBJ_JOINT, TCHAR_TO_UTF8(*(Prefix + TEXT("j_") + YawBodyName)));
    const int32 PitchJoint = PitchBodyName.IsEmpty() ? -1 : mj_name2id(M, mjOBJ_JOINT, TCHAR_TO_UTF8(*(Prefix + TEXT("j_") + PitchBodyName)));
    if ((!YawBodyName.IsEmpty() && (YawJoint < 0 || M->jnt_type[YawJoint] != mjJNT_HINGE)) ||
        (!PitchBodyName.IsEmpty() && (PitchJoint < 0 || M->jnt_type[PitchJoint] != mjJNT_HINGE ||
        (M->jnt_limited[PitchJoint] && (InitialPitch < M->jnt_range[2 * PitchJoint] || InitialPitch > M->jnt_range[2 * PitchJoint + 1])))))
    { Error = TEXT("invalid MuJoCo reset gimbal joint/limit"); return false; }
    if (Owner.MatchVehicleCount <= 1)
    {
        Owner.StopDedicated();
        mj_resetData(M, D);
        Owner.RemainderSeconds = 0;
        Owner.LastFrameSteps = 0;
    }
    auto* Worker = Owner.Dedicated.Get();
    TUniquePtr<FScopeLock> SolverLock;
    TUniquePtr<TGuardValue<bool>> NativeRead;
    if (Worker)
    {
        SolverLock = MakeUnique<FScopeLock>(&Worker->SolverExchange);
        // Drain completed substeps of all tracks before clearing this rig's epoch.
        bool Updated = false;
        if (!Owner.ConsumeDedicatedFrame(Updated, Error)) return false;
        NativeRead = MakeUnique<TGuardValue<bool>>(Worker->bResettingOnGameThread, true);
    }
    if (Owner.MatchVehicleCount > 1)
    {
        for (int32 Joint = 0; Joint < M->njnt; ++Joint)
        {
            if (Native.BodySlots[M->jnt_bodyid[Joint]] != Slot) continue;
            const int32 Q = M->jnt_qposadr[Joint], V = M->jnt_dofadr[Joint];
            const int32 Nq = Joint + 1 < M->njnt ? M->jnt_qposadr[Joint + 1] - Q : M->nq - Q;
            const int32 Nv = Joint + 1 < M->njnt ? M->jnt_dofadr[Joint + 1] - V : M->nv - V;
            mju_copy(D->qpos + Q, M->qpos0 + Q, Nq);
            mju_zero(D->qvel + V, Nv);
            mju_zero(D->qacc_warmstart + V, Nv);
            mju_zero(D->qfrc_applied + V, Nv);
        }
        for (const auto& Entry : Native.Bodies)
            if (Native.BodySlots[Entry.Value] == Slot) mju_zero(D->xfrc_applied + 6 * Entry.Value, 6);
        for (int32 A = 0; A < M->nu; ++A)
            if (M->actuator_trntype[A] == mjTRN_JOINT && Native.BodySlots[M->jnt_bodyid[M->actuator_trnid[2 * A]]] == Slot)
                D->ctrl[A] = 0;
    }
    const int32 Address = M->jnt_qposadr[RootJoint];
    const FVector P = Pose.GetLocation() / 100.; const FQuat Q = Pose.GetRotation().GetNormalized();
    D->qpos[Address] = P.X; D->qpos[Address + 1] = -P.Y; D->qpos[Address + 2] = P.Z;
    D->qpos[Address + 3] = Q.W; D->qpos[Address + 4] = -Q.X; D->qpos[Address + 5] = Q.Y; D->qpos[Address + 6] = -Q.Z;
    // Initial placement and explicit resets own qpos. Ordinary gimbal motion
    // uses actuator targets exclusively; the default R-reset angles remain zero.
    if (YawJoint >= 0) D->qpos[M->jnt_qposadr[YawJoint]] = FMath::UnwindRadians(InitialYaw);
    if (PitchJoint >= 0) D->qpos[M->jnt_qposadr[PitchJoint]] = InitialPitch;
    Owner.FrameContacts[Slot].Reset();
    const auto InvolvesResetRig = [Slot](const FMujocoContactSample& Sample) { return Sample.OtherVehicleSlot == Slot; };
    for (int32 Other = 0; Other < Owner.MatchVehicleCount; ++Other)
        if (Other != Slot) Owner.FrameContacts[Other].RemoveAll(InvolvesResetRig);
    Owner.MaximumPenetrationM[Slot] = 0;
    ++Owner.ResetCount[Slot];
    if (Slot == 0)
    {
        Native.ImpactWrite = Native.ImpactCount = 0;
        Native.ImpactTrigger = FImplementation::FImpactStep{};
        Native.NextImpactTime = D->time + .5;
        for (auto& Timing : Native.Timings) Timing = {};
        Native.TimingWindowStart = D->time;
    }
    auto& History = Native.SolverHistory[Slot];
    History.Count = History.Write = 0;
    History.CachedLower = INDEX_NONE;
    History.CachedTime = -1;
    ++Native.BodyStateGeneration;
    Native.LastDampingPowerW = 0;
    if (Owner.MatchVehicleCount > 1)
    {
        if (Worker)
        {
            Worker->MailboxDeadlines[Slot] = Worker->ControlDeadlines[Slot] = 0;
            Worker->PresentedContacts[Slot].Reset();
            for (int32 Other = 0; Other < Owner.MatchVehicleCount; ++Other)
                if (Other != Slot) Worker->PresentedContacts[Other].RemoveAll(InvolvesResetRig);
        }
        for (const auto& Entry : Native.Servos)
        {
            if (Entry.Value.Slot != Slot) continue;
            const double InitialPosition = D->qpos[M->jnt_qposadr[Entry.Value.Joint]];
            Owner.SetServoTarget(Entry.Key, InitialPosition, 0, false);
            if (Worker)
            {
                Worker->Active.Remove(Entry.Key);
                Worker->Mailbox.Remove(Entry.Key);
                auto& Control = Worker->Staged.FindChecked(Entry.Key);
                Control = {};
                Control.Position = InitialPosition;
                Control.Limit = Entry.Value.Limit;
            }
        }
    }
    mj_forward(M, D);
    if (!Owner.ValidateState(Error) || !Owner.CaptureSensorHistory(Error, Slot)) return false;
    if (!Worker) return true;
    Native.PresentedHistory[Slot] = Native.SolverHistory[Slot];
    if (!Worker->Capture(Worker->Presented, Error)) return false;
    // A queued endpoint at the reset time describes the pre-reset poses.
    if (!Worker->PresentedQueries.IsEmpty() && Worker->PresentedQueries.Last().Time >= D->time)
        Worker->PresentedQueries.Pop(EAllowShrinking::No);
    if (Worker->bKeepQueryFrames)
    {
        auto& Endpoint = Worker->PresentedQueries.AddDefaulted_GetRef();
        Endpoint.Time = D->time;
        for (const int32 Body : Native.OrderedBodyIds)
            Endpoint.Parts.Add(Worker->Presented.Bodies[Body].Body.World);
    }
    return true;
#else
    Error = TEXT("MuJoCo SDK unavailable"); return false;
#endif
}

bool FMujocoVehicleDynamics::ConfigureServo(const FString& Part, double Kp, double Kd, double Limit, FString& Error)
{
    if (SharedWorld) return SharedWorld->ConfigureServo(SlotPrefix(VehicleSlot()) + Part, Kp, Kd, Limit, Error);
#if RMSIM_WITH_MUJOCO
    auto* M = Impl->Model;
    if (!M || !FMath::IsFinite(Kp) || !FMath::IsFinite(Kd) || !FMath::IsFinite(Limit) || Kp < 0 || Kd < 0 || Limit <= 0)
    { Error = TEXT("invalid MuJoCo servo configuration"); return false; }
    const int32 Joint = mj_name2id(M, mjOBJ_JOINT, TCHAR_TO_UTF8(*RigElementName(Part, TEXT("j_"), MatchVehicleCount)));
    const int32 Actuator = mj_name2id(M, mjOBJ_ACTUATOR, TCHAR_TO_UTF8(*RigElementName(Part, TEXT("a_"), MatchVehicleCount)));
    const int32* Body = Impl->Bodies.Find(Part);
    if (!Body || Joint < 0 || Actuator < 0 || M->jnt_bodyid[Joint] != *Body || M->jnt_type[Joint] != mjJNT_HINGE ||
        M->actuator_trntype[Actuator] != mjTRN_JOINT || M->actuator_trnid[2 * Actuator] != Joint ||
        !FMath::IsNearlyEqual(double(M->actuator_gear[6 * Actuator]), 1., 1.e-9))
    { Error = TEXT("MuJoCo hinge/actuator/gear mapping invalid: ") + Part; return false; }
    FImplementation::FServo Servo; Servo.Joint = Joint; Servo.Actuator = Actuator; Servo.Slot = Impl->BodySlots[*Body];
    Servo.Kp = Kp; Servo.Kd = Kd; Servo.Limit = Limit;
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


bool FMujocoVehicleDynamics::SetRootExternalWrench(const FVector& ForceWorldRosN, const FVector& TorqueWorldRosNm)
{
#if RMSIM_WITH_MUJOCO
    auto& Owner = World();
    auto& Native = *Owner.Impl;
    const int32 Joint = Native.RootJoints[VehicleSlot()];
    if (!Native.Data || Joint < 0 || ForceWorldRosN.ContainsNaN() || TorqueWorldRosNm.ContainsNaN()) return false;
    TUniquePtr<FScopeLock> SolverLock;
    if (Owner.Dedicated && IsInGameThread())
        SolverLock = MakeUnique<FScopeLock>(&Owner.Dedicated->SolverExchange);
    mjtNum* W = Native.Data->xfrc_applied + 6 * Native.Model->jnt_bodyid[Joint];
    W[0] = ForceWorldRosN.X; W[1] = ForceWorldRosN.Y; W[2] = ForceWorldRosN.Z;
    W[3] = TorqueWorldRosNm.X; W[4] = TorqueWorldRosNm.Y; W[5] = TorqueWorldRosNm.Z;
    return true;
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::ConfigureBodyDamping(const FString& Part, double LinearPerSecond, double AngularPerSecond, FString& Error)
{
    if (SharedWorld) return SharedWorld->ConfigureBodyDamping(SlotPrefix(VehicleSlot()) + Part, LinearPerSecond, AngularPerSecond, Error);
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
    for (int32 PartIndex = 0; PartIndex < Impl->OrderedNativeNames.Num(); ++PartIndex)
    {
        FMujocoBodyState State;
        if (!GetBody(Impl->OrderedNativeNames[PartIndex], State))
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
    if (SharedWorld) return SharedWorld->SetServoTarget(SlotPrefix(VehicleSlot()) + Part, Position, Velocity, Wrap);
#if RMSIM_WITH_MUJOCO
    if (!FMath::IsFinite(Position) || !FMath::IsFinite(Velocity)) return false;
    if (Dedicated && IsInGameThread() && !Dedicated->bResettingOnGameThread)
    {
        auto* Control = Dedicated->Staged.Find(Part);
        if (!Control) return false;
        Control->Position = Position;
        Control->Velocity = Velocity;
        Control->bWrap = Wrap;
        Control->bTorque = false;
        Control->ExpiresWall = 0;
        return true;
    }
    const auto* S = Impl->Servos.Find(Part);
    if (!S) return false;
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

bool FMujocoVehicleDynamics::SetServoTrajectory(const FString& Part, double Position, double Velocity,
    bool Wrap, double AtSolverTime, double ExpiresWall)
{
    check(IsInGameThread());
    if (SharedWorld)
        return SharedWorld->SetServoTrajectory(SlotPrefix(VehicleSlot()) + Part, Position, Velocity, Wrap, AtSolverTime, ExpiresWall);
#if RMSIM_WITH_MUJOCO
    if (!Dedicated || !FMath::IsFinite(AtSolverTime) || !FMath::IsFinite(ExpiresWall) || ExpiresWall <= 0 ||
        !SetServoTarget(Part, Position, Velocity, Wrap)) return false;
    auto& Control = Dedicated->Staged[Part];
    Control.SolverTime = AtSolverTime;
    Control.ExpiresWall = ExpiresWall;
    return true;
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::SetTorque(const FString& Part, double Torque)
{
    if (SharedWorld) return SharedWorld->SetTorque(SlotPrefix(VehicleSlot()) + Part, Torque);
#if RMSIM_WITH_MUJOCO
    if (!FMath::IsFinite(Torque)) return false;
    if (Dedicated && IsInGameThread())
    {
        auto* Control = Dedicated->Staged.Find(Part);
        if (!Control) return false;
        Control->Position = Torque;
        Control->bTorque = true;
        Control->ExpiresWall = 0;
        return true;
    }
    const auto* S = Impl->Servos.Find(Part);
    if (!S) return false;
    Impl->Model->actuator_biastype[S->Actuator] = mjBIAS_NONE;
    Impl->Data->ctrl[S->Actuator] = FMath::Clamp(Torque, -S->Limit, S->Limit);
    return true;
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::SetForceLimit(const FString& Part, double Limit)
{
    if (SharedWorld) return SharedWorld->SetForceLimit(SlotPrefix(VehicleSlot()) + Part, Limit);
#if RMSIM_WITH_MUJOCO
    if (!FMath::IsFinite(Limit) || Limit <= 0) return false;
    if (Dedicated && IsInGameThread())
    {
        auto* Control = Dedicated->Staged.Find(Part);
        if (!Control) return false;
        Control->Limit = Limit;
        return true;
    }
    auto* S = Impl->Servos.Find(Part);
    if (!S) return false;
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
    if (SharedWorld) return SharedWorld->GetJoint(SlotPrefix(VehicleSlot()) + Part, Position, Velocity);
    Position = Velocity = 0;
#if RMSIM_WITH_MUJOCO
    if (Dedicated && IsInGameThread() && !Dedicated->bResettingOnGameThread)
    {
        const int32* Id = Impl->Bodies.Find(Part);
        if (!Id || !Dedicated->Presented.Bodies[*Id].bHasJoint) return false;
        Position = Dedicated->Presented.Bodies[*Id].Joint.X;
        Velocity = Dedicated->Presented.Bodies[*Id].Joint.Y;
        return true;
    }
    if (!Impl->Model || !Impl->Data) return false;
    const auto* M = Impl->Model; const auto* D = Impl->Data;
    const int32 Joint = mj_name2id(M, mjOBJ_JOINT, TCHAR_TO_UTF8(*RigElementName(Part, TEXT("j_"), MatchVehicleCount)));
    if (Joint < 0 || (M->jnt_type[Joint] != mjJNT_HINGE && M->jnt_type[Joint] != mjJNT_SLIDE)) return false;
    Position = D->qpos[M->jnt_qposadr[Joint]]; Velocity = D->qvel[M->jnt_dofadr[Joint]];
    return FMath::IsFinite(Position) && FMath::IsFinite(Velocity);
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::GetBody(const FString& Part, FMujocoBodyState& Out) const
{
    if (SharedWorld) return SharedWorld->GetBody(SlotPrefix(VehicleSlot()) + Part, Out);
#if RMSIM_WITH_MUJOCO
    const int32* Id = Impl->Bodies.Find(Part);
    if (!Id || !Impl->Data) return false;
    if (Dedicated && IsInGameThread() && !Dedicated->bResettingOnGameThread)
    {
        Out = Dedicated->Presented.Bodies[*Id].Body;
        return true;
    }
    FScopedMujocoTiming Timing(*this, EMujocoTimingScope::GetBody);
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
    if (SharedWorld) { SharedWorld->RecordTiming(Scope, WallSeconds); return; }
#if RMSIM_WITH_MUJOCO
    if (!bTimingEnabled || (Dedicated && IsInGameThread())) return;
    auto& Timing = Impl->Timings[int32(Scope)];
    Timing.Seconds += WallSeconds; ++Timing.Calls;
#endif
}

void FMujocoVehicleDynamics::FlushTiming()
{
#if RMSIM_WITH_MUJOCO
    if (!bTimingEnabled || !Impl->Data || (Dedicated && IsInGameThread())) return;
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

bool FMujocoVehicleDynamics::CaptureSensorHistory(FString& Error, int32 Slot)
{
#if RMSIM_WITH_MUJOCO
    auto& Timeline = Impl->SolverHistory[Slot];
    if (!Impl->Data || Timeline.Samples.IsEmpty())
    { Error = TEXT("MuJoCo sensor history unavailable"); return false; }
    const double Now = Time();
    if (Timeline.Count > 0)
    {
        const int32 Latest = (Timeline.Write + Timeline.Samples.Num() - 1) % Timeline.Samples.Num();
        if (Now <= Timeline.Samples[Latest].Time)
        { Error = TEXT("MuJoCo sensor history time must advance within its reset epoch"); return false; }
    }
    FImplementation::FSensorHistorySample Sample;
    Sample.Time = Now;
    for (int32 Parent = 0; Parent < 2; ++Parent)
    {
        FMujocoBodyState State;
        const FString& ParentName = (Parent == 0 || YawBodyName.IsEmpty()) ? RootBodyName : YawBodyName;
        if (!GetBody(SlotPrefix(Slot) + ParentName, State))
        { Error = TEXT("MuJoCo sensor-parent state unavailable"); return false; }
        Sample.Parent[Parent].World = State.World;
        Sample.Parent[Parent].VelocityCmS = State.VelocityCmS;
        Sample.Parent[Parent].AngularVelocityRadS = State.AngularVelocityRadS;
    }
    Timeline.Append(Sample);
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
    auto& Timeline = World().Impl->ReadHistory(VehicleSlot());
    if (Timeline.Count < 1) return false;
    const int32 Capacity = Timeline.Samples.Num();
    const int32 First = (Timeline.Write + Capacity - Timeline.Count) % Capacity;
    const int32 Last = (Timeline.Write + Capacity - 1) % Capacity;
    OldestSolverTime = Timeline.Samples[First].Time;
    NewestSolverTime = Timeline.Samples[Last].Time;
    SampleCount = Timeline.Count;
    return true;
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::FindSensorHistoryBracket(double SolverTime, int32& Lower, int32& Upper, double& Alpha) const
{
#if RMSIM_WITH_MUJOCO
    auto& Timeline = World().Impl->ReadHistory(VehicleSlot());
    if (!FMath::IsFinite(SolverTime)) { ++Timeline.InvalidTimeMisses; return false; }
    const int32 Count = Timeline.Count, Capacity = Timeline.Samples.Num();
    if (Count < 1) { ++Timeline.RangeMisses; return false; }
    const int32 First = (Timeline.Write + Capacity - Count) % Capacity;
    const auto At = [&Timeline, First, Capacity](int32 Logical) -> const FImplementation::FSensorHistorySample&
        { return Timeline.Samples[(First + Logical) % Capacity]; };
    if (SolverTime < At(0).Time || SolverTime > At(Count - 1).Time)
    { ++Timeline.RangeMisses; return false; }

    int32 LogicalLower = 0;
    if (Timeline.CachedLower != INDEX_NONE && SolverTime >= Timeline.CachedTime &&
        Timeline.CachedLower < Count && SolverTime >= At(Timeline.CachedLower).Time)
    {
        // Rays arrive in emission-time order: after one binary search, traverse
        // only the few native substep boundaries crossed by this ray batch.
        LogicalLower = Timeline.CachedLower;
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
    Timeline.CachedLower = LogicalLower; Timeline.CachedTime = SolverTime;
    return true;
#else
    return false;
#endif
}

bool FMujocoVehicleDynamics::SampleSensorParentWorldAtTime(const FString& ParentFrame, double SolverTime, FTransform& OutPose) const
{
    check(IsInGameThread());
#if RMSIM_WITH_MUJOCO
    auto& Timeline = World().Impl->ReadHistory(VehicleSlot());
    ++Timeline.Queries;
    const int32 Parent = ParentFrame == TEXT("base_link") ? 0 : ParentFrame == TEXT("yaw_link") ? 1 : INDEX_NONE;
    if (Parent == INDEX_NONE) { ++Timeline.ParentMisses; return false; }
    int32 Lower = 0, Upper = 0; double Alpha = 0;
    if (!FindSensorHistoryBracket(SolverTime, Lower, Upper, Alpha)) return false;
    const auto& A = Timeline.Samples[Lower].Parent[Parent];
    const auto& B = Timeline.Samples[Upper].Parent[Parent];
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
    auto& Timeline = World().Impl->ReadHistory(VehicleSlot());
    ++Timeline.Queries;
    const int32 Parent = ParentFrame == TEXT("base_link") ? 0 : ParentFrame == TEXT("yaw_link") ? 1 : INDEX_NONE;
    if (Parent == INDEX_NONE) { ++Timeline.ParentMisses; return false; }
    if (LocalMount.ContainsNaN()) { ++Timeline.InvalidMountMisses; return false; }
    int32 Lower = 0, Upper = 0; double Alpha = 0;
    if (!FindSensorHistoryBracket(SolverTime, Lower, Upper, Alpha)) return false;
    const auto& A = Timeline.Samples[Lower].Parent[Parent];
    const auto& B = Timeline.Samples[Upper].Parent[Parent];
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
        const int32 Slots[2] = {Impl->BodySlots[Body0], Impl->BodySlots[Body1]};
        if (Slots[0] == Slots[1]) continue;
        mjtNum Force[6] = {}; mj_contactForce(M, D, I, Force);
        if (!FMath::IsFinite(double(Force[0])) || FMath::Abs(double(Force[0])) < 1.e-8) continue;
        for (int32 Side = 0; Side < 2; ++Side)
        {
            const int32 Slot = Slots[Side];
            if (Slot == INDEX_NONE) continue;
            const int32 VehicleBody = Side == 0 ? Body0 : Body1;
            if (Slot == 0 && Impl->ImpactEvents < 6) Impl->RecordImpactContact(C, Force, Side);
            FMujocoContactSample Sample;
            Sample.BodyName = Impl->BodyNamesById[VehicleBody];
            Sample.OtherVehicleSlot = Slots[1 - Side];
            if (Sample.OtherVehicleSlot != INDEX_NONE)
                Sample.OtherBodyName = Impl->BodyNamesById[Side == 0 ? Body1 : Body0];
            Sample.OtherGeom = C.geom[1 - Side]; Sample.OtherFlex = C.flex[1 - Side];
            const char* OtherName = Sample.OtherGeom >= 0 ? mj_id2name(M, mjOBJ_GEOM, Sample.OtherGeom) :
                Sample.OtherFlex >= 0 ? mj_id2name(M, mjOBJ_FLEX, Sample.OtherFlex) : nullptr;
            Sample.OtherName = OtherName ? UTF8_TO_TCHAR(OtherName) : FString::Printf(TEXT("geom_%d_flex_%d"), Sample.OtherGeom, Sample.OtherFlex);
            Sample.PointCm = PolarToUE(C.pos, 100.);
            // MuJoCo defines the normal from geom[0] toward geom[1].
            Sample.IntoObstacleNormal = PolarToUE(C.frame).GetSafeNormal() * (Side == 0 ? 1. : -1.);
            Sample.PreImpactVelocityCmS = BeforeVelocities[VehicleBody];
            Sample.ForceN = FMath::Abs(double(Force[0])); Sample.ImpulseNs = Sample.ForceN * M->opt.timestep;
            Sample.TimeSeconds = D->time;
            MaximumPenetrationM[Slot] = FMath::Max(MaximumPenetrationM[Slot], FMath::Max(0., -double(C.dist)));
            FrameContacts[Slot].Add(MoveTemp(Sample));
        }
    }
#endif
}

bool FMujocoVehicleDynamics::Step(double Seconds, FString& Error)
{
    check(!SharedWorld);
    check(IsInGameThread() != IsDedicated());
    FScopedMujocoTiming Timing(*this, EMujocoTimingScope::Step);
#if RMSIM_WITH_MUJOCO
    if (!Impl->Model || !Impl->Data || !FMath::IsFinite(Seconds) || Seconds < 0 || Seconds > 1.)
    { Error = TEXT("invalid MuJoCo elapsed time; no elapsed simulation time discarded"); return false; }
    const double Dt = Impl->Model->opt.timestep;
    RemainderSeconds += Seconds;
    const int32 Count = FMath::FloorToInt((RemainderSeconds + Dt * 1.e-7) / Dt);
    if (Count > 10000) { Error = TEXT("MuJoCo substep budget exceeded"); return false; }
    for (auto& Contacts : FrameContacts) Contacts.Reset();
    LastFrameSteps = Count;
    for (int32 I = 0; I < Count; ++I)
    {
        {
            FScopedMujocoTiming BeforeTiming(*this, EMujocoTimingScope::PreImpact);
            if (!PrepareBodyDamping(Error)) return false;
        }
        const double Previous = Impl->Data->time;
        if (!Impl->ImpactHistory.IsEmpty() && Impl->ImpactEvents < 6)
        {
            const int32 Root = Impl->Model->jnt_bodyid[Impl->RootJoints[0]];
            const mjtNum* Position = Impl->Data->xpos + 3 * Root;
            const mjtNum* Qvel = Impl->Data->qvel + Impl->Model->jnt_dofadr[Impl->RootJoints[0]];
            auto& Step = Impl->ImpactCurrent;
            Step = FImplementation::FImpactStep{};
            Step.Position = FVector(Position[0], Position[1], Position[2]);
            Step.BeforeVelocity = FVector(Qvel[0], Qvel[1], Qvel[2]);
            Step.BeforeOmega = FVector(Qvel[3], Qvel[4], Qvel[5]);
        }
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
        Impl->FinishImpactStep(TotalSteps, ResetCount[0]);
        {
            FScopedMujocoTiming HistoryTiming(*this, EMujocoTimingScope::DerivedHistory);
            // mj_step ends after integration. These minimal stages refresh xpos,
            // xquat, xipos and cdof for the completed qpos, without a new solve.
            mj_kinematics(Impl->Model, Impl->Data);
            mj_comPos(Impl->Model, Impl->Data);
            ++Impl->BodyStateGeneration;
            for (int32 Slot = 0; Slot < MatchVehicleCount; ++Slot)
                if (!CaptureSensorHistory(Error, Slot)) return false;
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
    if(Impl->ViewerPublisher)Impl->ViewerPublisher->Publish(Impl->Model,Impl->Data,ResetCount[0]);
    return true;
#else
    Error = TEXT("MuJoCo SDK unavailable"); return false;
#endif
}

int32 FMujocoVehicleDynamics::WarningCount() const
{
    if (SharedWorld) return SharedWorld->WarningCount();
    int32 Total=0;
#if RMSIM_WITH_MUJOCO
    if(Impl&&Impl->Data)for(int32 I=0;I<mjNWARNING;++I)
        Total+=Dedicated ? Dedicated->Presented.Warnings[I] : Impl->Data->warning[I].number;
#endif
    return Total;
}

double FMujocoVehicleDynamics::Time() const
{
    if (SharedWorld) return SharedWorld->Time();
#if RMSIM_WITH_MUJOCO
    if (Dedicated && IsInGameThread() && !Dedicated->bResettingOnGameThread) return Dedicated->Presented.Time;
    return Impl->Data ? Impl->Data->time : 0;
#else
    return 0;
#endif
}

uint64 FMujocoVehicleDynamics::Steps() const
{
    if (SharedWorld) return SharedWorld->Steps();
#if RMSIM_WITH_MUJOCO
    if (Dedicated && IsInGameThread()) return Dedicated->Presented.Steps;
#endif
    return TotalSteps;
}

const TArray<FMujocoContactSample>& FMujocoVehicleDynamics::Contacts() const
{
    const auto& Owner = World();
    const int32 Slot = VehicleSlot();
#if RMSIM_WITH_MUJOCO
    if (Owner.Dedicated && IsInGameThread()) return Owner.Dedicated->PresentedContacts[Slot];
#endif
    return Owner.FrameContacts[Slot];
}
double FMujocoVehicleDynamics::Timestep() const
{
    if (SharedWorld) return SharedWorld->Timestep();
#if RMSIM_WITH_MUJOCO
    return Impl->Model ? Impl->Model->opt.timestep : 0;
#else
    return 0;
#endif
}

FVector FMujocoVehicleDynamics::GravityCmS2() const
{
    if (SharedWorld) return SharedWorld->GravityCmS2();
#if RMSIM_WITH_MUJOCO
    return Impl->Model?PolarToUE(Impl->Model->opt.gravity,100.):FVector::ZeroVector;
#else
    return FVector::ZeroVector;
#endif
}

TSharedRef<FJsonObject> FMujocoVehicleDynamics::Diagnostics() const
{
    check(IsInGameThread());
    const auto& Owner = World();
    const int32 Slot = VehicleSlot();
    auto J = MakeShared<FJsonObject>();
    uint64 PublishedResetCount = 0;
    double PublishedRemainder = 0, PublishedPenetration = 0;
    int32 PublishedSubsteps = 0;
#if RMSIM_WITH_MUJOCO
    const auto& Native = *Owner.Impl;
    const auto& State = Owner.Dedicated;
    if (State)
    {
        const auto& Frame = State->Presented;
        PublishedResetCount = Frame.ResetCount[Slot];
        PublishedRemainder = Frame.Remainder;
        PublishedPenetration = Frame.Penetration[Slot];
        PublishedSubsteps = Frame.Substeps;
        auto Worker = MakeShared<FJsonObject>();
        Worker->SetStringField(TEXT("mode"), TEXT("dedicated_native_solver"));
        Worker->SetNumberField(TEXT("thread_id"), State->Thread->GetThreadID());
        Worker->SetNumberField(TEXT("period_s"), State->Period);
        Worker->SetNumberField(TEXT("consumed_sequence"), double(Frame.Sequence));
        Worker->SetNumberField(TEXT("consumed_published_wall_s"), Frame.PublishedWall);
        Worker->SetNumberField(TEXT("history_overflow"), double(State->PresentedLostHistory));
        Worker->SetNumberField(TEXT("contact_overflow"), double(State->PresentedLostContacts));
        Worker->SetNumberField(TEXT("query_overflow"), double(State->PresentedLostQueries));
        Worker->SetBoolField(TEXT("failsafe"), Frame.bFailsafe[Slot]);
        Worker->SetNumberField(TEXT("failsafe_transitions"), double(Frame.FailsafeTransitions[Slot]));
        J->SetObjectField(TEXT("physics_worker"), Worker);
    }
    else
#endif
    {
        PublishedResetCount = Owner.ResetCount[Slot];
        PublishedRemainder = Owner.RemainderSeconds;
        PublishedPenetration = Owner.MaximumPenetrationM[Slot];
        PublishedSubsteps = Owner.LastFrameSteps;
    }
    const auto& PublishedContacts = Contacts();
    J->SetStringField(TEXT("backend"), TEXT("mujoco")); J->SetStringField(TEXT("model_xml"), Owner.ModelPath); J->SetStringField(TEXT("model_md5"), Owner.ModelMd5);
    J->SetNumberField(TEXT("runtime_binding_count"),Owner.VerifiedRuntimeBindings);
    J->SetBoolField(TEXT("runtime_bindings_verified"),Owner.VerifiedRuntimeBindings>0);
    J->SetBoolField(TEXT("physics_identity_verified"),Owner.bPhysicsIdentityVerified);
    J->SetNumberField(TEXT("native_vehicle_count"), Owner.MatchVehicleCount);
    J->SetNumberField(TEXT("native_vehicle_slot"),Slot);
    J->SetStringField(TEXT("runtime_binding_method"),TEXT("active-source MD5; physical map fields; SHA256 provenance retained"));
    J->SetStringField(TEXT("authority"), TEXT("native_mj_step; UE_query_only_visual_followers"));
    J->SetNumberField(TEXT("time_s"), Time()); J->SetNumberField(TEXT("step_s"), Timestep());
    J->SetNumberField(TEXT("steps"), double(Steps())); J->SetNumberField(TEXT("reset_count"), double(PublishedResetCount));
    J->SetNumberField(TEXT("step_count"),double(Steps()));J->SetNumberField(TEXT("pose_override_count"),double(PublishedResetCount>0?PublishedResetCount-1:0));
    J->SetNumberField(TEXT("contact_count"),PublishedContacts.Num());
    J->SetBoolField(TEXT("native_timing_enabled"), Owner.bTimingEnabled);
    double HistoryOldest=0, HistoryNewest=0; int32 HistoryCount=0;
    if (GetSensorPoseHistoryRange(HistoryOldest, HistoryNewest, HistoryCount))
    {
        auto History=MakeShared<FJsonObject>();
        History->SetStringField(TEXT("model"),TEXT("native_substep_kinematics_comPos; translation_lerp_quaternion_slerp; origin_velocity_lerp"));
        History->SetNumberField(TEXT("oldest_solver_time_s"),HistoryOldest);
        History->SetNumberField(TEXT("newest_solver_time_s"),HistoryNewest);
        History->SetNumberField(TEXT("samples"),HistoryCount);
        History->SetNumberField(TEXT("step_s"),Timestep());
        History->SetNumberField(TEXT("reset_count"),double(PublishedResetCount));
#if RMSIM_WITH_MUJOCO
        const auto& Timeline = Owner.Impl->ReadHistory(Slot);
        History->SetNumberField(TEXT("query_count"),double(Timeline.Queries));
        History->SetNumberField(TEXT("range_misses"),double(Timeline.RangeMisses));
        History->SetNumberField(TEXT("unsupported_parent_misses"),double(Timeline.ParentMisses));
        History->SetNumberField(TEXT("invalid_time_misses"),double(Timeline.InvalidTimeMisses));
        History->SetNumberField(TEXT("invalid_mount_misses"),double(Timeline.InvalidMountMisses));
        History->SetNumberField(TEXT("ring_overwrites"),double(Timeline.Overwrites));
#endif
        J->SetObjectField(TEXT("sensor_pose_history"),History);
    }
    double PeakNormalForce=0;for(const auto& C:PublishedContacts)PeakNormalForce=FMath::Max(PeakNormalForce,C.ForceN);
    J->SetNumberField(TEXT("normal_force_N"),PeakNormalForce);
    J->SetNumberField(TEXT("last_frame_substeps"), PublishedSubsteps); J->SetNumberField(TEXT("unconsumed_time_s"), PublishedRemainder);
    J->SetNumberField(TEXT("maximum_contact_penetration_m"), PublishedPenetration); J->SetNumberField(TEXT("last_frame_contact_samples"), PublishedContacts.Num());
#if RMSIM_WITH_MUJOCO
    if (Native.Model)
    {
        J->SetStringField(TEXT("version"), UTF8_TO_TCHAR(mj_versionString()));
        J->SetNumberField(TEXT("nq"), Native.Model->nq); J->SetNumberField(TEXT("nv"), Native.Model->nv);
        J->SetNumberField(TEXT("bodies"), Native.Model->nbody); J->SetNumberField(TEXT("geoms"), Native.Model->ngeom); J->SetNumberField(TEXT("flexes"), Native.Model->nflex);
        J->SetNumberField(TEXT("active_contacts"), State ? State->Presented.ContactCount : Native.Data->ncon);
        TArray<TSharedPtr<FJsonValue>> Warnings;
        int32 WarningCount=0;
        for (int32 I = 0; I < mjNWARNING; ++I)
        {
            const int32 Count = State ? State->Presented.Warnings[I] : Native.Data->warning[I].number;
            Warnings.Add(MakeShared<FJsonValueNumber>(Count));
            WarningCount += Count;
        }
        J->SetArrayField(TEXT("warning_counts"), Warnings);
        J->SetNumberField(TEXT("warning_count"),WarningCount);
        double Mass = 0;
        for (const auto& Entry : Native.Bodies)
            if (Native.BodySlots[Entry.Value] == Slot) Mass += Native.Model->body_mass[Entry.Value];
        J->SetNumberField(TEXT("vehicle_total_mass_kg"), Mass);
        J->SetStringField(TEXT("body_damping_model"),TEXT("source_per_second_COM_force_and_world_inertia_torque; native_force_integration"));
        J->SetNumberField(TEXT("body_damping_dissipation_w"),State ? State->Presented.DampingPower : Native.LastDampingPowerW);
        J->SetNumberField(TEXT("wheel_angular_damping_per_s"),.01);
        if(Owner.bTimingEnabled && Native.Data && !State)
        {
            const auto* M=Native.Model;const auto* D=Native.Data;
            J->SetStringField(TEXT("actuator_sampling_phase"),TEXT("final_forward_endpoint; current solver-time force, not a prior integrated-substep force sample"));
            TArray<TSharedPtr<FJsonValue>> States;States.Reserve(Native.Servos.Num());
            for(const auto& Entry:Native.Servos)
            {
                const auto& S=Entry.Value;
                if (S.Slot != Slot) continue;
                auto ActuatorState=MakeShared<FJsonObject>();
                ActuatorState->SetStringField(TEXT("part"), Entry.Key.Mid(SlotPrefix(Slot).Len()));
                ActuatorState->SetNumberField(TEXT("actuator_index"),S.Actuator);
                ActuatorState->SetNumberField(TEXT("joint_index"),S.Joint);
                const bool IndicesValid=S.Actuator>=0&&S.Actuator<M->nu&&S.Joint>=0&&S.Joint<M->njnt;
                ActuatorState->SetBoolField(TEXT("indices_valid"),IndicesValid);
                if(IndicesValid)
                {
                    const int32 A=S.Actuator,K=S.Joint,Dof=M->jnt_dofadr[K],Qpos=M->jnt_qposadr[K];
                    const char* ActuatorName=mj_id2name(M,mjOBJ_ACTUATOR,A);
                    const char* JointName=mj_id2name(M,mjOBJ_JOINT,K);
                    ActuatorState->SetStringField(TEXT("actuator_name"),ActuatorName?UTF8_TO_TCHAR(ActuatorName):TEXT(""));
                    ActuatorState->SetStringField(TEXT("joint_name"),JointName?UTF8_TO_TCHAR(JointName):TEXT(""));
                    ActuatorState->SetNumberField(TEXT("dof_index"),Dof);
                    ActuatorState->SetNumberField(TEXT("qpos_index"),Qpos);
                    ActuatorState->SetNumberField(TEXT("joint_type"),M->jnt_type[K]);
                    ActuatorState->SetNumberField(TEXT("transmission_type"),M->actuator_trntype[A]);
                    ActuatorState->SetNumberField(TEXT("transmission_joint_index"),M->actuator_trnid[2*A]);
                    TArray<TSharedPtr<FJsonValue>> Gear;
                    for(int32 I=0;I<6;++I)Gear.Add(MakeShared<FJsonValueNumber>(M->actuator_gear[6*A+I]));
                    ActuatorState->SetArrayField(TEXT("gear"),Gear);
                    const bool UnitHingeMapping=Dof>=0&&Dof<M->nv&&Qpos>=0&&Qpos<M->nq&&
                        M->jnt_type[K]==mjJNT_HINGE&&M->actuator_trntype[A]==mjTRN_JOINT&&
                        M->actuator_trnid[2*A]==K&&FMath::IsNearlyEqual(double(M->actuator_gear[6*A]),1.,1.e-9);
                    ActuatorState->SetBoolField(TEXT("unit_hinge_mapping_verified"),UnitHingeMapping);
                    if(UnitHingeMapping)
                    {
                        // ConfigureServo verifies this hinge/joint/gear mapping.
                        // These generalized coordinates are radians; forces are N*m.
                        ActuatorState->SetStringField(TEXT("force_unit"),TEXT("N*m"));
                        ActuatorState->SetNumberField(TEXT("actuator_force"),D->actuator_force[A]);
                        ActuatorState->SetNumberField(TEXT("qfrc_actuator"),D->qfrc_actuator[Dof]);
                        ActuatorState->SetNumberField(TEXT("qfrc_constraint"),D->qfrc_constraint[Dof]);
                        ActuatorState->SetNumberField(TEXT("qfrc_passive"),D->qfrc_passive[Dof]);
                        ActuatorState->SetNumberField(TEXT("qfrc_applied"),D->qfrc_applied[Dof]);
                        ActuatorState->SetNumberField(TEXT("qfrc_bias"),D->qfrc_bias[Dof]);
                        ActuatorState->SetNumberField(TEXT("qfrc_smooth"),D->qfrc_smooth[Dof]);
                        ActuatorState->SetNumberField(TEXT("qpos_rad"),D->qpos[Qpos]);
                        ActuatorState->SetNumberField(TEXT("qvel_rad_s"),D->qvel[Dof]);
                        ActuatorState->SetNumberField(TEXT("qacc_rad_s2"),D->qacc[Dof]);
                        ActuatorState->SetNumberField(TEXT("servo_force_limit_nm"),S.Limit);
                        ActuatorState->SetBoolField(TEXT("model_force_limited"),M->actuator_forcelimited[A]!=0);
                        ActuatorState->SetArrayField(TEXT("model_force_range_nm"),{MakeShared<FJsonValueNumber>(M->actuator_forcerange[2*A]),
                            MakeShared<FJsonValueNumber>(M->actuator_forcerange[2*A+1])});
                    }
                }
                States.Add(MakeShared<FJsonValueObject>(ActuatorState));
            }
            J->SetArrayField(TEXT("actuator_state"),States);
        }
    }
#endif
    return J;
}
