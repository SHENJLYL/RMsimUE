#include "../RosLidarComponent.h"
#include "../VehicleRig.h"
#include "Components/PrimitiveComponent.h"
#include "GpuLidarScene.h"
#include "HAL/PlatformTime.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

bool URosLidarComponent::SetGpuScene(FGpuLidarScene& Scene)
{
    if(!Rig||!bMid360||!bArticulated||!(GpuVehicleId=Scene.VehicleId(Rig)))return false;
    for(FName Material:Scene.GetMaterials())
    {
        const FString Name=Material.ToString();
        const double* Found=NirMaterialReflectance.Find(Name);
        const uint32 MaterialId=GpuOptics.Add({Found?*Found:DefaultNirReflectance,Found!=nullptr});
        if(Name==TEXT("aim_sentry_radar_glass"))DarkGlass=MaterialId;
        if(Name==TEXT("aim_sentry_mid360_blue_glass"))BlueGlass=MaterialId;
    }
    GpuScene=&Scene;
    InitializeGpuPulseCache();
    FParse::Value(FCommandLine::Get(),TEXT("GpuLidarReferenceAfter="),GpuReferenceDelay);
    UE_LOG(LogTemp,Display,TEXT("RMSIM_GPU_LIDAR_READY vehicle=%u query_group_stride=%d rays_s=%.0f verify_first_rays=%lld"),
        GpuVehicleId,QueryGroupStride,RMSimSensors::Mid360Model::SampledSlotsPerScan(FrameHz,QueryGroupStride)*FrameHz,
        bVerifyQueries?MaxVerificationRays:0);
    return true;
}

void URosLidarComponent::ApplyGpuResult(FRay& Ray,const FGpuLidarHit& Hit,TConstArrayView<FTransform> Poses,bool bCount)
{
    if(!Ray.bPoseValid)return;
    if(bCount)++Traced;
    const uint32 Dark=Hit.Windows&255,Blue=Hit.Windows>>8;
    Ray.Windows=Dark+Blue;
    if(bCount)
    {
        WindowPasses+=Ray.Windows;
        if(!GpuOptics[DarkGlass].bConfigured)DefaultMaterialRays+=Dark;
        if(!GpuOptics[BlueGlass].bConfigured)DefaultMaterialRays+=Blue;
    }
    Ray.bFound=(Hit.Flags&1)!=0;
    Ray.bSelfHit=(Hit.Flags&2)!=0;
    Ray.bOpticalLimit=(Hit.Flags&4)!=0;
    Ray.bDynamicHit=(Hit.Flags&8)!=0;
    if(bCount&&Ray.bOpticalLimit)++OpticalLimitRays;
    if(!Ray.bFound)return;
    const auto& Optical=GpuOptics.IsValidIndex(Hit.Material)?GpuOptics[Hit.Material]:GpuOptics[0];
    Ray.NirReflectance=Optical.Reflectance;
    Ray.bConfiguredMaterial=Optical.bConfigured;
    if(bCount&&!Optical.bConfigured)++DefaultMaterialRays;
    Ray.Hit.ImpactPoint=GpuScene->RefineImpactPoint(Hit,Ray.Start,Ray.Direction,Poses);
    Ray.Hit.ImpactNormal=FVector(Hit.Normal);
}

