#include "../RosLidarComponent.h"
#include "../VehicleRig.h"
#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "Containers/UnrealString.h"
#include "Misc/DefaultValueHelper.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformTime.h"
#include "Tasks/Task.h"

namespace
{
bool MidNumber(const TSharedPtr<FJsonObject>& J,const TCHAR* Key,double& Value,double Low,double High)
{
    const auto* V=J->Values.Find(Key);
    return V&&V->IsValid()&&(*V)->Type==EJson::Number&&(*V)->TryGetNumber(Value)&&FMath::IsFinite(Value)&&Value>=Low&&Value<=High;
}
void MidU32(TArray<uint8>& Bytes,uint32 Value)
{for(int32 I=0;I<4;++I)Bytes.Add(uint8(Value>>(8*I)));}
void MidFloat(TArray<uint8>& Bytes,float Value)
{uint32 Bits;FMemory::Memcpy(&Bits,&Value,4);MidU32(Bytes,Bits);}
}

bool URosLidarComponent::InitializeMid360(const TSharedPtr<FJsonObject>& Json,const FString& Profile,FString& Error)
{
    const TSharedPtr<FJsonObject>* Model;
    if(!Json->TryGetObjectField(TEXT("mid360"),Model)||!(*Model).IsValid())
    {Error=TEXT("MID360 v3 requires the mid360 measurement model");return false;}
    const auto& J=*Model;double QueueLimit=0;FString SequenceFile;
    const TSharedPtr<FJsonObject>* Materials;
    if(J->Values.Num()!=(J->HasField(TEXT("provenance"))?14:13)||RaysPerSecond!=200000||MinRange<.1||MaxRange>70||
       !RMSimSensors::Mid360Model::SupportedFrameRate(FrameHz)||
       !FMath::IsNearlyEqual(MinElevation,FMath::DegreesToRadians(-7.),1.e-10)||
       !FMath::IsNearlyEqual(MaxElevation,FMath::DegreesToRadians(52.),1.e-10)||
       !MidNumber(J,TEXT("angular_noise_std_rad"),AngularNoiseStd,0,.1)||
       !MidNumber(J,TEXT("near_range_noise_std_m"),NearNoiseStd,0,1)||
       !MidNumber(J,TEXT("noise_near_distance_m"),NoiseNearDistance,.01,10)||
       !MidNumber(J,TEXT("noise_reference_distance_m"),NoiseReferenceDistance,NoiseNearDistance+.01,100)||
       !MidNumber(J,TEXT("nir_default_reflectance"),DefaultNirReflectance,0,1)||
       !MidNumber(J,TEXT("range_reference_low_reflectance"),RangeLowReflectance,.001,1)||
       !MidNumber(J,TEXT("range_reference_low_m"),RangeLowMetres,.1,200)||
       !MidNumber(J,TEXT("range_reference_high_reflectance"),RangeHighReflectance,RangeLowReflectance+.001,1)||
       !MidNumber(J,TEXT("range_reference_high_m"),RangeHighMetres,RangeLowMetres+.001,200)||
       !J->TryGetBoolField(TEXT("incidence_cosine"),bIncidenceCosine)||
       !MidNumber(J,TEXT("pending_scan_limit"),QueueLimit,2,8)||FMath::FloorToDouble(QueueLimit)!=QueueLimit||
       !J->TryGetObjectField(TEXT("nir_material_reflectance"),Materials)||(*Materials)->Values.Num()>128||
       !J->TryGetStringField(TEXT("direction_sequence_file"),SequenceFile))
    {Error=TEXT("invalid MID360 nominal FOV/rate, measurement fields or bounded queue");return false;}
    if(!FMath::IsNearlyEqual(RangeLowReflectance,.1,1.e-12)||!FMath::IsNearlyEqual(RangeLowMetres,40.,1.e-12)||
       !FMath::IsNearlyEqual(RangeHighReflectance,.8,1.e-12)||!FMath::IsNearlyEqual(RangeHighMetres,70.,1.e-12))
    {Error=TEXT("MID360 v3 range anchors must retain 40m at10 percent and70m at80 percent");return false;}
    for(const auto& Item:(*Materials)->Values)
    {
        double Reflectance;
        if(Item.Key.IsEmpty()||Item.Key.Len()>128||!Item.Value.IsValid()||Item.Value->Type!=EJson::Number||
           !Item.Value->TryGetNumber(Reflectance)||!FMath::IsFinite(Reflectance)||Reflectance<0||Reflectance>1)
        {Error=TEXT("NIR905 material reflectance requires exact material names and values0..1");return false;}
        NirMaterialReflectance.Add(FString(Item.Key.Len(),*Item.Key),Reflectance);
    }
    if(J->HasField(TEXT("provenance")))
    {
        const TSharedPtr<FJsonObject>* Provenance;bool Calibrated=true;
        if(!J->TryGetObjectField(TEXT("provenance"),Provenance)||(*Provenance)->Values.Num()>16||
           !(*Provenance)->TryGetBoolField(TEXT("measurement_calibrated"),Calibrated)||Calibrated)
        {Error=TEXT("MID360 model provenance must declare measurement_calibrated=false");return false;}
        for(const auto& Item:(*Provenance)->Values)
        {
            if(!Item.Value.IsValid()||Item.Key.Len()>128||
               (Item.Value->Type!=EJson::String&&Item.Value->Type!=EJson::Boolean&&Item.Value->Type!=EJson::Number)||
               (Item.Value->Type==EJson::String&&Item.Value->AsString().Len()>512))
            {Error=TEXT("MID360 provenance must contain bounded scalar metadata");return false;}
        }
        ModelProvenance=*Provenance;
    }
    PendingScanLimit=int32(QueueLimit);
    if(!SequenceFile.IsEmpty())
    {
        const FString Path=FPaths::ConvertRelativePathToFull(FPaths::GetPath(Profile),SequenceFile);
        const int64 Size=IFileManager::Get().FileSize(*Path);
        TArray<FString> Lines;
        if(Size<=0||Size>128*1024*1024||!FFileHelper::LoadFileToStringArray(Lines,*Path)||Lines.Num()<5||Lines.Num()>2000001||
           Lines[0].TrimStartAndEnd()!=TEXT("offset_time_ns,azimuth_rad,elevation_rad,line"))
        {Error=TEXT("MID360 direction CSV requires offset_time_ns,azimuth_rad,elevation_rad,line header and4..2000000 points");return false;}
        for(int32 I=1;I<Lines.Num();++I)
        {
            TArray<FString> Fields;Lines[I].ParseIntoArray(Fields,TEXT(","),false);
            int64 Offset=0,Line=0;double Azimuth=0,Elevation=0;
            if(Fields.Num()!=4||!FDefaultValueHelper::ParseInt64(Fields[0],Offset)||!FDefaultValueHelper::ParseDouble(Fields[1],Azimuth)||
               !FDefaultValueHelper::ParseDouble(Fields[2],Elevation)||!FDefaultValueHelper::ParseInt64(Fields[3],Line)||
               Offset!=int64(I-1)*RMSimSensors::Mid360Model::PointIntervalNs||Line!=(I-1)%4||
               !FMath::IsFinite(Azimuth)||!FMath::IsFinite(Elevation)||FMath::Abs(Azimuth)>2*PI||
               Elevation<MinElevation-1.e-9||Elevation>MaxElevation+1.e-9)
            {Error=FString::Printf(TEXT("invalid MID360 direction CSV row%d; nominal5us time andfour channel order required"),I+1);return false;}
            RecordedDirections.Add(FVector2D(Azimuth,Elevation));
        }
        if(RecordedDirections.Num()%4!=0){Error=TEXT("MID360 direction replay must contain complete four-channel groups");return false;}
        DirectionSequenceHash=LexToString(FMD5Hash::HashFile(*Path));
    }
    return true;
}

