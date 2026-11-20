"""Validate native MuJoCo sensor sampling phases and body damping math.
This executes the pinned Python MuJoCo 4.0.1 core on small articulated/free-body
fixtures. It does not build/load the UE C++ wrapper or launch Unreal.
"""
from __future__ import annotations
import argparse, copy, datetime, hashlib, json, math, pathlib, sys
import mujoco
import numpy as np

FIXTURE = """<mujoco><option timestep=".001" integrator="implicitfast" gravity="0 0 -9.81"/>
<worldbody><geom type="plane" size="3 3 .1"/>
<body name="body" pos="0 0 .6"><freejoint name="chassis_free"/>
<inertial pos=".03 -.02 .02" mass="19.8" diaginertia=".4 .3 .2"/>
<geom type="box" size=".16 .12 .07"/>
<body name="yaw" pos=".05 .02 .16"><joint name="j_yaw" type="hinge" axis="0 0 1"/>
<inertial pos=".02 .01 .04" mass=".1" diaginertia=".002 .0015 .001"/>
<geom type="box" size=".05 .04 .03" pos=".02 .01 .04"/>
<site name="sensor" pos=".13 .08 .11" quat=".923879532511 0 -.382683432365 0"/>
<body name="pitch" pos=".02 0 .06"><joint name="j_pitch" axis="0 1 0"/>
<geom type="box" size=".04 .03 .02" mass=".1"/>
</body></body></body></worldbody>
<actuator><general joint="j_yaw" biastype="affine" biasprm="0 -8 -.8" forcerange="-8 8"/>
<general joint="j_pitch" biastype="affine" biasprm="0 -8 -.8" forcerange="-8 8"/></actuator></mujoco>"""
FREE = """<mujoco><option timestep=".001" integrator="implicitfast" gravity="0 0 0"/>
<worldbody><body name="body"><freejoint/><inertial pos=".12 -.05 .03"
quat=".923879532511 0 .382683432365 0" mass="3.7" diaginertia=".08 .12 .18"/>
<geom type="box" size=".1 .12 .13" contype="0" conaffinity="0"/>
</body></worldbody></mujoco>"""

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def state(model, data, body, scratch):
    jp, jr = scratch
    mujoco.mj_jacBody(model, data, jp, jr, body)
    return data.xpos[body].copy(), data.xquat[body].copy(), jp @ data.qvel, jr @ data.qvel

def contact_snapshot(model, data):
    forces=[]
    for i in range(data.ncon):
        f=np.zeros(6); mujoco.mj_contactForce(model,data,i,f); forces.append(f.copy())
    return data.contact.pos.copy(),data.contact.frame.copy(),np.asarray(forces),data.qacc.copy()

def slerp(a,b,t):
    b=b.copy();d=float(np.dot(a,b))
    if d<0: b=-b;d=-d
    if d>.9995:
        q=a+t*(b-a); return q/np.linalg.norm(q)
    theta=math.acos(min(1.,d))
    return (math.sin((1-t)*theta)*a+math.sin(t*theta)*b)/math.sin(theta)

def reference_sample(history,t,parent):
    # Independent sorted reference, not the runtime's ring/cache implementation.
    times=np.asarray([row[0] for row in history])
    if not math.isfinite(t) or t<times[0] or t>times[-1]: return None
    lower=int(np.searchsorted(times,t,side="right"))-1
    upper=min(lower+1,len(history)-1)
    span=times[upper]-times[lower];a=(t-times[lower])/span if span else 0.
    pa,qa,va,wa=history[lower][1][parent]
    pb,qb,vb,wb=history[upper][1][parent]
    return pa+a*(pb-pa),slerp(qa,qb,a),va+a*(vb-va),wa+a*(wb-wa)

def reset_articulated(model,data,yaw=.73,pitch=-.24):
    mujoco.mj_resetData(model,data)
    q=np.array([.91,.17,-.22,.29]);q/=np.linalg.norm(q)
    data.qpos[:7]=[.3,-.2,.6,*q]
    data.qpos[7:]=[yaw,pitch]
    data.qvel[:]=[1.2,-.3,.2,.6,-.4,1.1,1.7,-.8]
    mujoco.mj_forward(model,data)

