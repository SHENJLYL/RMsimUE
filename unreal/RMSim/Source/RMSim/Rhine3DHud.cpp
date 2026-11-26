#include "Rhine3DHud.h"

#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/Texture2D.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "InputCoreTypes.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Framework/Application/SlateApplication.h"
#include "Fonts/FontMeasure.h"
#include "Rendering/DrawElements.h"
#include "Styling/CoreStyle.h"
#include "Widgets/SCompoundWidget.h"
#include "Widgets/SLeafWidget.h"
#include "Widgets/SOverlay.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Layout/SBackgroundBlur.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SConstraintCanvas.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

namespace Rhine3DInternal
{
    static constexpr int32 NumBlurSlots = 10;

    bool IsOutpostPreviewEnabled()
    {
#if WITH_DEV_AUTOMATION_TESTS
        static const bool bEnabled = FParse::Param(FCommandLine::Get(), TEXT("RhineHudOutpostPreview"));
        return bEnabled;
#else
        return false;
#endif
    }

    inline FColor Col8(uint8 R, uint8 G, uint8 B, uint8 A = 255) { return FColor(R, G, B, A); }
    inline FLinearColor LinCol(uint8 R, uint8 G, uint8 B, uint8 A = 255) { return FLinearColor::FromSRGBColor(FColor(R, G, B, A)); }

    // Rhine Lab High-Contrast Optical Glass Palette
    const FColor C_Ivory      = Col8(252, 252, 248, 255);
    const FColor C_IvorySoft  = Col8(242, 244, 240, 252);
    const FColor C_Sage       = Col8(212, 226, 224, 248);
    const FColor C_SageDark   = Col8(156, 174, 172, 235);
    const FColor C_Gold       = Col8(236, 198, 146, 252);
    const FColor C_GoldHi     = Col8(255, 224, 176, 255);
    const FColor C_Teal       = Col8(98,  208, 232, 255);
    const FColor C_TealDeep   = Col8(42,  140, 172, 255);
    const FColor C_Verm       = Col8(252, 128, 72,  255);
    const FColor C_VermDeep   = Col8(224, 76,  42,  255);
    const FColor C_Mint       = Col8(96,  214, 168, 255);
    const FColor C_MintHi     = Col8(152, 242, 206, 255);
    const FColor C_Crimson    = Col8(245, 82,  72,  255);
    const FColor C_NeutralGry = Col8(186, 196, 200, 242);

    inline FSlateFontInfo MakeRhineFont(int32 Size, bool bBold)
    {
        static const FString BoldPath = TEXT("C:/Windows/Fonts/msyhbd.ttc");
        static const FString RegPath  = TEXT("C:/Windows/Fonts/msyh.ttc");
        static const bool bHasBold = FPaths::FileExists(BoldPath);
        static const bool bHasReg  = FPaths::FileExists(RegPath);

        FSlateFontInfo FontInfo = (bBold && bHasBold)
            ? FSlateFontInfo(BoldPath, Size)
            : ((!bBold && bHasReg) ? FSlateFontInfo(RegPath, Size)
            : (bHasBold ? FSlateFontInfo(BoldPath, Size)
            : FCoreStyle::GetDefaultFontStyle(bBold ? "Bold" : "Regular", Size)));
        FontInfo.OutlineSettings.OutlineSize = 1;
        FontInfo.OutlineSettings.OutlineColor = FLinearColor(0.01f, 0.015f, 0.02f, 0.92f);
        return FontInfo;
    }

    inline FVector3f Rot3D(float X, float Y, float Z, float Rx, float Ry, float Rz)
    {
        float C = FMath::Cos(Rz), Si = FMath::Sin(Rz);
        const float X1 = X * C - Y * Si, Y1 = X * Si + Y * C, Z1 = Z;
        C = FMath::Cos(Ry); Si = FMath::Sin(Ry);
        const float X2 = X1 * C + Z1 * Si, Y2 = Y1, Z2 = -X1 * Si + Z1 * C;
        C = FMath::Cos(Rx); Si = FMath::Sin(Rx);
        return FVector3f(X2, Y2 * C - Z2 * Si, Y2 * Si + Z2 * C);
    }

    inline FVector2f Proj3D(const FVector3f& P, float CX, float CY, float Fov, float Dist)
    {
        const float Denom = FMath::Max(0.25f, Dist + P.Z);
        const float Sc = Fov / Denom;
        return FVector2f(CX + P.X * Sc, CY + P.Y * Sc);
    }

    inline float EaseRhineOut(float T)
    {
        T = FMath::Clamp(T, 0.0f, 1.0f);
        return 1.0f - FMath::Pow(1.0f - T, 4.0f);
    }

    inline FColor TeamColor8(int32 TeamSign, uint8 Alpha = 248)
    {
        if (TeamSign > 0) return Col8(238, 126, 62, Alpha);
        if (TeamSign < 0) return Col8(94, 188, 200, Alpha);
        return Col8(221, 183, 136, Alpha);
    }

    struct FPanel3DBounds
    {
        FVector2f MinPt = FVector2f::ZeroVector;
        FVector2f MaxPt = FVector2f::ZeroVector;
        bool bVisible = false;
    };
}

// =============================================================================
// LAYER 1: CUSTOM SLATE 3D OPTICAL HUD CANVAS (RENDERS ON TOP OF SBackgroundBlur!)
// =============================================================================
class SRhine3DHudCanvasLayer : public SLeafWidget
{
public:
    SLATE_BEGIN_ARGS(SRhine3DHudCanvasLayer) {}
    SLATE_END_ARGS()

    void Construct(const FArguments& InArgs, FRhine3DHudController* InOwner, TArray<Rhine3DInternal::FPanel3DBounds>* InBlurBounds)
    {
        Owner = InOwner;
        BlurBounds = InBlurBounds;
        SetVisibility(EVisibility::HitTestInvisible);
        VertScratch.Reserve(1024);
        IdxScratch.Reserve(2048);
        LineScratch.Reserve(128);
        MinimapBrush.DrawAs = ESlateBrushDrawType::Image;
        MinimapBrush.Tiling = ESlateBrushTileType::NoTile;
    }

    virtual FVector2D ComputeDesiredSize(float) const override { return FVector2D::ZeroVector; }

