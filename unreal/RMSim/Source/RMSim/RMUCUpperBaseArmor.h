#pragma once

#include "CoreMinimal.h"

namespace RMUCUpperBaseArmor
{
struct FFace
{
    FVector Point;
    FVector Normal;
    double HalfWidthCm;
    double BelowCm;
    double AboveCm;
};

// Exposed face bounds measured in the post-import CAD frame on product 4774244.
// The visible edges are asymmetric about the original plane sample points.
inline const FFace RedFaces[] = {
    {{1146.2356, .0166, 106.5310}, {-.461749, .000011, .887011}, 7.054, 6.320, 6.165},
    {{1164.5064, 10.3112, 106.9428}, {.230771, .399902, .887031}, 7.045, 6.880, 6.8723},
    {{1164.5642, -10.4180, 106.8791}, {.230946, -.400001, .886940}, 7.045, 6.743, 6.7396},
};

inline FVector TeamNormal(const FVector& RedNormal, int32 Team)
{
    return Team == 0 ? RedNormal : RedNormal * FVector(-1., -1., 1.);
}

inline FVector TeamPoint(const FVector& RedPoint, int32 Team)
{
    return TeamNormal(RedPoint, Team) - (Team == 0 ? FVector::ZeroVector : FVector(.1605, 0., 0.));
}

inline bool Contains(const FVector& Point, const FVector& ImpactNormal, int32 Team)
{
    for (const FFace& Face : RedFaces)
    {
        const FVector Normal = TeamNormal(Face.Normal, Team);
        const FVector Across = FVector::CrossProduct(FVector::UpVector, Normal).GetSafeNormal();
        const FVector Up = FVector::CrossProduct(Normal, Across).GetSafeNormal();
        const FVector Offset = Point - TeamPoint(Face.Point, Team);
        const double Height = FVector::DotProduct(Offset, Up);
        if (FMath::Abs(FVector::DotProduct(Offset, Normal)) <= 1.6 &&
            FMath::Abs(FVector::DotProduct(Offset, Across)) <= Face.HalfWidthCm &&
            Height >= -Face.BelowCm && Height <= Face.AboveCm &&
            FMath::Abs(FVector::DotProduct(ImpactNormal, Normal)) >= .96)
        {
            return true;
        }
    }
    return false;
}
}
