#include "RosAimController.h"
#include "VehicleRig.h"
#include "TrainingRange.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Dom/JsonObject.h"
#include "HAL/PlatformTime.h"
#include "Misc/ConfigCacheIni.h"

namespace
{
bool AimNumber(const TSharedPtr<FJsonObject>& J,const FString& Key,double& Value)
{
    const auto* V=J->Values.Find(*Key);
    return V&&V->IsValid()&&(*V)->Type==EJson::Number&&(*V)->TryGetNumber(Value)&&FMath::IsFinite(Value);
}
}

bool URosAimController::Initialize(AVehicleRig* Vehicle,USceneCaptureComponent2D* Muzzle,ATrainingRange* Range,bool Enabled,FString& Error)
{
    Rig=Vehicle;Aim=Muzzle;Training=Range;bEnabled=Enabled;
    const bool Geometry=Rig&&Rig->GetRosGimbalGeometry(Aim,YawName,PitchName,PitchMin,PitchMax);
    if(Enabled&&!Geometry){Error=TEXT("ROS aim requires standard Z-yaw/Y-pitch axes with an X-forward muzzle");return false;}
    double YawDegrees=540,PitchDegrees=360;
    GConfig->GetDouble(TEXT("RMSim.ManualControl"),TEXT("GimbalYawSpeedDeg"),YawDegrees,GGameIni);
    GConfig->GetDouble(TEXT("RMSim.ManualControl"),TEXT("GimbalPitchSpeedDeg"),PitchDegrees,GGameIni);
    YawRateLimit=FMath::DegreesToRadians(YawDegrees);PitchRateLimit=FMath::DegreesToRadians(PitchDegrees);
    if(!FMath::IsFinite(YawRateLimit)||!FMath::IsFinite(PitchRateLimit)||YawRateLimit<=0||PitchRateLimit<=0||YawRateLimit>50||PitchRateLimit>50)
    {Error=TEXT("invalid ROS gimbal rate limits");return false;}
    Status=bEnabled?TEXT("waiting"):TEXT("observe_only");return true;
}

FVector2D URosAimController::WorldAngles() const
{
    const FVector F=Aim->GetForwardVector();
    return FVector2D(FMath::Atan2(-F.Y,F.X),FMath::Atan2(-F.Z,FMath::Sqrt(F.X*F.X+F.Y*F.Y)));
}

void URosAimController::Stop(const FString& Reason)
{
    bLive=false;bFire=false;bPermit=false;Yaw.Mode=0;Pitch.Mode=0;
    YawVelocity=0;PitchVelocity=0;Status=Reason;
}

void URosAimController::Reject(const FString& Reason)
{++RejectedCount;LastRejection=Reason;Stop(TEXT("rejected_")+Reason);}

void URosAimController::Reset(bool ResetPose)
{
    Stop(bEnabled?TEXT("waiting"):TEXT("observe_only"));AcceptedSeq=-1;LastCommandWall=-1;bLimited=false;
    if(ResetPose&&bEnabled){FString Error;Rig->SetGimbal(0,0,Error);}
    bHaveSample=false;PreviousMuzzle=Aim->GetComponentTransform();
}

void URosAimController::SetOperatorBrake(bool Brake)
{bOperatorBrake=Brake;if(Brake&&bEnabled)Stop(TEXT("operator_brake"));}

