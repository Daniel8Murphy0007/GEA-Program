from __future__ import annotations

import argparse

from .model import Layer, SubsurfaceModel


DEFAULT_LAYERS = (
    Layer("Shale cap", 400.0, 0.08, 15.0),
    Layer("Sandstone reservoir", 1200.0, 0.22, 185.0),
    Layer("Carbonate seal", 600.0, 0.12, 60.0),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gea-program",
        description="Run a simple geological subsurface simulation and generate a monitoring report.",
    )
    parser.add_argument("--well-depth-ft", type=float, default=4200.0, help="Target well depth in feet.")
    parser.add_argument("--flow-rate-bpd", type=float, default=1500.0, help="Production flow rate in barrels per day.")
    parser.add_argument(
        "--layer-config",
        nargs="*",
        default=[],
        help="Optional layer definitions in the form name:thickness_ft:porosity:permeability_md.",
    )
    return parser


def _parse_layer_config(raw_layers: list[str]) -> tuple[Layer, ...]:
    if not raw_layers:
        return DEFAULT_LAYERS

    parsed: list[Layer] = []
    for item in raw_layers:
        name, thickness_ft, porosity, permeability_md = item.split(":")
        parsed.append(
            Layer(
                name=name,
                thickness_ft=float(thickness_ft),
                porosity=float(porosity),
                permeability_md=float(permeability_md),
            )
        )
    return tuple(parsed)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    model = SubsurfaceModel(_parse_layer_config(args.layer_config))
    print(model.generate_report(well_depth_ft=args.well_depth_ft, flow_rate_bpd=args.flow_rate_bpd))
    return 0
