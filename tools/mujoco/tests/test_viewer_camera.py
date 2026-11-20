"""Exercise camera callbacks against the installed MuJoCo C API, without GLFW init."""
from pathlib import Path
import hashlib
import sys
import unittest

import glfw
import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_viewer import CameraInput, configure_display_lighting


class InputEvents:
    """Only OS input is replaced; every MuJoCo operation is the installed API."""
    def __init__(self):
        self.buttons = set()
        self.keys = set()
        self.callbacks = {}
        self.closed = False

    def __getattr__(self, name):
        return getattr(glfw, name)

    def get_cursor_pos(self, window): return (0.0, 0.0)
    def get_window_size(self, window): return (1280, 800)
    def get_mouse_button(self, window, button): return glfw.PRESS if button in self.buttons else glfw.RELEASE
    def get_key(self, window, key): return glfw.PRESS if key in self.keys else glfw.RELEASE
    def set_cursor_pos_callback(self, window, callback): self.callbacks['move'] = callback
    def set_scroll_callback(self, window, callback): self.callbacks['scroll'] = callback
    def set_key_callback(self, window, callback): self.callbacks['key'] = callback
    def set_window_should_close(self, window, value): self.closed = value


def physics_digest(model, data):
    hasher = hashlib.sha256(str(model.opt).encode())
    for value in (model.body_mass, model.body_inertia, model.geom_friction,
                  model.geom_size, model.geom_pos, model.geom_quat,
                  model.dof_damping, model.actuator_gainprm, model.actuator_biasprm,
                  model.actuator_gear, data.qpos, data.qvel, data.act, data.ctrl):
        hasher.update(value.tobytes())
    return hasher.hexdigest()


class ViewerCameraApiTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(mujoco.mj_version(), 4000001)
        self.model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody>
          <geom type="plane" size="5 5 .1"/>
          <body name="body" pos="1.2 2.1 .4"><freejoint/>
            <geom type="box" size=".2 .15 .1" mass="2"/>
          </body></worldbody></mujoco>''')
        self.data = mujoco.MjData(self.model)
        mujoco.mj_kinematics(self.model, self.data)
        mujoco.mj_comPos(self.model, self.data)
        mujoco.mj_camlight(self.model, self.data)
        self.scene = mujoco.MjvScene(self.model, maxgeom=20)
        self.camera = mujoco.MjvCamera()
        self.events = InputEvents(); self.window = object()
        self.input = CameraInput(self.events, mujoco, self.window, self.model, self.camera)
        mujoco.mjv_updateCamera(self.model, self.data, self.camera, self.scene)
        self.before = physics_digest(self.model, self.data)

    def tearDown(self):
        self.assertEqual(physics_digest(self.model, self.data), self.before)

    def test_rotate_both_axes_and_refresh_real_camera(self):
        self.events.buttons = {glfw.MOUSE_BUTTON_LEFT}
        before = (self.camera.azimuth, self.camera.elevation)
        self.events.callbacks['move'](self.window, 48.0, 32.0)
        self.assertNotEqual((self.camera.azimuth, self.camera.elevation), before)
        self.events.keys = {glfw.KEY_LEFT_SHIFT}
        self.events.callbacks['move'](self.window, 72.0, 48.0)
        mujoco.mjv_updateCamera(self.model, self.data, self.camera, self.scene)
        self.assertTrue(all(np.isfinite(camera.pos).all() for camera in self.scene.camera))

    def test_pan_and_both_zoom_inputs_use_real_five_argument_api(self):
        before = self.camera.lookat.copy()
        self.events.buttons = {glfw.MOUSE_BUTTON_RIGHT}
        self.events.callbacks['move'](self.window, 50.0, 20.0)
        self.assertFalse(self.input.follow)
        self.assertEqual(self.camera.type, mujoco.mjtCamera.mjCAMERA_FREE)
        self.assertFalse(np.array_equal(self.camera.lookat, before))
        self.events.keys = {glfw.KEY_RIGHT_SHIFT}
        self.events.callbacks['move'](self.window, 80.0, 40.0)
        self.events.keys.clear(); self.events.buttons = {glfw.MOUSE_BUTTON_MIDDLE}
        before_distance = self.camera.distance
        self.events.callbacks['move'](self.window, 80.0, 90.0)
        self.assertNotEqual(self.camera.distance, before_distance)
        before_distance = self.camera.distance
        self.events.callbacks['scroll'](self.window, 0.0, 2.0)
        self.assertNotEqual(self.camera.distance, before_distance)
        self.assertGreater(self.camera.distance, 0)

    def test_home_follow_and_close_do_not_change_solver_state(self):
        key = self.events.callbacks['key']
        key(self.window, glfw.KEY_F, 0, glfw.PRESS, 0)
        self.assertFalse(self.input.follow)
        key(self.window, glfw.KEY_F, 0, glfw.PRESS, 0)
        self.assertTrue(self.input.follow)
        key(self.window, glfw.KEY_HOME, 0, glfw.PRESS, 0)
        self.assertFalse(self.input.follow)
        np.testing.assert_array_equal(self.camera.lookat, self.model.stat.center)
        self.assertGreaterEqual(self.camera.distance, 3.0)
        key(self.window, glfw.KEY_F, 0, glfw.PRESS, 0)
        mujoco.mjv_updateCamera(self.model, self.data, self.camera, self.scene)
        self.assertEqual(self.camera.trackbodyid, 1)
        key(self.window, glfw.KEY_ESCAPE, 0, glfw.PRESS, 0)
        self.assertTrue(self.events.closed)

    def test_headlight_override_changes_only_render_fields(self):
        result = configure_display_lighting(self.model)
        self.assertTrue(result['display']['active'])
        self.assertEqual(self.model.vis.headlight.active, 1)
        np.testing.assert_allclose(self.model.vis.headlight.ambient, [.45] * 3)
        np.testing.assert_allclose(self.model.vis.headlight.diffuse, [.75] * 3)
        np.testing.assert_allclose(self.model.vis.headlight.specular, [.1] * 3)
        mujoco.mjv_makeLights(self.model, self.data, self.scene)
        self.assertGreaterEqual(self.scene.nlight, 1)
        np.testing.assert_allclose(self.scene.lights[0].ambient, [.45] * 3)


if __name__ == '__main__':
    unittest.main()
