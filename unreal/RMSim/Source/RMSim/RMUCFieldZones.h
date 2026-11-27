#pragma once

#include "CoreMinimal.h"

enum class ERMUCFieldZoneKind : uint8
{
    Supply,
    Base,
    Outpost,
    Fortress,
    CentralHighland,
    Vantage,
    HighlandLower,
    TraversalInterruption
};

enum class ERMUCFieldZoneSide : uint8
{
    Red,
    Blue,
    Neutral
};

struct FRMUCFieldZone
{
    FString Id;
    ERMUCFieldZoneKind Kind = ERMUCFieldZoneKind::Supply;
    ERMUCFieldZoneSide Side = ERMUCFieldZoneSide::Neutral;
    FVector ReferenceM = FVector::ZeroVector;
    FVector2D ZRangeM = FVector2D::ZeroVector;
    TArray<FVector2D> OuterM;
    TArray<TArray<FVector2D>> HolesM;

    // Position of the actual surface beneath the reader, in the ROS map frame.
    bool Contains(const FVector& PositionM) const;
};

class FRMUCFieldZones
{
public:
    bool Load(const FString& Filename, FString& Error);
    bool IsLoaded() const { return !Zones.IsEmpty(); }
    const FRMUCFieldZone* Find(ERMUCFieldZoneKind Kind, ERMUCFieldZoneSide Side) const;
    bool HasTraversalInterruption(const FVector& PositionM) const;
    const TArray<FRMUCFieldZone>& GetZones() const { return Zones; }

private:
    TArray<FRMUCFieldZone> Zones;
    TArray<FRMUCFieldZone> TraversalInterruptions;
};
