#include "ImportLabGameMode.h"
#include "RosBridgeComponent.h"
#include "VehicleRig.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"

void AImportLabGameMode::TickImuFixture(double Seconds)
{
#if WITH_DEV_AUTOMATION_TESTS
    if(bImuFixtureApplied||!Rig||!RosBridge||!RosBridge->HasClient())return;
    ImuFixtureElapsed+=Seconds;if(ImuFixtureElapsed<.5)return;
    if(ImuFixture==TEXT("freefall"))
    {
        FTransform Pose=Rig->GetActorTransform();Pose.AddToTranslation(FVector(0,0,500));Rig->ResetContactPose(Pose);
        bImuFixtureApplied=true;UE_LOG(LogRMSim,Display,TEXT("IMU_FIXTURE freefall raised500cm; actual gravity and suspension remain active"));
    }
    else if(ImuFixture==TEXT("incline"))
    {
        if(!TractionSlope)
        {
            const FRotator R(20,0,0);const FVector Centre(0,0,200);
            TractionSlope=GetWorld()->SpawnActor<AStaticMeshActor>(Centre,R);
            auto* Mesh=TractionSlope->GetStaticMeshComponent();Mesh->SetMobility(EComponentMobility::Movable);
            Mesh->SetStaticMesh(LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube")));
            Mesh->SetWorldScale3D(FVector(8,4,.1));Mesh->SetCollisionProfileName(TEXT("BlockAll"));
            Mesh->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);Mesh->RecreatePhysicsState();
            TractionPose=FTransform(R,Centre+R.Quaternion().GetUpVector()*7);return;
        }
        if(!TractionSlope->GetStaticMeshComponent()->IsPhysicsStateCreated())return;
        Rig->ResetContactPose(TractionPose);bImuFixtureApplied=true;
        UE_LOG(LogRMSim,Display,TEXT("IMU_FIXTURE actual20degree incline; placement only, no sensor value injection"));
    }
#endif
}
