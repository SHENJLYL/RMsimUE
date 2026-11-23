#include "VehicleRig.h"
#include "MujocoVehicleDynamics.h"
#include "Components/StaticMeshComponent.h"
#include "PhysicsEngine/PhysicsConstraintComponent.h"
#include "Dom/JsonObject.h"

namespace
{
constexpr double AmpsPerCount=16.5/2048.; // MF broadcast command and feedback share this scale.
int Corner(double Forward,double Left){return (Forward>=0?0:2)+(Left>=0?0:1);}
int Encoder(double Angle)
{
    const double Turns=Angle/(2*PI);
    return FMath::Clamp(FMath::FloorToInt((Turns-FMath::FloorToDouble(Turns))*32768.),0,32767);
}
}

bool AVehicleRig::EnableMotorCurrentControl(double WheelNmPerAmp,double SteerNmPerAmp)
{
    if(!bContactPhysics||!bSwerveContact||!FMath::IsFinite(WheelNmPerAmp)||!FMath::IsFinite(SteerNmPerAmp)||
        WheelNmPerAmp<=0||WheelNmPerAmp>5||SteerNmPerAmp<=0||SteerNmPerAmp>5)return false;
    TSet<int> Corners;
    for(const auto& W:ContactWheels)
    {
        if(!W.SteeringBody||(!W.SteerJoint&&!MujocoDynamics))return false;
        Corners.Add(Corner(W.RestLocalCm.X,W.LeftOffsetMetres));
    }
    if(Corners.Num()!=4)return false;
    WheelTorqueNmPerAmp=WheelNmPerAmp;SteerTorqueNmPerAmp=SteerNmPerAmp;
    SteerCurrentCommands.Init(0,4);WheelCurrentCommands.Init(0,4);
    bMotorCurrentControl=true;bMotorCommandValid=false;
    const double WheelMaximumNm=2000*AmpsPerCount*WheelTorqueNmPerAmp;
    ContactMotorTorqueNm=FMath::Min(ContactMotorTorqueNm,WheelMaximumNm);
    ContactBrakeTorqueNm=FMath::Min(ContactBrakeTorqueNm,WheelMaximumNm);
    ContactParameters[TEXT("MotorTorqueNm")]=ContactMotorTorqueNm;
    ContactParameters[TEXT("BrakeTorqueNm")]=ContactBrakeTorqueNm;
    DriveParameters[TEXT("SteeringTorqueNm")]=FMath::Min(GetDriveParameter(TEXT("SteeringTorqueNm")),2000*AmpsPerCount*SteerTorqueNmPerAmp);
    SetMotorServosEnabled(true);return true;
}

bool AVehicleRig::SetMotorCurrentCommands(const TArray<double>& Steer,const TArray<double>& Wheel)
{
    if(!bMotorCurrentControl||Steer.Num()!=4||Wheel.Num()!=4)return false;
    for(double Value:Steer)if(!FMath::IsFinite(Value)||FMath::Abs(Value)>2000)return false;
    for(double Value:Wheel)if(!FMath::IsFinite(Value)||FMath::Abs(Value)>2000)return false;
    SteerCurrentCommands=Steer;WheelCurrentCommands=Wheel;bMotorCommandValid=true;bContactBrake=false;return true;
}

void AVehicleRig::SetMotorServosEnabled(bool Enabled)
{
    bApplyingMotorCurrent=!Enabled;
    for(auto& W:ContactWheels)
    {
        if(MujocoDynamics){W.WheelIntegralBiasRadS=0;W.PreviousWheelTargetRadS=0;continue;}
        W.SteerJoint->SetOrientationDriveTwistAndSwing(Enabled,false);
        W.SteerJoint->SetAngularVelocityDriveTwistAndSwing(Enabled,false);
        if(Enabled)W.SteerJoint->SetAngularDriveParams(GetDriveParameter(TEXT("SteeringStiffnessNmRad"))*10000,
            GetDriveParameter(TEXT("SteeringDampingNmS"))*10000,
            FMath::Min(GetDriveParameter(TEXT("SteeringTorqueNm")),2000*AmpsPerCount*SteerTorqueNmPerAmp)*10000);
        W.Axle->SetAngularVelocityDriveTwistAndSwing(Enabled,false);
        W.WheelIntegralBiasRadS=0;W.PreviousWheelTargetRadS=0;
    }
}