bool URosAimController::Accept(const TSharedPtr<FJsonObject>& J,int64 Sequence)
{
    if(!bEnabled){Reject(TEXT("aim_control_disabled"));return false;}
    if(bOperatorBrake){Reject(TEXT("operator_brake"));return false;}
    if(Sequence<=AcceptedSeq){Reject(TEXT("sequence"));return false;}
    FAxis Y,P;
    auto ReadAxis=[&](const TCHAR* Prefix,FAxis& A,double PositionMax,double RateMax)
    {
        const FString Name(Prefix);double Mode;
        if(!AimNumber(J,Name+TEXT("_type"),Mode)||Mode<0||Mode>2||Mode!=FMath::FloorToDouble(Mode)||
            !AimNumber(J,Name,A.Position)||!AimNumber(J,Name+TEXT("_rate"),A.Rate)||
            !AimNumber(J,Name+TEXT("_min"),A.Minimum)||!AimNumber(J,Name+TEXT("_max"),A.Maximum)||
            FMath::Abs(A.Position)>PositionMax||FMath::Abs(A.Rate)>RateMax||FMath::Abs(A.Minimum)>PositionMax||FMath::Abs(A.Maximum)>PositionMax)return false;
        A.Mode=int(Mode);return A.Mode!=2||A.Maximum-A.Minimum>1.e-5;
    };
    double Fire=0,Permit=0;
    if(!ReadAxis(TEXT("yaw"),Y,PI,YawRateLimit)||!ReadAxis(TEXT("pitch"),P,PI/2,PitchRateLimit)||
        !AimNumber(J,TEXT("fire"),Fire)||!AimNumber(J,TEXT("permit"),Permit)||
        (Fire!=0&&Fire!=1)||(Permit!=0&&Permit!=1))
    {Reject(TEXT("aim_fields_or_limits"));return false;}
    if(Fire>0&&!Training){Reject(TEXT("shooting_not_enabled"));return false;}
    if(Fire>0&&Permit==0){Reject(TEXT("fire_permission"));return false;}
    const auto Current=WorldAngles();
    auto Update=[](FAxis& Old,FAxis New,double Actual)
    {
        if(New.Mode==2)
        {
            New.Target=Old.Mode==2?FMath::Clamp(Old.Target,New.Minimum,New.Maximum):FMath::Clamp(Actual,New.Minimum,New.Maximum);
            New.Direction=Old.Mode==2&&Old.Rate*New.Rate>=0?Old.Direction:(New.Rate<0?-1:1);
        }
        else New.Target=New.Mode==1?New.Position:Actual;
        Old=New;
    };
    Update(Yaw,Y,Current.X);Update(Pitch,P,Current.Y);
    bFire=Fire>0;bPermit=Permit>0;bLive=true;
    LastCommandWall=FPlatformTime::Seconds();Deadline=LastCommandWall+.2;AcceptedSeq=Sequence;Status=TEXT("active");return true;
}

void URosAimController::StepAxis(FAxis& A,double Seconds)
{
    if(A.Mode!=2||A.Rate==0)return;
    const double Width=A.Maximum-A.Minimum;
    double Phase=FMath::Fmod(A.Target-A.Minimum+A.Direction*FMath::Abs(A.Rate)*Seconds,2*Width);
    if(Phase<0)Phase+=2*Width;
    if(Phase<=Width)A.Target=A.Minimum+Phase;
    else{A.Target=A.Minimum+2*Width-Phase;A.Direction=-A.Direction;}
}

void URosAimController::Drive(double Seconds)
{
    StepAxis(Yaw,Seconds);StepAxis(Pitch,Seconds);
    const auto CurrentWorld=WorldAngles();
    const double WY=Yaw.Mode?Yaw.Target:CurrentWorld.X,WP=Pitch.Mode?Pitch.Target:CurrentWorld.Y;
    const FVector WorldDirection(FMath::Cos(WP)*FMath::Cos(WY),-FMath::Cos(WP)*FMath::Sin(WY),-FMath::Sin(WP));
    const FRotator Local=Rig->GetActorTransform().InverseTransformVectorNoScale(WorldDirection).Rotation();
    double BeforeY,BeforeP;Rig->GetGimbalAngles(BeforeY,BeforeP);
    const double DesiredY=Yaw.Mode?-FMath::DegreesToRadians(Local.Yaw):BeforeY;
    const double UnclampedP=Pitch.Mode?-FMath::DegreesToRadians(Local.Pitch):BeforeP;
    const double DesiredP=FMath::Clamp(UnclampedP,PitchMin,PitchMax);
    bLimited=FMath::Abs(DesiredP-UnclampedP)>1.e-6;
    if(bLimited&&Pitch.Mode==2)
    {
        if((UnclampedP>PitchMax&&Pitch.Direction>0)||(UnclampedP<PitchMin&&Pitch.Direction<0))Pitch.Direction=-Pitch.Direction;
        Pitch.Target=CurrentWorld.Y;
    }
    Rig->NudgeGimbal(FMath::Clamp(FMath::FindDeltaAngleRadians(BeforeY,DesiredY),-YawRateLimit*Seconds,YawRateLimit*Seconds),
        FMath::Clamp(DesiredP-BeforeP,-PitchRateLimit*Seconds,PitchRateLimit*Seconds));
}

bool URosAimController::CanFire() const
{return bEnabled&&bLive&&bPermit&&bFire&&!bOperatorBrake&&FPlatformTime::Seconds()<=Deadline&&Training&&Training->GetStatus()==TEXT("running");}

