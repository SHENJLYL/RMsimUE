#include "RosLidarComponent.h"
#include "Async/ParallelFor.h"
#include "Physics/Experimental/PhysScene_Chaos.h"
#include "PBDRigidsSolver.h"
#include "Framework/Threading.h"
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

bool URosLidarComponent::Initialize(AVehicleRig* Vehicle,const FString& Profile,FString& Error,double RayScale)
{
    FString Text;TSharedPtr<FJsonObject> J;
    if(!Vehicle||!Vehicle->IsContactPhysics()||!FFileHelper::LoadFileToString(Text,*Profile)||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),J)||!J.IsValid())
    {Error=TEXT("cannot read physical LiDAR runtime profile");return false;}
    FString Schema,Parent,Pattern;J->TryGetStringField(TEXT("schema"),Schema);bMid360=Schema==TEXT("rm_lidar_runtime_v3");bool Calibrated=true;const TSharedPtr<FJsonObject>* Pose;FVector Rpy;
    double SeedValue=0,ReflectivityValue=0;
    if(J->Values.Num()!=(bMid360?16:15)||!J->TryGetStringField(TEXT("schema"),Schema)||(Schema!=TEXT("rm_lidar_runtime_v1")&&Schema!=TEXT("rm_lidar_runtime_v2")&&!bMid360)||
       !J->TryGetBoolField(TEXT("calibrated"),Calibrated)||Calibrated||!J->TryGetStringField(TEXT("id"),Id)||Id.IsEmpty()||Id.Len()>64||Id==TEXT("base")||
       !FChar::IsAlpha(Id[0])||!J->TryGetStringField(TEXT("parent_frame"),Parent)||(Parent!=TEXT("base_link")&&((Schema!=TEXT("rm_lidar_runtime_v2")&&!bMid360)||Parent!=TEXT("yaw_link")))||
       !J->TryGetStringField(TEXT("pattern"),Pattern)||Pattern!=TEXT("spherical_fibonacci")||
       !J->TryGetObjectField(TEXT("origin"),Pose)||(*Pose)->Values.Num()!=2||!LidarVector(*Pose,TEXT("xyz"),Origin,5)||!LidarVector(*Pose,TEXT("rpy"),Rpy,2*PI)||
       !LidarNumber(J,TEXT("rays_per_second"),RaysPerSecond,100,200000)||
       !LidarNumber(J,TEXT("frame_hz"),FrameHz,1,bMid360?100:30)||RaysPerSecond/FrameHz>65536||
       !LidarNumber(J,TEXT("elevation_min_rad"),MinElevation,-PI/2,PI/2)||
       !LidarNumber(J,TEXT("elevation_max_rad"),MaxElevation,MinElevation+.001,PI/2)||
       !LidarNumber(J,TEXT("min_range_m"),MinRange,.01,10)||!LidarNumber(J,TEXT("max_range_m"),MaxRange,MinRange+.01,200)||
       !LidarNumber(J,TEXT("range_noise_std_m"),NoiseStd,0,1)||
       !LidarNumber(J,TEXT("reflectivity"),ReflectivityValue,0,255)||FMath::FloorToDouble(ReflectivityValue)!=ReflectivityValue||
       !LidarNumber(J,TEXT("noise_seed"),SeedValue,0,2147483647)||FMath::FloorToDouble(SeedValue)!=SeedValue)
    {Error=TEXT("invalid LiDAR identity, mount, scan or range configuration");return false;}
    if(FMath::IsFinite(RayScale)&&RayScale>0.0&&RayScale<0.999)
    {
        const double ClampedScale=FMath::Clamp(RayScale,0.1,1.0);
        RaysPerSecond=FMath::Max(bMid360?20000.0:1000.0,FMath::RoundToDouble(RaysPerSecond*ClampedScale));
    }
    if(bMid360&&(!Vehicle->IsMujocoPhysics()||!InitializeMid360(J,Profile,Error)))
    {if(Error.IsEmpty())Error=TEXT("MID360 v3 requires native MuJoCo pose history");return false;}
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
    UE_LOG(LogRMSim,Display,TEXT("LIDAR_READY pattern=%s rays_s=%.0f frame_hz=%.1f mount_uncalibrated=true"),
        bMid360?(RecordedDirections.IsEmpty()?TEXT("spherical_fibonacci_approx"):TEXT("recorded_direction_replay")):TEXT("spherical_fibonacci"),RaysPerSecond,FrameHz);
    return true;
}

