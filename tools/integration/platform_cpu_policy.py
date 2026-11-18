"""Select soft Windows CPU Sets for one owned, paced UE navigation process.

CPU Sets leave OS scheduling and per-thread selections intact. The only setter
used here is SetProcessDefaultCpuSets on the borrowed Popen process handle.
See https://learn.microsoft.com/windows/win32/procthread/cpu-sets and
https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-system_cpu_set_information.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import struct
import subprocess
import time

SCHEMA_VERSION = 1
MAX_TOPOLOGY_BYTES = 1024 * 1024
MAX_CPU_SETS = 32768


def decode_cpu_sets(raw: bytes) -> list[dict]:
    """Decode variable-size SYSTEM_CPU_SET_INFORMATION records, including bounds."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_TOPOLOGY_BYTES:
        raise ValueError("Invalid CPU Set topology buffer")
    result, ids, processors = [], set(), set()
    offset = 0
    while offset < len(raw):
        if len(raw)-offset < 8:
            raise ValueError("Truncated CPU Set record header")
        size, kind = struct.unpack_from("<II", raw, offset)
        if size < 8 or offset+size > len(raw):
            raise ValueError("Invalid CPU Set record size")
        if kind == 0:
            if size < 32:
                raise ValueError("Truncated CPU Set information")
            fields = struct.unpack_from("<IH6B", raw, offset+8)
            row = dict(zip(("id", "group", "logical_processor", "core", "cache", "numa",
                            "efficiency_class", "flags"), fields))
            key = (row["group"], row["logical_processor"])
            if row["logical_processor"] >= 64 or row["id"] in ids or key in processors:
                raise ValueError("Duplicate or invalid CPU Set processor")
            ids.add(row["id"]); processors.add(key)
            row["scheduling_class"] = raw[offset+20]
            row["allocation_tag"] = struct.unpack_from("<Q", raw, offset+24)[0]
            result.append(row)
        offset += size  # Future record kinds must be skipped using their own size.
    if not result or len(result) > MAX_CPU_SETS:
        raise ValueError("No supported CPU Sets or excessive topology size")
    return result


def select_performance_cpu_sets(topology: list[dict], before: dict) -> dict:
    """Keep explicit restrictions; select the highest reported performance class."""
    if not topology:
        raise ValueError("Empty CPU topology")
    ids, processors = set(), set()
    for row in topology:
        for name, maximum in (("id", 2**32-1), ("group", 65535),
                              ("logical_processor", 63), ("efficiency_class", 255), ("flags", 255)):
            value = row.get(name)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError("Invalid CPU Set field: "+name)
        key = (row["group"], row["logical_processor"])
        if row["id"] in ids or key in processors:
            raise ValueError("Duplicate CPU Set identity")
        ids.add(row["id"]); processors.add(key)
    existing = before.get("default_cpu_sets")
    if (not isinstance(existing, list) or any(type(x) is not int or x not in ids for x in existing)
            or len(set(existing)) != len(existing)):
        raise ValueError("Invalid existing CPU Set assignment")
    decision = {"action": "preserve", "requested_cpu_set_ids": [],
                "highest_efficiency_class": max(row["efficiency_class"] for row in topology)}
    if existing:
        return dict(decision, reason="existing_explicit_cpu_sets")
    groups = {row["group"] for row in topology}
    if len(groups) != 1:
        return dict(decision, reason="multiple_processor_groups_preserve_system")
    affinity = before.get("affinity", {})
    process_mask, system_mask = affinity.get("process_mask"), affinity.get("system_mask")
    if any(type(x) is not int or x < 0 for x in (process_mask, system_mask)):
        raise ValueError("Invalid process affinity observation")
    if not process_mask or not system_mask or process_mask & ~system_mask:
        return dict(decision, reason="affinity_cannot_be_proven_unrestricted")
    if process_mask != system_mask:
        return dict(decision, reason="existing_restrictive_affinity")
    if len({row["efficiency_class"] for row in topology}) == 1:
        return dict(decision, reason="homogeneous_cpu_classes")
    selected = sorted(row["id"] for row in topology
                      if row["efficiency_class"] == decision["highest_efficiency_class"]
                      and not (row["flags"] & 2 and not row["flags"] & 4)
                      and process_mask & (1 << row["logical_processor"]))
    if not selected:
        return dict(decision, reason="highest_class_unavailable_to_owned_process")
    return dict(decision, action="apply", reason="highest_available_performance_class",
                requested_cpu_set_ids=selected)


