#include "VehicleRig.h"
#include "MujocoVehicleDynamics.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "PhysicsEngine/PhysicsConstraintComponent.h"
#include "Control/SentrySwerveKinematics.h"
#include "HAL/PlatformTime.h"

namespace
{
FVector2D LimitVector(const FVector2D& V,double Maximum)
{ return V.SizeSquared()>Maximum*Maximum?V.GetSafeNormal()*Maximum:V; }
void DriveBasis(const FRotator& Rotation,FVector2D& Forward,FVector2D& Left)
{
    const double Yaw=-FMath::DegreesToRadians(Rotation.Yaw);
    Forward=FVector2D(FMath::Cos(Yaw),FMath::Sin(Yaw));Left=FVector2D(-Forward.Y,Forward.X);
}
}

bool AVehicleRig::InitializeDriveProfile(const TSharedPtr<FJsonObject>& Spec,FString& Error)
{
    const TSharedPtr<FJsonObject> *Profile=nullptr,*Control=nullptr,*Physics=nullptr;
    if(Spec->TryGetObjectField(TEXT("drive_profile"),Profile))
    {
        FString Schema,Units,Backend;
        if(!(*Profile)->TryGetStringField(TEXT("schema"),Schema)||Schema!=TEXT("rm_drive_profile_v1")||
            !(*Profile)->TryGetStringField(TEXT("units"),Units)||Units!=TEXT("SI")||
            !(*Profile)->TryGetStringField(TEXT("backend"),Backend)||
            Backend!=(bSwerveContact?TEXT("swerve_contact_v1"):TEXT("differential_contact_v1")))
        {Error=TEXT("drive profile schema, SI units or backend does not match the physical rig");return false;}
        if(!(*Profile)->TryGetObjectField(TEXT("control"),Control)||!(*Profile)->TryGetObjectField(TEXT("physics"),Physics))
        {Error=TEXT("drive profile requires control and physics parameters");return false;}
        PhysicsOverrides=*Physics;
    }
    struct FSetting {const TCHAR* Name;double Default,Minimum,Maximum;};
    for(const FSetting& P:{FSetting{TEXT("MaxForwardSpeedMps"),3,.1,20},{TEXT("MaxReverseSpeedMps"),1.5,.1,20},
        {TEXT("MaxLateralSpeedMps"),2,.1,20},{TEXT("LinearAccelerationMps2"),.8,.05,20},
        {TEXT("PrecisionSpeedMps"),.35,.05,3},
        {TEXT("LinearDecelerationMps2"),3,.05,30},{TEXT("BrakeDecelerationMps2"),6,.1,50},
        {TEXT("LinearJerkMps3"),4,.1,100},{TEXT("DecelerationJerkMps3"),20,.1,100},{TEXT("ManualYawRateRadS"),PI/2,.1,10},
        {TEXT("LateralAccelerationMps2"),10,.1,30},{TEXT("DirectionJerkMps3"),100,.1,200},
        {TEXT("AngularAccelerationRadS2"),3,.1,50},{TEXT("AngularDecelerationRadS2"),5,.1,50},
        {TEXT("GyroDefaultRadS"),2,.1,10},{TEXT("GyroMinRadS"),.5,.1,10},{TEXT("GyroMaxRadS"),4,.1,10},
        {TEXT("GyroStepRadS"),.5,.05,5},{TEXT("SteeringRateRadS"),15.7079632679,.1,50},
        {TEXT("SteeringStiffnessNmRad"),80,.1,1000},{TEXT("SteeringDampingNmS"),8,.01,100},
        {TEXT("SteeringTorqueNm"),8,.1,100},{TEXT("MaxWheelSpeedMps"),4,.1,25},
        {TEXT("WheelVelocityIntegralGainPerSecond"),3,0,50},{TEXT("WheelIntegralTorqueLimitNm"),2.5,0,100},
        {TEXT("YawVelocityFeedback"),0,0,5},{TEXT("LinearVelocityFeedback"),0,0,5}})
    {
        double Value=P.Default;
        if(Control&&(!(*Control)->TryGetNumberField(P.Name,Value)||!FMath::IsFinite(Value)||Value<P.Minimum||Value>P.Maximum))
        {Error=TEXT("invalid or missing drive parameter: ")+FString(P.Name);return false;}
        DriveParameters.Add(P.Name,Value);
    }
    if(GetDriveParameter(TEXT("GyroMinRadS"))>GetDriveParameter(TEXT("GyroDefaultRadS"))||
       GetDriveParameter(TEXT("GyroDefaultRadS"))>GetDriveParameter(TEXT("GyroMaxRadS")))
    {Error=TEXT("gyro default must lie between minimum and maximum");return false;}
    return true;
}

