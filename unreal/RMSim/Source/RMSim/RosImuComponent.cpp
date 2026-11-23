#include "RosImuComponent.h"
#include "VehicleRig.h"
#include "RosCoordinates.h"
#include "Sensors/Mid360Model.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "HAL/PlatformTime.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "Misc/FileHelper.h"
#include "Misc/SecureHash.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
TArray<TSharedPtr<FJsonValue>> ImuVector(const FVector& V)
{return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),MakeShared<FJsonValueNumber>(V.Z)};}
bool ImuNumber(const TSharedPtr<FJsonObject>& J,const TCHAR* Key,double& N)
{const auto* V=J->Values.Find(Key);return V&&V->IsValid()&&(*V)->Type==EJson::Number&&(*V)->TryGetNumber(N)&&FMath::IsFinite(N);}
bool ImuVec(const TSharedPtr<FJsonObject>& J,const TCHAR* Key,FVector& V,double Limit,bool Positive=false)
{
    const TArray<TSharedPtr<FJsonValue>>* A;
    if(!J->TryGetArrayField(Key,A)||A->Num()!=3)return false;
    for(int I=0;I<3;++I)
    {
        double X;
        if(!(*A)[I].IsValid()||(*A)[I]->Type!=EJson::Number||!(*A)[I]->TryGetNumber(X)||!FMath::IsFinite(X)||FMath::Abs(X)>Limit||(Positive&&X<0))return false;
        V[I]=X;
    }
    return true;
}
}

