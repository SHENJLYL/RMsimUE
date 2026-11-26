#pragma once

#include "CoreMinimal.h"

class AActor;
class UPointLightComponent;
class USceneComponent;
class UMaterialInstanceDynamic;
class UProceduralMeshComponent;
class UTexture2D;

namespace RMUCArmorPresentation
{
inline const FName SentryTag(TEXT("rmuc_sentry_armor_visual"));
inline const FName OutpostTag(TEXT("rmuc_outpost_armor_visual"));
inline const FName SentryLampSlot(TEXT("aim_sentry_red_led"));
inline const FName LampColor(TEXT("ArmorLampColor"));

FLinearColor TeamColor(bool bRed);
float LampLevel(bool bAlive, double FlashSeconds);
UMaterialInstanceDynamic* MakeEmitter(AActor& Owner, bool bRed, float Gain = 1.f);
UPointLightComponent* AddPointLight(AActor& Owner, USceneComponent& Parent, const FVector& LocalPosition,
    const FLinearColor& Color, float RadiusCm, float Candela = 0.f);
void SetPointLight(UPointLightComponent* Light, const FLinearColor& Color, float Candela);

UTexture2D* LoadSticker(const TCHAR* Pattern);
UMaterialInstanceDynamic* MakeFinish(AActor& Owner, UTexture2D* Sticker);
void SetLamp(UMaterialInstanceDynamic* Material, bool bRed, bool bAlive, double FlashSeconds = 0.);
UProceduralMeshComponent* Mount(AActor& Owner, USceneComponent& Parent, const FVector& CentreCm,
    const FVector& Normal, double CoverDepthCm, FName Tag,
    UMaterialInstanceDynamic* Finish);
}
