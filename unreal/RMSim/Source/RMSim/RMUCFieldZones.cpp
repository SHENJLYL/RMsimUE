#include "RMUCFieldZones.h"

#include "Algo/Find.h"
#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
constexpr const TCHAR* ZoneKindNames[] = {
    TEXT("supply"), TEXT("base"), TEXT("outpost"),
    TEXT("fortress"), TEXT("central_highland"), TEXT("vantage"), TEXT("highland_lower")
};
constexpr int32 ContinuousZoneKinds = 6;

bool ReadCoordinate(const TSharedPtr<FJsonValue>& Value, double& Coordinate)
{
    return Value.IsValid() && Value->Type == EJson::Number
        && Value->TryGetNumber(Coordinate) && FMath::IsFinite(Coordinate);
}

bool ReadPair(const TArray<TSharedPtr<FJsonValue>>& Values, FVector2D& Point)
{
    return Values.Num() == 2 && ReadCoordinate(Values[0], Point.X) && ReadCoordinate(Values[1], Point.Y);
}

bool ReadRing(const TArray<TSharedPtr<FJsonValue>>& Values, TArray<FVector2D>& Points)
{
    if (Values.Num() < 3)
    {
        return false;
    }
    for (const TSharedPtr<FJsonValue>& Value : Values)
    {
        const TArray<TSharedPtr<FJsonValue>>* Coordinates = nullptr;
        FVector2D Point;
        if (!Value.IsValid() || !Value->TryGetArray(Coordinates) || !ReadPair(*Coordinates, Point)
            || (!Points.IsEmpty() && Point == Points.Last()))
        {
            return false;
        }
        Points.Add(Point);
    }
    double TwiceArea = 0.0;
    for (int32 Index = 0, Previous = Points.Num() - 1; Index < Points.Num(); Previous = Index++)
    {
        TwiceArea += FVector2D::CrossProduct(Points[Previous], Points[Index]);
    }
    return Points[0] != Points.Last() && FMath::Abs(TwiceArea) > UE_DOUBLE_SMALL_NUMBER;
}

bool InsideRing(const TArray<FVector2D>& Ring, const FVector2D& Point)
{
    bool bInside = false;
    for (int32 Index = 0, Previous = Ring.Num() - 1; Index < Ring.Num(); Previous = Index++)
    {
        const FVector2D& A = Ring[Previous];
        const FVector2D& B = Ring[Index];
        const FVector2D Edge = B - A;
        const FVector2D Offset = Point - A;
        constexpr double EdgeEpsilon = 1e-9;
        const double Projection = FVector2D::DotProduct(Edge, Offset);
        if (FMath::Abs(FVector2D::CrossProduct(Edge, Offset)) <= EdgeEpsilon
            && Projection >= -EdgeEpsilon && Projection <= Edge.SizeSquared() + EdgeEpsilon)
        {
            return true;
        }
        if ((A.Y > Point.Y) != (B.Y > Point.Y)
            && Point.X < A.X + Edge.X * (Point.Y - A.Y) / Edge.Y)
        {
            bInside = !bInside;
        }
    }
    return bInside;
}

bool ReadZone(const FJsonObject& Object, FRMUCFieldZone& Zone, bool bInterruption)
{
    FString Kind;
    FString Side;
    const TArray<TSharedPtr<FJsonValue>>* Height = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* Outer = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* Holes = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* Reference = nullptr;
    if (!Object.TryGetStringField(TEXT("kind"), Kind)
        || !Object.TryGetStringField(TEXT("side"), Side)
        || (Side != TEXT("red") && Side != TEXT("blue"))
        || !Object.TryGetStringField(TEXT("id"), Zone.Id) || Zone.Id != Side + TEXT("_") + Kind
        || !Object.TryGetArrayField(TEXT("z_m"), Height) || !ReadPair(*Height, Zone.ZRangeM)
        || Zone.ZRangeM.X >= Zone.ZRangeM.Y
        || !Object.TryGetArrayField(TEXT("outer_m"), Outer) || !ReadRing(*Outer, Zone.OuterM)
        || !Object.TryGetArrayField(TEXT("holes_m"), Holes)
        || !Object.TryGetArrayField(TEXT("reference_m"), Reference) || Reference->Num() != 3
        || !ReadCoordinate((*Reference)[0], Zone.ReferenceM.X)
        || !ReadCoordinate((*Reference)[1], Zone.ReferenceM.Y)
        || !ReadCoordinate((*Reference)[2], Zone.ReferenceM.Z))
    {
        return false;
    }
    const TCHAR* const* Match = Algo::FindByPredicate(ZoneKindNames, [&Kind](const TCHAR* Name) { return Kind == Name; });
    if (!Match && !bInterruption)
    {
        return false;
    }
    Zone.Kind = bInterruption ? ERMUCFieldZoneKind::TraversalInterruption :
        static_cast<ERMUCFieldZoneKind>(Match - ZoneKindNames);
    Zone.Side = Side == TEXT("red") ? ERMUCFieldZoneSide::Red : ERMUCFieldZoneSide::Blue;
    for (const auto& Hole : *Holes)
    {
        const TArray<TSharedPtr<FJsonValue>>* Coordinates = nullptr;
        TArray<FVector2D> HoleM;
        if (!Hole.IsValid() || !Hole->TryGetArray(Coordinates) || !ReadRing(*Coordinates, HoleM))
        {
            return false;
        }
        for (const FVector2D& Vertex : HoleM)
        {
            if (!InsideRing(Zone.OuterM, Vertex))
            {
                return false;
            }
        }
        Zone.HolesM.Add(MoveTemp(HoleM));
    }
    return Zone.Contains(Zone.ReferenceM);
}
}

