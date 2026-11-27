"""Wire invariants for physical MID-360 slots, independently of scene geometry."""
import copy
import math
import struct
import unittest
from test_lidar_contract import frame
from rmsim_bridge.protocol import validate_state, ProtocolError


def physical_frame(hz=100, windows=1, group_stride=1):
    state, _ = frame()
    lidar = state['lidar']
    period = 1_000_000_000 // hz
    count = 200000 // hz
    lidar.update(profile_schema='rm_lidar_runtime_v3', frame_hz=hz,
                 max_range_m=70, elevation_min_rad=math.radians(-7), elevation_max_rad=math.radians(52), emitted_slots=count*windows,
                 delivery_suppressed_slots=0, disconnected_slots=0,
                 skipped_scan_windows=0,
                 physical_model=dict(schema='rm_mid360_physical_v1',
                     emission_interval_ns=5000, laser_channels=4,
                     retains_no_returns=True, pending_scan_capacity=8,
                     scan_pattern='spherical_fibonacci_approx',
                     pose_time_model='native_substep_translation_lerp_quaternion_slerp',
                     scene_time_model='current_ue_world_snapshot',
                     measurement_calibrated=False,angular_noise_std_rad=.001,range_noise_std_m=.02,near_range_noise_std_m=.03,
                     noise_near_distance_m=.2,noise_reference_distance_m=10.,nir_default_reflectance=.1,
                     range_reference_low_reflectance=.1,range_reference_low_m=40.,range_reference_high_reflectance=.8,
                     range_reference_high_m=70.,incidence_cosine=True,nir_material_reflectance={}),
                 rejection_counts={key:0 for key in ('no_hit','self_occluded',
                     'below_min_range','above_max_range','insufficient_signal',
                     'missing_pose_history','optical_pass_limit','noise_outside_range')},
                 scans=[])
    selected = [slot for slot in range(count) if (slot // 4) % group_stride == 0]
    if group_stride > 1:
        lidar['physical_model']['query_group_stride'] = group_stride
        lidar['sampled_rays_per_second'] = len(selected) * hz
        lidar['sampling_omitted_slots'] = (count - len(selected)) * windows
    payload = bytearray(range(12))
    for window in range(windows):
        start = state['sim_time_ns'] - (windows-window)*period
        offset = len(payload)
        for slot in selected:
            available = slot % 3 != 0
            payload.extend(struct.pack('<IfffBBBB', slot*5000,
                1. if available else 0., 0., .1 if available else 0.,
                75 if available else 0, 0, (start//5000+slot)%4, 0))
        lidar['scans'].append(dict(seq=window+1, start_ns=start,
            end_ns=start+period, point_count=len(selected), byte_offset=offset,
            byte_length=len(selected)*20))
    return state, bytes(payload)


class Mid360PhysicalWireTests(unittest.TestCase):
    def test_all_publication_windows_keep_nominal_emissions(self):
        for hz in (5, 10, 20, 50, 100):
            with self.subTest(hz=hz):
                state, payload = physical_frame(hz)
                validate_state(state, payload, 'aim_sentry')
                self.assertEqual(state['lidar']['scans'][0]['point_count'], 200000//hz)

    def test_dual_sentry_sampling_preserves_nominal_time_and_channels(self):
        for hz in (5, 10, 20, 50, 100):
            with self.subTest(hz=hz):
                state, payload = physical_frame(hz, group_stride=4)
                validate_state(state, payload, 'aim_sentry')
                self.assertEqual(state['lidar']['sampled_rays_per_second'], 50000)
                self.assertEqual(state['lidar']['scans'][0]['point_count'], 50000 // hz)
        state, payload = physical_frame(10, group_stride=4)
        bad = bytearray(payload)
        # The second four-channel burst occurs after 80 µs on the original grid.
        struct.pack_into('<I', bad, 12 + 4 * 20, 20000)
        with self.assertRaises(ProtocolError):
            validate_state(state, bad, 'aim_sentry')
        state['lidar']['sampled_rays_per_second'] = 200000
        with self.assertRaises(ProtocolError):
            validate_state(state, payload, 'aim_sentry')

    def test_eight_scan_backlog_is_bounded(self):
        state, payload = physical_frame(windows=8)
        validate_state(state, payload, 'aim_sentry')
        state['lidar']['physical_model']['pending_scan_capacity'] = 2
        with self.assertRaises(ProtocolError):
            validate_state(state, payload, 'aim_sentry')

    def test_timing_and_channel_cannot_be_rebucketed(self):
        state, payload = physical_frame()
        for index, values in ((1, (0,1.,0.,.1,75,0,1,0)),
                              (1, (5000,1.,0.,.1,75,0,2,0))):
            bad = bytearray(payload)
            struct.pack_into('<IfffBBBB', bad, 12+index*20, *values)
            with self.assertRaises(ProtocolError):
                validate_state(state, bad, 'aim_sentry')

    def test_zero_slots_cannot_be_removed_or_claim_reflectivity(self):
        state, payload = physical_frame()
        bad = copy.deepcopy(state)
        bad['lidar']['scans'][0]['point_count'] -= 1
        bad['lidar']['scans'][0]['byte_length'] -= 20
        with self.assertRaises(ProtocolError):
            validate_state(bad, payload[:-20], 'aim_sentry')
        invalid_zero = bytearray(payload)
        invalid_zero[12+16] = 60
        with self.assertRaises(ProtocolError):
            validate_state(state, invalid_zero, 'aim_sentry')

    def test_real_return_respects_blind_zone(self):
        state, payload = physical_frame()
        bad = bytearray(payload)
        struct.pack_into('<IfffBBBB', bad, 32, 5000,.05,0.,0.,75,0,1,0)
        with self.assertRaises(ProtocolError):
            validate_state(state, bad, 'aim_sentry')

    def test_window_cannot_drift_from_emission_epoch(self):
        state, payload = physical_frame()
        state['lidar']['scans'][0]['start_ns'] += 1
        with self.assertRaises(ProtocolError):
            validate_state(state, payload, 'aim_sentry')

    def test_hardware_claims_and_model_identity_are_explicit(self):
        state, payload = physical_frame()
        changes = ({'measurement_calibrated':True},
                   {'emission_interval_ns':1000},
                   {'laser_channels':16},
                   {'retains_no_returns':False},
                   {'pending_scan_capacity':9},
                   {'pose_time_model':'current_frame_for_all_points'},
                   {'scan_pattern':'claimed_factory_exact'})
        for change in changes:
            with self.subTest(change=change):
                bad = copy.deepcopy(state)
                bad['lidar']['physical_model'].update(change)
                with self.assertRaises(ProtocolError):
                    validate_state(bad, payload, 'aim_sentry')

    def test_emission_rate_and_missing_history_are_auditable(self):
        state, payload = physical_frame()
        for change in ({'rays_per_second':100000}, {'frame_hz':7},
                       {'emitted_slots':-1}):
            bad = copy.deepcopy(state)
            bad['lidar'].update(change)
            with self.assertRaises(ProtocolError):
                validate_state(bad, payload, 'aim_sentry')
        del state['lidar']['rejection_counts']['missing_pose_history']
        with self.assertRaises(ProtocolError):
            validate_state(state, payload, 'aim_sentry')




class NativeImuHistoryWireTests(unittest.TestCase):
    @staticmethod
    def sample():
        from test_imu_contract import packet
        data=packet()
        data.update(profile_schema='rm_imu_runtime_v3',
                    physical_model=dict(schema='rm_mid360_imu_physical_v1',
                        measurement_calibrated=False,support_latency_s=.001,
                        integration_window_s=.002))
        data['profiles'][0]['measurement']=dict(accel_range_mps2=156.9064,
            gyro_range_rad_s=34.90658503988659,
            accel_quantization_mps2=0.,gyro_quantization_rad_s=0.)
        data['samples'][0]['integration_dt_s']=.002
        data['stats'][0].update(accel_saturated_axes=0,gyro_saturated_axes=0,
            history_misses=0,warmup_samples=1,delivery_suppressed_samples=0)
        return data

    def test_completed_support_uses_200hz_grid_and_si(self):
        from rmsim_bridge.protocol import validate_imu
        validate_imu(self.sample(),'robot',10_000_000)

    def test_window_averaged_imu_requires_its_full_support(self):
        from rmsim_bridge.protocol import validate_imu
        value = self.sample()
        value['physical_model'].update(schema='rm_mid360_imu_physical_v2',
                                       support_latency_s=.0025, integration_window_s=.005)
        sample = value['samples'][0]
        sample['integration_dt_s'] = .005
        validate_imu(value, 'robot', sample['stamp_ns'] + 2_500_000)
        with self.assertRaises(ProtocolError):
            validate_imu(value, 'robot', sample['stamp_ns'] + 2_499_999)

    def test_nonintegral_nanosecond_sample_period_keeps_integer_index(self):
        from rmsim_bridge.protocol import validate_imu
        value=self.sample();value['profiles'][0]['rate_hz']=600.
        for index in (2,3,5):
            value['samples'][0]['stamp_ns']=round(index*1_000_000_000/600)
            validate_imu(value,'robot',10_000_000)

    def test_missing_support_old_interval_and_off_grid_are_rejected(self):
        from rmsim_bridge.protocol import validate_imu
        for update in ({'stamp_ns':10_000_000},{'stamp_ns':5_100_000},
                       {'integration_dt_s':.005}):
            value=self.sample();value['samples'][0].update(update)
            with self.assertRaises(ProtocolError):
                validate_imu(value,'robot',10_000_000)

    def test_physical_range_and_history_counters_are_required(self):
        from rmsim_bridge.protocol import validate_imu
        for update in ({'angular_velocity_rad_s':[0.,0.,36.]},
                       {'specific_force_mps2':[200.,0.,9.8]}):
            value=self.sample();value['samples'][0].update(update)
            with self.assertRaises(ProtocolError):
                validate_imu(value,'robot',10_000_000)
        value=self.sample();del value['stats'][0]['history_misses']
        with self.assertRaises(ProtocolError):
            validate_imu(value,'robot',10_000_000)

if __name__ == '__main__':
    unittest.main()