void URosLidarComponent::DiscardPending()
{
    Pending.Reset();
    // Reconnect discards delivery backlog, while the physical emission phase continues.
    if(bMid360)
    {
        // Buffered slots already belong to traced/missed; delivery_suppressed counts only untraced gate slots.
        Building.Reset();CurrentScanOrdinal=-1;FrameStart=-1;
        if(!RMSimSensors::Mid360Model::SupportedFrameRate(FrameHz)){DeliveryStartOrdinal=0;return;}
        const int64 Period=RMSimSensors::Mid360Model::ScanPeriodNs(FrameHz);
        const int64 Now=FMath::Max<int64>(0,FMath::RoundToInt64(PreviousTime*1.e9));
        DeliveryStartOrdinal=Now/Period+(Now%Period?1:0);
    }
    else {Building.Reset();PreviousTime=-1;FrameStart=-1;RayRemainder=0;Cadence.Reset();}
}

void URosLidarComponent::Reset()
{
    DiscardPending();Building.Reset();PreviousTime=-1;FrameStart=-1;FrameEnd=0;RayRemainder=0;Cadence.Reset();
    PulseClock.Reset();CurrentScanOrdinal=-1;DeliveryStartOrdinal=DeliverySuppressedSlots=0;NoHitRays=TooNearRays=TooFarRays=WeakReturnRays=MissingPoseRays=0;
    DisconnectedRays=OpticalLimitRays=NoiseRejectedRays=SkippedScanWindows=DefaultMaterialRays=IncompleteScanDrops=0;
    ProfileRayWall=ProfileSetupWall=ProfileQueryWall=ProfileOpticalWall=ProfilePackWall=ProfileStartSim=0;ProfileRayCount=0;
    RayIndex=0;Traced=0;Hits=0;MissedRays=0;DroppedScans=0;ScanSequence=0;HistoryResets=0;MaxStep=0;
    MotionGeneration=Rig->GetMotionGeneration();Random.Initialize(Seed);WindowPasses=0;SelfBlocked=0;
    VerifiedRays=0;VerifiedQueries=0;VerificationMaxErrorCm=0;
}

void URosLidarComponent::FinishScan()
{
    if(bMid360&&Building.Num()/20!=RMSimSensors::Mid360Model::ScanPeriodNs(FrameHz)/RMSimSensors::Mid360Model::PointIntervalNs)
    {++IncompleteScanDrops;++DroppedScans;Building.Reset();return;}
    FScan Scan;Scan.Sequence=++ScanSequence;Scan.Start=FrameStart;Scan.End=FrameEnd;Scan.Bytes=MoveTemp(Building);
    if(Pending.Num()>=PendingScanLimit){Pending.RemoveAt(0,1,EAllowShrinking::No);++DroppedScans;}
    Pending.Add(MoveTemp(Scan));Building.Reset();
}

void URosLidarComponent::Advance(double Time,bool Connected,double EpochSolverTime)
{
    if(bMid360){AdvanceMid360(Time,Connected,EpochSolverTime);return;}
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
    TArray<FRay> Rays;Rays.SetNum(Count);
    const double RayWallStart=bProfileTiming?FPlatformTime::Seconds():0;
    for(int32 I=0;I<Count;++I)
    {
        FRay& Ray=Rays[I];Ray.Index=RayIndex++;
        const double Azimuth=2*PI*FMath::Frac((Ray.Index+.5)*.6180339887498949);
        const double Z=FMath::Lerp(FMath::Sin(MinElevation),FMath::Sin(MaxElevation),FMath::Frac((Ray.Index+.5)*.7548776662466927));
        const double XY=FMath::Sqrt(FMath::Max(0.,1-Z*Z));
        Ray.Local=FVector(XY*FMath::Cos(Azimuth),-XY*FMath::Sin(Azimuth),Z);
        Ray.Direction=WorldMount.TransformVectorNoScale(Ray.Local);
        Ray.Start=Start;Ray.RayStart=Start;Ray.End=Start+Ray.Direction*(MaxRange*100);
    }
    if(bProfileTiming)ProfileSetupWall+=FPlatformTime::Seconds()-RayWallStart;
    TraceRays(Rays);
    const double PackStarted=bProfileTiming?FPlatformTime::Seconds():0.;
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
    if(bProfileTiming)ProfilePackWall+=FPlatformTime::Seconds()-PackStarted;
    LogTiming(Time,RayWallStart,Count);
    RayIndex+=Due-Count;FrameEnd=Time;
}