void URosLidarComponent::CompareGpuResult(const FRay& Reference,const FRay& Ray,int64 Count)
{
    ++GpuCompared;
    if(Reference.bFound!=Ray.bFound)++GpuHitDifferences;
    if(Reference.Windows!=Ray.Windows||Reference.bOpticalLimit!=Ray.bOpticalLimit)++GpuOpticsDifferences;
    if(Reference.Windows||Ray.Windows)++GpuWindowCompared;
    if(Reference.bFound&&Ray.bFound)
    {
        ++GpuHitPairs;
        if(Reference.bSelfHit)++GpuSelfCompared;
        else if(Reference.bDynamicHit)++GpuOtherCompared;
        else ++GpuStaticCompared;
        if(Reference.bSelfHit!=Ray.bSelfHit||Reference.bConfiguredMaterial!=Ray.bConfiguredMaterial||
            Reference.NirReflectance!=Ray.NirReflectance)++GpuOpticsDifferences;
        const double Distance=(Reference.Hit.ImpactPoint-Ray.Hit.ImpactPoint).Size();
        const double Dot=FVector::DotProduct(Reference.Hit.ImpactNormal.GetSafeNormal(),Ray.Hit.ImpactNormal.GetSafeNormal());
        const double Angle=FMath::RadiansToDegrees(FMath::Acos(FMath::Clamp(Dot,-1.,1.)));
        if(Distance>GpuMaxDistance||Angle>GpuMaxNormalAngle)
        {
            const auto* Component=Reference.Hit.GetComponent();
            const FVector Normal=Reference.Hit.ImpactNormal;
            UE_LOG(LogTemp,Display,
                TEXT("RMSIM_GPU_LIDAR_COMPARE_SAMPLE vehicle=%u pulse=%lld position_cm=%.9f normal_deg=%.7f self=%d dynamic=%d component=%s scale=%s normal=%s"),
                GpuVehicleId,Ray.Index,Distance,Angle,Reference.bSelfHit,Reference.bDynamicHit,*GetNameSafe(Component),
                Component?*Component->GetComponentScale().ToString():TEXT(""),*Normal.ToString());
        }
        GpuMaxDistance=FMath::Max(GpuMaxDistance,Distance);
        GpuMaxNormalAngle=FMath::Max(GpuMaxNormalAngle,Angle);
    }
    if(GpuCompared>=Count&&!bGpuVerificationLogged)
    {
        bGpuVerificationLogged=true;
        const bool Pass = GpuHitDifferences <= FMath::FloorToInt(Count*.0005) &&
            GpuOpticsDifferences == 0 && GpuMaxDistance < 1.e-4 && GpuMaxNormalAngle < .05;
        UE_LOG(LogTemp,Display,
            TEXT("RMSIM_GPU_LIDAR_VERIFY result=%s vehicle=%u rays=%lld hit_pairs=%lld hit_mismatch=%lld optics_mismatch=%lld static=%lld other=%lld self=%lld glass=%lld max_position_cm=%.9f max_normal_deg=%.7f"),
            Pass?TEXT("passed"):TEXT("failed"),GpuVehicleId,GpuCompared,GpuHitPairs,GpuHitDifferences,GpuOpticsDifferences,
            GpuStaticCompared,GpuOtherCompared,GpuSelfCompared,GpuWindowCompared,GpuMaxDistance,GpuMaxNormalAngle);
    }
}

void URosLidarComponent::QueueGpuRays(TArray<FRay>&& Rays,int64 NowNs,double Time,double Started)
{
#if WITH_DEV_AUTOMATION_TESTS
    if(!bGpuCoverageQueued&&NeedsGpuReference()&&GpuScene->HasReferenceSnapshot())QueueGpuVehicleCoverage();
#endif
    TArray<FGpuLidarRay> Batch;
    Batch.Reserve(Rays.Num());
    TArray<FRay> Reference;
    for(int32 I=0;I<Rays.Num();++I)
    {
        const FRay& Ray=Rays[I];
        if(Ray.bPoseValid)
        {
            Batch.Add({FVector3f(Ray.Start),0,FVector3f(Ray.Direction),GpuVehicleId,float(MaxRange*100.)});
            if(NeedsGpuReference()&&GpuScene->HasReferenceSnapshot())
            {
                ++GpuReferenceSubmitted;
                Reference.Add(Ray);
                // A reference is correlated by the original physical pulse number.
            }
        }
        else
        {
            // Keep missing-pose slots present while avoiding meaningless GPU intersections.
            Batch.Add({FVector3f::ZeroVector,0,FVector3f(1.,0.,0.),GpuVehicleId,0.f});
        }
    }
    if(!Reference.IsEmpty())TraceRays(Reference,true);
    const uint64 Delivery=DeliveryGeneration;
    const uint64 Motion=MotionGeneration;
    const TWeakObjectPtr<URosLidarComponent> Weak(this);
    const double SetupSeconds=bProfileTiming?FPlatformTime::Seconds()-Started:0.;
    GpuScene->AddRays(MoveTemp(Batch),[Weak,Delivery,Motion,NowNs,Time,SetupSeconds,
        Rays=MoveTemp(Rays),Reference=MoveTemp(Reference)](TConstArrayView<FGpuLidarHit> Hits,TConstArrayView<FTransform> Poses) mutable
    {
        auto* Self=Weak.Get();
        if(!Self||Self->DeliveryGeneration!=Delivery||!IsValid(Self->Rig)||Self->Rig->GetMotionGeneration()!=Motion)return;
        if(Hits.Num()!=Rays.Num())
        {
            ++Self->DroppedScans;
            Self->DiscardPending();
            return;
        }
        const double ConsumeStarted=Self->bProfileTiming?FPlatformTime::Seconds():0.;
        Self->ProfileSetupWall+=SetupSeconds;
        int32 Ref=0;
        for(int32 I=0;I<Hits.Num();++I)
        {
            Self->ApplyGpuResult(Rays[I],Hits[I],Poses);
            if(Reference.IsValidIndex(Ref)&&Rays[I].Index==Reference[Ref].Index)
                Self->CompareGpuResult(Reference[Ref++],Rays[I],MaxVerificationRays);
        }
        Self->ConsumeMid360Rays(Rays,NowNs);
        Self->LogTiming(Time,ConsumeStarted-SetupSeconds,Rays.Num());
    });
}

