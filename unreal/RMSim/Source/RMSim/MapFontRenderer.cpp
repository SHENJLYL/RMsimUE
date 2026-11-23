#include "MapFontRenderer.h"

#include "Engine/Font.h"
#include "Engine/Texture2D.h"
#include "Fonts/CompositeFont.h"
#include "GameFramework/HUD.h"

THIRD_PARTY_INCLUDES_START
#include <ft2build.h>
#include FT_FREETYPE_H
THIRD_PARTY_INCLUDES_END

namespace
{
// Original tiny_text_hud: PT Sans reducedheight, forceAutoHint=no.
// stdGuiRender.cpp converts factor_x32=64 to scale=64/32 and extent=(64+31)/32.
// gui_default.dshl therefore selects extraSmooth, cropped to a 2px glyph extent.
constexpr float SourceFontFxScale = 2.0f;
constexpr int32 SourceFontFxExtent = 2;
constexpr bool SourceForceAutoHinting = false;

struct FCoverageBitmap
{
    int32 Width = 0;
    int32 Height = 0;
    TArray<float> Pixels;

    float At(int32 X, int32 Y) const
    {
        return X >= 0 && X < Width && Y >= 0 && Y < Height ? Pixels[Y * Width + X] : 0.0f;
    }

    // Match the source shader's bilinear L8 sampling, with zero outside this glyph.
    float Sample(float X, float Y) const
    {
        if (X <= -0.5f || Y <= -0.5f || X > Width - 0.5f || Y > Height - 0.5f) return 0.0f;
        const int32 X0 = FMath::FloorToInt(X), Y0 = FMath::FloorToInt(Y);
        const float Fx = X - X0, Fy = Y - Y0;
        return FMath::Lerp(FMath::Lerp(At(X0, Y0), At(X0 + 1, Y0), Fx),
            FMath::Lerp(At(X0, Y0 + 1), At(X0 + 1, Y0 + 1), Fx), Fy);
    }
};

float SourceGlow(const FCoverageBitmap& Bitmap, float X, float Y)
{
    // gui_default.dshl: fontFxType=2 selects glow. Its two scale variants
    // use the original coefficients; neither is a repeated offset outline.
    if (SourceFontFxScale < 0.01f) return Bitmap.Sample(X, Y);
    if (SourceFontFxScale >= 1.3f)
    {
        constexpr float Weights[] = {0.329654f, 0.157414f, 0.0129324f};
        constexpr float Offsets[] = {0.622052f, 2.274f, 4.14755f};
        float Sum = 0.0f;
        for (int32 I = 0; I < 3; ++I)
            for (int32 J = 0; J < 3; ++J)
                for (float Sx : {-1.0f, 1.0f})
                    for (float Sy : {-1.0f, 1.0f})
                        Sum += Weights[I] * Weights[J] *
                            Bitmap.Sample(X + Sx * Offsets[I], Y + Sy * Offsets[J]);
        return FMath::Clamp(Sum, 0.0f, 1.0f);
    }

    float Inner = 0.0f, Outer = 0.0f;
    for (float Sx : {-1.0f, 1.0f})
        for (float Sy : {-1.0f, 1.0f})
        {
            Inner += Bitmap.Sample(X + Sx * 0.707f * SourceFontFxScale,
                Y + Sy * 0.707f * SourceFontFxScale);
            Outer += Bitmap.Sample(X + Sx * 1.41f * SourceFontFxScale,
                Y + Sy * 1.41f * SourceFontFxScale);
        }
    Outer += Bitmap.Sample(X - 2.0f * SourceFontFxScale, Y);
    Outer += Bitmap.Sample(X + 2.0f * SourceFontFxScale, Y);
    Outer += Bitmap.Sample(X, Y - 2.0f * SourceFontFxScale);
    Outer += Bitmap.Sample(X, Y + 2.0f * SourceFontFxScale);
    return FMath::Clamp(Bitmap.Sample(X, Y) * 0.6f + Inner * 0.25f + Outer * 0.125f, 0.0f, 1.0f);
}

FString GlyphKey(uint32 Codepoint, int32 PixelHeight, const FColor& Color)
{
    return FString::Printf(TEXT("%d:%u:%08x"), PixelHeight, Codepoint, Color.ToPackedRGBA());
}
}