    virtual int32 OnPaint(
        const FPaintArgs& Args,
        const FGeometry& AllottedGeometry,
        const FSlateRect& MyCullingRect,
        FSlateWindowElementList& OutDrawElements,
        int32 LayerId,
        const FWidgetStyle& InWidgetStyle,
        bool bParentEnabled) const override
    {
        using namespace Rhine3DInternal;
        if (!Owner || !Owner->IsEnabled()) return LayerId;

        const FVector2f ViewSize = AllottedGeometry.GetLocalSize();
        if (ViewSize.X < 64.0f || ViewSize.Y < 64.0f) return LayerId;

        const float W = ViewSize.X;
        const float H = ViewSize.Y;
        const float S = FMath::Clamp(FMath::Min(W / 1920.0f, H / 1080.0f), 0.58f, 1.5f);
        const float CX = W * 0.5f;
        const float CY = H * 0.5f;
        const float T = Owner->ElapsedSec;

        const FRhine3DHudState& St = Owner->GetDisplayState();
        const FSlateRenderTransform& RT = AllottedGeometry.GetAccumulatedRenderTransform();
        const FSlateBrush* WhiteBrush = FCoreStyle::Get().GetBrush("WhiteBrush");
        const FSlateResourceHandle WhiteHandle = FSlateApplication::Get().GetRenderer()->GetResourceHandle(*WhiteBrush);

        if (BlurBounds && BlurBounds->Num() == NumBlurSlots)
        {
            for (FPanel3DBounds& B : *BlurBounds) B.bVisible = false;
        }

        auto DrawPoly = [&](const FVector2f* Pts, int32 Count, const FColor* Cols)
        {
            if (Count < 3) return;
            VertScratch.Reset();
            IdxScratch.Reset();
            for (int32 I = 0; I < Count; ++I)
                VertScratch.Add(FSlateVertex::Make<ESlateVertexRounding::Disabled>(RT, Pts[I], FVector2f(0.f, 0.f), Cols[I]));
            for (int32 I = 1; I < Count - 1; ++I)
            {
                IdxScratch.Add(0); IdxScratch.Add(static_cast<SlateIndex>(I)); IdxScratch.Add(static_cast<SlateIndex>(I + 1));
            }
            FSlateDrawElement::MakeCustomVerts(OutDrawElements, LayerId, WhiteHandle, VertScratch, IdxScratch, nullptr, 0, 0);
        };

        auto DrawSolidPoly = [&](const FVector2f* Pts, int32 Count, const FColor& Col)
        {
            if (Count < 3) return;
            VertScratch.Reset();
            IdxScratch.Reset();
            for (int32 I = 0; I < Count; ++I)
                VertScratch.Add(FSlateVertex::Make<ESlateVertexRounding::Disabled>(RT, Pts[I], FVector2f(0.f, 0.f), Col));
            for (int32 I = 1; I < Count - 1; ++I)
            {
                IdxScratch.Add(0); IdxScratch.Add(static_cast<SlateIndex>(I)); IdxScratch.Add(static_cast<SlateIndex>(I + 1));
            }
            FSlateDrawElement::MakeCustomVerts(OutDrawElements, LayerId, WhiteHandle, VertScratch, IdxScratch, nullptr, 0, 0);
        };

        auto DrawLineSeg = [&](const FVector2f& A, const FVector2f& B, const FLinearColor& Col, float Thick = 1.0f)
        {
            LineScratch.Reset();
            LineScratch.Add(FVector2D(A.X, A.Y));
            LineScratch.Add(FVector2D(B.X, B.Y));
            FSlateDrawElement::MakeLines(OutDrawElements, LayerId + 1, AllottedGeometry.ToPaintGeometry(),
                LineScratch, ESlateDrawEffect::None, Col, true, Thick);
        };

        auto DrawPolyline = [&](const FVector2f* Pts, int32 Count, const FLinearColor& Col, float Thick = 1.0f, bool bClose = false)
        {
            if (Count < 2) return;
            LineScratch.Reset();
            for (int32 I = 0; I < Count; ++I) LineScratch.Add(FVector2D(Pts[I].X, Pts[I].Y));
            if (bClose) LineScratch.Add(FVector2D(Pts[0].X, Pts[0].Y));
            FSlateDrawElement::MakeLines(OutDrawElements, LayerId + 1, AllottedGeometry.ToPaintGeometry(),
                LineScratch, ESlateDrawEffect::None, Col, true, Thick);
        };

        auto MeasureTextW = [&](const FString& Txt, int32 FontSize, bool bBold) -> float
        {
            const int32 ScaledSize = FMath::Max(8, FMath::RoundToInt(FontSize * S));
            const FSlateFontInfo FontInfo = MakeRhineFont(ScaledSize, bBold);
            const TSharedRef<FSlateFontMeasure> Measure = FSlateApplication::Get().GetRenderer()->GetFontMeasureService();
            return static_cast<float>(Measure->Measure(Txt, FontInfo).X);
        };

        auto DrawTextEx = [&](const FString& Txt, const FVector2f& Pos, int32 FontSize, bool bBold, const FColor& Col, int32 AlignX = -1)
        {
            const int32 ScaledSize = FMath::Max(8, FMath::RoundToInt(FontSize * S));
            const FSlateFontInfo FontInfo = MakeRhineFont(ScaledSize, bBold);
            FVector2f DrawPos = Pos;
            if (AlignX >= 0)
            {
                const TSharedRef<FSlateFontMeasure> Measure = FSlateApplication::Get().GetRenderer()->GetFontMeasureService();
                const FVector2D Sz = Measure->Measure(Txt, FontInfo);
                if (AlignX == 0) DrawPos.X -= static_cast<float>(Sz.X) * 0.5f;
                else if (AlignX == 1) DrawPos.X -= static_cast<float>(Sz.X);
            }
            // Crisp dark drop shadow for maximum legibility against bright arena surfaces
            FSlateDrawElement::MakeText(
                OutDrawElements, LayerId + 2,
                AllottedGeometry.ToPaintGeometry(FVector2f(W, H), FSlateLayoutTransform(DrawPos + FVector2f(1.2f * S, 1.2f * S))),
                Txt, FontInfo, ESlateDrawEffect::None, FLinearColor(0.0f, 0.0f, 0.0f, 0.78f * (Col.A / 255.0f)));
            FSlateDrawElement::MakeText(
                OutDrawElements, LayerId + 2,
                AllottedGeometry.ToPaintGeometry(FVector2f(W, H), FSlateLayoutTransform(DrawPos)),
                Txt, FontInfo, ESlateDrawEffect::None, FLinearColor::FromSRGBColor(Col));
        };

        auto DrawArcStrip = [&](const FVector2f& Center, float RIn, float ROut, float DegA, float DegB,
            const FColor& ColA, const FColor& ColB, int32 Segs)
        {
            VertScratch.Reset();
            IdxScratch.Reset();
            for (int32 I = 0; I <= Segs; ++I)
            {
                const float U = float(I) / float(Segs);
                const float Rad = FMath::DegreesToRadians(FMath::Lerp(DegA, DegB, U));
                const float Ca = FMath::Cos(Rad), Sa = FMath::Sin(Rad);
                const FColor C(
                    FMath::Lerp(ColA.R, ColB.R, U), FMath::Lerp(ColA.G, ColB.G, U),
                    FMath::Lerp(ColA.B, ColB.B, U), FMath::Lerp(ColA.A, ColB.A, U));
                VertScratch.Add(FSlateVertex::Make<ESlateVertexRounding::Disabled>(RT, FVector2f(Center.X + Ca * RIn,  Center.Y + Sa * RIn),  FVector2f(0.f, 0.f), C));
                VertScratch.Add(FSlateVertex::Make<ESlateVertexRounding::Disabled>(RT, FVector2f(Center.X + Ca * ROut, Center.Y + Sa * ROut), FVector2f(0.f, 0.f), C));
            }
            for (int32 I = 0; I < Segs; ++I)
            {
                const SlateIndex Base = static_cast<SlateIndex>(I * 2);
                IdxScratch.Add(Base); IdxScratch.Add(Base + 1); IdxScratch.Add(Base + 3);
                IdxScratch.Add(Base); IdxScratch.Add(Base + 3); IdxScratch.Add(Base + 2);
            }
            FSlateDrawElement::MakeCustomVerts(OutDrawElements, LayerId, WhiteHandle, VertScratch, IdxScratch, nullptr, 0, 0);
        };

        const float RigShiftX = -Owner->ParX * 16.0f * S + Owner->ShakeX * S;
        const float RigShiftY = -Owner->ParY * 10.0f * S + Owner->ShakeY * S;

        const float BootT = Owner->bBootActive ? Owner->BootProgress : 1.60f;
        const float RingBoot = EaseRhineOut((BootT - 0.35f) / 0.70f);
        const float PanelBoot = EaseRhineOut((BootT - 0.62f) / 0.88f);
        const float PanelAlphaMul = FMath::Clamp(PanelBoot, 0.0f, 1.0f);

        struct FPanel3DTransform
        {
            float AnchorX, AnchorY, PW, PH;
            float YawRad, PitchRad, ZOff, Fov;
            FVector2f Map(float U, float V) const
            {
                const float LX = (U - 0.5f) * PW;
                const float LY = (V - 0.5f) * PH;
                const FVector3f R = Rot3D(LX, LY, 0.0f, PitchRad, YawRad, 0.0f);
                const float Den = FMath::Max(200.0f, Fov + R.Z + ZOff);
                const float Sc = Fov / Den;
                return FVector2f(AnchorX + R.X * Sc, AnchorY + R.Y * Sc);
            }
        };

        auto DrawTiltedGlassPanel = [&](int32 BlurSlotIdx, const FPanel3DTransform& P3, float ChamferPx, float AlphaScale = 1.0f)
        {
            const float EffA = PanelAlphaMul * AlphaScale;
            if (EffA < 0.02f) return;
            const float Cu = FMath::Clamp(ChamferPx / FMath::Max(1.0f, P3.PW), 0.0f, 0.25f);
            const float Cv = FMath::Clamp(ChamferPx / FMath::Max(1.0f, P3.PH), 0.0f, 0.25f);
            const FVector2f Poly[6] = {
                P3.Map(0.0f, 0.0f), P3.Map(1.0f - Cu, 0.0f), P3.Map(1.0f, Cv),
                P3.Map(1.0f, 1.0f), P3.Map(Cu, 1.0f), P3.Map(0.0f, 1.0f - Cv)
            };
            if (BlurBounds && BlurSlotIdx >= 0 && BlurSlotIdx < BlurBounds->Num())
            {
                FVector2f Mn(1e9f, 1e9f), Mx(-1e9f, -1e9f);
                for (int32 I = 0; I < 6; ++I)
                {
                    Mn.X = FMath::Min(Mn.X, Poly[I].X); Mn.Y = FMath::Min(Mn.Y, Poly[I].Y);
                    Mx.X = FMath::Max(Mx.X, Poly[I].X); Mx.Y = FMath::Max(Mx.Y, Poly[I].Y);
                }
                (*BlurBounds)[BlurSlotIdx].MinPt = Mn;
                (*BlurBounds)[BlurSlotIdx].MaxPt = Mx;
                (*BlurBounds)[BlurSlotIdx].bVisible = (EffA > 0.25f);
            }
            // Deepened smoked optical glass for high contrast while retaining frosted GPU blur depth
            const FColor TopL = Col8(34, 42, 44, static_cast<uint8>(182 * EffA));
            const FColor MidG = Col8(14, 19, 21, static_cast<uint8>(208 * EffA));
            const FColor BotR = Col8(9,  12, 14, static_cast<uint8>(226 * EffA));
            const FColor PolyCols[6] = { TopL, MidG, MidG, BotR, BotR, MidG };
            DrawPoly(Poly, 6, PolyCols);
            DrawPolyline(Poly, 6, LinCol(244, 243, 235, static_cast<uint8>(122 * EffA)), 1.1f, true);
            const FVector2f RimMid = P3.Map((1.0f - Cu) * 0.52f, 0.0f);
            DrawLineSeg(Poly[0], RimMid, LinCol(248, 248, 242, static_cast<uint8>(235 * EffA)), 1.5f);
            DrawLineSeg(RimMid, Poly[1], LinCol(236, 198, 146, static_cast<uint8>(165 * EffA)), 1.2f);
            DrawLineSeg(Poly[0], P3.Map(0.0f, 0.45f), LinCol(244, 243, 235, static_cast<uint8>(150 * EffA)), 1.1f);
        };

        auto DrawPanelQuad = [&](const FPanel3DTransform& P3, float U0, float V0, float U1, float V1, const FColor& ColA, const FColor& ColB)
        {
            const FVector2f Q[4] = { P3.Map(U0, V0), P3.Map(U1, V0), P3.Map(U1, V1), P3.Map(U0, V1) };
            const FColor QC[4] = { ColA, ColB, ColB, ColA };
            DrawPoly(Q, 4, QC);
        };

        // Subtle Layer-1 outer optical reference frame
        const FLinearColor FrameCol = LinCol(244, 243, 235, 24);
        DrawLineSeg(FVector2f(16 * S, 16 * S), FVector2f(W - 16 * S, 16 * S), FrameCol, 1.0f);
        DrawLineSeg(FVector2f(16 * S, H - 16 * S), FVector2f(W - 16 * S, H - 16 * S), FrameCol, 1.0f);
        DrawLineSeg(FVector2f(16 * S, 16 * S), FVector2f(16 * S, H - 16 * S), FrameCol, 1.0f);
        DrawLineSeg(FVector2f(W - 16 * S, 16 * S), FVector2f(W - 16 * S, H - 16 * S), FrameCol, 1.0f);
        DrawTextEx(TEXT("+"), FVector2f(12 * S, 9 * S), 10, false, Col8(244, 243, 235, 110));
        DrawTextEx(TEXT("+"), FVector2f(W - 22 * S, H - 23 * S), 10, false, Col8(244, 243, 235, 110));

        // =====================================================================
        // 01. TOP CURVED 3D TELEMETRY HEADER (BASE HP + DEDICATED OUTPOST HP & CAPTURE STATE + 5 UNITS)
        // =====================================================================
        const float BootZSlide = (1.0f - PanelBoot) * 260.0f * S;
        const float BootYDrop  = (1.0f - PanelBoot) * -42.0f * S;
        const float TopW = FMath::Min(1220.0f * S, W * 0.92f);
        const float ClockW = 224.0f * S;
        const float WingGap = 12.0f * S;
        const float WingW = (TopW - ClockW - WingGap * 2.0f) * 0.5f;
        const float TopH = 114.0f * S;
        const float TopCY = 16.0f * S + TopH * 0.5f + RigShiftY * 0.45f + BootYDrop;

        const FPanel3DTransform PTopL = { CX - ClockW * 0.5f - WingGap - WingW * 0.5f + RigShiftX * 0.5f, TopCY, WingW, TopH,
            FMath::DegreesToRadians(-8.0f), FMath::DegreesToRadians(6.5f), BootZSlide, 1450.0f * S };
        const FPanel3DTransform PTopC = { CX + RigShiftX * 0.5f, TopCY - 2.0f * S, ClockW, TopH,
            0.0f, FMath::DegreesToRadians(6.5f), BootZSlide, 1450.0f * S };
        const FPanel3DTransform PTopR = { CX + ClockW * 0.5f + WingGap + WingW * 0.5f + RigShiftX * 0.5f, TopCY, WingW, TopH,
            FMath::DegreesToRadians(8.0f), FMath::DegreesToRadians(6.5f), BootZSlide, 1450.0f * S };

        DrawTiltedGlassPanel(0, PTopL, 9.0f * S);
        DrawTiltedGlassPanel(1, PTopC, 12.0f * S);
        DrawTiltedGlassPanel(2, PTopR, 9.0f * S);

        // Apply optional simulated Outpost Capture preview mode (for [O] button testing)
        FRhine3DOutpostState EffRedPost = St.RedOutpost;
        FRhine3DOutpostState EffBluePost = St.BlueOutpost;
        if (Owner->GetSimOutpostMode() == 1)
        {
            EffRedPost.Hp = 420;
            EffRedPost.bControllerRed = true;
            EffRedPost.bFullControl = false;
            EffRedPost.TransitionPct = 0.62f;
            EffRedPost.PhaseLabel = TEXT("CAPTURING");
        }
        else if (Owner->GetSimOutpostMode() == 2)
        {
            EffRedPost.Hp = 1000;
            EffRedPost.bOwnerRed = false;
            EffRedPost.bControllerRed = false;
            EffRedPost.bFullControl = true;
            EffRedPost.TransitionPct = 1.0f;
            EffRedPost.PhaseLabel = TEXT("CAPTURED");
        }

        auto DrawOutpostStrip = [&](const FPanel3DTransform& P3, bool bSiteRed, const FRhine3DOutpostState& Post, float GhostPct)
        {
            const bool bControlledByEnemy = Post.bControllerRed != bSiteRed;
            const bool bDestroyed = (Post.Hp <= 0);
            const bool bContested = Post.PhaseLabel == TEXT("CONTESTED");
            const bool bReturning = Post.PhaseLabel == TEXT("RETURNING");
            const bool bCapturing = Post.PhaseLabel == TEXT("CAPTURING");
            const bool bTransitioning = Post.TransitionPct > 0.01f;

            // Outpost bar fill color reflects CURRENT OWNER (switches to enemy faction color when captured/rebuilt!)
            const FColor OwnerColA = bDestroyed ? C_NeutralGry : (Post.bOwnerRed ? C_VermDeep : C_TealDeep);
            const FColor OwnerColB = bDestroyed ? C_NeutralGry : (Post.bOwnerRed ? C_Verm : C_Teal);
            const FColor CtrlCol   = Post.bControllerRed ? C_Verm : C_Teal;

            // Strip container background (highlights with enemy border glow when captured!)
            DrawPanelQuad(P3, 0.03f, 0.34f, 0.97f, 0.57f,
                bControlledByEnemy ? Col8(CtrlCol.R, CtrlCol.G, CtrlCol.B, 34) : Col8(244, 243, 235, 16),
                bControlledByEnemy ? Col8(CtrlCol.R, CtrlCol.G, CtrlCol.B, 22) : Col8(244, 243, 235, 16));
            const FVector2f BoxPts[4] = { P3.Map(0.03f, 0.34f), P3.Map(0.97f, 0.34f), P3.Map(0.97f, 0.57f), P3.Map(0.03f, 0.57f) };
            DrawPolyline(BoxPts, 4,
                bControlledByEnemy ? FLinearColor::FromSRGBColor(CtrlCol) : (bTransitioning ? LinCol(240, 210, 168, 220) : LinCol(244, 243, 235, 48)),
                bControlledByEnemy ? 1.5f : 0.9f, true);

            // Outpost HP bar (inner left/right portion)
            const float BarU0 = 0.042f, BarU1 = 0.68f;
            DrawPanelQuad(P3, BarU0, 0.48f, BarU1, 0.54f, Col8(244, 243, 235, 26), Col8(244, 243, 235, 26));
            const float HpPct = FMath::Clamp(float(Post.Hp) / float(FMath::Max(1, Post.MaxHp)), 0.0f, 1.0f);
            if (GhostPct > HpPct + 0.002f)
            {
                DrawPanelQuad(P3, BarU0, 0.48f, BarU0 + (BarU1 - BarU0) * GhostPct, 0.54f, Col8(244, 243, 235, 185), Col8(244, 243, 235, 185));
            }
            if (HpPct > 0.004f)
            {
                DrawPanelQuad(P3, BarU0, 0.48f, BarU0 + (BarU1 - BarU0) * HpPct, 0.54f, OwnerColA, OwnerColB);
            }

            // Capture / Transition progress bar along very bottom edge of Outpost strip
            if (bTransitioning)
            {
                const FColor ChallCol = Post.bControllerRed ? C_Teal : C_Verm;
                DrawPanelQuad(P3, 0.03f, 0.55f, 0.03f + 0.94f * FMath::Clamp(Post.TransitionPct, 0.0f, 1.0f), 0.57f, ChallCol, C_GoldHi);
            }

            // Outpost text: HP + Explicit Ownership / Capture State Badge!
            const FString HpText = bDestroyed
                ? TEXT("前哨站  已摧毁")
                : FString::Printf(TEXT("前哨站  %d / %d"), Post.Hp, Post.MaxHp);
            DrawTextEx(HpText, P3.Map(0.042f, 0.355f), 8, true, bDestroyed ? C_NeutralGry : C_Ivory);

            FString StateBadge;
            FColor BadgeCol = CtrlCol;
            if (bContested)
            {
                StateBadge = TEXT("双方争夺  计时暂停");
                BadgeCol = C_GoldHi;
            }
            else if (bCapturing || bReturning)
            {
                StateBadge = FString::Printf(TEXT("%s %d%%"), bDestroyed ? TEXT("重建进度") : TEXT("控制易手"),
                    FMath::RoundToInt(Post.TransitionPct * 100));
                BadgeCol = C_GoldHi;
            }
            else if (Post.PhaseLabel == TEXT("DECAYING"))
            {
                StateBadge = FString::Printf(TEXT("夺取进度回退 %d%%"), FMath::RoundToInt(Post.TransitionPct * 100));
                BadgeCol = C_GoldHi;
            }
            else
            {
                StateBadge = FString::Printf(TEXT("%s%s"), Post.bControllerRed ? TEXT("红方") : TEXT("蓝方"),
                    Post.bFullControl ? TEXT("完全控制") : TEXT("临时控制"));
                if (bDestroyed) StateBadge += TEXT("  待重建");
            }
            DrawTextEx(StateBadge, P3.Map(0.955f, 0.385f), 8, true, BadgeCol, 1);
        };

        if (PanelAlphaMul > 0.25f)
        {
            const auto BaseStatus = [](bool bProtected, bool bLargeExposed)
            {
                if (bProtected) return TEXT("护盾保护");
                return bLargeExposed ? TEXT("大装甲展开") : TEXT("仅小装甲可受击");
            };
            // RED Wing: Row 1 Base HP + current damageable armor.
            DrawTextEx(TEXT("红方阵营"), PTopL.Map(0.03f, 0.055f), 10, true, C_Verm);
            DrawTextEx(FString::Printf(TEXT("基地 %d  [%s]"), St.RedBaseHp, BaseStatus(St.bRedBaseProtected, St.bRedBaseLargeArmorExposed)),
                PTopL.Map(0.97f, 0.06f), 8, true, St.bRedBaseProtected ? C_IvorySoft : C_GoldHi, 1);
            DrawPanelQuad(PTopL, 0.03f, 0.22f, 0.97f, 0.295f, Col8(244, 243, 235, 34), Col8(244, 243, 235, 34));
            const float RedPct = FMath::Clamp(float(St.RedBaseHp) / float(FMath::Max(1, St.BaseMaxHp)), 0.0f, 1.0f);
            if (Owner->RedBaseGhostPct > RedPct + 0.002f)
                DrawPanelQuad(PTopL, 0.97f - 0.94f * Owner->RedBaseGhostPct, 0.22f, 0.97f, 0.295f, Col8(244, 243, 235, 195), Col8(244, 243, 235, 195));
            if (RedPct > 0.004f)
                DrawPanelQuad(PTopL, 0.97f - 0.94f * RedPct, 0.22f, 0.97f, 0.295f, C_VermDeep, C_Verm);

            // Row 2 Dedicated Outpost HP & Capture State Strip
            DrawOutpostStrip(PTopL, true, EffRedPost, Owner->RedOutpostGhostPct);

            // Row 3: RMUC 2027 Active Ground Roster Unit Cards (4 units: 重装H1, 步兵I3, 步兵I4, 哨兵S7)
            const int32 RedCount = FMath::Min(5, St.RedUnits.Num());
            const float RedStep = RedCount > 0 ? (0.94f / float(RedCount)) : 0.235f;
            for (int32 I = 0; I < RedCount; ++I)
            {
                const auto& U = St.RedUnits[I];
                const float U0 = 0.03f + I * RedStep, U1 = U0 + RedStep - 0.012f;
                DrawPanelQuad(PTopL, U0, 0.62f, U1, 0.93f, Col8(244, 243, 235, 22), Col8(244, 243, 235, 22));
                const FVector2f B[4] = { PTopL.Map(U0, 0.62f), PTopL.Map(U1, 0.62f), PTopL.Map(U1, 0.93f), PTopL.Map(U0, 0.93f) };
                DrawPolyline(B, 4, U.bIsOwnUnit ? LinCol(255, 224, 176, 235) : LinCol(244, 243, 235, 58), U.bIsOwnUnit ? 1.5f : 0.9f, true);
                DrawTextEx(U.Code, PTopL.Map(U0 + 0.010f, 0.64f), 8, true, U.Hp > 0 ? C_Ivory : C_SageDark);
                DrawTextEx(U.Hp > 0 ? FString::FromInt(U.Hp) : TEXT("阵亡"), PTopL.Map(U1 - 0.010f, 0.64f), 8, false, C_Sage, 1);
                const float P = FMath::Clamp(float(U.Hp) / float(FMath::Max(1, U.MaxHp)), 0.0f, 1.0f);
                DrawPanelQuad(PTopL, U0 + 0.012f, 0.84f, U1 - 0.012f, 0.89f, Col8(244, 243, 235, 36), Col8(244, 243, 235, 36));
                if (P > 0.01f) DrawPanelQuad(PTopL, U0 + 0.012f, 0.84f, U0 + 0.012f + (U1 - U0 - 0.024f) * P, 0.89f, C_Verm, C_Verm);
            }

            // BLUE Wing: Row 1 Base HP + Protected tag
            DrawTextEx(FString::Printf(TEXT("[%s]  基地 %d"), BaseStatus(St.bBlueBaseProtected, St.bBlueBaseLargeArmorExposed), St.BlueBaseHp),
                PTopR.Map(0.03f, 0.06f), 8, true, St.bBlueBaseProtected ? C_IvorySoft : C_GoldHi);
            DrawTextEx(TEXT("蓝方阵营"), PTopR.Map(0.97f, 0.055f), 10, true, C_Teal, 1);
            DrawPanelQuad(PTopR, 0.03f, 0.22f, 0.97f, 0.295f, Col8(244, 243, 235, 34), Col8(244, 243, 235, 34));
            const float BluePct = FMath::Clamp(float(St.BlueBaseHp) / float(FMath::Max(1, St.BaseMaxHp)), 0.0f, 1.0f);
            if (Owner->BlueBaseGhostPct > BluePct + 0.002f)
                DrawPanelQuad(PTopR, 0.03f, 0.22f, 0.03f + 0.94f * Owner->BlueBaseGhostPct, 0.295f, Col8(244, 243, 235, 195), Col8(244, 243, 235, 195));
            if (BluePct > 0.004f)
                DrawPanelQuad(PTopR, 0.03f, 0.22f, 0.03f + 0.94f * BluePct, 0.295f, C_Teal, C_TealDeep);

            // Row 2 Dedicated Outpost HP & Capture State Strip
            DrawOutpostStrip(PTopR, false, EffBluePost, Owner->BlueOutpostGhostPct);

            // Row 3: RMUC 2027 Active Ground Roster Unit Cards
            const int32 BlueCount = FMath::Min(5, St.BlueUnits.Num());
            const float BlueStep = BlueCount > 0 ? (0.94f / float(BlueCount)) : 0.235f;
            for (int32 I = 0; I < BlueCount; ++I)
            {
                const auto& U = St.BlueUnits[I];
                const float U0 = 0.03f + I * BlueStep, U1 = U0 + BlueStep - 0.012f;
                DrawPanelQuad(PTopR, U0, 0.62f, U1, 0.93f, Col8(244, 243, 235, 22), Col8(244, 243, 235, 22));
                const FVector2f B[4] = { PTopR.Map(U0, 0.62f), PTopR.Map(U1, 0.62f), PTopR.Map(U1, 0.93f), PTopR.Map(U0, 0.93f) };
                DrawPolyline(B, 4, U.bIsOwnUnit ? LinCol(255, 224, 176, 235) : LinCol(244, 243, 235, 58), U.bIsOwnUnit ? 1.5f : 0.9f, true);
                DrawTextEx(U.Code, PTopR.Map(U0 + 0.010f, 0.64f), 8, true, U.Hp > 0 ? C_Ivory : C_SageDark);
                DrawTextEx(U.Hp > 0 ? FString::FromInt(U.Hp) : TEXT("阵亡"), PTopR.Map(U1 - 0.010f, 0.64f), 8, false, C_Sage, 1);
                const float P = FMath::Clamp(float(U.Hp) / float(FMath::Max(1, U.MaxHp)), 0.0f, 1.0f);
                DrawPanelQuad(PTopR, U0 + 0.012f, 0.84f, U1 - 0.012f, 0.89f, Col8(244, 243, 235, 36), Col8(244, 243, 235, 36));
                if (P > 0.01f) DrawPanelQuad(PTopR, U0 + 0.012f, 0.84f, U0 + 0.012f + (U1 - U0 - 0.024f) * P, 0.89f, C_Teal, C_Teal);
            }

            // Center Clock Core
            DrawTextEx(FString::Printf(TEXT("< + >  %s  < + >"), *St.MatchPhaseText), PTopC.Map(0.5f, 0.08f), 9, true, C_GoldHi, 0);
            const int32 Sec = FMath::Max(0, FMath::FloorToInt(St.StageRemainSeconds));
            DrawTextEx(FString::Printf(TEXT("%02d:%02d"), Sec / 60, Sec % 60), PTopC.Map(0.5f, 0.27f), 30, true, C_Ivory, 0);
            DrawPanelQuad(PTopC, 0.08f, 0.72f, 0.30f, 0.75f, C_Verm, C_Gold);
            DrawPanelQuad(PTopC, 0.30f, 0.72f, 0.52f, 0.75f, C_Gold, C_Mint);
            DrawPanelQuad(PTopC, 0.52f, 0.72f, 0.92f, 0.75f, C_Mint, C_Teal);
            DrawTextEx(TEXT("RMUC 2027 // 光学战术界面"), PTopC.Map(0.5f, 0.80f), 8, false, C_Sage, 0);

            if (Owner->PhaseSweepTimer > 0.01f)
            {
                const float Sw = 1.0f - FMath::Clamp(Owner->PhaseSweepTimer / 0.75f, 0.0f, 1.0f);
                DrawPanelQuad(PTopC, FMath::Clamp(Sw - 0.14f, 0.0f, 1.0f), 0.02f, FMath::Clamp(Sw + 0.04f, 0.0f, 1.0f), 0.98f,
                    Col8(240, 210, 168, 0), Col8(240, 210, 168, 120));
            }
        }

        // =====================================================================
        // 02. CENTER 3D ASTROLABE RETICLE + FLOWING OVERHEAT + BALLISTIC CARD
        // =====================================================================
        const float RCX = CX + Owner->ShakeX * 0.5f * S;
        const float RCY = CY + Owner->ShakeY * 0.5f * S;
        const float Lock = Owner->LockBlend;
        const float Rec = Owner->Recoil;
        const float RetR = 166.0f * S * FMath::Lerp(0.18f, 1.0f, RingBoot);

        auto DrawRing3D = [&](float Rad, float Rx, float Ry, float Rz, const FLinearColor& Col, float Thick, int32 Segs, int32 Ticks)
        {
            if (RingBoot < 0.05f) return;
            const float Fov = 540.0f * S;
            for (int32 I = 0; I < Segs; ++I)
            {
                if (I % 2 == 1) continue;
                const float A0 = (float(I) / float(Segs)) * 2.0f * PI, A1 = (float(I + 1) / float(Segs)) * 2.0f * PI;
                DrawLineSeg(
                    Proj3D(Rot3D(FMath::Cos(A0) * Rad, FMath::Sin(A0) * Rad, 0, Rx, Ry, Rz), RCX, RCY, Fov, Fov),
                    Proj3D(Rot3D(FMath::Cos(A1) * Rad, FMath::Sin(A1) * Rad, 0, Rx, Ry, Rz), RCX, RCY, Fov, Fov), Col, Thick);
            }
            for (int32 K = 0; K < Ticks; ++K)
            {
                const float A = (float(K) / float(Ticks)) * 2.0f * PI;
                const float R1 = Rad - 4.0f * S, R2 = Rad + ((K % 4 == 0) ? 7.0f : 3.0f) * S;
                DrawLineSeg(
                    Proj3D(Rot3D(FMath::Cos(A) * R1, FMath::Sin(A) * R1, 0, Rx, Ry, Rz), RCX, RCY, Fov, Fov),
                    Proj3D(Rot3D(FMath::Cos(A) * R2, FMath::Sin(A) * R2, 0, Rx, Ry, Rz), RCX, RCY, Fov, Fov), Col, Thick);
            }
        };

        const float TiltFade = 1.0f - Lock * 0.78f;
        DrawRing3D(RetR * (1.06f - Lock * 0.14f) + Rec * 20 * S,  0.34f * TiltFade + Owner->ParY * 0.22f, -0.26f * TiltFade + Owner->ParX * 0.24f,  T * 0.22f,
            Lock > 0.5f ? LinCol(240, 210, 168, 155) : LinCol(244, 243, 235, 86), 1.15f, 72, 24);
        DrawRing3D(RetR * (0.76f - Lock * 0.12f) + Rec * 12 * S, -0.42f * TiltFade - Owner->ParY * 0.16f,  0.36f * TiltFade - Owner->ParX * 0.18f, -T * 0.38f,
            Lock > 0.5f ? LinCol(221, 183, 136, 210) : LinCol(169, 184, 182, 128), 1.30f, 56, 12);
        DrawRing3D(RetR * (0.42f - Lock * 0.10f) + Rec * 6 * S,   0.18f * TiltFade,                       -0.20f * TiltFade,                        T * 0.65f,
            LinCol(244, 243, 235, 168), 1.05f, 44, 0);

        const float Gap = (8.0f + Rec * 10.0f - Lock * 3.0f) * S;
        const float Arm = 18.0f * S;
        const FLinearColor CrossL = Lock > 0.5f ? LinCol(240, 210, 168, 248) : LinCol(244, 243, 235, 245);
        DrawLineSeg(FVector2f(RCX - Gap - Arm, RCY), FVector2f(RCX - Gap, RCY), CrossL, 1.5f);
        DrawLineSeg(FVector2f(RCX + Gap, RCY), FVector2f(RCX + Gap + Arm, RCY), CrossL, 1.5f);
        DrawLineSeg(FVector2f(RCX, RCY - Gap - Arm * 0.75f), FVector2f(RCX, RCY - Gap), CrossL, 1.5f);
        DrawLineSeg(FVector2f(RCX, RCY + Gap), FVector2f(RCX, RCY + Gap + Arm * 1.15f), CrossL, 1.5f);
        for (int32 D = 1; D <= 3; ++D)
        {
            const float Ty = RCY + Gap + 9.0f * D * S, Tw = (9.0f - D * 2.0f) * S;
            DrawLineSeg(FVector2f(RCX - Tw, Ty), FVector2f(RCX + Tw, Ty), CrossL, 1.2f);
        }

        if (Lock > 0.05f)
        {
            const float D = (26.0f - Lock * 7.0f) * S, Rd = (1.0f - Lock) * 0.52f;
            const float Cd = FMath::Cos(Rd), Sd = FMath::Sin(Rd);
            const FVector2f DP[4] = {
                FVector2f(RCX + (-(-D) * Sd), RCY + (-D) * Cd), FVector2f(RCX + (D * Cd), RCY + (D * Sd)),
                FVector2f(RCX + (-D * Sd), RCY + (D * Cd)),     FVector2f(RCX + ((-D) * Cd), RCY + ((-D) * Sd))
            };
            DrawPolyline(DP, 4, LinCol(240, 210, 168, static_cast<uint8>(Lock * 240.0f)), 1.65f, true);
        }

        const float ArcR = RetR * 1.26f;
        const float HeatPct = FMath::Clamp(Owner->IsOverheatPreview() ? 0.94f : St.BarrelHeatPct, 0.0f, 1.0f);
        const float BufPct  = FMath::Clamp(St.SupercapPct, 0.0f, 1.0f);
        DrawArcStrip(FVector2f(RCX, RCY), ArcR - 5.0f * S, ArcR, 130.0f, 230.0f, Col8(244, 243, 235, 34), Col8(244, 243, 235, 34), 24);
        if (HeatPct > 0.005f)
        {
            const FColor HTop = HeatPct > 0.85f ? C_Crimson : (HeatPct > 0.55f ? C_Verm : C_Teal);
            const FColor HBot = HeatPct > 0.55f ? C_Gold : C_Teal;
            DrawArcStrip(FVector2f(RCX, RCY), ArcR - 5.5f * S, ArcR + 0.5f * S, 230.0f - 100.0f * HeatPct, 230.0f, HTop, HBot, 22);
        }
        if (HeatPct > 0.85f)
        {
            const float FlowDeg = FMath::Fmod(T * 52.0f, 10.0f);
            for (float A = 132.0f + FlowDeg; A < 226.0f; A += 10.0f)
            {
                const float R1 = ArcR - 14.0f * S, R2 = ArcR - 7.0f * S;
                const float Rad1 = FMath::DegreesToRadians(A), Rad2 = FMath::DegreesToRadians(A + 4.0f);
                DrawLineSeg(FVector2f(RCX + FMath::Cos(Rad1) * R1, RCY + FMath::Sin(Rad1) * R1),
                            FVector2f(RCX + FMath::Cos(Rad2) * R2, RCY + FMath::Sin(Rad2) * R2), LinCol(224, 67, 56, 210), 2.4f * S);
            }
            const uint8 PulseA = static_cast<uint8>(165 + 85 * FMath::Sin(T * 14.0f));
            DrawTextEx(TEXT("枪口过热 // 热量告警"), FVector2f(RCX - ArcR - 16 * S, RCY - 6 * S), 10, true, Col8(245, 82, 72, PulseA), 1);
        }

        DrawArcStrip(FVector2f(RCX, RCY), ArcR - 5.0f * S, ArcR, -50.0f, 50.0f, Col8(244, 243, 235, 34), Col8(244, 243, 235, 34), 24);
        if (BufPct > 0.005f)
            DrawArcStrip(FVector2f(RCX, RCY), ArcR - 5.5f * S, ArcR + 0.5f * S, 50.0f - 100.0f * BufPct, 50.0f, BufPct < 0.25f ? C_Gold : C_Mint, C_MintHi, 22);

        const float BalW = 176 * S, BalH = 104 * S;
        const FPanel3DTransform PBal = { RCX + ArcR + 28 * S + BalW * 0.5f + RigShiftX * 0.35f, RCY - 8 * S + RigShiftY * 0.35f,
            BalW, BalH, FMath::DegreesToRadians(11.0f), 0.0f, BootZSlide * 0.6f, 1300.0f * S };
        if (PanelAlphaMul > 0.25f)
        {
            const FVector2f LeadA(RCX + ArcR * 0.96f, RCY - 26 * S), LeadB(RCX + ArcR + 18 * S, RCY - 36 * S);
            DrawLineSeg(LeadA, LeadB, LinCol(244, 243, 235, 145), 1.0f);
            DrawLineSeg(LeadB, PBal.Map(0.0f, 0.24f), LinCol(244, 243, 235, 145), 1.0f);
        }
        DrawTiltedGlassPanel(5, PBal, 8.0f * S);
        if (PanelAlphaMul > 0.25f)
        {
            DrawTextEx(TEXT("火控弹道"), PBal.Map(0.07f, 0.08f), 8, false, C_Sage);
            DrawTextEx(Lock > 0.5f ? TEXT("已锁定") : TEXT("自由瞄准"), PBal.Map(0.93f, 0.08f), 8, true, Lock > 0.5f ? C_GoldHi : C_Sage, 1);
            DrawTextEx(TEXT("17mm 弹量"), PBal.Map(0.07f, 0.34f), 8, false, C_Sage);
            DrawTextEx(FString::FromInt(St.Ammo17mm), PBal.Map(0.93f, 0.24f), 20, true, C_GoldHi, 1);
            DrawTextEx(TEXT("弹丸初速"), PBal.Map(0.07f, 0.59f), 8, false, C_Sage);
            DrawTextEx(FString::Printf(TEXT("%.1f m/s"), St.MuzzleSpeedMs), PBal.Map(0.93f, 0.56f), 11, true, C_Ivory, 1);
            DrawTextEx(TEXT("热量 / 电容"), PBal.Map(0.07f, 0.78f), 8, false, C_Sage);
            DrawTextEx(FString::Printf(TEXT("%d%% / %d%%"), FMath::RoundToInt(HeatPct * 100), FMath::RoundToInt(BufPct * 100)),
                PBal.Map(0.93f, 0.77f), 10, false, C_IvorySoft, 1);
        }

        const float EffectiveHit = FMath::Max(St.OwnHitFlash, Owner->SimHitFlash);
        if (EffectiveHit > 0.02f)
        {
            const uint8 HA = static_cast<uint8>(FMath::Clamp(EffectiveHit * 245.0f, 30.0f, 245.0f));
            const uint8 ArmId = Owner->SimHitFlash > St.OwnHitFlash ? Owner->SimHitArmorId : St.LastArmorId;
            const float SDeg = (ArmId == 0) ? -125.0f : (ArmId == 1) ? 145.0f : (ArmId == 2) ? 55.0f : -35.0f;
            const float HR = RetR * (0.90f + (1.0f - FMath::Clamp(EffectiveHit, 0.0f, 1.0f)) * 0.14f);
            DrawArcStrip(FVector2f(RCX, RCY), HR, HR + 8.0f * S, SDeg, SDeg + 70.0f, Col8(224, 67, 56, HA), Col8(224, 67, 56, HA), 16);
        }
        if (St.HitConfirmFlash > 0.02f)
        {
            const FLinearColor HC = LinCol(238, 126, 62, 248);
            DrawLineSeg(FVector2f(RCX - 13 * S, RCY - 13 * S), FVector2f(RCX - 5 * S, RCY - 5 * S), HC, 2.2f);
            DrawLineSeg(FVector2f(RCX + 13 * S, RCY - 13 * S), FVector2f(RCX + 5 * S, RCY - 5 * S), HC, 2.2f);
            DrawLineSeg(FVector2f(RCX - 13 * S, RCY + 13 * S), FVector2f(RCX - 5 * S, RCY + 5 * S), HC, 2.2f);
            DrawLineSeg(FVector2f(RCX + 13 * S, RCY + 13 * S), FVector2f(RCX + 5 * S, RCY + 5 * S), HC, 2.2f);
            DrawTextEx(FString::Printf(TEXT("-%d"), St.LastHitDamage), FVector2f(RCX + 20 * S, RCY - 16 * S), 16, true, C_GoldHi);
        }

        // =====================================================================
        // 03. BOTTOM-LEFT 3D HOLOGRAPHIC CHASSIS & VITALS POD
        // =====================================================================
        const float PodW = 424.0f * S, PodH = 180.0f * S;
        const FPanel3DTransform PVit = {
            28.0f * S + PodW * 0.5f + RigShiftX * 0.65f - (1.0f - PanelBoot) * 75.0f * S,
            H - 52.0f * S - PodH * 0.5f + RigShiftY * 0.55f,
            PodW, PodH, FMath::DegreesToRadians(-12.0f), FMath::DegreesToRadians(-4.0f), BootZSlide, 1400.0f * S
        };
        DrawTiltedGlassPanel(3, PVit, 12.0f * S);

        if (PanelAlphaMul > 0.25f)
        {
            const FVector2f WellQ[4] = { PVit.Map(0.03f, 0.08f), PVit.Map(0.36f, 0.08f), PVit.Map(0.36f, 0.92f), PVit.Map(0.03f, 0.92f) };
            DrawSolidPoly(WellQ, 4, Col8(14, 17, 16, 95));
            DrawPolyline(WellQ, 4, LinCol(244, 243, 235, 45), 1.0f, true);

            const FVector2f ChC = PVit.Map(0.195f, 0.54f);
            const bool bGyroOn = FMath::Abs(St.GyroRadS) > 0.65f;
            const float ChFov = 220.0f * S, ChDist = 4.6f, ChRx = 0.62f;
            auto ProjCh = [&](float X, float Y, float Z) -> FVector2f { return Proj3D(Rot3D(X, Y, Z, ChRx, 0.0f, 0.0f), ChC.X, ChC.Y, ChFov, ChDist); };
            const FLinearColor CompL = bGyroOn ? LinCol(240, 210, 168, 220) : LinCol(244, 243, 235, 68);
            for (int32 I = 0; I < 36; ++I)
            {
                const float A0 = (float(I) / 36.0f) * 2.0f * PI, A1 = (float(I + 1) / 36.0f) * 2.0f * PI;
                DrawLineSeg(ProjCh(FMath::Cos(A0) * 1.35f, 0.35f, FMath::Sin(A0) * 1.35f),
                            ProjCh(FMath::Cos(A1) * 1.35f, 0.35f, FMath::Sin(A1) * 1.35f), CompL, bGyroOn ? 1.6f : 1.0f);
            }
            auto DrawWireBox3D = [&](float BX, float BY, float BZ, float SX, float SY, float SZ, float Yaw, const FLinearColor& EdgeC)
            {
                const float Off[8][3] = {{-SX,-SY,-SZ},{SX,-SY,-SZ},{SX,SY,-SZ},{-SX,SY,-SZ},{-SX,-SY,SZ},{SX,-SY,SZ},{SX,SY,SZ},{-SX,SY,SZ}};
                FVector2f P[8];
                for (int32 K = 0; K < 8; ++K)
                {
                    const FVector3f R1 = Rot3D(Off[K][0] + BX, Off[K][1] + BY, Off[K][2] + BZ, 0.0f, Yaw, 0.0f);
                    P[K] = ProjCh(R1.X, R1.Y, R1.Z);
                }
                const int32 Edges[12][2] = {{0,1},{1,2},{2,3},{3,0},{4,5},{5,6},{6,7},{7,4},{0,4},{1,5},{2,6},{3,7}};
                for (int32 E = 0; E < 12; ++E) DrawLineSeg(P[Edges[E][0]], P[Edges[E][1]], EdgeC, 1.15f);
            };
            const FLinearColor HullL = St.bFriendlyRed ? LinCol(238, 126, 62, 230) : LinCol(94, 188, 200, 230);
            const float WPos[4][3] = {{-0.66f,0.22f,-0.58f},{0.66f,0.22f,-0.58f},{-0.66f,0.22f,0.58f},{0.66f,0.22f,0.58f}};
            for (int32 K = 0; K < 4; ++K) DrawWireBox3D(WPos[K][0], WPos[K][1], WPos[K][2], 0.15f, 0.15f, 0.22f, Owner->ChassisYawRad, LinCol(244, 243, 235, 150));
            DrawWireBox3D(0.0f, 0.10f, 0.0f, 0.54f, 0.17f, 0.68f, Owner->ChassisYawRad, HullL);
            DrawWireBox3D(0.0f, -0.25f, 0.0f, 0.32f, 0.18f, 0.36f, 0.0f, LinCol(244, 243, 235, 235));
            DrawWireBox3D(0.0f, -0.27f, -0.62f, 0.055f, 0.055f, 0.48f, 0.0f, LinCol(240, 210, 168, 245));
            DrawTextEx(bGyroOn ? FString::Printf(TEXT("小陀螺 %.1f rad/s"), FMath::Abs(St.GyroRadS)) : TEXT("小陀螺 关闭"),
                PVit.Map(0.045f, 0.10f), 8, true, bGyroOn ? C_GoldHi : C_Sage);

            DrawTextEx(TEXT("07号 // 哨兵S7"), PVit.Map(0.39f, 0.08f), 8, false, C_Sage);
            DrawTextEx(St.bFriendlyRed ? TEXT("红方 S7") : TEXT("蓝方 S7"), PVit.Map(0.96f, 0.08f), 9, true, St.bFriendlyRed ? C_Verm : C_Teal, 1);
            const float HpPct = FMath::Clamp(float(St.OwnHp) / float(FMath::Max(1, St.OwnMaxHp)), 0.0f, 1.0f);
            const FColor HpCol = HpPct < 0.30f ? C_Crimson : (HpPct < 0.60f ? C_Gold : C_Mint);
            DrawTextEx(FString::FromInt(St.OwnHp), PVit.Map(0.39f, 0.16f), 24, true, C_Ivory);
            DrawTextEx(FString::Printf(TEXT("/ %d 血量"), St.OwnMaxHp), PVit.Map(0.58f, 0.25f), 9, false, C_Sage);
            DrawTextEx(HpPct < 0.30f ? TEXT("危急") : (HpPct < 0.60f ? TEXT("警戒") : TEXT("正常")),
                PVit.Map(0.96f, 0.25f), 8, true, HpCol, 1);

            const FVector2f HpBg[4] = { PVit.Map(0.405f, 0.37f), PVit.Map(0.96f, 0.37f), PVit.Map(0.945f, 0.44f), PVit.Map(0.39f, 0.44f) };
            DrawSolidPoly(HpBg, 4, Col8(244, 243, 235, 30));
            if (Owner->OwnHpGhostPct > HpPct + 0.002f)
            {
                const float UE = 0.39f + 0.555f * Owner->OwnHpGhostPct;
                const FVector2f GQ[4] = { PVit.Map(0.405f, 0.37f), PVit.Map(UE + 0.015f, 0.37f), PVit.Map(UE, 0.44f), PVit.Map(0.39f, 0.44f) };
                DrawSolidPoly(GQ, 4, Col8(244, 243, 235, 195));
            }
            if (HpPct > 0.005f)
            {
                const float UE = 0.39f + 0.555f * HpPct;
                const FVector2f FQ[4] = { PVit.Map(0.405f, 0.37f), PVit.Map(UE + 0.015f, 0.37f), PVit.Map(UE, 0.44f), PVit.Map(0.39f, 0.44f) };
                const FColor FC[4] = { HpCol, C_MintHi, C_MintHi, HpCol };
                DrawPoly(FQ, 4, FC);
            }

            const FVector2f EcgQ[4] = { PVit.Map(0.39f, 0.49f), PVit.Map(0.96f, 0.49f), PVit.Map(0.96f, 0.74f), PVit.Map(0.39f, 0.74f) };
            DrawSolidPoly(EcgQ, 4, Col8(14, 17, 16, 92));
            DrawPolyline(EcgQ, 4, LinCol(244, 243, 235, 38), 1.0f, true);
            const TArray<float>& Ecg = Owner->GetEcgSamples();
            if (Ecg.Num() > 2)
            {
                LineScratch.Reset();
                for (int32 I = 0; I < Ecg.Num(); ++I)
                {
                    const FVector2f Pt = PVit.Map(0.395f + (float(I) / float(Ecg.Num() - 1)) * 0.56f, 0.625f - Ecg[I] * 0.10f);
                    LineScratch.Add(FVector2D(Pt.X, Pt.Y));
                }
                FSlateDrawElement::MakeLines(OutDrawElements, LayerId + 2, AllottedGeometry.ToPaintGeometry(),
                    LineScratch, ESlateDrawEffect::None, EffectiveHit > 0.15f ? LinCol(224, 67, 56, 245) : LinCol(88, 184, 148, 240), true, 1.5f);
            }

            const TCHAR* Mods[7] = { TEXT("裁判"), TEXT("图传"), TEXT("底盘"), TEXT("发射"), TEXT("视觉"), TEXT("通信"), TEXT("电容") };
            for (int32 I = 0; I < 7; ++I)
            {
                const float U0 = 0.39f + I * 0.082f, U1 = U0 + 0.075f;
                const bool bOk = (St.ModuleStatusMask & (1 << I)) != 0;
                const FVector2f MQ[4] = { PVit.Map(U0, 0.79f), PVit.Map(U1, 0.79f), PVit.Map(U1, 0.92f), PVit.Map(U0, 0.92f) };
                DrawSolidPoly(MQ, 4, Col8(244, 243, 235, 24));
                DrawLineSeg(PVit.Map(U0, 0.79f), PVit.Map(U1, 0.79f), bOk ? LinCol(96, 214, 168, 250) : LinCol(245, 82, 72, 250), 2.4f);
                DrawTextEx(Mods[I], PVit.Map((U0 + U1) * 0.5f, 0.815f), 7, true, C_Ivory, 0);
            }
        }

        // =====================================================================
        // 04. BOTTOM-RIGHT HIGH-RES ARCHITECTURAL RMUC2027 3D/2D TACTICAL MINIMAP
        // =====================================================================
        const float MapW = 436.0f * S, MapH = 290.0f * S;
        const FPanel3DTransform PMap = {
            W - 28.0f * S - MapW * 0.5f + RigShiftX * 0.65f + (1.0f - PanelBoot) * 75.0f * S,
            H - 52.0f * S - MapH * 0.5f + RigShiftY * 0.55f,
            MapW, MapH, FMath::DegreesToRadians(11.0f), FMath::DegreesToRadians(-4.0f), BootZSlide, 1400.0f * S
        };
        DrawTiltedGlassPanel(4, PMap, 12.0f * S);

        if (PanelAlphaMul > 0.25f)
        {
            DrawTextEx(TEXT("RMUC 2027 // 战术全息沙盘"), PMap.Map(0.032f, 0.028f), 8, false, C_Sage);
            DrawTextEx(Owner->IsMinimap3D() ? TEXT("[M] 3D 全息") : TEXT("[M] 2D 平面"), PMap.Map(0.968f, 0.028f), 8, true, C_GoldHi, 1);

            const FVector2f MWell[4] = { PMap.Map(0.032f, 0.105f), PMap.Map(0.968f, 0.105f), PMap.Map(0.968f, 0.905f), PMap.Map(0.032f, 0.905f) };
            DrawSolidPoly(MWell, 4, Col8(10, 14, 16, 175));
            DrawPolyline(MWell, 4, LinCol(244, 243, 235, 56), 1.0f, true);

            // Project the REAL high-resolution architectural RMUC2027 blueprint onto a 14x8 subdivided 3D floor mesh!
            // Pull MDist back in 3D tilted mode so the closer bottom corners stay 100% inside the glass well frame.
            const float Tilt = Owner->MapTiltRad;
            const float TiltNorm = FMath::Clamp(Tilt / 0.56f, 0.0f, 1.0f);
            const FVector2f MC = PMap.Map(0.5f, 0.505f - 0.028f * TiltNorm);
            const float MYaw = FMath::Sin(T * 0.30f) * 0.030f * TiltNorm;
            const float MFov = 342.0f * S;
            const float MDist = 26.4f + TiltNorm * 6.8f;

            auto ProjMap = [&](float AX, float AY, float AZ) -> FVector2f
            {
                const float Rx = -Tilt * 1.26f;
                const FVector3f R = Rot3D(AX, AY, -AZ, Rx, 0.0f, MYaw);
                return Proj3D(R, MC.X, MC.Y, MFov, MDist);
            };

            // 4A. High-Resolution Architectural RMUC2027 Texture (TexMinimap = 1120x640 real blueprint!)
            if (St.MinimapTexture && IsValid(St.MinimapTexture))
            {
                if (MinimapBrush.GetResourceObject() != St.MinimapTexture)
                {
                    MinimapBrush.SetResourceObject(St.MinimapTexture);
                    MinimapBrush.ImageSize = FVector2D(St.MinimapTexture->GetSizeX(), St.MinimapTexture->GetSizeY());
                }
                const FSlateResourceHandle MapHandle = FSlateApplication::Get().GetRenderer()->GetResourceHandle(MinimapBrush);
                // Exact texture padding matching generate_rmuc2026_ui.py (pad_x=24/1120, pad_y=20/640)
                const float PadU = 24.0f / 1120.0f;
                const float PadV = 20.0f / 640.0f;
                constexpr int32 SubX = 14, SubY = 8;
                VertScratch.Reset();
                IdxScratch.Reset();
                const FColor MapTint = Col8(242, 246, 248, 242);
                for (int32 Iy = 0; Iy <= SubY; ++Iy)
                {
                    const float Fy = float(Iy) / float(SubY);
                    const float AY = (Fy - 0.5f) * 16.0f;
                    const float V  = FMath::Lerp(PadV, 1.0f - PadV, Fy);
                    for (int32 Ix = 0; Ix <= SubX; ++Ix)
                    {
                        const float Fx = float(Ix) / float(SubX);
                        const float AX = (Fx - 0.5f) * 28.0f;
                        const float U  = FMath::Lerp(PadU, 1.0f - PadU, Fx);
                        const FVector2f Pos = ProjMap(AX, AY, 0.0f);
                        VertScratch.Add(FSlateVertex::Make<ESlateVertexRounding::Disabled>(RT, Pos, FVector2f(U, V), MapTint));
                    }
                }
                for (int32 Iy = 0; Iy < SubY; ++Iy)
                {
                    for (int32 Ix = 0; Ix < SubX; ++Ix)
                    {
                        const SlateIndex R0 = static_cast<SlateIndex>(Iy * (SubX + 1) + Ix);
                        const SlateIndex R1 = static_cast<SlateIndex>((Iy + 1) * (SubX + 1) + Ix);
                        IdxScratch.Add(R0); IdxScratch.Add(R0 + 1); IdxScratch.Add(R1 + 1);
                        IdxScratch.Add(R0); IdxScratch.Add(R1 + 1); IdxScratch.Add(R1);
                    }
                }
                FSlateDrawElement::MakeCustomVerts(OutDrawElements, LayerId, MapHandle, VertScratch, IdxScratch, nullptr, 0, 0);
            }

            // Crisp Arena Perimeter Frame
            const FVector2f FloorC[4] = { ProjMap(-14, -8, 0), ProjMap(14, -8, 0), ProjMap(14, 8, 0), ProjMap(-14, 8, 0) };
            DrawPolyline(FloorC, 4, LinCol(244, 243, 235, 130), 1.2f, true);

            // 4B. Subtle 3D Elevation Wireframe Stems ONLY in 3D Holo Mode (preserves crisp blueprint detail!)
            if (Tilt > 0.08f)
            {
                const TArray<FRhine3DMapCell>& Cells = Owner->GetArenaCells();
                const float HFactor = 0.72f * (Tilt / 0.56f);
                for (int32 I = 0; I < Cells.Num(); I += 2)
                {
                    const FRhine3DMapCell& C = Cells[I];
                    const float H3D = C.HeightM * HFactor;
                    if (H3D > 0.32f)
                    {
                        DrawLineSeg(ProjMap(C.ArenaX, C.ArenaY, 0.0f), ProjMap(C.ArenaX, C.ArenaY, H3D),
                            LinCol(244, 243, 235, 32), 0.75f);
                    }
                }
            }

            // 4C. Live Outpost Gain-Zone Color Flip + Compact CAD Tower Ring on the Minimap!
            auto DrawMapOutpost = [&](const FRhine3DOutpostState& Post, bool bSiteRed)
            {
                const float OX = Post.bHasMapPos ? Post.ArenaX : (bSiteRed ? -3.06f : 3.06f);
                const float OY = Post.bHasMapPos ? Post.ArenaY : (bSiteRed ? -3.89f : 3.89f);
                const FVector2f OP = ProjMap(OX, OY, 0.08f);
                const bool bCaptured = (Post.bControllerRed != bSiteRed) || (Post.bOwnerRed != bSiteRed);
                const bool bTransitioning = Post.TransitionPct > 0.01f || Post.PhaseLabel == TEXT("CONTESTED");
                const bool bDestroyed = (Post.Hp <= 0);
                const FColor OuterC = !bDestroyed ? (Post.bOwnerRed ? C_Verm : C_Teal) : C_NeutralGry;
                const FColor CtrlC  = Post.bControllerRed ? C_Verm : C_Teal;

                const FColor ContourColor = Post.PhaseLabel == TEXT("CONTESTED") ? C_GoldHi :
                    (bTransitioning ? (Post.bControllerRed ? C_Teal : C_Verm) : CtrlC);
                const auto DrawZoneOutline = [&](const TArray<FVector2f>& Points, float Height)
                {
                    if (Points.Num() < 3) return;
                    TArray<FVector2f, TInlineAllocator<16>> Projected;
                    for (const auto& Point : Points)
                    {
                        Projected.Add(ProjMap(Point.X, Point.Y, Height));
                    }
                    const bool bEmphasis = bCaptured || bTransitioning || bDestroyed;
                    DrawPolyline(Projected.GetData(), Projected.Num(),
                        LinCol(ContourColor.R, ContourColor.G, ContourColor.B, bEmphasis ? 235 : 145),
                        bEmphasis ? 1.6f : 1.0f, true);
                };
                DrawZoneOutline(Post.ZonePolyArena, 0.04f);
                DrawZoneOutline(Post.HighlandPolyArena, 0.04f);

                // Compact hollow/dual-ring tower badge centered directly on the CAD outpost tower
                if (bCaptured || bTransitioning)
                {
                    const float PulseR = (6.5f + 4.0f * FMath::Abs(FMath::Sin(T * 4.2f))) * S;
                    DrawArcStrip(OP, PulseR - 1.3f * S, PulseR, 0.0f, 360.0f, C_GoldHi, C_GoldHi, 20);
                }
                DrawArcStrip(OP, 3.2f * S, 5.2f * S, 0.0f, 360.0f, OuterC, OuterC, 18);
                DrawArcStrip(OP, 0.0f, 2.6f * S, 0.0f, 360.0f, CtrlC, CtrlC, 16);
                DrawArcStrip(OP, 5.2f * S, 6.0f * S, 0.0f, 360.0f, Col8(244, 243, 235, 210), Col8(244, 243, 235, 210), 18);
            };
            DrawMapOutpost(EffRedPost, true);
            DrawMapOutpost(EffBluePost, false);

            // 4D. Sweeping Champagne-Gold Radar Scan Beam
            const float Ra = Owner->RadarAngleRad;
            const FVector2f MCent = ProjMap(0, 0, 0.06f);
            const FVector2f B1 = ProjMap(FMath::Cos(Ra) * 14.0f, FMath::Sin(Ra) * 8.0f, 0.06f);
            const FVector2f B2 = ProjMap(FMath::Cos(Ra - 0.34f) * 14.0f, FMath::Sin(Ra - 0.34f) * 8.0f, 0.06f);
            const FVector2f BeamTri[3] = { MCent, B1, B2 };
            const FColor BeamCol[3] = { Col8(240, 210, 168, 56), Col8(240, 210, 168, 72), Col8(240, 210, 168, 0) };
            DrawPoly(BeamTri, 3, BeamCol);
            DrawLineSeg(MCent, B1, LinCol(240, 210, 168, 210), 1.3f);

            // 4E. Live Robot Unit Pins + Clamped Player FOV Cone + Radar Ping Ripples
            const TArray<float>& Pulses = Owner->GetPinPulses();
            for (int32 I = 0; I < St.MapPins.Num(); ++I)
            {
                const FRhine3DMapPin& Pin = St.MapPins[I];
                const FVector2f P0 = ProjMap(Pin.ArenaX, Pin.ArenaY, 0.06f);
                const FVector2f P1 = ProjMap(Pin.ArenaX, Pin.ArenaY, 0.20f + (Tilt / 0.56f) * 0.85f);
                const FColor PCol = Pin.bIsRed ? C_Verm : C_Teal;

                if (Pin.bIsSelf)
                {
                    auto ClampRay = [&](float Ang) -> FVector2f
                    {
                        const float DX = FMath::Cos(Ang), DY = FMath::Sin(Ang);
                        float MaxLen = 4.5f;
                        if (DX > 1e-4f)  MaxLen = FMath::Min(MaxLen, (13.85f - Pin.ArenaX) / DX);
                        if (DX < -1e-4f) MaxLen = FMath::Min(MaxLen, (-13.85f - Pin.ArenaX) / DX);
                        if (DY > 1e-4f)  MaxLen = FMath::Min(MaxLen, (7.85f - Pin.ArenaY) / DY);
                        if (DY < -1e-4f) MaxLen = FMath::Min(MaxLen, (-7.85f - Pin.ArenaY) / DY);
                        MaxLen = FMath::Max(0.2f, MaxLen);
                        return ProjMap(Pin.ArenaX + DX * MaxLen, Pin.ArenaY + DY * MaxLen, 0.08f);
                    };
                    const FVector2f F1 = ClampRay(Pin.HeadingRad - 0.38f);
                    const FVector2f F2 = ClampRay(Pin.HeadingRad + 0.38f);
                    const FVector2f FovTri[3] = { P0, F1, F2 };
                    DrawSolidPoly(FovTri, 3, Col8(244, 243, 235, 58));
                }
                if (I < Pulses.Num() && Pulses[I] > 0.03f)
                {
                    const float Pulse = Pulses[I];
                    const float RipR = (5.0f + (1.0f - Pulse) * 16.0f) * S;
                    DrawArcStrip(P0, RipR - 1.3f * S, RipR, 0.0f, 360.0f,
                        Col8(240, 210, 168, static_cast<uint8>(Pulse * 215)),
                        Col8(240, 210, 168, static_cast<uint8>(Pulse * 215)), 18);
                }
                if (Tilt > 0.08f) DrawLineSeg(P0, P1, FLinearColor::FromSRGBColor(PCol), 1.3f);
                const float PinR = (Pin.bIsSelf ? 8.2f : 6.8f) * S;
                DrawArcStrip(P1, 0.0f, PinR + 1.5f * S, 0.0f, 360.0f, Col8(14, 17, 16, 220), Col8(14, 17, 16, 220), 16);
                DrawArcStrip(P1, 0.0f, PinR, 0.0f, 360.0f, PCol, PCol, 16);
                if (Pin.bIsSelf) DrawArcStrip(P1, PinR + 0.5f * S, PinR + 2.0f * S, 0.0f, 360.0f, C_GoldHi, C_GoldHi, 16);
                DrawTextEx(Pin.Label, FVector2f(P1.X, P1.Y - 6.0f * S), 8, true, C_Ivory, 0);
            }

            DrawTextEx(TEXT("RMUC 2027 真实场地  28m x 16m"), PMap.Map(0.032f, 0.922f), 7, false, C_Sage);
            DrawTextEx(FString::Printf(TEXT("扫描 %03d°"), FMath::RoundToInt(FMath::RadiansToDegrees(Ra))),
                PMap.Map(0.968f, 0.922f), 8, false, C_GoldHi, 1);
        }

        // =====================================================================
        // 05. EVENT TOAST, FACTION+VEHICLE COMBAT BROADCAST FEED & BUFF CHIPS
        // =====================================================================
        auto FitText = [&](FString Txt, float MaxPx, int32 Size, bool bBold) -> FString
        {
            if (MaxPx <= 12.0f * S || Txt.IsEmpty()) return FString();
            if (MeasureTextW(Txt, Size, bBold) <= MaxPx) return Txt;
            while (Txt.Len() > 1 && MeasureTextW(Txt + TEXT("…"), Size, bBold) > MaxPx)
            {
                Txt.LeftChopInline(1, EAllowShrinking::No);
            }
            return Txt + TEXT("…");
        };

        const bool bShowToast = (St.BannerEventTimer > 0.0f && !St.BannerEventText.IsEmpty()) || (Owner->SimToastTimer > 0.0f);
        if (bShowToast && PanelAlphaMul > 0.3f)
        {
            const float Wipe = EaseRhineOut(Owner->ToastWipeProgress);
            const float FullTW = 500.0f * S;
            const float TW = FullTW * FMath::Max(0.05f, Wipe);
            const float TH = 56.0f * S;
            const FPanel3DTransform PToast = { CX - (FullTW - TW) * 0.5f, 146.0f * S + TH * 0.5f, TW, TH, 0.0f, 0.0f, 0.0f, 1400.0f * S };
            DrawTiltedGlassPanel(6, PToast, 8.0f * S);
            DrawLineSeg(PToast.Map(1.0f, 0.0f), PToast.Map(1.0f, 1.0f), LinCol(240, 210, 168, 250), 2.5f * S);
            if (Wipe > 0.55f)
            {
                const FString& RawMsg = Owner->SimToastTimer > 0.0f ? Owner->SimToastText : St.BannerEventText;
                const FString Msg = FitText(RawMsg, FullTW - 36.0f * S, 12, true);
                DrawTextEx(TEXT("战术事件 // 优先播报"), FVector2f(CX, 152.0f * S), 8, true, C_GoldHi, 0);
                DrawTextEx(Msg, FVector2f(CX, 168.0f * S), 12, true, C_Ivory, 0);
            }
        }

        // Top-Right Structured Faction + Vehicle Combat Broadcast Feed (strictly clipped inside frame!)
        const int32 FeedCount = FMath::Min(5, St.BroadcastFeed.Num());
        if (FeedCount > 0 && PanelAlphaMul > 0.25f)
        {
            const float FW = 436.0f * S, RowH = 32.0f * S;
            const float TotalFH = FeedCount * RowH + 10.0f * S;
            const FPanel3DTransform PFeed = { W - 28.0f * S - FW * 0.5f + RigShiftX * 0.5f, 144.0f * S + TotalFH * 0.5f,
                FW, TotalFH, FMath::DegreesToRadians(9.0f), 0.0f, BootZSlide * 0.8f, 1400.0f * S };
            DrawTiltedGlassPanel(8, PFeed, 8.0f * S, 0.92f);

            for (int32 I = 0; I < FeedCount; ++I)
            {
                const FRhine3DBroadcastItem& Ev = St.BroadcastFeed[I];
                const float V0 = (5.0f * S + I * RowH) / TotalFH;
                const float V1 = (5.0f * S + (I + 1) * RowH - 3.0f * S) / TotalFH;

                const FColor ActorCol = TeamColor8(Ev.ActorTeam, 250);
                const FColor TargetCol = Ev.TargetTeam != 0 ? TeamColor8(Ev.TargetTeam, 248) : C_IvorySoft;

                DrawPanelQuad(PFeed, 0.015f, V0, 0.985f, V1, Col8(14, 18, 20, 72), Col8(14, 18, 20, 42));
                DrawPanelQuad(PFeed, 0.015f, V0, 0.028f, V1, ActorCol, ActorCol);

                const FVector2f RowL = PFeed.Map(0.042f, V0 + 0.03f / FMath::Max(1.0f, float(FeedCount)));
                const FVector2f TimeR = PFeed.Map(0.968f, V0 + 0.03f / FMath::Max(1.0f, float(FeedCount)));
                const float TimeW = Ev.TimeStr.IsEmpty() ? 0.0f : (MeasureTextW(Ev.TimeStr, 7, false) + 18.0f * S);
                const float RightLimitX = TimeR.X - TimeW;

                float CurX = RowL.X;
                const FString ActorTag = FitText(FString::Printf(TEXT("[%s]"), *Ev.ActorVehicle), RightLimitX - CurX, 8, true);
                if (!ActorTag.IsEmpty())
                {
                    DrawTextEx(ActorTag, FVector2f(CurX, RowL.Y), 8, true, ActorCol);
                    CurX += MeasureTextW(ActorTag, 8, true) + 3.0f * S;
                }

                const FString ActStr = FitText(FString::Printf(TEXT("> %s"), *Ev.ActionText), RightLimitX - CurX, 8, true);
                if (!ActStr.IsEmpty())
                {
                    DrawTextEx(ActStr, FVector2f(CurX, RowL.Y), 8, true, C_GoldHi);
                    CurX += MeasureTextW(ActStr, 8, true) + 4.0f * S;
                }

                if (!Ev.TargetName.IsEmpty() && RightLimitX - CurX > 16.0f * S)
                {
                    const FString TgtStr = FitText(Ev.TargetName, RightLimitX - CurX, 8, true);
                    if (!TgtStr.IsEmpty())
                    {
                        DrawTextEx(TgtStr, FVector2f(CurX, RowL.Y), 8, true, TargetCol);
                    }
                }

                if (!Ev.TimeStr.IsEmpty())
                {
                    DrawTextEx(Ev.TimeStr, TimeR, 7, false, C_Sage, 1);
                }
            }
        }

        // Bottom-Center 4 Standard Buff Chips
        const TCHAR* BuffCodes[4] = { TEXT("回血"), TEXT("防御"), TEXT("冷却"), TEXT("机关") };
        const TCHAR* BuffSubs[4]  = { TEXT("+10%/s"), TEXT("-50% 减伤"), TEXT("冷却加速"), TEXT("能量增益") };
        const bool BuffOn[4] = {
            St.bBuffHeal || Owner->bSimBuffBoost, St.bBuffArmor || Owner->bSimBuffBoost,
            St.bBuffCooling, St.bBuffPower || Owner->bSimBuffBoost
        };
        const float BChipW = 90.0f * S, BChipH = 44.0f * S, BGap = 8.0f * S;
        const float RackW = 4 * BChipW + 3 * BGap;
        if (BlurBounds && PanelAlphaMul > 0.25f)
        {
            (*BlurBounds)[7].MinPt = FVector2f(CX - RackW * 0.5f + RigShiftX * 0.4f, H - 54.0f * S - BChipH - 6.0f * S);
            (*BlurBounds)[7].MaxPt = FVector2f(CX + RackW * 0.5f + RigShiftX * 0.4f, H - 54.0f * S);
            (*BlurBounds)[7].bVisible = true;
        }
        for (int32 I = 0; I < 4; ++I)
        {
            const float BX = CX - RackW * 0.5f + I * (BChipW + BGap) + BChipW * 0.5f + RigShiftX * 0.4f;
            const float BY = H - 54.0f * S - BChipH * 0.5f - (BuffOn[I] ? 7.0f * S : 0.0f) + RigShiftY * 0.35f;
            const FPanel3DTransform PBuf = { BX, BY, BChipW, BChipH, 0.0f, FMath::DegreesToRadians(-6.0f), BootZSlide * 0.6f, 1400.0f * S };
            DrawTiltedGlassPanel(-1, PBuf, 7.0f * S, BuffOn[I] ? 1.0f : 0.52f);
            if (BuffOn[I] && PanelAlphaMul > 0.25f)
            {
                DrawLineSeg(PBuf.Map(0.0f, 0.0f), PBuf.Map(0.92f, 0.0f), LinCol(240, 210, 168, 245), 2.0f);
                const float Sw = FMath::Fmod(T * 0.55f + I * 0.18f, 1.4f) - 0.2f;
                if (Sw > 0.0f && Sw < 1.0f)
                {
                    const FVector2f Sh[4] = {
                        PBuf.Map(FMath::Clamp(Sw - 0.14f, 0.0f, 1.0f), 0.0f), PBuf.Map(FMath::Clamp(Sw + 0.04f, 0.0f, 1.0f), 0.0f),
                        PBuf.Map(FMath::Clamp(Sw - 0.06f, 0.0f, 1.0f), 1.0f), PBuf.Map(FMath::Clamp(Sw - 0.24f, 0.0f, 1.0f), 1.0f)
                    };
                    DrawSolidPoly(Sh, 4, Col8(240, 210, 168, 52));
                }
            }
            if (PanelAlphaMul > 0.25f)
            {
                DrawTextEx(BuffCodes[I], PBuf.Map(0.5f, 0.16f), 10, true, BuffOn[I] ? C_GoldHi : C_Sage, 0);
                DrawTextEx(BuffSubs[I],  PBuf.Map(0.5f, 0.54f), 8, false, BuffOn[I] ? C_IvorySoft : C_SageDark, 0);
            }
        }

        // Live Gain-Zone Status Telemetry Pill directly above the 4 Buff Chips
        if (!St.ZoneStatusText.IsEmpty() && PanelAlphaMul > 0.25f)
        {
            const float ZW = 384.0f * S, ZH = 24.0f * S;
            const FPanel3DTransform PZone = {
                CX + RigShiftX * 0.4f, H - 54.0f * S - BChipH - 22.0f * S + RigShiftY * 0.35f,
                ZW, ZH, 0.0f, FMath::DegreesToRadians(-5.0f), BootZSlide * 0.5f, 1400.0f * S
            };
            DrawTiltedGlassPanel(-1, PZone, 5.0f * S, 0.90f);
            const FLinearColor Accent = St.bZoneStatusLocked ? LinCol(238, 126, 62, 235) : LinCol(88, 184, 148, 240);
            DrawLineSeg(PZone.Map(0.0f, 0.0f), PZone.Map(0.0f, 1.0f), Accent, 2.5f * S);
            DrawLineSeg(PZone.Map(0.0f, 0.0f), PZone.Map(0.95f, 0.0f), Accent, 1.2f);
            DrawTextEx(St.ZoneStatusText, PZone.Map(0.5f, 0.24f), 8, true,
                St.bZoneStatusLocked ? C_GoldHi : C_Mint, 0);
        }

        // RMUC 2027 V1.0.0 Penalty & Respawn Overlays (FPV Video Cut / Overpower 5s / Overspeed / Dynamic Respawn)
        if (PanelAlphaMul > 0.25f)
        {
            // 1) Overheat FPV Video Feed Cut (§5.1.3)
            const FColor C_AlertRed = Col8(245, 82, 72, 255);
            if (St.bFpvVideoCut)
            {
                const FVector2f FullQ[4] = { FVector2f(0.0f, 0.0f), FVector2f(W, 0.0f), FVector2f(W, H), FVector2f(0.0f, H) };
                DrawSolidPoly(FullQ, 4, Col8(10, 13, 14, 224));
                const float ScanOffset = FMath::Fmod(T * 120.0f, 16.0f * S);
                for (float SY = ScanOffset; SY < H; SY += 16.0f * S)
                {
                    DrawLineSeg(FVector2f(0.0f, SY), FVector2f(W, SY), LinCol(245, 82, 72, 28), 1.0f);
                }
                const FPanel3DTransform PCut = { CX, CY - 10.0f * S, 480.0f * S, 76.0f * S, 0.0f, 0.0f, 0.0f, 1400.0f * S };
                DrawTiltedGlassPanel(-1, PCut, 8.0f * S, 1.0f);
                DrawLineSeg(PCut.Map(0.0f, 0.0f), PCut.Map(1.0f, 0.0f), LinCol(245, 82, 72, 250), 2.4f * S);
                DrawTextEx(TEXT("第一视角图传已断开 // 枪口热量超限"), PCut.Map(0.5f, 0.22f), 11, true, C_AlertRed, 0);
                DrawTextEx(St.bOverheatMatchLocked
                    ? TEXT("严重超热 (+100) — 本局永久锁定发射机构")
                    : FString::Printf(TEXT("发射机构已锁定 — 冷却至 0 后自动恢复 (当前 %.0f%%)"), St.BarrelHeatPct * 100.0f),
                    PCut.Map(0.5f, 0.58f), 9, false, C_Ivory, 0);
            }

            // 2) Overpower 5s Chassis Cut (§5.1.4) or Overspeed Lock (Table 5-3)
            float AlertY = H - 54.0f * S - BChipH - 54.0f * S;
            if (St.ChassisPowerCutSeconds > 0.01f)
            {
                const FPanel3DTransform PPow = { CX + RigShiftX * 0.4f, AlertY, 390.0f * S, 26.0f * S, 0.0f, FMath::DegreesToRadians(-5.0f), 0.0f, 1400.0f * S };
                DrawTiltedGlassPanel(-1, PPow, 5.0f * S, 0.98f);
                DrawLineSeg(PPow.Map(0.0f, 0.0f), PPow.Map(0.0f, 1.0f), LinCol(245, 82, 72, 250), 3.0f * S);
                DrawTextEx(FString::Printf(TEXT("底盘已断电 // 超功率处罚  %.1fs"), St.ChassisPowerCutSeconds),
                    PPow.Map(0.5f, 0.24f), 9, true, C_AlertRed, 0);
                AlertY -= 30.0f * S;
            }
            if (St.bOverspeedMatchLocked || St.OverspeedLockSeconds > 0.01f)
            {
                const FPanel3DTransform PSpd = { CX + RigShiftX * 0.4f, AlertY, 390.0f * S, 26.0f * S, 0.0f, FMath::DegreesToRadians(-5.0f), 0.0f, 1400.0f * S };
                DrawTiltedGlassPanel(-1, PSpd, 5.0f * S, 0.98f);
                DrawLineSeg(PSpd.Map(0.0f, 0.0f), PSpd.Map(0.0f, 1.0f), LinCol(252, 128, 72, 250), 3.0f * S);
                DrawTextEx(St.bOverspeedMatchLocked
                    ? TEXT("超速处罚 // 本局锁定发射机构")
                    : FString::Printf(TEXT("超速处罚 // 发射机构锁定  %.1fs"), St.OverspeedLockSeconds),
                    PSpd.Map(0.5f, 0.24f), 9, true, C_Verm, 0);
                AlertY -= 30.0f * S;
            }
            if (St.bOwnAlive && (St.InvulnerableRemainSeconds > 0.01f || St.WeakRemainSeconds > 0.01f))
            {
                const FPanel3DTransform PInv = { CX + RigShiftX * 0.4f, AlertY, 390.0f * S, 24.0f * S, 0.0f, FMath::DegreesToRadians(-5.0f), 0.0f, 1400.0f * S };
                DrawTiltedGlassPanel(-1, PInv, 5.0f * S, 0.95f);
                FString StatusTag;
                if (St.InvulnerableRemainSeconds > 0.01f) StatusTag += FString::Printf(TEXT("临时无敌 %.1fs"), St.InvulnerableRemainSeconds);
                if (St.WeakRemainSeconds > 0.01f) StatusTag += (StatusTag.IsEmpty() ? TEXT("") : TEXT("  //  ")) + FString::Printf(TEXT("虚弱禁射 %.1fs"), St.WeakRemainSeconds);
                DrawTextEx(StatusTag, PInv.Map(0.5f, 0.24f), 8, true, C_GoldHi, 0);
            }

            // 3) Death & Dynamic Respawn / Instant Gold Buyback (§5.3.2)
            if (!St.bOwnAlive)
            {
                const FPanel3DTransform PRes = { CX, CY + 96.0f * S, 440.0f * S, 76.0f * S, 0.0f, FMath::DegreesToRadians(-4.0f), 0.0f, 1400.0f * S };
                DrawTiltedGlassPanel(-1, PRes, 8.0f * S, 0.98f);
                DrawLineSeg(PRes.Map(0.0f, 0.0f), PRes.Map(1.0f, 0.0f), LinCol(245, 82, 72, 245), 2.0f * S);
                DrawTextEx(FString::Printf(TEXT("机体已战亡  //  复活读条 %.1fs (恢复 10%% 血量)"), St.RespawnRemainSeconds),
                    PRes.Map(0.5f, 0.16f), 10, true, C_AlertRed, 0);
                DrawPanelQuad(PRes, 0.06f, 0.46f, 0.94f, 0.58f, Col8(244, 243, 235, 36), Col8(244, 243, 235, 36));
                if (St.RespawnProgress > 0.01f)
                {
                    DrawPanelQuad(PRes, 0.06f, 0.46f, 0.06f + 0.88f * St.RespawnProgress, 0.58f, C_Gold, C_GoldHi);
                }
                const bool bCanBuy = (St.RemainingGold >= St.BuybackGoldCost);
                DrawTextEx(FString::Printf(TEXT("按 [R] 立即买活 (消耗 %d 金币 -> 满血)   |   当前金币: %d"), St.BuybackGoldCost, St.RemainingGold),
                    PRes.Map(0.5f, 0.68f), 8, true, bCanBuy ? C_MintHi : C_Sage, 0);
            }
        }

        // =====================================================================
        // 06. BOTTOM BRAND STRIP & 1.6s OPTICAL BOOT SCAN CHOREOGRAPHY
        // =====================================================================
        const float FootY = H - 30.0f * S;
        DrawLineSeg(FVector2f(28 * S, FootY - 4 * S), FVector2f(W - 28 * S, FootY - 4 * S), LinCol(244, 243, 235, 88), 1.0f);
        DrawTextEx(TEXT("POWERED BY RHINE LAB"), FVector2f(36 * S, FootY), 9, true, Col8(252, 252, 248, 235));
        const FVector2f BarQ[4] = {
            FVector2f(206 * S, FootY + 4 * S), FVector2f(228 * S, FootY + 4 * S),
            FVector2f(228 * S, FootY + 9 * S), FVector2f(206 * S, FootY + 9 * S)
        };
        DrawSolidPoly(BarQ, 4, C_Ivory);
        if (FMath::Fmod(T, 1.0f) < 0.55f) DrawTextEx(TEXT("|"), FVector2f(235 * S, FootY - 1 * S), 10, true, C_GoldHi);

        DrawTextEx(FString::Printf(TEXT("帧率 %d  //  偏航 %+.1f°  //  俯仰 %+.1f°  //  [F10] 控制台  //  [F11] 切换UI"),
            FMath::RoundToInt(Owner->FpsSmooth),
            FMath::RadiansToDegrees(St.GimbalYawRad),
            FMath::RadiansToDegrees(St.GimbalPitchRad)),
            FVector2f(W - 36 * S, FootY), 9, false, C_Sage, 1);

        if (Owner->bBootActive && BootT < 1.35f)
        {
            if (BootT < 0.80f)
            {
                const float ScanY = (BootT / 0.80f) * H;
                DrawLineSeg(FVector2f(0.0f, ScanY), FVector2f(W, ScanY), LinCol(240, 210, 168, 240), 2.5f);
                const FVector2f GlowQ[4] = {
                    FVector2f(0.0f, FMath::Max(0.0f, ScanY - 38 * S)), FVector2f(W, FMath::Max(0.0f, ScanY - 38 * S)),
                    FVector2f(W, ScanY), FVector2f(0.0f, ScanY)
                };
                const FColor GlowC[4] = { Col8(240, 210, 168, 0), Col8(240, 210, 168, 0), Col8(240, 210, 168, 52), Col8(240, 210, 168, 52) };
                DrawPoly(GlowQ, 4, GlowC);
            }
            const float CardA = BootT < 0.95f ? 1.0f : FMath::Clamp(1.0f - (BootT - 0.95f) / 0.40f, 0.0f, 1.0f);
            const FPanel3DTransform PBoot = { CX, CY, 440 * S, 44 * S, 0.0f, 0.0f, 0.0f, 1400.0f * S };
            DrawTiltedGlassPanel(9, PBoot, 6.0f * S, CardA);
            DrawTextEx(BootT < 0.78f ? TEXT("RHINE LAB // 光学 3D 战术界面初始化...") : TEXT("空间校准完成 // 战术链路已锁定"),
                FVector2f(CX, CY - 7 * S), 11, true, Col8(252, 252, 248, static_cast<uint8>(252 * CardA)), 0);
        }

        return LayerId + 3;
    }

private:
    FRhine3DHudController* Owner = nullptr;
    TArray<Rhine3DInternal::FPanel3DBounds>* BlurBounds = nullptr;
    mutable TArray<FSlateVertex> VertScratch;
    mutable TArray<SlateIndex> IdxScratch;
    mutable TArray<FVector2D> LineScratch;
    mutable FSlateBrush MinimapBrush;
};

