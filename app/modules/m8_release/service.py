from collections import defaultdict

from app.manifest import json_bytes, sha
from app.modules.m6_balance.shifts import shift_of
from app.modules.m8_release.model import DIRECTIONS

FLAGS = {
    'OFFSET_EXCEEDS': '抵掉超过放行数',
    'WINDOW_OVERLAP': '同站同向窗口重叠',
    'STATION_UNKNOWN': '站点不在本批闸机记录',
    'SPANS_SHIFTS': '窗口跨班次',
}
BLOCKING = {'OFFSET_EXCEEDS', 'WINDOW_OVERLAP', 'STATION_UNKNOWN'}
CHECK_LABELS = {'windows': FLAGS['WINDOW_OVERLAP'], 'offsets': FLAGS['OFFSET_EXCEEDS'],
                'stations': FLAGS['STATION_UNKNOWN'], 'net_split': '净补拆分不平', 'accounted': '批次记录缺失'}
STATUS = {'active': '待核对', 'verified': '已核对', 'superseded': '被取代', 'voided': '已作废'}
ZERO = {'gate': 0, 'released': 0, 'offset': 0, 'pending': 0, 'verified': 0, 'batches': 0}


def overlaps(active):
    flagged = set()
    lanes = defaultdict(list)
    for batch in active:
        lanes[(batch['station'], batch['direction'])].append(batch)
    for lane in lanes.values():
        lane.sort(key=lambda row: (row['start_ms'], row['end_ms'], row['batch'], row['version']))
        widest = None
        for current in lane:
            if widest and current['start_ms'] < widest['end_ms']:
                flagged.update((widest['sequence'], current['sequence']))
            if widest is None or current['end_ms'] > widest['end_ms']:
                widest = current
    return flagged


def compile_ledger(run_id, data, stored, generated_at):
    movements = [row for row in data['direction'] if row['direction'] in DIRECTIONS]
    stations = {row['station'] for row in data['intake']}
    active = [row for row in stored if row['status'] == 'active']
    overlapping = overlaps(active)

    evaluated = {}
    for batch in active:
        matched = [motion for motion in movements
                   if motion['station'] == batch['station'] and motion['direction'] == batch['direction']
                   and batch['start_ms'] <= motion['end_ms'] < batch['end_ms']]
        flags = set()
        if len(matched) > batch['released']:
            flags.add('OFFSET_EXCEEDS')
        if batch['sequence'] in overlapping:
            flags.add('WINDOW_OVERLAP')
        if batch['station'] not in stations:
            flags.add('STATION_UNKNOWN')
        if shift_of(batch['start_ms']) != shift_of(batch['end_ms'] - 1):
            flags.add('SPANS_SHIFTS')
        evaluated[batch['sequence']] = {'offset': len(matched), 'net': batch['released'] - len(matched),
                                        'shift': shift_of(batch['start_ms']), 'flags': flags,
                                        'blocked': bool(flags & BLOCKING)}

    rows = {}
    for fact in data['balance']['shifts']:
        for direction in DIRECTIONS:
            rows.setdefault((fact['shift'], fact['station'], direction), {**ZERO})
            rows[(fact['shift'], fact['station'], direction)]['gate'] = fact['entry' if direction == 'entry' else 'exit']
    for batch in active:
        facts = evaluated[batch['sequence']]
        key = (facts['shift'], batch['station'], batch['direction'])
        row = rows.setdefault(key, {**ZERO})
        if facts['blocked']:
            row.setdefault('excluded', 0)
            row['excluded'] += 1
            continue
        row['released'] += batch['released']
        row['offset'] += facts['offset']
        row['verified' if batch['verifier'] else 'pending'] += facts['net']
        row['batches'] += 1
        row.setdefault('marks', set())
        row['marks'] |= facts['flags']

    ledger_rows = []
    for (shift, station, direction), facts in sorted(rows.items()):
        if not (facts['gate'] or facts['released'] or facts.get('excluded')):
            continue
        net = facts['released'] - facts['offset']
        labels = [FLAGS[flag] for flag in sorted(facts.get('marks', ())) if flag in FLAGS]
        if facts.get('excluded'):
            labels.append('有异常批次未计入')
        ledger_rows.append({'shift': shift, 'station': station, 'direction': direction,
                            'label': DIRECTIONS[direction], 'net': net,
                            'reported': facts['gate'] + facts['verified'], 'prospective': facts['gate'] + net,
                            'marks': '、'.join(labels),
                            **{key: facts[key] for key in ZERO}})

    display = []
    for batch in stored:
        facts = evaluated.get(batch['sequence'])
        status = 'active' if batch['status'] == 'active' and not batch['verifier'] else \
                 'verified' if batch['status'] == 'active' else batch['status']
        display.append({'batch': batch['batch'], 'version': batch['version'], 'station': batch['station'],
                        'direction': batch['direction'], 'label': DIRECTIONS[batch['direction']],
                        'start_ms': batch['start_ms'], 'end_ms': batch['end_ms'],
                        'shift': facts['shift'] if facts else shift_of(batch['start_ms']),
                        'released': batch['released'], 'offset': facts['offset'] if facts else 0,
                        'net': facts['net'] if facts else 0, 'status': status, 'status_label': STATUS[status],
                        'marks': '、'.join(FLAGS[flag] for flag in sorted(facts['flags'])) if facts else '',
                        'entered_by': batch['entered_by'], 'entered_at': batch['entered_at'],
                        'verifier': batch['verifier'] or '', 'verified_at': batch['verified_at'] or '',
                        'note': batch['note'], 'void_note': batch['void_note'], 'raw': [batch['payload']]})
    display.sort(key=lambda row: (row['batch'], row['version']))

    totals = {'gate_entry': sum(row['gate'] for row in ledger_rows if row['direction'] == 'entry'),
              'gate_exit': sum(row['gate'] for row in ledger_rows if row['direction'] == 'exit'),
              'released': sum(row['released'] for row in ledger_rows),
              'offset': sum(row['offset'] for row in ledger_rows),
              'net': sum(row['net'] for row in ledger_rows),
              'pending': sum(row['pending'] for row in ledger_rows),
              'verified': sum(row['verified'] for row in ledger_rows),
              'active_batches': len(active), 'versions': len(stored),
              'blocked_batches': sum(1 for batch in active if evaluated[batch['sequence']]['blocked'])}
    checks = {'windows': not overlapping,
              'offsets': not any('OFFSET_EXCEEDS' in evaluated[batch['sequence']]['flags'] for batch in active),
              'stations': not any('STATION_UNKNOWN' in evaluated[batch['sequence']]['flags'] for batch in active),
              'net_split': all(row['pending'] + row['verified'] == row['net'] for row in ledger_rows),
              'accounted': len(display) == len(stored)}
    ledger = {'run_id': run_id, 'generated_at': generated_at, 'rows': ledger_rows, 'batches': display,
              'totals': totals, 'checks': checks, 'balanced': all(checks.values())}
    ledger['ledger_id'] = 'rel_' + sha(json_bytes({'run': run_id, 'rows': ledger_rows,
                                                   'batches': display}))[:24]
    return ledger
