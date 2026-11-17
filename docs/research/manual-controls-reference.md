# Ground-vehicle control reference

Date: 2026-11-15. User requested manual movement and camera behavior inspired by War Thunder. This is a behavior reference, not a claim to reproduce every game control or its exact physics.

## Chosen sample behavior

- W/S: throttle/reverse, A/D: hull rotation, Space: brake. No lateral translation in the manual profile.
- Mouse controls observation and desired aim; the turret approaches that aim with finite demo rates. Hull movement does not forcibly rotate world aim.
- Hold C to look around while preserving the selected world aim target. Release restores the saved aiming view. War Thunder has different mouse-view gun-lock options; this is the project's explicit variant.
- Shift toggles chase/scope view; right mouse narrows scope FOV. Switching views does not reset the turret.
- Wheel adjusts chase distance or scope zoom. This is a project simplification; no ballistic ranging/zeroing is implied.

## Primary references

- [Gaijin aiming guide](https://support.gaijin.net/hc/en-us/articles/209182965-Tank-battles-how-to-control-your-aim): view versus gun direction, sniper and zoom.
- [Official transmission guide](https://wiki.warthunder.com/controls/8411-transmission-modes-and-cruise-control): driving modes.
- [Official optics guide](https://wiki.warthunder.com/mechanics/235-optics): zoom versus sight distance.
- [Official old Wiki hints](https://old-wiki.warthunder.com/Hints): mouse-look reference.
- [Official 1.95 change log](https://warthunder.com/en/game/changelog/current/1053): mouse-view gun-direction option.

Acceleration, speed, turret rate, FOV and collision proxy values are demonstrator settings, not measured robot dynamics. ROS autonomous control remains a separate future integration.