bool UMapFontRenderer::Initialize(UFont* Font)
{
    if (Font == SourceFont && IsReady()) return true;
    ReleaseFace();
    SourceFont = Font;
    const FCompositeFont* Composite = Font ? Font->GetCompositeFont() : nullptr;
    if (!Composite || Composite->DefaultTypeface.Fonts.IsEmpty()) return false;
    const FFontData& Data = Composite->DefaultTypeface.Fonts[0].Font;
    const FFontFaceDataConstPtr Bytes = Data.GetFontFaceData();
    if (!Bytes.IsValid() || !Bytes->HasData()) return false;
    FaceBytes = Bytes->GetData();

    FT_Library Library = nullptr;
    if (FT_Init_FreeType(&Library)) return false;
    FreeTypeLibrary = Library;
    FT_Face Face = nullptr;
    if (FT_New_Memory_Face(Library, FaceBytes.GetData(), FaceBytes.Num(),
        Data.GetSubFaceIndex(), &Face))
    {
        ReleaseFace();
        return false;
    }
    FreeTypeFace = Face;
    if (FT_Set_Pixel_Sizes(Face, 0, 18) || FT_Load_Char(Face, '3', FT_LOAD_DEFAULT))
    {
        ReleaseFace();
        return false;
    }
    CurrentPixelHeight = 18;
    if (!BuildGlyph('3', FColor::Black, GlyphKey('3', 18, FColor::Black)))
    {
        ReleaseFace();
        return false;
    }
    UE_LOG(LogTemp, Display, TEXT("MAP_SOURCE_FONT_EFFECT_READY font=%s family=%hs "
        "coverage_gamma=0.666666667 effect=glow fx_scale=%.6f"), *GetNameSafe(Font),
        Face->family_name ? Face->family_name : "", SourceFontFxScale);
    return true;
}

void UMapFontRenderer::ReleaseFace()
{
    GlyphTextures.Reset();
    GlyphMetrics.Reset();
    if (FreeTypeFace) FT_Done_Face(static_cast<FT_Face>(FreeTypeFace));
    if (FreeTypeLibrary) FT_Done_FreeType(static_cast<FT_Library>(FreeTypeLibrary));
    FreeTypeFace = nullptr;
    FreeTypeLibrary = nullptr;
    CurrentPixelHeight = 0;
    FaceBytes.Reset();
}

void UMapFontRenderer::BeginDestroy()
{
    ReleaseFace();
    Super::BeginDestroy();
}

bool UMapFontRenderer::BuildGlyph(uint32 Codepoint,
    const FColor& Color, const FString& Key)
{
    FT_Face Face = static_cast<FT_Face>(FreeTypeFace);
    const FT_Int32 Flags = FT_LOAD_TARGET_NORMAL |
        (SourceForceAutoHinting ? FT_LOAD_FORCE_AUTOHINT : 0);
    if (FT_Load_Glyph(Face, FT_Get_Char_Index(Face, Codepoint), Flags) ||
        FT_Render_Glyph(Face->glyph, FT_RENDER_MODE_NORMAL)) return false;
    const FT_GlyphSlot Slot = Face->glyph;
    FMapFontGlyphMetrics Metrics;
    Metrics.Left = Slot->bitmap_left;
    Metrics.Top = Slot->bitmap_top;
    Metrics.Advance = int32(Slot->advance.x >> 6);
    if (!Slot->bitmap.width || !Slot->bitmap.rows)
    {
        GlyphMetrics.Add(Key, Metrics);
        return true;
    }
    if (Slot->bitmap.pixel_mode != FT_PIXEL_MODE_GRAY) return false;

    FCoverageBitmap Bitmap;
    // Original HB extents use this FT face/load flags; its glyph UV rectangle
    // reserves ceil(metrics/64)+1, with transparent space on the right/bottom.
    Bitmap.Width = int32((Slot->metrics.width + 63) >> 6) + 1;
    Bitmap.Height = int32((Slot->metrics.height + 63) >> 6) + 1;
    if (Bitmap.Width < int32(Slot->bitmap.width) || Bitmap.Height < int32(Slot->bitmap.rows)) return false;
    Bitmap.Pixels.SetNumZeroed(Bitmap.Width * Bitmap.Height);
    for (int32 Y = 0; Y < int32(Slot->bitmap.rows); ++Y)
    {
        const int32 Row = Slot->bitmap.pitch >= 0 ? Y : int32(Slot->bitmap.rows) - Y - 1;
        const uint8* Source = Slot->bitmap.buffer + Row * FMath::Abs(Slot->bitmap.pitch);
        for (int32 X = 0; X < int32(Slot->bitmap.width); ++X)
            Bitmap.Pixels[Y * Bitmap.Width + X] = Source[X] / 255.0f;
    }

    Metrics.Padding = SourceFontFxExtent;
    Metrics.Width = Bitmap.Width + Metrics.Padding * 2;
    Metrics.Height = Bitmap.Height + Metrics.Padding * 2;
    TArray<uint8> Bgra;
    Bgra.SetNumZeroed(Metrics.Width * Metrics.Height * 4);
    const float FontAlpha = Color.A / 255.0f;
    constexpr float GlowGray = 128.0f / 255.0f;
    auto Byte = [](float Value) { return uint8(FMath::Clamp(FMath::RoundToInt(Value * 255.0f), 0, 255)); };
    for (int32 Y = 0; Y < Metrics.Height; ++Y)
        for (int32 X = 0; X < Metrics.Width; ++X)
        {
            const float Gx = X - Metrics.Padding, Gy = Y - Metrics.Padding;
            // Original gamma_font(f)=pow(f,1/1.5), applied to glyph coverage.
            const float Core = FMath::Pow(Bitmap.Sample(Gx, Gy), 1.0f / 1.5f);
            const float Glow = SourceGlow(Bitmap, Gx, Gy) * (1.0f - Core);
            const float Alpha = Core * FontAlpha + Glow;
            if (Alpha <= 0.0f) continue;
            // Original output is premultiplied lerp(glow*gray, fontColor, Core).
            // Convert it to straight RGBA for Canvas's translucent texture blend.
            uint8* Pixel = Bgra.GetData() + (Y * Metrics.Width + X) * 4;
            Pixel[0] = Byte((Core * FontAlpha * (Color.B / 255.0f) + Glow * GlowGray) / Alpha);
            Pixel[1] = Byte((Core * FontAlpha * (Color.G / 255.0f) + Glow * GlowGray) / Alpha);
            Pixel[2] = Byte((Core * FontAlpha * (Color.R / 255.0f) + Glow * GlowGray) / Alpha);
            Pixel[3] = Byte(Alpha);
        }

    UTexture2D* Texture = UTexture2D::CreateTransient(Metrics.Width, Metrics.Height,
        PF_B8G8R8A8, NAME_None, TConstArrayView64<uint8>(Bgra.GetData(), Bgra.Num()));
    if (!Texture) return false;
    Texture->SRGB = true;
    Texture->NeverStream = true;
    Texture->LODGroup = TEXTUREGROUP_UI;
    Texture->Filter = TF_Nearest;
    Texture->UpdateResource();
    GlyphTextures.Add(Key, TObjectPtr<UTexture2D>(Texture));
    GlyphMetrics.Add(Key, Metrics);
    return true;
}