double URosLidarComponent::Gaussian(int64 Index,uint32 Dimension) const
{return RMSimSensors::Mid360Model::Gaussian(uint64(Seed),uint64(Index),Dimension);}

void URosLidarComponent::SelectScan(int64 Ordinal)
{
    if(CurrentScanOrdinal==Ordinal)return;
    const int64 PeriodNs=RMSimSensors::Mid360Model::ScanPeriodNs(FrameHz);
    if(CurrentScanOrdinal>=0)
    {
        FrameEnd=double((CurrentScanOrdinal+1)*PeriodNs)*1.e-9;
        FinishScan();
        SkippedScanWindows+=FMath::Max<int64>(0,Ordinal-CurrentScanOrdinal-1);
    }
    CurrentScanOrdinal=Ordinal;
    FrameStart=double(Ordinal*PeriodNs)*1.e-9;FrameEnd=FrameStart;
}

void URosLidarComponent::AdvanceMid360(double Time,bool Connected,double EpochSolverTime)
{
    if(!Rig||!FMath::IsFinite(Time)||Time<0||Time>9.e6||!FMath::IsFinite(EpochSolverTime))return;
    check(IsInGameThread());
    if(MotionGeneration!=Rig->GetMotionGeneration())
    {
        MotionGeneration=Rig->GetMotionGeneration();DiscardPending();++HistoryResets;
        const int64 Period=RMSimSensors::Mid360Model::ScanPeriodNs(FrameHz),Now=FMath::RoundToInt64(Time*1.e9);
        DeliveryStartOrdinal=Now/Period+(Now%Period?1:0);
    }
    if(PreviousTime>=0)MaxStep=FMath::Max(MaxStep,FMath::Max(0.,Time-PreviousTime));
    PreviousTime=Time;
    const int64 NowNs=FMath::RoundToInt64(Time*1.e9);
    // Dedicated mode replays each actual 5 ms query endpoint separately.
    const auto Batch=PulseClock.Advance(NowNs,Connected?4096:0);
    RayIndex=PulseClock.NextIndex();
    if(!Connected)
    {
        ++DeliveryGeneration;
        DisconnectedRays+=Batch.Missed;Pending.Reset();Building.Reset();CurrentScanOrdinal=-1;FrameStart=-1;return;
    }
    MissedRays+=Batch.Missed;
    const int64 PeriodNs=RMSimSensors::Mid360Model::ScanPeriodNs(FrameHz);
    const int64 PointsPerScan=PeriodNs/RMSimSensors::Mid360Model::PointIntervalNs;
    if(Batch.Missed>0)
    {
        ++DeliveryGeneration;
        // A bounded overload discards incomplete delivery windows, preserving full-frame slots.
        const int64 FirstWhole=Batch.First/PointsPerScan+(Batch.First%PointsPerScan?1:0);
        if(CurrentScanOrdinal>=0)
        {SkippedScanWindows+=FMath::Max<int64>(1,FirstWhole-CurrentScanOrdinal);++DroppedScans;}
        // Buffered slots already belong to traced/missed; delivery_suppressed counts only untraced gate slots.
        Building.Reset();CurrentScanOrdinal=-1;FrameStart=-1;
        DeliveryStartOrdinal=FMath::Max(DeliveryStartOrdinal,FirstWhole);
    }
    const int64 First=FMath::Min<int64>(Batch.End,FMath::Max<int64>(Batch.First,DeliveryStartOrdinal*PointsPerScan));
    DeliverySuppressedSlots+=First-Batch.First;
    const double Started=bProfileTiming?FPlatformTime::Seconds():0.;
    TArray<FRay> Rays;
    Rays.Reserve(int32(Batch.End-First));
    for(int64 Index=First;Index<Batch.End;++Index)
    {
        if(!RMSimSensors::Mid360Model::SamplesSlot(Index%PointsPerScan,QueryGroupStride))
        {
            ++SamplingOmittedSlots;
            continue;
        }
        FRay& Ray=Rays.AddDefaulted_GetRef();
        Ray.Index=Index;
        Ray.StampNs=Index*RMSimSensors::Mid360Model::PointIntervalNs;
        FTransform ParentPose;
        if(!Rig->SampleSensorParentWorldAtTime(ParentFrame,EpochSolverTime+double(Ray.StampNs)*1.e-9,ParentPose))
        {Ray.bPoseValid=false;++MissingPoseRays;++MissedRays;continue;}
        const FTransform Pose=Mount*ParentPose;
        GenerateMid360Pulse(Ray);
        Ray.Direction=Pose.TransformVectorNoScale(Ray.Local);Ray.Start=Pose.GetLocation();
        Ray.RayStart=Ray.Start;Ray.End=Ray.Start+Ray.Direction*(MaxRange*100);
    }
    if(bProfileTiming&&!GpuScene)ProfileSetupWall+=FPlatformTime::Seconds()-Started;
    if(GpuScene)
    {
        QueueGpuRays(MoveTemp(Rays),NowNs,Time,Started);
        return;
    }
    TraceRays(Rays);
    ConsumeMid360Rays(Rays,NowNs);
    LogTiming(Time,Started,Rays.Num());
}

