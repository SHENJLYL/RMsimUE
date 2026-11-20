"""Verify MuJoCo contact side ordering, including a compound-body CoM counterexample."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import mujoco
import numpy as np


CASES = {
    "vehicle_geom0_world_box": ("""<mujoco><option gravity="0 0 0"/><worldbody>
      <geom name="obstacle" type="box" pos="0 0 -.05" size="1 1 .05"/>
      <body name="vehicle" pos="0 0 .15"><freejoint/>
        <geom name="vehicle_contact" type="sphere" size=".2" mass="1"/>
      </body></worldbody></mujoco>""", 0, [0, 0, -1], False),
    "vehicle_geom1_world_plane": ("""<mujoco><option gravity="0 0 0"/><worldbody>
      <geom name="obstacle" type="plane" size="1 1 .05"/>
      <body name="vehicle" pos="0 0 .15"><freejoint/>
        <geom name="vehicle_contact" type="sphere" size=".2" mass="1"/>
      </body></worldbody></mujoco>""", 1, [0, 0, -1], False),
    "compound_body_com_outside_contact_hull": ("""<mujoco><option gravity="0 0 0"/><worldbody>
      <geom name="obstacle" type="sphere" pos="-.7 0 0" size=".2"/>
      <body name="vehicle"><freejoint/>
        <geom name="vehicle_contact" type="sphere" pos="-1 0 0" size=".2" mass="1"/>
        <geom name="vehicle_heavy_part" type="sphere" pos="1 0 0" size=".2" mass="99"/>
      </body></worldbody></mujoco>""", 1, [1, 0, 0], True),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"runtime": mujoco.mj_versionString(), "api_version": mujoco.mj_version(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "command": sys.argv, "cases": [], "passed": False}
    assert mujoco.mj_version() == 4000001
    polar = np.diag([1., -1., 1.])
    for name, (xml, expected_side, expected_mj, counterexample) in CASES.items():
        model = mujoco.MjModel.from_xml_string(xml)
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        assert data.ncon == 1, (name, data.ncon)
        contact = data.contact[0]
        vehicle = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "vehicle")
        side = next(i for i in range(2) if model.geom_bodyid[contact.geom[i]] == vehicle)
        normal_ue = polar @ contact.frame[:3]
        corrected = normal_ue * (1. if side == 0 else -1.)
        expected_ue = polar @ np.asarray(expected_mj, dtype=float)
        old = normal_ue.copy()
        toward_com = polar @ (data.xipos[vehicle] - contact.pos)
        if np.dot(old, toward_com) > 0:
            old *= -1
        force = np.zeros(6)
        mujoco.mj_contactForce(model, data, 0, force)
        record = {"case": name, "vehicle_side": side,
                  "geom_names": [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, int(g)) for g in contact.geom],
                  "contact_position_m": contact.pos.tolist(),
                  "vehicle_com_m": data.xipos[vehicle].tolist(),
                  "frame_normal_mujoco": contact.frame[:3].tolist(),
                  "expected_into_obstacle_ue": expected_ue.tolist(),
                  "corrected_into_obstacle_ue": corrected.tolist(),
                  "former_com_heuristic_ue": old.tolist(),
                  "normal_force_n": float(force[0]),
                  "original_heuristic_opposite": bool(np.dot(old, expected_ue) < -.999999)}
        assert side == expected_side, record
        assert np.dot(corrected, expected_ue) > .999999, record
        assert force[0] > 0, record
        assert not any(w.number for w in data.warning), record
        if counterexample:
            assert record["original_heuristic_opposite"], record
        record["passed"] = True
        report["cases"].append(record)
        (args.output / (name + ".xml")).write_text(xml, encoding="utf-8")
    report["passed"] = True
    (args.output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
