"""Author the RMUC campus gym from metre-scale modular architecture.

This is an original, deterministic layout. The seats referenced by ``seat_row``
are the separately licensed CC0 six-seat source derivative without its riser;
continuous concrete steps and usable aisle openings are authored here.

Coordinates are ROS right-handed: X/Y horizontal, Z up. Rotations are intrinsic
roll/pitch/yaw degrees (Rz(yaw) Ry(pitch) Rx(roll)). Seat rows face local -Y.
The runtime owns all physical floor, glass enclosure and fixed decorative doors.
These spectator/ceiling instances are visual-only and do not affect ROS scans.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def create_layout() -> dict:
    groups: dict[tuple, dict] = {}
    labels: list[dict] = []

    def add(group: str, material: str, center, size, *, rotation=(0, 0, 0),
            mesh="cube", shadow=False):
        key = (group, mesh, material, shadow)
        target = groups.setdefault(key, {
            "id": group, "mesh": mesh, "material": material,
            "cast_shadow": shadow, "collision": False, "instances": [],
        })
        target["instances"].append({
            "center": [round(float(v), 6) for v in center],
            "size": [round(float(v), 6) for v in size],
            "rotation_deg": [round(float(v), 6) for v in rotation],
        })

    def bar(group, material, start, end, diameter=.045, *, shadow=False):
        delta = [b-a for a, b in zip(start, end)]
        length = math.sqrt(sum(v*v for v in delta))
        pitch = math.degrees(math.acos(max(-1., min(1., delta[2]/length))))
        yaw = math.degrees(math.atan2(delta[1], delta[0]))
        add(group, material, [(a+b)*.5 for a, b in zip(start, end)],
            [diameter, diameter, length], rotation=[0, pitch, yaw],
            mesh="cylinder", shadow=shadow)

    # Floor skins stop outside the unmodified RMUC field. Root supplies the remaining visual floor outside the original field.
    # Continuous closed barriers keep robots inside the original CAD map.
    for sign in (-1, 1):
        add("ParquetApron", "wood_floor", [0, sign*9.925, .008], [37.8, 3.55, .016])
        add("ParquetApron", "wood_floor", [sign*16.525, 0, .008], [4.75, 16.3, .016])
        add("RubberCirculation", "rubber", [0, sign*11.30, .014], [40.0, .70, .012])
        add("RubberCirculation", "rubber", [sign*19.35, 0, .014], [.70, 22.6, .012])
        add("CourtPerimeterLines", "court_line", [0, sign*10.85, .022], [37.0, .055, .008])
        add("CourtPerimeterLines", "court_line", [sign*18.5, 0, .022], [.055, 21.7, .008])

    # A complete enclosed hall, 56 x 40 metres clear inside and 14 metres high.
    # The lower end-wall doors occupy a real opening in the wall segments.
    for sign in (-1, 1):
        y = sign*20.14
        add("LowerMasonry", "concrete", [0, y, 2.25], [56.6, .28, 4.5], shadow=True)
        add("AcousticWallBand", "acoustic_wood", [0, sign*19.965, 5.15], [55.6, .055, 1.3])
        add("UpperPlaster", "plaster", [0, y, 12.4], [56.6, .28, 3.2], shadow=True)
        add("WindowSills", "steel_light", [0, sign*19.95, 5.88], [55.6, .34, .24])
        add("WindowSills", "steel_light", [0, sign*19.95, 10.82], [55.6, .28, .16])
        for i in range(16):
            x = -26.25 + i*3.5
            add("ClerestoryGlass", "glass", [x, sign*20.02, 8.35], [3.43, .018, 4.82])
            add("WindowMullions", "steel_light", [x-1.75, sign*19.99, 8.35], [.075, .14, 4.9])
            add("WindowMullions", "steel_light", [x, sign*19.99, 8.35], [3.5, .10, .07])
        add("WindowMullions", "steel_light", [28, sign*19.99, 8.35], [.075, .14, 4.9])
        x = sign*28.14
        for ys in (-1, 1):
            add("LowerMasonry", "concrete", [x, ys*11.0, 2.25], [.28, 18.0, 4.5], shadow=True)
        add("LowerMasonry", "concrete", [x, 0, 3.9], [.28, 4., 1.2], shadow=True)
        add("AcousticWallBand", "acoustic_wood", [sign*27.965, 0, 5.15], [.055, 39.6, 1.3])
        add("UpperPlaster", "plaster", [x, 0, 12.4], [.28, 40.6, 3.2], shadow=True)
        add("WindowSills", "steel_light", [sign*27.95, 0, 5.88], [.34, 39.8, .24])
        add("WindowSills", "steel_light", [sign*27.95, 0, 10.82], [.28, 39.8, .16])
        for i in range(10):
            y = -18.0 + i*4.
            add("ClerestoryGlass", "glass", [sign*28.02, y, 8.35], [.018, 3.93, 4.82])
            add("WindowMullions", "steel_light", [sign*27.99, y-2., 8.35], [.14, .075, 4.9])
            add("WindowMullions", "steel_light", [sign*27.99, y, 8.35], [.10, 4., .07])
        add("WindowMullions", "steel_light", [sign*27.99, 20, 8.35], [.14, .075, 4.9])
        # Service entrance: two closed architectural doors and glazed lites.
        for leaf in (-1, 1):
            add("BuildingDoors", "steel", [sign*28.005, leaf*.965, 1.60], [.065, 1.89, 3.18])
            add("ClerestoryGlass", "glass", [sign*27.958, leaf*.965, 2.12], [.015, 1.57, 1.63])
            add("WindowMullions", "steel_light", [sign*27.91, leaf*.965, 1.24], [.045, 1.42, .045])
        add("ExitSigns", "emissive", [sign*27.88, 0, 3.58], [.08, .72, .24])
        labels.append({"text": "EXIT", "center": [sign*27.82, 0, 3.58],
                       "facing": [-sign, 0, 0], "height_m": .13, "color": [0.10, .40, .17]})

    add("RoofDeck", "plaster", [0, 0, 14.12], [56.6, 40.6, .30], shadow=True)
    for x in (-24., -16., -8., 0., 8., 16., 24.):
        for sign in (-1, 1):
            add("StructuralColumns", "concrete", [x, sign*19.57, 6.9], [.60, .78, 13.8], shadow=True)
            add("ColumnCapitals", "steel_light", [x, sign*19.48, 12.98], [1.0, 1.12, .34], shadow=True)
        # Flat parallel-chord steel trusses. All diagonals meet chord nodes;
        # each alternating web triangle has a credible 3.2 metre structural bay.
        bar("RoofTrusses", "steel", [x, -19.2, 12.13], [x, 19.2, 12.13], .20, shadow=True)
        bar("RoofTrusses", "steel", [x, -19.2, 13.55], [x, 19.2, 13.55], .20, shadow=True)
        for bay in range(12):
            y0 = -19.2+bay*3.2
            y1 = y0+3.2
            a, b = (12.13, 13.55) if bay%2 == 0 else (13.55, 12.13)
            bar("RoofTrusses", "steel", [x, y0, a], [x, y1, b], .10, shadow=True)
        for y in (-19.2, -12.8, -6.4, 0., 6.4, 12.8, 19.2):
            bar("RoofTrusses", "steel", [x, y, 12.13], [x, y, 13.55], .09, shadow=True)
    for y in (-18., -14.4, -10.8, -7.2, -3.6, 0., 3.6, 7.2, 10.8, 14.4, 18.):
        add("RoofPurlins", "steel_light", [0, y, 13.76], [55.6, .10, .24], shadow=True)
    for x0 in (-24., -8., 8.):
        for y0 in (-18., 10.8):
            bar("RoofTrusses", "steel", [x0, y0, 13.6], [x0+8., y0+7.2, 13.6], .065)
            bar("RoofTrusses", "steel", [x0+8., y0, 13.6], [x0, y0+7.2, 13.6], .065)

    # Acoustic blades are deliberately thick enough to read at robot-eye
    # distance. Gaps between the three banks expose the actual roof structure.
    for x0 in (-16., 0., 16.):
        for i in range(19):
            y = -16.2 + i*1.8
            add("AcousticCeilingBlades", "acoustic_wood", [x0, y, 11.57], [11.6, .065, .75], shadow=True)
            for x in (x0-4.8, x0+4.8):
                bar("CeilingHangers", "steel_light", [x, y, 11.945], [x, y, 13.7], .018)

    # Spectator stands: four long-side bays separated by genuine 1.4m aisles.
    # Every tread has exactly 0.4m rise and 0.8m going, with 0.2m aisle substeps.
    long_bays = [(-19.5, -10.5), (-9.1, -.7), (.7, 9.1), (10.5, 19.5)]
    long_aisles = [(-10.5, -9.1), (-.7, .7), (9.1, 10.5)]
    end_bays = [(-8.7, -.7), (.7, 8.7)]
    seat_count = 0
    seat_row_size = [2.9080137014, .4000108838, .7500060201]

    def place_bank(axis, sign, front, bays, aisle_ranges):
        nonlocal seat_count
        for row in range(6):
            deck = .4*(row+1)
            depth = front + .8*row
            for lo, hi in bays:
                middle = (lo+hi)*.5
                # Each step is supported to floor; there are no floating tiers.
                if axis == "y":
                    center, size = [middle, sign*(depth+.4), deck*.5], [hi-lo, .8, deck]
                else:
                    center, size = [sign*(depth+.4), middle, deck*.5], [.8, hi-lo, deck]
                add("StandRisers", "concrete", center, size, shadow=True)
                width = hi-lo
                count = max(1, int((width-.28)//3.05))
                spacing = 3.10
                for part in range(count):
                    lateral = middle + (part-(count-1)*.5)*spacing
                    backset = depth+.55
                    yaw = (0 if sign>0 else 180) if axis=="y" else (-90 if sign>0 else 90)
                    c = [lateral, sign*backset, deck+.375003] if axis=="y" else [sign*backset, lateral, deck+.375003]
                    add("CC0SpectatorSeats", "source", c, seat_row_size,
                        mesh="seat_row", rotation=[0, 0, yaw])
                    seat_count += 6
            for lo, hi in aisle_ranges:
                mid = (lo+hi)*.5
                for sub in range(2):
                    step_top = row*.4+(sub+1)*.2
                    d = depth+sub*.4+.2
                    c = [mid, sign*d, step_top*.5] if axis=="y" else [sign*d, mid, step_top*.5]
                    s = [hi-lo, .4, step_top] if axis=="y" else [.4, hi-lo, step_top]
                    add("StairAisles", "concrete", c, s, shadow=True)
                    nosing_c = [mid, sign*(d-.17), step_top+.008] if axis=="y" else [sign*(d-.17), mid, step_top+.008]
                    nosing_s = [hi-lo-.06, .045, .012] if axis=="y" else [.045, hi-lo-.06, .012]
                    add("StairNosings", "court_line", nosing_c, nosing_s)
                # Shared central rail leaves two pedestrian lanes, not solid
                # fencing across the stair opening. Rail follows actual slope.
                start = [mid, sign*depth, row*.4+1.03] if axis=="y" else [sign*depth, mid, row*.4+1.03]
                end = [mid, sign*(depth+.8), (row+1)*.4+1.03] if axis=="y" else [sign*(depth+.8), mid, (row+1)*.4+1.03]
                bar("SpectatorHandrails", "steel_light", start, end, .042)
                if row%2 == 0:
                    foot = [mid, sign*(depth+.4), row*.4+.4] if axis=="y" else [sign*(depth+.4), mid, row*.4+.4]
                    top = [mid, sign*(depth+.4), row*.4+1.23] if axis=="y" else [sign*(depth+.4), mid, row*.4+1.23]
                    bar("SpectatorHandrails", "steel_light", foot, top, .038)
        # Raised rear circulation is connected to every aisle, and sits behind
        # the sixth riser. Its perimeter guard reads clearly from the field.
        extent = max(abs(v) for pair in bays for v in pair)
        behind = front+4.8
        c = [0, sign*(behind+.85), 1.2] if axis=="y" else [sign*(behind+.85), 0, 1.2]
        s = [2*extent, 1.7, 2.4] if axis=="y" else [1.7, 2*extent, 2.4]
        add("StandRisers", "concrete", c, s, shadow=True)
        for z in (2.65, 3.42):
            a = [-extent, sign*(behind+1.67), z] if axis=="y" else [sign*(behind+1.67), -extent, z]
            b = [extent, sign*(behind+1.67), z] if axis=="y" else [sign*(behind+1.67), extent, z]
            bar("SpectatorHandrails", "steel_light", a, b, .042)
        for i in range(int(2*extent/1.5)+1):
            lateral = -extent+i*(2*extent/int(2*extent/1.5))
            a = [lateral, sign*(behind+1.67), 2.4] if axis=="y" else [sign*(behind+1.67), lateral, 2.4]
            b = [lateral, sign*(behind+1.67), 3.42] if axis=="y" else [sign*(behind+1.67), lateral, 3.42]
            bar("SpectatorHandrails", "steel_light", a, b, .038)

    for sign in (-1, 1):
        place_bank("y", sign, 11.8, long_bays, long_aisles)
        place_bank("x", sign, 20.0, end_bays, [(-.7, .7)])

    # A front parapet and rail border each seating bay, with gaps left open for
    # the stair lanes. This also gives a useful foreground depth cue in the hall.
    for sign in (-1, 1):
        for lo, hi in long_bays:
            add("StandFrontParapet", "concrete", [(lo+hi)*.5, sign*11.70, .31], [hi-lo, .18, .62], shadow=True)
            for z in (.82, 1.15):
                bar("SpectatorHandrails", "steel_light", [lo, sign*11.69, z], [hi, sign*11.69, z])
            for x in (lo, (lo+hi)*.5, hi):
                bar("SpectatorHandrails", "steel_light", [x, sign*11.69, .62], [x, sign*11.69, 1.15], .038)
        for lo, hi in end_bays:
            add("StandFrontParapet", "concrete", [sign*19.90, (lo+hi)*.5, .31], [.18, hi-lo, .62], shadow=True)
            for z in (.82, 1.15):
                bar("SpectatorHandrails", "steel_light", [sign*19.89, lo, z], [sign*19.89, hi, z])
            for y in (lo, (lo+hi)*.5, hi):
                bar("SpectatorHandrails", "steel_light", [sign*19.89, y, .62], [sign*19.89, y, 1.15], .038)

    # Realistic linear high-bay LED housings, hung in four rows. Visible lenses
    # are paired with only twelve actual area lights, set separately below.
    for x in (-16., 0., 16.):
        for y in (-13., -5., 5., 13.):
            add("LightFixtures", "steel", [x, y, 10.84], [2.2, .34, .14])
            add("LightLenses", "emissive", [x, y, 10.762], [2.02, .28, .018])
            for dx in (-.8, .8):
                bar("CeilingHangers", "steel_light", [x+dx, y, 10.91], [x+dx, y, 12.05], .016)

    # Two modest wall scoreboards; 7-segment strokes are geometry, so no
    # unlicensed logos or external fonts are required by the package.
    digits = {
        "0": "abcedf", "1": "bc", "2": "abged", "3": "abgcd",
        "4": "fgbc", "5": "afgcd", "6": "afgecd", "7": "abc",
        "8": "abcdefg", "9": "abfgcd",
    }
    segments = {
        "a": (0., .34, .23, .045), "g": (0., 0., .23, .045), "d": (0., -.34, .23, .045),
        "f": (-.135, .17, .045, .26), "b": (.135, .17, .045, .26),
        "e": (-.135, -.17, .045, .26), "c": (.135, -.17, .045, .26),
    }
    for sign in (-1, 1):
        add("ScoreboardCases", "steel", [sign*27.66, 0, 7.32], [.32, 5.4, 2.75], shadow=True)
        add("ScoreboardFaces", "scoreboard", [sign*27.482, 0, 7.32], [.025, 5.16, 2.51])
        for index, digit in enumerate("0500005"):
            lateral = (index-3)*.55
            for segment in digits[digit]:
                dy, dz, width, height = segments[segment]
                add("ScoreboardDigits", "emissive", [sign*27.462, lateral+dy, 7.26+dz], [.014, width, height])

    # Warm-neutral LED illumination. The runtime may reduce shadowed lights
    # using its quality policy while preserving the visible fixture placement.
    lights = []
    for x in (-16., 0., 16.):
        for y in (-13., -5., 5., 13.):
            lights.append({"center": [x, y, 10.7], "rotation_deg": [0, 90, 0],
                           "color_kelvin": 4300, "intensity_lumens": 21000,
                           "width_m": 2.0, "height_m": .30,
                           "attenuation_m": 25., "cast_shadows": y in (-5., 5.)})

    # Runtime preview viewpoints face the interior. They stay under the roof;
    # ordinary overhead screenshots would otherwise photograph the roof skin.
    views = [
        {"id": "interior_hero", "position": [23.5, -17.8, 7.8], "look_at": [-1.5, 1.4, 2.8], "fov": 78},
        {"id": "opposite_corner", "position": [-23.6, 17.6, 6.6], "look_at": [1.0, -1.0, 2.3], "fov": 78},
        {"id": "robot_eye", "position": [5.8, 6.4, .65], "look_at": [0., -12., 4.0], "fov": 90},
    ]
    # The original RMUC edge surface is at -0.21m. All architectural ground,
    # steps, service doors and columns use the same datum, avoiding a floating
    # apron or a hidden 21cm step outside the ornamental field gate.
    floor_z = -.21
    for group in groups.values():
        for instance in group["instances"]:
            instance["center"][2] = round(instance["center"][2] + floor_z, 6)
    for light in lights:
        light["center"][2] += floor_z
    for label in labels:
        label["center"][2] += floor_z
    for view in views:
        view["position"][2] += floor_z
        view["look_at"][2] += floor_z
    result = {
        "schema": "rmsim_campus_gym_layout_v1", "units": "m",
        "coordinates": "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP",
        "rotation_convention": "intrinsic roll,pitch,yaw degrees; Rz*Ry*Rx; seat_row faces local -Y",
        "provenance": "Original modular architecture; separately licensed CC0 source seats and PBR materials.",
        "hall_clear_size_m": [56., 40., 14.], "floor_z_m": floor_z,
        "central_field_bounds_m": {"lo": [-14., -8., -.301], "hi": [14., 8., 3.498]},
        "groups": list(groups.values()), "lights": lights, "labels": labels, "views": views,
        "layout_checks": {
            "seat_count": seat_count, "stand_rows": 6,
            "tread_depth_m": .8, "row_rise_m": .4,
            "aisle_step_rise_m": .2, "aisle_width_m": 1.4,
            "clear_circulation_min_m": 3.45,
            "closed_decorative_field_gates": "Fixed matching glass door leaves remain closed; continuous runtime barriers prevent vehicles leaving the field.",
            "geometry_scope": "Exterior unseen detail omitted; roof, structural supports, glazed clerestory and hall walls complete.",
        },
    }
    return result


def validate(layout: dict) -> dict:
    assert layout["schema"] == "rmsim_campus_gym_layout_v1"
    count = 0
    for group in layout["groups"]:
        assert group["collision"] is False
        assert group["mesh"] in ("cube", "cylinder", "sphere", "seat_row")
        for instance in group["instances"]:
            assert all(len(instance[key]) == 3 for key in ("center", "size", "rotation_deg"))
            assert all(math.isfinite(v) for key in instance for v in instance[key])
            assert min(instance["size"]) > 0
            count += 1
    assert count < 5000, count
    assert len(layout["lights"]) <= 12
    assert layout["layout_checks"]["seat_count"] == sum(len(g["instances"])*6 for g in layout["groups"] if g["mesh"]=="seat_row")
    # Every field-level solid visual shell volume is outside the 28x16 map.
    # Topside ceiling and overhead truss instances intentionally span it.
    for group in layout["groups"]:
        if group["mesh"] != "cube":
            continue
        for item in group["instances"]:
            x,y,z = item["center"]
            sx,sy,sz = item["size"]
            if z-sz*.5 >= 4.5:
                continue
            intersects_field = abs(x)-sx*.5 < 14.-1e-5 and abs(y)-sy*.5 < 8.-1e-5
            assert not intersects_field, (group["id"], item)
    return {"status": "passed", "groups": len(layout["groups"]), "instances": count,
            "seat_count": layout["layout_checks"]["seat_count"],
            "lights": len(layout["lights"]), "hall_clear_size_m": layout["hall_clear_size_m"],
            "scope": "Deterministic finite geometry, budget, seat count and central-field clearance; native rendering and physics require UE acceptance."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default = Path(__file__).resolve().parents[2]/"assets/maps/rmuc2027/arena/campus-gym/layout.json"
    parser.add_argument("--output", type=Path, default=default)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    layout = create_layout()
    summary = validate(layout)
    payload = (json.dumps(layout, ensure_ascii=False, separators=(",", ":"))+"\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    summary["layout_sha256"] = hashlib.sha256(payload).hexdigest()
    summary["layout_bytes"] = len(payload)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
