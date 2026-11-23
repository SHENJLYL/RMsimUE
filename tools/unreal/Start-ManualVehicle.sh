#!/usr/bin/env bash
# Native Linux wrapper for the same project, FBX importer and C++ sample as Windows.
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo="$(cd -- "$script_dir/../.." && pwd -P)"
engine_root="${ENGINE_ROOT:-}"
manifest="$repo/assets/vehicles/aim_sentry/asset_import.json"
map_file="$repo/assets/maps/rmuc2027/map.json"
spawn_id=default
capture_only=false
training=false
kinematic_preview=false
ros_bridge=false
ros_control=false
ros_motor_control=false
ros_aim_control=false
ros_imu=false
imu_profile=""
ros_lidar=false
lidar_profile=""
sim_step=0
ros_port=9451
stage=preflight

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 3; }
usage() {
    printf '%s\n' 'Usage: ENGINE_ROOT=/path/to/UE bash Start-ManualVehicle.sh [--manifest FILE] [--map FILE] [--spawn ID] [--training] [--capture-only] [--kinematic-preview]' \
        '       bash Start-ManualVehicle.sh --engine-root DIR [--manifest FILE] [--map FILE] [--spawn ID] [--training] [--capture-only] [--kinematic-preview]'
    printf '%s\n' 'ROS: --ros-bridge (observe), --ros-control (velocity), or --ros-motor-control (LK current), optionally --ros-port PORT.'
    printf '%s\n' 'Add --ros-aim-control to grant independent gimbal/shooter ownership (shooting also needs --training).'
    printf '%s\n' 'IMU: --ros-imu [--imu-profile FILE]; optional --sim-step-seconds 0.001.'
    printf '%s\n' 'LiDAR: --ros-lidar [--lidar-profile FILE]; original Livox messages must be built for ROS publishing.'
}
while (($#)); do
    case "$1" in
        --engine-root|--manifest|--map|--spawn|--ros-port|--imu-profile|--lidar-profile|--sim-step-seconds)
            (($# >= 2)) || fail "$1 needs a value"
            case "$1" in
                --engine-root) engine_root="$2" ;;
                --manifest) manifest="$2" ;;
                --map) map_file="$2" ;;
                --spawn) spawn_id="$2" ;;
                --ros-port) ros_port="$2" ;;
                --imu-profile) imu_profile="$2"; ros_imu=true; ros_bridge=true ;;
                --lidar-profile) lidar_profile="$2"; ros_lidar=true; ros_bridge=true ;;
                --sim-step-seconds) sim_step="$2" ;;
            esac
            shift 2 ;;
        --capture-only) capture_only=true; shift ;;
        --training) training=true; shift ;;
        --kinematic-preview) kinematic_preview=true; shift ;;
        --ros-bridge) ros_bridge=true; shift ;;
        --ros-control) ros_bridge=true; ros_control=true; shift ;;
        --ros-motor-control) ros_bridge=true; ros_motor_control=true; shift ;;
        --ros-aim-control) ros_bridge=true; ros_aim_control=true; shift ;;
        --ros-imu) ros_bridge=true; ros_imu=true; shift ;;
        --ros-lidar) ros_bridge=true; ros_lidar=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; fail "Unknown argument: $1" ;;
    esac
done

[[ -n "$engine_root" && -d "$engine_root" ]] || fail 'Explicit ENGINE_ROOT/--engine-root must name an installed UE directory; no download is attempted.'
engine_root="$(cd -- "$engine_root" && pwd -P)"
build="$engine_root/Engine/Build/BatchFiles/Linux/Build.sh"
editor="$engine_root/Engine/Binaries/Linux/UnrealEditor"
project="$repo/unreal/RMSim/RMSim.uproject"
importer="$script_dir/import_vehicle_assets.py"
[[ -f "$build" && -x "$editor" ]] || fail "Missing Linux Build.sh or executable UnrealEditor under $engine_root"
[[ -f "$project" && -f "$importer" && -f "$manifest" ]] || fail 'Project, editor importer or asset manifest is missing.'
[[ -f "$map_file" ]] || fail 'Map file is missing.'
map_file="$(cd -- "$(dirname -- "$map_file")" && pwd -P)/$(basename -- "$map_file")"
manifest="$(cd -- "$(dirname -- "$manifest")" && pwd -P)/$(basename -- "$manifest")"
[[ "$(uname -s)" == Linux ]] || fail 'This entry point requires native Linux; a shell syntax check on another OS is not an Ubuntu run.'
case "$(uname -r)" in *[Mm]icrosoft*|*WSL*) fail 'WSL does not satisfy this project native-Linux profile.' ;; esac
for value in "$engine_root" "$repo" "$manifest" "$map_file" "$spawn_id" "$imu_profile" "$lidar_profile"; do
    case "$value" in *'"'*|*$'\n'*|*$'\r'*) fail 'Quotes/newlines in paths are unsupported by the UE command-line boundary.' ;; esac
