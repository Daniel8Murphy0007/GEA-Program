"""strata_join - the depth-join / joint-distribution layer of the strata-inference engine.

v1.69.0 (first layer of the ground-strata imaging mission, Daniel's 2026-08-28 direction).

WHAT THIS IS
    The catalogue holds 50 wells, but its entries are single-property tables.
    Probabilistic strata inference needs JOINT observations: many properties,
    same borehole, overlapping depths.  This module depth-aligns the catalogue
    entries that share a borehole into co-located joint property tables, and
    exposes the empirical structure those tables contain:

      joint_table(well)          - depth-binned matrix of co-located properties
      pair_stats(well, a, b)     - co-located sample count + Pearson r for a pair
      conditional(well, t, g, v) - empirical P(target | given=v): k-nearest
                                   co-located bins -> estimate, spread, support
      library_pairs()            - every property pair in the library with
                                   enough co-located support to mean anything

HONESTY RULES (same discipline as the catalogue itself)
    - No interpolation is invented: a depth bin carries a property value only
      if the archive actually sampled that property inside the bin (bin mean).
    - Thin data REFUSES: pair statistics below MIN_PAIR_N co-located bins and
      conditionals below MIN_COND_N return a REFUSED status instead of a number.
    - Extrapolation is labeled: a conditional queried outside the observed
      range of the conditioning property says so in its own output.
    - Every number is recomputed from the verbatim catalogue archives at call
      time; nothing here is fitted, stored, or tuned.
"""

from __future__ import annotations

import math

from .profile_catalog import CATALOG

# Wells with >= 2 depth-indexed catalogue entries whose coverage overlaps.
# well -> (default_bin_m, {property: (catalogue_entry, channel)})
WELL_GROUPS = {
    "504b": (5.0, {
        "porosity": ("dsdp_504b_physical_properties", "Poros"),
        "wet_bulk_density": ("dsdp_504b_physical_properties", "WBD"),
        "grain_density": ("dsdp_504b_physical_properties", "Density grain"),
        "water_content": ("dsdp_504b_physical_properties", "Water wm"),
        "vp": ("dsdp_504b_sound_velocity", "Vp"),
    }),
    "ktb_hb": (250.0, {
        "density": ("ktb_hb_bhgm_density", "RHO"),
        "temperature": ("ktb_hb_hlog246_temperature", "TMP3"),
        "compressive_strength": ("ktb_hb_rockmech_compress",
                                 "COMPRESSIVE_STRENGTH"),
        "youngs_modulus": ("ktb_hb_rockmech_compress", "E_MODUL"),
    }),
    "site_1027": (60.0, {
        "thermal_conductivity": ("odp_1027b_thermal_conductivity", "k"),
        "cork_temperature": ("odp_1027c_cork_temperature", "t (1999)"),
    }),
}

MIN_PAIR_N = 8   # co-located bins below this: pair statistics refuse
MIN_COND_N = 5   # neighbours below this: conditional refuses


def _series(entry, channel):
    """Depth-aligned finite samples of one catalogue channel."""
    st = CATALOG[entry].stream()
    if st.index_kind != "depth":
        raise ValueError("%s is not depth-indexed" % entry)
    ch = st.channels[channel]
    out = [(float(d), float(v)) for d, v in zip(st.index, ch.values)
           if v == v]
    return out, ch.unit


def joint_table(well, bin_m=None):
    """Depth-binned co-located property matrix for one well group.

    Returns {'well', 'bin_m', 'bins': [centres], 'units': {prop: unit},
    'columns': {prop: [mean-or-None per bin]}, 'counts': {prop: [n per bin]}}.
    A bin row exists only where at least two properties are populated.
    """
    default_bin, props = WELL_GROUPS[well]
    bin_m = float(bin_m or default_bin)
    data, units = {}, {}
    lo, hi = math.inf, -math.inf
    for prop, (entry, channel) in props.items():
        samples, unit = _series(entry, channel)
        data[prop] = samples
        units[prop] = unit
        if samples:
            lo = min(lo, min(d for d, _ in samples))
            hi = max(hi, max(d for d, _ in samples))
    first = math.floor(lo / bin_m) * bin_m
    nbins = int(math.ceil((hi - first) / bin_m)) + 1
    sums = {p: [0.0] * nbins for p in props}
    counts = {p: [0] * nbins for p in props}
    for prop, samples in data.items():
        for d, v in samples:
            i = int((d - first) / bin_m)
            sums[prop][i] += v
            counts[prop][i] += 1
    keep = [i for i in range(nbins)
            if sum(1 for p in props if counts[p][i]) >= 2]
    return {
        "well": well, "bin_m": bin_m,
        "bins": [first + (i + 0.5) * bin_m for i in keep],
        "units": units,
        "columns": {p: [(sums[p][i] / counts[p][i]) if counts[p][i] else None
                        for i in keep] for p in props},
        "counts": {p: [counts[p][i] for i in keep] for p in props},
    }


