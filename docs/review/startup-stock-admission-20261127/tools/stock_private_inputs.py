#!/usr/bin/env python3
"""Resolve lab inputs without putting private workspace or device addresses in Git."""

from functools import cache
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import stat


@cache
def private_inputs():
    path = Path(os.environ.get('AIDOKU_STOCK_INPUTS', ''))
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError('use_absolute_private_stock_inputs')
    directory = path.parent.stat()
    if directory.st_uid != os.geteuid() or directory.st_mode & 0o077:
        raise ValueError('stock_input_directory_not_private')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= 8192):
            raise ValueError('stock_input_file_not_private')
        raw = source.read(8193)
    if not 0 < len(raw) <= 8192:
        raise ValueError('stock_input_size_invalid')
    data = json.loads(raw)
    if not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 1 or not isinstance(data.get('paths'), dict):
        raise ValueError('invalid_stock_input_format')
    return data


def private_path(name):
    value = private_inputs()['paths'].get(name)
    if not isinstance(value, str) or any(c in value for c in '\0\n\r'):
        raise ValueError(f'missing_stock_input_{name}')
    path = Path(value)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError(f'noncanonical_stock_input_{name}')
    return path


def device_hostname():
    value = private_inputs().get('device_hostname')
    if not isinstance(value, str) or str(IPv4Address(value)) != value:
        raise ValueError('invalid_private_device_hostname')
    return value.encode('ascii')
