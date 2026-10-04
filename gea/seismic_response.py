# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic_response - the instrument response: from counts to ground motion, with the station's own file.

A seismometer's record is counts; the station's response - the FDSN StationXML
the archives serve beside the data - says how many counts a metre per second
of ground motion made, at every frequency. This module reads that file, builds
the response from its stages the standard way (SEED manual, chapter 6, as
evalresp does it: poles and zeros of the analog stage, the gain of each stage,
the FIR and IIR coefficient stages of the digitizer), and removes it from a
record in the frequency domain with a water level, giving velocity,
displacement or acceleration in SI units.

What it does not do: invent a response. A channel with no stages, a polynomial
(non-linear) response, or a response whose stages' product disagrees with the
declared sensitivity by more than a few percent is reported, not patched. A
record without a matching response stays in counts, labelled as such.

    gea seismic --action response --stationxml station.xml
    gea seismic --action remove-response --file tx.mseed --stationxml station.xml --output VEL --out tx_vel.mseed
"""

from __future__ import annotations

import io
import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Sequence, Tuple, Union

import numpy as np

from .seismic import Trace, iso, parse_time

NS = '{http://www.fdsn.org/xml/station/1}'
UNITS_SI = {'M/S': 'VEL', 'M/S**2': 'ACC', 'M': 'DISP', 'M/S/S': 'ACC', 'M/S^2': 'ACC'}
OUTPUT_UNITS = {'VEL': 'm/s', 'ACC': 'm/s^2', 'DISP': 'm'}


@dataclass
class Stage:
    number: int
    kind: str                                   # PZ | COEF | FIR | POLY | GAIN
    input_units: str = ''
    output_units: str = ''
    gain: float = 1.0
    gain_frequency: float = 0.0
    pz_type: str = ''                           # LAPLACE (RADIANS/SECOND) | LAPLACE (HERTZ) | DIGITAL (Z-TRANSFORM)
    a0: float = 1.0
    a0_frequency: float = 0.0
    zeros: List[complex] = field(default_factory=list)
    poles: List[complex] = field(default_factory=list)
    numerators: List[float] = field(default_factory=list)
    denominators: List[float] = field(default_factory=list)
    fir_symmetry: str = 'NONE'
    input_sample_rate: float = 0.0
    decimation_factor: int = 1
    delay: float = 0.0
    correction: float = 0.0


@dataclass
class ChannelResponse:
    network: str
    station: str
    location: str
    channel: str
    start: Optional[float]
    end: Optional[float]
    latitude: Optional[float]
    longitude: Optional[float]
    elevation: Optional[float]
    depth: Optional[float]
    azimuth: Optional[float]
    dip: Optional[float]
    sample_rate: Optional[float]
    sensor: str
    sensitivity: Optional[float]
    sensitivity_frequency: Optional[float]
    input_units: str
    output_units: str
    stages: List[Stage]
    source: str = ''

    @property
    def id(self) -> str:
        return f"{self.network}.{self.station}.{self.location}.{self.channel}"

    def covers(self, t: float) -> bool:
        return (self.start is None or t >= self.start) and (self.end is None or t <= self.end)

    def summary(self) -> dict:
        return {'id': self.id, 'start': iso(self.start, 0) if self.start else None, 'end': iso(self.end, 0) if self.end else None,
                'latitude': self.latitude, 'longitude': self.longitude, 'elevation_m': self.elevation, 'depth_m': self.depth,
                'azimuth': self.azimuth, 'dip': self.dip, 'sample_rate_hz': self.sample_rate, 'sensor': self.sensor,
                'sensitivity': self.sensitivity, 'sensitivity_frequency_hz': self.sensitivity_frequency,
                'input_units': self.input_units, 'output_units': self.output_units,
                'stages': [f"{s.number}:{s.kind}" + (f"({len(s.poles)}p/{len(s.zeros)}z)" if s.kind == 'PZ' else (f"({len(s.numerators)} coef)" if s.kind in ('COEF', 'FIR') else '')) for s in self.stages],
                'source': self.source}


# ---------------------------------------------------------------------------
# StationXML
# ---------------------------------------------------------------------------
def _text(el, tag, default=None, cast=float):
    e = el.find(NS + tag) if el is not None else None
    if e is None or e.text is None or not e.text.strip():
        return default
    try:
        return cast(e.text.strip())
    except ValueError:
        return default


def _units(el, tag) -> str:
    e = el.find(NS + tag) if el is not None else None
    return (_text(e, 'Name', '', str) or '').upper()


def _parse_stage(st) -> Stage:
    number = int(st.get('number', '0'))
    gain_el = st.find(NS + 'StageGain')
    gain = _text(gain_el, 'Value', 1.0)
    gain_f = _text(gain_el, 'Frequency', 0.0)
    pz = st.find(NS + 'PolesZeros')
    co = st.find(NS + 'Coefficients')
    fir = st.find(NS + 'FIR')
    poly = st.find(NS + 'Polynomial')
    dec = st.find(NS + 'Decimation')
    s = Stage(number=number, kind='GAIN', gain=gain, gain_frequency=gain_f)
    if dec is not None:
        s.input_sample_rate = _text(dec, 'InputSampleRate', 0.0)
        s.decimation_factor = int(_text(dec, 'Factor', 1, float))
        s.delay = _text(dec, 'Delay', 0.0)
        s.correction = _text(dec, 'Correction', 0.0)
    if pz is not None:
        s.kind = 'PZ'
        s.input_units, s.output_units = _units(pz, 'InputUnits'), _units(pz, 'OutputUnits')
        s.pz_type = (_text(pz, 'PzTransferFunctionType', '', str) or '').upper()
        s.a0 = _text(pz, 'NormalizationFactor', 1.0)
        s.a0_frequency = _text(pz, 'NormalizationFrequency', 0.0)
        s.zeros = [complex(_text(z, 'Real', 0.0), _text(z, 'Imaginary', 0.0)) for z in pz.findall(NS + 'Zero')]
        s.poles = [complex(_text(p, 'Real', 0.0), _text(p, 'Imaginary', 0.0)) for p in pz.findall(NS + 'Pole')]
    elif co is not None:
        s.kind = 'COEF'
        s.input_units, s.output_units = _units(co, 'InputUnits'), _units(co, 'OutputUnits')
        s.numerators = [float(n.text) for n in co.findall(NS + 'Numerator') if n.text]
        s.denominators = [float(n.text) for n in co.findall(NS + 'Denominator') if n.text]
    elif fir is not None:
        s.kind = 'FIR'
        s.input_units, s.output_units = _units(fir, 'InputUnits'), _units(fir, 'OutputUnits')
        s.fir_symmetry = (_text(fir, 'Symmetry', 'NONE', str) or 'NONE').upper()
        s.numerators = [float(n.text) for n in fir.findall(NS + 'NumeratorCoefficient') if n.text]
    elif poly is not None:
        s.kind = 'POLY'
        s.input_units, s.output_units = _units(poly, 'InputUnits'), _units(poly, 'OutputUnits')
    return s


def read_stationxml(src: Union[str, bytes]) -> List[ChannelResponse]:
    """Every channel of an FDSN StationXML document (a path, or the bytes a station service returned)."""
    if isinstance(src, (bytes, bytearray)):
        root = ET.fromstring(bytes(src))
        name = '<bytes>'
    else:
        root = ET.parse(src).getroot()
        name = os.fspath(src)
    out: List[ChannelResponse] = []
    for net in root.iter(NS + 'Network'):
        ncode = net.get('code', '')
        for sta in net.findall(NS + 'Station'):
            scode = sta.get('code', '')
            for ch in sta.findall(NS + 'Channel'):
                resp = ch.find(NS + 'Response')
                sens = resp.find(NS + 'InstrumentSensitivity') if resp is not None else None
                stages = sorted((_parse_stage(st) for st in (resp.findall(NS + 'Stage') if resp is not None else [])), key=lambda s: s.number)
                out.append(ChannelResponse(
                    network=ncode, station=scode, location=ch.get('locationCode', '') or '', channel=ch.get('code', ''),
                    start=parse_time(ch.get('startDate')) if ch.get('startDate') else None,
                    end=parse_time(ch.get('endDate')) if ch.get('endDate') else None,
                    latitude=_text(ch, 'Latitude'), longitude=_text(ch, 'Longitude'), elevation=_text(ch, 'Elevation'), depth=_text(ch, 'Depth'),
                    azimuth=_text(ch, 'Azimuth'), dip=_text(ch, 'Dip'), sample_rate=_text(ch, 'SampleRate'),
                    sensor=(_text(ch.find(NS + 'Sensor'), 'Description', '', str) or _text(ch.find(NS + 'Sensor'), 'Type', '', str) or ''),
                    sensitivity=_text(sens, 'Value'), sensitivity_frequency=_text(sens, 'Frequency'),
                    input_units=_units(sens, 'InputUnits'), output_units=_units(sens, 'OutputUnits'),
                    stages=stages, source=name))
    return out


def select(responses: Sequence[ChannelResponse], tr: Trace) -> ChannelResponse:
    """The response of a trace's channel, in force at the trace's start; raises with the list when there is none."""
    cands = [r for r in responses if (r.network, r.station, r.location, r.channel) == (tr.network, tr.station, tr.location, tr.channel)]
    live = [r for r in cands if r.covers(tr.starttime)]
    if live:
        return live[0]
    have = sorted({r.id for r in responses})
    if cands:
        raise LookupError(f"{tr.id}: the station file has this channel but no epoch covering {iso(tr.starttime, 0)}: "
                          + ', '.join(f"{iso(r.start, 0) if r.start else '..'}..{iso(r.end, 0) if r.end else '..'}" for r in cands))
    raise LookupError(f"{tr.id}: not in the station file (it has {', '.join(have) if have else 'no channels'})")


# ---------------------------------------------------------------------------
# the response at a set of frequencies
# ---------------------------------------------------------------------------
def stage_transfer(s: Stage, freqs: np.ndarray) -> np.ndarray:
    """One stage's transfer function (gain included) at the frequencies, as the SEED manual defines each kind."""
    f = np.asarray(freqs, dtype=float)
    h = np.ones_like(f, dtype=complex)
    if s.kind == 'PZ':
        if 'HERTZ' in s.pz_type:
            x = 1j * f                                               # LAPLACE (HERTZ): the poles and zeros are in Hz
        elif 'DIGITAL' in s.pz_type or 'Z-TRANSFORM' in s.pz_type:
            fs = s.input_sample_rate or 1.0
            x = np.exp(1j * 2 * np.pi * f / fs)                     # DIGITAL (Z-TRANSFORM), as evalresp evaluates it
        else:
            x = 2j * np.pi * f                                       # LAPLACE (RADIANS/SECOND)
        num = np.ones_like(x)
        for z in s.zeros:
            num = num * (x - z)
        den = np.ones_like(x)
        for p in s.poles:
            den = den * (x - p)
        with np.errstate(divide='ignore', invalid='ignore'):
            h = s.a0 * num / den
        h = np.where(np.isfinite(h), h, 0.0)
    elif s.kind in ('COEF', 'FIR'):
        b = list(s.numerators)
        if s.kind == 'FIR' and s.fir_symmetry == 'ODD':
            b = b + b[-2::-1]
        elif s.kind == 'FIR' and s.fir_symmetry == 'EVEN':
            b = b + b[::-1]
        fs = s.input_sample_rate
        if b and fs > 0:
            k = np.arange(len(b))
            w = -2j * np.pi * np.outer(f, k) / fs
            h = np.exp(w) @ np.asarray(b, dtype=float)
            if s.denominators:
                a = np.asarray(s.denominators, dtype=float)
                ka = np.arange(len(a))
                h = h / (np.exp(-2j * np.pi * np.outer(f, ka) / fs) @ a)
            if s.correction:
                h = h * np.exp(2j * np.pi * f * s.correction)      # the stage's declared delay correction, as evalresp applies it
    elif s.kind == 'POLY':
        raise ValueError(f"stage {s.number} is a polynomial (non-linear) response; it cannot be removed by division")
    return h * s.gain


