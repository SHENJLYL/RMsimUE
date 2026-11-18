#pragma once
#include "CoreMinimal.h"
#include "Engine/GameEngine.h"
#include "RMSimGameEngine.generated.h"

/** Select a persistent scene target before the game viewport is constructed. */
UCLASS()
class RMSIM_API URMSimGameEngine : public UGameEngine
{
    GENERATED_BODY()
public:
    virtual TSharedRef<SViewport> CreateGameViewportWidget() override;
};
