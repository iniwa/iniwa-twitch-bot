"""Stage one bounded legacy data copy, excluding credential settings recursively.

Never modify the source or overwrite a destination. An interrupted/changed copy
keeps an unsupported marker so the candidate inspector cannot accept it.
"""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat


class StagingError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


SECRET_WORDS = ('token', 'secret', 'password', 'authorization', 'credential')
MAX_FILE = 64 * 1024**2
MAX_TOTAL = 256 * 1024**2
MAX_FILES = 4096


def _identity(path):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or path.is_symlink() or path.resolve() != path:
        raise StagingError('unsafe_source_entry')
    if value.st_size > MAX_FILE:
        raise StagingError('source_limit_exceeded')
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _inventory(root):
    paths = []
    ignored = 0
    for path in root.iterdir():
        if path.name in ('config.json', 'viewers.json'):
            paths.append(path)
        elif path.name == 'history':
            if path.is_symlink() or not path.is_dir() or path.resolve() != path:
                raise StagingError('unsafe_source_entry')
            for child in path.iterdir():
                if child.name == 'stream_index.json' or re.fullmatch(r'stream_[A-Za-z0-9_-]{1,128}\.jsonl', child.name):
                    paths.append(child)
                else:
                    ignored += 1
                if len(paths) > MAX_FILES: raise StagingError('source_limit_exceeded')
        else:
            ignored += 1
    result = {p.relative_to(root).as_posix(): _identity(p) for p in sorted(paths)}
    if not {'config.json', 'viewers.json', 'history/stream_index.json'} <= result.keys():
        raise StagingError('required_source_missing')
    if sum(v[2] for v in result.values()) > MAX_TOTAL:
        raise StagingError('source_limit_exceeded')
    return result, ignored


def _read(path):
    with path.open('rb') as source:
        content = source.read(MAX_FILE + 1)
    if len(content) > MAX_FILE: raise StagingError('source_limit_exceeded')
    return content


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError
        result[key] = value
    return result


def _credential_key(key):
    words = re.sub(r'([a-z])([A-Z])', r'\1_\2', key).casefold()
    return any(word in SECRET_WORDS or word in ('tokens','secrets','passwords','credentials')
               for word in re.split(r'[^a-z]+', words)) or words.endswith(('accesstoken','refreshtoken','clientsecret'))


def _without_credentials(value):
    removed = 0
    if isinstance(value, dict):
        clean = {}
        for key, child in value.items():
            if _credential_key(key):
                removed += 1
            else:
                clean[key], count = _without_credentials(child)
                removed += count
        return clean, removed
    if isinstance(value, list):
        clean = []
        for child in value:
            sanitized, count = _without_credentials(child)
            clean.append(sanitized); removed += count
        return clean, removed
    return value, 0


def _config(raw):
    try:
        def invalid_constant(_): raise ValueError
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=invalid_constant)
        if not isinstance(document, dict): raise ValueError
        clean, removed = _without_credentials(document)
        return json.dumps(clean, ensure_ascii=True, allow_nan=False).encode(), removed
    except (ValueError, UnicodeError, RecursionError):
        raise StagingError('invalid_source_configuration') from None


def stage(source, target):
    source, target = Path(source), Path(target)
    try:
        if not source.is_absolute() or source.resolve() != source or not source.is_dir():
            raise StagingError('invalid_source_root')
        if not target.is_absolute() or target.exists() or target.is_symlink() or target.parent.resolve() != target.parent or target.is_relative_to(source) or source.is_relative_to(target):
            raise StagingError('unsafe_staging_target')
        before, ignored = _inventory(source)
        target.mkdir(mode=0o700)
        marker = target/'.staging-incomplete'
        with marker.open('xb'): pass
        digests = {}
        removed = 0
        for name in before:
            raw = _read(source/name)
            digests[name] = sha256(raw).digest()
            if name == 'config.json': raw, removed = _config(raw)
            destination = target/name
            destination.parent.mkdir(mode=0o700, exist_ok=True)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as output:
                output.write(raw); output.flush(); os.fsync(output.fileno())
        after, ignored_after = _inventory(source)
        if before != after or ignored != ignored_after:
            raise StagingError('source_changed')
        for name, digest in digests.items():
            if sha256(_read(source/name)).digest() != digest:
                raise StagingError('source_changed')
        if _inventory(source) != (before, ignored):
            raise StagingError('source_changed')
        marker.unlink()
        return {'state':'staged', 'files':len(before), 'source_bytes':sum(v[2] for v in before.values()),
                'credential_fields_removed':removed, 'ignored_entries':ignored, 'source_unchanged':True,
                'requires_candidate_verification':True}
    except OSError:
        raise StagingError('staging_io_failed') from None


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--target',required=True)
    args=parser.parse_args(argv)
    try:
        result=stage(args.source,args.target)
    except StagingError as exc:
        print(json.dumps({'state':'not_staged','error':exc.code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__=='__main__': raise SystemExit(main())
