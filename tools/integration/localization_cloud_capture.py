"""Bounded, read-only PointCloud2 capture; no ROS dependency or point conversion."""
from __future__ import annotations
from collections import deque
import hashlib
import json
from pathlib import Path

FIELD_BYTES = {1:1, 2:1, 3:2, 4:2, 5:4, 6:4, 7:4, 8:8}
TOPICS = ("/terrain_map_ext", "/registered_scan")


def pointcloud_metadata(message):
    """Validate storage bounds and preserve the exact declared field layout."""
    dimensions = {key:int(getattr(message,key)) for key in ("height","width","point_step","row_step")}
    if any(value < 0 for value in dimensions.values()):
        raise ValueError("Negative PointCloud2 dimensions")
    if dimensions["width"] and not dimensions["point_step"]:
        raise ValueError("Nonempty cloud has zero point_step")
    if dimensions["row_step"] < dimensions["width"]*dimensions["point_step"]:
        raise ValueError("PointCloud2 row_step is smaller than its points")
    size = dimensions["row_step"]*dimensions["height"]
    if len(message.data) != size:
        raise ValueError("PointCloud2 storage does not match row_step/height")
    fields = []
    for field in message.fields:
        row = {key:getattr(field,key) for key in ("name","offset","datatype","count")}
        if (not isinstance(row["name"],str) or row["datatype"] not in FIELD_BYTES
                or row["count"] <= 0 or row["offset"] < 0
                or row["offset"]+row["count"]*FIELD_BYTES[row["datatype"]] > dimensions["point_step"]):
            raise ValueError("PointCloud2 field exceeds its point storage")
        fields.append(row)
    sec, nanosec = int(message.header.stamp.sec), int(message.header.stamp.nanosec)
    if not 0 <= nanosec < 1000000000:
        raise ValueError("Invalid PointCloud2 nanoseconds")
    return dict(dimensions, header={"frame_id":message.header.frame_id,
                "stamp":{"sec":sec,"nanosec":nanosec},"stamp_ns":sec*1000000000+nanosec},
                fields=fields,is_bigendian=bool(message.is_bigendian),is_dense=bool(message.is_dense),
                data_bytes=size)


