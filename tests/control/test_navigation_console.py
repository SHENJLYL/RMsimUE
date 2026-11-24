"""Console scene identity and RViz configuration contracts; no ROS/UE required."""
import hashlib
import io
import json
from contextlib import redirect_stderr
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/integration'))
import external_sentry_connector as external
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
        for reference in ('terrain-support.bin', "'terrain-support.bin'",
                          json.dumps(r'Q:\old-checkout\maps\terrain-support.bin')):
            with self.subTest(reference=reference):
                original = f'ground_support_file: {reference}\ncurve_clearance: 0.60\n'
                template.write_text(original, encoding='utf-8')
                scene['simulation_navigation_package']['profile']['sha256'] = config.digest(template)
                runtime = config.select_navigation_source(scene, 'simulation', output)
                self.assertEqual(runtime.read_text(), 'ground_support_file: '+json.dumps(str(support.resolve()))+
                                 '\ncurve_clearance: 0.60\n')
                self.assertEqual(template.read_text(), original)
        template.write_text('ground_support_file: other-grid.bin\n', encoding='utf-8')
        scene['simulation_navigation_package']['profile']['sha256'] = config.digest(template)
        with self.assertRaisesRegex(ValueError, 'must reference its declared'):
            config.select_navigation_source(scene, 'simulation', output)
        self.assertEqual(config.select_navigation_source(scene, 'simulation', output, template), template.resolve())

        # Reproduce an editor changing the YAML and updating only the old top-level digest.
        template.write_bytes(b'ground_support_file: terrain-support.bin\r\ncurve_clearance: 0.62\r\n')
        release['source_sha256'] = config.digest(template)
        sidecar.write_text(json.dumps(release), encoding='utf-8')
        legacy_scene = config.prepare_scene('rmuc2027', self.root/'legacy')
        warning = io.StringIO()
        with redirect_stderr(warning):
            runtime = config.select_navigation_source(legacy_scene, 'simulation', output)
        self.assertIn('matched legacy source_sha256', warning.getvalue())
        self.assertTrue(runtime.read_text().endswith('curve_clearance: 0.62\n'))
        self.assertEqual(json.loads(sidecar.read_text()), release)

        support_bytes = support.read_bytes()
        support.write_bytes(b'different support grid')
        with redirect_stderr(io.StringIO()), self.assertRaisesRegex(ValueError, 'RMUC support grid differs'):
            config.select_navigation_source(legacy_scene, 'simulation', output)
        support.write_bytes(support_bytes)

        template.write_bytes(template.read_bytes() + b'# changed without either digest\r\n')
        with self.assertRaisesRegex(ValueError, 'set simulation_navigation.sha256'):
            config.select_navigation_source(legacy_scene, 'simulation', output)

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

    def test_external_connector_writes_rviz_and_uses_the_running_scenes_map_and_spawn(self):
        self.scene['spawns'].append({'id':'red','position_m':[8.,2.,0.], 'yaw_rad':1.})
        runtime_map = self.source.parent/'map.json'
        runtime_map.write_bytes(json.dumps(self.scene, indent=4).encode())
        rviz = self.root/'artifacts/runtime/rviz/windows/rviz2.exe'
        rviz.parent.mkdir(parents=True)
        rviz.touch()
        commands = []

        def launch(command, **kwargs):
            commands.append(command)
            process = Mock(pid=len(commands), returncode=0)
            process.poll.return_value = 0 if command[0] == str(rviz) else None
            return process

        argv = ['external_sentry_connector.py','--robot-id','sentry_test','--spawn-id','red']
        with (patch.object(external,'ROOT',self.root), patch.object(sys,'argv',argv),
              patch.dict(external.os.environ), patch.object(external,'configure_navigation_transport'),
              patch.object(external.shutil,'which',return_value='ros2.exe'),
              patch.object(external.subprocess,'Popen',side_effect=launch)):
            self.assertEqual(external.main(), 0)
        self.assertEqual(len(commands), 3)
        nav = commands[1]
        self.assertIn('initial_pose:=[8.0, 2.0, 1.0]', nav)
        map_yaml = Path(next(value.split(':=', 1)[1] for value in nav if value.startswith('map_yaml:=')))
        metadata = json.loads(map_yaml.with_name('metadata.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['source_md5'], hashlib.md5(runtime_map.read_bytes()).hexdigest())
        self.assertEqual(map_yaml.parents[1].joinpath('scene.json').read_bytes(), runtime_map.read_bytes())
        manager = json.loads(Path(commands[2][2]).read_text(encoding='utf-8'))['Visualization Manager']
        self.assertEqual(manager['Global Options']['Fixed Frame'], 'sentry_test/map')
        view = manager['Views']['Current']
        self.assertEqual((view['X'],view['Y'],view['Scale']), (8.,2.,65.))
        self.assertIn('use_sim_time:=true', commands[2])

if __name__=='__main__':unittest.main()
