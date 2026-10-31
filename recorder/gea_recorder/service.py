# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""service - the box running: the source read block by block on one thread, every block handed to the writer,
the rig feed on its own thread, the status page on its own; the clock asked every ten seconds what its time is
worth, and that written into every record. Started by systemd at boot (gea-recorder.service), stopped by it at
shutdown, which closes every file and writes the manifest. A source that fails is reopened after five seconds and
the page says so; nothing else stops the box but the disk filling, and then it stops recording and says so."""
from __future__ import annotations

import os
import signal
import threading
import time
import traceback
from typing import Optional

from . import NAME, __version__, clock, rig as _rig, sources, status as _status, writer as _writer


class Recorder:
    def __init__(self, cfg: dict, realtime: bool = True, seed: int = 1):
        self.cfg = cfg
        self.realtime = realtime
        self.seed = seed
        self.writer = _writer.Writer(cfg)
        self.source: Optional[sources.Source] = None
        self.rig = _rig.RigLogger(cfg, cfg['output']['dir']) if (cfg.get('rig') or {}).get('enabled') else None
        self.status_server = _status.StatusServer(cfg, self.health, cfg['output']['dir'])
        self.t_started = time.time()
        self.clock_state = clock.status(str((cfg.get('clock') or {}).get('source', 'chrony')))
        self.clock_checked = time.monotonic()
        self.state = 'starting'
        self.recording = False
        self.source_error: Optional[str] = None
        self.blocks = 0
        self.samples = 0
        self._stop = threading.Event()
        self.acq_thread: Optional[threading.Thread] = None

    # -- what the page shows --------------------------------------------------------------------------------------------
    def health(self) -> dict:
        return {'program': NAME, 'version': __version__, 'site': self.cfg['site'], 'station': self.cfg['station'], 'recording': self.recording, 'state': self.state,
                'source': self.source.describe() if self.source else self.cfg['adc']['driver'], 'source_error': self.source_error, 'clock': self.clock_state,
                'uptime_s': time.time() - self.t_started, 'blocks': self.blocks, 'samples': self.samples, 'channels': self.writer.levels(),
                'rig': self.rig.status() if self.rig else {'enabled': False, 'connected': False, 'frames': 0, 'last': None, 'error': None, 'transport': None},
                'free_mb': round(self.writer.free_mb()), 'n_files': len(self.writer.closed), 'bytes_closed': sum(f['bytes'] for f in self.writer.closed),
                'mirror': self.writer.mirror if self.writer.mirror and os.path.isdir(self.writer.mirror) else None, 'stopped_reason': self.writer.stopped_reason}

    # -- the loop ----------------------------------------------------------------------------------------------------------
    def _acquire(self) -> None:
        while not self._stop.is_set():
            try:
                self.source = sources.make(self.cfg, realtime=self.realtime, seed=self.seed)
                self.source.open()
                self.source_error = None
                self.state = f'recording: {self.source.describe()}'
                self.recording = True
                while not self._stop.is_set():
                    block, t_first = self.source.read()
                    if time.monotonic() - self.clock_checked > 10.0:
                        self.clock_state = clock.status(str((self.cfg.get('clock') or {}).get('source', 'chrony')))
                        self.clock_checked = time.monotonic()
                    self.writer.add_block(block, t_first, int(self.clock_state['quality']))
                    self.blocks += 1; self.samples += int(block.shape[1])
                    if self.writer.stopped_reason:
                        self.state = self.writer.stopped_reason; self.recording = False
                    elif not self.recording:
                        self.state = f'recording: {self.source.describe()}'; self.recording = True
            except EOFError:
                self.state = 'the replay is finished'; self.recording = False
                return
            except Exception as e:
                self.source_error = f'{type(e).__name__}: {e}'
                self.state = f'the source failed: {self.source_error}; trying again in 5 s'
                self.recording = False
                try:
                    if self.source:
                        self.source.close()
                except Exception:
                    pass
                if self._stop.wait(5.0):
                    return

    def start(self) -> None:
        self.status_server.start()
        if self.rig:
            self.rig.start()
        self.acq_thread = threading.Thread(target=self._acquire, name='acquire', daemon=True)
        self.acq_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.acq_thread:
            self.acq_thread.join(timeout=10)
        try:
            if self.source:
                self.source.close()
        except Exception:
            pass
        self.writer.stop(int(self.clock_state['quality']))
        if self.rig:
            self.rig.stop()
        self.status_server.stop()
        self.state = 'stopped'; self.recording = False

    def run_forever(self) -> int:
        """Until SIGTERM or SIGINT (systemd's stop, or Ctrl+C on a bench)."""
        done = threading.Event()

        def _sig(*a):
            done.set()
        for s in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(s, _sig)
            except (ValueError, OSError):
                pass
        self.start()
        try:
            while not done.is_set():
                done.wait(1.0)
                if self.acq_thread and not self.acq_thread.is_alive() and self.state.startswith('the replay'):
                    break
        finally:
            self.stop()
        return 0


def run_for(cfg: dict, seconds: float, realtime: bool = False, seed: int = 1) -> dict:
    """A bench run of a stated length (the self-test): start, record, stop; the health at the end."""
    r = Recorder(cfg, realtime=realtime, seed=seed)
    r.start()
    t_end = time.monotonic() + seconds
    try:
        if realtime:
            while time.monotonic() < t_end:
                time.sleep(0.2)
        else:
            # free-running: wait until the writer has the requested span of samples
            need = seconds * float(cfg['adc']['sample_rate'])
            while r.samples < need and time.monotonic() < t_end + 60:
                time.sleep(0.05)
    finally:
        r.stop()
    return r.health()
