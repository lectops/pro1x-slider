# SPDX-License-Identifier: GPL-3.0-only
"""Bounded, versioned file protocol shared by the companion and watcher."""
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

APP_ID = 'pro1x-slider.lectops'
VERSION = '0.3.0'
PROTOCOL_VERSION = SCHEMA_VERSION = 1
MAX_JSON_BYTES = 16384

def default_config():
    return {'schema_version': 1, 'revision': str(uuid.uuid4()), 'enabled': True,
            'backlight': {'enabled': True, 'brightness_percent': 100},
            'rotation': {'enabled': True, 'direction': 'keyboard'},
            'keyboard': {'hide_when_open': True}}

def validate_config(value):
    def keys(obj, expected):
        if not isinstance(obj, dict) or set(obj) != set(expected):
            raise ValueError('Missing or unknown configuration fields')
    keys(value, ('schema_version', 'revision', 'enabled', 'backlight', 'rotation', 'keyboard'))
    if type(value['schema_version']) is not int or value['schema_version'] != SCHEMA_VERSION:
        raise ValueError('Unsupported configuration schema')
    if not isinstance(value['revision'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['revision']):
        raise ValueError('Invalid configuration revision')
    keys(value['backlight'], ('enabled', 'brightness_percent'))
    keys(value['rotation'], ('enabled', 'direction'))
    keys(value['keyboard'], ('hide_when_open',))
    for flag in (value['enabled'], value['backlight']['enabled'], value['rotation']['enabled'], value['keyboard']['hide_when_open']):
        if type(flag) is not bool:
            raise ValueError('Feature switches must be booleans')
    brightness = value['backlight']['brightness_percent']
    if type(brightness) is not int or not 0 <= brightness <= 100:
        raise ValueError('Brightness must be an integer from 0 to 100')
    if value['rotation']['direction'] not in ('keyboard', 'reversed'):
        raise ValueError('Invalid landscape direction')
    return copy.deepcopy(value)

def config_path(home=None):
    base = Path(home) / '.config' if home is not None else Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    return base / APP_ID / 'config.json'

def status_path(home=None):
    base = Path(home) / '.cache' if home is not None else Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache')))
    return base / APP_ID / 'status.json'

def read_json(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ValueError('JSON file is too large')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=unique)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('Invalid JSON') from error

def load_config(path):
    return validate_config(read_json(path))

def atomic_json_write(path, data, durable=False):
    path = Path(path)
    content = (json.dumps(data, sort_keys=True, allow_nan=False) + '\n').encode()
    if len(content) > MAX_JSON_BYTES:
        raise ValueError('JSON file is too large')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            if durable:
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, path)
        if durable:
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def save_config(path, config):
    config = validate_config(config)
    atomic_json_write(path, config, durable=True)
    return config