bool UMapFontRenderer::Draw(AHUD* Hud, const FString& Text, FVector2D Position,
    float PixelHeight, const FLinearColor& Color, bool CenterX, bool CenterY)
{
    if (!Hud || !IsReady() || Text.IsEmpty()) return false;
    const int32 Height = FMath::Clamp(FMath::FloorToInt(PixelHeight), 6, 96);
    FT_Face Face = static_cast<FT_Face>(FreeTypeFace);
    if (CurrentPixelHeight != Height)
    {
        if (FT_Set_Pixel_Sizes(Face, 0, Height)) return false;
        CurrentPixelHeight = Height;
    }
    // Map labels are a small finite alphabet; limit resize/alternate-color cache growth.
    if (GlyphMetrics.Num() > 512)
    {
        GlyphTextures.Reset();
        GlyphMetrics.Reset();
    }
    const FColor SourceColor = Color.ToFColorSRGB();
    struct FRunGlyph { FString Key; int32 PenX = 0; };
    TArray<FRunGlyph> Run;
    int32 Width = 0;
    FT_UInt Previous = 0;
    for (TCHAR Character : Text)
    {
        const uint32 Codepoint = uint32(Character);
        const FT_UInt Index = FT_Get_Char_Index(Face, Codepoint);
        const FString Key = GlyphKey(Codepoint, Height, SourceColor);
        if (!GlyphMetrics.Contains(Key) && !BuildGlyph(Codepoint, SourceColor, Key)) return false;
        if (Previous && Index && FT_HAS_KERNING(Face))
        {
            FT_Vector Kerning = {};
            if (!FT_Get_Kerning(Face, Previous, Index, FT_KERNING_DEFAULT, &Kerning))
                Width += int32(Kerning.x >> 6);
        }
        Run.Add({Key, Width});
        Width += GlyphMetrics.FindChecked(Key).Advance;
        Previous = Index;
    }

    const int32 Ascent = int32(Face->size->metrics.ascender >> 6);
    const int32 LineHeight = int32(Face->size->metrics.height >> 6);
    if (CenterX) Position.X -= Width * 0.5;
    if (CenterY) Position.Y -= LineHeight * 0.5;
    // Source glyph metrics are integer pixels. Keep the cached final
    // coverage/glow result at 1:1 so it is not blurred a second time by Canvas.
    Position.X = FMath::RoundToDouble(Position.X);
    Position.Y = FMath::RoundToDouble(Position.Y);
    for (const FRunGlyph& Glyph : Run)
    {
        const TObjectPtr<UTexture2D>* Texture = GlyphTextures.Find(Glyph.Key);
        if (!Texture) continue; // A whitespace glyph still contributes its advance.
        const FMapFontGlyphMetrics& Metrics = GlyphMetrics.FindChecked(Glyph.Key);
        Hud->DrawTexture(Texture->Get(), Position.X + Glyph.PenX + Metrics.Left - Metrics.Padding,
            Position.Y + Ascent - Metrics.Top - Metrics.Padding, Metrics.Width, Metrics.Height,
            0, 0, 1, 1, FLinearColor::White, BLEND_Translucent);
    }
    return true;
}
