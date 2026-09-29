"""gea.shell - the desktop window (desktop extra): Wells | Reports | Geology.

Everything the window shows is the same machinery the command line uses;
the window never computes anything of its own. Wells browses the catalogue
with each entry's provenance; Reports runs the report family for a selected
well into a folder and opens the dashboard; Geology is the operator surface
from the survey track when its renderers are installed.
"""
from __future__ import annotations

import os
import sys
import webbrowser


def _require_qt() -> bool:
    try:
        from PyQt6 import QtWidgets  # noqa: F401
        return True
    except ImportError:
        return False


def main() -> int:
    if not _require_qt():
        print('gea gui needs PyQt6:  pip install "gea-program[desktop]"')
        return 3
    from PyQt6.QtWidgets import (QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
                                 QListWidget, QTextEdit, QPushButton, QLabel, QFileDialog, QLineEdit)
    from . import __version__
    from .profile_catalog import CATALOG

    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle(f'GEA {__version__} - downhole gauge monitoring')
    tabs = QTabWidget()

    # ---- Wells: the catalogue with provenance ----
    wells = QWidget(); wl = QHBoxLayout(wells)
    wlist = QListWidget(); wlist.addItems(sorted(CATALOG))
    wdetail = QTextEdit(); wdetail.setReadOnly(True)

    def show_well(item):
        e = CATALOG[item.text()]
        wdetail.setPlainText('\n'.join(f'{k}: {v}' for k, v in sorted(e.provenance.items())))
    wlist.itemClicked.connect(show_well)
    wl.addWidget(wlist, 1); wl.addWidget(wdetail, 2)
    tabs.addTab(wells, 'Wells')

    # ---- Reports: run the family for a well and open the dashboard ----
    rep = QWidget(); rl = QVBoxLayout(rep)
    info = QLabel('Production wells in the catalogue carry downhole pressure; give the well tag and the gauge station depth (ft).')
    entry = QLineEdit('volve_f12_f14_production_excerpt'); entry.setPlaceholderText('catalogue entry')
    tag = QLineEdit('15/9-F-12'); tag.setPlaceholderText('well tag inside the entry')
    md = QLineEdit('10000'); md.setPlaceholderText('station MD, ft (from completion records)')
    td = QLineEdit('10500'); td.setPlaceholderText('total depth, ft')
    hist = QLineEdit(); hist.setPlaceholderText('optional: historian CSV for alarms and resilience')
    pick = QPushButton('Choose historian CSV...')
    run = QPushButton('Generate reports and open the dashboard')
    out = QTextEdit(); out.setReadOnly(True)

    def choose():
        p, _ = QFileDialog.getOpenFileName(win, 'Historian CSV', '', 'CSV (*.csv)')
        if p:
            hist.setText(p)

    def do_run():
        from .__main__ import main as sub
        folder = QFileDialog.getExistingDirectory(win, 'Output folder for the reports')
        if not folder:
            return
        args = ['dashboard', '--catalog-well', f'{entry.text()}:{tag.text()}:{md.text()}', '--td', td.text(),
                '--name', tag.text(), '--out', folder]
        if hist.text():
            args += ['--file', hist.text()]
        out.setPlainText('running: gea ' + ' '.join(args)); app.processEvents()
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = sub(args)
        out.setPlainText(buf.getvalue() + f'\nexit {rc}')
        idx = os.path.join(folder, 'index.html')
        if rc == 0 and os.path.exists(idx):
            webbrowser.open('file://' + os.path.abspath(idx))
    pick.clicked.connect(choose); run.clicked.connect(do_run)
    for w in (info, entry, tag, md, td, hist, pick, run, out):
        rl.addWidget(w)
    tabs.addTab(rep, 'Reports')

    # ---- Geology: the survey-track operator surface, when its renderers exist ----
    try:
        from .project import build_geology_tab
        tabs.addTab(build_geology_tab(), 'Geology')
    except Exception:
        pass

    win.setCentralWidget(tabs)
    win.resize(1100, 700)
    win.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
