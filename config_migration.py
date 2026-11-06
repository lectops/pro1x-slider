# SPDX-License-Identifier: GPL-3.0-only
"""User-owned preference migration with exact preimages for installer rollback."""
import base64
import json
import os
from pathlib import Path
import tempfile

from slider_common import (atomic_json_write, config_path, default_config,
                           read_json, validate_config)

JOURNAL = 'settings-upgrade.json'
WAKE_HELPER = 'pro1x-wake.service'
FINGERPRINT_HELPER = 'pro1x-fingerprint-no-wake.service'


def snapshot(path):
    if path.is_symlink():
        return {'link': os.readlink(path)}
    if not path.exists():
        return None
    with path.open('rb') as stream:
        data = stream.read(16385)
    if len(data) > 16384:
        raise ValueError('Preferences are too large to migrate safely')
    return {'data': base64.b64encode(data).decode()}


def encoded(config):
    return {'data': base64.b64encode((json.dumps(config, sort_keys=True) + '\n').encode()).decode()}


def put(path, value):
    if value is None:
        path.unlink(missing_ok=True)
        return
    if 'link' in value:
        # Migration never changes symlinks, so rollback need not recreate one.
        if snapshot(path) != value:
            raise RuntimeError('A preference symlink changed during migration')
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.settings-upgrade-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(base64.b64decode(value['data'], validate=True))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def adopt(value, helpers):
    config = validate_config(value)
    if value.get('schema_version') == 1:
        wake = helpers.get(WAKE_HELPER, {})
        fingerprint = helpers.get(FINGERPRINT_HELPER, {})
        if wake.get('active') or wake.get('enabled'):
            config['wake'] = {'slider_open': True, 'keyboard': True}
        if fingerprint.get('active') or fingerprint.get('enabled'):
            config['fingerprint']['wake_screen'] = False
    return config


def prepare(base, home, helpers):
    base = Path(base)
    journal = base / 'state' / JOURNAL
    if os.path.lexists(journal):
        raise RuntimeError('Previous settings upgrade needs recovery: ' + str(journal))
    paths = {'config': config_path(home), 'checkpoint': base / 'state/last-known-good.json'}
    before = {name: snapshot(path) for name, path in paths.items()}
    after = dict(before)
    origin = 'preserved'
    if before['config'] is None:
        if before['checkpoint'] is not None:
            config = adopt(read_json(paths['checkpoint']), helpers)
            origin = 'last-known-good'
        else:
            config = default_config()
            # A fresh configuration can inherit already installed helpers too.
            if any(v.get('active') or v.get('enabled') for v in helpers.values()):
                legacy = dict(config, schema_version=1)
                legacy.pop('wake'); legacy.pop('fingerprint')
                config = adopt(legacy, helpers)
            origin = 'defaults'
            legacy = base / 'state/landscape-orientation'
            if os.path.lexists(legacy):
                if legacy.is_symlink():
                    raise ValueError('Refusing a symlink in the legacy direction configuration')
                with legacy.open() as stream:
                    raw = stream.read(129)
                mapping = {'LandscapeOrientation': 'reversed', 'InvertedLandscapeOrientation': 'keyboard'}
                if len(raw) > 128 or raw.strip() not in mapping:
                    raise ValueError('Invalid saved landscape direction')
                config['rotation']['direction'] = mapping[raw.strip()]
                origin = 'legacy-direction'
        after['config'] = encoded(config)
    else:
        # An invalid existing GUI file or a symlink remains untouched. The
        # watcher can continue from a valid checkpoint, as in previous releases.
        if 'data' in before['config']:
            try:
                raw = read_json(paths['config'])
                config = adopt(raw, helpers)
            except ValueError:
                pass
            else:
                if raw != config:
                    after['config'] = encoded(config)
                    origin = 'schema-2'
    if before['checkpoint'] and 'data' in before['checkpoint']:
        try:
            raw = read_json(paths['checkpoint'])
            checkpoint = adopt(raw, helpers)
        except ValueError:
            pass
        else:
            if raw != checkpoint:
                after['checkpoint'] = encoded(checkpoint)
    allowed_checkpoint = [before['checkpoint'], after['checkpoint']]
    if after['config'] and 'data' in after['config']:
        try:
            config = validate_config(json.loads(base64.b64decode(after['config']['data'])))
            allowed_checkpoint.append(encoded(config))
        except (ValueError, UnicodeError):
            pass
    receipt = {'schema_version': 1, 'before': before, 'after': after,
               'allowed_checkpoint': allowed_checkpoint}
    atomic_json_write(journal, receipt, durable=True)
    for name, path in paths.items():
        if snapshot(path) != before[name]:
            raise RuntimeError('Preferences changed concurrently; migration stopped')
        if before[name] != after[name]:
            put(path, after[name])
    return origin


def rollback(base, home):
    base = Path(base)
    journal = base / 'state' / JOURNAL
    if not journal.exists():
        return
    record = read_json(journal)
    paths = {'config': config_path(home), 'checkpoint': base / 'state/last-known-good.json'}
    for name, path in paths.items():
        allowed = record['allowed_checkpoint'] if name == 'checkpoint' else [record['before'][name], record['after'][name]]
        if snapshot(path) not in allowed:
            raise RuntimeError('Preferences changed after upgrade; retaining recovery instead of overwriting them')
    for name, path in paths.items():
        if snapshot(path) != record['before'][name]:
            put(path, record['before'][name])
    journal.unlink()


def commit(base):
    (Path(base) / 'state' / JOURNAL).unlink(missing_ok=True)
