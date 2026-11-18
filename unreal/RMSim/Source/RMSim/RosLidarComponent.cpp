#include "RosLidarComponent.h"
#include "Async/ParallelFor.h"
#include "PhysicsEngine/ClusterUnionComponent.h"
#include "VehicleRig.h"
#include "RosCoordinates.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "Components/PrimitiveComponent.h"
#include "Materials/MaterialInterface.h"
#include "Misc/FileHelper.h"
#include "Misc/SecureHash.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "HAL/PlatformTime.h"
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
    if(J->Values.Num()!=15||!J->TryGetStringField(TEXT("schema"),Schema)||(Schema!=TEXT("rm_lidar_runtime_v1")&&Schema!=TEXT("rm_lidar_runtime_v2"))||
       !J->TryGetBoolField(TEXT("calibrated"),Calibrated)||Calibrated||!J->TryGetStringField(TEXT("id"),Id)||Id.IsEmpty()||Id.Len()>64||Id==TEXT("base")||
       !FChar::IsAlpha(Id[0])||!J->TryGetStringField(TEXT("parent_frame"),Parent)||(Parent!=TEXT("base_link")&&(Schema!=TEXT("rm_lidar_runtime_v2")||Parent!=TEXT("yaw_link")))||
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
    ParentFrame=Parent;bArticulated=Parent!=TEXT("base_link");
    FTransform ParentPose;if(!Vehicle->GetSensorParentWorld(Parent,ParentPose)){Error=TEXT("unknown LiDAR parent joint");return false;}
    if(bArticulated)Vehicle->EnableArticulatedSensorQueries();
    Rig=Vehicle;Mount=FTransform(RosCoordinates::Rotation(Rpy),RosCoordinates::Position(Origin));
    bProfileTiming=FParse::Param(FCommandLine::Get(),TEXT("ProfileSensorTiming"));
    bSerialQuery=FParse::Param(FCommandLine::Get(),TEXT("LidarSerialQuery"));
    bVerifyQueries=FParse::Param(FCommandLine::Get(),TEXT("VerifyLidarQueries"));
    if(bSerialQuery&&bVerifyQueries)
    {Error=TEXT("VerifyLidarQueries requires the parallel query path");return false;}
    UE_LOG(LogRMSim,Display,TEXT("LIDAR_QUERY_MODE mode=%s verify_first_rays=%lld"),
        bSerialQuery?TEXT("serial"):TEXT("parallel_staged"),bVerifyQueries?MaxVerificationRays:0);
    Seed=int32(SeedValue);Reflectivity=int32(ReflectivityValue);Random.Initialize(Seed);
    ProfileHash=LexToString(FMD5Hash::HashFile(*Profile));MotionGeneration=Rig->GetMotionGeneration();
    UE_LOG(LogRMSim,Display,TEXT("LIDAR_READY pattern=spherical_fibonacci rays_s=%.0f frame_hz=%.1f mount_uncalibrated=true"),RaysPerSecond,FrameHz);
    return true;
}

void URosLidarComponent::DiscardPending()
{Pending.Reset();Building.Reset();PreviousTime=-1;FrameStart=-1;RayRemainder=0;Cadence.Reset();}