void AVehicleRig::ApplyMotorCurrentDrive()
{
    for(auto& W:ContactWheels)
    {
        const int Index=Corner(W.RestLocalCm.X,W.LeftOffsetMetres);
        if(MujocoDynamics)
        {
            MujocoDynamics->SetForceLimit(W.WheelPart,ContactMotorTorqueNm);
            MujocoDynamics->SetForceLimit(W.SteeringPart,GetDriveParameter(TEXT("SteeringTorqueNm")));
            MujocoDynamics->SetTorque(W.SteeringPart,-SteerCurrentCommands[Index]*AmpsPerCount*SteerTorqueNmPerAmp);
            MujocoDynamics->SetTorque(W.WheelPart,WheelCurrentCommands[Index]*AmpsPerCount*WheelTorqueNmPerAmp*W.AxisLocal.Y);
            double Rate=0;MujocoDynamics->GetJoint(W.SteeringPart,W.SteeringMeasuredRad,Rate);
            W.SteeringTargetRad=FMath::UnwindRadians(W.SteeringMeasuredRad);continue;
        }
        const FVector SteerAxis=W.Slider->GetUpVector();
        const FVector WheelAxis=W.SteeringBody->GetComponentQuat().RotateVector(W.AxisLocal);
        // Physical shaft torques and equal reactions; no velocity/position override.
        const FVector SteerTorque=SteerAxis*(SteerCurrentCommands[Index]*AmpsPerCount*SteerTorqueNmPerAmp*10000);
        const FVector WheelTorque=WheelAxis*(WheelCurrentCommands[Index]*AmpsPerCount*WheelTorqueNmPerAmp*W.AxisLocal.Y*10000);
        W.SteeringBody->AddTorqueInRadians(SteerTorque,NAME_None,false);
        W.Slider->AddTorqueInRadians(-SteerTorque,NAME_None,false);
        W.Wheel->AddTorqueInRadians(WheelTorque,NAME_None,false);
        W.SteeringBody->AddTorqueInRadians(-WheelTorque,NAME_None,false);
        const FVector LocalForward=GetActorTransform().InverseTransformVectorNoScale(W.SteeringBody->GetForwardVector());
        W.SteeringMeasuredRad=-FMath::Atan2(LocalForward.Y,LocalForward.X);
        W.SteeringTargetRad=W.SteeringMeasuredRad; // Hold the last physical direction if the safety brake takes over.
    }
}

TSharedRef<FJsonObject> AVehicleRig::GetMotorFeedback() const
{
    auto State=MakeShared<FJsonObject>();State->SetBoolField(TEXT("enabled"),bMotorCurrentControl);
    State->SetBoolField(TEXT("current_active"),bApplyingMotorCurrent&&bMotorCommandValid&&!bContactBrake);
    State->SetStringField(TEXT("order"),TEXT("front_left,front_right,back_left,back_right"));
    State->SetNumberField(TEXT("amps_per_count"),AmpsPerCount);
    State->SetNumberField(TEXT("wheel_nm_per_amp"),WheelTorqueNmPerAmp);State->SetNumberField(TEXT("steer_nm_per_amp"),SteerTorqueNmPerAmp);
    State->SetBoolField(TEXT("calibrated"),false);
    State->SetNumberField(TEXT("fallback_wheel_brake_nm"),ContactBrakeTorqueNm);
    TArray<TSharedPtr<FJsonValue>> Steers,Wheels;Steers.SetNum(4);Wheels.SetNum(4);
    for(const auto& W:ContactWheels)
    {
        const int Index=Corner(W.RestLocalCm.X,W.LeftOffsetMetres);
        const auto Feedback=[](double Angle,double Speed,double Current)
        {
            auto J=MakeShared<FJsonObject>();J->SetNumberField(TEXT("encoder"),Encoder(Angle));
            J->SetNumberField(TEXT("speed_deg_s"),FMath::Clamp(FMath::RoundToInt(FMath::RadiansToDegrees(Speed)),-32768,32767));
            J->SetNumberField(TEXT("current"),FMath::RoundToInt(Current));return MakeShared<FJsonValueObject>(J);
        };
        if(MujocoDynamics)
        {
            double SteerAngle=0,SteerVelocity=0,WheelAngle=0,WheelVelocity=0;
            MujocoDynamics->GetJoint(W.SteeringPart,SteerAngle,SteerVelocity);
            MujocoDynamics->GetJoint(W.WheelPart,WheelAngle,WheelVelocity);
            const bool Active=bApplyingMotorCurrent&&bMotorCommandValid&&!bContactBrake;
            Steers[Index]=Feedback(-SteerAngle,-SteerVelocity,Active?SteerCurrentCommands[Index]:0);
            Wheels[Index]=Feedback(WheelAngle*W.AxisLocal.Y,WheelVelocity*W.AxisLocal.Y,Active?WheelCurrentCommands[Index]:0);continue;
        }
        const FVector Forward=W.Slider->GetComponentTransform().InverseTransformVectorNoScale(W.SteeringBody->GetForwardVector());
        const double Angle=FMath::Atan2(Forward.Y,Forward.X);
        const double SteerSpeed=FVector::DotProduct(W.SteeringBody->GetPhysicsAngularVelocityInRadians()-W.Slider->GetPhysicsAngularVelocityInRadians(),W.Slider->GetUpVector());
        const FVector WheelAxis=W.SteeringBody->GetComponentQuat().RotateVector(W.AxisLocal);
        const double WheelSpeed=FVector::DotProduct(W.Wheel->GetPhysicsAngularVelocityInRadians()-W.SteeringBody->GetPhysicsAngularVelocityInRadians(),WheelAxis)*W.AxisLocal.Y;
        const FQuat Local=W.SteeringBody->GetComponentQuat().Inverse()*W.Wheel->GetComponentQuat();
        const double WheelAngle=Local.GetTwistAngle(W.AxisLocal)*W.AxisLocal.Y;
        const bool Active=bApplyingMotorCurrent&&bMotorCommandValid&&!bContactBrake;
        Steers[Index]=Feedback(Angle,SteerSpeed,Active?SteerCurrentCommands[Index]:0);
        Wheels[Index]=Feedback(WheelAngle,WheelSpeed,Active?WheelCurrentCommands[Index]:0);
    }
    State->SetArrayField(TEXT("steer"),Steers);State->SetArrayField(TEXT("wheel"),Wheels);return State;
}
