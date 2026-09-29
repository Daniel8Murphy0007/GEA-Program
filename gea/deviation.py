"""deviation — wellbore deviation, MD vs TVD (v1.6.0 extension).

Real wells are deviated: a gauge sits at a MEASURED DEPTH (MD, distance along
the wellbore string) while the pressure and temperature it feels are set by
TRUE VERTICAL DEPTH (TVD). A vertical-well model overstates P/T at every
station of a deviated well — on a 60-degree tangent, by nearly a factor of
two in the deviated section.

`DeviationSurvey` maps MD -> TVD from survey pairs (CSV loadable) or from the
common kickoff-and-tangent well shape. Attach it to `SimulatorConfig` or
`CaseStudyConfig`: sensors stay addressed by MD (their position on the
string); the physics is evaluated at TVD.

Headless-safe: numpy only.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import List

import numpy as np


@dataclass
class DeviationSurvey:
    """MD -> TVD mapping from survey stations (sorted on MD, interpolated)."""
    md_ft: List[float]
    tvd_ft: List[float]
    name: str = "deviation"

    def tvd_of(self, md_ft: float) -> float:
        return float(np.interp(md_ft, self.md_ft, self.tvd_ft))

    @classmethod
    def from_kickoff(cls, kickoff_md_ft: float, inclination_deg: float,
                     td_md_ft: float, n_points: int = 50) -> "DeviationSurvey":
        """The common well shape: vertical to the kickoff point, then a
        straight tangent held at `inclination_deg` from vertical to TD.
        TVD(md) = md for md <= kickoff; kickoff + (md-kickoff)*cos(incl) after.
        """
        if not (0.0 <= inclination_deg < 90.0):
            raise ValueError("inclination must be in [0, 90) degrees")
        c = math.cos(math.radians(inclination_deg))
        mds = sorted(set(float(m) for m in np.linspace(0.0, td_md_ft, n_points))
                     | {float(kickoff_md_ft)})   # the kink must be an exact station
        tvds = [md if md <= kickoff_md_ft else kickoff_md_ft + (md - kickoff_md_ft) * c
                for md in mds]
        return cls(md_ft=mds, tvd_ft=tvds,
                   name=f"kickoff {kickoff_md_ft:.0f} ft / {inclination_deg:g} deg tangent")


def load_deviation_csv(path) -> DeviationSurvey:
    """Load a deviation survey CSV with header: md_ft,tvd_ft
    (any row order; sorted on MD; extra columns ignored)."""
    md, tvd = [], []
    with Path(path).open(newline="") as f:
        for row in csv.DictReader(f):
            md.append(float(row["md_ft"]))
            tvd.append(float(row["tvd_ft"]))
    order = np.argsort(md)
    return DeviationSurvey(md_ft=[md[i] for i in order],
                           tvd_ft=[tvd[i] for i in order],
                           name=Path(path).stem)
