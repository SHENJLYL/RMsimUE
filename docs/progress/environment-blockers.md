# External runtime prerequisites

Updated 2026-11-15 (Asia/Shanghai). Installation, sandbox access, source compilation and runtime acceptance are tracked separately.

## Unreal Engine

- **Installation blocker cleared:** UE 5.8.4 Installed Build, CL 58902052, is present at `E:\simulator-tools\UE_5.8`. Editor/Editor-Cmd, Build.bat, UBT, core/engine DLLs, ShaderCompileWorker and bundled .NET 10 were found.
- `LauncherInstalled.dat` has an empty installation list. This does not invalidate the inspected engine files; pass the root explicitly to the project scripts.
- VS2026 18.4.2, actual MSVC 14.50.35728 and Windows SDK 10.0.26100.0 match the installed engine's supported configuration. The MSVC folder name 14.50.35717 is a family name; do not mistake it for the compiler binary's patch version.
- **Sandbox access handled for this build:** direct UBT access to `Engine/Source` outside the workspace was denied. The approved outside-sandbox build proceeded; no global permission change or account bypass was used.
- **Windows editor-module build passed:** V5 shared-build setting mismatch was corrected to V7, and the C4458 local `Role` name was renamed `JointRole`. The real build exited 0 on 2026-11-15; `artifacts/unreal/build.log` records `Result: Succeeded` and `unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll` exists.
- **Import and runtime baseline passed:** actual import exited 0; seven meshes passed UE bounds checks. Probe-04 and probe-05 completed with wrapper exit 0, 9/9 engine checks and six decodable PNGs. The real GUI displayed the vehicle. Evidence: `artifacts/unreal/2026-11-15/mesh-scale-verification.json` and `artifacts/unreal/2026-11-15/probe-05/`.
- **Current gate: requested interaction.** War Thunder-style movement/camera changes are in progress and not accepted. CUA's user-input guard prevented complete real keyboard verification, so engine probes do not certify the complete manual input path. No remaining engine-installation blocker is inferred from that limitation.
- Resolved runtime/verification issues: camera frustum inflated robot bounds; visual wheel-spin bounds caused false floor overlap; wrapper UTC DateTime reparsing shifted timestamps by 8 hours. The fixes are represented in the successful probe-04/05 baseline. sRGB capture brightness/studio lighting were corrected, without a physical-calibration claim.

Current evidence: `Engine/Build/Build.version` under the explicit engine root; `artifacts/unreal/build-backup-2026.11.15-07.07.51.log`; `artifacts/unreal/build.log`. `tools/unreal/Check-Engine.ps1` provides a reproducible file-presence precheck.

The unused AndroidFileServer plugin is disabled and its generated local token has been removed; no token value is documented. The runnable sample does not require that service.

Historical evidence: `artifacts/environment/epic-launcher-install.log` records the 2026-11-13 official MSI failure (1925/1603, administrator permission). The user subsequently installed the engine. That old failure no longer requires another install/sign-in request.

## Native Ubuntu

No native Ubuntu execution target has been selected for this project. The same-source `tools/unreal/Start-ManualVehicle.sh` wrapper exists, with Windows Git Bash syntax/help/missing-engine preflight checks recorded under C008. Those checks, Windows builds and WSL runs cannot count as native Ubuntu acceptance. This does not prevent the first user-prioritized Windows manual example.
