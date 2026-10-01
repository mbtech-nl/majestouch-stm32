#!/usr/bin/env python3
"""Validate the design.

  python3 check.py            schematic, firmware and board checks
  python3 check.py --no-board skip the board checks (no pcbnew module needed)

1. Schematic: exports the netlist with kicad-cli and checks it against the
   Filco header map and STM32F072 pin tables written out here from the
   datasheet (DS9826), independently of the KiCad symbols.
2. Firmware: every pin in firmware/majestouch_stm32 (keyboard.json matrix,
   keyboard.c lock LEDs) must land on the right net in the schematic.
3. Board: footprints and pad nets match the schematic, and KiCad's DRC is
   clean (as saved). Headless pcbnew can't see the footprint libraries, so
   "lib_footprint_issues" is ignored.

Exits non-zero on any error. Needs KiCad 7 (kicad-cli, and the pcbnew Python
module for the board checks).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
PROJECT = 'majestouch_stm32'
SCH = os.path.join(ROOT, 'kicad', PROJECT + '.kicad_sch')
PCB = os.path.join(ROOT, 'kicad', PROJECT + '.kicad_pcb')
FW = os.path.join(ROOT, 'firmware', PROJECT)

# Full-size Filco header (deskthority "Controller matrix traces", Kitten Paw).
# J1 = header pins 1-18, J2 = 19-36 (J2 pin n is header pin n + 18).
HEADER = {
    1: 'COL0', 2: 'ROW_A', 3: 'ROW_B', 4: 'ROW_C', 5: 'ROW_D', 6: 'ROW_E',
    7: 'ROW_F', 8: 'ROW_G', 9: 'ROW_H', 10: 'ROW_I', 11: 'ROW_J',
    12: 'GND', 13: 'USB_DP_CONN', 14: 'USB_DM_CONN', 15: None, 16: None,
    17: None, 18: 'VBUS',
    19: 'ROW_K', 20: 'COL1', 21: 'ROW_L', 22: 'COL2', 23: 'COL3', 24: 'ROW_M',
    25: 'ROW_N', 26: 'ROW_O', 27: 'ROW_P', 28: 'ROW_Q', 29: 'COL4',
    30: 'ROW_R', 31: 'COL5', 32: 'COL6', 33: 'COL7',
    34: 'LED_NUM', 35: 'LED_CAPS', 36: 'LED_SCRL',
}
ROWS = [f'ROW_{c}' for c in 'ABCDEFGHIJKLMNOPQR']
COLS = [f'COL{i}' for i in range(8)]
LEDS = ['LED_NUM', 'LED_CAPS', 'LED_SCRL']   # keyboard.c order: num, caps, scroll

# STM32F072 LQFP-48 pinout (DS9826) and its 5 V-tolerant (FT) pins in use here
STM32_PIN = {name: i + 1 for i, name in enumerate(
    'VBAT PC13 PC14 PC15 PF0 PF1 NRST VSSA VDDA PA0 PA1 PA2 PA3 PA4 PA5 PA6 PA7 PB0 PB1 '
    'PB2 PB10 PB11 VSS VDD PB12 PB13 PB14 PB15 PA8 PA9 PA10 PA11 PA12 PA13 VSS VDDIO2 '
    'PA14 PA15 PB3 PB4 PB5 PB6 PB7 BOOT0 PB8 PB9 VSS VDD'.split())}
STM32_NAME = {str(n): name for name, n in STM32_PIN.items()}   # VSS/VDD repeat: checked by number
STM32_FT = {'PA8', 'PA9', 'PA10', 'PA13', 'PA14', 'PA15', 'PB3', 'PB4', 'PB5', 'PB6', 'PB7',
            'PB8', 'PB9'}

errors = []


def error(msg):
    errors.append(msg)
    print('ERROR', msg)


# --- minimal s-expression reader for KiCad's netlist ---------------------------
_TOKEN = re.compile(r'\s*(\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+)')


def parse(text):
    pos, stack, cur = 0, [], []
    while (m := _TOKEN.match(text, pos)):
        tok, pos = m.group(1), m.end()
        if tok == '(':
            stack.append(cur)
            cur = []
        elif tok == ')':
            done, cur = cur, stack.pop()
            cur.append(done)
        else:
            cur.append(tok[1:-1].replace('\\"', '"') if tok.startswith('"') else tok)
    return cur[0]


def find(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]


def first(node, key):
    r = find(node, key)
    return r[0] if r else None


def netlist():
    """{net: {(ref, pin)}} and {(ref, pin): net}, names without the '/' prefix."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'netlist.net')
        r = subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '-o', out, SCH],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f'kicad-cli netlist export failed:\n{r.stdout}{r.stderr}')
        tree = parse(open(out).read())
    nets, pin_net = {}, {}
    for n in find(first(tree, 'nets'), 'net'):
        name = first(n, 'name')[1].lstrip('/')
        nodes = {(first(x, 'ref')[1], first(x, 'pin')[1]) for x in find(n, 'node')}
        nets[name] = nodes
        for node in nodes:
            pin_net[node] = name
    return nets, pin_net


