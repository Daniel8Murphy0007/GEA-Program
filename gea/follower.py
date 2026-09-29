"""follower — the quasi-live file-follower port (v1.10.0 extension).

Addresses the connectivity concern (Daniel, 2026-08-24) at the tier that is
buildable TODAY with zero network code and zero invented site behavior: most
historians can be configured to continuously append-export their readings to
a file. `HistorianFollower` watches that growing file, re-ingests it on each
poll, and reports how many NEW samples arrived — near-real-time monitoring
through the same read-only port machinery, feeding the same reconciler.

Design:
  * READ-ONLY, like every port. The follower never touches the source file
    beyond opening it for reading.
  * RE-INGEST, not tail-parse. Each poll re-reads the whole file through
    `read_historian_csv` and diffs by sample count. This is deliberately
    simple and robust: partial trailing lines, historian rewrites, and file
    rotation (row count DROPS -> follower resets and says so) are all handled
    by construction rather than by fragile incremental parsing. Historian
    exports at 1 reading/min are small; re-reading them is cheap.
  * NO SLEEP LOOPS inside the library. `poll()` is a single step the caller
    schedules (cron, scheduler, or the `watch()` convenience generator which
    sleeps between polls only when the caller iterates it).

Connectivity tiers after this module (the honest ladder):
  SIMULATE (v1.3.0)  -> OFFLINE-INGEST real exports (v1.8.0)
  -> FILE-FOLLOW quasi-live (THIS)  -> LIVE PROTOCOL (declared, refusing
  until site details; Modbus client pending Daniel's dependency ruling).

Headless-safe: numpy only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from .ports import LiveStream, read_historian_csv


@dataclass
class FollowerPoll:
    """One poll result: the full current stream + what changed."""
    stream: Optional[LiveStream]
    total_samples: int
    new_samples: int
    rotated: bool            # True when the file shrank (rotation/rewrite) and the follower reset
    error: Optional[str] = None


class HistorianFollower:
    """Follows a continuously-appended historian CSV export (read-only)."""

    def __init__(self, path, reconciler=None):
        self.path = Path(path)
        self.reconciler = reconciler          # optional: a Reconciler to run each poll
        self._seen = 0

    def poll(self) -> FollowerPoll:
        """One follow step: re-ingest, diff by sample count, optionally
        reconcile. Missing/unreadable file is a soft error (sites glitch)."""
        try:
            stream = read_historian_csv(self.path)
        except (OSError, ValueError) as e:
            return FollowerPoll(stream=None, total_samples=self._seen,
                                new_samples=0, rotated=False, error=str(e)[:120])
        n = int(len(stream.index))
        rotated = n < self._seen
        new = n if rotated else n - self._seen
        self._seen = n
        return FollowerPoll(stream=stream, total_samples=n,
                            new_samples=int(new), rotated=bool(rotated))

    def poll_and_reconcile(self) -> dict:
        """Poll, and when new samples arrived and a reconciler is attached,
        run the two-stream reconciliation on the CURRENT full stream."""
        p = self.poll()
        out = {'total_samples': p.total_samples, 'new_samples': p.new_samples,
               'rotated': p.rotated, 'error': p.error, 'reconciliation': None}
        if p.stream is not None and p.new_samples > 0 and self.reconciler is not None:
            out['reconciliation'] = self.reconciler.reconcile(p.stream)
        return out

    def watch(self, interval_s: float = 60.0,
              max_polls: Optional[int] = None) -> Iterator[FollowerPoll]:
        """Convenience generator: poll forever (or max_polls) at interval_s.
        The sleep lives HERE, in the caller's iteration - the library itself
        never blocks. Default cadence matches the 1 reading/min telemetry
        class."""
        k = 0
        while max_polls is None or k < max_polls:
            yield self.poll()
            k += 1
            if max_polls is None or k < max_polls:
                time.sleep(interval_s)