void URosLidarComponent::Reset()
{
    DiscardPending();RayIndex=0;Traced=0;Hits=0;MissedRays=0;DroppedScans=0;ScanSequence=0;HistoryResets=0;MaxStep=0;
    MotionGeneration=Rig->GetMotionGeneration();Random.Initialize(Seed);WindowPasses=0;SelfBlocked=0;
    VerifiedRays=0;VerifiedQueries=0;VerificationMaxErrorCm=0;
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
    if(PreviousTime<0){PreviousTime=Time;FrameStart=Time;FrameEnd=Time;Cadence.Start(Time,FrameHz);return;}
    const double Dt=Time-PreviousTime;PreviousTime=Time;
    if(Dt<=0)return;
    MaxStep=FMath::Max(MaxStep,Dt);
    if(Cadence.Advance(Time))
    {
        // Header/end and point offsets retain actual sampled physics times;
        // only the window deadline is phase-locked to the scan epoch.
        FinishScan();FrameStart=Time;FrameEnd=Time;
    }
    // All rays in this physical frame share its actual pose/time. No invented subframe poses.
    const double Requested=Dt*RaysPerSecond+RayRemainder;
    const int64 Due=FMath::FloorToInt64(Requested);RayRemainder=Requested-Due;
    const int32 Count=int32(FMath::Min<int64>(Due,FMath::Min(4096,65536-Building.Num()/20)));
    MissedRays+=Due-Count;
    FTransform ParentPose;if(!Rig->GetSensorParentWorld(ParentFrame,ParentPose))return;
    const FTransform WorldMount=Mount*ParentPose;
    const FVector Start=WorldMount.GetLocation();
    // The game thread waits for each stage after synchronous Chaos has completed.
    // UE 5.8 SceneQuery.cpp holds FScopedSceneReadLock around each ray query;
    // workers only read that scene and write their own result. UObject/material
    // inspection, optical decisions, random noise and serialization stay here.
    check(IsInGameThread());
    UWorld* World=GetWorld();
    FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimLidar),true);
    Query.bReturnFaceIndex=bArticulated;
    const ECollisionChannel Channel=bArticulated?ECC_GameTraceChannel1:ECC_Visibility;
    struct FRay
    {
        int64 Index=0;
        FVector Local,Direction,RayStart,End;
        FHitResult Hit;
        bool bHit=false,bFound=false;
    };
    TArray<FRay> Rays;Rays.SetNum(Count);
    TArray<int32> Active,Next;Active.Reserve(Count);Next.Reserve(Count);
    const double RayWallStart=bProfileTiming?FPlatformTime::Seconds():0;
    for(int32 I=0;I<Count;++I)
    {
        FRay& Ray=Rays[I];Ray.Index=RayIndex++;
        const double Azimuth=2*PI*FMath::Frac((Ray.Index+.5)*.6180339887498949);
        const double Z=FMath::Lerp(FMath::Sin(MinElevation),FMath::Sin(MaxElevation),FMath::Frac((Ray.Index+.5)*.7548776662466927));
        const double XY=FMath::Sqrt(FMath::Max(0.,1-Z*Z));
        Ray.Local=FVector(XY*FMath::Cos(Azimuth),-XY*FMath::Sin(Azimuth),Z);
        Ray.Direction=WorldMount.TransformVectorNoScale(Ray.Local);
        Ray.RayStart=Start;Ray.End=Start+Ray.Direction*(MaxRange*100);
        Active.Add(I);
    }
    Traced+=Count;
    const int32 VerifyCount=bVerifyQueries?int32(FMath::Min<int64>(Count,MaxVerificationRays-VerifiedRays)):0;
    int32 FaceLogBudget=int32(FMath::Max<int64>(0,8-SelfBlocked));
    // Preserve all optical intersections, including opaque parts of this robot.
    for(int32 Pass=0;Pass<(bArticulated?16:1)&&!Active.IsEmpty();++Pass)
    {
        const bool Parallel=!bSerialQuery&&Active.Num()>=128;
        auto Trace=[&](int32 ActiveIndex)
        {
            FRay& Ray=Rays[Active[ActiveIndex]];
            FCollisionQueryParams LocalQuery=Query;
            // Cluster union sub-tracing invokes GT-only component methods. Query
            // their outer shape here, then repeat those rare hits on GT below.
            if(Parallel)LocalQuery.bTraceIntoSubComponents=false;
            Ray.bHit=World->LineTraceSingleByChannel(Ray.Hit,Ray.RayStart,Ray.End,Channel,LocalQuery);
        };
        if(Parallel)ParallelFor(TEXT("RMSimLidarQueries"),Active.Num(),32,Trace,EParallelForFlags::Unbalanced);
        else for(int32 I=0;I<Active.Num();++I)Trace(I);
        Next.Reset();
        for(int32 I:Active)
        {
            FRay& Ray=Rays[I];
            if(Parallel&&Ray.bHit&&Cast<UClusterUnionComponent>(Ray.Hit.GetComponent()))
                Ray.bHit=World->LineTraceSingleByChannel(Ray.Hit,Ray.RayStart,Ray.End,Channel,Query);
            if(I<VerifyCount)
            {
                FHitResult SerialHit;
                const bool SerialFound=World->LineTraceSingleByChannel(SerialHit,Ray.RayStart,Ray.End,Channel,Query);
                const double ErrorCm=Ray.bHit&&SerialFound?(SerialHit.ImpactPoint-Ray.Hit.ImpactPoint).Size():0.;
                VerificationMaxErrorCm=FMath::Max(VerificationMaxErrorCm,ErrorCm);
                ++VerifiedQueries;
                const bool Same=SerialFound==Ray.bHit&&(!SerialFound||
                    (SerialHit.GetActor()==Ray.Hit.GetActor()&&SerialHit.GetComponent()==Ray.Hit.GetComponent()&&
                     SerialHit.FaceIndex==Ray.Hit.FaceIndex&&SerialHit.bBlockingHit==Ray.Hit.bBlockingHit&&
                     SerialHit.bStartPenetrating==Ray.Hit.bStartPenetrating&&ErrorCm<=1.e-4&&
                     FMath::Abs(double(SerialHit.Distance)-double(Ray.Hit.Distance))<=1.e-4));
                if(!Same)
                    UE_LOG(LogRMSim,Fatal,TEXT("LIDAR_QUERY_VERIFICATION result=failed ray=%lld pass=%d parallel_hit=%d serial_hit=%d parallel_face=%d serial_face=%d impact_error_cm=%.9f"),
                        Ray.Index,Pass,Ray.bHit,SerialFound,Ray.Hit.FaceIndex,SerialHit.FaceIndex,ErrorCm);
            }
            if(!Ray.bHit)continue;
            const FHitResult& Hit=Ray.Hit;
            bool Window=false;
            if(bArticulated&&Hit.GetActor()==Rig&&Hit.GetComponent()&&Hit.FaceIndex!=INDEX_NONE)
            {
                int32 Section=INDEX_NONE;const auto* Material=Hit.GetComponent()->GetMaterialFromCollisionFaceIndex(Hit.FaceIndex,Section);
                Window=Material&&(Material->GetName()==TEXT("aim_sentry_mid360_blue_glass")||Material->GetName()==TEXT("aim_sentry_radar_glass"));
                if(FaceLogBudget>0)
                {--FaceLogBudget;UE_LOG(LogRMSim,Display,TEXT("LIDAR_SELF_FACE comp=%s face=%d section=%d material=%s distance_cm=%.6f window=%d"),*Hit.GetComponent()->GetName(),Hit.FaceIndex,Section,Material?*Material->GetName():TEXT("none"),Hit.Distance,Window);}
            }
            if(!Window){Ray.bFound=true;continue;}
            ++WindowPasses;Ray.RayStart=Hit.ImpactPoint+Ray.Direction*.02;
            Next.Add(I);
        }
        Swap(Active,Next);
    }
    // Original ray order retains deterministic return order and seeded noise.
    for(const FRay& Ray:Rays)
    {
        if(!Ray.bFound)continue;
        const FHitResult& Hit=Ray.Hit;
        double Distance=(Hit.ImpactPoint-Start).Size()/100.;
        if(Hit.GetActor()==Rig){if(SelfBlocked<8)UE_LOG(LogRMSim,Display,TEXT("LIDAR_SELF_BLOCK comp=%s face=%d distance_m=%.6f penetrating=%d"),*Hit.GetComponent()->GetName(),Hit.FaceIndex,Distance,Hit.bStartPenetrating);++SelfBlocked;}
        if(Distance<MinRange||Distance>MaxRange)continue;
        if(NoiseStd>0)Distance+=NoiseStd*FMath::Sqrt(-2*FMath::Loge(FMath::Max(double(Random.FRand()),1.e-12)))*FMath::Cos(2*PI*Random.FRand());
        if(Distance<MinRange||Distance>MaxRange)continue;
        const FVector Point(Ray.Local.X*Distance,-Ray.Local.Y*Distance,Ray.Local.Z*Distance);
        LidarU32(Building,uint32(FMath::RoundToInt64((Time-FrameStart)*1.e9)));
        LidarFloat(Building,float(Point.X));LidarFloat(Building,float(Point.Y));LidarFloat(Building,float(Point.Z));
        Building.Add(uint8(Reflectivity));Building.Add(0);Building.Add(uint8(Ray.Index%4));Building.Add(0);++Hits;
    }
    if(VerifyCount>0)
    {
        VerifiedRays+=VerifyCount;
        if(VerifiedRays==MaxVerificationRays)
            UE_LOG(LogRMSim,Display,TEXT("LIDAR_QUERY_VERIFICATION result=passed rays=%lld scene_queries=%lld max_impact_error_cm=%.9f"),
                VerifiedRays,VerifiedQueries,VerificationMaxErrorCm);
    }
    if(bProfileTiming)
    {
        ProfileRayWall+=FPlatformTime::Seconds()-RayWallStart;ProfileRayCount+=Count;
        if(ProfileStartSim==0)ProfileStartSim=Time;
        if(Time-ProfileStartSim>=5.)
        {
            UE_LOG(LogRMSim,Display,TEXT("RMSIM_LIDAR_TIMING rays=%lld wall_total_ms=%.3f wall_us_per_ray=%.3f sim_elapsed=%.3f query_mode=%s"),
                ProfileRayCount,ProfileRayWall*1000.,ProfileRayWall*1.e6/FMath::Max(1.,double(ProfileRayCount)),Time-ProfileStartSim,bSerialQuery?TEXT("serial"):TEXT("parallel_staged"));
            ProfileRayCount=0;ProfileRayWall=0;ProfileStartSim=Time;
        }
    }
    RayIndex+=Due-Count;FrameEnd=Time;
}

