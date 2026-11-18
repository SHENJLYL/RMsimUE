"""Portable default selection and strict reconstruction contracts for map packages."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ros2_ws/src/rmsim_bridge'))
sys.path.insert(0,str(ROOT/'tools/integration'))
from rmsim_bridge.navigation_map_identity import packaged_localization_map, validate_localization_map
import navigation_console_config as console_config


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PackagedLocalizationMapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.package=self.root/'assets/maps/rmuc2027/navigation-console'
        self.local=self.package/'localization/vertical85';self.local.mkdir(parents=True)
        self.scene={'schema_version':1,'map_id':'fixture','display_name':'fixture','units':'m','coordinate_system':'ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP','spawns':[{'id':'default','position_m':[2.5,-1.25,.42],'yaw_rad':1.2}]}
        (self.package/'scene.json').write_text(json.dumps(self.scene),encoding='utf-8')
        descriptor=self.package.parent/'mesh-source.json'
        descriptor.write_text(json.dumps({'source_to_sim_translation_m':[1.,2.,3.]}),encoding='utf-8')
        spec='image: map.pgm\nresolution: 0.05\norigin: [-1, 2, 0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n'
        self.raster=b'P5\n3 2\n255\n'+bytes([0,205,254,254,254,0])
        self.provenance=b'known fixture provenance bytes'
        for folder,pixels in [(self.package,b'P5\n3 2\n255\n'+bytes([0,254,205,254,0,254])),
                              (self.local,self.raster)]:
            (folder/'map.yaml').write_text(spec,encoding='utf-8')
            (folder/'map.pgm').write_bytes(pixels)
        (self.local/'surface-provenance.npz').write_bytes(self.provenance)
        scene_hash=console_config.scene_geometry(self.scene)
        metadata={'role':'offline_geometry_candidate','status':'fixture','map_id':'fixture',
            'scene_geometry_sha256':scene_hash,'descriptor_sha256':digest(descriptor),
            'source_collision_sha256':'3'*64,'source_to_sim_translation_m':[1.,2.,3.],
            'coordinate_system':'ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP','units':'m',
            'width':3,'height':2,'resolution':.05,'origin':[-1.,2.,0.],
            'map_yaml_sha256':digest(self.local/'map.yaml'),'raster_sha256':digest(self.local/'map.pgm'),
            'surface_provenance_sha256':digest(self.local/'surface-provenance.npz'),
            'reference_navigation_raster_sha256':digest(self.package/'map.pgm')}
        (self.local/'metadata.json').write_text(json.dumps(metadata),encoding='utf-8')
        self.specification={'scene_geometry_sha256':scene_hash,'raster_sha256':digest(self.package/'map.pgm'),
            'map_metadata':{'map_id':'fixture','collision_sha256':'3'*64,'source_md5':'a'*32},
            'localization_map':{'schema':'rmsim_packaged_localization_map_v1','default':True,'status':'fixture',
                'files':{role:{'path':(self.local/name).relative_to(self.package).as_posix(),
                               'sha256':digest(self.local/name)}
                    for role,name in [('yaml','map.yaml'),('raster','map.pgm'),('metadata','metadata.json'),
                                      ('surface_provenance','surface-provenance.npz')]},
                'export_recipe':{'schema':'rmsim_localization_surface_export_recipe_v1'}}}
        self.save()

    def save(self):
        (self.package/'SOURCE.json').write_text(json.dumps(self.specification),encoding='utf-8')

    def test_actual_scene_preparation_chooses_and_validates_default(self):
        with mock.patch.object(console_config,'ROOT',self.root):
            scene=console_config.prepare_scene('rmuc2027',self.root/'prepared')
        self.assertEqual(scene['initial_pose'],[2.5,-1.25,1.2])
        self.assertEqual(scene['initial_pose_prior']['mode'],'scene_spawn')
        self.assertEqual(scene['localization_map_selection'],'package_default')
        self.assertEqual(scene['localization_map_package']['manifest_sha256'],digest(self.package/'SOURCE.json'))
        self.assertEqual(scene['localization_map_yaml'],str((self.local/'map.yaml').resolve()))
        identity=validate_localization_map(scene['map_yaml'],scene['localization_map_yaml'])
        self.assertEqual(identity['localization']['raster_sha256'],digest(self.local/'map.pgm'))

    def test_explicit_override_does_not_touch_missing_or_corrupt_default(self):
        explicit=self.root/'new-vehicle-map.yaml';explicit.write_text('selected externally')
        (self.local/'map.pgm').write_bytes(b'corrupted')
        callback=mock.Mock(side_effect=AssertionError('Default rebuild called'))
        selected,role=console_config.select_localization_map(self.package,self.specification,explicit,callback)
        self.assertEqual((selected,role),(str(explicit.resolve()),'explicit'))
        callback.assert_not_called()

    def test_package_without_default_retains_one_map(self):
        self.assertEqual(console_config.select_localization_map(None,{}),('','single_map'))
        self.assertIsNone(packaged_localization_map(self.package,{}))
        self.specification['localization_map']['default']=False
        self.assertIsNone(packaged_localization_map(self.package,self.specification))

    def test_relative_package_can_move_without_identity_change(self):
        before=packaged_localization_map(self.package,self.specification)
        relocated=self.root/'portable-copy';shutil.copytree(self.package,relocated)
        after=packaged_localization_map(relocated,self.specification)
        self.assertNotEqual(before['yaml_path'],after['yaml_path'])
        self.assertEqual(before['files']['raster']['sha256'],after['files']['raster']['sha256'])
        self.assertEqual(after['yaml_path'],str(relocated/'localization/vertical85/map.yaml'))

    def test_absolute_paths_and_traversal_are_rejected(self):
        for path in ['../map.yaml','/tmp/map.yaml','D:/machine/map.yaml','localization\\map.yaml','localization//map.yaml']:
            with self.subTest(path=path):
                declaration=copy.deepcopy(self.specification)
                declaration['localization_map']['files']['yaml']['path']=path
                with self.assertRaisesRegex(ValueError,'relative POSIX'):
                    packaged_localization_map(self.package,declaration)

    def test_corrupt_existing_bytes_are_not_automatically_rebuilt(self):
        (self.local/'map.pgm').write_bytes(b'wrong raster')
        callback=mock.Mock()
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            console_config.select_localization_map(self.package,self.specification,restore=callback)
        callback.assert_not_called()

    def test_only_missing_derived_files_trigger_restore_and_result_is_checked(self):
        (self.local/'map.pgm').unlink();(self.local/'surface-provenance.npz').unlink()
        calls=[]
        def restore(manifest):
            calls.append(manifest)
            (self.local/'map.pgm').write_bytes(self.raster)
            (self.local/'surface-provenance.npz').write_bytes(self.provenance)
        selected,role=console_config.select_localization_map(self.package,self.specification,restore=restore)
        self.assertEqual(calls,[self.package/'SOURCE.json']);self.assertEqual(role,'package_default')
        self.assertEqual(selected,str(self.local/'map.yaml'))
        self.assertEqual(packaged_localization_map(self.package,self.specification)['missing_derived'],[])

    def test_bad_restore_output_is_rejected(self):
        (self.local/'map.pgm').unlink()
        def restore(unused):
            (self.local/'map.pgm').write_bytes(b'bad export')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            console_config.select_localization_map(self.package,self.specification,restore=restore)

    def test_missing_tracked_metadata_is_not_regenerated(self):
        (self.local/'metadata.json').unlink()
        callback=mock.Mock()
        with self.assertRaisesRegex(ValueError,'file is missing'):
            console_config.select_localization_map(self.package,self.specification,restore=callback)
        callback.assert_not_called()

    def test_self_consistent_wrong_scene_is_rejected(self):
        metadata=json.loads((self.local/'metadata.json').read_text())
        metadata['scene_geometry_sha256']='f'*64
        (self.local/'metadata.json').write_text(json.dumps(metadata))
        self.specification['localization_map']['files']['metadata']['sha256']=digest(self.local/'metadata.json')
        with self.assertRaisesRegex(ValueError,'different navigation geometry'):
            packaged_localization_map(self.package,self.specification)


if __name__=='__main__':
    unittest.main()