def phase_test():
    m=mujoco.MjModel.from_xml_string(FIXTURE)
    baseline=mujoco.MjData(m);reset_articulated(m,baseline)
    actual=copy.copy(baseline)
    bodies=[m.body("body").id,m.body("yaw").id]
    scratch=(np.zeros((3,m.nv)),np.zeros((3,m.nv)))
    history=[(actual.time,[state(m,actual,b,scratch) for b in bodies])]
    max_pose=max_vel=max_traj=max_lever=max_contact_change=0.
    stale_pose=stale_velocity=0.;contact_frames=0
    for step in range(1200):
        ctrl=np.array([8*(.73+.4*math.sin(step*.002)),8*(-.24+.1*math.sin(step*.001))])
        baseline.ctrl[:]=ctrl;actual.ctrl[:]=ctrl
        mujoco.mj_step(m,baseline);mujoco.mj_step(m,actual)
        old_poses=actual.xpos[bodies].copy()
        old_vel=np.zeros(6);mujoco.mj_objectVelocity(m,actual,mujoco.mjtObj.mjOBJ_BODY,bodies[1],old_vel,0)
        contacts=contact_snapshot(m,actual);contact_frames+=actual.ncon>0
        qpos=actual.qpos.copy();qvel=actual.qvel.copy();ctrl_before=actual.ctrl.copy();time=actual.time
        mujoco.mj_kinematics(m,actual);mujoco.mj_comPos(m,actual)
        assert np.array_equal(qpos,actual.qpos) and np.array_equal(qvel,actual.qvel)
        assert np.array_equal(ctrl_before,actual.ctrl) and time==actual.time
        after=contact_snapshot(m,actual)
        for a,b in zip(contacts,after):
            assert a.shape==b.shape
            if a.size:max_contact_change=max(max_contact_change,float(np.max(np.abs(a-b))))
        oracle=copy.copy(actual);mujoco.mj_forward(m,oracle)
        for field in ["xpos","xquat","xipos","cdof"]:
            max_pose=max(max_pose,float(np.max(np.abs(getattr(actual,field)-getattr(oracle,field)))))
        samples=[]
        for b in bodies:
            s=state(m,actual,b,scratch);o=state(m,oracle,b,scratch)
            max_vel=max(max_vel,float(np.max(np.abs(s[2]-o[2]))),float(np.max(np.abs(s[3]-o[3]))))
            samples.append(s)
        history.append((actual.time,samples))
        stale_pose=max(stale_pose,float(np.max(np.abs(old_poses-actual.xpos[bodies]))))
        stale_velocity=max(stale_velocity,float(np.max(np.abs(old_vel[:3]-samples[1][3]))))
        jp=np.zeros((3,m.nv));jr=np.zeros((3,m.nv))
        mujoco.mj_jacSite(m,actual,jp,jr,m.site("sensor").id)
        parent=samples[1]
        lever=actual.site_xpos[m.site("sensor").id]-parent[0]
        predicted=parent[2]+np.cross(parent[3],lever)
        max_lever=max(max_lever,float(np.max(np.abs(predicted-jp@actual.qvel))))
        if (step+1)%5==0:
            mujoco.mj_forward(m,baseline);mujoco.mj_forward(m,actual)
        max_traj=max(max_traj,float(np.max(np.abs(actual.qpos-baseline.qpos))),
                     float(np.max(np.abs(actual.qvel-baseline.qvel))))
    assert contact_frames>0
    assert max_contact_change==0.
    assert max_pose<1e-12 and max_vel<1e-11 and max_lever<1e-11 and max_traj<1e-12
    assert stale_pose>1e-5 and stale_velocity>1e-5
    initial=reference_sample(history,0.,1);latest=reference_sample(history,history[-1][0],1)
    assert initial is not None and latest is not None
    assert np.array_equal(initial[0],history[0][1][1][0])
    assert np.array_equal(latest[0],history[-1][1][1][0])
    for t in np.arange(.005,.010,5e-6):
        sample=reference_sample(history,float(t),1)
        assert sample is not None and abs(np.linalg.norm(sample[1])-1)<1e-12
    assert reference_sample(history,-5e-6,1) is None
    assert reference_sample(history,history[-1][0]+5e-6,1) is None
    old_final=history[-1][0]
    reset_articulated(m,actual,-.61,.19)
    history=[(actual.time,[state(m,actual,b,scratch) for b in bodies])]
    assert len(history)==1 and history[0][0]==0.
    assert reference_sample(history,old_final,1) is None
    assert np.linalg.norm(history[0][1][1][0]-initial[0])<1e-12
    assert abs(np.dot(history[0][1][1][1],initial[1]))<.99
    return dict(native_steps=1200,contact_frames=contact_frames,maximum_fresh_pose_error=max_pose,
        maximum_origin_velocity_error=max_vel,maximum_lever_arm_velocity_error=max_lever,
        maximum_solved_contact_change=max_contact_change,maximum_trajectory_change=max_traj,
        observed_stale_pose_error=stale_pose,observed_stale_angular_velocity_error=stale_velocity,
        interpolation_period_s=5e-6,history_node_period_s=.001,
        initial_nonzero_yaw_and_pitch=True,exact_endpoints_and_reset_epoch_checked=True)