bool FRMUCFieldZone::Contains(const FVector& PositionM) const
{
    if (PositionM.ContainsNaN() || PositionM.Z < ZRangeM.X || PositionM.Z > ZRangeM.Y)
    {
        return false;
    }
    const FVector2D XY(PositionM.X, PositionM.Y);
    return InsideRing(OuterM, XY) && !HolesM.ContainsByPredicate(
        [&XY](const TArray<FVector2D>& Hole) { return InsideRing(Hole, XY); });
}

const FRMUCFieldZone* FRMUCFieldZones::Find(ERMUCFieldZoneKind Kind, ERMUCFieldZoneSide Side) const
{
    return Zones.FindByPredicate(
        [Kind, Side](const FRMUCFieldZone& Zone) { return Zone.Kind == Kind && Zone.Side == Side; });
}

bool FRMUCFieldZones::HasTraversalInterruption(const FVector& PositionM) const
{
    return TraversalInterruptions.ContainsByPredicate(
        [&PositionM](const FRMUCFieldZone& Zone) { return Zone.Contains(PositionM); });
}

bool FRMUCFieldZones::Load(const FString& Filename, FString& Error)
{
    Zones.Reset();
    TraversalInterruptions.Reset();
    Error.Reset();
    auto Fail = [&Error, &Filename](const FString& Problem)
    {
        Error = FString::Printf(TEXT("RMUC field zones %s: %s"), *Filename, *Problem);
        return false;
    };

    FString SourceText;
    if (!FFileHelper::LoadFileToString(SourceText, *Filename))
    {
        return Fail(TEXT("cannot read file"));
    }
    TSharedPtr<FJsonObject> Root;
    double Version = 0.0;
    FString Frame;
    FString SourceStepDigest;
    const TSharedPtr<FJsonObject>* Source = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
    if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(SourceText), Root) || !Root.IsValid()
        || !Root->TryGetNumberField(TEXT("schema_version"), Version) || Version != 1.0
        || !Root->TryGetStringField(TEXT("frame"), Frame)
        || Frame != TEXT("ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP")
        || !Root->TryGetObjectField(TEXT("source"), Source) || !Source->IsValid()
        || !(*Source)->TryGetStringField(TEXT("source_step_sha256"), SourceStepDigest)
        || SourceStepDigest != TEXT("a5ad3ede3b7674fc98f2e73b71717216383cb2b4b5dcdbe900c2038918bea8e0")
        || !Root->TryGetArrayField(TEXT("zones"), Values) || Values->Num() != 2 * ContinuousZoneKinds)
    {
        return Fail(TEXT("invalid manifest schema, frame, source identity or zone count"));
    }

    TArray<FRMUCFieldZone> Loaded;
    TArray<FRMUCFieldZone> Interruptions;
    TSet<FString> Ids;
    const auto LoadZones = [&](const TArray<TSharedPtr<FJsonValue>>& Entries, TArray<FRMUCFieldZone>& Result,
        ERMUCFieldZoneKind First, ERMUCFieldZoneKind Last)
    {
        for (const auto& Value : Entries)
        {
            const TSharedPtr<FJsonObject>* Object = nullptr;
            FRMUCFieldZone Zone;
            if (!Value.IsValid() || !Value->TryGetObject(Object) || !Object->IsValid()
                || !ReadZone(**Object, Zone, First == ERMUCFieldZoneKind::TraversalInterruption)
                || Zone.Kind < First || Zone.Kind > Last || Ids.Contains(Zone.Id))
            {
                return false;
            }
            Ids.Add(Zone.Id);
            Result.Add(MoveTemp(Zone));
        }
        return true;
    };
    if (!LoadZones(*Values, Loaded, ERMUCFieldZoneKind::Supply, ERMUCFieldZoneKind::Vantage))
        return Fail(TEXT("invalid or duplicate persistent zone"));

    if (Root->HasField(TEXT("highland_traversal")))
    {
        const TSharedPtr<FJsonObject>* Traversal = nullptr;
        const TArray<TSharedPtr<FJsonValue>>* Lower = nullptr;
        const TArray<TSharedPtr<FJsonValue>>* Other = nullptr;
        const auto LowerKind = ERMUCFieldZoneKind::HighlandLower;
        const auto OtherKind = ERMUCFieldZoneKind::TraversalInterruption;
        if (!Root->TryGetObjectField(TEXT("highland_traversal"), Traversal) || !Traversal->IsValid()
            || !(*Traversal)->TryGetArrayField(TEXT("lower_zones"), Lower) || Lower->Num() != 2
            || !(*Traversal)->TryGetArrayField(TEXT("interruption_zones"), Other) || Other->Num() != 24
            || !LoadZones(*Lower, Loaded, LowerKind, LowerKind)
            || !LoadZones(*Other, Interruptions, OtherKind, OtherKind))
        {
            return Fail(TEXT("invalid or duplicate highland traversal zone"));
        }
    }
    Zones = MoveTemp(Loaded);
    TraversalInterruptions = MoveTemp(Interruptions);
    return true;
}