done
if $capture_only; then command -v timeout >/dev/null || fail 'GNU timeout is required for --capture-only.'; fi

trap 'code=$?; printf "FAILED stage=%s exit=%s; no runtime acceptance implied.\n" "$stage" "$code" >&2; exit "$code"' ERR
run_stage() { stage="$1"; shift; printf 'Stage: %s\n' "$stage"; "$@"; }

run_stage build bash "$build" RMSimEditor Linux Development "$project" -WaitMutex -NoHotReloadFromIDE
import_args=("$project" -run=pythonscript "-script=\"$importer\"" "-AssetManifest=\"$manifest\"" -unattended -nop4 -NoSplash)
run_stage import "$editor" "${import_args[@]}"
rig="$(dirname -- "$manifest")/ue_mesh_rig.json"
[[ -f "$rig" ]] || fail "Importer did not create $rig"
if ! $kinematic_preview; then
    run_stage collision "$editor" "$project" -run=pythonscript "-script=\"$script_dir/ensure_vehicle_collision.py\"" \
        "-CollisionRig=\"$rig\"" "-CollisionEvidence=\"$repo/artifacts/assets/contact-physics/prepare-$(date -u +%Y%m%d-%H%M%S)\"" \
        -unattended -nop4 -NoSplash
fi

# Source-derived mesh packages carry a portable FBX descriptor; import with the same Python as Windows.
map_source="$(dirname -- "$map_file")/mesh-source.json"
if [[ -f "$map_source" ]]; then
    run_stage import_map "$editor" "$project" -run=pythonscript "-script=\"$script_dir/import_mesh_map.py\"" \
        "-MapSource=\"$map_source\"" -unattended -nop4 -NoSplash
fi

run_args=("$project" '/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode'
    -game -windowed -ResX=1600 -ResY=1000 -NoSplash "-MeshRigFile=\"$rig\"" "-MapFile=\"$map_file\"" "-SpawnId=\"$spawn_id\"")
if [[ "$map_file" == "$repo/assets/maps/rmuc2027/map.json" ]]; then
    run_args+=(-CustomConfig=RMSimGymLumen -vulkan -sm6)
    if ! $capture_only; then run_args+=('-ExecCmds="r.ScreenPercentage 50"' -RMSimViewportHz=120); fi
fi
if $training; then run_args+=(-TrainingMode); fi
if ! $kinematic_preview; then run_args+=(-ContactPhysics); fi
if $ros_bridge; then
    [[ "$ros_port" =~ ^[0-9]+$ ]] && ((ros_port >= 1024 && ros_port <= 65535)) || fail 'ROS port must be 1024..65535'
    $kinematic_preview && fail 'ROS bridge requires contact physics'
    run_args+=(-RosBridge "-RosPort=$ros_port")
fi
if $ros_control; then run_args+=(-RosControl); fi
if $ros_motor_control; then run_args+=(-RosMotorControl); fi
if $ros_aim_control; then run_args+=(-RosAimControl); fi
if $ros_imu; then run_args+=(-RosImu); fi
if $ros_lidar; then run_args+=(-RosLidar); fi
if [[ -n "$lidar_profile" ]]; then
    [[ -f "$lidar_profile" ]] || fail 'LiDAR profile is missing.'
    lidar_profile="$(cd -- "$(dirname -- "$lidar_profile")" && pwd -P)/$(basename -- "$lidar_profile")"
    run_args+=("-LidarProfile=\"$lidar_profile\"")
fi
if [[ -n "$imu_profile" ]]; then
    [[ -f "$imu_profile" ]] || fail 'IMU profile is missing.'
    imu_profile="$(cd -- "$(dirname -- "$imu_profile")" && pwd -P)/$(basename -- "$imu_profile")"
    run_args+=("-ImuProfile=\"$imu_profile\"")
fi
[[ "$sim_step" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail 'Invalid simulation step number'
run_args+=("-SimStepSeconds=$sim_step")
if $capture_only; then
    capture_dir="$repo/artifacts/unreal/linux-capture-$(date -u +%Y%m%d-%H%M%S)"
    mkdir -p -- "$capture_dir"
    run_args+=("-CaptureDir=\"$capture_dir\"" -ExitAfterCapture)
    run_stage capture timeout --kill-after=10s 180s "$editor" "${run_args[@]}"
    [[ -s "$capture_dir/overview.png" ]] || fail "Process exited without overview.png in $capture_dir"
    printf 'Capture process exited; inspect PNGs and controls before accepting runtime behavior: %s\n' "$capture_dir"
else
    run_stage interactive "$editor" "${run_args[@]}"
    printf '%s\n' 'Interactive process exited; no visual/manual-control acceptance is inferred.'
fi