void URosLidarComponent::TraceRays(TArray<FRay>& Rays)
{
    // The game thread waits for each stage after synchronous Chaos has completed.
    // UE 5.8 SceneQuery.cpp holds FScopedSceneReadLock around each ray query;
    // workers only read that scene and write their own result. UObject/material
    // inspection, optical decisions, random noise and serialization stay here.
    check(IsInGameThread());
    UWorld* World=GetWorld();
    const auto* PhysicsScene=World->GetPhysicsScene();
    auto* Solver=PhysicsScene?PhysicsScene->GetSolver():nullptr;
    // A custom query handler may own a different locking or callback contract.
    Chaos::FPhysSceneLock* const SceneReadLock=Solver&&World->PhysicsQueryHandler==nullptr?&Solver->GetExternalDataLock_External():nullptr;
    FCollisionQueryParams Query(SCENE_QUERY_STAT(RMSimLidar),true);
    Query.bReturnFaceIndex=bArticulated||bMid360;
    const ECollisionChannel Channel=bArticulated?ECC_GameTraceChannel1:ECC_Visibility;
    const int32 Count=Rays.Num();
    TArray<int32> Active,Next;Active.Reserve(Count);Next.Reserve(Count);
    for(int32 I=0;I<Count;++I)if(Rays[I].bPoseValid)Active.Add(I);
    Traced+=Active.Num();
    // This cache lives for one synchronous GT batch, including all window passes.
    // Material changes between batches are observed on the next call.
    struct FMaterialOptics
    {
        double NirReflectance=0;
        bool bConfigured=false;
        bool bWindow=false;
    };
    TMap<const UMaterialInterface*,FMaterialOptics> MaterialCache;
    if(!Active.IsEmpty())MaterialCache.Reserve(16);
    const bool NeedsNirMaterials=bMid360&&!NirMaterialReflectance.IsEmpty();
    int32 VerifyCount=0;
    if(bVerifyQueries)for(int32 I:Active)
        if(VerifiedRays+VerifyCount<MaxVerificationRays){Rays[I].bVerify=true;++VerifyCount;}
    int32 FaceLogBudget=Traced==Active.Num()?8:0; // At most8 diagnostics in the first traced batch per epoch.
    // Preserve all optical intersections, including opaque parts of this robot.
    for(int32 Pass=0;Pass<(bArticulated?16:1)&&!Active.IsEmpty();++Pass)
    {
        const bool Parallel=!bSerialQuery&&Active.Num()>=128;
        auto TraceRange=[&](int32 Begin,int32 End)
        {
            // Query params contain mutable ignore-list bookkeeping: keep one
            // independent copy per work block, reused only by that block.
            FCollisionQueryParams LocalQuery=Query;
            // Cluster union sub-tracing invokes GT-only component methods. Query
            // their outer shape here, then repeat those rare hits on GT below.
            if(Parallel)LocalQuery.bTraceIntoSubComponents=false;
            const auto TraceQueries=[&]
            {
                for(int32 ActiveIndex=Begin;ActiveIndex<End;++ActiveIndex)
                {
                    FRay& Ray=Rays[Active[ActiveIndex]];
                    Ray.bHit=World->LineTraceSingleByChannel(Ray.Hit,Ray.RayStart,Ray.End,Channel,LocalQuery);
                }
            };
            if(Parallel&&SceneReadLock)
            {
                // Same solver external lock as each ray's FScopedSceneReadLock.
                // Release within this worker block, before the ParallelFor join
                // or GT ClusterUnion/material callbacks; inner ray locks remain.
                Chaos::FPhysicsSceneGuardScopedRead BlockRead(*SceneReadLock);
                TraceQueries();
            }
            else TraceQueries();
        };
        const double QueryStarted=bProfileTiming?FPlatformTime::Seconds():0.;
        if(Parallel)
        {
            // UE Unbalanced dispatches one body call per scheduling item.
            // Group actual rays explicitly, retaining every per-ray API and lock.
            constexpr int32 RaysPerBlock=32;
            const int32 Blocks=FMath::DivideAndRoundUp(Active.Num(),RaysPerBlock);
            ParallelFor(TEXT("RMSimLidarQueries"),Blocks,1,[&](int32 Block)
            {
                const int32 Begin=Block*RaysPerBlock;
                TraceRange(Begin,FMath::Min(Begin+RaysPerBlock,Active.Num()));
            },EParallelForFlags::Unbalanced);
        }
        else TraceRange(0,Active.Num());
        if(bProfileTiming)ProfileQueryWall+=FPlatformTime::Seconds()-QueryStarted;
        const double OpticalStarted=bProfileTiming?FPlatformTime::Seconds():0.;
        Next.Reset();
        for(int32 I:Active)
        {
            FRay& Ray=Rays[I];
            if(Parallel&&Ray.bHit&&Cast<UClusterUnionComponent>(Ray.Hit.GetComponent()))
                Ray.bHit=World->LineTraceSingleByChannel(Ray.Hit,Ray.RayStart,Ray.End,Channel,Query);
            if(Ray.bVerify)
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
            const bool SelfArticulated=bArticulated&&Hit.GetActor()==Rig;
            const UPrimitiveComponent* Component=Hit.GetComponent();
            const UMaterialInterface* Material=nullptr;
            int32 Section=INDEX_NONE;
            // With no NIR overrides, environment material cannot change the result.
            // Self-window classification still resolves the actual collision face.
            if((NeedsNirMaterials||SelfArticulated)&&Component&&Hit.FaceIndex!=INDEX_NONE)
                Material=Component->GetMaterialFromCollisionFaceIndex(Hit.FaceIndex,Section);
            FMaterialOptics Optical;Optical.NirReflectance=DefaultNirReflectance;
            if(Material)
            {
                if(const auto* Cached=MaterialCache.Find(Material))Optical=*Cached;
                else
                {
                    const FString MaterialName=Material->GetName();
                    if(NeedsNirMaterials)
                    {
                        if(const double* Configured=NirMaterialReflectance.Find(MaterialName))
                        {Optical.NirReflectance=*Configured;Optical.bConfigured=true;}
                    }
                    Optical.bWindow=MaterialName==TEXT("aim_sentry_mid360_blue_glass")||MaterialName==TEXT("aim_sentry_radar_glass");
                    MaterialCache.Add(Material,Optical);
                }
            }
            const bool Window=SelfArticulated&&Optical.bWindow;
            if(Ray.bVerify)
            {
                // Check the optimization against the previous per-hit classification.
                // This shadow path only runs for the existing first4096-ray audit.
                double ReferenceNir=DefaultNirReflectance;bool ReferenceConfigured=false,ReferenceWindow=false;
                if(bMid360)
                {
                    const UMaterialInterface* ReferenceMaterial=nullptr;
                    if(Component&&Hit.FaceIndex!=INDEX_NONE)
                    {int32 ReferenceSection=INDEX_NONE;ReferenceMaterial=Component->GetMaterialFromCollisionFaceIndex(Hit.FaceIndex,ReferenceSection);}
                    const double* Configured=ReferenceMaterial?NirMaterialReflectance.Find(ReferenceMaterial->GetName()):nullptr;
                    ReferenceNir=Configured?*Configured:DefaultNirReflectance;ReferenceConfigured=Configured!=nullptr;
                }
                if(SelfArticulated&&Component&&Hit.FaceIndex!=INDEX_NONE)
                {
                    int32 ReferenceSection=INDEX_NONE;const auto* ReferenceMaterial=Component->GetMaterialFromCollisionFaceIndex(Hit.FaceIndex,ReferenceSection);
                    ReferenceWindow=ReferenceMaterial&&(ReferenceMaterial->GetName()==TEXT("aim_sentry_mid360_blue_glass")||ReferenceMaterial->GetName()==TEXT("aim_sentry_radar_glass"));
                }
                if(Window!=ReferenceWindow||(bMid360&&(Optical.bConfigured!=ReferenceConfigured||Optical.NirReflectance!=ReferenceNir)))
                    UE_LOG(LogRMSim,Fatal,TEXT("LIDAR_MATERIAL_VERIFICATION result=failed ray=%lld pass=%d window=%d reference_window=%d configured=%d reference_configured=%d nir=%.17g reference_nir=%.17g"),
                        Ray.Index,Pass,Window,ReferenceWindow,Optical.bConfigured,ReferenceConfigured,Optical.NirReflectance,ReferenceNir);
            }
            if(bMid360)
            {
                Ray.NirReflectance=Optical.NirReflectance;
                if(!Optical.bConfigured)++DefaultMaterialRays;
            }
            if(SelfArticulated&&Component&&Hit.FaceIndex!=INDEX_NONE&&FaceLogBudget>0)
            {--FaceLogBudget;UE_LOG(LogRMSim,Display,TEXT("LIDAR_SELF_FACE comp=%s face=%d section=%d material=%s distance_cm=%.6f window=%d"),*Component->GetName(),Hit.FaceIndex,Section,Material?*Material->GetName():TEXT("none"),Hit.Distance,Window);}
            if(!Window){Ray.bFound=true;continue;}
            ++WindowPasses;Ray.RayStart=Hit.ImpactPoint+Ray.Direction*.02;
            Next.Add(I);
        }
        Swap(Active,Next);
        if(bProfileTiming)ProfileOpticalWall+=FPlatformTime::Seconds()-OpticalStarted;
    }
    if(bMid360){OpticalLimitRays+=Active.Num();for(int32 I:Active)Rays[I].bOpticalLimit=true;}
    if(VerifyCount>0)
    {
        VerifiedRays+=VerifyCount;
        if(VerifiedRays==MaxVerificationRays)
            UE_LOG(LogRMSim,Display,TEXT("LIDAR_QUERY_VERIFICATION result=passed rays=%lld scene_queries=%lld max_impact_error_cm=%.9f material_equivalence=passed"),
                VerifiedRays,VerifiedQueries,VerificationMaxErrorCm);
    }
}