class WindowsCpuSetApi:
    """Narrow native API wrapper; all handle ownership stays with subprocess.Popen."""
    def __init__(self):
        self.calls = []
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle, u32, ptr = ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER
        definitions = {
            "GetCurrentProcess": ([], handle),
            "GetProcessId": ([handle], u32),
            "GetExitCodeProcess": ([handle, ptr(u32)], ctypes.c_int),
            "QueryFullProcessImageNameW": ([handle, u32, ctypes.c_wchar_p, ptr(u32)], ctypes.c_int),
            "GetProcessAffinityMask": ([handle, ptr(ctypes.c_size_t), ptr(ctypes.c_size_t)], ctypes.c_int),
            "GetPriorityClass": ([handle], u32),
            "GetProcessInformation": ([handle, ctypes.c_int, handle, u32], ctypes.c_int),
            "GetSystemCpuSetInformation": ([handle, u32, ptr(u32), handle, u32], ctypes.c_int),
            "GetProcessDefaultCpuSets": ([handle, ptr(u32), u32, ptr(u32)], ctypes.c_int),
            "SetProcessDefaultCpuSets": ([handle, ptr(u32), u32], ctypes.c_int),
        }
        for name, (arguments, returned) in definitions.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, returned

    def _call(self, name, *args, sizing=False):
        ctypes.set_last_error(0)
        value = getattr(self.kernel, name)(*args)
        error = ctypes.get_last_error()
        self.calls.append({"api": name, "succeeded": bool(value), "last_error": error,
                           "expected_buffer_query": sizing and not value and error == 122})
        if not value and not (sizing and error == 122):
            raise OSError(error, name+" failed")
        return value

    def identity(self, handle):
        pid = self._call("GetProcessId", handle)
        exit_code = ctypes.c_uint32()
        self._call("GetExitCodeProcess", handle, ctypes.byref(exit_code))
        size = ctypes.c_uint32(32768); buffer = ctypes.create_unicode_buffer(size.value)
        self._call("QueryFullProcessImageNameW", handle, 0, buffer, ctypes.byref(size))
        return {"pid": pid, "exit_code": exit_code.value, "executable": buffer.value}

    def current_handle(self):
        # Pseudo-handle borrowed for read-only preflight; it is never closed or set.
        return self._call("GetCurrentProcess")

    def cpu_sets(self, handle):
        size = ctypes.c_uint32()
        self._call("GetProcessDefaultCpuSets", handle, None, 0, ctypes.byref(size), sizing=True)
        for _ in range(3):
            if size.value > MAX_CPU_SETS:
                raise ValueError("Excessive process CPU Set count")
            if not size.value:
                return []
            count = size.value; buffer = (ctypes.c_uint32 * count)()
            ok = self._call("GetProcessDefaultCpuSets", handle, buffer, count, ctypes.byref(size), sizing=True)
            if ok and size.value <= count:
                return sorted(buffer[:size.value])
        raise RuntimeError("CPU Set assignment changed during bounded read")

    def topology(self, handle):
        size = ctypes.c_uint32()
        self._call("GetSystemCpuSetInformation", None, 0, ctypes.byref(size), handle, 0, sizing=True)
        for _ in range(3):
            if not 32 <= size.value <= MAX_TOPOLOGY_BYTES:
                raise ValueError("Invalid topology allocation size")
            count = size.value; buffer = ctypes.create_string_buffer(count)
            ok = self._call("GetSystemCpuSetInformation", buffer, count, ctypes.byref(size), handle, 0, sizing=True)
            if ok and size.value <= count:
                return decode_cpu_sets(buffer.raw[:size.value])
        raise RuntimeError("CPU topology changed during bounded read")

    def restrictions(self, handle):
        process, system = ctypes.c_size_t(), ctypes.c_size_t()
        self._call("GetProcessAffinityMask", handle, ctypes.byref(process), ctypes.byref(system))
        power = (ctypes.c_uint32 * 3)(1, 0, 0)
        self._call("GetProcessInformation", handle, 4, power, ctypes.sizeof(power))
        return {"affinity": {"process_mask": process.value, "system_mask": system.value},
                "priority_class": self._call("GetPriorityClass", handle),
                "power_throttling": {"version": power[0], "control_mask": power[1], "state_mask": power[2]}}

    def observe(self, handle):
        return dict(self.restrictions(handle), default_cpu_sets=self.cpu_sets(handle))

    def apply(self, handle, ids):
        if not ids:
            raise ValueError("Refusing to clear CPU Set assignments")
        self._call("SetProcessDefaultCpuSets", handle, (ctypes.c_uint32 * len(ids))(*ids), len(ids))