def wrench(model,data,body,linear,angular,scratch):
    pos,quat,velocity,omega=state(model,data,body,scratch)
    vcom=velocity+np.cross(omega,data.xipos[body]-pos)
    rotation=data.ximat[body].reshape(3,3)
    momentum=rotation@(model.body_inertia[body]*(rotation.T@omega))
    force=-model.body_mass[body]*linear*vcom
    torque=-angular*momentum
    power=-float(force@vcom+torque@omega)
    return np.r_[force,torque],vcom,omega,power

def damping_case(linear,angular,spin):
    # COM at the body origin gives an exact isolated decay oracle.
    m=mujoco.MjModel.from_xml_string(FREE.replace('pos=".12 -.05 .03"','pos="0 0 0"'));d=mujoco.MjData(m);body=m.body("body").id
    q=np.array([.87,.22,-.16,.39]);q/=np.linalg.norm(q);d.qpos[3:7]=q
    mujoco.mj_forward(m,d)
    v=np.array([1.2,-.8,.4]) if linear else np.zeros(3)
    omega=d.ximat[body].reshape(3,3)[:,0]*spin
    r=d.xipos[body]-d.xpos[body]
    d.qvel[:3]=v-np.cross(omega,r)
    d.qvel[3:6]=d.xmat[body].reshape(3,3).T@omega
    mujoco.mj_forward(m,d)
    scratch=(np.zeros((3,m.nv)),np.zeros((3,m.nv)))
    initial=wrench(m,d,body,linear,angular,scratch)
    energies=[];powers=[]
    for step in range(1000):
        wr,vcom,w,power=wrench(m,d,body,linear,angular,scratch)
        saved=d.xfrc_applied.copy();d.xfrc_applied[body]+=wr
        powers.append(power)
        rot=d.ximat[body].reshape(3,3);momentum=rot@(m.body_inertia[body]*(rot.T@w))
        energies.append(.5*m.body_mass[body]*float(vcom@vcom)+.5*float(w@momentum))
        mujoco.mj_step(m,d);d.xfrc_applied[:]=saved
        assert np.array_equal(d.xfrc_applied,saved)
        mujoco.mj_kinematics(m,d);mujoco.mj_comPos(m,d)
    final=wrench(m,d,body,linear,angular,scratch)
    expected_v=initial[1]*(1-linear*.001)**1000
    expected_w=initial[2]*(1-angular*.001)**1000
    verr=float(np.max(np.abs(final[1]-expected_v)))
    werr=float(np.max(np.abs(final[2]-expected_w)))
    assert min(powers)>=-1e-12 and max(np.diff(energies))<1e-10
    assert verr<2e-7 and werr<2e-7,(linear,angular,verr,werr)
    if not linear:assert np.max(np.abs(final[1]))<2e-7
    return dict(linear_per_s=linear,angular_per_s=angular,steps=1000,
        linear_decay_error=verr,angular_decay_error=werr,
        initial_energy_j=energies[0],final_energy_j=energies[-1],minimum_dissipation_w=min(powers),
        COM_at_body_origin_for_exact_decay=True,rotated_principal_inertia=True)


