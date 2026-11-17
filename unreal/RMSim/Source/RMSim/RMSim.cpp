#include "Modules/ModuleManager.h"

IMPLEMENT_PRIMARY_GAME_MODULE(FDefaultGameModuleImpl, RMSim, "RMSim");

#if WITH_DEV_AUTOMATION_TESTS
#include "Misc/AutomationTest.h"
#include "RosCoordinates.h"

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRMSimCoordinateTest, "RMSim.Coordinates.ROS_to_UE",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRMSimCoordinateTest::RunTest(const FString& Parameters)
{
    TestTrue(TEXT("one metre position and Y reflection"),
        RosCoordinates::Position(FVector(1, 2, 3)).Equals(FVector(100, -200, 300), 1.e-9));
    TestTrue(TEXT("ROS +yaw maps UE X to -Y"),
        RosCoordinates::Rotation(FVector(0, 0, PI / 2)).RotateVector(FVector(1, 0, 0)).Equals(FVector(0, -1, 0), 1.e-6));
    TestTrue(TEXT("ROS +pitch maps UE X to -Z"),
        RosCoordinates::Rotation(FVector(0, PI / 2, 0)).RotateVector(FVector(1, 0, 0)).Equals(FVector(0, 0, -1), 1.e-6));
    TestTrue(TEXT("ROS +roll maps reflected Y to Z"),
        RosCoordinates::Rotation(FVector(PI / 2, 0, 0)).RotateVector(FVector(0, -1, 0)).Equals(FVector(0, 0, 1), 1.e-6));
    TestTrue(TEXT("joint axial transform agrees with yaw"),
        RosCoordinates::JointMotion(FVector(0, 0, 1), PI / 2).RotateVector(FVector(1, 0, 0)).Equals(FVector(0, -1, 0), 1.e-6));
    return true;
}
#endif
