"""Compile the production render scheduler and exercise timing/epoch contracts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "unreal/RMSim/Source/RMSim/Rendering/NativeRenderScheduler.h"

CASES = r'''
#include "NativeRenderScheduler.h"
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
using Scheduler=RMSimRendering::NativeRenderScheduler;
constexpr double Step=.005;
void Check(bool ok,const char* detail){if(!ok)throw std::runtime_error(detail);}
void Row(const char* name,const Scheduler& s,double wall=0.,double sim=0.){
 std::cout<<std::setprecision(17)<<"{\"name\":\""<<name<<"\",\"passed\":true"
 <<",\"decisions\":"<<s.GetDecisions()<<",\"renders\":"<<s.GetRenderFrames()
 <<",\"physics_skips\":"<<s.GetPhysicsPrioritySkips()<<",\"cadence_skips\":"<<s.GetCadenceSkips()
 <<",\"starvation_frames\":"<<s.GetStarvationFrames()<<",\"dropped_slots\":"<<s.GetDroppedSlots()
 <<",\"epoch_resets\":"<<s.GetEpochResets()<<",\"max_lag_s\":"<<s.GetMaxLagSeconds()
 <<",\"max_render_gap_s\":"<<s.GetMaxObservedRenderGapSeconds()
 <<",\"wall_s\":"<<wall<<",\"sim_s\":"<<sim<<"}\n";
}
void NormalCadence(){
 Scheduler s;s.SetTargetHz(60.);int last=-100,threes=0,fours=0;
 for(int i=0;i<12000;++i){
  const double sim=double(i)*Step;
  auto d=s.Decide(100.+sim,100.+sim,Step,sim,1,true);
  Check(d.Active,"normal schedule inactive");
  if(d.Render){if(last>=0){Check(i-last>=3,"consecutive render catchup");if(i-last==3)++threes;if(i-last==4)++fours;}
   last=i;}
 }
 Check(s.GetRenderFrames()==3600,"60Hz render count on 200Hz world");
 Check(threes>0&&fours>0,"missing rational three/four tick cadence");
 Check(s.GetPhysicsPrioritySkips()==0&&s.GetStarvationFrames()==0,"normal rendering used overload fallback");
 Check(s.GetDecisions()==s.GetRenderFrames()+s.GetCadenceSkips()+s.GetPhysicsPrioritySkips(),"decision conservation");
 Row("normal_200hz_world_60hz_render",s,60.,60.);
}
void LatePhysicsAndStarvation(){
 Scheduler s;s.SetTargetHz(60.);
 Check(s.Decide(100.,99.,Step,0.,1,true).Why==Scheduler::Reason::PhysicsPriority,"late first decision drew");
 std::vector<double> draws;
 for(int i=1;i<=200;++i){
  const double now=100.+double(i)*Step;
  auto d=s.Decide(now,now-1.,Step,double(i)*Step,1,true);
  if(d.Render){Check(d.Why==Scheduler::Reason::Starvation,"late render not labelled starvation");draws.push_back(now);}
 }
 Check(draws.size()==4&&s.GetStarvationFrames()==4,"bounded four starvation renders per second");
 Check(s.GetPhysicsPrioritySkips()>190,"physics did not take priority");
 for(std::size_t i=1;i<draws.size();++i)Check(std::abs(draws[i]-draws[i-1]-.25)<1.e-8,"starvation cadence");
 Row("late_physics_with_250ms_interaction_fallback",s,1.,1.);
}
void DropExpiredSlots(){
 Scheduler s;s.SetTargetHz(60.);
 Check(s.Decide(100.,100.,Step,0.,1,true).Render,"first render");
 Check(!s.Decide(100.100,100.100,Step,.005,1,true).Render,"hitch rendered before full physical gap");
 Check(!s.Decide(100.105,100.105,Step,.010,1,true).Render,"hitch rendered after only two physical ticks");
 Check(s.Decide(100.110,100.110,Step,.015,1,true).Render,"current display slot missing");
 Check(s.GetDroppedSlots()==5,"expired slots not discarded exactly");
 Check(!s.Decide(100.115,100.115,Step,.020,1,true).Render,"first burst draw");
 Check(!s.Decide(100.120,100.120,Step,.025,1,true).Render,"second burst draw");
 Check(s.Decide(100.125,100.125,Step,.030,1,true).Render,"posthitch rational cadence did not resume");
 Row("discard_expired_slots_without_render_burst",s);
}
void ResetsAndFallbacks(){
 Scheduler s;s.SetTargetHz(60.);
 s.Decide(10.,10.,Step,4.,1,true);
 s.Decide(10.01,9.,Step,4.005,1,true);
 auto reset=s.Decide(20.,20.,Step,0.,2,true);
 Check(reset.Active&&reset.Render&&reset.LagSeconds==0.,"new generation inherited stale deadline");
 Check(s.GetEpochResets()==1&&s.GetGeneration()==2,"generation reset not recorded");
 auto pause=s.Decide(21.,20.,Step,.005,2,false);
 Check(!pause.Active&&pause.Render,"paused/nonpaced fallback blocked legacy path");
 Check(s.Decide(25.,25.,Step,.005,2,true).Render,"resume inherited rendering debt");
 s.Decide(25.01,25.01,Step,.01,2,true);
 Check(s.Decide(25.02,25.02,Step,0.,2,true).Render,"same-generation time reset inherited render phase");
 Check(s.GetEpochResets()==2,"time regression reset not counted");
 s.Decide(26.,25.,Step,0.,3,false); // R can invalidate the old pacer anchor for one frame.
 Check(s.Decide(26.005,26.005,Step,.005,3,true).Render,"reanchored R generation did not render");
 Check(s.GetEpochResets()==3&&s.GetGeneration()==3,"generation reset lost across no-anchor fallback");
 Row("generation_pause_resume_and_time_regression",s);
}
void InvalidInput(){
 Scheduler s;s.SetTargetHz(60.);
 const double nan=std::numeric_limits<double>::quiet_NaN();
 for(int i=0;i<6;++i){
  if(i==5)s.SetTargetHz(0.);
  auto d=s.Decide(i==0?nan:1.,i==1?nan:1.,i==2?0.:Step,i==3?nan:0.,1,i!=4);
  Check(!d.Active&&d.Render,"invalid/disabled schedule did not preserve engine default");
 }
 Check(s.GetDecisions()==0,"invalid input reached the active scheduler");
 Row("missing_anchor_invalid_clock_and_disabled_fallback",s);
}
void WorkSequence(const char* name,double world_cost,double draw_cost,bool hitches){
 Scheduler s;s.SetTargetHz(60.);
 double wall=0.,sim=0.;int last_draw_tick=-100;
 for(int i=0;i<12000;++i){
  wall=std::max(wall,sim); // The existing positive-remaining pacing wait.
  sim=double(i+1)*Step;  // Every complete world still advances exactly once.
  wall+=world_cost;
  if(hitches&&(i==999||i==2999||i==5999))wall+=.080;
  auto d=s.Decide(wall,sim,Step,sim,1,true);
  if(d.Render){
   Check(i-last_draw_tick>=3||d.Why==Scheduler::Reason::Starvation,"render burst in work replay");
   last_draw_tick=i;wall+=draw_cost;
  }
 }
 Check(s.GetDecisions()==12000,"work replay lost a world decision");
 Check(s.GetEpochResets()==0,"work replay reanchored an epoch");
 if(world_cost<Step){
  Check(std::abs(sim/wall-1.)<.001,"recoverable render/work hitches lost simulation time");
  Check(s.GetStarvationFrames()==0,"recoverable work used starvation fallback");
  if(hitches)Check(s.GetPhysicsPrioritySkips()>0&&s.GetDroppedSlots()>0,"hitch did not prioritize complete physics steps");
 }else{
  Check(sim/wall<.90&&s.GetMaxLagSeconds()>10.,"sustained overload was hidden by clock rewriting");
  Check(s.GetStarvationFrames()>0,"sustained overload lost interaction frames");
 }
 Row(name,s,wall,sim);
}
int main(){try{
 NormalCadence();LatePhysicsAndStarvation();DropExpiredSlots();ResetsAndFallbacks();InvalidInput();
 WorkSequence("under_budget_complete_world_loop",.002,.004,false);
 WorkSequence("recoverable_hitches_preserve_all_world_steps",.002,.004,true);
 WorkSequence("sustained_overload_stays_visible",.006,.004,false);
 return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}}
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--header", type=Path, default=HEADER)
    parser.add_argument("--cxx", default=shutil.which("g++") or shutil.which("clang++") or shutil.which("cl"))
    args = parser.parse_args()
    if not args.cxx:
        parser.error("A C++ compiler is required; pass --cxx.")
    header = args.header.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = output / "scheduler-contract.cpp"
    source.write_text(CASES, encoding="utf-8")
    binary = output / ("scheduler-contract.exe" if sys.platform == "win32" else "scheduler-contract")
    is_msvc = Path(args.cxx).stem.lower() == "cl"
    command = ([args.cxx, "/nologo", "/std:c++17", "/EHsc", "/W4", "/WX", "/I" + str(header.parent),
                str(source), "/Fe:" + str(binary), "/Fo:" + str(output / "scheduler-contract.obj")]
               if is_msvc else [args.cxx, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(header.parent),
                                str(source), "-o", str(binary)])
    compiled = subprocess.run(command, cwd=output, capture_output=True, text=True, timeout=30)
    (output / "compiler.log").write_text(compiled.stdout + compiled.stderr, encoding="utf-8")
    report = {"schema": "rmsim_native_render_scheduler_contract_v1", "header": str(header),
              "header_sha256": hashlib.sha256(header.read_bytes()).hexdigest(), "compiler_command": command,
              "compile_exit_code": compiled.returncode, "test_exit_code": None, "checks": [],
              "scope": "Production C++ scheduling helper with explicit work sequences; no engine or GPU performance claim."}
    if compiled.returncode == 0:
        run = subprocess.run([str(binary)], cwd=output, capture_output=True, text=True, timeout=10)
        (output / "test.log").write_text(run.stdout + run.stderr, encoding="utf-8")
        report["test_exit_code"] = run.returncode
        report["checks"] = [json.loads(line) for line in run.stdout.splitlines() if line.startswith("{")]
    report["passed"] = report["compile_exit_code"] == 0 and report["test_exit_code"] == 0 and len(report["checks"]) == 8
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]),
                      "report": str(output / "verification.json")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
