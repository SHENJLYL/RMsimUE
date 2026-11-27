#include "../RosImuComponent.h"
#include "../VehicleRig.h"
#include "Mid360Model.h"
#include "HAL/PlatformTime.h"

void URosImuComponent::AdvanceNative(double Time,bool Collect,double EpochSolverTime)
{
    if(!Rig||!FMath::IsFinite(Time)||Time<0||Time>9.e6||!FMath::IsFinite(EpochSolverTime))return;
    check(IsInGameThread());
    LastAdvanceTime=Time;
    constexpr double HalfSupport=NativeIntegrationSeconds * .5;
    if(MotionGeneration!=Rig->GetMotionGeneration())
    {
        MotionGeneration=Rig->GetMotionGeneration();
        for(auto& C:Channels)
        {
            C.Pending.Reset();C.HasHistory=false;C.HasRelativeHistory=false;++C.HistoryResets;
            // Keep the epoch grid, with both velocity supports inside this motion generation.
            C.NextSampleIndex=RMSimSensors::EpochSampleGrid::FirstAtOrAfter(Time+HalfSupport,C.Rate);
            C.DeliveryNotBefore=Time;
        }
    }
    const FVector Gravity=Rig->GetDynamicsGravityVectorCmS2();GravityZ=Gravity.Z;
    const double SupportedThrough=Time-HalfSupport;
    for(auto& C:Channels)
    {
        const int64 End=RMSimSensors::EpochSampleGrid::LastSupported(SupportedThrough,C.Rate)+1;
        const int64 Due=FMath::Max<int64>(0,End-C.NextSampleIndex);
        const int64 Missed=FMath::Max<int64>(0,Due-PendingSamplesPerChannel());
        C.MissedPeriods+=Missed;C.NextSampleIndex+=Missed;
        for(int64 I=Missed;I<Due;++I)
        {
            const int64 SampleIndex=C.NextSampleIndex++;
            const double SampleTime=RMSimSensors::EpochSampleGrid::Time(SampleIndex,C.Rate);
            if(SampleTime<HalfSupport){++C.WarmupSamples;continue;}
            const double Started=bProfileTiming?FPlatformTime::Seconds():0.;
            FTransform MinusPose,Pose,PlusPose;FVector MinusVelocity,Velocity,PlusVelocity,MinusOmega,Omega,PlusOmega;
            const double SolverStamp=EpochSolverTime+SampleTime;
            if(!Rig->SampleSensorMotionAtTime(C.ParentFrame,SolverStamp-HalfSupport,C.Mount,MinusPose,MinusVelocity,MinusOmega)||
               !Rig->SampleSensorMotionAtTime(C.ParentFrame,SolverStamp,C.Mount,Pose,Velocity,Omega)||
               !Rig->SampleSensorMotionAtTime(C.ParentFrame,SolverStamp+HalfSupport,C.Mount,PlusPose,PlusVelocity,PlusOmega))
            {++C.HistoryMisses;++C.Invalid;continue;}
            // Integrate over a full 200 Hz interval so substep servo oscillations
            // cannot alias into a sustained acceleration or gyro bias.
            const FVector SpecificWorld=(PlusVelocity-MinusVelocity)/NativeIntegrationSeconds-Gravity;
            const FVector Specific=Pose.GetRotation().UnrotateVector(SpecificWorld)/100.;
            FQuat RotationDelta=PlusPose.GetRotation()*MinusPose.GetRotation().Inverse();
            RotationDelta.Normalize();
            if(RotationDelta.W<0)RotationDelta=FQuat(-RotationDelta.X,-RotationDelta.Y,-RotationDelta.Z,-RotationDelta.W);
            const FVector LocalOmega=Pose.GetRotation().UnrotateVector(RotationDelta.ToRotationVector())/NativeIntegrationSeconds;
            const FVector Accel(Specific.X,-Specific.Y,Specific.Z);
            const FVector Gyro(-LocalOmega.X,LocalOmega.Y,-LocalOmega.Z);
            if(bProfileTiming){ProfileSampleWall+=FPlatformTime::Seconds()-Started;++ProfileSampleCount;}
            PublishSample(C,SampleTime,NativeIntegrationSeconds,Accel,Gyro,Collect,SampleIndex);
        }
    }
    if(bProfileTiming&&Time-ProfileStartSim>=5.)
    {
        UE_LOG(LogRMSim,Display,TEXT("RMSIM_IMU_TIMING samples=%lld sample_ms=%.3f pack_ms=%.3f sim_elapsed=%.3f support_half_s=%.6f"),
            ProfileSampleCount,ProfileSampleWall*1000.,ProfilePackWall*1000.,Time-ProfileStartSim,HalfSupport);
        ProfileSampleCount=0;ProfileSampleWall=ProfilePackWall=0;ProfileStartSim=Time;
    }
}
