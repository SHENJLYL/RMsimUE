"""Map-only AMCL integration, source ownership, and actual-published-map guards."""
import array
import ast
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest

import yaml

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.navigation_config import prepare
from rmsim_bridge.navigation_map_identity import (
    validate_localization_map,map_file_signatures,verify_occupancy_message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LocalizationMapTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.folder=Path(self.temporary.name)
        self.nav=self.folder/'navigation';self.loc=self.folder/'localization'
        self.nav.mkdir();self.loc.mkdir()
        spec="image: map.pgm\nresolution: 0.05\norigin: [-1.0, 2.0, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n"
        for directory,pixels in [(self.nav,bytes([0,254,205,254,0,254])),
                                 (self.loc,bytes([0,205,254,254,254,0]))]:
            (directory/'map.yaml').write_text(spec,encoding='utf-8')
            (directory/'map.pgm').write_bytes(b'P5\n3 2\n255\n'+pixels)
        self.nav_meta=dict(map_id='fixture',source_md5='a'*32,scene_geometry_sha256='1'*64,
                           descriptor_sha256='2'*64,collision_sha256='3'*64,
                           source_to_sim_translation_m=[1.,2.,3.])
        self.loc_meta=dict(role='offline_geometry_candidate',status='generated_not_native_validated',
                          map_id='fixture',scene_geometry_sha256='1'*64,descriptor_sha256='2'*64,
                          source_collision_sha256='3'*64,source_to_sim_translation_m=[1.,2.,3.],
                          coordinate_system='ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP',units='m',
                          width=3,height=2,resolution=.05,origin=[-1.,2.,0.],
                          map_yaml_sha256=digest(self.loc/'map.yaml'),raster_sha256=digest(self.loc/'map.pgm'),
                          reference_navigation_raster_sha256=digest(self.nav/'map.pgm'))
        self.save()
        self.reference=ROOT/'ros2_ws/src/pb2025_nav_bringup/config/reality/nav2_params.yaml'

    def save(self):
        (self.nav/'metadata.json').write_text(json.dumps(self.nav_meta),encoding='utf-8')
        (self.loc/'metadata.json').write_text(json.dumps(self.loc_meta),encoding='utf-8')

    def validate(self):
        return validate_localization_map(self.nav/'map.yaml',self.loc/'map.yaml')

    def message(self):
        return types.SimpleNamespace(
            header=types.SimpleNamespace(frame_id='robot/map'),
            info=types.SimpleNamespace(width=3,height=2,resolution=struct.unpack('<f',struct.pack('<f',.05))[0],
                origin=types.SimpleNamespace(position=types.SimpleNamespace(x=-1.,y=2.,z=0.),
                    orientation=types.SimpleNamespace(x=0.,y=0.,z=0.,w=1.))),
            data=array.array('b',[0,0,100,100,-1,0]))

    def runner_method(self,name):
        source=ast.parse((ROOT/'ros2_ws/src/rmsim_bridge/rmsim_bridge/navigation_runner.py').read_text(encoding='utf-8'))
        cls=next(n for n in source.body if isinstance(n,ast.ClassDef) and n.name=='NavigationRunner')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
        namespace=dict(validate_localization_map=validate_localization_map,
                       map_file_signatures=map_file_signatures,verify_occupancy_message=verify_occupancy_message)
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(ROOT/'navigation_runner.py'),'exec'),namespace)
        return namespace[name]

    def test_native_trinary_row_order_and_identity(self):
        identity=self.validate()
        self.assertEqual(identity['localization']['occupancy_sha256'],
                         hashlib.sha256(bytes([0,0,100,100,255,0])).hexdigest())
        self.assertEqual(identity['map_id'],'fixture')
        self.assertEqual(identity['localization']['resolution'],.05000000074505806)
        self.assertEqual(identity['localization']['origin'],[-1.,2.,0.])
        self.assertEqual(identity['navigation']['raster_sha256'],digest(self.nav/'map.pgm'))

    def test_dual_map_only_adds_native_map_server_and_lifecycle(self):
        args=(self.reference,self.nav/'map.yaml','robot',[.6,-.4,.15],'simulation')
        baseline,old_meta=prepare(*args)
        dual,metadata=prepare(*args,localization_map_yaml=self.loc/'map.yaml')
        for name in baseline:
            if name!='lifecycle_manager_localization':
                self.assertEqual(dual[name],baseline[name],name)
        self.assertEqual(dual['lifecycle_manager_localization']['ros__parameters']['node_names'],
                         ['map_server','localization_map_server','amcl'])
        self.assertEqual(dual['localization_map_server']['ros__parameters']['topic_name'],'localization_map')
        self.assertEqual(dual['localization_map_server']['ros__parameters']['frame_id'],'robot/map')
        self.assertEqual(metadata['localization_map_binding'],{'source_topic':'map','target_topic':'localization_map'})
        self.assertNotIn('localization_map',old_meta)

    def test_original_external_amcl_topic_and_numbers_remain_source_owned(self):
        data=yaml.safe_load((ROOT/'ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml').read_text(encoding='utf-8'))
        data['amcl']['ros__parameters'].update(map_topic='/vehicle/map_input',sigma_hit=.071,max_beams=73)
        source=self.folder/'vehicle.yaml';source.write_text(yaml.safe_dump(data),encoding='utf-8')
        config,metadata=prepare(self.reference,self.nav/'map.yaml','robot',[0.,0.,0.],
                                'simulation',str(source),self.loc/'map.yaml')
        amcl=config['amcl']['ros__parameters']
        self.assertEqual(amcl['map_topic'],'/vehicle/map_input')
        self.assertEqual(amcl['sigma_hit'],.071);self.assertEqual(amcl['max_beams'],73)
        self.assertEqual(metadata['localization_map_binding']['source_topic'],'/vehicle/map_input')
        self.assertEqual(config['controller_server']['ros__parameters']['FollowPath']['v_linear_max'],
                         data['controller_server']['ros__parameters']['FollowPath']['v_linear_max'])
        self.assertEqual(config['velocity_smoother']['ros__parameters']['max_accel'][:2],[.8,.8])

    def test_absent_optional_map_keeps_original_nodes(self):
        config,metadata=prepare(self.reference,self.nav/'map.yaml','robot',[0.,0.,0.])
        self.assertNotIn('localization_map_server',config)
        self.assertNotIn('localization_map',metadata)
        self.assertEqual(config['lifecycle_manager_localization']['ros__parameters']['node_names'],['map_server','amcl'])

    def test_wrong_scene_source_or_map_hash_is_rejected(self):
        original=copy.deepcopy(self.loc_meta)
        cases=[('map_id','other'),('scene_geometry_sha256','4'*64),
               ('descriptor_sha256','4'*64),('source_collision_sha256','4'*64),
               ('source_to_sim_translation_m',[1.,2.,3.1]),
               ('reference_navigation_raster_sha256','4'*64),('raster_sha256','4'*64),
               ('map_yaml_sha256','4'*64),('units','cm'),
               ('coordinate_system','UNREAL_LEFT_HANDED'),('role','traversability')]
        for key,value in cases:
            with self.subTest(field=key):
                self.loc_meta=copy.deepcopy(original);self.loc_meta[key]=value;self.save()
                with self.assertRaises(ValueError):self.validate()
        self.loc_meta=original;self.save()

    def test_spatial_shift_is_rejected_even_with_updated_hash(self):
        path=self.loc/'map.yaml'
        spec=yaml.safe_load(path.read_text(encoding='utf-8'));spec['origin'][0]+=.025
        path.write_text(yaml.safe_dump(spec),encoding='utf-8')
        self.loc_meta['map_yaml_sha256']=digest(path);self.loc_meta['origin']=spec['origin'];self.save()
        with self.assertRaisesRegex(ValueError,'spatial'):self.validate()

    def test_live_map_payload_frame_and_origin_are_checked(self):
        expected=self.validate()['localization']
        verify_occupancy_message(self.message(),expected,'robot/map')
        for field in ('data','frame','origin','resolution'):
            with self.subTest(field=field):
                message=self.message()
                if field=='data':message.data[0]=100
                elif field=='frame':message.header.frame_id='other/map'
                elif field=='origin':message.info.origin.position.x+=.025
                else:message.info.resolution=.1
                with self.assertRaises(ValueError):verify_occupancy_message(message,expected,'robot/map')

    def test_actual_runner_latches_wrong_published_map(self):
        identity=self.validate()
        receiver=types.SimpleNamespace(robot='robot',map_identity=identity,map_seen={'localization':False},map_fault='')
        callback=self.runner_method('observe_map')
        callback(receiver,'localization',self.message())
        self.assertTrue(receiver.map_seen['localization']);self.assertEqual(receiver.map_fault,'')
        message=self.message();message.data[0]=100
        callback(receiver,'localization',message)
        self.assertIn('Published occupancy',receiver.map_fault)

    def test_actual_runner_rejects_changed_valid_map_on_reload(self):
        identity=self.validate()
        runner=types.SimpleNamespace(map_identity=identity,map_yaml=str(self.nav/'map.yaml'),
                                     localization_map_yaml=str(self.loc/'map.yaml'))
        reload=self.runner_method('verify_map_inputs');reload(runner)
        old_signatures=runner.map_signatures
        path=self.loc/'map.pgm';raw=bytearray(path.read_bytes());raw[-1]=0 if raw[-1] else 254;path.write_bytes(raw)
        self.loc_meta['raster_sha256']=digest(path);self.save()
        self.assertNotEqual(map_file_signatures(identity),old_signatures)
        # A self-consistent replacement is still different from the prepared session.
        self.validate()
        with self.assertRaisesRegex(ValueError,'changed after navigation preparation'):reload(runner)

    def test_truncated_pgm_is_rejected(self):
        path=self.loc/'map.pgm';path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError,'payload'):self.validate()


if __name__=='__main__':
    unittest.main()
