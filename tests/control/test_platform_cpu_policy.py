"""CPU policy must preserve explicit restrictions and report actual native failures."""
import copy
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"tools/integration"))
from platform_cpu_policy import decode_cpu_sets, select_performance_cpu_sets, apply_owned_process_policy


def record(identifier, logical, efficiency, flags=0, group=0, size=32):
    return struct.pack("<IIIH6BIQ", size, 0, identifier, group, logical, logical, 0, 0,
                       efficiency, flags, 0, 0)+bytes(size-32)


def topology():
    return decode_cpu_sets(record(300, 0, 0)+record(301, 1, 3)+record(302, 2, 3, 2)
                           +record(303, 3, 3, 6))


def before():
    return {"default_cpu_sets": [], "affinity": {"process_mask": 15, "system_mask": 15},
            "priority_class": 32, "power_throttling": {"version": 1, "control_mask": 0, "state_mask": 0}}


def owned():
    process = Mock(spec=subprocess.Popen)
    process.pid, process._handle, process.args = 912, 884, ["C:/ue/UnrealEditor.exe", "project.uproject"]
    process.poll.return_value = None
    return process


class NativeApi:
    def __init__(self, initial=None, mismatch=False, fail=False, wrong_pid=False):
        self.initial, self.mismatch, self.fail = copy.deepcopy(initial or before()), mismatch, fail
        self.wrong_pid, self.applied, self.calls = wrong_pid, [], []
        self.observations = 0

    def identity(self, handle):
        self.calls.append({"api": "GetProcessId", "succeeded": True, "last_error": 0})
        return {"pid": 913 if self.wrong_pid else 912, "exit_code": 259,
                "executable": "C:\\ue\\UnrealEditor.exe"}

    def observe(self, handle):
        self.observations += 1
        result = copy.deepcopy(self.initial)
        if self.applied and not self.mismatch:
            result["default_cpu_sets"] = self.applied[:]
        return result

    def topology(self, handle):
        return topology()

    def apply(self, handle, ids):
        self.calls.append({"api": "SetProcessDefaultCpuSets", "succeeded": not self.fail,
                           "last_error": 5 if self.fail else 0})
        if self.fail:
            raise OSError(5, "SetProcessDefaultCpuSets failed")
        self.applied = ids[:]


