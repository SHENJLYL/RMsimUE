#include "RosLidarComponent.h"
#include "VehicleRig.h"
#include "RosCoordinates.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "Misc/FileHelper.h"
#include "Misc/SecureHash.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
bool LidarNumber(const TSharedPtr<FJsonObject>& J,const TCHAR* Key,double& Value,double Low,double High)
{
    const auto* V=J->Values.Find(Key);
    return V&&V->IsValid()&&(*V)->Type==EJson::Number&&(*V)->TryGetNumber(Value)&&FMath::IsFinite(Value)&&Value>=Low&&Value<=High;
}
bool LidarVector(const TSharedPtr<FJsonObject>& J,const TCHAR* Key,FVector& Value,double Limit)
{
    const TArray<TSharedPtr<FJsonValue>>* V;
    if(!J->TryGetArrayField(Key,V)||V->Num()!=3)return false;
    for(int I=0;I<3;++I)if(!(*V)[I].IsValid()||(*V)[I]->Type!=EJson::Number||!(*V)[I]->TryGetNumber(Value[I])||!FMath::IsFinite(Value[I])||FMath::Abs(Value[I])>Limit)return false;
    return true;
}
void LidarU32(TArray<uint8>& Bytes,uint32 Value)
{for(int I=0;I<4;++I)Bytes.Add(uint8(Value>>(8*I)));}
void LidarFloat(TArray<uint8>& Bytes,float Value)
{uint32 Bits;FMemory::Memcpy(&Bits,&Value,4);LidarU32(Bytes,Bits);}
TArray<TSharedPtr<FJsonValue>> LidarVec(const FVector& V)
{return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),MakeShared<FJsonValueNumber>(V.Z)};}
}

bool URosLidarComponent::Initialize(AVehicleRig* Vehicle,const FString& Profile,FString& Error)
{
    FString Text;TSharedPtr<FJsonObject> J;
    if(!Vehicle||!Vehicle->IsContactPhysics()||!FFileHelper::LoadFileToString(Text,*Profile)||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),J)||!J.IsValid())
    {Error=TEXT("cannot read physical LiDAR runtime profile");return false;}
    FString Schema,Parent,Pattern;bool Calibrated=true;const TSharedPtr<FJsonObject>* Pose;FVector Rpy;
    double SeedValue=0,ReflectivityValue=0;
    if(J->Values.Num()!=15||!J->TryGetStringField(TEXT("schema"),Schema)||Schema!=TEXT("rm_lidar_runtime_v1")||
       !J->TryGetBoolField(TEXT("calibrated"),Calibrated)||Calibrated||!J->TryGetStringField(TEXT("id"),Id)||Id.IsEmpty()||Id.Len()>64||Id==TEXT("base")||
       !FChar::IsAlpha(Id[0])||!J->TryGetStringField(TEXT("parent_frame"),Parent)||Parent!=TEXT("base_link")||
       !J->TryGetStringField(TEXT("pattern"),Pattern)||Pattern!=TEXT("spherical_fibonacci")||
       !J->TryGetObjectField(TEXT("origin"),Pose)||(*Pose)->Values.Num()!=2||!LidarVector(*Pose,TEXT("xyz"),Origin,5)||!LidarVector(*Pose,TEXT("rpy"),Rpy,2*PI)||
       !LidarNumber(J,TEXT("rays_per_second"),RaysPerSecond,100,200000)||
       !LidarNumber(J,TEXT("frame_hz"),FrameHz,1,30)||RaysPerSecond/FrameHz>65536||
       !LidarNumber(J,TEXT("elevation_min_rad"),MinElevation,-PI/2,PI/2)||
       !LidarNumber(J,TEXT("elevation_max_rad"),MaxElevation,MinElevation+.001,PI/2)||
       !LidarNumber(J,TEXT("min_range_m"),MinRange,.01,10)||!LidarNumber(J,TEXT("max_range_m"),MaxRange,MinRange+.01,200)||
       !LidarNumber(J,TEXT("range_noise_std_m"),NoiseStd,0,1)||
       !LidarNumber(J,TEXT("reflectivity"),ReflectivityValue,0,255)||FMath::FloorToDouble(ReflectivityValue)!=ReflectivityValue||
       !LidarNumber(J,TEXT("noise_seed"),SeedValue,0,2147483647)||FMath::FloorToDouble(SeedValue)!=SeedValue)
    {Error=TEXT("invalid LiDAR identity, mount, scan or range configuration");return false;}
    for(TCHAR Ch:Id)if(Ch>127||!(FChar::IsAlnum(Ch)||Ch=='_')){Error=TEXT("invalid LiDAR id");return false;}
    Rig=Vehicle;Mount=FTransform(RosCoordinates::Rotation(Rpy),RosCoordinates::Position(Origin));
    Seed=int32(SeedValue);Reflectivity=int32(ReflectivityValue);Random.Initialize(Seed);
    ProfileHash=LexToString(FMD5Hash::HashFile(*Profile));MotionGeneration=Rig->GetMotionGeneration();
    UE_LOG(LogRMSim,Display,TEXT("LIDAR_READY pattern=spherical_fibonacci rays_s=%.0f frame_hz=%.1f mount_uncalibrated=true"),RaysPerSecond,FrameHz);
    return true;
}