void AVehicleRig::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if(MujocoDynamics){TickMujocoPhysics(DeltaSeconds);return;}
    if(bContactPhysics&&bExternalDriveLease&&FPlatformTime::Seconds()>ExternalDriveDeadline)
        SetContactDrive(0,0,true);
    if(bContactPhysics)
    {
        const bool CurrentActive=bMotorCurrentControl&&bMotorCommandValid&&!bContactBrake&&
            bExternalDriveLease&&FPlatformTime::Seconds()<=ExternalDriveDeadline;
        if(CurrentActive!=bApplyingMotorCurrent)SetMotorServosEnabled(!CurrentActive);
        if(CurrentActive)ApplyMotorCurrentDrive();
        else UpdateContactDrive(FMath::Clamp(double(DeltaSeconds),0.,.1));
    }
}

void AVehicleRig::EnableExternalDriveLease(bool Enabled)
{bExternalDriveLease=Enabled;ExternalDriveDeadline=0;if(Enabled&&bContactPhysics)SetContactDrive(0,0,true);}

void AVehicleRig::RenewExternalDriveLease(double Seconds)
{ExternalDriveDeadline=FMath::IsFinite(Seconds)&&Seconds>0?FPlatformTime::Seconds()+FMath::Min(Seconds,.25):0.;}

double AVehicleRig::GetMeasuredYawRate() const
{
    if(MujocoDynamics){FMujocoBodyState S;return MujocoDynamics->GetBody(TEXT("body"),S)?-FVector::DotProduct(S.AngularVelocityRadS,GetActorUpVector()):0.;}
    return ContactChassis?-FVector::DotProduct(ContactChassis->GetPhysicsAngularVelocityInRadians(),GetActorUpVector()):0.;
}

FVector AVehicleRig::GetCommandOriginVelocity() const
{
    if(MujocoDynamics)return GetVelocity();
    return ContactChassis?ContactChassis->GetPhysicsLinearVelocityAtPoint(GetActorLocation()):GetVelocity();
}

bool AVehicleRig::SampleChassisImu(const FTransform& Mount,FTransform& SensorWorld,FVector& PointVelocityCm,FVector& AngularVelocityRad) const
{
    if(MujocoDynamics)
    {
        FMujocoBodyState S;if(!MujocoDynamics->GetBody(TEXT("body"),S))return false;
        SensorWorld=Mount*S.World;AngularVelocityRad=S.AngularVelocityRadS;
        PointVelocityCm=S.VelocityCmS+FVector::CrossProduct(S.AngularVelocityRadS,SensorWorld.GetLocation()-S.World.GetLocation());
        return !SensorWorld.ContainsNaN()&&!PointVelocityCm.ContainsNaN()&&!AngularVelocityRad.ContainsNaN();
    }
    if(!ContactChassis||!ContactChassis->IsSimulatingPhysics())return false;
    SensorWorld=Mount*GetActorTransform();
    PointVelocityCm=ContactChassis->GetPhysicsLinearVelocityAtPoint(SensorWorld.GetLocation());
    AngularVelocityRad=ContactChassis->GetPhysicsAngularVelocityInRadians();
    return !SensorWorld.ContainsNaN()&&!PointVelocityCm.ContainsNaN()&&!AngularVelocityRad.ContainsNaN();
}

void AVehicleRig::SetContactDrive(double ForwardMetresPerSecond,double YawRadiansPerSecond,bool Brake)
{ SetContactPlanarDrive(FVector2D(ForwardMetresPerSecond,0),YawRadiansPerSecond,false,Brake); }

void AVehicleRig::SetFreeRollingDiagnostic(bool Enabled,double SteeringRosRadians)
{
    if(!bSwerveContact||!FMath::IsFinite(SteeringRosRadians))return;
    bFreeRollingDiagnostic=Enabled;
    for(auto& Wheel:ContactWheels)Wheel.SteeringTargetRad=FMath::UnwindRadians(SteeringRosRadians);
}