class PlatformCpuPolicyTests(unittest.TestCase):
    def test_decode_skips_unknown_kind_and_honors_extended_size(self):
        raw = struct.pack("<II", 12, 9)+b"abcd"+record(411, 2, 5, size=40)
        values = decode_cpu_sets(raw)
        self.assertEqual([(r["id"], r["logical_processor"], r["efficiency_class"]) for r in values],
                         [(411, 2, 5)])

    def test_rejects_truncated_overlapping_duplicate_and_invalid_records(self):
        for raw in (b"", b"short", struct.pack("<II", 0, 0), struct.pack("<II", 500, 0),
                    record(1, 0, 1)[:-1], record(1, 0, 1)+record(1, 1, 0),
                    record(1, 0, 1)+record(2, 0, 0), record(1, 64, 1)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_cpu_sets(raw)

    def test_dynamic_highest_class_excludes_allocated_to_other_process(self):
        decision = select_performance_cpu_sets(topology(), before())
        self.assertEqual(decision["requested_cpu_set_ids"], [301, 303])
        self.assertEqual(decision["highest_efficiency_class"], 3)

    def test_preserves_explicit_sets_affinity_homogeneous_and_multigroup(self):
        rows = topology()
        cases = []
        state = before(); state["default_cpu_sets"] = [300]
        cases.append((rows, state, "existing_explicit_cpu_sets"))
        state = before(); state["affinity"]["process_mask"] = 3
        cases.append((rows, state, "existing_restrictive_affinity"))
        uniform = copy.deepcopy(rows)
        for row in uniform:
            row["efficiency_class"] = 0
        cases.append((uniform, before(), "homogeneous_cpu_classes"))
        groups = copy.deepcopy(rows); groups[-1]["group"] = 1
        cases.append((groups, before(), "multiple_processor_groups_preserve_system"))
        for selected_rows, state, reason in cases:
            with self.subTest(reason=reason):
                result = select_performance_cpu_sets(selected_rows, state)
                self.assertEqual((result["action"], result["reason"]), ("preserve", reason))
                self.assertEqual(result["requested_cpu_set_ids"], [])

    def test_owned_handle_applied_and_native_readback_preserved(self):
        api = NativeApi(); process = owned()
        result = apply_owned_process_policy(process, process.args[0], paced=True, api=api, platform_name="nt")
        self.assertEqual((result["status"], result["outcome"]), ("passed", "applied"))
        self.assertEqual(result["observed_default_cpu_set_ids"], [301, 303])
        self.assertEqual(result["before"]["affinity"], result["after"]["affinity"])
        self.assertTrue(all(c["passed"] for c in result["checks"]))

    def test_pid_mismatch_and_unowned_object_cannot_mutate(self):
        for process, api in ((owned(), NativeApi(wrong_pid=True)), (object(), NativeApi())):
            with self.subTest(process=process):
                result = apply_owned_process_policy(process, "C:/ue/UnrealEditor.exe", paced=True,
                                                    api=api, platform_name="nt")
                self.assertEqual(result["status"], "failed")
                self.assertEqual(api.applied, [])

    def test_failed_setter_and_wrong_readback_never_report_success(self):
        for api in (NativeApi(fail=True), NativeApi(mismatch=True)):
            with self.subTest(api=api):
                process = owned()
                result = apply_owned_process_policy(process, process.args[0], paced=True,
                                                    api=api, platform_name="nt")
                self.assertEqual(result["status"], "failed")
                self.assertIn("error", result)
                self.assertTrue(result["native_calls"])

    def test_explicit_policy_preserved_and_system_unpaced_nonwindows_do_not_call_api(self):
        state = before(); state["default_cpu_sets"] = [300]
        api = NativeApi(initial=state); process = owned()
        result = apply_owned_process_policy(process, process.args[0], paced=True, api=api, platform_name="nt")
        self.assertEqual((result["status"], result["outcome"]), ("passed", "unchanged"))
        self.assertEqual(api.applied, [])
        for mode, paced, platform in (("system", True, "nt"), ("performance", False, "nt"),
                                       ("performance", True, "posix")):
            api = NativeApi(); process = owned()
            result = apply_owned_process_policy(process, process.args[0], paced=paced, mode=mode,
                                                api=api, platform_name=platform)
            self.assertEqual((result["status"], result["outcome"]), ("passed", "unchanged"))
            self.assertEqual(api.calls, [])


    def _bound_policy_fixture(self, directory):
        import hashlib
        import json
        from platform_cpu_policy import __file__ as helper_source
        process = owned(); api = NativeApi()
        policy = apply_owned_process_policy(process, process.args[0], paced=True, api=api, platform_name="nt")
        helper_hash = hashlib.sha256(Path(helper_source).read_bytes()).hexdigest()
        session = Path(directory)
        path = session/"process-policy/verification.json"; path.parent.mkdir()
        provenance = {"module_sha256": "a"*64, "cpu_policy_source_sha256": helper_hash,
                      "cpu_policy_requested": "performance", "cpu_policy_platform": "nt",
                      "pace_real_time": True, "process_policy_path": str(path)}
        provenance_path = session/"runtime-provenance.json"
        provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
        policy.update(session_directory=str(session), runtime_provenance_path=str(provenance_path),
                      runtime_provenance_sha256=hashlib.sha256(provenance_path.read_bytes()).hexdigest(),
                      module_sha256=provenance["module_sha256"], capture_phase="immediately_after_owned_ue_popen",
                      command=[process.args[0],str(ROOT/"unreal/RMSim/RMSim.uproject"),
                               "-SimRealTimePace","-abslog="+str(session/"runtime.log")])
        for name in ("GetExitCodeProcess", "QueryFullProcessImageNameW", "GetProcessAffinityMask",
                     "GetPriorityClass", "GetProcessInformation", "GetProcessDefaultCpuSets",
                     "GetSystemCpuSetInformation"):
            policy["native_calls"].append({"api":name,"succeeded":True,"last_error":0})
        status = {"pid":os.getpid(),"process_policy":{"pid":process.pid}}
        def write():
            path.write_text(json.dumps(policy),encoding="utf-8")
            status["process_policy"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        write()
        return policy,provenance,session,status,helper_hash,write

    def test_release_gate_rejects_fabricated_success_and_wrong_native_readback(self):
        import tempfile
        from summarize_navigation_realtime import cpu_policy_checks
        with tempfile.TemporaryDirectory() as directory:
            policy,prov,session,status,source_hash,write = self._bound_policy_fixture(directory)
            self.assertTrue(all(cpu_policy_checks(policy,prov,session,status,source_hash).values()))
            policy["after"]["default_cpu_sets"] = [300]
            policy["observed_default_cpu_set_ids"] = [300]
            write()
            checks = cpu_policy_checks(policy,prov,session,status,source_hash)
            self.assertFalse(checks["cpu_policy_dynamic_selection_and_native_readback"])
            policy["native_calls"] = []
            write()
            self.assertFalse(cpu_policy_checks(policy,prov,session,status,source_hash)["cpu_policy_native_calls_and_checks_passed"])

    def test_release_gate_rejects_other_pid_and_changed_helper_even_with_passed_checks(self):
        import tempfile
        from summarize_navigation_realtime import cpu_policy_checks
        with tempfile.TemporaryDirectory() as directory:
            policy,prov,session,status,source_hash,write = self._bound_policy_fixture(directory)
            policy["native_process"]["pid"] += 1
            policy["helper_sha256"] = "b"*64
            write()
            checks = cpu_policy_checks(policy,prov,session,status,source_hash)
            self.assertFalse(checks["cpu_policy_actual_owned_process"])
            self.assertFalse(checks["cpu_policy_current_helper_and_request"])



    def _worker_budget_fixture(self):
        from platform_cpu_policy import prepare_ue_worker_budget, worker_budget_digest
        class ParentApi(NativeApi):
            def current_handle(self):
                return 123
            def identity(self, handle):
                result = super().identity(handle); result["pid"] = os.getpid()
                return result
            def topology(self, handle):
                rows = super().topology(handle)
                for row in rows:
                    if row["id"] == 303: row["flags"] = 0
                return rows
        process = owned()
        pre = prepare_ue_worker_budget(process.args, paced=True, api=ParentApi(), platform_name="nt")
        process.args = pre["command"]
        policy = apply_owned_process_policy(process, process.args[0], paced=True, api=NativeApi(),
                                            platform_name="nt", worker_budget=pre)
        for name in ("GetCurrentProcess", "GetExitCodeProcess", "QueryFullProcessImageNameW",
                     "GetProcessAffinityMask", "GetPriorityClass", "GetProcessInformation",
                     "GetProcessDefaultCpuSets", "GetSystemCpuSetInformation"):
            pre["native_calls"].append({"api":name,"succeeded":True,"last_error":0})
        fingerprint = worker_budget_digest(pre)
        policy["worker_budget"]["preflight_sha256"] = fingerprint
        provenance = {"worker_budget":pre,"worker_budget_sha256":fingerprint,
                      "cpu_policy_source_sha256":pre["helper_sha256"],"ue_command":pre["command"],
                      "ue_command_sha256":pre["command_sha256"],"ue_corelimit":pre["corelimit"]}
        return policy, provenance

    def test_worker_budget_gate_requires_actual_native_core_count_and_current_preflight(self):
        from summarize_navigation_realtime import worker_budget_checks
        policy, provenance = self._worker_budget_fixture()
        marker = "LogInit: CPU Page size=4096, Cores=2"
        self.assertTrue(all(worker_budget_checks(policy,provenance,marker).values()))
        checks = worker_budget_checks(policy,provenance,"LogInit: CPU Page size=4096, Cores=24")
        self.assertFalse(checks["worker_budget_native_ue_startup_core_count_matches"])
        self.assertFalse(worker_budget_checks(policy,provenance,"")["worker_budget_native_ue_startup_core_count_matches"])
        provenance["worker_budget"]["helper_sha256"] = "c"*64
        self.assertFalse(worker_budget_checks(policy,provenance,marker)["worker_budget_source_hash_command_and_time_binding"])

    def test_worker_budget_gate_rejects_wrong_actual_child_sets_and_argv(self):
        from summarize_navigation_realtime import worker_budget_checks
        policy, provenance = self._worker_budget_fixture()
        marker = "LogInit: CPU Page size=4096, Cores=2"
        policy["worker_budget"]["child_dynamic_decision"]["requested_cpu_set_ids"] = [300]
        self.assertFalse(worker_budget_checks(policy,provenance,marker)["worker_budget_parent_and_child_native_decisions_match"])
        policy["command"] = [*policy["command"],"-corelimit=24"]
        self.assertFalse(worker_budget_checks(policy,provenance,marker)["worker_budget_source_hash_command_and_time_binding"])


    @unittest.skipUnless(os.name == "nt", "Windows native API smoke")
    def test_native_borrowed_handle_for_disposable_owned_python(self):
        with subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                              stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) as process:
            try:
                result = apply_owned_process_policy(process, sys.executable, paced=True)
                type(self).native_report = result
                self.assertEqual(result["status"], "passed", result)
                self.assertEqual(result["native_process"]["pid"], process.pid)
                self.assertEqual(result["before"]["affinity"], result["after"]["affinity"])
                self.assertTrue(result["native_calls"])
            finally:
                process.stdin.close()
                process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
