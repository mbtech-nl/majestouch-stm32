#!/usr/bin/env python3
"""Build the fabrication package from the board.

  python3 fab.py            -> kicad/fab/
  FAB_REV=v0.1 python3 fab.py   name the zip after a release (CI does this)
  FAB_OUT=/tmp/fab python3 fab.py   write somewhere else

Read-only on the KiCad files; everything goes into kicad/fab/:
  gerbers/                         Gerbers + Excellon drill (+ drill map)
  majestouch_stm32-gerbers-<rev>.zip  upload this to the PCB fab
  bom.csv                          parts list: part used, and what an equivalent needs
  cpl.csv                          pick-and-place positions (only for machine assembly)

Plain Gerber (RS-274X) and Excellon that any fab takes; the settings follow
JLCPCB's KiCad guide: Protel extensions, solder mask subtracted from
silkscreen, no X2/netlist attributes, Excellon in mm with decimal zeros,
absolute origin, PTH and NPTH in separate files.
"""
import collections
import csv
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
PROJECT = 'majestouch_stm32'
BOARD = os.path.join(ROOT, 'kicad', PROJECT + '.kicad_pcb')
OUT = os.environ.get('FAB_OUT') or os.path.join(ROOT, 'kicad', 'fab')
LAYERS = ['F.Cu', 'B.Cu', 'F.Paste', 'B.Paste', 'F.SilkS', 'B.SilkS', 'F.Mask', 'B.Mask',
          'Edge.Cuts']

# value -> (manufacturer, part number, description). Any equivalent part works:
# the description says what matters. These are the parts used for the first build.
PARTS = {
    'STM32F072CBT6': ('ST', 'STM32F072CBT6', 'MCU, LQFP-48'),
    'USBLC6-2SC6': ('ST', 'USBLC6-2SC6', 'USB ESD protection, SOT-23-6'),
    'AP2112K-3.3': ('Diodes Inc.', 'AP2112K-3.3TRG1', '3.3 V 600 mA LDO, SOT-23-5'),
    '100nF': ('YAGEO', 'CC0603KRX7R9BB104', 'MLCC 100 nF, X7R, >= 10 V, 0603'),
    '1uF': ('YAGEO', 'CC0603KRX5R8BB105', 'MLCC 1 uF, X5R/X7R, >= 10 V, 0603'),
    '10nF': ('Fenghua', '0603B103K500NT', 'MLCC 10 nF, X7R, >= 10 V, 0603'),
    '10k': ('YAGEO', 'RC0603FR-0710KL', 'resistor 10 k, 1 % or 5 %, 0603'),
    '4.7uF': ('Samsung', 'CL21A475KAQNNNE', 'MLCC 4.7 uF, X5R/X7R, >= 10 V, 0805'),
    '10uF': ('Samsung', 'CL21A106KAYNNNE', 'MLCC 10 uF, X5R/X7R, >= 10 V, 0805'),
    'Filco 1-18': ('any', '', 'pin header 2.0 mm pitch, 1x40 breakaway: snap into 2x 18 for J1 + J2'),
    'Filco 19-36': ('any', '', 'same strip as J1'),
    'BOOT': ('any', '', 'pin header 2.54 mm, 1x2, optional (jumper cap or tweezers)'),
    'RESET': ('any', '', 'pin header 2.54 mm, 1x2, optional (jumper cap or tweezers)'),
}


def run(*cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f'{" ".join(cmd)} failed:\n{r.stdout}{r.stderr}')
    return r.stdout


def git_rev():
    if os.environ.get('FAB_REV'):          # set by CI to the release tag
        return os.environ['FAB_REV']
    try:
        rev = run('git', '-c', 'safe.directory=*', '-C', ROOT, 'describe', '--tags', '--always', '--dirty')
        return rev.strip()
    except SystemExit:
        return 'nogit'


def board_info():
    import pcbnew
    b = pcbnew.LoadBoard(BOARD)  # never saved
    box = b.GetBoardEdgesBoundingBox()
    fps = []
    for fp in b.GetFootprints():
        fps.append({'ref': fp.GetReference(), 'value': fp.GetValue(),
                    'footprint': str(fp.GetFPID().GetLibItemName()),
                    'side': 'bottom' if fp.GetLayer() == pcbnew.B_Cu else 'top'})
    return pcbnew.ToMM(box.GetWidth()), pcbnew.ToMM(box.GetHeight()), fps


def main():
    rev = git_rev()
    if rev.endswith('-dirty'):
        print(f'warning: uncommitted changes ({rev}); the package will not match a commit')
    shutil.rmtree(OUT, ignore_errors=True)
    gdir = os.path.join(OUT, 'gerbers')
    os.makedirs(gdir)

    run('kicad-cli', 'pcb', 'export', 'gerbers', '-o', gdir + os.sep, '--layers', ','.join(LAYERS),
        '--subtract-soldermask', '--no-x2', '--no-netlist', BOARD)
    run('kicad-cli', 'pcb', 'export', 'drill', '-o', gdir + os.sep, '--format', 'excellon',
        '--excellon-units', 'mm', '--excellon-zeros-format', 'decimal',
        '--excellon-oval-format', 'alternate', '--drill-origin', 'absolute',
        '--excellon-separate-th', '--generate-map', '--map-format', 'gerberx2', BOARD)

    files = sorted(os.listdir(gdir))
    zpath = os.path.join(OUT, f'{PROJECT}-gerbers-{rev}.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(os.path.join(gdir, f), f)

    w, h, fps = board_info()

    # BOM: one line per value/footprint, with the part used and what an equivalent needs
    groups = collections.OrderedDict()
    for fp in sorted(fps, key=lambda f: (f['value'], f['footprint'], f['ref'])):
        groups.setdefault((fp['value'], fp['footprint']), []).append(fp['ref'])
    with open(os.path.join(OUT, 'bom.csv'), 'w', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['Designator', 'Qty', 'Value', 'Footprint', 'Manufacturer', 'Part number', 'Description'])
        for (value, footprint), refs in groups.items():
            maker, mpn, desc = PARTS.get(value, ('', '', 'not looked up'))
            wr.writerow([','.join(sorted(refs, key=lambda r: (r.rstrip('0123456789'), int(r.lstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZ') or 0)))),
                         len(refs), value, footprint, maker, mpn, desc])

    # CPL: KiCad's position file with the usual assembly-service columns
    pos = os.path.join(OUT, 'pos.csv')
    run('kicad-cli', 'pcb', 'export', 'pos', '-o', pos, '--format', 'csv', '--units', 'mm',
        '--side', 'both', BOARD)
    with open(pos) as src, open(os.path.join(OUT, 'cpl.csv'), 'w', newline='') as dst:
        rd = csv.DictReader(src)
        wr = csv.writer(dst)
        wr.writerow(['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'])
        for r in rd:
            wr.writerow([r['Ref'], f'{float(r["PosX"]):.4f}mm', f'{float(r["PosY"]):.4f}mm',
                         'Top' if r['Side'] == 'top' else 'Bottom', r['Rot']])
    os.remove(pos)

    print(f'board {w:.2f} x {h:.2f} mm, {len(fps)} parts, rev {rev}')
    print('gerber/drill files:', ', '.join(files))
    print(os.path.normpath(zpath))


if __name__ == '__main__':
    main()