# --- 1. schematic -----------------------------------------------------------------
def check_schematic(nets, pin_net):
    def expect(ref, pin, net):
        got = pin_net.get((ref, str(pin)))
        if got != net:
            error(f'{ref}.{pin}: expected {net}, got {got}')

    def refs(net):
        return sorted(r for r, _ in nets.get(net, ()))

    for hp, net in HEADER.items():
        ref, pin = ('J1', hp) if hp <= 18 else ('J2', hp - 18)
        if net:
            expect(ref, pin, net)
        elif not pin_net.get((ref, str(pin)), '').startswith('unconnected'):
            error(f'{ref}.{pin} (header {hp}) should be unconnected')

    # matrix: header straight to a GPIO, nothing else on the line
    for net in ROWS + COLS:
        if refs(net) not in (['J1', 'U1'], ['J2', 'U1']):
            error(f'{net}: expected header + U1 only, got {sorted(nets.get(net, ()))}')
            continue
        pin = next(p for r, p in nets[net] if r == 'U1')
        if not STM32_NAME.get(pin, '').startswith('P') or STM32_NAME[pin] in ('PA11', 'PA12'):
            error(f'{net}: on U1 pin {pin} ({STM32_NAME.get(pin)}), not a free GPIO')

    # lock LEDs: header pin -> series resistor -> 5 V-tolerant MCU pin
    for led in LEDS:
        hdr, drv = refs(led), refs(led + '_DRV')
        res = (set(hdr) & set(drv)) - {'J2', 'U1'}
        if len(hdr) != 2 or 'J2' not in hdr or len(drv) != 2 or 'U1' not in drv or len(res) != 1:
            error(f'{led}: expected header -> resistor -> U1, got {hdr} / {drv}')
            continue
        pin = next(p for r, p in nets[led + '_DRV'] if r == 'U1')
        if STM32_NAME.get(pin) not in STM32_FT:
            error(f'{led}_DRV on U1 pin {pin} ({STM32_NAME.get(pin)}), which is not 5 V tolerant')

    # USB: flow-through ESD, header side in on 1/3, MCU side out on 6/4
    expect('U3', 1, 'USB_DP_CONN'); expect('U3', 6, 'USB_DP')
    expect('U3', 3, 'USB_DM_CONN'); expect('U3', 4, 'USB_DM')
    expect('U3', 2, 'GND'); expect('U3', 5, 'VBUS')
    expect('U1', STM32_PIN['PA12'], 'USB_DP'); expect('U1', STM32_PIN['PA11'], 'USB_DM')
    for net, want in (('USB_DP_CONN', ['J1', 'U3']), ('USB_DM_CONN', ['J1', 'U3']),
                      ('USB_DP', ['U1', 'U3']), ('USB_DM', ['U1', 'U3'])):
        if refs(net) != want:
            error(f'{net} must only join {want}, got {sorted(nets.get(net, ()))}')

    # power: LDO, MCU supplies, BOOT0 pull-down, NRST
    expect('U2', 1, 'VBUS'); expect('U2', 3, 'VBUS'); expect('U2', 2, 'GND'); expect('U2', 5, '+3V3')
    for pin in (1, 9, 24, 36, 48):          # VBAT, VDDA, VDD, VDDIO2, VDD
        expect('U1', pin, '+3V3')
    for pin in (8, 23, 35, 47):             # VSSA, VSS
        expect('U1', pin, 'GND')
    expect('U1', STM32_PIN['BOOT0'], 'BOOT0'); expect('R1', 1, 'BOOT0'); expect('R1', 2, 'GND')
    expect('U1', STM32_PIN['NRST'], 'NRST')

    for n, v in nets.items():
        if len(v) < 2 and not n.startswith('unconnected'):
            error(f'net {n} has a single node {v}')
    print(f'schematic: {len(nets)} nets, {len({r for r, _ in pin_net})} parts')


