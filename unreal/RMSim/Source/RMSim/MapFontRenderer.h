#pragma once

#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "MapFontRenderer.generated.h"

class AHUD;
class UFont;
class UTexture2D;

struct FMapFontGlyphMetrics
{
    int32 Left = 0;
    int32 Top = 0;
    int32 Advance = 0;
    int32 Padding = 0;
    int32 Width = 0;
    int32 Height = 0;
};

// Local CPU glyph cache for the original tactical-map font shader. Canvas's
// runtime font path cannot apply its coverage gamma or its source glow kernel.
UCLASS()
class RMSIM_API UMapFontRenderer : public UObject
{
    GENERATED_BODY()
public:
    bool Initialize(UFont* Font);
    bool IsReady() const { return FreeTypeFace != nullptr; }
    bool Draw(AHUD* Hud, const FString& Text, FVector2D Position, float PixelHeight,
        const FLinearColor& Color, bool CenterX, bool CenterY);
    virtual void BeginDestroy() override;

private:
    bool BuildGlyph(uint32 Codepoint, const FColor& Color,
        const FString& Key);
    void ReleaseFace();

    UPROPERTY() TObjectPtr<UFont> SourceFont = nullptr;
    UPROPERTY() TMap<FString, TObjectPtr<UTexture2D>> GlyphTextures;
    TMap<FString, FMapFontGlyphMetrics> GlyphMetrics;
    TArray<uint8> FaceBytes;
    void* FreeTypeLibrary = nullptr;
    void* FreeTypeFace = nullptr;
    int32 CurrentPixelHeight = 0;
};
