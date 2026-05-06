"""Yaw / heading helpers for intersection-pairing presets."""

import math


def principal_value(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def effective_approach_yaw(connector_yaw, vehicle_yaw):
    # Prefer the connector heading, but short windows can snap to a connector
    # pointing the wrong way; fall back to vehicle yaw if they disagree by >90 deg.
    if connector_yaw is None:
        return vehicle_yaw
    if vehicle_yaw is None:
        return connector_yaw
    if abs(principal_value(vehicle_yaw - connector_yaw)) > math.pi / 2:
        return vehicle_yaw
    return connector_yaw


def opposite_approach(yaw_a, yaw_b):
    diff = abs(principal_value(yaw_b - yaw_a))
    tol = math.radians(30)
    return (math.pi - tol) <= diff <= math.pi


def perpendicular_approach(yaw_a, yaw_b):
    diff = abs(principal_value(yaw_b - yaw_a))
    tol = math.radians(30)
    return (math.pi / 2 - tol) <= diff <= (math.pi / 2 + tol)
