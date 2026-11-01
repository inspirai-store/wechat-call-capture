"""Retrieve short-lived TURN credentials from a trusted SSH issuer."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import time

from wechat_capture import CaptureError

PROFILE = Path(__file__).resolve().parent / '.phone-turn.json'
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def load_profile(profile=PROFILE):
    if not profile.is_file():
        return None
    try:
        config = json.loads(profile.read_text(encoding='utf-8'))
        target = config['issuer_ssh']
        if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+', target):
            raise ValueError('bad target')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CaptureError('手机 TURN 配置无效，请检查本机配对配置。') from exc
    return config


def configured_ice(profile=PROFILE):
    config = load_profile(profile)
    if not config:
        return None
    target = config['issuer_ssh']
    try:
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=8', target, '/usr/local/libexec/phone-camera-turn-credentials'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=15, creationflags=CREATE_NO_WINDOW, check=True)
        if len(result.stdout) > 4096:
            raise ValueError('unexpected issuer response')
        data = json.loads(result.stdout)
        url, user, credential, expires = (data['url'], data['username'], data['credential'], data['expires'])
        if (not isinstance(url, str) or not url.startswith(('turn:', 'turns:')) or
                not isinstance(user, str) or not user or not isinstance(credential, str) or not credential or
                not isinstance(expires, int) or expires < time.time() + 60):
            raise ValueError('invalid issuer response')
        return [{'urls': ['stun:stun.l.google.com:19302']},
                {'urls': [url], 'username': user, 'credential': credential}]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise CaptureError('无法获取短期 TURN 凭据：请检查 SSH 免密连接和服务状态。') from exc
