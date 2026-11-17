#pragma once

#include "CoreMinimal.h"

// ROS basis -> UE basis: S = diag(1,-1,1). Length m -> cm.
// Rotation is R_ue = S R_ros S, not a negation of Euler yaw alone.
namespace RosCoordinates
{
inline FVector Position(const FVector& Metres)
{
    return FVector(Metres.X, -Metres.Y, Metres.Z) * 100.0;
}

inline FQuat Rotation(const FVector& Rpy)
{
    const double Cr = FMath::Cos(Rpy.X * 0.5), Sr = FMath::Sin(Rpy.X * 0.5);
    const double Cp = FMath::Cos(Rpy.Y * 0.5), Sp = FMath::Sin(Rpy.Y * 0.5);
    const double Cy = FMath::Cos(Rpy.Z * 0.5), Sy = FMath::Sin(Rpy.Z * 0.5);
    const double X = Sr * Cp * Cy - Cr * Sp * Sy;
    const double Y = Cr * Sp * Cy + Sr * Cp * Sy;
    const double Z = Cr * Cp * Sy - Sr * Sp * Cy;
    const double W = Cr * Cp * Cy + Sr * Sp * Sy;
    return FQuat(-X, Y, -Z, W).GetNormalized();
}

inline FQuat JointMotion(const FVector& RosAxis, double AngleRad)
{
    // An angular axis is an axial vector: det(S) S a = (-ax,ay,-az).
    return FQuat(FVector(-RosAxis.X, RosAxis.Y, -RosAxis.Z).GetSafeNormal(), AngleRad);
}
}
