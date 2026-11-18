"""Read-only cloud capture must retain exact bytes, timing labels and memory bounds."""
import hashlib,json,struct,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/integration"))
from localization_cloud_capture import LocalizationCloudCapture,pointcloud_metadata,TOPICS

def cloud(stamp=1000000000,payload=None,**changes):
    fields=[NS(name=name,offset=index*4,datatype=7,count=1) for index,name in enumerate(("x","y","z","intensity"))]
    result=NS(header=NS(frame_id="aim_sentry/odom",stamp=NS(sec=stamp//10**9,nanosec=stamp%10**9)),
              height=1,width=1,point_step=16,row_step=16,is_bigendian=False,is_dense=False,
              fields=fields,data=bytearray(payload if payload is not None else struct.pack("<4f",1,2,3,4)))
    for key,value in changes.items():setattr(result,key,value)
    return result
def context(wall=10,session="s",epoch=0):
    return {"received_monotonic_s":wall,"latest_clock_ns":1000001000,"session":session,"epoch":epoch}

class LocalizationCloudCaptureTests(unittest.TestCase):
    def test_organized_big_endian_padding_and_nan_payload_round_trip(self):
        payload=struct.pack(">4f",1,2,float("nan"),4)+struct.pack(">4f",5,6,7,8)+b"padding!"
        payload+=struct.pack(">4f",9,10,11,12)+struct.pack(">4f",13,14,15,16)+b"padding?"
        message=cloud(payload=payload,height=2,width=2,row_step=40,is_bigendian=True)
        capture=LocalizationCloudCapture();capture.add(TOPICS[0],message,context())
        message.data[0]=0  # The retained bytes must be independent of a reused ROS buffer.
        capture.snapshot("single_goal",context(11),1000000000,1000000000)
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/"raw"
            result=capture.write(output,{"runtime_provenance_sha256":"a"*64})
            manifest=json.loads(Path(result["manifest_path"]).read_text())
            row=manifest["records"][0];stored=(output/row["data_file"]).read_bytes()
            self.assertEqual(stored,payload);self.assertEqual(row["data_sha256"],hashlib.sha256(payload).hexdigest())
            self.assertEqual((row["height"],row["width"],row["point_step"],row["row_step"],row["is_bigendian"]),(2,2,16,40,True))
            self.assertEqual(struct.unpack_from(">f",stored,40)[0],9.)
            self.assertEqual(row["fields"][2],{"name":"z","offset":8,"datatype":7,"count":1})
            with self.assertRaises(FileExistsError):capture.write(output,{})

    def test_exact_nanosecond_match_and_labeled_nearest_are_distinct(self):
        capture=LocalizationCloudCapture()
        capture.add(TOPICS[0],cloud(1000000001),context(8))
        capture.add(TOPICS[1],cloud(1000000000),context(9))
        capture.snapshot("single_goal",context(10),1000000001,1000000001)
        selections=capture.snapshots[0]["topics"]
        self.assertTrue(selections[TOPICS[0]]["obstacle_scan"]["exact_found"])
        row=selections[TOPICS[1]]["obstacle_scan"]
        self.assertFalse(row["exact_found"]);self.assertIsNone(row["exact"])
        self.assertEqual(row["nearest_when_exact_missing"]["stamp_delta_ns"],-1)
        self.assertEqual(selections[TOPICS[1]]["latest_received"]["received_age_s"],1.)

    def test_exact_missing_when_other_session_or_future_receipt(self):
        capture=LocalizationCloudCapture()
        capture.add(TOPICS[0],cloud(),context(20))
        capture.add(TOPICS[1],cloud(),context(9,session="another"))
        capture.snapshot("single_goal",context(10),1000000000,None)
        for row in capture.snapshots[0]["topics"].values():
            self.assertFalse(row["amcl"]["exact_found"]);self.assertIsNone(row["latest_received"])
            self.assertEqual(row["obstacle_scan"]["missing_reason"],"target_stamp_unavailable")

    def test_invalid_layout_and_oversize_are_counted_before_copy(self):
        capture=LocalizationCloudCapture(max_bytes=16)
        capture.add(TOPICS[0],cloud(row_step=8),context())
        message=cloud();message.fields[2].offset=20
        capture.add(TOPICS[0],message,context())
        capture.add(TOPICS[0],cloud(payload=bytes(32),width=2,row_step=32),context())
        self.assertEqual(capture.total_bytes,0)
        self.assertEqual(capture.counters[TOPICS[0]]["invalid"],2)
        self.assertEqual(capture.counters[TOPICS[0]]["oversize"],1)

    def test_ring_count_and_cross_topic_byte_budget_are_bounded(self):
        capture=LocalizationCloudCapture(max_samples=2,max_bytes=48)
        for index in range(5):capture.add(TOPICS[0],cloud(1000000000+index),context(index))
        self.assertEqual(len(capture.rings[TOPICS[0]]),2)
        capture.add(TOPICS[1],cloud(),context(6));capture.add(TOPICS[1],cloud(1000000001),context(7))
        self.assertLessEqual(capture.total_bytes,48);self.assertLessEqual(capture.peak_bytes,48)
        self.assertEqual(capture.counters[TOPICS[0]]["evicted_ring"],3)
        self.assertEqual(capture.counters[TOPICS[0]]["evicted_budget"],1)

    def test_endpoint_pins_are_included_in_global_memory_limit(self):
        capture=LocalizationCloudCapture(max_samples=1,max_bytes=32)
        capture.add(TOPICS[0],cloud(1000000000),context(8))
        capture.add(TOPICS[1],cloud(1000000001),context(9))
        capture.snapshot("single_goal",context(10),1000000000,1000000001)
        capture.add(TOPICS[0],cloud(1000000002),context(11))
        self.assertEqual(capture.total_bytes,32)
        self.assertEqual(capture.counters[TOPICS[0]]["dropped_pinned_budget"],1)
        capture.snapshot("waypoints",context(12),None,None)
        with self.assertRaises(ValueError):capture.snapshot("third",context(13))
        self.assertEqual(len(capture.pinned),2)

if __name__=="__main__":unittest.main()