void URosAimController::Advance(double Seconds)
{
    if(!FMath::IsFinite(Seconds)||Seconds<0)return;
    if(bEnabled&&bLive&&FPlatformTime::Seconds()>Deadline)Stop(TEXT("expired"));
    if(bEnabled&&Seconds>0)
    {
        if(bLive)Drive(Seconds);else{YawVelocity=0;PitchVelocity=0;}
        const FTransform End=Aim->GetComponentTransform();
        const FTransform Start=bHaveSample?PreviousMuzzle:End;
        // Use solver elapsed time; normal deltas are small, and long intervals never create old firing.
        for(double Remaining=Seconds;Training&&Remaining>1.e-9;)
        {
            const double Step=FMath::Min(Remaining,.25),Elapsed=Seconds-Remaining;
            FTransform A,B;A.Blend(Start,End,Elapsed/Seconds);B.Blend(Start,End,(Elapsed+Step)/Seconds);
            Training->AdvanceInterval(Step,CanFire(),false,A,B);Remaining-=Step;
        }
    }
    double Y,P;Rig->GetGimbalAngles(Y,P);
    YawVelocity=bHaveSample&&Seconds>0?(Y-PreviousYaw)/Seconds:0;
    PitchVelocity=bHaveSample&&Seconds>0?(P-PreviousPitch)/Seconds:0;
    PreviousYaw=Y;PreviousPitch=P;PreviousMuzzle=Aim->GetComponentTransform();bHaveSample=true;
}

TSharedRef<FJsonObject> URosAimController::State(int64 Stamp) const
{
    auto J=MakeShared<FJsonObject>();J->SetBoolField(TEXT("enabled"),bEnabled);
    J->SetStringField(TEXT("actuator_model"),TEXT("rate_limited_kinematic_gimbal; fixed_neutral_collision_proxy; uncalibrated"));
    J->SetStringField(TEXT("status"),Status);J->SetNumberField(TEXT("accepted_seq"),AcceptedSeq);
    J->SetNumberField(TEXT("rejected_count"),RejectedCount);J->SetStringField(TEXT("last_rejection"),LastRejection);
    J->SetNumberField(TEXT("command_age_s"),LastCommandWall<0?-1:FPlatformTime::Seconds()-LastCommandWall);
    J->SetNumberField(TEXT("stamp_ns"),double(Stamp));J->SetStringField(TEXT("yaw_joint"),YawName);J->SetStringField(TEXT("pitch_joint"),PitchName);
    double Y,P;Rig->GetGimbalAngles(Y,P);const auto W=WorldAngles();
    J->SetNumberField(TEXT("yaw_rad"),Y);J->SetNumberField(TEXT("pitch_rad"),P);
    J->SetNumberField(TEXT("world_yaw_rad"),W.X);J->SetNumberField(TEXT("world_pitch_rad"),W.Y);
    J->SetNumberField(TEXT("yaw_velocity_rad_s"),YawVelocity);J->SetNumberField(TEXT("pitch_velocity_rad_s"),PitchVelocity);
    J->SetNumberField(TEXT("pitch_min_rad"),PitchMin);J->SetNumberField(TEXT("pitch_max_rad"),PitchMax);
    J->SetNumberField(TEXT("yaw_rate_limit"),YawRateLimit);J->SetNumberField(TEXT("pitch_rate_limit"),PitchRateLimit);
    J->SetNumberField(TEXT("target_world_yaw"),Yaw.Target);J->SetNumberField(TEXT("target_world_pitch"),Pitch.Target);
    J->SetBoolField(TEXT("mechanically_limited"),bLimited);J->SetBoolField(TEXT("trigger_active"),CanFire());
    auto Shooter=MakeShared<FJsonObject>();Shooter->SetBoolField(TEXT("available"),Training!=nullptr);
    if(Training)
    {
        const auto Full=Training->State(false);
        for(const TCHAR* Key:{TEXT("status"),TEXT("epoch"),TEXT("time"),TEXT("ammo"),TEXT("heat"),TEXT("shots")})Shooter->SetField(Key,Full->Values[Key]);
        const auto Parameters=Full->GetObjectField(TEXT("demo_parameters"));
        for(const TCHAR* Key:{TEXT("max_heat"),TEXT("heat_per_shot"),TEXT("shot_interval"),TEXT("cooling_per_second"),TEXT("launch_cm_s")})Shooter->SetField(Key,Parameters->Values[Key]);
        Shooter->SetStringField(TEXT("clock_source"),bEnabled?TEXT("Chaos_solver"):TEXT("manual_demo_timer"));
    }
    J->SetObjectField(TEXT("shooter"),Shooter);return J;
}

TSharedRef<FJsonObject> URosAimController::Evaluation() const
{return Training?Training->State(false):MakeShared<FJsonObject>();}
