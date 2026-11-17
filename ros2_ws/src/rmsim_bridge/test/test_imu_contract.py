import copy
import unittest
from rmsim_bridge.protocol import ProtocolError,validate_imu


def packet():
    profile=dict(id='imu',frame_id='robot/imu_link',parent_frame_id='robot/base_link',rate_hz=200,
      pose_in_base=dict(position_m=[0,0,.15],rotation_xyzw=[0,0,0,1]),accel_bias_mps2=[0]*3,gyro_bias_rad_s=[0]*3,
      accel_noise_std_mps2=[0]*3,gyro_noise_std_rad_s=[0]*3,noise_seed=1)
    sample=dict(id='imu',seq=1,stamp_ns=5_000_000,integration_dt_s=.005,angular_velocity_rad_s=[0,0,1],specific_force_mps2=[0,0,9.8])
    return dict(calibrated=False,model='raw6axis',profile_md5='test',gravity_z_mps2=-9.8,profiles=[profile],samples=[sample],
      stats=[dict(id='imu',measured_samples=1,missed_periods=0,dropped_samples=0,history_resets=0,invalid_samples=0)])


class ImuContractTests(unittest.TestCase):
    def test_valid_si_sample_and_unavailable_attitude_contract(self):
        validate_imu(packet(),'robot',10_000_000)
    def test_invalid_batches_rejected(self):
        updates=[lambda x:x['samples'][0].update(stamp_ns=11_000_000),
                 lambda x:x['samples'].append(copy.deepcopy(x['samples'][0])),
                 lambda x:x['profiles'][0].update(frame_id='world'),
                 lambda x:x['profiles'][0].update(accel_noise_std_mps2=[-1,0,0]),
                 lambda x:x['samples'][0].update(specific_force_mps2=[0,float('nan'),0]),
                 lambda x:x['samples'][0].update(integration_dt_s=0)]
        for update in updates:
            value=packet();update(value)
            with self.assertRaises(ProtocolError):validate_imu(value,'robot',10_000_000)


if __name__=='__main__':unittest.main()
