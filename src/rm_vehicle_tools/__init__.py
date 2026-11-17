"""Standard-library vehicle-package validation; imports never execute package code."""

from .vehicle import VehicleError, load_vehicle

__all__ = ["VehicleError", "load_vehicle"]