void URosLidarComponent::DiscardPending()
{Pending.Reset();Building.Reset();PreviousTime=-1;FrameStart=-1;RayRemainder=0;}

void URosLidarComponent::Reset()
{
    DiscardPending();RayIndex=0;Traced=0;Hits=0;MissedRays=0;DroppedScans=0;ScanSequence=0;HistoryResets=0;MaxStep=0;
    MotionGeneration=Rig->GetMotionGeneration();Random.Initialize(Seed);
}

void URosLidarComponent::FinishScan()
{
    FScan Scan;Scan.Sequence=++ScanSequence;Scan.Start=FrameStart;Scan.End=FrameEnd;Scan.Bytes=MoveTemp(Building);
    if(Pending.Num()==2){Pending.RemoveAt(0,1,EAllowShrinking::No);++DroppedScans;}
    Pending.Add(MoveTemp(Scan));Building.Reset();
}

void URosLidarComponent::Advance(double Time,bool Connected)
{
    if(!Rig||!FMath::IsFinite(Time)||Time<0)return;
    if(MotionGeneration!=Rig->GetMotionGeneration())
    {MotionGeneration=Rig->GetMotionGeneration();DiscardPending();++HistoryResets;}
    if(!Connected){DiscardPending();return;}
    if(PreviousTime<0){PreviousTime=Time;FrameStart=Time;FrameEnd=Time;return;}
    const double Dt=Time-PreviousTime;PreviousTime=Time;
    if(Dt<=0)return;
    MaxStep=FMath::Max(MaxStep,Dt);
    if(Time-FrameStart+1.e-9>=1./FrameHz)
    {FinishScan();FrameStart=Time;FrameEnd=Time;}
    // All rays in this physical frame share its actual pose/time. No invented subframe poses.
    const double Requested=Dt*RaysPerSecond+RayRemainder;
    const int64 Due=FMath::FloorToInt64(Requested);RayRemainder=Requested-Due;
    const int32 Count=int32(FMath::Min<int64>(Due,FMath::Min(4096,65536-Building.Num()/20)));
    MissedRays+=Due-Count;
    const FTransform WorldMount=Mount*Rig->GetActorTransform();
    const FVector Start=WorldMount.GetLocation();
    FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimLidar),true);
    // Self geometry is deliberately not ignored: placement and occlusion must be physical.
    for(int32 I=0;I<Count;++I)
    {
        const int64 Index=RayIndex++;
        const double Azimuth=2*PI*FMath::Frac((Index+.5)*.6180339887498949);
        const double Z=FMath::Lerp(FMath::Sin(MinElevation),FMath::Sin(MaxElevation),FMath::Frac((Index+.5)*.7548776662466927));
        const double XY=FMath::Sqrt(FMath::Max(0.,1-Z*Z));
        const FVector Local(XY*FMath::Cos(Azimuth),-XY*FMath::Sin(Azimuth),Z);
        const FVector Direction=WorldMount.TransformVectorNoScale(Local);
        FHitResult Hit;++Traced;
        if(!GetWorld()->LineTraceSingleByChannel(Hit,Start,Start+Direction*(MaxRange*100),ECC_Visibility,Query))continue;
        double Distance=Hit.Distance/100.;if(Distance<MinRange||Distance>MaxRange)continue;
        if(NoiseStd>0)Distance+=NoiseStd*FMath::Sqrt(-2*FMath::Loge(FMath::Max(double(Random.FRand()),1.e-12)))*FMath::Cos(2*PI*Random.FRand());
        if(Distance<MinRange||Distance>MaxRange)continue;
        const FVector Point(Local.X*Distance,-Local.Y*Distance,Local.Z*Distance);
        LidarU32(Building,uint32(FMath::RoundToInt64((Time-FrameStart)*1.e9)));
        LidarFloat(Building,float(Point.X));LidarFloat(Building,float(Point.Y));LidarFloat(Building,float(Point.Z));
        Building.Add(uint8(Reflectivity));Building.Add(0);Building.Add(uint8(Index%4));Building.Add(0);++Hits;
    }
    RayIndex+=Due-Count;FrameEnd=Time;
}

