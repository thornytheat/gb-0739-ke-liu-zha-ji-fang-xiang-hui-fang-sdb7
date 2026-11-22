from app.fields import key, number, timestamp
from app.problems import BadRow, Problem

DIRECTIONS = {'entry': '进站放行', 'exit': '出站放行'}
TEXT_LIMIT = 64
RELEASED_LIMIT = 999_999


def text(value, optional=False):
    value = str(value or '').strip()
    if not value:
        if optional:
            return ''
        raise BadRow('MISSING_VALUE')
    if len(value) > TEXT_LIMIT:
        raise BadRow('BAD_TEXT')
    return value


def invalid(exc, batch=''):
    return Problem(str(exc), 422, {'batch': batch} if batch else None)


def parse_submission(body):
    try:
        submission = {
            'batch': key(str(body.get('batch', ''))),
            'station': key(str(body.get('station', ''))),
            'direction': str(body.get('direction', '')).strip(),
            'start': str(body.get('start', '')).strip(),
            'end': str(body.get('end', '')).strip(),
            'released': number(str(body.get('released', '')), 1, RELEASED_LIMIT),
            'entered_by': text(body.get('entered_by')),
            'note': text(body.get('note'), optional=True),
        }
        if submission['direction'] not in DIRECTIONS:
            raise BadRow('BAD_DIRECTION')
        start_ms, end_ms = timestamp(submission['start']), timestamp(submission['end'])
        if start_ms >= end_ms:
            raise BadRow('BAD_WINDOW')
    except BadRow as exc:
        raise invalid(exc, body.get('batch', '')) from exc
    return {**submission, 'start_ms': start_ms, 'end_ms': end_ms}


def parse_verification(body):
    try:
        return text(body.get('verifier')), text(body.get('note'), optional=True)
    except BadRow as exc:
        raise invalid(exc) from exc


def parse_void(body):
    try:
        return text(body.get('note'))
    except BadRow as exc:
        raise invalid(exc) from exc