#if WITH_DEV_AUTOMATION_TESTS
void URosLidarComponent::QueueGpuVehicleCoverage()
{
    bGpuCoverageQueued=true;
    auto Batch=GpuScene->MakeVehicleCoverageRays(GpuVehicleId);
    TArray<FRay> Reference;
    Reference.Reserve(Batch.Num());
    for(const auto& Input:Batch)
    {
        FRay& Ray=Reference.AddDefaulted_GetRef();
        Ray.Start=Ray.RayStart=FVector(Input.Origin);
        Ray.Direction=FVector(Input.Direction);
        Ray.End=Ray.Start+Ray.Direction*Input.RangeCm;
    }
    TraceRays(Reference,true);
    const TWeakObjectPtr<URosLidarComponent> Weak(this);
    const uint64 Delivery=DeliveryGeneration;
    GpuScene->AddRays(MoveTemp(Batch),[Weak,Delivery,Reference=MoveTemp(Reference)]
        (TConstArrayView<FGpuLidarHit> Hits,TConstArrayView<FTransform> Poses)
    {
        auto* Self=Weak.Get();
        if(!Self||Self->DeliveryGeneration!=Delivery||Hits.Num()!=Reference.Num())return;
        int32 HitMismatch=0,OpticsMismatch=0,SelfHits=0,OtherHits=0;
        double MaxDistance=0.,MaxAngle=0.;
        for(int32 I=0;I<Hits.Num();++I)
        {
            const FRay& Baseline=Reference[I];
            FRay Result;
            Result.Start=Baseline.Start;
            Result.Direction=Baseline.Direction;
            Self->ApplyGpuResult(Result,Hits[I],Poses,false);
            if(Result.bFound!=Baseline.bFound)++HitMismatch;
            if(Result.Windows!=Baseline.Windows||Result.bOpticalLimit!=Baseline.bOpticalLimit)++OpticsMismatch;
            if(!Baseline.bFound||!Result.bFound)continue;
            if(Baseline.bSelfHit)++SelfHits;
            else if(Baseline.bDynamicHit)++OtherHits;
            if(Result.bSelfHit!=Baseline.bSelfHit||Result.bConfiguredMaterial!=Baseline.bConfiguredMaterial||
                Result.NirReflectance!=Baseline.NirReflectance)++OpticsMismatch;
            if(!Baseline.bDynamicHit)continue;
            MaxDistance=FMath::Max(MaxDistance,FVector::Distance(Result.Hit.ImpactPoint,Baseline.Hit.ImpactPoint));
            const double Dot=FVector::DotProduct(Result.Hit.ImpactNormal.GetSafeNormal(),Baseline.Hit.ImpactNormal.GetSafeNormal());
            MaxAngle=FMath::Max(MaxAngle,FMath::RadiansToDegrees(FMath::Acos(FMath::Clamp(Dot,-1.,1.))));
        }
        const bool Pass=Hits.Num()>0&&SelfHits>0&&OtherHits>0&&HitMismatch==0&&OpticsMismatch==0&&MaxDistance<1.e-4&&MaxAngle<.05;
        UE_LOG(LogTemp,Display,
            TEXT("RMSIM_GPU_LIDAR_VEHICLE_COVERAGE result=%s vehicle=%d pairs=%d self=%d other=%d hit_mismatch=%d optics_mismatch=%d max_position_cm=%.9f max_normal_deg=%.7f"),
            Pass?TEXT("passed"):TEXT("failed"),Self->GpuVehicleId,Hits.Num(),SelfHits,OtherHits,HitMismatch,OpticsMismatch,MaxDistance,MaxAngle);
    });
}
#endif
