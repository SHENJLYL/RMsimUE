"""Pure acceptance checks for a fresh navigation session, independent of ROS imports."""
import math


def fresh_reconnect(old_identity, old_starts, navigation, bridge, error_m):
    """Reject stale/mismatched sessions, changed epochs, repeated starts and invalid errors."""
    identity = navigation.get('identity')
    if not isinstance(identity, list) or len(identity) != 2 or not isinstance(old_identity, list) or len(old_identity) != 2:
        return False
    return bool(
        navigation.get('phase') == 'running'
        and navigation.get('starts') == old_starts + 1
        and isinstance(identity[0], str) and identity[0]
        and identity[0] != old_identity[0]
        and type(identity[1]) is int and identity[1] == old_identity[1]
        and bridge.get('connected') and bridge.get('state_fresh')
        and [bridge.get('session'), bridge.get('epoch')] == identity
        and isinstance(error_m, (float, int)) and not isinstance(error_m, bool)
        and math.isfinite(error_m) and 0 <= error_m < .2
    )