bool URosImuComponent::Initialize(AVehicleRig* Vehicle,const FString& Profile,FString& Error)
{
    FString Text;TSharedPtr<FJsonObject> Root;
    if(!Vehicle||!Vehicle->IsContactPhysics()||!FFileHelper::LoadFileToString(Text,*Profile)||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Root)||!Root.IsValid())
    {Error=TEXT("cannot read physical IMU runtime profile");return false;}
    FString Schema;Root->TryGetStringField(TEXT("schema"),Schema);bPhysicalV3=Schema==TEXT("rm_imu_runtime_v3");bool Calibrated=true;const TArray<TSharedPtr<FJsonValue>>* Imus;
    if(Root->Values.Num()!=3||!Root->TryGetStringField(TEXT("schema"),Schema)||(Schema!=TEXT("rm_imu_runtime_v1")&&Schema!=TEXT("rm_imu_runtime_v2")&&!bPhysicalV3)||
        !Root->TryGetBoolField(TEXT("calibrated"),Calibrated)||Calibrated||!Root->TryGetArrayField(TEXT("imus"),Imus)||Imus->Num()<1||Imus->Num()>4)
    {Error=TEXT("IMU profile requires schema, calibrated=false and1..4 sensors");return false;}
    if(bPhysicalV3&&!Vehicle->IsMujocoPhysics()){Error=TEXT("IMU v3 requires native MuJoCo motion history");return false;}
    bProfileTiming=FParse::Param(FCommandLine::Get(),TEXT("ProfileSensorTiming"));
    TSet<FString> Names;
    for(const auto& Item:*Imus)
    {
        if(!Item.IsValid()||Item->Type!=EJson::Object){Error=TEXT("IMU sensor entry must be an object");return false;}
        const auto J=Item->AsObject();FChannel C;FString Parent;double Seed=0;
        const TSharedPtr<FJsonObject>* Origin;
        if(!J.IsValid()||J->Values.Num()!=(bPhysicalV3?10:9)||!J->TryGetStringField(TEXT("id"),C.Id)||C.Id.IsEmpty()||C.Id.Len()>64||C.Id==TEXT("base")||
            !FChar::IsAlpha(C.Id[0])||Names.Contains(C.Id)||!J->TryGetStringField(TEXT("parent_frame"),Parent)||(Parent!=TEXT("base_link")&&((Schema!=TEXT("rm_imu_runtime_v2")&&!bPhysicalV3)||Parent!=TEXT("yaw_link")))||
            !J->TryGetObjectField(TEXT("origin"),Origin)||(*Origin)->Values.Num()!=2||!ImuNumber(J,TEXT("rate_hz"),C.Rate)||C.Rate<1||C.Rate>1000||
            !ImuNumber(J,TEXT("noise_seed"),Seed)||Seed<0||Seed>2147483647.||FMath::FloorToDouble(Seed)!=Seed||
            !ImuVec(J,TEXT("accel_bias_mps2"),C.AccelBias,100)||!ImuVec(J,TEXT("gyro_bias_rad_s"),C.GyroBias,100)||
            !ImuVec(J,TEXT("accel_noise_std_mps2"),C.AccelStd,100,true)||!ImuVec(J,TEXT("gyro_noise_std_rad_s"),C.GyroStd,10,true))
        {Error=TEXT("invalid IMU identity, parent, rate, noise or bias");return false;}
        if(bPhysicalV3)
        {
            const TSharedPtr<FJsonObject>* M;
            if(!J->TryGetObjectField(TEXT("measurement"),M)||(*M)->Values.Num()!=4||
               !ImuNumber(*M,TEXT("accel_range_mps2"),C.AccelRange)||C.AccelRange<0||C.AccelRange>1000||
               !ImuNumber(*M,TEXT("gyro_range_rad_s"),C.GyroRange)||C.GyroRange<0||C.GyroRange>1000||
               !ImuNumber(*M,TEXT("accel_quantization_mps2"),C.AccelQuantum)||C.AccelQuantum<0||C.AccelQuantum>10||
               !ImuNumber(*M,TEXT("gyro_quantization_rad_s"),C.GyroQuantum)||C.GyroQuantum<0||C.GyroQuantum>1)
            {Error=TEXT("invalid IMU SI measurement range/quantization; zero disables each effect");return false;}
        }
        for(TCHAR Ch:C.Id)if(Ch>127||!(FChar::IsAlnum(Ch)||Ch=='_')){Error=TEXT("invalid IMU identifier");return false;}
        FVector Rpy;
        if(!ImuVec(*Origin,TEXT("xyz"),C.OriginMetres,5)||!ImuVec(*Origin,TEXT("rpy"),Rpy,2*PI))
        {Error=TEXT("invalid IMU mount in SI units");return false;}
        C.ParentFrame=Parent;FTransform ParentPose;
        if(!Vehicle->GetSensorParentWorld(Parent,ParentPose)){Error=TEXT("unknown IMU parent joint");return false;}
        C.Mount=FTransform(RosCoordinates::Rotation(Rpy),RosCoordinates::Position(C.OriginMetres));
        C.Seed=int32(Seed);C.Random.Initialize(C.Seed);Names.Add(C.Id);Channels.Add(C);
    }
    Rig=Vehicle;RobotId=Rig->GetVehicleId();MotionGeneration=Rig->GetMotionGeneration();
    ProfileHash=LexToString(FMD5Hash::HashFile(*Profile));GravityZ=Rig->GetDynamicsGravityCmS2();
    UE_LOG(LogRMSim,Display,TEXT("IMU_READY sensors=%d profile=%s orientation=unavailable SI=true calibrated=false"),Channels.Num(),*Profile);
    return true;
}

double URosImuComponent::Gaussian(FRandomStream& Random)
{return FMath::Sqrt(-2*FMath::Loge(FMath::Max(double(Random.FRand()),1.e-12)))*FMath::Cos(2*PI*Random.FRand());}

void URosImuComponent::Reset()
{
    MotionGeneration=Rig->GetMotionGeneration();LastAdvanceTime=0;ProfileStartSim=ProfileSampleWall=ProfilePackWall=0;ProfileSampleCount=0;
    for(auto& C:Channels){C.HasHistory=false;C.HasRelativeHistory=false;C.NextDue=0;C.NextSampleIndex=0;C.PreviousTime=0;C.Pending.Reset();C.Sequence=0;C.MissedPeriods=0;C.Dropped=0;C.HistoryResets=0;C.Invalid=0;C.AccelSaturatedAxes=C.GyroSaturatedAxes=C.HistoryMisses=C.WarmupSamples=C.DeliverySuppressed=0;C.DeliveryNotBefore=0;C.Random.Initialize(C.Seed);}
}

