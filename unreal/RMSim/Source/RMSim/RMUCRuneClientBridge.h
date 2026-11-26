#pragma once

#include "CoreMinimal.h"

class ARMUCGameReferee;
class FJsonObject;
class FSocket;

// Private loopback connection from the authenticated RMUC client gateway.
class FRMUCRuneClientBridge
{
public:
    ~FRMUCRuneClientBridge();
    bool Open(int32 Port);
    void BeginRound() { ++Epoch; LastSequence = -1; NextState = 0.; }
    void Tick(ARMUCGameReferee& Referee);

private:
    void CloseClient();
    bool ReadLine(ARMUCGameReferee& Referee, const FString& Text);
    bool Enqueue(TSharedRef<FJsonObject> Message);

    FSocket* Listener = nullptr;
    FSocket* Client = nullptr;
    FString Token, Session;
    TArray<uint8> Inbound, Outbound;
    int64 Epoch = 0, LastSequence = -1;
    double NextState = 0., HelloDeadline = 0.;
    bool bAuthenticated = false;
};
