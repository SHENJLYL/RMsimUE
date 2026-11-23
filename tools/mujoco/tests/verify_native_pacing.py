"""Check the native absolute-deadline policy with source-bound offline work sequences."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
STEP = .005


def policy_source(source):
    method = source.split("void AImportLabGameMode::PacePhysicsFrame()", 1)[1].split(
        "void AImportLabGameMode::EndPlay", 1)[0]
    policy = method.split("        if(NextPhysicsFrameWall<0||NewEpoch)", 1)[1].split(
        "\n    }\n    else\n    {\n        // Preserve the existing Chaos pacing behavior.", 1)[0]
    policy = "if(NextPhysicsFrameWall<0||NewEpoch)" + policy
    normal = policy.split("        NextPhysicsFrameWall+=SimTime-MujocoPaceLastSimSeconds;", 1)[1]
    assert not re.search(r"NextPhysicsFrameWall\s*=", normal), "A late frame must retain its absolute deadline"
    assert policy.count("NextPhysicsFrameWall=Now;") == 1
    assert policy.count("++MujocoPaceAnchorCount") == 1
    assert "MujocoPaceLastSimSeconds=SimTime;" in normal
    assert "Now-MujocoPaceLastLagWarningWall>=LateWarningIntervalSeconds" in normal
    assert "MujocoPaceReanchoredWallSeconds" not in method
    assert "MaximumCatchupSeconds" not in method
    assert "for(double Remaining=NextPhysicsFrameWall-FPlatformTime::Seconds();Remaining>0;" in method
    assert "FPlatformProcess::SleepNoStats(Remaining>.001?float(Remaining-.0005):0.f);" in method
    assert "NextPhysicsFrameWall+=RealTimeStepSeconds;" in method  # Existing Chaos branch.
    assert "NextPhysicsFrameWall=-1;MujocoPaceLastSimSeconds=-1;" in method
    epoch = re.search(r"const bool NewEpoch=.*?;", method).group(0)
    assert epoch == "const bool NewEpoch=Generation!=MujocoPaceMotionGeneration||SimTime<MujocoPaceLastSimSeconds;"
    warning = re.search(r"constexpr double LateWarningSeconds=([^,]+),LateWarningIntervalSeconds=([^;]+);", normal)
    assert tuple(map(float, warning.groups())) == (.05, 1.)
    for token in ("mj_step(", "SetSimulationTime", "SetActorTransform(", "qpos[", "qvel["):
        assert token not in policy
    return policy, epoch


class Policy:
    """Algebraic reference for the inspected native decision; no clocks are altered."""
    def __init__(self):
        self.deadline = self.last_sim = -1.
        self.generation = self.anchors = self.warnings = 0
        self.last_warning = -1.
        self.lag = self.max_lag = 0.

    def advance(self, now, sim, generation=0):
        epoch = generation != self.generation or sim < self.last_sim
        if self.deadline < 0 or epoch:
            self.deadline, self.last_sim, self.generation = now, sim, generation
            self.lag, self.last_warning = 0., -1.
            self.anchors += 1
            return
        self.deadline += sim - self.last_sim
        self.last_sim = sim
        lag = now - self.deadline
        self.lag = max(0., lag)
        self.max_lag = max(self.max_lag, lag)
        if lag > .05 and (self.last_warning < 0 or now-self.last_warning >= 1.):
            self.last_warning = now
            self.warnings += 1


def result(name, policy, wall, sim, frames, fixed_work=False):
    return dict(name=name, wall_seconds=wall, sim_seconds=sim,
                raw_realtime_factor=sim/wall if fixed_work else None,
                anchors=policy.anchors, lag_warnings=policy.warnings,
                late_debt_s=policy.lag, max_lag_s=policy.max_lag,
                pacer_observations=frames, physical_frames=frames if fixed_work else None,
                native_substeps=frames*5 if fixed_work else None)


def work_sequence(name, base, hitches=False):
    policy = Policy()
    policy.advance(0., 0.)
    wall = sim = 0.
    for index in range(12000):
        work = .08 if hitches and index in (1999,3999,5999,7999,9999) else base
        wall += work
        sim += STEP
        policy.advance(wall, sim)
        wall = max(wall, policy.deadline)  # The unchanged positive-remaining wait.
    return result(name, policy, wall, sim, 12000, fixed_work=True)


def verify_replay():
    rows = [work_sequence("under_budget",.0046),
            work_sequence("transient_hitches",.0046,True),
            work_sequence("sustained_overbudget",.0052)]
    for row in rows[:2]:
        assert abs(row["wall_seconds"]-60.) < 1.e-8
        assert abs(row["sim_seconds"]-60.) < 1.e-8
        assert row["anchors"] == 1
    assert rows[1]["max_lag_s"] > .05 and rows[1]["lag_warnings"] == 5
    slow = rows[2]
    assert abs(slow["wall_seconds"]-62.4) < 1.e-8
    assert abs(slow["raw_realtime_factor"]-60/62.4) < 1.e-10
    assert abs(slow["late_debt_s"]-2.4) < 1.e-8
    assert slow["anchors"] == 1
    assert 0 < slow["lag_warnings"] <= math.ceil(slow["wall_seconds"])+1
    for generation, regressed, name in [(1,False,"generation_reset"),(0,True,"time_regression")]:
        p=Policy();p.advance(100.,1.)
        p.advance(100.2,1.1)
        assert p.lag > .05 and p.anchors == 1
        next_sim=0. if regressed else 1.1
        p.advance(100.201,next_sim,generation)
        assert p.anchors==2 and p.deadline==100.201 and p.lag==0.
        p.advance(100.2056,next_sim+.005,generation)
        assert abs(p.deadline-100.206)<1.e-10
        rows.append(result(name,p,max(100.2056,p.deadline),next_sim+.005,3))
    p=Policy();p.advance(10.,2.)
    p.advance(10.001,2.003)
    assert abs(p.deadline-10.003)<1.e-10
    p.advance(10.004,2.003)
    assert abs(p.deadline-10.003)<1.e-10 and abs(p.lag-.001)<1.e-10
    p.advance(10.007,2.010)
    assert abs(p.deadline-10.010)<1.e-10 and p.anchors==1
    rows.append(result("actual_solver_delta",p,max(10.007,p.deadline),2.010,3))
    return rows


def compile_actual_policy(policy, epoch, header, cxx, output):
    fields = [line.strip() for line in header.splitlines()
              if line.strip().startswith(("double RealTimeStepSeconds=", "double MujocoPace", "uint64 MujocoPace"))]
    assert len(fields) == 4, fields
    code = r"""
