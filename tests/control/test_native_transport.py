"""Local DDS fallback retains loopback scope and rejects ambiguous profiles."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.native_transport import loopback_environment


class NativeTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.profile=Path(self.temp.name)/'dds.xml'
        self.source=(ROOT/'ros2_ws/src/rmsim_bridge/config/fastdds-loopback-udp.xml').read_text(encoding='utf-8')
        self.profile.write_text(self.source,encoding='utf-8')

    def rejects(self,before,after):
        self.assertIn(before,self.source)
        self.profile.write_text(self.source.replace(before,after),encoding='utf-8')
        with self.assertRaises(ValueError):
            loopback_environment({},self.profile)

    def test_valid_profile_preserves_other_environment_and_does_not_mutate_input(self):
        original={'ROS_DOMAIN_ID':'173','ROS_LOCALHOST_ONLY':'1','RMW_IMPLEMENTATION':'rmw_fastrtps_cpp'}
        result,report=loopback_environment(original,self.profile)
        self.assertEqual(original['ROS_LOCALHOST_ONLY'],'1')
        self.assertEqual(result['ROS_DOMAIN_ID'],'173')
        self.assertEqual(report['allowed_interfaces'],['127.0.0.1'])
        self.assertEqual(report['mode'],'loopback_udp');self.assertEqual(len(report['profile_sha256']),64)
        self.assertEqual(result['FASTRTPS_DEFAULT_PROFILES_FILE'],str(self.profile.resolve()))
        self.assertEqual(result['ROS_LOCALHOST_ONLY'],'0')

    def test_non_loopback_rejected(self):self.rejects('127.0.0.1','192.168.1.1')
    def test_wildcard_rejected(self):self.rejects('127.0.0.1','0.0.0.0')
    def test_extra_interface_rejected(self):self.rejects('</interfaceWhiteList>','<address>192.168.1.1</address></interfaceWhiteList>')
    def test_empty_identity_rejected(self):self.rejects('rmsim_loopback_udp','')
    def test_shm_rejected(self):self.rejects('<type>UDPv4</type>','<type>SHM</type>')
    def test_builtins_rejected(self):self.rejects('<useBuiltinTransports>false','<useBuiltinTransports>true')
    def test_duplicate_builtins_rejected(self):self.rejects('</rtps>','<useBuiltinTransports>true</useBuiltinTransports></rtps>')
    def test_nondefault_rejected(self):self.rejects('is_default_profile="true"','is_default_profile="false"')
    def test_unknown_transport_rejected(self):self.rejects('<userTransports><transport_id>rmsim_loopback_udp','<userTransports><transport_id>other')
    def test_external_config_rejected(self):
        with self.assertRaises(ValueError):loopback_environment({'FASTDDS_ENVIRONMENT_FILE':'other.json'},self.profile)
    def test_wrong_rmw_rejected(self):
        with self.assertRaises(ValueError):loopback_environment({'RMW_IMPLEMENTATION':'rmw_cyclonedds_cpp'},self.profile)
    def test_missing_profile_rejected(self):
        self.profile.unlink()
        with self.assertRaises(FileNotFoundError):loopback_environment({},self.profile)
    def test_doctype_rejected(self):self.rejects('<dds xmlns=','<!DOCTYPE dds><dds xmlns=')


if __name__=='__main__':unittest.main()