bool AVehicleRig::SetContactPlanarDrive(const FVector2D& LinearMetresPerSecond,double YawRadiansPerSecond,bool WorldFrame,bool Brake)
{
    if(bExternalVelocityTracking){DriveSpeedAcceleration=0;DriveDirectionRate=0;}
    bExternalVelocityTracking=false;
    if(!bContactPhysics)return false;
    if(!FMath::IsFinite(LinearMetresPerSecond.X)||!FMath::IsFinite(LinearMetresPerSecond.Y)||!FMath::IsFinite(YawRadiansPerSecond)||
        (!bSwerveContact&&(WorldFrame||FMath::Abs(LinearMetresPerSecond.Y)>1.e-6)))
    {DriveRequestedLinear=FVector2D::ZeroVector;ContactDriveForward=0;ContactDriveYaw=0;bContactBrake=true;return false;}
    bDriveWorldFrame=WorldFrame;bContactBrake=Brake;
    if(Brake)bMotorCommandValid=false;
    DriveRequestedLinear=LimitVector(LinearMetresPerSecond,GetDriveParameter(TEXT("MaxForwardSpeedMps")));
    ContactDriveForward=LinearMetresPerSecond.X;
    ContactDriveYaw=FMath::Clamp(YawRadiansPerSecond,-GetDriveParameter(TEXT("GyroMaxRadS")),GetDriveParameter(TEXT("GyroMaxRadS")));
    return true;
}

bool AVehicleRig::SetExternalVelocityTarget(const FVector2D& LinearMetresPerSecond,double YawRadiansPerSecond,bool Brake)
{
    if(!bExternalDriveLease)return false;
    const bool Accepted=SetContactPlanarDrive(LinearMetresPerSecond,YawRadiansPerSecond,false,Brake);
    bExternalVelocityTracking=Accepted;
    return Accepted;
}

TSharedRef<FJsonObject> AVehicleRig::GetVelocityCommandState() const
{
    auto State=MakeShared<FJsonObject>();
    auto Vector=[](const FVector2D& V)->TArray<TSharedPtr<FJsonValue>>
        {return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),MakeShared<FJsonValueNumber>(0.)};};
    FVector2D Forward,Left;DriveBasis(GetActorRotation(),Forward,Left);
    const FVector2D Filtered=bDriveWorldFrame?DriveFilteredWorld:
        FVector2D(FVector2D::DotProduct(DriveFilteredWorld,Forward),FVector2D::DotProduct(DriveFilteredWorld,Left));
    State->SetStringField(TEXT("velocity_command_mode"),bExternalVelocityTracking?TEXT("external_shaped_velocity"):TEXT("manual_input_shaper"));
    State->SetStringField(TEXT("applied_velocity_command_mode"),bAppliedExternalVelocityTracking?TEXT("external_shaped_velocity"):TEXT("manual_input_shaper"));
    State->SetBoolField(TEXT("requested_in_world_frame"),bDriveWorldFrame);
    State->SetArrayField(TEXT("requested_linear_m_s"),Vector(DriveRequestedLinear));
    State->SetArrayField(TEXT("filtered_linear_m_s"),Vector(Filtered));
    State->SetArrayField(TEXT("applied_requested_world_linear_m_s"),Vector(DriveAppliedRequestedWorld));
    State->SetArrayField(TEXT("filtered_world_linear_m_s"),Vector(DriveFilteredWorld));
    State->SetNumberField(TEXT("applied_requested_yaw_rad_s"),DriveAppliedRequestedYaw);
    State->SetNumberField(TEXT("filtered_yaw_rad_s"),DriveFilteredYaw);
    State->SetBoolField(TEXT("physical_brake"),bContactBrake);
    return State;
}

