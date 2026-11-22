from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.modules.m8_release.model import parse_submission, parse_verification, parse_void
from app.modules.m8_release.service import compile_ledger
from app.modules.m8_release.store import ReleaseStore, now
from app.problems import Problem
from app.query import QueryRows, snapshot
from app.settings import PREVIEW

router = APIRouter(prefix='/api/releases')


def persisted(request, run_id):
    if run_id == PREVIEW:
        raise Problem('SAMPLE_READONLY', 409)
    return request.app.state.archive.read(run_id)


@router.get('/{run_id}')
def releases(request: Request, run_id: str, query: QueryRows = Depends()):
    data = snapshot(request, run_id)
    store = ReleaseStore(request.app.state.archive)
    readonly = run_id == PREVIEW
    ledger = compile_ledger(run_id, data, [] if readonly else store.batches(run_id), now())
    published = None if readonly else store.published(run_id)
    history = [] if readonly else [{'ledger_id': row['ledger_id'], 'published_at': row['published_at'],
                                    'result': row['result'], 'result_label': '发布' if row['result'] == 'published' else '失败',
                                    'detail': row['detail']} for row in store.history(run_id)]
    return query.result({'main': ledger['rows'], 'batches': ledger['batches'], 'history': history},
                         totals=ledger['totals'], checks=ledger['checks'], balanced=ledger['balanced'],
                         ledger_id=ledger['ledger_id'], published=published, readonly=readonly,
                         dirty=not readonly and (not published or published['ledger_id'] != ledger['ledger_id']))


@router.post('/{run_id}/batches')
def enter(request: Request, run_id: str, payload: Annotated[dict, Body()]):
    persisted(request, run_id)
    record, reused = ReleaseStore(request.app.state.archive).enter(run_id, parse_submission(payload))
    return JSONResponse({'batch': record['batch'], 'version': record['version'], 'reused': reused,
                         'status': record['status']}, status_code=200 if reused else 201)


@router.post('/{run_id}/batches/{batch}/verify')
def verify(request: Request, run_id: str, batch: str, payload: Annotated[dict, Body()]):
    persisted(request, run_id)
    verifier, note = parse_verification(payload)
    record, reused = ReleaseStore(request.app.state.archive).verify(run_id, batch, verifier, note)
    return JSONResponse({'batch': record['batch'], 'version': record['version'], 'reused': reused,
                         'verifier': record['verifier']}, status_code=200 if reused else 201)


@router.post('/{run_id}/batches/{batch}/void')
def void(request: Request, run_id: str, batch: str, payload: Annotated[dict, Body()]):
    persisted(request, run_id)
    note = parse_void(payload)
    record, reused = ReleaseStore(request.app.state.archive).void(run_id, batch, note)
    return JSONResponse({'batch': record['batch'], 'version': record['version'], 'reused': reused,
                         'status': record['status']}, status_code=200 if reused else 201)


@router.post('/{run_id}/publish')
def publish(request: Request, run_id: str):
    data = persisted(request, run_id)
    ledger, reused = ReleaseStore(request.app.state.archive).publish(run_id, data)
    return JSONResponse({'ledger_id': ledger['ledger_id'], 'reused': reused, 'balanced': ledger['balanced'],
                         'totals': ledger['totals']}, status_code=200 if reused else 201)