namespace
{
constexpr int64 PulsePageSize=8192;

FVector PulseDirection(int64 Index,TConstArrayView<FVector2D> Recorded,double MinZ,double MaxZ)
{
    double Azimuth,Elevation;
    if(Recorded.IsEmpty())
    {
        Azimuth=2*PI*FMath::Frac((Index+.5)*.6180339887498949);
        const double Z=FMath::Lerp(MinZ,MaxZ,FMath::Frac((Index+.5)*.7548776662466927));
        Elevation=FMath::Asin(Z);
    }
    else
    {
        const FVector2D& D=Recorded[int32(Index%Recorded.Num())];
        Azimuth=D.X;Elevation=D.Y;
    }
    const double XY=FMath::Cos(Elevation);
    return FVector(XY*FMath::Cos(Azimuth),-XY*FMath::Sin(Azimuth),FMath::Sin(Elevation));
}
}

struct URosLidarComponent::FMid360PulseConfig
{
    TArray<FVector2D> Recorded;
    double MinZ=0.,MaxZ=0.,AngularNoise=0.;
    uint64 Seed=0;
};

struct URosLidarComponent::FMid360PulsePage
{
    explicit FMid360PulsePage(int64 InFirst) : First(InFirst) {}
    int64 First;
    TArray<FPreparedPulse> Data;
    TAtomic<bool> Ready{false};
};

