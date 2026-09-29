from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Layer:
    """A geological layer used in a subsurface model."""

    name: str
    thickness_ft: float
    porosity: float
    permeability_md: float
    pressure_gradient_psi_ft: float = 0.43
    temperature_gradient_f_ft: float = 0.015

    def __post_init__(self) -> None:
        if self.thickness_ft <= 0:
            raise ValueError("Layer thickness must be positive.")
        if not 0 <= self.porosity <= 1:
            raise ValueError("Porosity must be between 0 and 1.")
        if self.permeability_md < 0:
            raise ValueError("Permeability must be non-negative.")


@dataclass(frozen=True)
class WellSimulationResult:
    well_depth_ft: float
    flow_rate_bpd: float
    reservoir_pressure_psi: float
    bottom_hole_pressure_psi: float
    productivity_index: float
    estimated_recovery_pct: float

    def as_dict(self) -> dict[str, float]:
        return {
            "well_depth_ft": self.well_depth_ft,
            "flow_rate_bpd": self.flow_rate_bpd,
            "reservoir_pressure_psi": self.reservoir_pressure_psi,
            "bottom_hole_pressure_psi": self.bottom_hole_pressure_psi,
            "productivity_index": self.productivity_index,
            "estimated_recovery_pct": self.estimated_recovery_pct,
        }


class SubsurfaceModel:
    """Simple reservoir model used for monitoring and reporting scenarios."""

    def __init__(self, layers: Sequence[Layer] | Iterable[Layer]):
        self.layers = tuple(layers)
        if not self.layers:
            raise ValueError("At least one layer is required to build a model.")

    @property
    def total_thickness_ft(self) -> float:
        return sum(layer.thickness_ft for layer in self.layers)

    @property
    def average_porosity(self) -> float:
        weighted_total = sum(layer.thickness_ft * layer.porosity for layer in self.layers)
        return weighted_total / self.total_thickness_ft

    @property
    def average_permeability_md(self) -> float:
        weighted_total = sum(layer.thickness_ft * layer.permeability_md for layer in self.layers)
        return weighted_total / self.total_thickness_ft

    @property
    def average_pressure_gradient_psi_ft(self) -> float:
        weighted_total = sum(
            layer.thickness_ft * layer.pressure_gradient_psi_ft for layer in self.layers
        )
        return weighted_total / self.total_thickness_ft

    @property
    def average_temperature_gradient_f_ft(self) -> float:
        weighted_total = sum(
            layer.thickness_ft * layer.temperature_gradient_f_ft for layer in self.layers
        )
        return weighted_total / self.total_thickness_ft

    def reservoir_pressure_psi(self, depth_ft: float) -> float:
        return max(0.0, 14.7 + depth_ft * self.average_pressure_gradient_psi_ft)

    def reservoir_temperature_f(self, depth_ft: float) -> float:
        return 60.0 + depth_ft * self.average_temperature_gradient_f_ft

    def simulate_well(self, well_depth_ft: float, flow_rate_bpd: float) -> WellSimulationResult:
        if well_depth_ft <= 0:
            raise ValueError("Well depth must be positive.")
        if flow_rate_bpd < 0:
            raise ValueError("Flow rate must be non-negative.")

        reservoir_pressure = self.reservoir_pressure_psi(well_depth_ft)
        drawdown = max(0.0, flow_rate_bpd * 0.12)
        bottom_hole_pressure = max(0.0, reservoir_pressure - drawdown)
        productivity_index = (
            self.average_permeability_md * self.average_porosity * self.total_thickness_ft
        ) / max(1.0, well_depth_ft / 1000.0)
        estimated_recovery_pct = min(
            95.0,
            18.0 + (self.average_porosity * 100.0) * 0.6 + (self.average_permeability_md / 100.0) * 0.4,
        )

        return WellSimulationResult(
            well_depth_ft=well_depth_ft,
            flow_rate_bpd=flow_rate_bpd,
            reservoir_pressure_psi=round(reservoir_pressure, 2),
            bottom_hole_pressure_psi=round(bottom_hole_pressure, 2),
            productivity_index=round(productivity_index, 4),
            estimated_recovery_pct=round(estimated_recovery_pct, 2),
        )

    def generate_report(self, *, well_depth_ft: float, flow_rate_bpd: float) -> str:
        result = self.simulate_well(well_depth_ft, flow_rate_bpd)
        layer_summary = "\n".join(
            f"- {layer.name}: {layer.thickness_ft:.1f} ft, {layer.porosity:.2%} porosity, {layer.permeability_md:.1f} md"
            for layer in self.layers
        )

        return (
            "GEA-Program Subsurface Report\n"
            "============================\n"
            f"Well depth: {result.well_depth_ft:.1f} ft\n"
            f"Flow rate: {result.flow_rate_bpd:.1f} bpd\n"
            f"Reservoir pressure: {result.reservoir_pressure_psi:.2f} psi\n"
            f"Bottom-hole pressure: {result.bottom_hole_pressure_psi:.2f} psi\n"
            f"Productivity index: {result.productivity_index:.4f}\n"
            f"Estimated recovery: {result.estimated_recovery_pct:.2f}%\n\n"
            "Layer model:\n"
            f"{layer_summary}\n"
            f"Average porosity: {self.average_porosity:.2%}\n"
            f"Average permeability: {self.average_permeability_md:.1f} md"
        )


def simulate_well(layers: Sequence[Layer] | Iterable[Layer], well_depth_ft: float, flow_rate_bpd: float) -> WellSimulationResult:
    model = SubsurfaceModel(layers)
    return model.simulate_well(well_depth_ft, flow_rate_bpd)


def generate_report(layers: Sequence[Layer] | Iterable[Layer], well_depth_ft: float, flow_rate_bpd: float) -> str:
    model = SubsurfaceModel(layers)
    return model.generate_report(well_depth_ft=well_depth_ft, flow_rate_bpd=flow_rate_bpd)