// =============================================================================
// ROOT VIEWPORT WIDGET: GPU SBackgroundBlur + 3D CANVAS + COLLAPSIBLE TEST CONSOLE
// =============================================================================
class SRhine3DHudViewport : public SCompoundWidget
{
public:
    SLATE_BEGIN_ARGS(SRhine3DHudViewport) {}
    SLATE_END_ARGS()

    void Construct(const FArguments& InArgs, FRhine3DHudController* InOwner)
    {
        Owner = InOwner;
        BlurBounds.SetNum(Rhine3DInternal::NumBlurSlots);

        TSharedRef<SConstraintCanvas> BlurCanvas = SNew(SConstraintCanvas);
        BlurCanvas->SetVisibility(EVisibility::HitTestInvisible);

        BlurSlots.Reserve(Rhine3DInternal::NumBlurSlots);
        BlurWidgets.Reserve(Rhine3DInternal::NumBlurSlots);
        for (int32 I = 0; I < Rhine3DInternal::NumBlurSlots; ++I)
        {
            TSharedRef<SBackgroundBlur> Blur = SNew(SBackgroundBlur)
                .BlurStrength(14.0f)
                .BlurRadius(16)
                .bApplyAlphaToBlur(true)
                .Visibility(EVisibility::Collapsed);
            SConstraintCanvas::FSlot* SlotPtr = nullptr;
            BlurCanvas->AddSlot()
                .Anchors(FAnchors(0.0f, 0.0f, 0.0f, 0.0f))
                .Alignment(FVector2D(0.0f, 0.0f))
                .Offset(FMargin(0, 0, 0, 0))
                .Expose(SlotPtr)
                [ Blur ];
            BlurSlots.Add(SlotPtr);
            BlurWidgets.Add(Blur);
        }

        auto MakeConsoleBtn = [&](const FText& Label, TFunction<void()> OnClick) -> TSharedRef<SWidget>
        {
            return SNew(SButton)
                .ButtonColorAndOpacity(FLinearColor(0.08f, 0.11f, 0.12f, 0.88f))
                .ContentPadding(FMargin(8.0f, 4.0f))
                .OnClicked_Lambda([OnClick]() { OnClick(); return FReply::Handled(); })
                [
                    SNew(STextBlock)
                    .Text(Label)
                    .Font(Rhine3DInternal::MakeRhineFont(9, false))
                    .ColorAndOpacity(FLinearColor(0.98f, 0.98f, 0.96f, 1.0f))
                ];
        };

        ChildSlot
        [
            SNew(SOverlay)
            + SOverlay::Slot() [ BlurCanvas ]
            + SOverlay::Slot() [ SNew(SRhine3DHudCanvasLayer, Owner, &BlurBounds) ]
            + SOverlay::Slot()
            .HAlign(HAlign_Left)
            .VAlign(VAlign_Top)
            .Padding(FMargin(28.0f, 144.0f, 0.0f, 0.0f))
            [
                SNew(SBox)
                .WidthOverride(244.0f)
                [
                    SNew(SBorder)
                    .BorderImage(FCoreStyle::Get().GetBrush("WhiteBrush"))
                    .BorderBackgroundColor(FLinearColor(0.04f, 0.055f, 0.06f, 0.84f))
                    .Padding(FMargin(8.0f, 6.0f))
                    [
                        SNew(SVerticalBox)
                        + SVerticalBox::Slot().AutoHeight()
                        [
                            SNew(SButton)
                            .ButtonColorAndOpacity(FLinearColor(0.10f, 0.13f, 0.14f, 0.85f))
                            .ContentPadding(FMargin(6.0f, 3.0f))
                            .OnClicked_Lambda([this]() { if (Owner) Owner->ToggleTestConsoleExpanded(); return FReply::Handled(); })
                            [
                                SNew(STextBlock)
                                .Text_Lambda([this]() {
                                    return (Owner && Owner->IsTestConsoleExpanded())
                                        ? FText::FromString(TEXT("// 实战测试控制台 [F10]  [ - ]"))
                                        : FText::FromString(TEXT("// 实战测试控制台 [F10]  [ + ]"));
                                })
                                .Font(Rhine3DInternal::MakeRhineFont(9, true))
                                .ColorAndOpacity(FLinearColor(1.0f, 0.88f, 0.69f, 1.0f))
                            ]
                        ]
                        + SVerticalBox::Slot().AutoHeight().Padding(FMargin(0.0f, 6.0f, 0.0f, 0.0f))
                        [
                            SNew(SVerticalBox)
                            .Visibility_Lambda([this]() { return (Owner && Owner->IsTestConsoleExpanded()) ? EVisibility::Visible : EVisibility::Collapsed; })
                            + SVerticalBox::Slot().AutoHeight().Padding(0, 2)
                            [ MakeConsoleBtn(FText::FromString(TEXT("[B] 重放 1.6s 开机校准动效")), [this]() { if (Owner) Owner->TriggerBootSequence(); }) ]
                            + SVerticalBox::Slot().AutoHeight().Padding(0, 2)
                            [ MakeConsoleBtn(FText::FromString(TEXT("[X] 模拟装甲受击 + 震动")), [this]() { if (Owner) Owner->TriggerSimulatedHit(); }) ]
                            + SVerticalBox::Slot().AutoHeight().Padding(0, 2)
                            [ MakeConsoleBtn(FText::FromString(TEXT("[N] 切换 >85% 过热流动告警")), [this]() { if (Owner) Owner->ToggleSimulatedOverheat(); }) ]
                            + SVerticalBox::Slot().AutoHeight().Padding(0, 2)
                            [ MakeConsoleBtn(FText::FromString(TEXT("[Q] 切换自瞄锁定预览")), [this]() { if (Owner) Owner->ToggleAutoAimPreview(); }) ]
                            + SVerticalBox::Slot().AutoHeight().Padding(0, 2)
                            [ MakeConsoleBtn(FText::FromString(TEXT("[M] 切换小地图 3D / 2D")), [this]() { if (Owner) Owner->ToggleMinimap3D(); }) ]
                            + SVerticalBox::Slot().AutoHeight().Padding(0, 2)
                            [
                                SNew(SBox)
                                .Visibility(Rhine3DInternal::IsOutpostPreviewEnabled() ? EVisibility::Visible : EVisibility::Collapsed)
                                [ MakeConsoleBtn(FText::FromString(TEXT("[O] 前哨站界面预览")), [this]() { if (Owner) Owner->CycleSimulatedOutpostCapture(); }) ]
                            ]
                            + SVerticalBox::Slot().AutoHeight().Padding(0, 2)
                            [ MakeConsoleBtn(FText::FromString(TEXT("[E] 触发事件播报 + 增益流光")), [this]() { if (Owner) Owner->TriggerSampleEventToast(); }) ]
                            + SVerticalBox::Slot().AutoHeight().Padding(0, 2)
                            [ MakeConsoleBtn(FText::FromString(TEXT("[F11] 切换官方 2026 UI")), [this]() { if (Owner) Owner->RequestSwitchUiMode(); }) ]
                        ]
                    ]
                ]
            ]
        ];
    }