void URosLidarComponent::InitializeGpuPulseCache()
{
    auto Config=MakeShared<FMid360PulseConfig,ESPMode::ThreadSafe>();
    Config->Recorded=RecordedDirections;
    Config->MinZ=FMath::Sin(MinElevation);
    Config->MaxZ=FMath::Sin(MaxElevation);
    Config->AngularNoise=AngularNoiseStd;
    Config->Seed=uint64(Seed);
    GpuPulseConfig=MoveTemp(Config);
}

bool URosLidarComponent::PrepareGpuPulse(FRay& Ray)
{
    const int64 First=Ray.Index/PulsePageSize*PulsePageSize;
    if((!GpuPulseCurrent||GpuPulseCurrent->First!=First)&&GpuPulseNext&&
        GpuPulseNext->First==First&&GpuPulseNext->Ready.Load())
        GpuPulseCurrent=MoveTemp(GpuPulseNext);
    // Only one unpublished page per component. On an epoch jump, finish the
    // already scheduled immutable page before requesting the new range.
    const bool bPrepared=GpuPulseCurrent&&GpuPulseCurrent->First==First;
    const int64 Needed=bPrepared?First+PulsePageSize:First;
    if(!GpuPulseNext||(GpuPulseNext->First!=Needed&&GpuPulseNext->Ready.Load()))
    {
        const auto Page=MakeShared<FMid360PulsePage,ESPMode::ThreadSafe>(Needed);
        GpuPulseNext=Page;
        const auto Config=GpuPulseConfig;
        UE::Tasks::Launch(UE_SOURCE_LOCATION,[Page,Config]
        {
            Page->Data.SetNumUninitialized(PulsePageSize);
            for(int64 I=0;I<PulsePageSize;++I)
            {
                const int64 Index=Page->First+I;
                FPreparedPulse& P=Page->Data[I];
                P.Local=PulseDirection(Index,Config->Recorded,Config->MinZ,Config->MaxZ);
                P.RangeGaussian=RMSimSensors::Mid360Model::Gaussian(Config->Seed,uint64(Index),0);
                double Azimuth=FMath::Atan2(-P.Local.Y,P.Local.X),Elevation=FMath::Asin(P.Local.Z);
                if(Config->AngularNoise>0.)
                {
                    Azimuth+=Config->AngularNoise*RMSimSensors::Mid360Model::Gaussian(Config->Seed,uint64(Index),1);
                    Elevation=FMath::Clamp(Elevation+Config->AngularNoise*
                        RMSimSensors::Mid360Model::Gaussian(Config->Seed,uint64(Index),2),-PI/2,PI/2);
                }
                P.ElevCos=FMath::Cos(Elevation);P.ElevSin=FMath::Sin(Elevation);
                P.AzCos=FMath::Cos(Azimuth);P.AzSin=FMath::Sin(Azimuth);
            }
            Page->Ready.Store(true);
        });
    }
    if(!bPrepared){++GpuPreparedPulseMisses;return false;}
    ++GpuPreparedPulseCount;
    Ray.Pulse=GpuPulseCurrent->Data[Ray.Index-First];
    Ray.Local=Ray.Pulse.Local;
    Ray.bPreparedPulse=true;
    return true;
}

void URosLidarComponent::GenerateMid360Pulse(FRay& Ray)
{
    if(GpuScene&&PrepareGpuPulse(Ray))return;
    Ray.Local=PulseDirection(Ray.Index,RecordedDirections,FMath::Sin(MinElevation),FMath::Sin(MaxElevation));
}

