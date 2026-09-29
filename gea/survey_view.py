"""survey_view - the first HONEST renderer for the surveying tool
(v1.80.0: 'one honest map/cross-section renderer would make everything built
this week visible for the first time').

Two figures, both drawn ONLY from gate-verified objects, both labeled with
what they are NOT:

  render_site_map(path)        - every Earth Model site on a lat/lon frame.
                                 Sites only - no basemap, no coastlines: the
                                 map shows exactly what the library has
                                 touched and nothing it has not.
  render_ktb_inversion(path)   - the KTB gravity inversion as a cross-section:
                                 implied density vs depth, layer-boundary
                                 candidates, and the Prediction-V2 Vp band
                                 with its honest spread, statused
                                 PINNED_AWAITING_DEEP_SONIC on the figure
                                 itself.

Headless by construction (matplotlib Agg); no display required. If matplotlib
is absent the functions raise a clear ImportError naming the optional
dependency - the renderer is presentation, never load-bearing: no catalogue
entry, gate pin, or acceptance check REQUIRES it to exist (the v0.406.0
red-gate lesson applied in advance).
"""

from __future__ import annotations

from typing import Optional


def _mpl():
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        return plt
    except ImportError as e:
        raise ImportError(
            "survey_view needs the optional 'matplotlib' package "
            "(pip install matplotlib). Rendering is presentation-only; "
            "nothing in the gate or acceptance suite requires it.") from e


def render_site_map(path: str) -> dict:
    """The library on one frame: every registered site, marked by kind of
    ground (multi-entry sites emphasized). Returns a summary dict."""
    plt = _mpl()
    from .earth_model import EarthModel
    em = EarthModel()
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ones, multis = [], []
    for s in em.sites.values():
        (multis if len(s.entries) > 1 else ones).append(s)
    ax.scatter([s.longitude for s in ones], [s.latitude for s in ones],
               s=28, c='#2b6cb0', label='site (single entry)', zorder=3)
    ax.scatter([s.longitude for s in multis], [s.latitude for s in multis],
               s=90, c='#c05621', marker='*',
               label='multi-entry site (reunited by coordinates)', zorder=4)
    for s in multis:
        ax.annotate('%d entries' % len(s.entries),
                    (s.longitude, s.latitude), fontsize=7,
                    xytext=(4, 4), textcoords='offset points')
    ax.set_xlim(-180, 180)
    ax.set_ylim(-90, 90)
    ax.axhline(0, lw=0.4, color='0.75')
    ax.axvline(0, lw=0.4, color='0.75')
    ax.grid(True, lw=0.3, alpha=0.5)
    ax.set_xlabel('longitude (deg)')
    ax.set_ylabel('latitude (deg)')
    cen = em.census()
    ax.set_title('GEA Surveying Tool - Earth Model: %d registered sites, '
                 '%.0f km great-circle span' % (cen['sites'],
                                                cen['great_circle_span_km']))
    ax.text(0.01, 0.02,
            'HONESTY: sites only - no basemap; every point is an '
            'archive-declared coordinate; %d entries remain unregistered '
            '(reasons on file)' % cen['unregistered_entries'],
            transform=ax.transAxes, fontsize=7, color='0.35')
    ax.legend(loc='lower right', fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {'sites_drawn': cen['sites'], 'multi_entry': len(multis),
            'path': path}


def render_ktb_inversion(path: str) -> dict:
    """The inverse engine's KTB read as a cross-section: implied density
    column, boundary candidates, and the Prediction-V2 band - honest spread
    and unsettled status printed on the figure."""
    plt = _mpl()
    from .inverse_engine import invert_gravity_column
    r = invert_gravity_column(prior_family='continental_crystalline')
    est = r['estimates']
    z = [e.depth_m for e in est]
    rho = [e.implied_density_gcc for e in est]
    ok = [e for e in est if e.posteriors['vp']['status'] == 'OK'
          and not e.posteriors['vp']['extrapolation']]
    vz = [e.depth_m for e in ok]
    vp = [e.posteriors['vp']['estimate'] for e in ok]
    vs = [e.posteriors['vp']['std'] for e in ok]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 8), sharey=True)
    a1.plot(rho, z, lw=0.8, color='#2b6cb0')
    for b in r['boundary_candidates']:
        a1.axhline(b['depth_m'], lw=0.6, color='#c05621', alpha=0.7)
    a1.invert_yaxis()
    a1.set_xlabel('implied density (g/cc)\nfrom measured gravity + GEA constants')
    a1.set_ylabel('depth (m)')
    a1.set_title('KTB implied density\n(%d intervals; %d boundary candidates)'
                 % (r['n_intervals'], len(r['boundary_candidates'])))
    a1.grid(True, lw=0.3, alpha=0.5)

    a2.plot(vp, vz, lw=0.9, color='#276749')
    a2.fill_betweenx(vz, [v - s for v, s in zip(vp, vs)],
                     [v + s for v, s in zip(vp, vs)],
                     color='#276749', alpha=0.18,
                     label='honest spread (+/- 1 sigma of prior support)')
    a2.set_xlabel('predicted Vp (m/s)\ncontinental_crystalline family prior')
    a2.set_title('Prediction V2 - Vp column\nSTATUS: PINNED_AWAITING_DEEP_SONIC')
    a2.grid(True, lw=0.3, alpha=0.5)
    a2.legend(loc='lower left', fontsize=7)
    fig.suptitle('GEA Surveying Tool - the inverse engine reads the KTB '
                 'gravity column (every value re-verified by the fidelity gate)',
                 fontsize=10)
    fig.text(0.5, 0.005,
             'HONESTY: prior = 19 site-native pairs from a 10 m window '
             '(entry 52); the previous prediction from a transferred prior '
             'was REFUTED (+10%) and is on the permanent record',
             ha='center', fontsize=7, color='0.35')
    fig.tight_layout(rect=(0, 0.02, 1, 0.97))
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {'intervals_drawn': r['n_intervals'], 'vp_points': len(ok),
            'path': path}
