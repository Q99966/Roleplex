"""短来源凭据和分页游标绑定当前 World、角色、触发者及回复目的地。"""
import base64
import hashlib
import hmac
import json

from fastapi import HTTPException
from ..config import settings


def _signature(scope, purpose, value):
    world = hashlib.sha256(settings.database_url.encode()).hexdigest()
    bound = f'{world}:{scope.conversation_id}:{scope.role_id}:{scope.user_id}:{purpose}:{value}'
    return hmac.new(settings.jwt_secret.encode(), bound.encode(), hashlib.sha256).hexdigest()[:32]


def make_reference(scope, kind, source_id, revision):
    value = f'{"m" if kind == "message" else "s"}.{source_id}.{revision}'
    return value + '.' + _signature(scope, 'source', value)


def parse_reference(scope, reference):
    try:
        kind, identity, revision, signature = reference.split('.')
        value = f'{kind}.{identity}.{revision}'
        if kind not in {'m', 's'} or not hmac.compare_digest(signature, _signature(scope, 'source', value)):
            raise ValueError()
        revision = int(revision)
        if revision < 0 or revision > 2**31 - 1:
            raise ValueError()
        if kind == 'm' and (not identity.isdigit() or not 0 < int(identity) < 2**63):
            raise ValueError()
        return 'message' if kind == 'm' else 'summary', identity, revision
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, 'MEMORY_SOURCE_NOT_FOUND') from None


def encode_cursor(scope, value):
    text = base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode().rstrip('=')
    return text + '.' + _signature(scope, 'cursor', text)


def decode_cursor(scope, value):
    try:
        text, signature = value.rsplit('.', 1)
        if not hmac.compare_digest(signature, _signature(scope, 'cursor', text)):
            raise ValueError()
        return json.loads(base64.urlsafe_b64decode(text + '=' * (-len(text) % 4)))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, 'MEMORY_CURSOR_INVALID') from None
