# RMSim project handoff

Read `.codestable/goal.md`, `.codestable/attention.md`, and `docs/progress/current.md` before changing this project.

The standing goal covers native Windows/Ubuntu ROS 2 from the same source, extensible vehicles/maps, realistic presentation, and server-authoritative matches. Current user priority: usable RViz waypoint navigation, original sentry navigation-yaw following, and source-positioned articulated Mid-360. Windows console acceptance is in docs/progress/current.md; preserve the accepted 2000/140 suspension and extend coverage from its evidence.

Keep source assets outside this workspace read-only. Preserve current control mappings, real scale, source wheel hulls, and genuine wheel-ground contact. Do not replace contact motion with pose overrides to pass terrain tests.

Keep configuration in the vehicle package. Save a rollback baseline before changing physics. Record command, exit status, hashes, passed/failed checks, and coverage boundaries; keep failed evidence. Native Windows results do not count as native Ubuntu acceptance. Simulation truth stays in independent evaluation, never localization input.

Commit source, configuration, licenses/notices and compact verification records. Keep secrets, binaries, engine content, downloaded third-party trees, source-restricted models/textures, logs and dense trajectories local as specified in `.gitignore`. Preserve upstream patches in `third_party/patches`.

Record each accepted change in `docs/changes`, update `docs/progress/current.md`, and keep architecture current. Use separate commits for repository setup, test instrumentation, and accepted tuning. Never force-push or overwrite unrelated work.
