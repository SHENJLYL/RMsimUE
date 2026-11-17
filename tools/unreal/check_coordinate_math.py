"""Independent matrix identity check. Does not compile/execute Unreal C++.

The actual RosCoordinates.h implementation also has UE automation checks under
RMSim.Coordinates.ROS_to_UE; those remain unrun until an engine is available.
"""
import json
import math
import random


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def matrix_from_rpy(r, p, y):
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return multiply(multiply([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]],
                             [[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]]),
                    [[1, 0, 0], [0, cr, -sr], [0, sr, cr]])


def mirrored_quat_matrix(r, p, y):
    cr, sr, cp, sp, cy, sy = math.cos(r / 2), math.sin(r / 2), math.cos(p / 2), math.sin(p / 2), math.cos(y / 2), math.sin(y / 2)
    x = -(sr * cp * cy - cr * sp * sy)
    qy = cr * sp * cy + sr * cp * sy
    z = -(cr * cp * sy - sr * sp * cy)
    w = cr * cp * cy + sr * sp * sy
    return [[1 - 2 * (qy * qy + z * z), 2 * (x * qy - z * w), 2 * (x * z + qy * w)],
            [2 * (x * qy + z * w), 1 - 2 * (x * x + z * z), 2 * (qy * z - x * w)],
            [2 * (x * z - qy * w), 2 * (qy * z + x * w), 1 - 2 * (x * x + qy * qy)]]


def main():
    generator = random.Random(20261113)
    cases = [(math.pi / 2, 0, 0), (0, math.pi / 2, 0), (0, 0, math.pi / 2)]
    cases += [tuple(generator.uniform(-math.pi, math.pi) for _ in range(3)) for _ in range(1000)]
    sign = [1, -1, 1]
    largest_error = 0.0
    for rpy in cases:
        rotation = matrix_from_rpy(*rpy)
        reflected = [[sign[i] * rotation[i][j] * sign[j] for j in range(3)] for i in range(3)]
        actual = mirrored_quat_matrix(*rpy)
        error = max(abs(actual[i][j] - reflected[i][j]) for i in range(3) for j in range(3))
        assert error < 1.e-12, (rpy, error)
        largest_error = max(largest_error, error)
    print(json.dumps({"scope": "python_matrix_identity_only", "cases": len(cases),
                      "max_abs_error": largest_error, "cpp_compiled": False,
                      "unreal_executed": False, "result": "passed"}, indent=2))


if __name__ == "__main__":
    main()
