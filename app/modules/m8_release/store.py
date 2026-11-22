import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from app.manifest import json_bytes
from app.modules.m8_release.service import CHECK_LABELS, compile_ledger
from app.problems import Problem

COLUMNS = ('batch', 'version', 'station', 'direction', 'start_ms', 'end_ms', 'released',
           'entered_by', 'entered_at', 'note', 'payload', 'status', 'verifier', 'verified_at',
           'verify_note', 'void_note')


def now():
    return datetime.now(UTC).isoformat(timespec='seconds')


class ReleaseStore:
    def __init__(self, archive):
        self.archive = archive

    def connect(self):
        db = self.archive.connect()
        db.row_factory = sqlite3.Row
        return db

    def batches(self, run_id):
        with closing(self.connect()) as db:
            rows = db.execute('SELECT * FROM release_batches WHERE run_id = ? ORDER BY sequence', (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def history(self, run_id):
        with closing(self.connect()) as db:
            rows = db.execute('SELECT * FROM release_ledgers WHERE run_id = ? ORDER BY sequence DESC', (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def published(self, run_id):
        with closing(self.connect()) as db:
            row = db.execute("SELECT ledger_id, published_at FROM release_ledgers WHERE run_id = ? AND result = 'published' "
                             'ORDER BY sequence DESC LIMIT 1', (run_id,)).fetchone()
        return dict(row) if row else None

    def latest(self, db, run_id, batch):
        row = db.execute('SELECT * FROM release_batches WHERE run_id = ? AND batch = ? '
                         'ORDER BY version DESC LIMIT 1', (run_id, batch)).fetchone()
        return dict(row) if row else None

    def enter(self, run_id, submission):
        payload = json_bytes({key: submission[key] for key in
                              ('batch', 'station', 'direction', 'start', 'end', 'released', 'entered_by', 'note')}).decode()
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            latest = self.latest(db, run_id, submission['batch'])
            if latest and latest['status'] == 'active' and latest['payload'] == payload:
                return latest, True
            version = latest['version'] + 1 if latest else 1
            if latest and latest['status'] == 'active':
                db.execute("UPDATE release_batches SET status = 'superseded' WHERE sequence = ?", (latest['sequence'],))
            record = {'run_id': run_id, 'batch': submission['batch'], 'version': version,
                      'station': submission['station'], 'direction': submission['direction'],
                      'start_ms': submission['start_ms'], 'end_ms': submission['end_ms'],
                      'released': submission['released'], 'entered_by': submission['entered_by'],
                      'entered_at': now(), 'note': submission['note'], 'payload': payload,
                      'status': 'active', 'verifier': None, 'verified_at': None, 'verify_note': '', 'void_note': ''}
            db.execute('INSERT INTO release_batches (run_id, batch, version, station, direction, start_ms, end_ms, '
                       'released, entered_by, entered_at, note, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (run_id, record['batch'], version, record['station'], record['direction'],
                        record['start_ms'], record['end_ms'], record['released'], record['entered_by'],
                        record['entered_at'], record['note'], payload))
        return record, False

    def verify(self, run_id, batch, verifier, note):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            latest = self.latest(db, run_id, batch)
            if not latest:
                raise Problem('BATCH_NOT_FOUND', 404, {'batch': batch})
            if latest['status'] != 'active':
                raise Problem('BATCH_INACTIVE', 409, {'batch': batch, 'status': latest['status']})
            if latest['verifier']:
                return latest, True
            verified_at = now()
            db.execute('UPDATE release_batches SET verifier = ?, verified_at = ?, verify_note = ? WHERE sequence = ?',
                       (verifier, verified_at, note, latest['sequence']))
            latest.update(verifier=verifier, verified_at=verified_at, verify_note=note)
        return latest, False

    def void(self, run_id, batch, note):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            latest = self.latest(db, run_id, batch)
            if not latest:
                raise Problem('BATCH_NOT_FOUND', 404, {'batch': batch})
            if latest['status'] == 'voided':
                return latest, True
            db.execute("UPDATE release_batches SET status = 'voided', void_note = ? WHERE sequence = ?",
                       (note, latest['sequence']))
            latest.update(status='voided', void_note=note)
        return latest, False

    def publish(self, run_id, data):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            stored = [dict(row) for row in db.execute(
                'SELECT * FROM release_batches WHERE run_id = ? ORDER BY sequence', (run_id,)).fetchall()]
            ledger = compile_ledger(run_id, data, stored, now())
            current = db.execute("SELECT ledger_id FROM release_ledgers WHERE run_id = ? AND result = 'published' "
                                 'ORDER BY sequence DESC LIMIT 1', (run_id,)).fetchone()
            if current and current['ledger_id'] == ledger['ledger_id']:
                return ledger, True
            if ledger['balanced']:
                detail = f"批次 {ledger['totals']['active_batches']} · 放行 {ledger['totals']['released']} · 净补 {ledger['totals']['net']}"
                result = 'published'
            else:
                detail = '；'.join(label for name, label in CHECK_LABELS.items() if not ledger['checks'][name])
                result = 'failed'
            db.execute('INSERT INTO release_ledgers (run_id, ledger_id, snapshot, published_at, result, detail) '
                       'VALUES (?, ?, ?, ?, ?, ?)',
                       (run_id, ledger['ledger_id'], json_bytes(ledger), ledger['generated_at'], result, detail))
        if not ledger['balanced']:
            raise Problem('LEDGER_FAILED', 409, ledger['checks'])
        return ledger, False
