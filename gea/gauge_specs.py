# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""gauge_specs — real-datasheet gauge parameterization.

Until now every layer ran on the template's generic anchors (0.215 %FS/yr
baseline, 150 C / 15,000 psi knees, FS 30,000 psi). This module lets the whole
stack run on a REAL gauge's published numbers instead: a `GaugeSpec` carries
the datasheet values with an explicit citation, presets carry web-verified
public specs, and `load_gauge_spec_json` takes any datasheet the user types in.

Citation rules:
  * Every preset cites its source and the date it was verified. No invented
    vendor numbers — the one candidate value that could not be verified in the
    fetched source text (a "<0.02 %FS/yr at 200 C" claim from a search-engine
    summary) was NOT made a preset.
  * Datasheet drift bounds are REFERENCE-CONDITION spec limits (the GEOQ 177
    table's <0.01 %FS/yr is ~20x below the template's 0.215 stressed-service
    baseline). The program suppression RATIO (1.0324) is baseline-independent;
    the absolute separation in psi/yr scales with whichever baseline the spec
    supplies. Both readings are honest; the module reports which one is in use.

External confirmation gained in sourcing (2026-08-23): the ChampionX
Quartzdyne performance page states drift rate increases with temperature with
engineering focus at 150 C and above — independent support for the template's
150 C thermal-knee anchor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class GaugeSpec:
    """A gauge datasheet: the anchors the physics layer runs on.

    `source` is mandatory prose naming where every number came from —
    a spec without a citation is not a spec.
    """
    name: str
    source: str
    full_scale_psi: float
    baseline_drift_pct_fs_yr: float
    max_temp_C: Optional[float] = None
    accuracy_pct_fs: Optional[float] = None
    thermal_knee_C: float = 150.0        # anchor: industry thermal knee (template; ChampionX-confirmed focus >=150 C)
    pressure_knee_psi: float = 15000.0   # anchor: template pressure knee
    thermal_exponent: float = 1.15       # template engineering fit
    pressure_exponent: float = 0.9       # template engineering fit
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Presets — every number cited; verified against the named source text
# ---------------------------------------------------------------------------
GAUGE_SPECS: Dict[str, GaugeSpec] = {
    'template_generic': GaugeSpec(
        name='template_generic',
        source=("program template baseline (design note, 22 Aug 2026): 0.215 %FS/yr "
                "typical good-quartz STRESSED-SERVICE baseline with 150 C / "
                "15,000 psi knees and exponents 1.15/0.9 (engineering fit); "
                "FS 30,000 psi HPHT class. The default when no datasheet is given."),
        full_scale_psi=30000.0,
        baseline_drift_pct_fs_yr=0.215,
        max_temp_C=200.0,
        accuracy_pct_fs=None,
        notes="stressed-service drift class, not a reference-condition spec bound",
    ),
    'geoq177_16k': GaugeSpec(
        name='geoq177_16k',
        source=("GEO PSI GEOQ 177 public specification table (Quartzdyne "
                "sensor), geopsi.com/products/downhole-gauges/geoq-177/, "
                "fetched 2026-08-23: 16,000 psiA range; pressure drift "
                "<0.01 %FS/yr; accuracy +/-0.02 %FS; 177 C rating."),
        full_scale_psi=16000.0,
        baseline_drift_pct_fs_yr=0.01,
        max_temp_C=177.0,
        accuracy_pct_fs=0.02,
        notes="reference-condition spec bound (drift is '<' the value, used as the bound)",
    ),
    'geoq177_30k': GaugeSpec(
        name='geoq177_30k',
        source=("GEO PSI GEOQ 177 public specification table (Quartzdyne "
                "sensor), geopsi.com/products/downhole-gauges/geoq-177/, "
                "fetched 2026-08-23: 30,000 psiA range; pressure drift "
                "<0.01 %FS/yr; accuracy +/-0.025 %FS; 177 C rating."),
        full_scale_psi=30000.0,
        baseline_drift_pct_fs_yr=0.01,
        max_temp_C=177.0,
        accuracy_pct_fs=0.025,
        notes="reference-condition spec bound (drift is '<' the value, used as the bound)",
    ),
}


def load_gauge_spec_json(path) -> GaugeSpec:
    """Load a user-entered datasheet from JSON. Required keys: name, source,
    full_scale_psi, baseline_drift_pct_fs_yr. Optional keys map to the other
    GaugeSpec fields. A missing/empty `source` is rejected."""
    with Path(path).open(encoding="utf-8") as f:
        d = json.load(f)
    if not d.get('source'):
        raise ValueError("gauge spec JSON must carry a non-empty 'source' citation")
    allowed = {k for k in GaugeSpec.__dataclass_fields__}
    return GaugeSpec(**{k: v for k, v in d.items() if k in allowed})
