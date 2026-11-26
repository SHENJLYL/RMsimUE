#include "RMUCRuneClientBridge.h"

#include "RMUCGameReferee.h"
#include "Dom/JsonObject.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformTime.h"
#include "IPAddress.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SocketSubsystem.h"
#include "Sockets.h"

namespace
{
constexpr int32 MaxLineBytes = 2048;
constexpr int32 MaxOutputBytes = 16384;

bool IntegerField(const FJsonObject& Object, const TCHAR* Name, int64& Value)
{
    double Number;
    if (!Object.TryGetNumberField(Name, Number) || !FMath::IsFinite(Number) ||
        Number < 0. || Number > 9007199254740991. || FMath::FloorToDouble(Number) != Number) return false;
    Value = static_cast<int64>(Number);
    return true;
}
}

FRMUCRuneClientBridge::~FRMUCRuneClientBridge()
{
    CloseClient();
    if (Listener) ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(Listener);
}

bool FRMUCRuneClientBridge::Open(int32 Port)
{
    Token = FPlatformMisc::GetEnvironmentVariable(TEXT("RMSIM_RUNE_BRIDGE_TOKEN"));
    if (Port < 1024 || Port > 65535 || Token.Len() < 16 || Token.Len() > 128) return false;
    auto* Sockets = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
    const auto Address = Sockets->CreateInternetAddr();
    bool bValid = false;
    Address->SetIp(TEXT("127.0.0.1"), bValid);
    Address->SetPort(Port);
    Listener = Sockets->CreateSocket(NAME_Stream, TEXT("RMSim Rune Gateway"), false);
    Session = FGuid::NewGuid().ToString(EGuidFormats::Digits);
    return bValid && Listener && Listener->SetNonBlocking(true) && Listener->Bind(*Address) && Listener->Listen(1);
}

void FRMUCRuneClientBridge::CloseClient()
{
    if (Client) ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(Client);
    Client = nullptr;
    Inbound.Reset();
    Outbound.Reset();
    bAuthenticated = false;
    LastSequence = -1;
    NextState = 0.;
}

bool FRMUCRuneClientBridge::Enqueue(TSharedRef<FJsonObject> Message)
{
    Message->SetNumberField(TEXT("v"), 1);
    Message->SetStringField(TEXT("session"), Session);
    Message->SetNumberField(TEXT("epoch"), static_cast<double>(Epoch));
    FString Text;
    if (!FJsonSerializer::Serialize(Message, TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Text))) return false;
    FTCHARToUTF8 Bytes(*Text);
    if (Outbound.Num() + Bytes.Length() + 1 > MaxOutputBytes) return false;
    Outbound.Append(reinterpret_cast<const uint8*>(Bytes.Get()), Bytes.Length());
    Outbound.Add('\n');
    return true;
}

bool FRMUCRuneClientBridge::ReadLine(ARMUCGameReferee& Referee, const FString& Text)
{
    TSharedPtr<FJsonObject> Message;
    int64 Version;
    FString Kind;
    if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Message) || !Message ||
        !IntegerField(*Message, TEXT("v"), Version) || Version != 1 || !Message->TryGetStringField(TEXT("kind"), Kind)) return false;
    if (!bAuthenticated)
    {
        FString ReceivedToken;
        bAuthenticated = Kind == TEXT("hello") && Message->TryGetStringField(TEXT("token"), ReceivedToken) && ReceivedToken == Token;
        return bAuthenticated;
    }

    FString ReceivedSession;
    int64 ReceivedEpoch, Sequence, Robot, Activate;
    if (Kind != TEXT("rune_activate") || !Message->TryGetStringField(TEXT("session"), ReceivedSession) ||
        !IntegerField(*Message, TEXT("epoch"), ReceivedEpoch) || !IntegerField(*Message, TEXT("seq"), Sequence) ||
        !IntegerField(*Message, TEXT("robot_id"), Robot) || !IntegerField(*Message, TEXT("activate"), Activate)) return false;
    FString Result;
    if (ReceivedSession != Session || ReceivedEpoch != Epoch) Result = TEXT("stale_round");
    else if (Sequence <= LastSequence) Result = TEXT("duplicate");
    else
    {
        LastSequence = Sequence;
        if (Activate != 1) Result = TEXT("invalid_activate");
        else if (Robot == 3 || Robot == 4 || Robot == 103 || Robot == 104)
            Result = Referee.RequestInfantryClientRune(Robot < 100, static_cast<int32>(Robot % 100));
        else Result = TEXT("invalid_seat");
    }
    auto Reply = MakeShared<FJsonObject>();
    Reply->SetStringField(TEXT("kind"), TEXT("rune_result"));
    Reply->SetNumberField(TEXT("seq"), static_cast<double>(Sequence));
    Reply->SetStringField(TEXT("result"), Result);
    NextState = 0.;
    return Enqueue(Reply);
}

void FRMUCRuneClientBridge::Tick(ARMUCGameReferee& Referee)
{
    if (!Listener) return;
    const double Now = FPlatformTime::Seconds();
    bool bPending = false;
    if (!Client && Listener->HasPendingConnection(bPending) && bPending)
    {
        Client = Listener->Accept(TEXT("RMSim Rune Gateway Client"));
        if (Client && (!Client->SetNonBlocking(true) || !Client->SetNoDelay(true))) CloseClient();
        HelloDeadline = Now + 2.;
    }
    if (!Client) return;
    if (!bAuthenticated && Now > HelloDeadline) { CloseClient(); return; }
    uint8 Buffer[2048];
    int32 Received = 0;
    if (!Client->Recv(Buffer, sizeof(Buffer), Received)) { CloseClient(); return; }
    Inbound.Append(Buffer, Received);
    int32 End;
    while (Inbound.Find(static_cast<uint8>('\n'), End))
    {
        if (End == 0 || End > MaxLineBytes) { CloseClient(); return; }
        const FUTF8ToTCHAR Text(reinterpret_cast<const ANSICHAR*>(Inbound.GetData()), End);
        if (!ReadLine(Referee, FString(Text.Length(), Text.Get()))) { CloseClient(); return; }
        Inbound.RemoveAt(0, End + 1, EAllowShrinking::No);
    }
    if (Inbound.Num() > MaxLineBytes) { CloseClient(); return; }
    if (bAuthenticated && Now >= NextState)
    {
        auto State = Referee.RuneClientState();
        State->SetStringField(TEXT("kind"), TEXT("rune_state"));
        if (!Enqueue(State)) { CloseClient(); return; }
        NextState = Now + 1.;
    }
    int32 Sent = 0;
    if (!Outbound.IsEmpty())
    {
        if (!Client->Send(Outbound.GetData(), Outbound.Num(), Sent))
        {
            if (ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->GetLastErrorCode() != SE_EWOULDBLOCK) CloseClient();
        }
        else Outbound.RemoveAt(0, Sent, EAllowShrinking::No);
    }
}