void URosImuComponent::DiscardPending()
{for(auto& C:Channels){C.Pending.Reset();if(bPhysicalV3)C.DeliveryNotBefore=LastAdvanceTime;}}

bool URosImuComponent::UsesId(const FString& Id) const
{for(const auto& C:Channels)if(C.Id==Id)return true;return false;}

bool URosImuComponent::UsesArticulatedMount() const
{for(const auto& C:Channels)if(C.ParentFrame!=TEXT("base_link"))return true;return false;}

void URosImuComponent::Advance(double Time,bool Collect,double EpochSolverTime)
{
    if(bPhysicalV3){AdvanceNative(Time,Collect,EpochSolverTime);return;}
    if(!Rig||!FMath::IsFinite(Time)||Time<0)return;
    if(MotionGeneration!=Rig->GetMotionGeneration())
    {
        MotionGeneration=Rig->GetMotionGeneration();
        for(auto& C:Channels){C.HasHistory=false;C.HasRelativeHistory=false;C.Pending.Reset();C.NextDue=Time;++C.HistoryResets;}
    }
    const FVector Gravity=Rig->GetDynamicsGravityVectorCmS2();
    GravityZ=Gravity.Z;
    for(auto& C:Channels)
    {
        if(Time+1.e-9<C.NextDue)continue;
        const double Period=1./C.Rate;
        const int64 Missed=FMath::Max<int64>(0,FMath::FloorToInt64((Time-C.NextDue+1.e-9)/Period));
        C.MissedPeriods+=Missed;C.NextDue+=(Missed+1)*Period;
        FTransform Pose;FVector Velocity,Omega;
        FTransform Relative=C.Mount;
        if(C.ParentFrame!=TEXT("base_link"))
        {
            FTransform Parent;if(!Rig->GetSensorParentWorld(C.ParentFrame,Parent)){++C.Invalid;C.HasHistory=false;continue;}
            Relative=(C.Mount*Parent).GetRelativeTransform(Rig->GetActorTransform());
        }
        if(!Rig->SampleChassisImu(Relative,Pose,Velocity,Omega)){++C.Invalid;C.HasHistory=false;continue;}
        if(C.ParentFrame!=TEXT("base_link"))
        {
            if(!C.HasRelativeHistory)
            {C.PreviousRelative=Relative;C.PreviousRelativeTime=Time;C.HasRelativeHistory=true;C.HasHistory=false;continue;}
            const double JointDt=Time-C.PreviousRelativeTime;
            if(JointDt<=1.e-8){++C.Invalid;C.HasHistory=false;continue;}
            Velocity+=Rig->GetActorQuat().RotateVector((Relative.GetLocation()-C.PreviousRelative.GetLocation())/JointDt);
            FQuat Delta=Relative.GetRotation()*C.PreviousRelative.GetRotation().Inverse();Delta.Normalize();
            if(Delta.W<0)Delta=FQuat(-Delta.X,-Delta.Y,-Delta.Z,-Delta.W);
            Omega+=Rig->GetActorQuat().RotateVector(Delta.GetRotationAxis()*Delta.GetAngle()/JointDt);
            C.PreviousRelative=Relative;C.PreviousRelativeTime=Time;
        }
        if(!C.HasHistory){C.PreviousVelocity=Velocity;C.PreviousTime=Time;C.HasHistory=true;continue;}
        const double Dt=Time-C.PreviousTime;
        if(Dt<=1.e-8){++C.Invalid;C.HasHistory=false;continue;}
        const FVector WorldSpecific=(Velocity-C.PreviousVelocity)/Dt-Gravity;
        const FVector LocalSpecific=Pose.GetRotation().UnrotateVector(WorldSpecific)/100;
        const FVector LocalOmega=Pose.GetRotation().UnrotateVector(Omega);
        FVector Accel(LocalSpecific.X,-LocalSpecific.Y,LocalSpecific.Z);
        // Angular velocity is axial under the UE->ROS reflection, unlike acceleration.
        FVector Gyro(-LocalOmega.X,LocalOmega.Y,-LocalOmega.Z);
        C.PreviousVelocity=Velocity;C.PreviousTime=Time;
        PublishSample(C,Time,Dt,Accel,Gyro,Collect);
    }
}