def _same_windows_path(left, right):
    return ntpath.normcase(ntpath.normpath(str(left))) == ntpath.normcase(ntpath.normpath(str(right)))


def worker_budget_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _selected_core_counts(topology, identifiers):
    rows = [row for row in topology if row["id"] in identifiers]
    if len(rows) != len(identifiers) or not rows:
        raise ValueError("Selected CPU Set identity is incomplete")
    if any(type(row.get("core")) is not int or not 0 <= row["core"] <= 255 for row in rows):
        raise ValueError("Invalid physical core index")
    return len({(row["group"], row["core"]) for row in rows}), len(rows)


def _bounded_core_command(command, count):
    """Preserve matching explicit limits; reject duplicate/ambiguous conflicts."""
    found = {}
    for argument in command:
        lower = argument.lower()
        for key in ("corelimit", "physicalcorelimit"):
            if re.match(r"^-+"+key, lower):
                match = re.fullmatch(r"-"+key+r"=(\d+)", lower)
                if not match or key in found:
                    raise ValueError("Ambiguous or duplicate explicit UE "+key)
                number = int(match[1])
                if number != count:
                    raise ValueError("Explicit UE "+key+" conflicts with selected physical core budget")
                found[key] = argument
    if "corelimit" in found:
        return list(command), found["corelimit"], "existing_matching"
    argument = "-corelimit="+str(count)
    return [*command, argument], argument, "automatic"


