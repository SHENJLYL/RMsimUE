"""Receive-only safety and state-identity contracts; no MuJoCo/GL imports."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from viewer_protocol import (MAX_PACKET_BYTES, STATE_SCHEMA, SnapshotError,
                             SnapshotValidator, ViewerContract, compare_body_poses,
                             normalize_session_token)


class ViewerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.contract = ViewerContract("a" * 32, "b" * 64, "c" * 64, 21, 20, 0, 14, 16)
        self.validator = SnapshotValidator(self.contract)
        self.packet = dict(schema=STATE_SCHEMA, session_token="a" * 32,
                           model_sha256="b" * 64, physics_identity_sha256="c" * 64,
                           nq=21, nv=20, na=0, nu=14, nbody=16,
                           generation=1, sequence=10, sim_time_s=3.0,
                           qpos=[0.0] * 21, qvel=[0.0] * 20, act=[], ctrl=[0.0] * 14,
                           body_xpos=[[0.0, 0.0, 0.0] for _ in range(16)],
                           body_xquat=[[1.0, 0.0, 0.0, 0.0] for _ in range(16)])

    def accept(self, packet=None):
        return self.validator.accept(json.dumps(self.packet if packet is None else packet).encode())

    def test_valid_complete_snapshot(self):
        snapshot = self.accept()
        self.assertEqual((snapshot.generation, snapshot.sequence, snapshot.sim_time_s), (1, 10, 3.0))
        self.assertEqual((len(snapshot.qpos), len(snapshot.body_xpos)), (21, 16))

    def test_hex_token_case_matches_native_and_status_identity(self):
        token = "aB12" * 8
        normalized = normalize_session_token(token)
        contract = ViewerContract(token, "b" * 64, "c" * 64, 21, 20, 0, 14, 16)
        self.assertEqual(normalized, contract.session_token)
        self.assertEqual(hashlib.sha256(normalized.encode("ascii")).hexdigest(),
                         hashlib.sha256(token.lower().encode("ascii")).hexdigest())
        self.validator = SnapshotValidator(contract)
        self.packet["session_token"] = token.lower()
        self.assertEqual(self.accept().sequence, 10)
        self.packet.update(session_token=token.upper(), sequence=11)
        self.assertEqual(self.accept().sequence, 11)

    def test_identity_mismatches_never_advance_stream(self):
        for key, bad in (("schema", "other"), ("session_token", "d" * 32), ("session_token", "\u516d" * 32),
                         ("model_sha256", "d" * 64), ("physics_identity_sha256", "d" * 64)):
            with self.subTest(key=key):
                packet = copy.deepcopy(self.packet); packet[key] = bad
                with self.assertRaises(SnapshotError): self.accept(packet)
                self.assertIsNone(self.validator.latest)

    def test_all_array_shapes_and_count_types_are_strict(self):
        for key in ("qpos", "qvel", "act", "ctrl", "body_xpos", "body_xquat"):
            with self.subTest(key=key):
                packet = copy.deepcopy(self.packet); packet[key] = packet[key] + [0]
                with self.assertRaises(SnapshotError): self.accept(packet)
        for value in (True, 21.0, -1, 22):
            packet = copy.deepcopy(self.packet); packet["nq"] = value
            with self.assertRaises(SnapshotError): self.accept(packet)

    def test_nonfinite_and_non_numeric_state_rejected(self):
        for bad in (float("nan"), float("inf"), -float("inf"), True, "0", 10 ** 400):
            packet = copy.deepcopy(self.packet); packet["qvel"][3] = bad
            with self.subTest(value=str(bad)[:20]), self.assertRaises(SnapshotError): self.accept(packet)
        packet = copy.deepcopy(self.packet); packet["body_xpos"][3][1] = float("nan")
        with self.assertRaises(SnapshotError): self.accept(packet)

    def test_body_quaternion_must_be_unit(self):
        self.packet["body_xquat"][2] = [0, 0, 0, 0]
        with self.assertRaisesRegex(SnapshotError, "nonunit_body"): self.accept()

    def test_reset_keeps_global_sequence_and_allows_time_restart(self):
        self.accept()
        packet = copy.deepcopy(self.packet); packet.update(generation=2, sequence=11, sim_time_s=0.005)
        self.assertEqual(self.accept(packet).generation, 2)
        packet.update(generation=1, sequence=12, sim_time_s=9.0)
        with self.assertRaisesRegex(SnapshotError, "old_generation"): self.accept(packet)
        self.assertEqual(self.validator.latest.sequence, 11)

    def test_reordered_duplicate_and_regressed_time_rejected(self):
        self.accept()
        for updates in (dict(sequence=10), dict(sequence=9), dict(generation=2, sequence=1),
                        dict(sequence=11, sim_time_s=2.999)):
            with self.subTest(updates=updates):
                packet = copy.deepcopy(self.packet); packet.update(updates)
                with self.assertRaises(SnapshotError): self.accept(packet)

    def test_packets_have_bounded_utf8_unique_json(self):
        for packet in (b"", b"x" * (MAX_PACKET_BYTES + 1), b"\xff", b"[]", b'{"schema":1,"schema":2}'):
            with self.subTest(length=len(packet)), self.assertRaises(SnapshotError):
                self.validator.accept(packet)

    def test_integer_precision_and_time_contract(self):
        for key, value in (("generation", 2 ** 53), ("sequence", -1), ("sequence", True),
                           ("sim_time_s", -0.1), ("sim_time_s", float("inf"))):
            packet = copy.deepcopy(self.packet); packet[key] = value
            with self.subTest(key=key), self.assertRaises(SnapshotError): self.accept(packet)

    def test_pose_comparison_handles_sign_and_detects_independent_errors(self):
        snapshot = self.accept()
        positions = copy.deepcopy(self.packet["body_xpos"])
        quaternions = [[-1.0, 0, 0, 0] for _ in range(16)]
        self.assertEqual(compare_body_poses(positions, quaternions, snapshot),
                         dict(position_m=0.0, orientation_rad=0.0, quaternion_linf=0.0))
        positions[4][1] = 0.002
        quaternions[7] = [math.cos(0.01), math.sin(0.01), 0, 0]
        error = compare_body_poses(positions, quaternions, snapshot)
        self.assertAlmostEqual(error["position_m"], 0.002)
        self.assertAlmostEqual(error["orientation_rad"], 0.02)


if __name__ == "__main__":
    unittest.main()