def transfer(chan: ChannelResponse, freqs: np.ndarray, output: str = 'VEL') -> Tuple[np.ndarray, dict]:
    """Counts per unit of ground motion at the frequencies, for VEL (m/s), DISP (m) or ACC (m/s^2),
    with the check every evaluation carries: the stages' product against the declared sensitivity."""
    f = np.asarray(freqs, dtype=float)
    if not chan.stages:
        raise ValueError(f"{chan.id}: the station file has no response stages for this channel")
    h = np.ones_like(f, dtype=complex)
    for s in chan.stages:
        h = h * stage_transfer(s, f)
    note = {}
    if chan.sensitivity and chan.sensitivity_frequency:
        hs = np.ones(1, dtype=complex)
        for s in chan.stages:
            hs = hs * stage_transfer(s, np.array([chan.sensitivity_frequency]))
        ratio = float(abs(hs[0])) / chan.sensitivity
        note = {'stages_vs_sensitivity': round(ratio, 4), 'sensitivity_frequency_hz': chan.sensitivity_frequency}
        if not (0.95 <= ratio <= 1.05):
            raise ValueError(f"{chan.id}: the product of the stages at {chan.sensitivity_frequency} Hz is {ratio:.3f} x the declared sensitivity; "
                             "the station file is inconsistent and the response is not applied")
    unit_in = UNITS_SI.get(chan.input_units.upper())
    if unit_in is None:
        raise ValueError(f"{chan.id}: input units {chan.input_units!r} are not a ground-motion unit (M/S, M/S**2, M)")
    output = output.upper()
    if output not in OUTPUT_UNITS:
        raise ValueError("output must be VEL, ACC or DISP")
    order = {'DISP': 0, 'VEL': 1, 'ACC': 2}
    n = order[output] - order[unit_in]             # derivatives from the instrument's unit to the requested one
    with np.errstate(divide='ignore', invalid='ignore'):
        iw = 2j * np.pi * f
        if n > 0:
            h = h / np.power(iw, n)                  # counts per (m/s^2) = counts per (m/s) / (iw)
        elif n < 0:
            h = h * np.power(iw, -n)
    h = np.where(np.isfinite(h), h, 0.0)
    note['output'] = output
    note['unit'] = OUTPUT_UNITS[output]
    return h, note