int32 URosImuComponent::PendingSamplesPerChannel() const
{
    // The existing wire contract caps the combined IMU batch at 128.
    return Rig&&Rig->GetDedicatedMujocoPeriod()>0?128/FMath::Max(1,Channels.Num()):32;
}

void URosImuComponent::PublishSample(FChannel& C,double Time,double IntegrationDt,FVector Accel,FVector Gyro,bool Collect,int64 SampleIndex)
{
    ++C.Sequence;
        for(int I=0;I<3;++I)
        {
            Accel[I]+=C.AccelBias[I]+(C.AccelStd[I]>0?C.AccelStd[I]*Gaussian(C.Random):0);
            Gyro[I]+=C.GyroBias[I]+(C.GyroStd[I]>0?C.GyroStd[I]*Gaussian(C.Random):0);
            if(bPhysicalV3)
            {
                bool Saturated=false;
                Accel[I]=RMSimSensors::Mid360Model::QuantizeAndClamp(Accel[I],C.AccelRange,C.AccelQuantum,Saturated);
                if(Saturated)++C.AccelSaturatedAxes;
                Gyro[I]=RMSimSensors::Mid360Model::QuantizeAndClamp(Gyro[I],C.GyroRange,C.GyroQuantum,Saturated);
                if(Saturated)++C.GyroSaturatedAxes;
            }
        }
        if(Accel.ContainsNaN()||Gyro.ContainsNaN()){++C.Invalid;C.HasHistory=false;return;}
        if(!Collect||Time+1.e-9<C.DeliveryNotBefore){++C.DeliverySuppressed;return;}
        const double PackStarted=bProfileTiming?FPlatformTime::Seconds():0.;
        auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("id"),C.Id);J->SetNumberField(TEXT("seq"),C.Sequence);
        const int64 Stamp=SampleIndex>=0?RMSimSensors::EpochSampleGrid::StampNs(SampleIndex,C.Rate):FMath::RoundToInt64(Time*1.e9);
        J->SetNumberField(TEXT("stamp_ns"),double(Stamp));J->SetNumberField(TEXT("integration_dt_s"),IntegrationDt);
        J->SetArrayField(TEXT("angular_velocity_rad_s"),ImuVector(Gyro));J->SetArrayField(TEXT("specific_force_mps2"),ImuVector(Accel));
        if(C.Pending.Num()>=PendingSamplesPerChannel()){C.Pending.RemoveAt(0,1,EAllowShrinking::No);++C.Dropped;}
        C.Pending.Add(MakeShared<FJsonValueObject>(J));

        if(bProfileTiming)ProfilePackWall+=FPlatformTime::Seconds()-PackStarted;
}

