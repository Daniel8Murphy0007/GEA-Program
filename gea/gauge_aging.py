# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""gauge_aging - the aging rate of a gauge is what its datasheet says it is.

One model, one source: the published drift specification of the instrument
(percent of full scale per year), taken from a cited datasheet (`gauge_specs`)
or from the user's own. The program does not invent a temperature or pressure
dependence the datasheet does not publish: above the instrument's rated
temperature the rate is returned unchanged and flagged `over_rating`, so the
reader sees that the datasheet no longer applies there rather than a number
the program made up.

Everything that needs an aging rate - the reconciler's drift band, the model
card, the simulator's synthetic drift, the bench test's acceptance bound -
reads it from here.

Numpy-free; stdlib only.
"""

from __future__ import annotations

from typing import Optional

from .gauge_specs import GaugeSpec, DEFAULT_SPEC


def aging_rate(spec: Optional[GaugeSpec] = None, temp_c: Optional[float] = None, pressure_psi: Optional[float] = None) -> dict:
    """The datasheet aging rate at the given conditions.

    Returns {rate_pct_fs_yr, rate_psi_yr, full_scale_psi, accuracy_pct_fs, accuracy_psi,
             over_rating, rated_temp_C, rated_pressure_psi, gauge_spec, source, basis}.
    """
    s = spec or DEFAULT_SPEC
    rate_pct = float(s.baseline_drift_pct_fs_yr)
    fs = float(s.full_scale_psi)
    over_t = temp_c is not None and s.max_temp_C is not None and temp_c > float(s.max_temp_C)
    over_p = pressure_psi is not None and pressure_psi > fs
    return {
        'rate_pct_fs_yr': rate_pct,
        'rate_psi_yr': rate_pct / 100.0 * fs,
        'full_scale_psi': fs,
        'accuracy_pct_fs': s.accuracy_pct_fs,
        'accuracy_psi': (float(s.accuracy_pct_fs) / 100.0 * fs) if s.accuracy_pct_fs is not None else None,
        'over_rating': bool(over_t or over_p),
        'over_rating_detail': ((f'temperature {temp_c:g} C above the rated {s.max_temp_C:g} C; ' if over_t else '')
                               + (f'pressure {pressure_psi:g} psi above full scale {fs:g} psi' if over_p else '')).strip('; '),
        'rated_temp_C': s.max_temp_C,
        'rated_pressure_psi': fs,
        'gauge_spec': s.name,
        'source': s.source,
        'basis': 'the published drift specification of the instrument, taken as its aging rate; no dependence on conditions is added',
    }


def rate_psi_yr(spec: Optional[GaugeSpec] = None) -> float:
    return aging_rate(spec)['rate_psi_yr']


def accuracy_psi(spec: Optional[GaugeSpec] = None) -> Optional[float]:
    return aging_rate(spec)['accuracy_psi']
