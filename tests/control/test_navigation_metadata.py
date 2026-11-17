"""Run in the native ROS environment (PyYAML dependency)."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.navigation_config import prepare

class NavigationMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name);self.map=self.path/'map.yaml';self.map.write_text('image: map.pgm\n')
        self.meta=self.path/'metadata.json'
        self.reference=ROOT/'ros2_ws/src/pb2025_nav_bringup/config/reality/nav2_params.yaml'
    def call(self):return prepare(self.reference,self.map,'aim_sentry',[.6,-.4,.15])
    def test_bom_and_plain_utf8_are_identical(self):
        value={'map_id':'fixture','source_md5':'1'*32};text=json.dumps(value)
        self.meta.write_text(text,encoding='utf-8');plain=self.call()
        self.meta.write_text(text,encoding='utf-8-sig');self.assertEqual(self.call(),plain)
    def test_bom_does_not_bypass_identity_requirement(self):
        self.meta.write_text('{"map_id":"fixture"}',encoding='utf-8-sig')
        with self.assertRaisesRegex(ValueError,'identity'):self.call()
    def test_malformed_json_is_still_rejected(self):
        self.meta.write_text('{bad}',encoding='utf-8-sig')
        with self.assertRaises(json.JSONDecodeError):self.call()

if __name__=='__main__':unittest.main()