TSharedRef<FJsonObject> URosImuComponent::TakeState()
{
    auto Root=MakeShared<FJsonObject>();Root->SetStringField(TEXT("model"),TEXT("raw6axis_point_velocity_difference; interval_average_acceleration; no_AHRS"));
    Root->SetStringField(TEXT("profile_md5"),ProfileHash);Root->SetBoolField(TEXT("calibrated"),false);
    if(bPhysicalV3)
    {
        Root->SetStringField(TEXT("profile_schema"),TEXT("rm_imu_runtime_v3"));
        Root->SetStringField(TEXT("model"),TEXT("native_point_velocity_centered_difference; instantaneous_native_gyro; SI; no_AHRS"));
        auto Model=MakeShared<FJsonObject>();Model->SetStringField(TEXT("schema"),TEXT("rm_mid360_imu_physical_v1"));
        Model->SetNumberField(TEXT("support_latency_s"),.001);Model->SetNumberField(TEXT("integration_window_s"),.002);
        Model->SetBoolField(TEXT("measurement_calibrated"),false);
        Model->SetStringField(TEXT("noise_model"),TEXT("per_sample_independent_Gaussian_std; not_noise_density"));
        Model->SetStringField(TEXT("range_quantization_model"),TEXT("configured_uncalibrated_per_axis_SI; zero_disables_effect"));
        Model->SetStringField(TEXT("time_model"),TEXT("epoch_locked_sample_grid; wait_for_native_support; no_extrapolation"));
        Root->SetObjectField(TEXT("physical_model"),Model);
    }
    Root->SetNumberField(TEXT("gravity_z_mps2"),GravityZ/100);
    TArray<TSharedPtr<FJsonValue>> Profiles,Samples,Stats;
    for(auto& C:Channels)
    {
        auto P=MakeShared<FJsonObject>();P->SetStringField(TEXT("id"),C.Id);P->SetStringField(TEXT("frame_id"),RobotId+TEXT("/")+C.Id+TEXT("_link"));
        P->SetStringField(TEXT("parent_frame_id"),RobotId+TEXT("/")+C.ParentFrame);P->SetNumberField(TEXT("rate_hz"),C.Rate);
        auto Mount=MakeShared<FJsonObject>();Mount->SetArrayField(TEXT("position_m"),ImuVector(C.OriginMetres));
        const auto Q=C.Mount.GetRotation();Mount->SetArrayField(TEXT("rotation_xyzw"),{MakeShared<FJsonValueNumber>(-Q.X),MakeShared<FJsonValueNumber>(Q.Y),MakeShared<FJsonValueNumber>(-Q.Z),MakeShared<FJsonValueNumber>(Q.W)});
        P->SetObjectField(C.ParentFrame==TEXT("base_link")?TEXT("pose_in_base"):TEXT("pose_in_parent"),Mount);
        if(C.ParentFrame!=TEXT("base_link"))P->SetNumberField(TEXT("mount_schema"),2);P->SetArrayField(TEXT("accel_noise_std_mps2"),ImuVector(C.AccelStd));
        P->SetArrayField(TEXT("gyro_noise_std_rad_s"),ImuVector(C.GyroStd));P->SetArrayField(TEXT("accel_bias_mps2"),ImuVector(C.AccelBias));
        P->SetArrayField(TEXT("gyro_bias_rad_s"),ImuVector(C.GyroBias));P->SetNumberField(TEXT("noise_seed"),C.Seed);
        if(bPhysicalV3)
        {
            auto M=MakeShared<FJsonObject>();M->SetNumberField(TEXT("accel_range_mps2"),C.AccelRange);
            M->SetNumberField(TEXT("gyro_range_rad_s"),C.GyroRange);M->SetNumberField(TEXT("accel_quantization_mps2"),C.AccelQuantum);
            M->SetNumberField(TEXT("gyro_quantization_rad_s"),C.GyroQuantum);P->SetObjectField(TEXT("measurement"),M);
        }
        Profiles.Add(MakeShared<FJsonValueObject>(P));Samples.Append(C.Pending);C.Pending.Reset();
        auto S=MakeShared<FJsonObject>();S->SetStringField(TEXT("id"),C.Id);S->SetNumberField(TEXT("measured_samples"),C.Sequence);
        S->SetNumberField(TEXT("missed_periods"),C.MissedPeriods);S->SetNumberField(TEXT("dropped_samples"),C.Dropped);
        S->SetNumberField(TEXT("history_resets"),C.HistoryResets);S->SetNumberField(TEXT("invalid_samples"),C.Invalid);
        if(bPhysicalV3)
        {
            S->SetNumberField(TEXT("accel_saturated_axes"),C.AccelSaturatedAxes);S->SetNumberField(TEXT("gyro_saturated_axes"),C.GyroSaturatedAxes);
            S->SetNumberField(TEXT("history_misses"),C.HistoryMisses);S->SetNumberField(TEXT("warmup_samples"),C.WarmupSamples);
            S->SetNumberField(TEXT("delivery_suppressed_samples"),C.DeliverySuppressed);
        }
        Stats.Add(MakeShared<FJsonValueObject>(S));
    }
    Root->SetArrayField(TEXT("profiles"),Profiles);Root->SetArrayField(TEXT("samples"),Samples);Root->SetArrayField(TEXT("stats"),Stats);return Root;
}
