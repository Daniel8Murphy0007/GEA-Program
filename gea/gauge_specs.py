# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""gauge_specs - gauge datasheets, each with its citation.

A `GaugeSpec` carries the published numbers of one instrument: full scale,
the drift specification (percent of full scale per year), the accuracy, the
temperature rating, and the prose that says where every number came from.
Presets are public datasheets fetched and verified on the date given;
`load_gauge_spec_json` takes any datasheet the user types in. A spec without
a citation is not a spec and is rejected.

Citation rules:
  * Every preset names its source and the date it was verified. No invented
    vendor numbers - a value that could not be verified in the fetched source
    text was not made a preset.
  * The drift specification is the datasheet's reference-condition bound
    (a "<0.01 %FS/yr" line is used as 0.01). Nothing is added to it for
    temperature or pressure; above the rating the program says so instead
    (`gauge_aging.aging_rate`).

The default, when no datasheet is given, is the GEOQ 177 30,000 psi entry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class GaugeSpec:
    """A gauge datasheet. `source` is mandatory prose naming where every number came from."""
    name: str
    source: str
    full_scale_psi: float
    baseline_drift_pct_fs_yr: float
    max_temp_C: Optional[float] = None
    accuracy_pct_fs: Optional[float] = None
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Presets - every number cited; verified against the named source text
# ---------------------------------------------------------------------------
GAUGE_SPECS: Dict[str, GaugeSpec] = {
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
DEFAULT_SPEC_NAME = 'geoq177_30k'
DEFAULT_SPEC = GAUGE_SPECS[DEFAULT_SPEC_NAME]


def get_spec(name: Optional[str]) -> GaugeSpec:
    """A preset by name; None or '' gives the default. Unknown names raise with the list."""
    if not name:
        return DEFAULT_SPEC
    if name not in GAUGE_SPECS:
        raise KeyError(f"no gauge datasheet named {name!r}; the presets are {sorted(GAUGE_SPECS)} - or load your own with load_gauge_spec_json")
    return GAUGE_SPECS[name]


def load_gauge_spec_json(path) -> GaugeSpec:
    """Load a user-entered datasheet from JSON. Required keys: name, source,
    full_scale_psi, baseline_drift_pct_fs_yr. Optional keys map to the other
    GaugeSpec fields. A missing/empty `source` is rejected."""
    with Path(path).open(encoding="utf-8") as f:
        d = json.load(f)
    if not d.get('source'):
        raise ValueError("gauge spec JSON must carry a non-empty 'source' citation")
    allowed = {k for k in GaugeSpec.__dataclass_fields__}
    unknown = sorted(k for k in d if k not in allowed)
    if unknown:
        raise ValueError(f"gauge spec JSON: unknown keys {unknown}; the fields are {sorted(allowed)}")
    return GaugeSpec(**{k: v for k, v in d.items() if k in allowed})
