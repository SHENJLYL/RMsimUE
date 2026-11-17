import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/integration'))
from navigation_recovery import fresh_reconnect

class RecoveryAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.old = ['old-session', 7]
        self.nav = {'phase': 'running', 'starts': 2, 'identity': ['new-session', 7]}
        self.bridge = {'connected': True, 'state_fresh': True, 'session': 'new-session', 'epoch': 7}
    def test_valid_fresh_reconnect(self):
        self.assertTrue(fresh_reconnect(self.old, 1, self.nav, self.bridge, .09))
    def test_stale_or_disconnected_bridge(self):
        for field in ('connected', 'state_fresh'):
            b = self.bridge.copy(); b[field] = False
            self.assertFalse(fresh_reconnect(self.old, 1, self.nav, b, .09))
    def test_changed_epoch_is_a_reset_not_reconnect(self):
        n = copy.deepcopy(self.nav); n['identity'][1] = 8
        b = self.bridge.copy(); b['epoch'] = 8
        self.assertFalse(fresh_reconnect(self.old, 1, n, b, .09))
    def test_old_or_mismatched_session(self):
        for session in ('old-session', '', 'different-session'):
            n = copy.deepcopy(self.nav); n['identity'][0] = session
            self.assertFalse(fresh_reconnect(self.old, 1, n, self.bridge, .09))
    def test_invalid_position_error(self):
        for error in (float('nan'), float('inf'), -.1, .2, True, None):
            self.assertFalse(fresh_reconnect(self.old, 1, self.nav, self.bridge, error))
    def test_incomplete_or_repeated_start(self):
        for change in ({'starts': 1}, {'starts': 3}, {'phase': 'localizing'}, {'identity': None}, {'identity': ['new-session', True]}):
            n = copy.deepcopy(self.nav); n.update(change)
            self.assertFalse(fresh_reconnect(self.old, 1, n, self.bridge, .09))

if __name__ == '__main__': unittest.main()