def damping_wrench_test():
    m=mujoco.MjModel.from_xml_string(FREE);d=mujoco.MjData(m);body=m.body("body").id
    q=np.array([.87,.22,-.16,.39]);q/=np.linalg.norm(q);d.qpos[3:7]=q
    d.qvel[:]=[1.4,.1,-.3,.8,-1.2,2.3]
    mujoco.mj_forward(m,d)
    scratch=(np.zeros((3,m.nv)),np.zeros((3,m.nv)))
    wr,vcom,omega,power=wrench(m,d,body,.05,.01,scratch)
    origin,quat,velocity,w=state(m,d,body,scratch)
    polar=np.diag([1.,-1.,1.]);axial=-polar
    vcom_ue=polar@velocity*100+np.cross(axial@w,polar@(d.xipos[body]-origin)*100)
    converted=polar@vcom_ue/100
    conversion_error=float(np.max(np.abs(converted-vcom)))
    base=copy.copy(d);drag=copy.copy(d)
    drag.xfrc_applied[body]+=wr
    mujoco.mj_forward(m,base);mujoco.mj_forward(m,drag)
    jp=np.zeros((3,m.nv));jr=np.zeros((3,m.nv));mujoco.mj_jacBodyCom(m,d,jp,jr,body)
    delta=drag.qacc-base.qacc
    linear_error=float(np.max(np.abs(jp@delta+.05*vcom)))
    angular_error=float(np.max(np.abs(jr@delta+.01*omega)))
    assert conversion_error<1e-12 and linear_error<1e-12 and angular_error<1e-12 and power>0
    sentinel=np.array([.2,-.1,.3,.01,.02,-.03])
    d.xfrc_applied[body]=sentinel
    original=d.xfrc_applied.copy();d.xfrc_applied[body]+=wr
    mujoco.mj_step(m,d);d.xfrc_applied[:]=original
    assert np.array_equal(original,d.xfrc_applied)
    mujoco.mj_kinematics(m,d);mujoco.mj_comPos(m,d)
    cached=state(m,d,body,scratch);qpos=d.qpos.copy();qvel=d.qvel.copy()
    current_drag=wrench(m,d,body,.05,.01,scratch)[0]
    d.xfrc_applied[body]+=current_drag
    mujoco.mj_forward(m,d)
    qacc=d.qacc.copy();d.xfrc_applied[:]=original
    refreshed=state(m,d,body,scratch)
    assert np.array_equal(qpos,d.qpos) and np.array_equal(qvel,d.qvel)
    assert all(np.array_equal(a,b) for a,b in zip(cached,refreshed))
    assert np.array_equal(original,d.xfrc_applied)
    oracle=copy.copy(d);oracle.xfrc_applied[body]+=current_drag;mujoco.mj_forward(m,oracle)
    assert np.array_equal(qacc,oracle.qacc)
    return dict(COM_offset_m=m.body_ipos[body].tolist(),nonprincipal_world_angular_velocity=True,
        linear_acceleration_error=linear_error,angular_acceleration_error=angular_error,
        ROS_UE_lever_arm_conversion_error=conversion_error,dissipation_w=power,
        applied_external_wrench_restored_exactly=True,
        final_forward_uses_current_drag_and_preserves_body_cache=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=pathlib.Path,required=True);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    result={"scope":"Pinned MuJoCo core numerical fixtures; UE C++ wrapper compilation/runtime not covered",
        "created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mujoco_version":mujoco.mj_versionString(),"command":sys.argv,"checks":{}}
    try:
        assert mujoco.mj_versionString()=="4.0.1"
        result["checks"]["sensor_phase"]=phase_test()
        result["checks"]["damping_offset_wrench"]=damping_wrench_test()
        result["checks"]["damping_linear"]=damping_case(.05,0.,0.)
        result["checks"]["damping_angular"]=damping_case(0.,.05,3.)
        result["checks"]["damping_wheel_angular"]=damping_case(0.,.01,3.)
        result["checks"]["damping_combined"]=damping_case(.05,.05,3.)
        result["status"]="passed"
    except Exception as exc:
        import traceback
        result["status"]="failed";result["error"]=repr(exc);result["traceback"]=traceback.format_exc()
    root=pathlib.Path(__file__).resolve().parents[3]
    result["source_hashes"]={str(path.relative_to(root)):sha(path) for path in [
        pathlib.Path(__file__).resolve(),root/"unreal/RMSim/Source/RMSim/MujocoVehicleDynamics.cpp",
        root/"unreal/RMSim/Source/RMSim/MujocoVehicleDynamics.h",
        root/"unreal/RMSim/Source/RMSim/VehicleMujocoPhysics.cpp",root/"unreal/RMSim/Source/RMSim/VehicleRig.h"]}
    (args.output/"verification.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2))
    return 0 if result["status"]=="passed" else 2

if __name__=="__main__":
    raise SystemExit(main())
