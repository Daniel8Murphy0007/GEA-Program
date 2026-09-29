# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Gamma-ray / lithology module (v1.46.0) - field-tier step 6 (first half).

The evaluation's stated oilfield goal: "No NaI(Tl) GR, API units, lithology
from GR, or LAS curve -> formation flag. Several catalogue LAS files already
carry GR." They do - and this module works ONLY from those measured curves:

    ktb_vb_vlog251_temperature   GR   1,082 points, 74.5-124.7 API (KTB pilot)
    volve_15_9_19_sr_excerpt     GR     133 points,  5.3-72.5 gAPI (North Sea)
    l07_01_nl_excerpt            GR      50 points, 101.5-130.6 GAPI
    ... (any LiveStream with an API-unit gamma channel)

Honesty rules, enforced in code:
- CHANNEL DISCIPLINE: a gamma channel is one with an API-family unit
  (API/GAPI/gAPI). Name matching alone is refused - the catalogue itself
  contains the trap cases (KTB 'GRAV' in mGals is gravimetry; 'Density
  grain' contains the letters GR): units decide, not substrings.
- METHOD LABELS: shale volume uses the linear gamma-ray index
  Vsh = (GR - GR_clean)/(GR_shale - GR_clean) - the industry-standard
  first-pass estimate, labeled INDUSTRY_STANDARD_METHOD (classical
  petrophysics, NOT a GEA derivation; per the Hybrid doctrine it is never
  dressed as one). Clean/shale picks default to the P5/P95 percentiles of
  the measured curve, labeled STATISTICAL_PICKS (they are statistics of
  this log, not formation knowledge); caller-supplied picks override.
  The sand/shale cutoff defaults to 0.5, labeled CONVENTION.
- NO INVENTED TOOL: no NaI(Tl) vendor datasheet is shipped, because none
  was fetched - the detector-tool entry stays PARAMETERS_USER_SUPPLIED
  (the tool-library rule); this module processes MEASURED curves only.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from .profile_catalog import CATALOG

API_UNITS = {"API", "GAPI"}          # normalized upper-case API family
METHOD_LABEL = ("INDUSTRY_STANDARD_METHOD: linear gamma-ray index "
                "Vsh = (GR - GR_clean)/(GR_shale - GR_clean) - classical "
                "petrophysics first-pass shale volume; NOT a GEA derivation")
PICKS_LABEL = ("STATISTICAL_PICKS: GR_clean/GR_shale = P5/P95 percentiles of "
               "THIS measured curve (statistics of the log, not formation "
               "knowledge); supply picks from core/regional work to override")
CUTOFF_LABEL = "CONVENTION: sand/shale flag cutoff Vsh = %.2f (operator-adjustable)"
DETECTOR_NOTE = ("NaI(Tl)/BGO detector tool: PARAMETERS_USER_SUPPLIED - no "
                 "vendor datasheet was fetched, so none is shipped; this "
                 "module processes measured API curves only")


def find_gr_channels(stream) -> List[str]:
    """Gamma channels by UNIT DISCIPLINE: API-family unit required. The
    catalogue's own trap cases (GRAV in mGals = gravimetry; 'Density grain'
    contains 'GR') are excluded by construction: units decide."""
    out = []
    for name, ch in stream.channels.items():
        unit = (ch.unit or "").strip().upper()
        if unit in API_UNITS:
            v = np.asarray(ch.values, dtype=float)
            if np.any(~np.isnan(v)):
                out.append(name)
    return out


def shale_volume(gr: np.ndarray,
                 gr_clean: Optional[float] = None,
                 gr_shale: Optional[float] = None) -> Tuple[np.ndarray, dict]:
    """Linear gamma-ray index with labeled picks. Returns (vsh, meta);
    values clamped to [0, 1] with the clamp count disclosed in meta."""
    g = np.asarray(gr, dtype=float)
    fin = g[~np.isnan(g)]
    if len(fin) < 4:
        raise ValueError("shale_volume needs >= 4 finite GR samples")
    picks = "caller-supplied"
    if gr_clean is None or gr_shale is None:
        gr_clean = float(np.percentile(fin, 5))
        gr_shale = float(np.percentile(fin, 95))
        picks = PICKS_LABEL
    if gr_shale <= gr_clean:
        raise ValueError(
            f"GR_shale ({gr_shale:g}) must exceed GR_clean ({gr_clean:g}) - "
            "a flat curve carries no lithology contrast (refusing, not "
            "inventing one)")
    raw = (g - gr_clean) / (gr_shale - gr_clean)
    vsh = np.clip(raw, 0.0, 1.0)
    clamped = int(np.sum((raw < 0.0) | (raw > 1.0)) - np.sum(np.isnan(raw)))
    return vsh, {"method": METHOD_LABEL, "picks": picks,
                 "gr_clean_api": round(gr_clean, 2),
                 "gr_shale_api": round(gr_shale, 2),
                 "clamped_samples": max(clamped, 0)}


def formation_flags(depths: np.ndarray, vsh: np.ndarray,
                    cutoff: float = 0.5) -> List[dict]:
    """Contiguous SAND/SHALE intervals from the Vsh curve. NaN samples end
    intervals (gaps are gaps - never bridged)."""
    d = np.asarray(depths, dtype=float)
    v = np.asarray(vsh, dtype=float)
    out: List[dict] = []
    cur = None
    for i in range(len(d)):
        if np.isnan(v[i]):
            cur = None
            continue
        flag = "SHALE" if v[i] >= cutoff else "SAND"
        if cur is not None and cur["flag"] == flag:
            cur["base_depth"] = float(d[i])
            cur["_vals"].append(float(v[i]))
        else:
            cur = {"flag": flag, "top_depth": float(d[i]),
                   "base_depth": float(d[i]), "_vals": [float(v[i])]}
            out.append(cur)
    for iv in out:
        iv["n_samples"] = len(iv["_vals"])
        iv["mean_vsh"] = round(float(np.mean(iv["_vals"])), 3)
        del iv["_vals"]
    return out


def gamma_report(entry_name: str, channel: Optional[str] = None,
                 cutoff: float = 0.5,
                 gr_clean: Optional[float] = None,
                 gr_shale: Optional[float] = None) -> dict:
    """Lithology-from-GR on a catalogue entry's MEASURED curve: channel by
    unit discipline, Vsh with labeled method/picks, sand/shale intervals.
    Refuses - naming what it saw - when no API-unit gamma channel exists."""
    st = CATALOG[entry_name].stream()
    chans = find_gr_channels(st)
    if not chans:
        seen = {n: st.channels[n].unit for n in st.channels}
        raise NotImplementedError(
            f"'{entry_name}' has no gamma channel with an API-family unit - "
            f"channels seen: {seen}. Unit discipline refuses name-only "
            "matches (GRAV/mGals is gravimetry, not gamma).")
    if channel is None:
        channel = max(chans, key=lambda c: int(
            np.sum(~np.isnan(np.asarray(st.channels[c].values, dtype=float)))))
    elif channel not in chans:
        raise KeyError(f"'{channel}' is not an API-unit gamma channel of "
                       f"'{entry_name}' (gamma channels: {chans})")
    g = np.asarray(st.channels[channel].values, dtype=float)
    vsh, meta = shale_volume(g, gr_clean=gr_clean, gr_shale=gr_shale)
    fin = g[~np.isnan(g)]
    intervals = formation_flags(st.index, vsh, cutoff=cutoff)
    return {
        "entry": entry_name, "channel": channel,
        "unit": st.channels[channel].unit,
        "n_samples": int(len(fin)),
        "depth_span": [float(st.index.min()), float(st.index.max())],
        "gr_api": {"min": round(float(fin.min()), 2),
                   "max": round(float(fin.max()), 2),
                   "mean": round(float(fin.mean()), 2)},
        "vsh": {"min": round(float(np.nanmin(vsh)), 3),
                "max": round(float(np.nanmax(vsh)), 3), **meta},
        "cutoff": CUTOFF_LABEL % cutoff,
        "intervals": intervals,
        "detector_note": DETECTOR_NOTE,
        "provenance": {
            "source": CATALOG[entry_name].provenance.get("source_database", "?"),
            "license": CATALOG[entry_name].provenance.get("license", "?")},
    }


def gamma_entries() -> Dict[str, List[str]]:
    """Catalogue entries carrying at least one API-unit gamma channel."""
    out: Dict[str, List[str]] = {}
    for name, e in CATALOG.items():
        try:
            chans = find_gr_channels(e.stream())
        except Exception:
            continue
        if chans:
            out[name] = chans
    return out