def prepare_ue_worker_budget(command, *, paced, mode="performance", api=None, platform_name=None):
    """Read inherited restrictions before UE initializes any worker pools.

    -corelimit caps both UE physical/logical counts. Count unique physical cores
    in the selected CPU Sets, including SMT siblings once. No setter is used here.
    """
    platform_name = os.name if platform_name is None else platform_name
    report = {"schema_version": 1, "status": "failed", "action": "preserve",
              "outcome": "unchanged", "launch_allowed": True, "requested_policy": mode,
              "paced_navigation": bool(paced), "platform": platform_name, "launcher_pid": os.getpid(),
              "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "started_monotonic_s": time.monotonic(), "command_before": [], "command": [],
              "expected_cpu_set_ids": [], "physical_core_count": None, "logical_processor_count": None,
              "corelimit": None, "corelimit_argument": None, "corelimit_source": "unchanged",
              "expected_windows_worker_threads_to_spawn": None, "checks": [], "native_calls": []}

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        if not passed:
            raise ValueError(name)

    try:
        check("valid_requested_policy", mode in ("performance", "system"))
        check("explicit_ue_argument_vector", isinstance(command, (list, tuple)) and bool(command))
        report["command_before"] = report["command"] = [str(value) for value in command]
        if mode == "system" or not paced or platform_name != "nt":
            report.update(status="passed", reason="system_policy_requested" if mode == "system" else
                          "unpaced_navigation" if not paced else "non_windows_system_policy")
            return report
        api = api if api is not None else WindowsCpuSetApi()
        handle = api.current_handle()
        parent = report["parent_process"] = api.identity(handle)
        check("readonly_current_parent_identity", parent["pid"] == os.getpid() and parent["exit_code"] == 259)
        before = report["parent_before"] = api.observe(handle)
        topology = report["topology"] = api.topology(handle)
        decision = report["decision"] = select_performance_cpu_sets(topology, before)
        report["reason"] = decision["reason"]
        if decision["action"] != "apply":
            report["status"] = "passed"
            return report
        identifiers = decision["requested_cpu_set_ids"]
        selected = [row for row in topology if row["id"] in identifiers]
        if any(row["flags"] & 2 for row in selected):
            # A set allocated to this parent need not be available to a new child.
            report.update(status="passed", reason="parent_allocated_cpu_sets_cannot_predict_child")
            return report
        physical, logical = _selected_core_counts(topology, identifiers)
        report.update(expected_cpu_set_ids=identifiers, physical_core_count=physical,
                      logical_processor_count=logical)
        try:
            augmented, argument, source = _bounded_core_command(report["command_before"], physical)
        except ValueError:
            report["launch_allowed"] = False
            raise
        report.update(status="passed", action="apply", outcome="limited", command=augmented,
                      corelimit=physical, corelimit_argument=argument, corelimit_source=source,
                      expected_windows_worker_threads_to_spawn=max(physical-1, 2))
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as error:
        report.update(status="failed", reason="worker_budget_preflight_failed", error=repr(error))
    finally:
        report["native_calls"] = list(api.calls) if api is not None else []
        report["completed_monotonic_s"] = time.monotonic()
        report["command_sha256"] = worker_budget_digest(report["command"])
    return report


