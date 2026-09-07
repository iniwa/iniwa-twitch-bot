"""Validated, bounded chat definitions and pure previews. No script evaluation."""

import re

from .persistence import PersistenceError

ROLES = {'everyone': 0, 'subscriber': 1, 'moderator': 2, 'broadcaster': 3}


def text(value, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()) or any(ord(c) < 32 for c in value):
        raise PersistenceError('invalid_automation_definition', 'automation')
    return value.strip()


def number(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PersistenceError('invalid_automation_definition', 'automation')
    return value


def command_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'![a-zA-Z0-9_]{1,32}', value):
        raise PersistenceError('invalid_command_name', 'automation')
    return value.lower()


def specification(kind, value):
    if not isinstance(value, dict):
        raise PersistenceError('invalid_automation_definition', 'automation')
    if kind == 'command':
        if set(value) != {'trigger', 'aliases', 'response_type', 'body', 'role', 'shared_seconds', 'user_seconds'}:
            raise PersistenceError('invalid_automation_definition', 'automation')
        if not isinstance(value['aliases'], list) or len(value['aliases']) > 10 or value['response_type'] not in ('text', 'game', 'uptime', 'list') or not isinstance(value['role'], str) or value['role'] not in ROLES:
            raise PersistenceError('invalid_automation_definition', 'automation')
        names = [command_name(value['trigger']), *(command_name(n) for n in value['aliases'])]
        if len(set(names)) != len(names):
            raise PersistenceError('command_name_conflict', 'automation')
        return dict(trigger=names[0], aliases=names[1:], response_type=value['response_type'],
                    body=text(value['body'], 500, empty=value['response_type'] != 'text'), role=value['role'],
                    shared_seconds=number(value['shared_seconds'], 0, 86400), user_seconds=number(value['user_seconds'], 0, 86400))
    if kind == 'post':
        if set(value) != {'body', 'target', 'category_id', 'minutes', 'comments'} or value['target'] not in ('all', 'category', 'default'):
            raise PersistenceError('invalid_automation_definition', 'automation')
        category = value['category_id']
        if value['target'] == 'category':
            if not isinstance(category, str) or not category.isascii() or not category.isdigit():
                raise PersistenceError('invalid_category', 'automation')
        elif category is not None:
            raise PersistenceError('invalid_category', 'automation')
        return dict(body=text(value['body'], 500), target=value['target'], category_id=category,
                    minutes=number(value['minutes'], 1, 1440), comments=number(value['comments'], 0, 100000))
    raise PersistenceError('invalid_automation_kind', 'automation')


def render_command(spec, stream, now, definitions, role):
    if spec['response_type'] == 'text':
        return spec['body']
    if spec['response_type'] == 'game':
        return stream.game or 'カテゴリーを確認できません。'
    if spec['response_type'] == 'uptime':
        from ..adapters.persistence.sqlite import from_rfc3339
        if not stream.started_at:
            return '配信時間を確認できません。'
        seconds = max(0, int((now-from_rfc3339(stream.started_at)).total_seconds()))
        return f'配信開始から {seconds//3600}時間{seconds%3600//60}分です。'
    names = [d['specification']['trigger'] for d in definitions if d['kind'] == 'command' and d['enabled'] and ROLES[d['specification']['role']] <= role]
    result = '使えるコマンド: '
    for name in names:
        if len(result)+len(name)+1 > 496:
            return result+' …'
        result += name+' '
    return result.strip()