TSharedRef<FJsonObject> URosLidarComponent::TakeState(TArray<uint8>& Bytes,int32 Offset)
{
    auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("id"),Id);J->SetStringField(TEXT("profile_md5"),ProfileHash);
    J->SetBoolField(TEXT("calibrated"),false);J->SetStringField(TEXT("model"),TEXT("ideal_single_return_raycast; spherical_fibonacci_not_device_pattern; constant_reflectivity; physics_frame_time_quantization"));
    J->SetStringField(TEXT("frame_id"),Rig->GetVehicleId()+TEXT("/")+Id+TEXT("_link"));
    J->SetStringField(TEXT("parent_frame_id"),Rig->GetVehicleId()+TEXT("/")+ParentFrame);
    auto Pose=MakeShared<FJsonObject>();Pose->SetArrayField(TEXT("position_m"),LidarVec(Origin));const auto Q=Mount.GetRotation();
    Pose->SetArrayField(TEXT("rotation_xyzw"),{MakeShared<FJsonValueNumber>(-Q.X),MakeShared<FJsonValueNumber>(Q.Y),MakeShared<FJsonValueNumber>(-Q.Z),MakeShared<FJsonValueNumber>(Q.W)});J->SetObjectField(bArticulated?TEXT("pose_in_parent"):TEXT("pose_in_base"),Pose);
    if(bArticulated){J->SetNumberField(TEXT("mount_schema"),2);J->SetNumberField(TEXT("optical_window_passes"),WindowPasses);J->SetNumberField(TEXT("self_occluded_rays"),SelfBlocked);}
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