def _colocated(well, a, b, bin_m=None):
    t = joint_table(well, bin_m)
    xa, xb = t["columns"][a], t["columns"][b]
    return [(x, y) for x, y in zip(xa, xb)
            if x is not None and y is not None], t["bin_m"]


def pair_stats(well, a, b, bin_m=None):
    """Co-located Pearson statistics for one property pair; refuses when thin."""
    pairs, used_bin = _colocated(well, a, b, bin_m)
    n = len(pairs)
    if n < MIN_PAIR_N:
        return {"status": "REFUSED_THIN_DATA", "well": well, "pair": (a, b),
                "n": n, "min_n": MIN_PAIR_N, "bin_m": used_bin}
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / (n - 1))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys) / (n - 1))
    if sx == 0.0 or sy == 0.0:
        return {"status": "REFUSED_ZERO_VARIANCE", "well": well,
                "pair": (a, b), "n": n, "bin_m": used_bin}
    r = (sum((x - mx) * (y - my) for x, y in pairs) / (n - 1)) / (sx * sy)
    return {"status": "OK", "well": well, "pair": (a, b), "n": n,
            "bin_m": used_bin, "pearson_r": r,
            "mean": {a: mx, b: my}, "std": {a: sx, b: sy},
            "range": {a: (min(xs), max(xs)), b: (min(ys), max(ys))}}


def conditional(well, target, given, value, k=7, bin_m=None):
    """Empirical estimate of `target` given `given` = value, from the k
    nearest co-located bins.  Honest output: estimate, spread, the support
    actually used, and an extrapolation flag; refuses when thin."""
    pairs, used_bin = _colocated(well, given, target, bin_m)
    if len(pairs) < MIN_PAIR_N:
        return {"status": "REFUSED_THIN_DATA", "well": well, "n": len(pairs),
                "min_n": MIN_PAIR_N, "bin_m": used_bin}
    value = float(value)
    lo = min(g for g, _ in pairs)
    hi = max(g for g, _ in pairs)
    near = sorted(pairs, key=lambda p: abs(p[0] - value))[:max(int(k), 1)]
    n = len(near)
    if n < MIN_COND_N:
        return {"status": "REFUSED_THIN_DATA", "well": well, "n": n,
                "min_n": MIN_COND_N, "bin_m": used_bin}
    tv = [t for _, t in near]
    est = sum(tv) / n
    spread = (math.sqrt(sum((t - est) ** 2 for t in tv) / (n - 1))
              if n > 1 else 0.0)
    return {"status": "OK", "well": well, "target": target,
            "given": {given: value}, "estimate": est, "std": spread, "n": n,
            "support": {given: (min(g for g, _ in near),
                                max(g for g, _ in near))},
            "extrapolation": not (lo <= value <= hi), "bin_m": used_bin}


def library_pairs(bin_m=None):
    """Every property pair in every well group, with co-located support.
    OK rows sorted by |r| descending; refused rows listed after, so thin
    joins stay visible instead of disappearing."""
    ok, refused = [], []
    for well, (_, props) in WELL_GROUPS.items():
        names = sorted(props)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                s = pair_stats(well, a, b, bin_m)
                (ok if s["status"] == "OK" else refused).append(s)
    ok.sort(key=lambda s: -abs(s["pearson_r"]))
    return {"ok": ok, "refused": refused,
            "wells": sorted(WELL_GROUPS), "n_ok": len(ok),
            "n_refused": len(refused)}