void URosLidarComponent::ConsumeMid360Rays(TConstArrayView<FRay> Rays,int64 NowNs)
{
    const int64 PeriodNs=RMSimSensors::Mid360Model::ScanPeriodNs(FrameHz);
    const double PackStarted=bProfileTiming?FPlatformTime::Seconds():0.;
    // Queries share this UE scene snapshot. Emitter poses/time use the native1ms history;
    // dynamic obstacle/self-occluder meshes have the current world-frame pose (typically5ms).
    for(const FRay& Ray:Rays)
    {
        SelectScan(RMSimSensors::Mid360Model::ScanOrdinal(Ray.Index,FrameHz));
        FrameEnd=double(Ray.StampNs)*1.e-9;
        FVector Point=FVector::ZeroVector;uint8 PointReflectivity=0;
        do
        {
            if(!Ray.bPoseValid)break;
            if(!Ray.bFound){if(!Ray.bOpticalLimit)++NoHitRays;break;}
            const FHitResult& Hit=Ray.Hit;
            if(Ray.bSelfHit)++SelfBlocked; // Opaque self geometry also produces its physical first return.
            double Distance=(Hit.ImpactPoint-Ray.Start).Size()/100.;
            if(Distance<MinRange){++TooNearRays;break;}
            if(Distance>MaxRange){++TooFarRays;break;}
            const double Incidence=bIncidenceCosine?FMath::Max(0.,FVector::DotProduct(-Ray.Direction,Hit.ImpactNormal.GetSafeNormal())):1.;
            const double Detectable=RMSimSensors::Mid360Model::DetectableRange(Ray.NirReflectance*Incidence,
                RangeLowReflectance,RangeLowMetres,RangeHighReflectance,RangeHighMetres,MaxRange);
            if(Distance>Detectable){++WeakReturnRays;break;}
            const double Sigma=RMSimSensors::Mid360Model::RangeSigma(Distance,NearNoiseStd,NoiseStd,NoiseNearDistance,NoiseReferenceDistance);
            if(Sigma>0)Distance+=Sigma*(Ray.bPreparedPulse?Ray.Pulse.RangeGaussian:Gaussian(Ray.Index,0));
            if(Distance<MinRange||Distance>MaxRange){++NoiseRejectedRays;break;}
            if(Ray.bPreparedPulse)
            {
                const double XY=Ray.Pulse.ElevCos*Distance;
                Point=FVector(XY*Ray.Pulse.AzCos,XY*Ray.Pulse.AzSin,Distance*Ray.Pulse.ElevSin);
            }
            else
            {
                double Azimuth=FMath::Atan2(-Ray.Local.Y,Ray.Local.X),Elevation=FMath::Asin(Ray.Local.Z);
                if(AngularNoiseStd>0){Azimuth+=AngularNoiseStd*Gaussian(Ray.Index,1);Elevation=FMath::Clamp(Elevation+AngularNoiseStd*Gaussian(Ray.Index,2),-PI/2,PI/2);}
                const double XY=FMath::Cos(Elevation)*Distance;
                Point=FVector(XY*FMath::Cos(Azimuth),XY*FMath::Sin(Azimuth),Distance*FMath::Sin(Elevation));
            }
            // Device diffuse reflectivity uses0..150;151..255 is reserved for retroreflectors.
            PointReflectivity=uint8(FMath::Clamp(FMath::RoundToInt(150.*Ray.NirReflectance),0,150));++Hits;
        }while(false);
        const int64 StartNs=CurrentScanOrdinal*RMSimSensors::Mid360Model::ScanPeriodNs(FrameHz);
        MidU32(Building,uint32(Ray.StampNs-StartNs));
        MidFloat(Building,float(Point.X));MidFloat(Building,float(Point.Y));MidFloat(Building,float(Point.Z));
        // Selected slots with absent returns retain zero XYZ/reflectivity and their time/channel.
        Building.Add(PointReflectivity);Building.Add(0);Building.Add(uint8(Ray.Index%RMSimSensors::Mid360Model::ChannelCount));Building.Add(0);
    }
    if(CurrentScanOrdinal>=0&&NowNs>=(CurrentScanOrdinal+1)*PeriodNs)
    {
        FrameEnd=double((CurrentScanOrdinal+1)*PeriodNs)*1.e-9;FinishScan();CurrentScanOrdinal=-1;FrameStart=-1;
    }
    if(bProfileTiming)ProfilePackWall+=FPlatformTime::Seconds()-PackStarted;
}
