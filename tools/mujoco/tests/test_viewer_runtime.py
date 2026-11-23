"""File-integrity contracts for a validated display runtime; no GL or solver."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import viewer_runtime as runtime


class RuntimeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.env = self.root / runtime.ENVIRONMENT_RELATIVE
        for name in runtime.SOURCE_FILES:
            self.write(name, ("fixture " + name).encode())
        self.script = self.root / runtime.SOURCE_FILES[0]
        self.python = self.env / "Scripts/python.exe"
        self.dll = self.env / "Lib/site-packages/mujoco/mujoco.dll"
        self.write(self.python, b"fixture display Python")
        self.write(self.dll, b"fixture renderer DLL")
        counts = dict(nq=21, nv=20, na=0, nu=14, nbody=16, ngeom=253, nmesh=249,
                      nflex=14, nflexvert=891028, nflexelem=1608500)
        self.manifest_path = self.root / "assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json"
        xml = self.manifest_path.with_name("model.xml")
        self.write(xml, b"fixture model identity; never loaded or stepped")
        self.manifest = dict(model_xml="model.xml", model_sha256=runtime.sha256_file(xml),
                             physics_identity_sha256="a" * 64, geometry_manifest_sha256="b" * 64,
                             model_counts=counts)
        self.write(self.manifest_path, self.manifest)
        self.build_path = self.root / "evidence/build.json"
        self.build = dict(schema="rmsim_viewer_renderer_build_v1", renderer_extension=runtime.EXTENSION,
                          status="build_and_import_pass_gl_equivalence_pending", upstream_commit=runtime.UPSTREAM_COMMIT,
                          patch_sha256=runtime.PATCH_SHA256, public_abi_layout_changed=False,
                          solver_source_changed=False, viewer_python=str(self.python), viewer_environment=str(self.env),
                          viewer_dll_sha256=runtime.sha256_file(self.dll))
        self.write(self.build_path, self.build)
        cache = dict(enabled=True, live_contexts=1, live_flex=14, live_bytes=231624000,
                     uploads=14, fast_batches=14, failures=0, rejected_contexts=0)
        self.gl_path = self.root / "evidence/gl.json"
        self.gl = dict(schema="rmsim_full_model_renderer_gl_equivalence_v1", status="passed",
                       viewer_source_sha256=runtime.sha256_file(self.script),
                       runtime=dict(dll_sha256=runtime.sha256_file(self.dll)), solver_steps=0,
                       control_messages_sent=0, physics_processes_started=0, state_unchanged=True,
                       solver_time_unchanged=True, scene_arrays_unchanged=True, protected_dlls_unchanged=True,
                       recreated_context_pixels_equal=True, all_body_pose_position_error_m=0,
                       all_body_pose_quaternion_component_error=0, counts=counts, static_flex_faces=3217000,
                       cases=[dict(name=name, color_equal=True, depth_equal=True, color_changed_pixels=0,
                                   max_depth_difference=0, cache_after_cached=copy.deepcopy(cache))
                              for name in ("near", "whole", "opposite")],
                       after_final_free=dict(enabled=False, live_contexts=0, live_flex=0, live_bytes=0,
                                             failures=0, rejected_contexts=0),
                       identity={**self.manifest, "manifest_sha256": runtime.sha256_file(self.manifest_path)})
        self.receipt_path = self.env / runtime.RECEIPT_NAME
        self.receipt = dict(schema="rmsim_optimized_viewer_runtime_v1", status="validated",
                            patch_sha256=runtime.PATCH_SHA256, renderer_extension=runtime.EXTENSION,
                            source_sha256={name: runtime.sha256_file(self.root / name) for name in runtime.SOURCE_FILES},
                            viewer_python_sha256=runtime.sha256_file(self.python),
                            viewer_dll_sha256=runtime.sha256_file(self.dll))
        self.bind_evidence()

    def write(self, name, content):
        path = name if isinstance(name, Path) else self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else (json.dumps(content) + "\n").encode())

    def bind_evidence(self):
        self.write(self.gl_path, self.gl)
        self.write(self.build_path, self.build)
        for name, path in (("build_manifest", self.build_path), ("gl_verification", self.gl_path)):
            self.receipt[name] = dict(path=path.relative_to(self.root).as_posix(), sha256=runtime.sha256_file(path))
        self.write(self.receipt_path, self.receipt)

    def verify(self):
        return runtime.validate_optimized_viewer_runtime(self.root, self.script)

    def test_validated_files_return_exact_launcher_and_status_contract(self):
        result = self.verify()
        self.assertEqual(result["python"], str(self.python))
        self.assertEqual(result["dll_path"], str(self.dll))
        self.assertEqual(set(result["runtime_identity"]), {"receipt_sha256", "build_manifest_sha256",
                         "gl_verification_sha256", "viewer_dll_sha256", "source_sha256"})
        self.assertEqual(result["runtime_identity"]["source_sha256"], runtime.sha256_file(self.script))
        self.assertEqual(result["source_sha256"], self.receipt["source_sha256"])

    def test_missing_or_pending_receipt_rejects_without_fallback(self):
        self.receipt["status"] = "pending"
        self.write(self.receipt_path, self.receipt)
        with self.assertRaisesRegex(runtime.ViewerRuntimeError, "completed GL"):
            self.verify()
        self.receipt_path.unlink()
        with self.assertRaisesRegex(runtime.ViewerRuntimeError, "missing or inconsistent"):
            self.verify()

    def test_source_dll_and_evidence_tampering_each_rejects(self):
        for path in (self.script, self.dll, self.gl_path):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.assertRaises(runtime.ViewerRuntimeError):
                    self.verify()
                path.write_bytes(original)

    def test_rehashed_semantic_gl_failures_still_reject(self):
        original = copy.deepcopy(self.gl)
        mutations = [lambda g: g.update(status="failed"), lambda g: g.update(solver_steps=1),
                     lambda g: g.update(all_body_pose_position_error_m=1e-6),
                     lambda g: g["cases"][0].update(color_equal=False),
                     lambda g: g["cases"][0]["cache_after_cached"].update(live_flex=0),
                     lambda g: g["after_final_free"].update(live_bytes=4),
                     lambda g: g["counts"].update(nmesh=248)]
        for mutate in mutations:
            self.gl = copy.deepcopy(original)
            mutate(self.gl)
            self.bind_evidence()
            with self.assertRaises(runtime.ViewerRuntimeError):
                self.verify()

    def test_unreviewed_patch_or_solver_build_rejects(self):
        for key, value in (("patch_sha256", "c" * 64), ("solver_source_changed", True)):
            previous = self.build[key]
            self.build[key] = value
            self.bind_evidence()
            with self.assertRaises(runtime.ViewerRuntimeError):
                self.verify()
            self.build[key] = previous

    def test_changed_model_xml_rejects(self):
        self.manifest_path.with_name("model.xml").write_bytes(b"changed geometry")
        with self.assertRaisesRegex(runtime.ViewerRuntimeError, "model XML hash"):
            self.verify()

    def test_seal_validates_before_replacing_existing_receipt(self):
        previous = self.receipt_path.read_bytes()
        self.gl["status"] = "failed"
        self.write(self.gl_path, self.gl)
        with self.assertRaises(runtime.ViewerRuntimeError):
            runtime.seal_optimized_viewer_runtime(self.root, self.build_path, self.gl_path)
        self.assertEqual(self.receipt_path.read_bytes(), previous)
        self.assertEqual(list(self.env.glob("*.tmp")), [])
        self.gl["status"] = "passed"
        self.write(self.gl_path, self.gl)
        result = runtime.seal_optimized_viewer_runtime(self.root, self.build_path, self.gl_path)
        self.assertEqual(result["receipt"]["sha256"], runtime.sha256_file(self.receipt_path))


if __name__ == "__main__":
    unittest.main()
