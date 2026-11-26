"""Bind native C++ sentry behavior trees to an RMUC 2027 simulation session."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import xml.etree.ElementTree as ET


SOURCE_TREES = ('competition_phase1.xml', 'competition_retreat.xml',
                'competition_recover.xml', 'competition_combat.xml')


def format_route(poses, name):
    if not poses or any(len(pose) != 3 or not all(math.isfinite(value) for value in pose) for pose in poses):
        raise ValueError(f'Invalid native behavior route: {name}')
    return '|'.join(';'.join(map(str, pose)) for pose in poses)


def prepare_phase1(installed, runtime, poses, robot):
    goal_count = 0
    for source_name in SOURCE_TREES:
        xml = ET.parse(installed / 'behavior_trees' / source_name)
        for element in xml.iter():
            if element.get('ID') == 'phase1_wait_game':
                element.set('ID', 'competition_wait_game')
            if element.tag == 'IsTfReady':
                element.set('map_frame', f'{robot}/map')
                element.set('base_frame', f'{robot}/base_link')
            if element.tag == 'IsMapReady':
                element.set('settle_time', '0.1')
            if element.tag == 'SendNav2ThroughPoses':
                route = 'retreat' if source_name == 'competition_retreat.xml' else 'advance'
                element.set('goals', format_route([poses[route]], route))
                element.set('frame_id', f'{robot}/map')
                goal_count += 1
            if element.tag == 'PublishSpinSpeed' and element.get('spin_speed') not in (None, '0'):
                # The existing simulator match actuator exposes a 5 rad/s setpoint.
                element.set('spin_speed', '5.0')
        xml.write(runtime / source_name, encoding='utf-8', xml_declaration=True)
    if goal_count != 2:
        raise ValueError(f'Unexpected waypoint actions in installed competition trees: {goal_count}')


def prepare_match(installed, runtime, assets, poses, robot, team):
    field_zones = json.loads((assets / 'field-zones.json').read_text(encoding='utf-8'))
    zones = {zone['id']: zone['reference_m'] for zone in field_zones['zones']}
    enemy = 'blue' if team == 'red' else 'red'
    yaw = math.pi if team == 'red' else 0.0
    xml = ET.parse(installed / 'behavior_trees' / 'rmuc2027_match.xml')
    ready = xml.find('.//IsTfReady')
    ready.set('map_frame', f'{robot}/map')
    ready.set('base_frame', f'{robot}/base_link')
    xml.find('.//SendNav2ThroughPoses').set('frame_id', f'{robot}/map')
    tactics = xml.find('.//RMUC2027Tactics')
    tactics.set('side', team)
    own_outpost_card = [2.85, 4.70] if team == 'red' else [-2.60, -3.15]
    enemy_outpost_card = [-2.60, -3.15] if team == 'red' else [2.85, 4.70]
    for port, route in (
        ('patrol', poses['patrol']),
        ('own_outpost_heal', [[*own_outpost_card, yaw]]),
        ('own_outpost_attack', poses['own_outpost_attack']),
        ('own_outpost_rebuild', [[*own_outpost_card, yaw]]),
        ('own_base_supply', [[*zones[f'{team}_supply'][:2], yaw]]),
        ('own_fortress', [[*zones[f'{team}_fortress'][:2], yaw]]),
        ('enemy_outpost_attack', poses['enemy_outpost_attack']),
        ('enemy_outpost_capture', [[*enemy_outpost_card, yaw]]),
        ('enemy_fortress', [[*zones[f'{enemy}_fortress'][:2], yaw]]),
        ('enemy_base_attack', poses['enemy_base_attack']),
    ):
        tactics.set(port, format_route(route, f'{team}/{port}'))
    xml.write(runtime / 'rmuc2027_match.xml', encoding='utf-8', xml_declaration=True)


def prepare_behavior(root: Path, session: Path, map_file: Path, spawn_id: str, robot: str,
                     *, target_tree: str = 'rmuc2027_match'):
    scene = json.loads(map_file.read_text(encoding='utf-8-sig'))
    spawn = next((entry for entry in scene['spawns'] if entry['id'] == spawn_id), None)
    if scene['id'] != 'rmuc2027' or not spawn or spawn.get('team') not in ('red', 'blue'):
        raise ValueError('The C++ match tree requires an RMUC 2027 red or blue spawn')
    team = spawn['team']
    assets = root / 'assets/maps/rmuc2027'
    poses = json.loads((assets / 'behavior-waypoints.json').read_text(encoding='utf-8'))[team]

    overlay = root / 'ros2_ws/install_behavior'
    package = overlay / 'behavior'
    installed = package / 'share/behavior'
    suffix = '.exe' if os.name == 'nt' else ''
    server, client = (package / 'lib/behavior' / (name + suffix) for name in ('behavior_server', 'behavior_client'))
    for executable in (server, client):
        if not executable.is_file():
            raise FileNotFoundError(f'{executable} is missing. Run tools/environment/Build-Behavior.ps1')
    runtime = session / 'behavior-trees'
    runtime.mkdir(parents=True)
    plugins = ['behavior/bt_plugins']
    if not (installed / 'bt_plugins').is_dir():
        raise FileNotFoundError(f'Native behavior plugins are missing under {installed}')
    if target_tree == 'competition_phase1':
        prepare_phase1(installed, runtime, poses, robot)
    elif target_tree == 'rmuc2027_match':
        tactics = 'rmsim_sentry_tactics'
        sim_plugins = overlay / tactics / 'share' / tactics
        if not (sim_plugins / 'bt_plugins').is_dir():
            raise FileNotFoundError(f'{sim_plugins} is missing. Run tools/environment/Build-Behavior.ps1')
        plugins.append(f'{tactics}/bt_plugins')
        prepare_match(sim_plugins, runtime, assets, poses, robot, team)
    else:
        raise ValueError(f'Unknown native match tree: {target_tree}')

    # Nav2 already owns /behavior_server in the same ROS graph.
    server_node = 'rmsim_sentry_bt_server'
    client_node = 'rmsim_sentry_bt_client'
    params = session / 'behavior-params.yaml'
    params.write_text(
        f'{server_node}:\n'
        '  ros__parameters:\n'
        '    use_sim_time: true\n'
        '    action_name: pb2025_sentry_behavior\n'
        '    tick_frequency: 20\n'
        '    groot2_port: 0\n'
        '    ros_plugins_timeout: 1000\n'
        '    use_cout_logger: true\n'
        '    write_tree_model: false\n'
        f'    plugins: {json.dumps(plugins)}\n'
        f'    behavior_trees: [{json.dumps(runtime.resolve().as_posix())}]\n'
        f'{client_node}:\n'
        '  ros__parameters:\n'
        '    use_sim_time: true\n'
        f'    target_tree: {target_tree}\n', encoding='utf-8')

    def command(executable, node):
        return [str(executable), '--ros-args', '-r', f'__node:={node}', '--params-file', str(params)]
    server_command = command(server, server_node)
    for source, target in (
        ('gimbal_scan_cmd', f'/{robot}/gimbal_scan_cmd'),
        ('auto_aim_switch', f'/{robot}/behavior/auto_aim_switch'),
        ('tactical_fire_permit', f'/{robot}/behavior/fire_permit'),
        ('tactical_vision_track', f'/{robot}/behavior/vision_track'),
        ('tactical_navigation_status', f'/rmsim/{robot}/navigation'),
        ('cmd_spin', f'/{robot}/behavior/cmd_spin'),
        ('cmd_vel', f'/{robot}/behavior/cmd_vel'),
        ('tactical_target_priority', f'/{robot}/behavior/target_priority'),
        ('tactical_ammo_purchase', f'/{robot}/behavior/ammo_purchase'),
        ('tactical_state', f'/{robot}/behavior/tactical_state'),
    ):
        server_command.extend(('-r', f'{source}:={target}'))
    return server_command, command(client, client_node)
