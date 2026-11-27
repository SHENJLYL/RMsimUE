#include "RMUCGameReferee.h"
#include "VehicleRig.h"

#if WITH_DEV_AUTOMATION_TESTS
#include "Dom/JsonObject.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

void ARMUCGameReferee::StepFieldBoundaryProbe() const
{
    static const FString Fixture = []
    {
        FString Name;
        FParse::Value(FCommandLine::Get(), TEXT("RefereeFieldBoundaryProbe="), Name);
        return Name;
    }();
    if (Fixture.IsEmpty() || MatchElapsedSeconds < .5 || !IsValid(Rig)) return;
    static bool bFinished = false;
    if (bFinished) return;
    bFinished = true;

    FString Input;
    TArray<TSharedPtr<FJsonValue>> Samples;
    if (!FFileHelper::LoadFileToString(Input, *Fixture) ||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Input), Samples) || Samples.IsEmpty())
    {
        UE_LOG(LogTemp, Error, TEXT("RMUC_FIELD_BOUNDARY_PROBE invalid fixture %s"), *Fixture);
        FPlatformMisc::RequestExitWithStatus(false, 2);
        return;
    }

    int32 Passed = 0;
    std::array<bool, 2> LowerRead{};
    std::array<bool, 2> UpperRead{};
    for (const auto& Sample : Samples)
    {
        const auto Object = Sample->AsObject();
        if (!Object.IsValid()) continue;
        FString ZoneId, SampleId;
        FVector P;
        bool bExpected = false;
        if (!Object->TryGetStringField(TEXT("zone"), ZoneId) || !Object->TryGetStringField(TEXT("id"), SampleId) ||
            !Object->TryGetNumberField(TEXT("x_m"), P.X) || !Object->TryGetNumberField(TEXT("y_m"), P.Y) ||
            !Object->TryGetNumberField(TEXT("surface_z_m"), P.Z) || !Object->TryGetBoolField(TEXT("inside"), bExpected))
        {
            continue;
        }
        const auto* Zone = FieldZones.GetZones().FindByPredicate(
            [&ZoneId](const FRMUCFieldZone& Value) { return Value.Id == ZoneId; });
        if (!Zone) continue;
        const FHitResult Hit = TraceFieldReaderSurface(*Rig, FVector(P.X, -P.Y, P.Z + .08) * 100.0);
        const bool bGround = Hit.bBlockingHit && Hit.ImpactNormal.Z >= .65;
        const FVector ActualM(Hit.ImpactPoint.X * .01, -Hit.ImpactPoint.Y * .01, Hit.ImpactPoint.Z * .01);
        const double HeightErrorM = FMath::Abs(P.Z - ActualM.Z);
        const bool bInside = bGround && Zone->Contains(ActualM);
        const bool bPass = bGround && HeightErrorM <= .01 && bInside == bExpected;
        Passed += bPass ? 1 : 0;
        if (bPass && bInside)
        {
            const auto Index = Zone->Side == ERMUCFieldZoneSide::Red ? 0 : 1;
            if (Zone->Kind == ERMUCFieldZoneKind::HighlandLower) LowerRead[Index] = true;
            if (Zone->Kind == ERMUCFieldZoneKind::CentralHighland) UpperRead[Index] = true;
        }
        UE_LOG(LogTemp, Display, TEXT("RMUC_FIELD_BOUNDARY_CASE id=%s zone=%s expected=%d active=%d ground=%d height_error_m=%.6f pass=%d"),
            *SampleId, *ZoneId, bExpected, bInside, bGround, HeightErrorM, bPass);
    }
    UE_LOG(LogTemp, Display, TEXT("RMUC_FIELD_BOUNDARY_PROBE passed=%d total=%d"), Passed, Samples.Num());
    using Card = ERMUCHighlandCard;
    bool bTraversalPassed = true;
    for (const auto Team : FRMUCFieldRules::Teams)
    {
        const auto Index = FRMUCFieldRules::Index(Team);
        const bool bRed = Team == FieldTeam::Red;
        FRMUCHighlandTraversal Traversal;
        if (LowerRead[Index]) Traversal.Observe(bRed ? Card::RedLower : Card::BlueLower, 1.0, true);
        Traversal.Observe(Card::None, 2.0, true);
        if (UpperRead[Index]) Traversal.Observe(bRed ? Card::RedUpper : Card::BlueUpper, 5.0, true);
        const bool bAwarded = Traversal.Remaining(5.0) == 30.0 && Traversal.Remaining(35.0) == 0.0;
        UE_LOG(LogTemp, Display, TEXT("RMUC_FIELD_TRAVERSAL_PROBE side=%s lower=%d upper=%d fixture_buff_s=%.0f pass=%d"),
            bRed ? TEXT("red") : TEXT("blue"), LowerRead[Index], UpperRead[Index], Traversal.Remaining(5.0), bAwarded);
        bTraversalPassed &= bAwarded;
    }
    FPlatformMisc::RequestExitWithStatus(false, Passed == Samples.Num() && bTraversalPassed ? 0 : 2);
}
#endif