    void SyncBlurPanels()
    {
        // Deepened high-contrast smoked optical glass renders cleanly without 10x per-frame GPU SBackgroundBlur SceneColor copies.
        for (int32 I = 0; I < BlurWidgets.Num(); ++I)
        {
            if (BlurWidgets[I].IsValid())
            {
                BlurWidgets[I]->SetVisibility(EVisibility::Collapsed);
            }
        }
    }

private:
    FRhine3DHudController* Owner = nullptr;
    TArray<Rhine3DInternal::FPanel3DBounds> BlurBounds;
    TArray<SConstraintCanvas::FSlot*> BlurSlots;
    TArray<TSharedPtr<SBackgroundBlur>> BlurWidgets;
};

// =============================================================================
// FRhine3DHudController IMPLEMENTATION
// =============================================================================

FRhine3DHudController::FRhine3DHudController()
{
    EcgSamples.Init(0.0f, 96);
    PinPulses.Reserve(16);
}

FRhine3DHudController::~FRhine3DHudController()
{
    if (ViewportWidget.IsValid() && GEngine && GEngine->GameViewport)
        GEngine->GameViewport->RemoveViewportWidgetContent(ViewportWidget.ToSharedRef());
    ViewportWidget.Reset();
}

void FRhine3DHudController::LoadArenaHeightfieldOnce()
{
    if (bHeightfieldLoaded) return;
    bHeightfieldLoaded = true;
    const FString GridPath = FPaths::ConvertRelativePathToFull(
        FPaths::Combine(FPaths::ProjectDir(), TEXT("../../assets/ui/rhine_lab/rmuc2027_grid56x32.json")));
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *GridPath)) return;
    TSharedPtr<FJsonObject> Root;
    TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid()) return;
    const int32 NX = Root->GetIntegerField(TEXT("nx"));
    const int32 NY = Root->GetIntegerField(TEXT("ny"));
    const TArray<TSharedPtr<FJsonValue>>& HArr = Root->GetArrayField(TEXT("h"));
    const TArray<TSharedPtr<FJsonValue>>& TArr = Root->GetArrayField(TEXT("t"));
    ArenaCells.Reset();
    ArenaCells.Reserve(400);
    for (int32 Iy = 0; Iy < FMath::Min(NY, HArr.Num()); ++Iy)
    {
        const TArray<TSharedPtr<FJsonValue>>& HRow = HArr[Iy]->AsArray();
        const TArray<TSharedPtr<FJsonValue>>& TRow = TArr[Iy]->AsArray();
        for (int32 Ix = 0; Ix < FMath::Min(NX, HRow.Num()); ++Ix)
        {
            const float HVal = static_cast<float>(HRow[Ix]->AsNumber()) * 0.1f;
            if (HVal > 0.08f)
            {
                FRhine3DMapCell Cell;
                Cell.ArenaX = (float(Ix) / float(NX) - 0.5f) * 28.0f;
                Cell.ArenaY = (float(Iy) / float(NY) - 0.5f) * 16.0f;
                Cell.HeightM = HVal;
                Cell.CellType = static_cast<uint8>(TRow[Ix]->AsNumber());
                ArenaCells.Add(Cell);
            }
        }
    }
}

