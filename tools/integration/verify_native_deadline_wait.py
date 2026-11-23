"""Compile and exercise the production Windows deadline-wait helper directly."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "unreal/RMSim/Source/RMSim/Timing/WindowsDeadlineWait.h"

CASES = r'''
#define RMSIM_DEADLINE_WAIT_CONTRACT 1
#include "WindowsDeadlineWait.h"
#include <algorithm>
#include <array>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>
using Waiter=FRMSimWindowsDeadlineWait;
void Check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
double Now(){static const double inverse=[](){LARGE_INTEGER f;QueryPerformanceFrequency(&f);return 1./double(f.QuadPart);}();
 LARGE_INTEGER q;QueryPerformanceCounter(&q);return double(q.QuadPart)*inverse;}
double ThreadCpu(){FILETIME c,e,k,u;Check(GetThreadTimes(GetCurrentThread(),&c,&e,&k,&u)!=0,"GetThreadTimes");
 ULARGE_INTEGER a,b;a.LowPart=k.dwLowDateTime;a.HighPart=k.dwHighDateTime;b.LowPart=u.dwLowDateTime;b.HighPart=u.dwHighDateTime;
 return double(a.QuadPart+b.QuadPart)*1.e-7;}
DWORD HandleCount(){DWORD n=0;Check(GetProcessHandleCount(GetCurrentProcess(),&n)!=0,"GetProcessHandleCount");return n;}
struct FRMSimWindowsDeadlineWaitContractAccess
{
 static HANDLE Open(Waiter& w){w.EnsureTimer();return w.Timer;}
 static void UseEventToProvokeRealArmError(Waiter& w){
  w.Timer=CreateEventW(nullptr,FALSE,FALSE,nullptr);Check(w.Timer!=nullptr,"create failure-injection event");
  w.bCreationAttempted=true;
 }
};
void ExpiredAndInvalid(){
 Waiter w;const auto a=w.WaitUntil(Now()-.01,Now);
 const auto b=w.WaitUntil(std::numeric_limits<double>::quiet_NaN(),Now);
 Check(a.TimerWaitCalls==0&&a.FallbackSleepCalls==0&&a.DeadlineOvershootSeconds==0.,"past deadline waited or misreported wait overshoot");
 Check(b.InvalidDeadline&&w.GetStatistics().CreateCalls==0,"invalid deadline allocated timer");
 std::cout<<"{\"name\":\"past_and_invalid_deadlines_do_not_wait_or_allocate\",\"passed\":true,\"timer_creates\":0}\n";
}
void MeasuredDeadlines(){
 Waiter w;std::vector<double> overshoot;double requested=0.,actual=0.;int expired_before_wait=0;
 const std::array<double,6> durations={.0002,.0006,.0012,.0018,.0032,.005};
 const double begin=Now(),cpu=ThreadCpu();
 for(int i=0;i<240;++i){const double deadline=Now()+durations[std::size_t(i)%durations.size()];
  const auto r=w.WaitUntil(deadline,Now);Check(Now()>=deadline,"wait returned before absolute deadline");
  Check(!r.InvalidDeadline&&r.FallbackSleepCalls==0&&r.NewFailures==0,"high-resolution path unavailable");
  if(r.TimerWaitCalls)overshoot.push_back(r.DeadlineOvershootSeconds);else ++expired_before_wait;
  requested+=r.RequestedSeconds;actual+=r.ActualWaitSeconds;}
 const double elapsed=Now()-begin,cpu_elapsed=ThreadCpu()-cpu;
 const auto s=w.GetStatistics();Check(s.CreateCalls==1&&s.TimerWaitCalls>0&&s.TimerWaitCalls<=960&&!overshoot.empty(),"high-resolution wait unavailable or unbounded request amplification");
 DWORD flags=0;Check(GetHandleInformation(FRMSimWindowsDeadlineWaitContractAccess::Open(w),&flags)!=0&&!(flags&HANDLE_FLAG_INHERIT),"timer handle inherited");
 std::sort(overshoot.begin(),overshoot.end());
 std::cout<<"{\"name\":\"real_high_resolution_sub_ms_and_ms_deadlines\",\"passed\":true,\"deadlines\":240,\"timer_creates\":"<<s.CreateCalls
 <<",\"timer_wait_calls\":"<<s.TimerWaitCalls<<",\"fallback_calls\":"<<s.FallbackSleepCalls<<",\"sleep_zero_calls\":0"
 <<",\"expired_before_wait\":"<<expired_before_wait<<",\"wall_s\":"<<elapsed<<",\"thread_cpu_s\":"<<cpu_elapsed<<",\"requested_s\":"<<requested<<",\"blocked_s\":"<<actual
 <<",\"overshoot_p50_ms\":"<<overshoot[overshoot.size()/2]*1000.<<",\"overshoot_p95_ms\":"<<overshoot[(overshoot.size()-1)*95/100]*1000.
 <<",\"overshoot_max_ms\":"<<overshoot.back()*1000.<<",\"non_inheritable\":true}\n";
}
void AbsoluteSchedule(){
 Waiter w;const double begin=Now();int no_wait=0;double requested=0.;
 for(int i=1;i<=120;++i){
  if(i==40)Sleep(35); // Induced lateness, with every original deadline retained.
  const double deadline=begin+double(i)*.005;
  const auto r=w.WaitUntil(deadline,Now);
  Check(Now()>=deadline&&!r.InvalidDeadline&&r.NewFailures==0,"absolute schedule wait contract");
  if(r.TimerWaitCalls==0)++no_wait;requested+=r.RequestedSeconds;
 }
 const double elapsed=Now()-begin;const auto s=w.GetStatistics();
 Check(no_wait>=5&&s.TimerWaitCalls<=240&&s.FallbackSleepCalls==0,"late slots were reanchored or unbounded waits introduced");
 Check(elapsed>=.6,"absolute end deadline shortened");
 std::cout<<"{\"name\":\"absolute_200hz_grid_retains_every_slot_after_hitch\",\"passed\":true,\"logical_slots\":120,\"step_s\":0.005,\"anchor_changes\":0"
 <<",\"expired_slots_without_wait\":"<<no_wait<<",\"timer_wait_calls\":"<<s.TimerWaitCalls<<",\"requested_s\":"<<requested
 <<",\"wall_s\":"<<elapsed<<",\"final_deadline_error_ms\":"<<(elapsed-.6)*1000.<<"}\n";
}
void CancelAndRelease(){
 const DWORD before=HandleCount();Waiter w;const HANDLE handle=FRMSimWindowsDeadlineWaitContractAccess::Open(w);
 Check(handle!=nullptr&&HandleCount()==before+1,"single owned timer handle");
 LARGE_INTEGER due;due.QuadPart=-5000000;
 Check(SetWaitableTimerEx(handle,&due,0,nullptr,nullptr,nullptr,0)!=0,"arm pending timer before cleanup");
 w.CancelAndClose();w.CancelAndClose();const auto s=w.GetStatistics();
 Check(s.CancelCalls==1&&s.CloseCalls==1&&s.CancelFailures==0&&s.CloseFailures==0,"pending timer cancellation or idempotent release failed");
 Check(HandleCount()==before,"pending timer handle not released");
 DWORD flags=0;Check(!GetHandleInformation(handle,&flags)&&GetLastError()==ERROR_INVALID_HANDLE,"released handle remains live");
 for(int i=0;i<128;++i){Waiter again;Check(FRMSimWindowsDeadlineWaitContractAccess::Open(again)!=nullptr,"repeated timer allocation");}
 Check(HandleCount()==before,"destructor handle leak");
 std::cout<<"{\"name\":\"pending_cancel_idempotent_close_and_destructor_release\",\"passed\":true,\"cancel_calls\":1,\"close_calls\":1,\"destructor_cycles\":128,\"handles_before\":"<<before<<",\"handles_after\":"<<HandleCount()<<"}\n";
}
void RealApiFailureFallback(){
 Waiter w;FRMSimWindowsDeadlineWaitContractAccess::UseEventToProvokeRealArmError(w);
 const double begin=Now();const auto a=w.WaitUntil(begin+.00025,Now);
 const auto error=w.GetStatistics().LastError;
 const auto b=w.WaitUntil(Now()+.00025,Now);const auto s=w.GetStatistics();
 Check(a.NewFailures==1&&error==ERROR_INVALID_HANDLE&&w.IsFallback(),"OS arm failure was not recorded");
 Check(a.TimerWaitCalls==0&&a.FallbackSleepCalls>=1&&a.RequestedSeconds>=.001,"failure did not use at least one positive millisecond");
 Check(b.NewFailures==0&&b.TimerWaitCalls==0&&b.FallbackSleepCalls>=1&&b.RequestedSeconds>=.001,"sticky fallback retried broken timer or yielded zero");
 Check(s.CloseCalls==1&&s.CloseFailures==0,"failure event not released");
 std::cout<<"{\"name\":\"real_os_arm_failure_uses_counted_minimum_1ms_fallback\",\"passed\":true,\"fault\":\"test replaces timer with an event so SetWaitableTimerEx returns ERROR_INVALID_HANDLE\",\"win32_error\":"<<error
 <<",\"failures\":"<<s.Failures<<",\"fallback_calls\":"<<s.FallbackSleepCalls<<",\"sleep_zero_calls\":0,\"requested_s\":"<<a.RequestedSeconds+b.RequestedSeconds
 <<",\"wall_s\":"<<Now()-begin<<"}\n";
}
int main(){try{std::cout<<std::setprecision(17);ExpiredAndInvalid();MeasuredDeadlines();AbsoluteSchedule();CancelAndRelease();RealApiFailureFallback();return 0;}
 catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}}
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--header", type=Path, default=HEADER)
    parser.add_argument("--cxx", default=shutil.which("cl"))
    args = parser.parse_args()
    if sys.platform != "win32" or not args.cxx:
        parser.error("Run in the Windows MSVC developer environment; no timer emulation is used.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    header = args.header.resolve()
    source = output / "deadline-wait-contract.cpp"
    source.write_text(CASES, encoding="utf-8")
    binary = output / "deadline-wait-contract.exe"
    command = [args.cxx, "/nologo", "/std:c++17", "/O2", "/EHsc", "/W4", "/WX", "/D_WIN32_WINNT=0x0A00",
               "/I" + str(header.parent), str(source), "/Fe:" + str(binary), "/Fo:" + str(output / "deadline-wait-contract.obj")]
    compiled = subprocess.run(command, cwd=output, capture_output=True, text=True, timeout=30)
    (output / "compiler.log").write_text(compiled.stdout + compiled.stderr, encoding="utf-8")
    report = {"schema": "rmsim_windows_deadline_wait_contract_v1", "platform": platform.platform(),
              "header": str(header), "header_sha256": hashlib.sha256(header.read_bytes()).hexdigest(),
              "compiler_command": command, "compile_exit_code": compiled.returncode,
              "test_exit_code": None, "checks": [],
              "scope": "Exact production helper, real Win32 high-resolution timer and QPC; absolute schedule replay is logical, without UE/solver/render/ROS.",
              "timing_scope": "Reported deadline errors are observations on this host; no full-session FPS or realtime gate is inferred.",
              "system_timer_priority_or_power_changes": False}
    if compiled.returncode == 0:
        run = subprocess.run([str(binary)], cwd=output, capture_output=True, text=True, timeout=15)
        (output / "test.log").write_text(run.stdout + run.stderr, encoding="utf-8")
        report["test_exit_code"] = run.returncode
        report["checks"] = [json.loads(line) for line in run.stdout.splitlines() if line.startswith("{")]
    report["passed"] = compiled.returncode == 0 and report["test_exit_code"] == 0 and len(report["checks"]) == 5
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "report": str(output / "verification.json")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