#include <algorithm>
#include <cstdint>
#include <iomanip>
#include <iostream>
using uint64=std::uint64_t;
struct FMath { static double Max(double a,double b){return std::max(a,b);} };
#define UE_LOG(...) ((void)0)
#define TEXT(value) value
struct Pacer {
FIELDS
 void Advance(double Now,double SimTime,uint64 Generation=0) {
  EPOCH
  auto FinishPaceTiming=[](){};
  POLICY
 }
};
void Print(const char* name,const Pacer& p,double wall,double sim,int steps) {
 std::cout<<std::setprecision(17)<<name<<" "<<wall<<" "<<sim<<" "
 <<p.MujocoPaceAnchorCount<<" "<<p.MujocoPaceLagWarningCount<<" "
 <<p.MujocoPaceCurrentLagSeconds<<" "<<p.MujocoPaceMaxLagSeconds<<" "<<steps<<"\n";
}
void Run(const char* name,double base,bool hitches) {
 Pacer p;double wall=0,sim=0;p.Advance(wall,sim);
 for(int i=0;i<12000;++i){
  bool hitch=hitches&&(i==1999||i==3999||i==5999||i==7999||i==9999);
  wall+=hitch?.08:base;sim+=.005;p.Advance(wall,sim);
  wall=std::max(wall,p.NextPhysicsFrameWall);
 }
 Print(name,p,wall,sim,12000);
}
int main(){
 Run("under_budget",.0046,false);Run("transient_hitches",.0046,true);Run("sustained_overbudget",.0052,false);
 for(int i=0;i<2;++i){
  Pacer p;p.Advance(100.,1.);p.Advance(100.2,1.1);
  double sim=i?0.:1.1;uint64 generation=i?0:1;
  p.Advance(100.201,sim,generation);p.Advance(100.2056,sim+.005,generation);
  Print(i?"time_regression":"generation_reset",p,std::max(100.2056,p.NextPhysicsFrameWall),sim+.005,3);
 }
 Pacer p;p.Advance(10.,2.);p.Advance(10.001,2.003);p.Advance(10.004,2.003);p.Advance(10.007,2.010);
 Print("actual_solver_delta",p,std::max(10.007,p.NextPhysicsFrameWall),2.010,3);
}
""".replace("FIELDS","\n".join(fields)).replace("EPOCH",epoch).replace("POLICY",policy)
    source=output/"extracted-policy.cpp";binary=output/"extracted-policy-test"
    source.write_text(code,encoding="utf-8")
    command=[cxx,"-std=c++17","-O2",str(source),"-o",str(binary)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=30)
    (output/"compiler.log").write_text(compiled.stdout+compiled.stderr,encoding="utf-8")
    assert compiled.returncode==0, compiled.stderr
    run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10,check=True)
    (output/"compiled-policy-output.txt").write_text(run.stdout,encoding="utf-8")
    parsed=[]
    for line in run.stdout.splitlines():
        name,wall,sim,anchors,warnings,lag,peak,frames=line.split()
        parsed.append(dict(name=name,wall_seconds=float(wall),sim_seconds=float(sim),
                           anchors=int(anchors),lag_warnings=int(warnings),
                           late_debt_s=float(lag),max_lag_s=float(peak),pacer_observations=int(frames)))
    for expected,actual in zip(verify_replay(),parsed,strict=True):
        for key,value in actual.items():
            if isinstance(value,float):assert abs(value-expected[key])<1.e-9,(key,value,expected[key])
            else:assert value==expected[key],(key,value,expected[key])
    return dict(status="passed",command=command,scope="Extracted actual C++ native decision and actual header fields; Unreal logging stubbed, unchanged wait represented by max(wall,deadline)",cases=parsed)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=ROOT/"unreal/RMSim/Source/RMSim/ImportLabGameMode.cpp")
    parser.add_argument("--header",type=Path,default=ROOT/"unreal/RMSim/Source/RMSim/ImportLabGameMode.h")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--cxx",help="Optional host C++ compiler for the extracted real policy; never builds UE")
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    source=args.source.read_text(encoding="utf-8-sig");header=args.header.read_text(encoding="utf-8-sig")
    policy,epoch=policy_source(source)
    rows=verify_replay()
    report=dict(status="passed",scope="Offline source contracts and synthetic work; no UE/ROS execution",
                sources=[dict(path=str(p.resolve()),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (args.source,args.header)],
                policy_sha256=hashlib.sha256(policy.encode()).hexdigest(),
                source_contracts="passed",cases=rows,step_seconds=STEP,native_substep_seconds=.001,
                production_thresholds_modified=False,clock_or_pose_override=False)
    if args.cxx:report["compiled_actual_policy"]=compile_actual_policy(policy,epoch,header,args.cxx,args.output)
    (args.output/"verification.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"cases":rows,"compiled_actual_policy":report.get("compiled_actual_policy",{}).get("status","not_requested"),"policy_sha256":report["policy_sha256"]},indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
