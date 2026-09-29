# GEA-Program

GEA-Program is a geological subsurface simulation toolkit for active and passive well monitoring, production reporting, and reservoir characterization. It models layered subsurface strata, estimates reservoir pressure conditions, and produces a concise engineering report for a target well.

## Features
- Layered geological model with porosity and permeability inputs
- Reservoir pressure and temperature estimation by depth
- Productivity index and estimated recovery calculations
- CLI for generating a quick monitoring report

## Quick start

```bash
python -m gea_program --well-depth-ft 4200 --flow-rate-bpd 1500
```

You can also override the default formation stack with custom layers using a colon-delimited format:

```bash
python -m gea_program --layer-config "Sand:1200:0.24:220" "Shale:400:0.08:12"
```

## Python API

```python
from gea_program import Layer, SubsurfaceModel

layers = [
    Layer("Shale cap", 400.0, 0.08, 15.0),
    Layer("Sandstone reservoir", 1200.0, 0.22, 185.0),
]

model = SubsurfaceModel(layers)
result = model.simulate_well(well_depth_ft=4200.0, flow_rate_bpd=1500.0)
print(result.as_dict())
```
