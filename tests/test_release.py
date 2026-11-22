import pytest

WINDOW = {'start': '2026-11-19T23:00:00+08:00', 'end': '2026-11-20T02:00:00+08:00'}


@pytest.fixture
def run(client):
    return client.post('/api/runs/sample').json()['run_id']


def enter(client, run, **overrides):
    payload = {'batch': 'REL-STH-01', 'station': 'STH', 'direction': 'entry', **WINDOW,
               'released': 5, 'entered_by': 'op1', 'note': ''}
    return client.post(f'/api/releases/{run}/batches', json={**payload, **overrides})


def ledger(client, run, view='main', **params):
    return client.get(f'/api/releases/{run}', params={'view': view, 'limit': 100, **params}).json()


def row_of(client, run, station='STH', direction='entry', shift='2026-11-19 夜班'):
    rows = ledger(client, run)['items']
    return next(row for row in rows if (row['station'], row['direction'], row['shift']) == (station, direction, shift))


def test_ledger_rows_show_source_offset_and_net_by_station_shift(client, run):
    assert enter(client, run).status_code == 201
    row = row_of(client, run)
    assert (row['gate'], row['released'], row['offset'], row['net']) == (2, 5, 2, 3)
    assert (row['pending'], row['verified'], row['reported'], row['prospective']) == (3, 0, 2, 5)
    gate_only = row_of(client, run, station='NTH')
    assert (gate_only['gate'], gate_only['released'], gate_only['batches']) == (4, 0, 0)
    totals = ledger(client, run)['totals']
    assert (totals['gate_entry'], totals['released'], totals['offset'], totals['net']) == (9, 5, 2, 3)


def test_identical_reentry_is_reused_not_accumulated(client, run):
    assert enter(client, run).status_code == 201
    again = enter(client, run)
    assert again.status_code == 200 and again.json() == {'batch': 'REL-STH-01', 'version': 1,
                                                         'reused': True, 'status': 'active'}
    batches = ledger(client, run, 'batches')
    assert batches['total'] == 1 and ledger(client, run)['totals']['released'] == 5


def test_changed_reentry_supersedes_and_resets_verification(client, run):
    enter(client, run)
    client.post(f'/api/releases/{run}/batches/REL-STH-01/verify', json={'verifier': 'aud1'})
    changed = enter(client, run, released=7)
    assert changed.status_code == 201 and changed.json()['version'] == 2
    batches = {row['version']: row for row in ledger(client, run, 'batches')['items']}
    assert [row['status_label'] for row in batches.values()] == ['被取代', '待核对']
    totals = ledger(client, run)['totals']
    assert (totals['released'], totals['pending'], totals['verified'], totals['versions']) == (7, 5, 0, 2)


def test_verify_moves_pending_to_verified_and_is_idempotent(client, run):
    enter(client, run)
    verified = client.post(f'/api/releases/{run}/batches/REL-STH-01/verify', json={'verifier': 'aud1', 'note': '现场复核'})
    assert verified.status_code == 201
    again = client.post(f'/api/releases/{run}/batches/REL-STH-01/verify', json={'verifier': 'aud2'})
    assert again.status_code == 200 and again.json()['verifier'] == 'aud1'
    row = row_of(client, run)
    assert (row['pending'], row['verified'], row['reported']) == (0, 3, 5)
    assert client.post(f'/api/releases/{run}/batches/REL-NONE/verify', json={'verifier': 'aud1'}).status_code == 404


def test_void_excludes_batch_and_reentry_revives(client, run):
    enter(client, run)
    voided = client.post(f'/api/releases/{run}/batches/REL-STH-01/void', json={'note': '重复登记'})
    assert voided.status_code == 201
    assert client.post(f'/api/releases/{run}/batches/REL-STH-01/void', json={'note': '再作废'}).status_code == 200
    assert ledger(client, run)['totals']['released'] == 0
    assert client.post(f'/api/releases/{run}/batches/REL-STH-01/verify', json={'verifier': 'aud1'}).status_code == 409
    revived = enter(client, run)
    assert revived.status_code == 201 and revived.json()['version'] == 2
    assert ledger(client, run)['totals']['released'] == 5
    labels = [row['status_label'] for row in ledger(client, run, 'batches')['items']]
    assert labels == ['已作废', '待核对']


