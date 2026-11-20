"""Generate the static-flex scaling fixture without third-party dependencies."""
import argparse
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("n", type=int, help="cells per side; triangles = 2*n*n")
p.add_argument("output", type=Path)
a = p.parse_args()
if a.n < 1:
    p.error("n must be positive")
n = a.n
cube = " ".join(str(v) for x in [-.05, .05] for y in [-.05, .05]
                for z in [-.05, .05] for v in [x, y, z])
with a.output.open("w") as f:
    f.write('<mujoco><size memory="128M"/><option timestep=".001" integrator="implicitfast"/>'
            '<default><geom solref=".002 1" solimp=".99 .999 .0001 .5 2"/></default>'
            '<asset><mesh name="cube" vertex="' + cube + '"/></asset>'
            '<worldbody><body name="probe" pos="0 0 .0501"><freejoint/>'
            '<geom type="mesh" mesh="cube" mass="1"/></body></worldbody>'
            '<deformable><flex name="floor" dim="2" body="world" radius=".0001" vertex="')
    step = 28.0/n
    for j in range(n+1):
        y = 14.0 if j == n else -14.0+j*step
        for i in range(n+1):
            x = 14.0 if i == n else -14.0+i*step
            if i or j:
                f.write(" ")
            f.write(f"{x:.9g} {y:.9g} 0")
    f.write('" element="')
    for half in range(2):
        for j in range(n):
            for i in range(n):
                v = j*(n+1)+i
                if half or i or j:
                    f.write(" ")
                if half == 0:
                    f.write(f"{v} {v+1} {v+n+2}")
                else:
                    f.write(f"{v} {v+n+2} {v+n+1}")
    f.write('"><contact selfcollide="none" solref=".002 1" '
            'solimp=".99 .999 .0001 .5 2"/></flex></deformable></mujoco>')
print(f"{a.output}: {(n+1)**2} vertices, {2*n*n} triangles")
