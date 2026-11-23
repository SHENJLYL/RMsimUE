#pragma once

// The contract executable compiles this exact helper with the Windows SDK.
// Engine builds use UE's API wrapper so Windows macros do not escape the header.
#if defined(RMSIM_DEADLINE_WAIT_CONTRACT)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <Windows.h>
#else
#include "Windows/WindowsHWrapper.h"
#endif
#include <cmath>
#include <cstdint>

// Game-thread owned. The caller supplies the same monotonic clock used to form
// its absolute deadline. No simulation clock, epoch or frame count is changed.
class FRMSimWindowsDeadlineWait
{
public:
    struct FStatistics
    {
        std::uint64_t CreateCalls=0,TimerWaitCalls=0,FallbackSleepCalls=0;
        std::uint64_t Failures=0,CancelCalls=0,CancelFailures=0,CloseCalls=0,CloseFailures=0;
        std::uint32_t LastError=0;
    };
    struct FResult
    {
        std::uint64_t TimerWaitCalls=0,FallbackSleepCalls=0,NewFailures=0;
        double RequestedSeconds=0.,ActualWaitSeconds=0.,DeadlineOvershootSeconds=0.;
        bool InvalidDeadline=false;
    };

    FRMSimWindowsDeadlineWait()=default;
    FRMSimWindowsDeadlineWait(const FRMSimWindowsDeadlineWait&)=delete;
    FRMSimWindowsDeadlineWait& operator=(const FRMSimWindowsDeadlineWait&)=delete;
    ~FRMSimWindowsDeadlineWait(){CancelAndClose();}

    const FStatistics& GetStatistics() const {return Statistics;}
    bool IsFallback() const {return bFallback;}

    template<class FClock> FResult WaitUntil(double Deadline,FClock Now)
    {
        FResult Result;
        double Current=Now();
        if(!std::isfinite(Deadline)||!std::isfinite(Current))
        {
            Result.InvalidDeadline=true;
            return Result;
        }
        const auto PreviousFailures=Statistics.Failures;
        while(Current<Deadline)
        {
            EnsureTimer();
            Current=Now();
            if(Current>=Deadline)break;
            const double Remaining=Deadline-Current;
            // Bound conversion and each kernel wait; all re-arms still use the
            // original absolute deadline, including unusually long waits.
            const double Slice=Remaining<1.?Remaining:1.;
            bool bWaited=false;
            if(Timer)
            {
                LARGE_INTEGER DueTime;
                const LONGLONG RelativeTicks=static_cast<LONGLONG>(std::ceil(Slice*10000000.));
                DueTime.QuadPart=-(RelativeTicks>0?RelativeTicks:1);
                if(SetWaitableTimerEx(Timer,&DueTime,0,nullptr,nullptr,nullptr,0))
                {
                    ++Statistics.TimerWaitCalls;++Result.TimerWaitCalls;
                    Result.RequestedSeconds+=double(-DueTime.QuadPart)*1.e-7;
                    const double WaitStart=Now();
                    const DWORD WaitResult=WaitForSingleObject(Timer,INFINITE);
                    Result.ActualWaitSeconds+=Now()-WaitStart;
                    if(WaitResult==WAIT_OBJECT_0)bWaited=true;
                    else Fail(WaitResult==WAIT_FAILED?GetLastError():ERROR_GEN_FAILURE);
                }
                else Fail(GetLastError());
            }
            if(!bWaited)
            {
                // Explicit degraded path: positive milliseconds only. Windows
                // SleepNoStats truncates sub-ms requests to SwitchToThread.
                const double Left=Deadline-Now();
                if(Left>0.)
                {
                    const double FallbackSlice=Left<1.?Left:1.;
                    DWORD Milliseconds=static_cast<DWORD>(std::ceil(FallbackSlice*1000.));
                    if(Milliseconds<1)Milliseconds=1;
                    ++Statistics.FallbackSleepCalls;++Result.FallbackSleepCalls;
                    Result.RequestedSeconds+=double(Milliseconds)*.001;
                    const double WaitStart=Now();
                    Sleep(Milliseconds);
                    Result.ActualWaitSeconds+=Now()-WaitStart;
                }
            }
            Current=Now();
        }
        if(Result.TimerWaitCalls+Result.FallbackSleepCalls>0)
            Result.DeadlineOvershootSeconds=Current>Deadline?Current-Deadline:0.;
        Result.NewFailures=Statistics.Failures-PreviousFailures;
        return Result;
    }

    // Called after removing the game-thread pacing delegate, with no active
    // waiter. Cancel is for a pending timer, not cross-thread interruption.
    void CancelAndClose()
    {
        if(!Timer)return;
        ++Statistics.CancelCalls;
        if(!CancelWaitableTimer(Timer)){++Statistics.CancelFailures;Statistics.LastError=GetLastError();}
        ++Statistics.CloseCalls;
        if(!CloseHandle(Timer)){++Statistics.CloseFailures;Statistics.LastError=GetLastError();}
        Timer=nullptr;
    }

private:
    friend struct FRMSimWindowsDeadlineWaitContractAccess;
    HANDLE Timer=nullptr;
    bool bCreationAttempted=false,bFallback=false;
    FStatistics Statistics;

    void EnsureTimer()
    {
        if(bCreationAttempted)return;
        bCreationAttempted=true;++Statistics.CreateCalls;
        // Unnamed, auto-reset, non-inheritable; no process/system timer changes.
        Timer=CreateWaitableTimerExW(nullptr,nullptr,CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
            TIMER_MODIFY_STATE|SYNCHRONIZE);
        if(!Timer)Fail(GetLastError());
    }
    void Fail(DWORD Error)
    {
        ++Statistics.Failures;Statistics.LastError=Error;bFallback=true;
        // Keep failure sticky for this instance instead of retrying an
        // unsupported API on every 5ms frame.
        CancelAndClose();
        Statistics.LastError=Error;
    }
};