void FRhine3DHudController::EnsureMounted(UWorld* World)
{
    if (bMounted || !GEngine || !GEngine->GameViewport) return;
    LoadArenaHeightfieldOnce();
    ViewportWidget = SNew(SRhine3DHudViewport, this);
    GEngine->GameViewport->AddViewportWidgetContent(ViewportWidget.ToSharedRef(), 40);
    bMounted = true;
}

void FRhine3DHudController::SetEnabled(UWorld* World, bool bEnable)
{
    bEnabled = bEnable;
    if (bEnable)
    {
        EnsureMounted(World);
        if (ViewportWidget.IsValid()) ViewportWidget->SetVisibility(EVisibility::SelfHitTestInvisible);
    }
    else if (ViewportWidget.IsValid())
    {
        ViewportWidget->SetVisibility(EVisibility::Collapsed);
    }
}

void FRhine3DHudController::Destroy(UWorld* World)
{
    if (ViewportWidget.IsValid() && GEngine && GEngine->GameViewport)
        GEngine->GameViewport->RemoveViewportWidgetContent(ViewportWidget.ToSharedRef());
    ViewportWidget.Reset();
    bMounted = false;
    bEnabled = false;
}

void FRhine3DHudController::TriggerBootSequence() { BootProgress = 0.0f; bBootActive = true; }
void FRhine3DHudController::TriggerSimulatedHit()
{
    SimHitFlash = 1.0f;
    SimHitArmorId = static_cast<uint8>(FMath::RandRange(0, 3));
    ShakeX = FMath::FRandRange(-16.0f, 16.0f);
    ShakeY = FMath::FRandRange(-12.0f, 12.0f);
}
void FRhine3DHudController::ToggleSimulatedOverheat()     { bOverheatPreview = !bOverheatPreview; }
void FRhine3DHudController::ToggleAutoAimPreview()        { bAutoAimPreview = !bAutoAimPreview; }
void FRhine3DHudController::ToggleMinimap3D()             { bMinimap3D = !bMinimap3D; }
void FRhine3DHudController::ToggleTestConsoleExpanded()   { bConsoleExpanded = !bConsoleExpanded; }
void FRhine3DHudController::RequestSwitchUiMode()         { bSwitchUiRequested = true; }
void FRhine3DHudController::CycleSimulatedOutpostCapture()
{
    if (!Rhine3DInternal::IsOutpostPreviewEnabled()) return;
    SimOutpostMode = (SimOutpostMode + 1) % 3;
    if (SimOutpostMode == 1)
    {
        SimToastText = TEXT("BLUE #7 SENTRY CAPTURING RED OUTPOST (62%)");
        SimToastTimer = 2.6f; ToastWipeProgress = 0.0f;
    }
    else if (SimOutpostMode == 2)
    {
        SimToastText = TEXT("RED OUTPOST CAPTURED & REBUILT BY BLUE!");
        SimToastTimer = 3.2f; ToastWipeProgress = 0.0f;
    }
}
void FRhine3DHudController::TriggerSampleEventToast()
{
    bSimBuffBoost = !bSimBuffBoost;
    SimToastText = bSimBuffBoost ? TEXT("RED #7 SENTRY ACTIVATED ENERGY RUNE (+50%)") : TEXT("BLUE #7 SENTRY ELIMINATED RED #3 INFANTRY");
    SimToastTimer = 3.2f;
    ToastWipeProgress = 0.0f;
}
bool FRhine3DHudController::ConsumeUiSwitchRequest()
{
    const bool bRes = bSwitchUiRequested;
    bSwitchUiRequested = false;
    return bRes;
}

