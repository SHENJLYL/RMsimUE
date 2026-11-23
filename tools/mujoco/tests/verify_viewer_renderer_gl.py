"""Isolated, real-WGL functional comparison; never starts or steps a solver."""
from pathlib import Path
import argparse
import hashlib
import json
import struct
import sys
import time
import traceback
import zlib


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def array_digest(array):
    return hashlib.sha256(memoryview(array).cast("B")).hexdigest()


def png(path, rgb):
    """Save actual GL pixels using the standard library, with no image edit."""
    height, width, _ = rgb.shape
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = b"".join(b"\0" + rgb[row].tobytes() for row in range(height - 1, -1, -1))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
                           chunk(b"IDAT", zlib.compress(pixels, 4)) + chunk(b"IEND", b""))


def run(args):
    root = args.report_dir.resolve()
    root.mkdir(exist_ok=False, parents=True)
    sys.path.insert(0, str(args.viewer_source.resolve().parent))
    import live_viewer as viewer
    import mujoco
    import numpy as np
    import glfw
    record = {"schema": "rmsim_full_model_renderer_gl_equivalence_v1", "status": "running",
              "pid": __import__("os").getpid(), "python": sys.executable,
              "viewer_source_sha256": digest(args.viewer_source), "solver_steps": 0,
              "control_messages_sent": 0, "physics_processes_started": 0,
              "timing_scope": "isolated hidden WGL functional run; explicit completion fences/readback; not a three-window FPS result",
              "background_load": args.background_load, "cases": []}
    context = window = cache = None
    started = time.monotonic()
    protected = {
        r"D:\simulator\artifacts\environments\mujoco-4.0.1\Lib\site-packages\mujoco\mujoco.dll": "552b7a8ca56617a4702ac0359b216ffaefa95535a4794728edd2df0300aa2e78",
        r"D:\simulator\third_party\mujoco-sdk\4.0.1-rmsim-static-world-flex.3\windows-x86_64\bin\mujoco.dll": "544b51a2a79f5bd10341a977ddba7db22c8639db4f1c8b5d13081363af4019e4",
    }
    try:
        assert all(digest(path) == expected for path, expected in protected.items())
        runtime = viewer.runtime_identity(mujoco)
        record["runtime"] = runtime
        assert runtime["dll_sha256"] == args.expected_dll_sha256
        manifest, xml, _, identity = viewer.read_manifest(args.manifest.resolve(), "a" * 32)
        record["identity"] = identity
        model = mujoco.MjModel.from_xml_path(str(xml))
        data = mujoco.MjData(model)
        viewer.initialize_display_model(mujoco, model, data, manifest)
        record["display_lighting"] = viewer.configure_display_lighting(model)
        record["counts"] = {name: int(getattr(model, name)) for name in
                            ("nq", "nv", "na", "nu", "nbody", "ngeom", "nmesh", "nflex", "nflexvert", "nflexelem")}
        before = {name: np.array(getattr(data, name), copy=True) for name in
                  ("qpos", "qvel", "act", "ctrl", "xpos", "xquat")}
        before_time = float(data.time)
        record["source_qpos"] = {"origin": "sealed manifest initial_qpos", "values": data.qpos.tolist()}
        cache = viewer.StaticFlexGpuCache(True, runtime)
        assert glfw.init()
        glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
        window = glfw.create_window(1280, 800, "RMSim renderer functional comparison", None, None)
        assert window
        glfw.make_context_current(window)
        glfw.swap_interval(0)
        context = mujoco.MjrContext(model, mujoco.mjtFontScale.mjFONTSCALE_150)
        record["graphics"] = viewer.graphics_identity(glfw, window, context)
        record["graphics"]["swap_interval_requested"] = 0
        record["window_visible"] = bool(glfw.get_window_attrib(window, glfw.VISIBLE))
        scene = mujoco.MjvScene(model, maxgeom=max(1000, model.ngeom + model.nflex + 128))
        options, camera, perturb = mujoco.MjvOption(), mujoco.MjvCamera(), mujoco.MjvPerturb()
        options.geomgroup[:] = 1
        options.flexgroup[:] = 1
        for flag in (mujoco.mjtVisFlag.mjVIS_FLEXVERT, mujoco.mjtVisFlag.mjVIS_FLEXEDGE,
                     mujoco.mjtVisFlag.mjVIS_FLEXSKIN, mujoco.mjtVisFlag.mjVIS_CONTACTPOINT,
                     mujoco.mjtVisFlag.mjVIS_CONTACTFORCE):
            options.flags[flag] = 0
        options.flags[mujoco.mjtVisFlag.mjVIS_FLEXFACE] = 1
        camera.type = mujoco.mjtCamera.mjCAMERA_FREE
        mujoco.mjv_updateScene(model, data, options, perturb, camera, mujoco.mjtCatBit.mjCAT_ALL, scene)
        scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = 0
        scene.flags[mujoco.mjtRndFlag.mjRND_REFLECTION] = 0
        record["static_flex_faces"] = int(sum(scene.flexfaceused))
        record["static_flex_faces_by_id"] = [int(value) for value in scene.flexfaceused]
        record["scene_geom_count"] = int(scene.ngeom)
        immutable_arrays = {name: array_digest(getattr(scene, name)) for name in
                            ("flexface", "flexnormal", "flextexcoord")}
        width, height = glfw.get_framebuffer_size(window)
        record["framebuffer"] = [width, height]
        viewport = mujoco.MjrRect(0, 0, width, height)
        body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "body")
        if body < 0:
            body = 1
        cases = [
            ("near_chassis", data.xpos[body].copy(), 3.5, 135.0, -25.0),
            ("whole_arena", model.stat.center.copy(), max(3.0, 1.2 * model.stat.extent), 90.0, -65.0),
            ("opposite_view", data.xpos[body].copy(), 6.0, -45.0, -35.0),
        ]

        def draw_capture():
            mujoco.mjr_render(viewport, scene, context)
            rgb = np.empty((height, width, 3), dtype=np.uint8)
            depth = np.empty((height, width), dtype=np.float32)
            mujoco.mjr_readPixels(rgb, depth, viewport, context)
            return rgb, depth

        def timing_samples():
            # Completion fences are explicit diagnostics only, never live viewer behavior.
            mujoco.mjr_finish()
            values = []
            for _ in range(8):
                begin = time.perf_counter()
                mujoco.mjr_render(viewport, scene, context)
                submitted = time.perf_counter()
                mujoco.mjr_finish()
                completed = time.perf_counter()
                values.append({"submit_ms": (submitted - begin) * 1000,
                               "finish_wait_ms": (completed - submitted) * 1000,
                               "completed_ms": (completed - begin) * 1000})
            return {name: {"mean": float(np.mean([x[name] for x in values])),
                           "max": max(x[name] for x in values)} for name in values[0]}

        last_rgb = last_depth = None
        for name, lookat, distance, azimuth, elevation in cases:
            camera.lookat[:] = lookat
            camera.distance, camera.azimuth, camera.elevation = distance, azimuth, elevation
            scene.ngeom = 0
            mujoco.mjv_addGeoms(model, data, options, perturb, mujoco.mjtCatBit.mjCAT_ALL, scene)
            mujoco.mjv_updateCamera(model, data, camera, scene)
            mujoco.mjv_makeLights(model, data, scene)
            cache.library.rmsim_mjr_setStaticFlexCacheEnabled(0)
            old_rgb, old_depth = draw_capture()
            stock_timing = timing_samples()
            stock_stats = cache.status()
            cache.library.rmsim_mjr_setStaticFlexCacheEnabled(1)
            upload_start = time.perf_counter()
            new_rgb, new_depth = draw_capture()
            first_completed_ms = (time.perf_counter() - upload_start) * 1000
            cached_timing = timing_samples()
            new_stats = cache.status()
            color_equal = bool(np.array_equal(old_rgb, new_rgb))
            depth_equal = bool(np.array_equal(old_depth, new_depth))
            entry = {"name": name, "camera": viewer.camera_state(camera),
                     "color_equal": color_equal, "depth_equal": depth_equal,
                     "color_changed_pixels": int(np.any(old_rgb != new_rgb, axis=2).sum()),
                     "max_color_difference": int(np.abs(old_rgb.astype(np.int16) - new_rgb.astype(np.int16)).max()),
                     "max_depth_difference": float(np.abs(old_depth - new_depth).max()),
                     "stock_rgb_sha256": array_digest(old_rgb), "cached_rgb_sha256": array_digest(new_rgb),
                     "stock_depth_sha256": array_digest(old_depth), "cached_depth_sha256": array_digest(new_depth),
                     "rgb_stddev": float(np.std(new_rgb)), "cache_after_stock": stock_stats,
                     "cache_after_cached": new_stats, "cached_first_completed_with_readback_ms": first_completed_ms,
                     "fenced_stock_timing_8": stock_timing, "fenced_cached_timing_8": cached_timing}
            record["cases"].append(entry)
            png(root / (name + "-stock.png"), old_rgb)
            png(root / (name + "-cached.png"), new_rgb)
            assert color_equal and depth_equal, name
            assert new_stats["live_flex"] == model.nflex and new_stats["uploads"] == model.nflex
            assert new_stats["failures"] == 0 and new_stats["rejected_contexts"] == 0
            assert entry["rgb_stddev"] > 2.0
            last_rgb, last_depth = new_rgb, new_depth

        assert immutable_arrays == {name: array_digest(getattr(scene, name)) for name in immutable_arrays}
        record["scene_array_sha256"] = immutable_arrays
        record["scene_arrays_unchanged"] = True
        record["state_unchanged"] = all(np.array_equal(before[name], getattr(data, name)) for name in before)
        record["solver_time_unchanged"] = float(data.time) == before_time
        record["all_body_pose_position_error_m"] = float(np.max(np.abs(data.xpos - before["xpos"])))
        record["all_body_pose_quaternion_component_error"] = float(np.max(np.abs(data.xquat - before["xquat"])))
        assert record["state_unchanged"] and record["solver_time_unchanged"]
        context.free()
        context = None
        record["after_first_free"] = cache.status()
        assert all(record["after_first_free"][key] == 0 for key in ("live_contexts", "live_flex", "live_bytes"))
        context = mujoco.MjrContext(model, mujoco.mjtFontScale.mjFONTSCALE_150)
        recreated_rgb, recreated_depth = draw_capture()
        assert np.array_equal(last_rgb, recreated_rgb) and np.array_equal(last_depth, recreated_depth)
        record["recreated_context"] = cache.status()
        assert record["recreated_context"]["uploads"] == 2 * model.nflex
        record["recreated_context_pixels_equal"] = True
        record["status"] = "passed"
    except Exception as error:
        record["status"] = "failed"
        record["error"] = repr(error)
        record["traceback"] = traceback.format_exc()
    finally:
        if context is not None:
            context.free()
        if cache is not None:
            record["after_final_free"] = cache.close()
            if any(record["after_final_free"].get(key, 0) for key in ("live_contexts", "live_flex", "live_bytes")):
                record["status"] = "failed"
                record["cleanup_error"] = "renderer resources remain"
        if window is not None:
            glfw.destroy_window(window)
        glfw.terminate()
        record["protected_dll_sha256_after"] = {path: digest(path) for path in protected}
        record["protected_dlls_unchanged"] = record["protected_dll_sha256_after"] == protected
        if not record["protected_dlls_unchanged"]:
            record["status"] = "failed"
        record["wall_seconds"] = time.monotonic() - started
        (root / "verification.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": record["status"], "report": str(root / "verification.json"),
                          "cases": len(record["cases"]), "error": record.get("error"),
                          "wall_seconds": record["wall_seconds"]}), flush=True)
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--viewer-source", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--expected-dll-sha256", required=True)
    parser.add_argument("--background-load", default="not controlled; functional comparison only")
    raise SystemExit(run(parser.parse_args()))
