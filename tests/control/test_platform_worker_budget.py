"""A pre-start UE worker cap must match the exact owned child's soft CPU Sets."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"tools/integration"))
from platform_cpu_policy import prepare_ue_worker_budget, apply_owned_process_policy, worker_budget_digest


def topology():
    # Two performance physical cores, each with two logical SMT siblings.
    return [dict(id=100+i, group=0, logical_processor=i, core=(i+1)//2,
                 efficiency_class=0 if i == 0 else 3, flags=0)
            for i in range(5)]


def restrictions():
    return {"default_cpu_sets": [], "affinity": {"process_mask": 31, "system_mask": 31},
            "priority_class": 32, "power_throttling": {"version": 1, "control_mask": 0, "state_mask": 0}}


class Api:
    def __init__(self, rows=None, before=None, unavailable=False):
        self.rows = copy.deepcopy(topology() if rows is None else rows)
        self.before = copy.deepcopy(restrictions() if before is None else before)
        self.unavailable, self.calls, self.applied = unavailable, [], []

    def current_handle(self):
        self.calls.append({"api": "GetCurrentProcess", "succeeded": not self.unavailable})
        if self.unavailable:
            raise OSError(50, "Fixture unavailable API")
        return 11

    def identity(self, handle):
        return {"pid": os.getpid() if handle == 11 else 912, "exit_code": 259,
                "executable": sys.executable if handle == 11 else "C:/ue/UnrealEditor.exe"}

    def observe(self, handle):
        result = copy.deepcopy(self.before)
        if handle == 22 and self.applied:
            result["default_cpu_sets"] = self.applied[:]
        return result

    def topology(self, handle):
        return copy.deepcopy(self.rows)

    def apply(self, handle, identifiers):
        if handle != 22:
            raise AssertionError("Preflight attempted to mutate its parent")
        self.calls.append({"api": "SetProcessDefaultCpuSets", "succeeded": True, "last_error": 0})
        self.applied = identifiers[:]


def owned(command):
    process = Mock(spec=subprocess.Popen)
    process.pid, process._handle, process.args = 912, 22, list(command)
    process.poll.return_value = None
    return process


class WorkerBudgetTests(unittest.TestCase):
    command = ["C:/ue/UnrealEditor.exe", "project.uproject", "-SimRealTimePace"]

    def plan(self, api=None, command=None, **kwargs):
        return prepare_ue_worker_budget(self.command if command is None else command, paced=True,
                                        api=api or Api(), platform_name="nt", **kwargs)

    def test_readonly_preflight_counts_physical_smt_cores_once(self):
        api = Api(); original = copy.deepcopy(api.before)
        result = self.plan(api)
        self.assertEqual((result["status"], result["action"]), ("passed", "apply"))
        self.assertEqual(result["expected_cpu_set_ids"], [101, 102, 103, 104])
        self.assertEqual((result["physical_core_count"], result["logical_processor_count"]), (2, 4))
        self.assertEqual(result["command"], self.command+["-corelimit=2"])
        self.assertEqual(result["command_sha256"], worker_budget_digest(result["command"]))
        self.assertEqual(api.before, original)
        self.assertFalse(api.applied)
        self.assertFalse(any(c["api"].startswith("Set") for c in result["native_calls"]))

    def test_preserves_parent_explicit_sets_affinity_multigroup_and_homogeneous(self):
        cases=[]
        state=restrictions(); state["default_cpu_sets"]=[101]
        cases.append(Api(before=state))
        state=restrictions(); state["affinity"]["process_mask"]=7
        cases.append(Api(before=state))
        rows=topology(); rows[-1]["group"]=1
        cases.append(Api(rows=rows))
        rows=topology()
        for row in rows: row["efficiency_class"]=1
        cases.append(Api(rows=rows))
        rows=topology(); rows[1]["flags"]=6  # Allocated to parent, not proven child-accessible.
        cases.append(Api(rows=rows))
        for api in cases:
            with self.subTest(rows=api.rows):
                result=self.plan(api)
                self.assertEqual((result["status"],result["action"]),("passed","preserve"))
                self.assertEqual(result["command"],self.command)
                self.assertIsNone(result["corelimit"])
                self.assertFalse(api.applied)

    def test_matching_explicit_limit_is_preserved_without_duplicate(self):
        command=self.command+["-CoReLiMiT=2", "-physicalcorelimit=2"]
        result=self.plan(command=command)
        self.assertEqual(result["status"],"passed")
        self.assertEqual(result["command"],command)
        self.assertEqual(result["corelimit_source"],"existing_matching")
        self.assertEqual(result["corelimit_argument"],"-CoReLiMiT=2")

    def test_conflicting_duplicate_or_malformed_limits_block_launch(self):
        for additions in (["-corelimit=1"],["-corelimit=0"],["-corelimit=2","-CORELIMIT=2"],
                          ["-corelimit","2"],["--corelimit=2"],["-physicalcorelimit=3"]):
            with self.subTest(arguments=additions):
                command=self.command+additions
                result=self.plan(command=command)
                self.assertEqual(result["status"],"failed")
                self.assertFalse(result["launch_allowed"])
                self.assertEqual(result["command"],command)

    def test_owned_child_matches_declared_ids_count_command_and_readback(self):
        api=Api(); plan=self.plan(api); process=owned(plan["command"])
        result=apply_owned_process_policy(process,self.command[0],paced=True,api=api,
                                          platform_name="nt",worker_budget=plan)
        self.assertEqual((result["status"],result["outcome"]),("passed","applied"))
        self.assertEqual(result["observed_default_cpu_set_ids"],plan["expected_cpu_set_ids"])
        self.assertEqual(result["worker_budget"]["actual_physical_core_count"],2)
        self.assertEqual(result["worker_budget"]["actual_logical_processor_count"],4)
        self.assertEqual(result["worker_budget"]["preflight_sha256"],worker_budget_digest(plan))
        self.assertTrue(all(c["passed"] for c in result["checks"]))

    def test_changed_child_topology_cannot_apply_a_stale_budget(self):
        api=Api(); plan=self.plan(api); api.rows[-1]["efficiency_class"]=1
        result=apply_owned_process_policy(owned(plan["command"]),self.command[0],paced=True,
                                          api=api,platform_name="nt",worker_budget=plan)
        self.assertEqual(result["status"],"failed")
        self.assertFalse(api.applied)
        self.assertIn("worker_budget_actual_cpu_sets_match_preflight",result["error"])

    def test_changed_command_or_forged_core_count_cannot_apply(self):
        for alter in ("command","count"):
            api=Api(); plan=self.plan(api); process=owned(plan["command"])
            if alter=="command":process.args[-1]="-corelimit=3"
            else:plan["physical_core_count"]=3
            with self.subTest(alter=alter):
                result=apply_owned_process_policy(process,self.command[0],paced=True,api=api,
                                                  platform_name="nt",worker_budget=plan)
                self.assertEqual(result["status"],"failed")
                self.assertFalse(api.applied)

    def test_preflight_preserve_remains_unmodified_even_if_child_is_eligible(self):
        state=restrictions(); state["default_cpu_sets"]=[101]
        plan=self.plan(Api(before=state)); child_api=Api()
        result=apply_owned_process_policy(owned(plan["command"]),self.command[0],paced=True,
                                          api=child_api,platform_name="nt",worker_budget=plan)
        self.assertEqual((result["status"],result["outcome"]),("passed","unchanged"))
        self.assertEqual(result["worker_budget"]["child_dynamic_decision"]["action"],"apply")
        self.assertFalse(child_api.applied)

    def test_system_unpaced_nonwindows_skip_probe_and_unavailable_is_truthful(self):
        for mode,paced,platform in (("system",True,"nt"),("performance",False,"nt"),("performance",True,"posix")):
            api=Api()
            result=prepare_ue_worker_budget(self.command,paced=paced,mode=mode,api=api,platform_name=platform)
            self.assertEqual((result["status"],result["action"]),("passed","preserve"))
            self.assertEqual(api.calls,[])
            self.assertEqual(result["command"],self.command)
        api=Api(unavailable=True); plan=self.plan(api)
        self.assertEqual((plan["status"],plan["action"]),("failed","preserve"))
        self.assertEqual(plan["command"],self.command)
        self.assertTrue(plan["launch_allowed"])
        child=Api()
        result=apply_owned_process_policy(owned(plan["command"]),self.command[0],paced=True,
                                          api=child,platform_name="nt",worker_budget=plan)
        self.assertEqual(result["status"],"failed")
        self.assertFalse(child.applied)

    @unittest.skipUnless(os.name == "nt", "Actual Windows read-only parent + disposable owned child")
    def test_native_preflight_and_owned_python_child(self):
        command=[sys.executable,"-c","import sys; sys.stdin.read()"]
        plan=prepare_ue_worker_budget(command,paced=True)
        self.assertEqual(plan["status"],"passed",plan)
        self.assertTrue(plan["launch_allowed"])
        self.assertFalse(any(c["api"].startswith("Set") for c in plan["native_calls"]))
        with subprocess.Popen(plan["command"],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) as process:
            try:
                result=apply_owned_process_policy(process,sys.executable,paced=True,worker_budget=plan)
                type(self).native_preflight=plan
                type(self).native_report=result
                self.assertEqual(result["status"],"passed",result)
                self.assertEqual(result["native_process"]["pid"],process.pid)
                if plan["action"]=="apply":
                    self.assertEqual(result["observed_default_cpu_set_ids"],plan["expected_cpu_set_ids"])
                    self.assertEqual(result["worker_budget"]["actual_physical_core_count"],plan["corelimit"])
            finally:
                process.stdin.close()
                process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