def apply_owned_process_policy(process, expected_executable, *, paced, mode="performance", api=None,
                               platform_name=None, worker_budget=None):
    """Return truthful observations; a failed policy leaves launch available for diagnostics."""
    platform_name = os.name if platform_name is None else platform_name
    report = {"schema_version": SCHEMA_VERSION, "status": "failed", "mode": "owned_process_cpu_sets",
              "requested_policy": mode, "paced_navigation": bool(paced), "platform": platform_name,
              "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "started_monotonic_s": time.monotonic(), "pid": getattr(process, "pid", None), "launcher_pid": os.getpid(),
              "expected_executable": str(expected_executable), "requested_cpu_set_ids": [],
              "scope": "UE process default soft CPU Sets; OS scheduling and explicit thread policy retained",
              "checks": [], "native_calls": []}

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        if not passed:
            raise ValueError(name)

    try:
        check("valid_requested_policy", mode in ("performance", "system"))
        check("owned_live_popen", isinstance(process, subprocess.Popen)
              and type(process.pid) is int and process.pid > 0 and process.poll() is None)
        command = process.args
        check("owned_executable_argument_matches", isinstance(command, (list, tuple)) and bool(command)
              and _same_windows_path(command[0], expected_executable))
        report["command"] = [str(value) for value in command]
        if worker_budget is not None:
            check("worker_budget_preflight_passed", isinstance(worker_budget, dict)
                  and worker_budget.get("status") == "passed" and worker_budget.get("launch_allowed") is True)
            check("worker_budget_request_and_source_match",
                  worker_budget.get("requested_policy") == mode
                  and worker_budget.get("paced_navigation") == bool(paced)
                  and worker_budget.get("platform") == platform_name
                  and worker_budget.get("helper_sha256") == report["helper_sha256"])
            check("worker_budget_actual_command_matches_declared",
                  worker_budget.get("command") == report["command"]
                  and worker_budget.get("command_sha256") == worker_budget_digest(report["command"]))
            report["worker_budget"] = {
                "preflight_sha256": worker_budget_digest(worker_budget),
                "declared_action": worker_budget.get("action"), "corelimit": worker_budget.get("corelimit"),
                "corelimit_argument": worker_budget.get("corelimit_argument"),
                "expected_cpu_set_ids": worker_budget.get("expected_cpu_set_ids"),
                "expected_physical_core_count": worker_budget.get("physical_core_count"),
                "expected_logical_processor_count": worker_budget.get("logical_processor_count"),
                "actual_ue_command_sha256": worker_budget_digest(report["command"])}
        if mode == "system" or not paced or platform_name != "nt":
            report.update(status="passed", outcome="unchanged",
                          reason="system_policy_requested" if mode == "system" else
                          "unpaced_navigation" if not paced else "non_windows_system_policy")
            return report
        handle = getattr(process, "_handle", None)
        check("borrowed_popen_handle_present", handle is not None and int(handle) not in (0, -1))
        api = api if api is not None else WindowsCpuSetApi()
        report["handle_source"] = "owned_subprocess_popen_handle"
        native = report["native_process"] = api.identity(handle)
        check("native_process_identity_matches_owned_popen", native["pid"] == process.pid
              and native["exit_code"] == 259
              and _same_windows_path(native["executable"], expected_executable))
        before = report["before"] = api.observe(handle)
        topology = report["topology"] = api.topology(handle)
        decision = report["decision"] = select_performance_cpu_sets(topology, before)
        if worker_budget is not None:
            bound = report["worker_budget"]
            bound["child_dynamic_decision"] = dict(decision)
            if worker_budget["action"] == "apply":
                check("worker_budget_actual_cpu_sets_match_preflight",
                      decision["action"] == "apply"
                      and decision["requested_cpu_set_ids"] == worker_budget["expected_cpu_set_ids"])
                physical, logical = _selected_core_counts(topology, decision["requested_cpu_set_ids"])
                bound.update(actual_physical_core_count=physical, actual_logical_processor_count=logical)
                check("worker_budget_actual_core_counts_match_preflight",
                      physical == worker_budget["physical_core_count"] == worker_budget["corelimit"]
                      and logical == worker_budget["logical_processor_count"])
                same, argument, source = _bounded_core_command(report["command"], physical)
                check("worker_budget_corelimit_present_before_ue_startup",
                      source == "existing_matching" and same == report["command"]
                      and argument == worker_budget["corelimit_argument"])
            else:
                check("worker_budget_preserve_declaration", worker_budget["action"] == "preserve"
                      and worker_budget["corelimit"] is None)
                decision = report["decision"] = dict(decision, action="preserve", requested_cpu_set_ids=[],
                    reason="worker_budget_preflight_preserve:"+worker_budget["reason"])
        report["requested_cpu_set_ids"] = decision["requested_cpu_set_ids"]
        report["reason"] = decision["reason"]
        if decision["action"] == "apply":
            # Recheck liveness on the same handle immediately before the only mutation.
            check("owned_process_still_live_before_apply", process.poll() is None)
            api.apply(handle, decision["requested_cpu_set_ids"])
        after = report["after"] = api.observe(handle)
        report["observed_default_cpu_set_ids"] = after["default_cpu_sets"]
        expected = decision["requested_cpu_set_ids"] if decision["action"] == "apply" else before["default_cpu_sets"]
        check("native_cpu_sets_readback_matches_decision", after["default_cpu_sets"] == expected)
        check("affinity_priority_and_power_policy_unchanged",
              all(after[key] == before[key] for key in ("affinity", "priority_class", "power_throttling")))
        report.update(status="passed", outcome="applied" if decision["action"] == "apply" else "unchanged")
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as error:
        report.update(status="failed", error=repr(error))
    finally:
        report["native_calls"] = list(api.calls) if api is not None else []
        report["completed_monotonic_s"] = time.monotonic()
    return report
