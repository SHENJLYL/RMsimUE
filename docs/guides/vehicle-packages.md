# Vehicle packages

The v1 importer validates a bounded URDF subset. It is a portable data contract, not a guarantee that CAD models already contain valid dynamics or control mappings.

## Verified examples

From the repository root run:

    python tools/vehicles/verify_samples.py

Canonical exports and exact test output appear in artifacts/vehicles. Tooling uses the Python standard library; recorded execution is Windows/Python 3.14.

## Add a physical vehicle package

Copy an example directory into assets/vehicles/<new-id>, then update:

1. vehicle.json: ID, URDF path, base/wheel/gimbal joint names, limits and sensors.
2. model/robot.urdf: actual parts, units, origins, axes/limits, collisions and inertial parameters.
3. Local meshes. References must remain inside the package.

Windows PowerShell:

    $env:PYTHONPATH = Join-Path (Get-Location) 'src'
    python -m rm_vehicle_tools validate assets/vehicles/<new-id>
    python -m rm_vehicle_tools export assets/vehicles/<new-id> --output artifacts/vehicles/<new-id>.canonical.json

Ubuntu native command (not yet runtime-verified here):

    PYTHONPATH=src python3 -m rm_vehicle_tools validate assets/vehicles/<new-id>
    PYTHONPATH=src python3 -m rm_vehicle_tools export assets/vehicles/<new-id> --output artifacts/vehicles/<new-id>.canonical.json

Output directory must exist. Failure exits 2 and leaves source files untouched. Package paths use forward slashes; length is metres, angles radians, mass kilograms.

## Boundaries

- Single rooted URDF; fixed/continuous/revolute joints.
- Box/cylinder/sphere; mesh references validated/hashed, not loaded by this tool.
- Positive physically valid inertial tensors required for physical packages.
- No mimic/transmission/Gazebo-extension interpretation or arbitrary plugin execution.
- New drive mechanisms need corresponding engine controllers; visual import alone cannot create their dynamics.

The user's AIM sentry follows the separate manual visual-rig path in aim-sentry-assets.md. War Thunder gameplay values are not measured RoboMaster inertia.
