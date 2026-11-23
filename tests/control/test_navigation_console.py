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
        self.scene={'kind':'rm_mesh_map','id':'rmuc2027','units':'m','coordinate_system':'ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP','meshes':[{'id':'floor','collision_asset':'/Game/floor'}],'spawns':[{'id':'default','position_m':[5.8,6.4,0.03147864],'yaw_rad':0.}]}
        (self.source/'scene.json').write_text(json.dumps(self.scene))
        (self.source.parent/'map.json').write_text(json.dumps(self.scene))
        (self.source.parent/'mesh-source.json').write_text(json.dumps({'source_to_sim_translation_m':[0.,0.,0.]}))
        (self.source/'map.pgm').write_bytes(b'P5\n2 2\n255\n'+bytes([0,254,254,0]))
        (self.source/'map.yaml').write_text('image: map.pgm\nresolution: 0.05\norigin: [0,0,0]\n')
        self.spec={'scene_geometry_sha256':config.scene_geometry(self.scene),'raster_sha256':config.digest(self.source/'map.pgm'),'map_metadata':{'map_id':'rmuc2027'}}
        (self.source/'SOURCE.json').write_text(json.dumps(self.spec))
        self.mock=patch.object(config,'ROOT',self.root);self.mock.start();self.addCleanup(self.mock.stop)
    def test_surveyed_ground_uses_only_the_selected_map_package(self):
        ref = self.source / 'terrain-ground.bin'
        ref.write_bytes(b'ground fixture')
        self.spec['surveyed_ground'] = {'file': ref.name, 'sha256': config.digest(ref)}
        (self.source/'SOURCE.json').write_text(json.dumps(self.spec))
        result = config.prepare_scene('rmuc2027', self.root/'survey')
        nav = Path(result['map_yaml']).parent
        self.assertEqual((nav/ref.name).read_bytes(), ref.read_bytes())
        self.assertEqual(json.loads((nav/'metadata.json').read_text())['surveyed_ground'], self.spec['surveyed_ground'])
        ref.write_bytes(b'stale heights')
        with self.assertRaisesRegex(ValueError, 'Surveyed ground'):
            config.prepare_scene('rmuc2027', self.root/'changed')
        ref.unlink()
        missing=config.prepare_scene('rmuc2027', self.root/'missing')
        info=json.loads((Path(missing['map_yaml']).parent/'metadata.json').read_text())
        self.assertEqual(info['surveyed_ground_status'], 'reference_missing')
        self.assertNotIn('surveyed_ground', info)

    def test_release_keeps_generated_map_manifest_immutable(self):
        manifest = self.source/'SOURCE.json'
        manifest_bytes = manifest.read_bytes()
        survey, support = self.source/'terrain-ground.bin', self.source/'terrain-support.bin'
        survey.write_bytes(b'masked heights')
        support.write_bytes(b'plain heights')
        template = self.source/'navigation-profile.yaml'
        template.write_text('ground_support_file: terrain-support.bin\n', encoding='utf-8')
        release = {
            'schema': 'rmsim_navigation_release_v1',
            'map_package_sha256': config.digest(manifest),
            'surveyed_ground': {'file': survey.name, 'sha256': config.digest(survey)},
            'navigation_clearance': {'preferred_curve_m': .84, 'inflation_radius_m': .45},
            'simulation_navigation': {
                'file': template.name, 'sha256': config.digest(template),
                'ground_support': {'file': support.name, 'sha256': config.digest(support)},
            },
        }
        sidecar = self.source/'navigation-release.json'
        sidecar.write_text(json.dumps(release), encoding='utf-8')
        output = self.root/'packaged'
        scene = config.prepare_scene('rmuc2027', output/'scene')
        navigation = Path(scene['map_yaml']).parent
        metadata = json.loads((navigation/'metadata.json').read_text())
        self.assertEqual(metadata['surveyed_ground'], release['surveyed_ground'])
        self.assertEqual(metadata['navigation_clearance'], release['navigation_clearance'])
        self.assertEqual(manifest.read_bytes(), manifest_bytes)
        self.assertEqual((navigation/survey.name).read_bytes(), survey.read_bytes())
        runtime = config.select_navigation_source(scene, 'simulation', output)
        self.assertEqual(runtime.read_text(), 'ground_support_file: '+json.dumps(str(support.resolve()))+'\n')
        self.assertEqual(config.select_navigation_source(scene, 'simulation', output, template), template.resolve())
        release['map_package_sha256'] = '0'*64
        sidecar.write_text(json.dumps(release), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Navigation release differs'):
            config.prepare_scene('rmuc2027', self.root/'mismatched')

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