# --- 2. firmware ------------------------------------------------------------------
def check_firmware(pin_net):
    def net_of(qmk_pin):                    # QMK "A6" -> PA6 -> U1 pin -> net
        n = STM32_PIN.get('P' + qmk_pin)
        return pin_net.get(('U1', str(n))) if n else None

    kb = json.load(open(os.path.join(FW, 'keyboard.json')))
    if kb.get('processor') != 'STM32F072':
        error(f"keyboard.json: processor {kb.get('processor')}, expected STM32F072")
    if kb.get('diode_direction') != 'COL2ROW':
        error(f"keyboard.json: diode_direction {kb.get('diode_direction')}, expected COL2ROW")
    for kind, want in (('rows', ROWS), ('cols', COLS)):
        got = kb['matrix_pins'][kind]
        if len(got) != len(want):
            error(f'keyboard.json: {len(got)} {kind}, expected {len(want)}')
        for i, (p, net) in enumerate(zip(got, want)):
            if net_of(p) != net:
                error(f'keyboard.json: {kind}[{i}] = {p} is {net_of(p)} in the schematic, expected {net}')

    src = open(os.path.join(FW, 'keyboard.c')).read()
    m = re.search(r'lock_leds\[\]\s*=\s*\{([^}]*)\}', src)
    leds = [p.strip() for p in m.group(1).split(',')] if m else []
    if len(leds) != len(LEDS):
        error(f'keyboard.c: lock_leds = {leds}, expected {len(LEDS)} pins')
    for p, led in zip(leds, LEDS):
        if net_of(p) != led + '_DRV':
            error(f'keyboard.c: lock LED {p} is {net_of(p)} in the schematic, expected {led}_DRV')
    print(f'firmware: {len(ROWS)} rows, {len(COLS)} cols, {len(leds)} LEDs')


# --- 3. board ---------------------------------------------------------------------
def check_board(pin_net):
    import pcbnew
    board = pcbnew.LoadBoard(PCB)           # never saved
    sch_refs = {r for r, _ in pin_net}
    pcb_refs = {fp.GetReference() for fp in board.GetFootprints()}
    if sch_refs != pcb_refs:
        error(f'board vs schematic parts: only on board {sorted(pcb_refs - sch_refs)}, '
              f'only in schematic {sorted(sch_refs - pcb_refs)}')
    pads = 0
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            pads += 1
            got = pad.GetNetname().lstrip('/')
            want = pin_net.get((fp.GetReference(), pad.GetNumber()))
            if got != want:
                error(f'{fp.GetReference()}.{pad.GetNumber()}: board net {got}, schematic {want}')

    with tempfile.TemporaryDirectory() as tmp:
        rpt = os.path.join(tmp, 'drc.rpt')
        pcbnew.WriteDRCReport(board, rpt, pcbnew.EDA_UNITS_MILLIMETRES, True)
        report = open(rpt).read()
    found = re.findall(r'^\[(\w+)\]: (.*)$', report, re.M)
    issues = [(k, msg) for k, msg in found if k != 'lib_footprint_issues']
    for k, msg in issues:
        error(f'DRC {k}: {msg}')
    unconnected = int(re.search(r'Found (\d+) unconnected', report).group(1))
    if unconnected:
        error(f'DRC: {unconnected} unconnected pad(s)')
    print(f'board: {len(pcb_refs)} parts, {pads} pads, DRC {len(issues)} issue(s), '
          f'{unconnected} unconnected')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--no-board', action='store_true', help='skip the board checks (no pcbnew)')
    args = ap.parse_args()
    nets, pin_net = netlist()
    check_schematic(nets, pin_net)
    check_firmware(pin_net)
    if not args.no_board:
        check_board(pin_net)
    print('OK' if not errors else f'{len(errors)} error(s)')
    return not errors


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
