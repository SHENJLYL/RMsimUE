"""Drive the actual owned RViz controls; verify motion independently through ROS."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser();p.add_argument('--session',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--skip-single',action='store_true');p.add_argument('--goal-x',type=float,default=9.45);p.add_argument('--goal-y',type=float,default=6.4);args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    summary={'status':'running','scope':'real Windows RViz mouse/buttons plus independent native ROS observer','runs':[],'target':[args.goal_x,args.goal_y],'return_point':[5.8,6.4]}
    active=None
    def save():(args.output/'verification.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    def ui(action,**values):
        command=['powershell','-NoProfile','-File',str(ROOT/'tools/integration/Invoke-RvizConsoleUi.ps1'),'-SessionDirectory',str(args.session),'-Action',action]
        for key,value in values.items():command+=['-'+key,str(value)]
        result=subprocess.run(command,capture_output=True,text=True,errors='replace',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
        print(result.stdout.strip(),flush=True)
    def wait_file(path,timeout=150):
        end=time.monotonic()+timeout
        while not path.is_file():
            if active.poll() is not None:raise RuntimeError('Independent observer ended before UI readiness')
            if time.monotonic()>end:raise TimeoutError('Waiting for UI observer readiness')
            time.sleep(.1)
    try:
        for mode,target in [('single',(args.goal_x,args.goal_y)),('waypoints',(args.goal_x,args.goal_y)),('cancel',(5.8,6.4))]:
            if args.skip_single and mode=='single':continue
            out=args.output/mode
            with (args.output/(mode+'.log')).open('w',encoding='utf-8') as log:
                active=subprocess.Popen([sys.executable,str(ROOT/'tools/integration/observe_console_goal.py'),'--mode',mode,'--x',str(target[0]),'--y',str(target[1]),'--output',str(out)],stdout=log,stderr=subprocess.STDOUT)
                wait_file(out/'ready-for-ui.json')
                print('UI_TEST',mode,flush=True)
                ui('Capture',CaptureName=mode+'-view')
                if mode=='waypoints':
                    ui('Button',ButtonName='Waypoint / Nav Through Poses Mode',CaptureName='waypoint-mode')
                    ui('Goal',X=5.8,Y=6.4,CaptureName='waypoint-one')
                    ui('Goal',X=args.goal_x,Y=args.goal_y,CaptureName='waypoint-two')
                    ui('Button',ButtonName='Start Waypoint Following',CaptureName='waypoint-running')
                else:
                    ui('Goal',X=target[0],Y=target[1],CaptureName=mode+'-running')
                if mode=='cancel':
                    wait_file(out/'ready-to-cancel.json',90)
                    ui('Button',ButtonName='Cancel',CaptureName='cancelled')
                code=active.wait(timeout=330)
                result=json.loads((out/'verification.json').read_text())
                summary['runs'].append({'mode':mode,'status':result['status'],'checks':len(result['checks']),'path':str(out/'verification.json')})
                save()
                if code or result['status']!='passed':raise RuntimeError('GUI gate failed: '+mode+'; see '+str(out))
                print('UI_TEST_PASSED',mode,len(result['checks']),flush=True)
                time.sleep(1)
        summary['status']='passed';summary['checks']=sum(item['checks'] for item in summary['runs'])
    except Exception as error:
        summary.update(status='failed',error=repr(error));raise
    finally:
        if active is not None and active.poll() is None:active.terminate();active.wait(timeout=10)
        save()
    print('RViz UI acceptance PASS',summary['checks'],'checks',flush=True)

if __name__=='__main__':main()