def test_offset_exceeding_release_blocks_publish_and_keeps_previous(client, run):
    enter(client, run)
    first = client.post(f'/api/releases/{run}/publish')
    assert first.status_code == 201
    morning = {'start': '2026-11-20T06:00:00+08:00', 'end': '2026-11-20T07:00:00+08:00'}  # 覆盖早班 2 次进站
    enter(client, run, batch='REL-STH-02', released=1, **morning)
    failed = client.post(f'/api/releases/{run}/publish')
    assert failed.status_code == 409 and failed.json()['code'] == 'LEDGER_FAILED'
    assert failed.json()['detail']['offsets'] is False
    state = ledger(client, run)
    assert state['published']['ledger_id'] == first.json()['ledger_id'] and state['dirty'] is True
    history = ledger(client, run, 'history')['items']
    assert [row['result'] for row in history] == ['failed', 'published']
    assert enter(client, run, batch='REL-STH-02', released=1, **morning).json()['reused'] is True
    fixed = enter(client, run, batch='REL-STH-02', released=3, **morning)
    assert fixed.json()['version'] == 2
    assert client.post(f'/api/releases/{run}/publish').status_code == 201
    assert [row['result'] for row in ledger(client, run, 'history')['items']] == ['published', 'failed', 'published']


def test_overlapping_windows_block_publish_and_are_excluded(client, run):
    enter(client, run)
    enter(client, run, batch='REL-STH-02', start='2026-11-20T01:00:00+08:00', end='2026-11-20T03:00:00+08:00')
    state = ledger(client, run)
    assert state['checks']['windows'] is False and state['balanced'] is False
    assert state['totals']['released'] == 0 and state['totals']['blocked_batches'] == 2
    assert client.post(f'/api/releases/{run}/publish').status_code == 409
    marks = [row['marks'] for row in ledger(client, run, 'batches')['items']]
    assert all('窗口重叠' in mark for mark in marks)


def test_rerun_same_sources_keeps_ledger_without_double_count(client, run):
    enter(client, run)
    client.post(f'/api/releases/{run}/publish')
    assert client.post(f'/api/runs/{run}/rerun').json()['run_id'] == run
    state = ledger(client, run)
    assert (state['totals']['released'], state['totals']['versions']) == (5, 1)
    assert state['dirty'] is False


def test_sample_preview_is_readonly(client):
    state = client.get('/api/releases/sample').json()
    assert state['readonly'] is True and state['totals']['gate_entry'] == 9
    assert state['totals']['released'] == 0 and state['items']
    response = client.post('/api/releases/sample/batches', json={'batch': 'REL-1'})
    assert response.status_code == 409 and response.json()['code'] == 'SAMPLE_READONLY'


def test_unknown_station_blocks_publish(client, run):
    enter(client, run, station='XXX')
    state = ledger(client, run)
    assert state['checks']['stations'] is False
    assert client.post(f'/api/releases/{run}/publish').status_code == 409
    batch = ledger(client, run, 'batches')['items'][0]
    assert '站点不在本批闸机记录' in batch['marks']


def test_window_spanning_shifts_is_marked_but_still_counts(client, run):
    enter(client, run, start='2026-11-20T13:00:00+08:00', end='2026-11-20T15:00:00+08:00')
    batch = ledger(client, run, 'batches')['items'][0]
    assert '窗口跨班次' in batch['marks'] and batch['shift'] == '2026-11-20 早班'
    assert ledger(client, run)['balanced'] is True
    assert client.post(f'/api/releases/{run}/publish').status_code == 201


def test_invalid_submission_rejected(client, run):
    assert enter(client, run, direction='in').json()['code'] == 'BAD_DIRECTION'
    assert enter(client, run, end=WINDOW['start'], start=WINDOW['end']).json()['code'] == 'BAD_WINDOW'
    assert enter(client, run, released=0).json()['code'] == 'BAD_INTEGER'
    assert enter(client, run, batch='').json()['code'] == 'MISSING_VALUE'
    assert ledger(client, run)['totals']['versions'] == 0


def test_history_and_superseded_versions_never_lost(client, run):
    enter(client, run)
    enter(client, run, released=9)
    client.post(f'/api/releases/{run}/batches/REL-STH-01/void', json={'note': '改用手工台账'})
    enter(client, run, released=4)
    batches = ledger(client, run, 'batches')['items']
    assert [(row['version'], row['status_label']) for row in batches] == [(1, '被取代'), (2, '已作废'), (3, '待核对')]
    assert all(row['raw'] for row in batches)
    assert ledger(client, run)['totals']['released'] == 4