TSharedRef<FJsonObject> URosLidarComponent::TakeState(TArray<uint8>& Bytes,int32 Offset)
{
    auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("id"),Id);J->SetStringField(TEXT("profile_md5"),ProfileHash);
    J->SetBoolField(TEXT("calibrated"),false);J->SetStringField(TEXT("model"),TEXT("ideal_single_return_raycast; spherical_fibonacci_not_device_pattern; constant_reflectivity; physics_frame_time_quantization"));
    J->SetStringField(TEXT("frame_id"),Rig->GetVehicleId()+TEXT("/")+Id+TEXT("_link"));
    J->SetStringField(TEXT("parent_frame_id"),Rig->GetVehicleId()+TEXT("/base_link"));
    auto Pose=MakeShared<FJsonObject>();Pose->SetArrayField(TEXT("position_m"),LidarVec(Origin));const auto Q=Mount.GetRotation();
    Pose->SetArrayField(TEXT("rotation_xyzw"),{MakeShared<FJsonValueNumber>(-Q.X),MakeShared<FJsonValueNumber>(Q.Y),MakeShared<FJsonValueNumber>(-Q.Z),MakeShared<FJsonValueNumber>(Q.W)});J->SetObjectField(TEXT("pose_in_base"),Pose);
    J->SetNumberField(TEXT("rays_per_second"),RaysPerSecond);J->SetNumberField(TEXT("frame_hz"),FrameHz);
    J->SetNumberField(TEXT("min_range_m"),MinRange);J->SetNumberField(TEXT("max_range_m"),MaxRange);
    J->SetNumberField(TEXT("elevation_min_rad"),MinElevation);J->SetNumberField(TEXT("elevation_max_rad"),MaxElevation);
    J->SetNumberField(TEXT("traced_rays"),Traced);J->SetNumberField(TEXT("returns"),Hits);J->SetNumberField(TEXT("missed_rays"),MissedRays);
    J->SetNumberField(TEXT("dropped_scans"),DroppedScans);J->SetNumberField(TEXT("history_resets"),HistoryResets);J->SetNumberField(TEXT("max_physics_step_s"),MaxStep);
    TArray<TSharedPtr<FJsonValue>> Scans;
    for(auto& Scan:Pending)
    {
        auto S=MakeShared<FJsonObject>();S->SetNumberField(TEXT("seq"),Scan.Sequence);
        S->SetNumberField(TEXT("start_ns"),double(FMath::RoundToInt64(Scan.Start*1.e9)));S->SetNumberField(TEXT("end_ns"),double(FMath::RoundToInt64(Scan.End*1.e9)));
        S->SetNumberField(TEXT("point_count"),Scan.Bytes.Num()/20);S->SetNumberField(TEXT("byte_offset"),Offset+Bytes.Num());S->SetNumberField(TEXT("byte_length"),Scan.Bytes.Num());
        Bytes.Append(Scan.Bytes);Scans.Add(MakeShared<FJsonValueObject>(S));
    }
    Pending.Reset();J->SetArrayField(TEXT("scans"),Scans);return J;
}
