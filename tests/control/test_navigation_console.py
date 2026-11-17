"""Console scene identity and RViz configuration contracts; no ROS/UE required."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/integration'))
import navigation_console_config as config

class NavigationConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'assets/maps/rmuc2027/navigation-console';self.source.mkdir(parents=True)
        self.scene={'kind':'rm_mesh_map','id':'rmuc2027','meshes':[{'id':'floor','collision_asset':'/Game/floor'}],'spawns':[]}
        (self.source/'scene.json').write_text(json.dumps(self.scene))
        (self.source/'map.pgm').write_bytes(b'P5\n2 2\n255\n'+bytes([0,254,254,0]))
        (self.source/'map.yaml').write_text('image: map.pgm\nresolution: 0.05\norigin: [0,0,0]\n')
        self.spec={'scene_geometry_sha256':config.scene_geometry(self.scene),'raster_sha256':config.digest(self.source/'map.pgm'),'map_metadata':{'map_id':'rmuc2027'}}
        (self.source/'SOURCE.json').write_text(json.dumps(self.spec))
        self.mock=patch.object(config,'ROOT',self.root);self.mock.start();self.addCleanup(self.mock.stop)
    def test_snapshot_retries_a_transient_windows_sharing_violation(self):
        path=self.root/'status.json';original=Path.replace;count=[0]
        def replace(value,target):
            count[0]+=1
            if count[0]==1:raise PermissionError('reader holds a Windows handle')
            return original(value,target)
        with patch.object(Path,'replace',replace),patch.object(config.time,'sleep'):
            self.assertTrue(config.atomic_json(path,{'phase':'ready'}))
        self.assertEqual(json.loads(path.read_text()),{'phase':'ready'})
        self.assertEqual(count[0],2)
    def test_snapshot_lock_is_bounded_and_preserves_previous_evidence(self):
        path=self.root/'status.json';path.write_text('{"old":true}')
        with patch.object(Path,'replace',side_effect=PermissionError('locked')),patch.object(config.time,'sleep'):
            self.assertFalse(config.atomic_json(path,{'new':True},attempts=2))
        self.assertEqual(json.loads(path.read_text()),{'old':True})
    def test_scene_hash_matches_exact_emitted_bytes(self):
        result=config.prepare_scene('rmuc2027',self.root/'run')
        raw=Path(result['scene_file']).read_bytes();metadata=json.loads((self.root/'run/navigation/metadata.json').read_text())
        self.assertEqual(metadata['source_md5'],hashlib.md5(raw).hexdigest())
        self.assertEqual(result['scene_sha256'],hashlib.sha256(raw).hexdigest())
    def test_changed_raster_is_rejected(self):
        (self.source/'map.pgm').write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError,'raster'):config.prepare_scene('rmuc2027',self.root/'run')
    def test_changed_geometry_is_rejected(self):
        self.scene['meshes'][0]['collision_asset']='/Game/other'
        (self.source/'scene.json').write_text(json.dumps(self.scene))
        with self.assertRaisesRegex(ValueError,'geometry'):config.prepare_scene('rmuc2027',self.root/'run')
    def test_unknown_scene_rejected(self):
        with self.assertRaises(ValueError):config.prepare_scene('../other',self.root/'run')
    def test_existing_evidence_not_overwritten(self):
        config.prepare_scene('rmuc2027',self.root/'run')
        with self.assertRaises(FileExistsError):config.prepare_scene('rmuc2027',self.root/'run')
    def test_whitespace_does_not_change_geometry(self):
        self.assertEqual(config.scene_geometry(self.scene),config.scene_geometry(json.loads(json.dumps(self.scene,indent=4))))
    def test_rviz_uses_estimated_frames_and_real_topics(self):
        data=config.rviz_configuration({'x':0,'y':0,'scale':24});manager=data['Visualization Manager']
        self.assertEqual(manager['Global Options']['Fixed Frame'],'aim_sentry/map')
        self.assertIn({'Class':'nav2_rviz_plugins/GoalTool'},manager['Tools'])
        self.assertNotIn('/evaluation/',json.dumps(data))
        topics=[d['Topic']['Value'] for d in manager['Displays'] if 'Topic' in d]
        for topic in ('/map','/plan','/registered_scan','/obstacle_scan','/waypoints'):self.assertIn(topic,topics)

if __name__=='__main__':unittest.main()