class LocalizationCloudCapture:
    def __init__(self, max_samples=12, max_bytes=32*1024*1024):
        if not 1 <= max_samples <= 12 or not 1 <= max_bytes <= 32*1024*1024:
            raise ValueError("Capture budgets exceed the diagnostic contract")
        self.max_samples, self.max_bytes = max_samples, max_bytes
        self.rings = {topic:deque() for topic in TOPICS}
        self.records, self.pinned, self.snapshots = {}, set(), []
        self.total_bytes = self.peak_bytes = self.sequence = 0
        self.counters = {topic:{name:0 for name in ("received","retained","invalid","oversize",
                         "evicted_ring","evicted_budget","dropped_pinned_budget")} for topic in TOPICS}

    def _release(self, identifier):
        if identifier not in self.pinned:
            self.total_bytes -= self.records.pop(identifier)["metadata"]["data_bytes"]

    def add(self, topic, message, context):
        counters = self.counters[topic];counters["received"] += 1
        try:
            metadata = pointcloud_metadata(message)
        except (ValueError, TypeError, AttributeError, OverflowError):
            counters["invalid"] += 1;return
        size = metadata["data_bytes"]
        if size > self.max_bytes:
            counters["oversize"] += 1;return
        ring = self.rings[topic]
        while len(ring) >= self.max_samples:
            self._release(ring.popleft());counters["evicted_ring"] += 1
        while self.total_bytes+size > self.max_bytes:
            candidates = [(identifier,t) for t,queue in self.rings.items()
                          for identifier in queue if identifier not in self.pinned]
            if not candidates:
                counters["dropped_pinned_budget"] += 1;return
            identifier,old_topic = min(candidates)
            self.rings[old_topic].remove(identifier);self._release(identifier)
            self.counters[old_topic]["evicted_budget"] += 1
        self.sequence += 1
        # Copy only after validating and reserving the bounded buffer budget.
        payload = bytes(message.data)
        self.records[self.sequence] = {"metadata":dict(metadata,topic=topic,context=dict(context)),
                                       "payload":payload}
        ring.append(self.sequence);self.total_bytes += size
        self.peak_bytes = max(self.peak_bytes,self.total_bytes);counters["retained"] += 1

    def snapshot(self, mode, context, amcl_stamp_ns=None, scan_stamp_ns=None):
        if len(self.snapshots) >= 2:
            raise ValueError("Only the two existing precision endpoints may be captured")
        endpoint = {"mode":mode,"context":dict(context),"targets":{"amcl":amcl_stamp_ns,
                    "obstacle_scan":scan_stamp_ns},"topics":{}}
        def reference(identifier, target=None):
            meta = self.records[identifier]["metadata"];sample = meta["context"]
            self.pinned.add(identifier)
            return {"record_id":identifier,"stamp_ns":meta["header"]["stamp_ns"],
                    "stamp_delta_ns":meta["header"]["stamp_ns"]-target if target is not None else None,
                    "received_age_s":context["received_monotonic_s"]-sample["received_monotonic_s"],
                    "header_age_to_latest_clock_ns":context.get("latest_clock_ns",0)-meta["header"]["stamp_ns"]}
        for topic,ring in self.rings.items():
            candidates = [identifier for identifier in ring
                if self.records[identifier]["metadata"]["context"].get("session") == context.get("session")
                and self.records[identifier]["metadata"]["context"].get("epoch") == context.get("epoch")
                and self.records[identifier]["metadata"]["context"]["received_monotonic_s"] <= context["received_monotonic_s"]]
            selected = {}
            for name,target in endpoint["targets"].items():
                exact = [identifier for identifier in candidates if target is not None
                         and self.records[identifier]["metadata"]["header"]["stamp_ns"] == target]
                nearest = min(candidates,key=lambda identifier:abs(
                    self.records[identifier]["metadata"]["header"]["stamp_ns"]-target)) if candidates and target is not None else None
                selected[name] = {"requested_stamp_ns":target,"exact_found":bool(exact),
                    "exact":reference(exact[-1],target) if exact else None,
                    "nearest_when_exact_missing":reference(nearest,target) if not exact and nearest is not None else None,
                    "missing_reason":None if exact else "target_stamp_unavailable" if target is None else "exact_stamp_absent_from_bounded_ring"}
            selected["latest_received"] = reference(candidates[-1]) if candidates else None
            endpoint["topics"][topic] = selected
        self.snapshots.append(endpoint)
        return len(self.snapshots)-1

    def summary(self):
        return {"enabled":True,"max_samples_per_topic":self.max_samples,"max_buffer_bytes":self.max_bytes,
                "retained_buffer_bytes":self.total_bytes,"peak_buffer_bytes":self.peak_bytes,
                "retained_records":len(self.records),"pinned_records":len(self.pinned),
                "endpoint_snapshots":len(self.snapshots),"counters":self.counters,
                "memory_scope":"Raw buffers in rings and pinned endpoint snapshots; middleware-owned message memory is external"}

    def write(self, output, bindings):
        output = Path(output);output.mkdir(parents=True,exist_ok=False)
        records = []
        for identifier in sorted(self.pinned):
            row = self.records[identifier];payload = row["payload"]
            target = output/("pointcloud2-%06d.bin"%identifier)
            with target.open("xb") as stream:stream.write(payload)
            records.append(dict(row["metadata"],record_id=identifier,data_file=target.name,
                                data_sha256=hashlib.sha256(payload).hexdigest()))
        report = {"status":"completed","acceptance_scope":"optional raw localization diagnostic; exact matches and missing samples are reported separately",
                  "schema":"rmsim_raw_pointcloud2_endpoints_v1","helper_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  **bindings,"capture":self.summary(),"snapshots":self.snapshots,"records":records,
                  "scope":"Unmodified PointCloud2 data bytes and layout; no values are published or fed into localization/control"}
        target = output/"verification.json"
        with target.open("x",encoding="utf-8") as stream:
            json.dump(report,stream,indent=2,allow_nan=False);stream.write("\n")
        return {"enabled":True,"status":"completed","manifest_path":str(target),
                "manifest_sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"capture":self.summary()}