void AVehicleRig::UpdateContactDrive(double Seconds)
{
    if(Seconds<=0||!ContactChassis)return;
    FVector2D Forward,Left;DriveBasis(GetActorRotation(),Forward,Left);
    const FVector2D Target=bContactBrake?FVector2D::ZeroVector:
        bDriveWorldFrame?DriveRequestedLinear:Forward*DriveRequestedLinear.X+Left*DriveRequestedLinear.Y;
    const FVector2D PreviousWorld=DriveFilteredWorld;
    const double TargetYaw=bContactBrake?0:ContactDriveYaw;
    DriveAppliedRequestedWorld=Target;DriveAppliedRequestedYaw=TargetYaw;
    bAppliedExternalVelocityTracking=bExternalVelocityTracking;
    if(bExternalVelocityTracking)
    {
        // The original ROS smoother owns the velocity time profile. Apply its
        // sample to wheel setpoints without a second manual input shaper.
        DriveFilteredWorld=Target;
        DriveFilteredYaw=TargetYaw;
        DriveSpeedAcceleration=(Target.Size()-PreviousWorld.Size())/Seconds;
        DriveDirectionRate=0;
    }
    else
    {
    const double CurrentSpeed=PreviousWorld.Size(),TargetSpeed=Target.Size();
    FVector2D Direction=CurrentSpeed>1.e-8?PreviousWorld/CurrentSpeed:Target.GetSafeNormal();
    const FVector2D TargetDirection=TargetSpeed>1.e-8?Target/TargetSpeed:Direction;
    const double Alignment=FVector2D::DotProduct(Direction,TargetDirection);
    // Reversing stops first. A perpendicular command changes heading promptly,
    // without treating the entire direction change as a slow forward acceleration.
    const bool Reversing=CurrentSpeed>.01&&TargetSpeed>.01&&Alignment<-.95;
    const double SpeedGoal=Reversing?0:TargetSpeed,SpeedError=SpeedGoal-CurrentSpeed;
    const bool Slowing=SpeedError<0;
    const double Limit=GetDriveParameter(bContactBrake?TEXT("BrakeDecelerationMps2"):
        Slowing?TEXT("LinearDecelerationMps2"):TEXT("LinearAccelerationMps2"));
    const double Jerk=GetDriveParameter(!Slowing&&!bContactBrake?TEXT("LinearJerkMps3"):TEXT("DecelerationJerkMps3"));
    if(CurrentSpeed<.01&&TargetSpeed>1.e-5&&Alignment<.95)DriveSpeedAcceleration=0;
    const double DesiredAcceleration=FMath::Sign(SpeedError)*FMath::Min(Limit,FMath::Sqrt(2*Jerk*FMath::Abs(SpeedError)));
    DriveSpeedAcceleration=FMath::FInterpConstantTo(DriveSpeedAcceleration,DesiredAcceleration,Seconds,Jerk);
    double NextSpeed=CurrentSpeed+DriveSpeedAcceleration*Seconds;
    if(FMath::IsNearlyZero(SpeedError,1.e-6)||(SpeedError>0&&NextSpeed>=SpeedGoal)||(SpeedError<0&&NextSpeed<=SpeedGoal))
    {NextSpeed=SpeedGoal;DriveSpeedAcceleration=0;}
    if(NextSpeed<0){NextSpeed=0;DriveSpeedAcceleration=0;}
    if(CurrentSpeed<.01&&TargetSpeed>1.e-5)
    {Direction=TargetDirection;DriveDirectionRate=0;}
    else if(!Reversing&&TargetSpeed>1.e-5)
    {
        const double Error=FMath::Atan2(Direction.X*TargetDirection.Y-Direction.Y*TargetDirection.X,Alignment);
        const double ReferenceSpeed=FMath::Max(FMath::Max(CurrentSpeed,NextSpeed),.1);
        const double RateLimit=GetDriveParameter(TEXT("LateralAccelerationMps2"))/ReferenceSpeed;
        const double RateAcceleration=GetDriveParameter(TEXT("DirectionJerkMps3"))/ReferenceSpeed;
        const double DesiredRate=FMath::Sign(Error)*FMath::Min(RateLimit,FMath::Sqrt(2*RateAcceleration*FMath::Abs(Error)));
        DriveDirectionRate=FMath::Clamp(FMath::FInterpConstantTo(DriveDirectionRate,DesiredRate,Seconds,RateAcceleration),-RateLimit,RateLimit);
        double Angle=DriveDirectionRate*Seconds;
        if(Angle*Error>=0&&FMath::Abs(Angle)>=FMath::Abs(Error)){Angle=Error;DriveDirectionRate=0;}
        const double C=FMath::Cos(Angle),S=FMath::Sin(Angle);
        Direction=FVector2D(C*Direction.X-S*Direction.Y,S*Direction.X+C*Direction.Y);
    }
    else DriveDirectionRate=0;
    DriveFilteredWorld=Direction*NextSpeed;

    const bool YawAccelerating=TargetYaw*DriveFilteredYaw>=0&&FMath::Abs(TargetYaw)>FMath::Abs(DriveFilteredYaw);
    DriveFilteredYaw=FMath::FInterpConstantTo(DriveFilteredYaw,TargetYaw,Seconds,GetDriveParameter(YawAccelerating?TEXT("AngularAccelerationRadS2"):TEXT("AngularDecelerationRadS2")));

    }
    DriveAcceleration=(DriveFilteredWorld-PreviousWorld)/Seconds;
    const FVector Measured=GetCommandOriginVelocity()/100;
    const FVector2D MeasuredWorld(Measured.X,-Measured.Y);
    const FVector2D Corrected=DriveFilteredWorld+(DriveFilteredWorld-MeasuredWorld)*GetDriveParameter(TEXT("LinearVelocityFeedback"));
    const double Vx=FVector2D::DotProduct(Corrected,Forward),Vy=bSwerveContact?FVector2D::DotProduct(Corrected,Left):0.;
    const double Wz=DriveFilteredYaw+(DriveFilteredYaw-GetMeasuredYawRate())*GetDriveParameter(TEXT("YawVelocityFeedback"));
    const bool Parking=Target.IsNearlyZero(1.e-4)&&FMath::Abs(TargetYaw)<1.e-4&&DriveFilteredWorld.Size()<.015&&FMath::Abs(DriveFilteredYaw)<.02;
    const bool WakeForDrive=!Parking||bFreeRollingDiagnostic;
    double Largest=GetDriveParameter(TEXT("MaxWheelSpeedMps"));
    for(const auto& W:ContactWheels)Largest=FMath::Max(Largest,FVector2D(Vx-Wz*W.LeftOffsetMetres,Vy+Wz*W.RestLocalCm.X/100).Size());
    const double Desaturate=GetDriveParameter(TEXT("MaxWheelSpeedMps"))/Largest;
    for(auto& W:ContactWheels)
    {
        double Speed=Vx-Wz*W.LeftOffsetMetres;
        if((W.SteerJoint||MujocoDynamics)&&W.SteeringBody)
        {
            const double PreviousSteeringTarget=W.SteeringTargetRad;
            const FVector LocalForward=GetActorTransform().InverseTransformVectorNoScale(W.SteeringBody->GetForwardVector());
            W.SteeringMeasuredRad=-FMath::Atan2(LocalForward.Y,LocalForward.X);
            if(MujocoDynamics){double Rate=0;MujocoDynamics->GetJoint(W.SteeringPart,W.SteeringMeasuredRad,Rate);W.SteeringMeasuredRad=FMath::UnwindRadians(W.SteeringMeasuredRad);}
            const double SideSpeed=Vy+Wz*W.RestLocalCm.X/100;
            const double Magnitude=FMath::Sqrt(Speed*Speed+SideSpeed*SideSpeed);
            if(Parking)
            {
                // Do not turn the modules toward tiny noisy velocity corrections while
                // parked. Keep their directions and brake the rolling component.
                Speed=Speed*FMath::Cos(W.SteeringTargetRad)+SideSpeed*FMath::Sin(W.SteeringTargetRad);
            }
            else if(Magnitude>1.e-6)
            {
                const auto TargetWheel=RMSimSentry::SolveWheel(Vx,Vy,Wz,W.RestLocalCm.X/100,W.LeftOffsetMetres,W.SteeringMeasuredRad);
                const double Desired=FMath::UnwindRadians(TargetWheel.SteeringRosRadians);Speed=TargetWheel.SurfaceSpeedMetresPerSecond;
                W.SteeringTargetRad=FMath::UnwindRadians(W.SteeringTargetRad+FMath::Clamp(
                    FMath::FindDeltaAngleRadians(W.SteeringTargetRad,Desired),-GetDriveParameter(TEXT("SteeringRateRadS"))*Seconds,GetDriveParameter(TEXT("SteeringRateRadS"))*Seconds));
                Speed*=FMath::Max(0.,FMath::Cos(FMath::FindDeltaAngleRadians(W.SteeringMeasuredRad,Desired)));
            }
            else Speed=0;
            // Native implicit PD uses the same moving steering target and rate.
            const double TargetRate=FMath::FindDeltaAngleRadians(PreviousSteeringTarget,W.SteeringTargetRad)/Seconds;
            if(MujocoDynamics)MujocoDynamics->SetServoTarget(W.SteeringPart,W.SteeringTargetRad,TargetRate,true);
            else
            {
                W.SteerJoint->ConstraintInstance.SetAngularOrientationTarget(FQuat(FVector::ForwardVector,W.SteeringTargetRad));
                W.SteerJoint->SetAngularVelocityTarget(FVector(TargetRate/(2*PI),0,0));
                if(WakeForDrive)W.SteeringBody->WakeAllRigidBodies();
            }
        }
        Speed*=Desaturate;
        const double DesiredOmega=Speed/W.RadiusMetres*W.AxisLocal.Y;
        const auto* Parent=W.SteeringBody?W.SteeringBody:W.Slider;
        const FVector WheelAxis=Parent->GetComponentQuat().RotateVector(W.AxisLocal);
        double ActualOmega=FVector::DotProduct(W.Wheel->GetPhysicsAngularVelocityInRadians()-Parent->GetPhysicsAngularVelocityInRadians(),WheelAxis);
        if(MujocoDynamics){double Position=0;MujocoDynamics->GetJoint(W.WheelPart,Position,ActualOmega);}
        if(bFreeRollingDiagnostic||(FMath::Abs(DesiredOmega)>1&&FMath::Abs(W.PreviousWheelTargetRadS)>1&&DesiredOmega*W.PreviousWheelTargetRadS<0))W.WheelIntegralBiasRadS=0;
        if(!bFreeRollingDiagnostic)
        {
            const double BiasLimit=FMath::Min(GetDriveParameter(TEXT("WheelIntegralTorqueLimitNm")),ContactMotorTorqueNm)/ContactMotorDamping;
            W.WheelIntegralBiasRadS=FMath::Clamp(W.WheelIntegralBiasRadS+
                GetDriveParameter(TEXT("WheelVelocityIntegralGainPerSecond"))*(DesiredOmega-ActualOmega)*Seconds,-BiasLimit,BiasLimit);
        }
        W.PreviousWheelTargetRadS=DesiredOmega;
        // PI velocity control: keep native implicit damping for stable substeps and
        // add a bounded integral torque via target bias. This holds gravity at zero speed.
        if(MujocoDynamics)
        {
            W.RequestedRevolutionsPerSecond=(DesiredOmega+W.WheelIntegralBiasRadS)/(2*PI);
            MujocoDynamics->SetForceLimit(W.WheelPart,bContactBrake?ContactBrakeTorqueNm:ContactMotorTorqueNm);
            if(bFreeRollingDiagnostic)MujocoDynamics->SetTorque(W.WheelPart,0);
            else MujocoDynamics->SetServoTarget(W.WheelPart,0,DesiredOmega+W.WheelIntegralBiasRadS,false);
        }
        else
        {
            W.RequestedRevolutionsPerSecond=-(DesiredOmega+W.WheelIntegralBiasRadS)/(2*PI);
            W.Axle->SetAngularVelocityDriveTwistAndSwing(!bFreeRollingDiagnostic,false);
            W.Axle->SetAngularDriveParams(0,ContactMotorDamping*10000,(bContactBrake?ContactBrakeTorqueNm:ContactMotorTorqueNm)*10000);
            W.Axle->SetAngularVelocityTarget(FVector(W.RequestedRevolutionsPerSecond,0,0));
            if(WakeForDrive){W.Wheel->WakeAllRigidBodies();W.Slider->WakeAllRigidBodies();}
        }
    }
    // Repeatedly waking a resting island restarts contact settling every frame.
    // New drive commands wake it here; external contacts still wake it in Chaos.
    if(WakeForDrive&&!MujocoDynamics)ContactChassis->WakeAllRigidBodies();
}