def _cosine_window(f: np.ndarray, f1: float, f2: float, f3: float, f4: float) -> np.ndarray:
    w = np.zeros_like(f)
    m = (f >= f2) & (f <= f3)
    w[m] = 1.0
    m = (f > f1) & (f < f2)
    w[m] = 0.5 * (1 - np.cos(np.pi * (f[m] - f1) / (f2 - f1)))
    m = (f > f3) & (f < f4)
    w[m] = 0.5 * (1 + np.cos(np.pi * (f[m] - f3) / (f4 - f3)))
    return w


def remove_response(tr: Trace, chan: ChannelResponse, output: str = 'VEL', water_level_db: float = 60.0,
                    pre_filt: Optional[Tuple[float, float, float, float]] = None, taper_fraction: float = 0.05) -> Tuple[Trace, dict]:
    """Counts -> ground motion. Demean, detrend, cosine taper, FFT, optional cosine band window, division by the
    response with a water level (the response is never let below max|H| x 10^(-wl/20), so the noise floor of
    the instrument's dead band is not amplified into the result), inverse FFT. The standard deconvolution."""
    x = np.asarray(tr.data, dtype=np.float64)
    n = len(x)
    if n < 16:
        raise ValueError("too few samples to remove a response")
    t = np.arange(n, dtype=float)
    a, b = np.polyfit(t, x, 1)
    x = x - (a * t + b)
    k = int(max(1, round(taper_fraction * n)))
    if k > 0:
        tp = np.ones(n)
        ramp = 0.5 * (1 - np.cos(np.pi * np.arange(k) / k))
        tp[:k] = ramp
        tp[-k:] = ramp[::-1]
        x = x * tp
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    spec = np.fft.rfft(x, nfft)
    f = np.fft.rfftfreq(nfft, d=1.0 / tr.sample_rate)
    h, note = transfer(chan, f, output)
    if pre_filt:
        spec = spec * _cosine_window(f, *pre_filt)
    amp = np.abs(h)
    floor = amp.max() * 10.0 ** (-water_level_db / 20.0)
    hw = h.copy()
    low = amp < floor
    hw[low & (amp > 0)] = h[low & (amp > 0)] * (floor / amp[low & (amp > 0)])
    hw[amp == 0] = floor
    spec = spec / hw
    y = np.fft.irfft(spec, nfft)[:n]
    out = Trace(tr.network, tr.station, tr.location, tr.channel, tr.starttime, tr.sample_rate, y, source=tr.source, encoding='RESPONSE_REMOVED',
                gaps=list(tr.gaps), unit=OUTPUT_UNITS[output.upper()])
    note.update({'water_level_db': water_level_db, 'pre_filt_hz': list(pre_filt) if pre_filt else None, 'taper_fraction': taper_fraction,
                 'response_source': chan.source, 'response_id': chan.id, 'sensor': chan.sensor, 'nfft': nfft,
                 'basis': 'frequency-domain division by the station response built from its StationXML stages, with a water level; '
                          'demeaned, detrended and cosine-tapered first'})
    return out, note


def fdsn_stationxml(base: str, network: str, station: str, channel: str, start, end, out: str, location: str = '*', timeout: float = 60.0) -> dict:
    """Fetch the StationXML with responses (level=response) for a channel and time, to `out`."""
    import json
    import urllib.request
    from .seismic import fdsn_url
    url = fdsn_url(base, 'station', network=network, station=station, location=location or '*', channel=channel, level='response',
                   starttime=iso(parse_time(start), 0)[:-1], endtime=iso(parse_time(end), 0)[:-1], nodata='404')
    req = urllib.request.Request(url, headers={'User-Agent': 'gea-program seismic ingest'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    with open(out, 'wb') as fh:
        fh.write(data)
    rec = {'url': url, 'bytes': len(data), 'file': os.path.basename(out), 'channels': [c.id for c in read_stationxml(data)]}
    with open(out + '.request.json', 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, indent=1)
    return rec
