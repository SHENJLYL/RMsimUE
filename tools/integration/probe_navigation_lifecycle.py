"""Exercise the real lifecycle manager when a committed transition reply is lost."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time

os.environ['ROS_DOMAIN_ID'] = '178'
os.environ['ROS_LOCALHOST_ONLY'] = '1'
import rmsim_bridge  # Native Windows DLL scope, also harmless on Linux.
import rclpy
from rclpy.node import Node
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from lifecycle_msgs.msg import State, Transition
from lifecycle_msgs.srv import ChangeState, GetState
from std_srvs.srv import Trigger


class Peer(Node):
    def __init__(self, name, committed):
        super().__init__(name)
        self.state = State.PRIMARY_STATE_UNCONFIGURED
        self.committed = committed
        self.calls = []
        self.configured = threading.Event()
        self.release_reply = threading.Event()
        group = ReentrantCallbackGroup()
        self.create_service(ChangeState, name+'/change_state', self.change, callback_group=group)
        self.create_service(GetState, name+'/get_state', self.get_state, callback_group=group)

    def change(self, request, response):
        transition = request.transition.id
        self.calls.append(transition)
        if transition == Transition.TRANSITION_CONFIGURE:
            if self.committed:
                self.state = State.PRIMARY_STATE_INACTIVE
            self.configured.set()
            # State may be committed while the response never reaches the caller.
            # Keep GetState independently available, as in the observed DDS failure.
            self.release_reply.wait()
        elif transition == Transition.TRANSITION_ACTIVATE:
            self.state = State.PRIMARY_STATE_ACTIVE
        response.success = True
        return response

    def get_state(self, request, response):
        response.current_state.id = self.state
        return response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manager', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=['committed', 'unchanged'], default='committed')
    parser.add_argument('--expect-hang', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    peer_name = 'rmsim_lifecycle_peer_'+str(os.getpid())
    manager_name = 'rmsim_lifecycle_manager_'+str(os.getpid())
    params = args.output/'parameters.yaml'
    params.write_text(json.dumps({manager_name: {'ros__parameters': {
        'node_names': [peer_name], 'autostart': True, 'bond_timeout': 0.0,
        'service_timeout': 1.0, 'use_sim_time': False}}}), encoding='utf-8')
    rclpy.init()
    peer = Peer(peer_name, args.case == 'committed')
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(peer)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    report = {'status': 'running', 'case': args.case, 'expected_hang': args.expect_hang,
              'scope': 'native manager and concurrent lifecycle RPC; no simulator truth or robot control'}
    log = (args.output/'manager.log').open('w', encoding='utf-8')
    manager = None
    try:
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        manager = subprocess.Popen([str(args.manager), '--ros-args', '-r', '__node:='+manager_name,
                                    '--params-file', str(params)], stdout=log, stderr=subprocess.STDOUT, **options)
        report['manager_pid'] = manager.pid
        report['runtime_hashes'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in args.manager.parent.glob('*lifecycle*') if p.is_file()}
        assert peer.configured.wait(15), 'Manager did not send configure'
        client = peer.create_client(Trigger, manager_name+'/is_active')
        assert client.wait_for_service(timeout_sec=5), 'Manager status service unavailable'
        started = time.monotonic()
        future = client.call_async(Trigger.Request())
        while not future.done() and time.monotonic()-started < 8:
            assert manager.poll() is None, 'Manager exited before reconciliation'
            time.sleep(.02)
        report.update(elapsed_wall_s=time.monotonic()-started, transitions=peer.calls,
                      peer_state=peer.state, status_reply_received=future.done())
        if args.expect_hang:
            assert not future.done() and peer.calls == [Transition.TRANSITION_CONFIGURE]
            assert peer.state == State.PRIMARY_STATE_INACTIVE
            report['status'] = 'reproduced_expected_hang'
        else:
            assert future.done(), 'Manager still waits indefinitely for the lost reply'
            report['manager_active'] = future.result().success
            expected = [Transition.TRANSITION_CONFIGURE]
            if args.case == 'committed':
                expected.append(Transition.TRANSITION_ACTIVATE)
                assert report['manager_active'] and peer.state == State.PRIMARY_STATE_ACTIVE
            else:
                assert not report['manager_active'] and peer.state == State.PRIMARY_STATE_UNCONFIGURED
            assert peer.calls == expected, 'A non-idempotent transition was replayed'
            report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        if manager and manager.poll() is None:
            manager.terminate()
            manager.wait(timeout=10)
        peer.release_reply.set()
        executor.shutdown(timeout_sec=5)
        thread.join(timeout=5)
        peer.destroy_node()
        rclpy.shutdown()
        log.close()
        (args.output/'verification.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print('Native lifecycle probe', report['status'], report['case'], flush=True)


if __name__ == '__main__':
    main()