void FRhine3DHudController::UpdateFrame(UWorld* World, const FRhine3DHudState& InState, float DeltaSeconds)
{
    const float Dt = FMath::Clamp(DeltaSeconds, 0.001f, 0.05f);
    DisplayState = InState;
    if (!bEnabled) return;
    EnsureMounted(World);

    ElapsedSec += Dt;
    FpsSmooth += ((1.0f / Dt) - FpsSmooth) * 0.08f;

    if (APlayerController* PC = World ? World->GetFirstPlayerController() : nullptr)
    {
        auto CheckEdge = [&](const FKey& Key, bool& bPrev) -> bool
        {
            const bool bNow = PC->IsInputKeyDown(Key);
            const bool bTrig = (bNow && !bPrev);
            bPrev = bNow;
            return bTrig;
        };
        if (CheckEdge(EKeys::B, bPrevB)) TriggerBootSequence();
        if (CheckEdge(EKeys::X, bPrevX)) TriggerSimulatedHit();
        if (CheckEdge(EKeys::N, bPrevN)) ToggleSimulatedOverheat();
        if (CheckEdge(EKeys::M, bPrevM)) ToggleMinimap3D();
        if (CheckEdge(EKeys::O, bPrevO)) CycleSimulatedOutpostCapture();
        if (CheckEdge(EKeys::E, bPrevE)) TriggerSampleEventToast();
        if (CheckEdge(EKeys::F10, bPrevF10)) ToggleTestConsoleExpanded();
    }

    if (bBootActive)
    {
        BootProgress += Dt;
        if (BootProgress >= 1.65f) bBootActive = false;
    }

    const float GimYawVel = (InState.GimbalYawRad - PrevGimbalYaw) / Dt;
    const float GimPitchVel = (InState.GimbalPitchRad - PrevGimbalPitch) / Dt;
    PrevGimbalYaw = InState.GimbalYawRad;
    PrevGimbalPitch = InState.GimbalPitchRad;

    const float TargetParX = FMath::Clamp(FMath::Sin(InState.GimbalYawRad) * 0.55f + GimYawVel * 0.14f, -1.0f, 1.0f);
    const float TargetParY = FMath::Clamp(InState.GimbalPitchRad * 0.70f + GimPitchVel * 0.14f, -1.0f, 1.0f);
    ParX = UKismetMathLibrary::FloatSpringInterp(ParX, TargetParX, ParXSpring, 52.0f, 0.74f, Dt);
    ParY = UKismetMathLibrary::FloatSpringInterp(ParY, TargetParY, ParYSpring, 52.0f, 0.74f, Dt);

    if (InState.TotalShotsFired > PrevShotsFired)
    {
        RecoilSpring.Velocity += 9.5f;
        PrevShotsFired = InState.TotalShotsFired;
    }
    Recoil = FMath::Max(0.0f, UKismetMathLibrary::FloatSpringInterp(Recoil, 0.0f, RecoilSpring, 155.0f, 0.65f, Dt));

    const bool bLockedNow = InState.bAutoAimLocked || bAutoAimPreview;
    LockBlend += ((bLockedNow ? 1.0f : 0.0f) - LockBlend) * FMath::Min(1.0f, Dt * 10.0f);
    MapTiltRad = UKismetMathLibrary::FloatSpringInterp(MapTiltRad, bMinimap3D ? 0.56f : 0.001f, MapTiltSpring, 64.0f, 0.85f, Dt);

    if (InState.OwnHitFlash > 0.90f && FMath::Abs(ShakeX) < 1.0f)
    {
        ShakeX = FMath::FRandRange(-14.0f, 14.0f);
        ShakeY = FMath::FRandRange(-10.0f, 10.0f);
    }
    ShakeX *= FMath::Pow(0.0015f, Dt);
    ShakeY *= FMath::Pow(0.0015f, Dt);
    SimHitFlash = FMath::Max(0.0f, SimHitFlash - Dt * 1.45f);

    const float OwnPct   = FMath::Clamp(float(InState.OwnHp) / float(FMath::Max(1, InState.OwnMaxHp)), 0.0f, 1.0f);
    const float RedPct   = FMath::Clamp(float(InState.RedBaseHp) / float(FMath::Max(1, InState.BaseMaxHp)), 0.0f, 1.0f);
    const float BluPct   = FMath::Clamp(float(InState.BlueBaseHp) / float(FMath::Max(1, InState.BaseMaxHp)), 0.0f, 1.0f);
    const float RedOpPct = FMath::Clamp(float(InState.RedOutpost.Hp) / float(FMath::Max(1, InState.RedOutpost.MaxHp)), 0.0f, 1.0f);
    const float BluOpPct = FMath::Clamp(float(InState.BlueOutpost.Hp) / float(FMath::Max(1, InState.BlueOutpost.MaxHp)), 0.0f, 1.0f);
    OwnHpGhostPct      = OwnPct   > OwnHpGhostPct      ? OwnPct   : FMath::FInterpTo(OwnHpGhostPct,      OwnPct,   Dt, 2.2f);
    RedBaseGhostPct    = RedPct   > RedBaseGhostPct    ? RedPct   : FMath::FInterpTo(RedBaseGhostPct,    RedPct,   Dt, 1.8f);
    BlueBaseGhostPct   = BluPct   > BlueBaseGhostPct   ? BluPct   : FMath::FInterpTo(BlueBaseGhostPct,   BluPct,   Dt, 1.8f);
    RedOutpostGhostPct = RedOpPct > RedOutpostGhostPct ? RedOpPct : FMath::FInterpTo(RedOutpostGhostPct, RedOpPct, Dt, 2.0f);
    BlueOutpostGhostPct= BluOpPct > BlueOutpostGhostPct? BluOpPct : FMath::FInterpTo(BlueOutpostGhostPct,BluOpPct, Dt, 2.0f);

    if (InState.MatchPhaseText != PrevPhaseText)
    {
        PrevPhaseText = InState.MatchPhaseText;
        PhaseSweepTimer = 0.75f;
    }
    PhaseSweepTimer = FMath::Max(0.0f, PhaseSweepTimer - Dt);

    if (SimToastTimer > 0.0f) SimToastTimer = FMath::Max(0.0f, SimToastTimer - Dt);
    const bool bAnyToast = (InState.BannerEventTimer > 0.0f && !InState.BannerEventText.IsEmpty()) || SimToastTimer > 0.0f;
    if (bAnyToast) ToastWipeProgress = FMath::Min(1.0f, ToastWipeProgress + Dt * 2.4f);
    else ToastWipeProgress = 0.0f;

    if (FMath::Abs(InState.GyroRadS) > 0.65f) ChassisYawRad += InState.GyroRadS * Dt;
    else ChassisYawRad = -InState.GimbalYawRad;

    RadarAngleRad = FMath::Fmod(RadarAngleRad + Dt * 1.45f, 2.0f * PI);
    if (PinPulses.Num() != InState.MapPins.Num()) PinPulses.Init(0.0f, InState.MapPins.Num());
    for (int32 I = 0; I < InState.MapPins.Num(); ++I)
    {
        const float UAng = FMath::Atan2(InState.MapPins[I].ArenaY / 8.0f, InState.MapPins[I].ArenaX / 14.0f);
        const float Diff = FMath::Fmod(FMath::Fmod(RadarAngleRad - UAng, 2.0f * PI) + 2.0f * PI, 2.0f * PI);
        if (Diff < 0.14f) PinPulses[I] = 1.0f;
        else PinPulses[I] = FMath::Max(0.0f, PinPulses[I] - Dt * 1.6f);
    }

    const float HitNow = FMath::Max(InState.OwnHitFlash, SimHitFlash);
    EcgPhase += Dt * (HitNow > 0.1f ? 14.0f : 6.5f);
    const float Beat = FMath::Fmod(EcgPhase, 2.0f * PI);
    float EVal = 0.0f;
    if (Beat < 0.25f) EVal = FMath::Sin(Beat / 0.25f * PI) * 0.22f;
    else if (Beat < 0.42f) EVal = -0.25f;
    else if (Beat < 0.58f) EVal = 0.92f;
    else if (Beat < 0.76f) EVal = -0.38f;
    else if (Beat < 1.15f) EVal = FMath::Sin((Beat - 0.76f) / 0.39f * PI) * 0.28f;
    if (HitNow > 0.1f) EVal += FMath::Sin(ElapsedSec * 58.0f) * 0.75f * HitNow;
    for (int32 I = 0; I < EcgSamples.Num() - 1; ++I) EcgSamples[I] = EcgSamples[I + 1];
    EcgSamples.Last() = EVal;

    if (ViewportWidget.IsValid()) ViewportWidget->SyncBlurPanels();
}