void URosLidarComponent::LogTiming(double Time,double Started,int32 Count)
{
    if(!bProfileTiming)return;
    ProfileRayWall+=FPlatformTime::Seconds()-Started;ProfileRayCount+=Count;
    if(ProfileStartSim==0)ProfileStartSim=Time;
    if(Time-ProfileStartSim<5.)return;
    UE_LOG(LogRMSim,Display,TEXT("RMSIM_LIDAR_TIMING rays=%lld wall_total_ms=%.3f wall_us_per_ray=%.3f sim_elapsed=%.3f query_mode=%s setup_ms=%.3f query_ms=%.3f optical_ms=%.3f pack_ms=%.3f"),
        ProfileRayCount,ProfileRayWall*1000.,ProfileRayWall*1.e6/FMath::Max(1.,double(ProfileRayCount)),Time-ProfileStartSim,
        bSerialQuery?TEXT("serial"):TEXT("parallel_staged"),ProfileSetupWall*1000.,ProfileQueryWall*1000.,ProfileOpticalWall*1000.,ProfilePackWall*1000.);
    ProfileRayCount=0;ProfileRayWall=ProfileSetupWall=ProfileQueryWall=ProfileOpticalWall=ProfilePackWall=0;ProfileStartSim=Time;
}

TSharedRef<FJsonObject> URosLidarComponent::TakeState(TArray<uint8>& Bytes,int32 Offset)
{
    auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("id"),Id);J->SetStringField(TEXT("profile_md5"),ProfileHash);
    J->SetBoolField(TEXT("calibrated"),false);J->SetStringField(TEXT("model"),TEXT("ideal_single_return_raycast; spherical_fibonacci_not_device_pattern; constant_reflectivity; physics_frame_time_quantization"));
    if(bMid360)
    {
        J->SetStringField(TEXT("profile_schema"),TEXT("rm_lidar_runtime_v3"));
        J->SetStringField(TEXT("model"),TEXT("MID360_public_spec_first_return; nominal5us_slots; native_pose_history; uncalibrated_measurement_model; current_UE_scene_snapshot"));
        auto P=MakeShared<FJsonObject>();P->SetStringField(TEXT("schema"),TEXT("rm_mid360_physical_v1"));
        P->SetNumberField(TEXT("emission_interval_ns"),RMSimSensors::Mid360Model::PointIntervalNs);
        P->SetNumberField(TEXT("laser_channels"),RMSimSensors::Mid360Model::ChannelCount);
        P->SetBoolField(TEXT("retains_no_returns"),true);P->SetNumberField(TEXT("pending_scan_capacity"),PendingScanLimit);
        P->SetStringField(TEXT("scan_pattern"),RecordedDirections.IsEmpty()?TEXT("spherical_fibonacci_approx"):TEXT("recorded_direction_replay"));
        P->SetStringField(TEXT("direction_sequence_md5"),DirectionSequenceHash);P->SetNumberField(TEXT("direction_sequence_points"),RecordedDirections.Num());
        P->SetStringField(TEXT("replay_time_model"),TEXT("cyclic_nominal5us_four_channel_sequence"));
        P->SetStringField(TEXT("pose_time_model"),TEXT("native_substep_translation_lerp_quaternion_slerp"));
        P->SetStringField(TEXT("scene_time_model"),TEXT("current_ue_world_snapshot"));
        P->SetStringField(TEXT("clock_model"),TEXT("simulation_epoch; no_device_UDP_PTP_transport_emulation"));
        P->SetStringField(TEXT("reflectivity_encoding"),TEXT("uncalibrated_diffuse_linear_150_times_NIR905_reflectance; retroreflectors_unmodeled"));
        P->SetStringField(TEXT("noise_time_model"),TEXT("counter_based_seed_global_emission_index_and_dimension"));
        P->SetStringField(TEXT("beam_model"),TEXT("pencil_ray; assembled_output_beam_footprint_uncalibrated"));
        P->SetStringField(TEXT("self_return_policy"),TEXT("opaque_self_geometry_first_return; no_self_return_mask"));
        P->SetBoolField(TEXT("measurement_calibrated"),false);
        P->SetNumberField(TEXT("angular_noise_std_rad"),AngularNoiseStd);P->SetNumberField(TEXT("range_noise_std_m"),NoiseStd);
        P->SetNumberField(TEXT("near_range_noise_std_m"),NearNoiseStd);P->SetNumberField(TEXT("noise_near_distance_m"),NoiseNearDistance);
        P->SetNumberField(TEXT("noise_reference_distance_m"),NoiseReferenceDistance);P->SetNumberField(TEXT("nir_default_reflectance"),DefaultNirReflectance);
        P->SetNumberField(TEXT("range_reference_low_reflectance"),RangeLowReflectance);P->SetNumberField(TEXT("range_reference_low_m"),RangeLowMetres);
        P->SetNumberField(TEXT("range_reference_high_reflectance"),RangeHighReflectance);P->SetNumberField(TEXT("range_reference_high_m"),RangeHighMetres);
        P->SetBoolField(TEXT("incidence_cosine"),bIncidenceCosine);
        auto Materials=MakeShared<FJsonObject>();for(const auto& M:NirMaterialReflectance)Materials->SetNumberField(M.Key,M.Value);
        P->SetObjectField(TEXT("nir_material_reflectance"),Materials);
        if(ModelProvenance.IsValid())P->SetObjectField(TEXT("provenance"),ModelProvenance);
        J->SetObjectField(TEXT("physical_model"),P);
        auto Rejected=MakeShared<FJsonObject>();Rejected->SetNumberField(TEXT("no_hit"),NoHitRays);
        Rejected->SetNumberField(TEXT("below_min_range"),TooNearRays);Rejected->SetNumberField(TEXT("above_max_range"),TooFarRays);
        Rejected->SetNumberField(TEXT("insufficient_signal"),WeakReturnRays);Rejected->SetNumberField(TEXT("missing_pose_history"),MissingPoseRays);
        Rejected->SetNumberField(TEXT("self_occluded"),0);Rejected->SetNumberField(TEXT("optical_pass_limit"),OpticalLimitRays);
        Rejected->SetNumberField(TEXT("noise_outside_range"),NoiseRejectedRays);J->SetObjectField(TEXT("rejection_counts"),Rejected);
        J->SetNumberField(TEXT("emitted_slots"),RayIndex);J->SetNumberField(TEXT("delivery_suppressed_slots"),DeliverySuppressedSlots);
        J->SetNumberField(TEXT("disconnected_slots"),DisconnectedRays);J->SetNumberField(TEXT("skipped_scan_windows"),SkippedScanWindows);
        J->SetNumberField(TEXT("incomplete_scan_drops"),IncompleteScanDrops);
        J->SetNumberField(TEXT("default_NIR_material_intersections"),DefaultMaterialRays);
        J->SetStringField(TEXT("query_execution"),bSerialQuery?TEXT("game_thread_serial"):TEXT("UE_task_pool_contiguous_blocks32_game_thread_optical_stage"));
    }
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
