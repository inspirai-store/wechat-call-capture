import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from phone_turn import configured_ice
from wechat_capture import CaptureError


class TurnIssuerTests(unittest.TestCase):
    def test_missing_profile_uses_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(configured_ice(Path(tmp) / 'missing.json'))

    def test_issuer_supplies_ephemeral_credentials_without_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / 'profile.json'
            profile.write_text(json.dumps({'issuer_ssh': 'test@example.com'}), encoding='utf-8')
            result = subprocess.CompletedProcess([], 0, stdout=json.dumps({
                'url': 'turn:example.com:3479?transport=udp', 'username': 'temp-user',
                'credential': 'temp-password', 'expires': int(time.time()) + 300}).encode())
            with patch('phone_turn.subprocess.run', return_value=result) as run:
                ice = configured_ice(profile)
            self.assertEqual(ice[1]['username'], 'temp-user')
            self.assertEqual(ice[1]['credential'], 'temp-password')
            self.assertIsInstance(run.call_args.args[0], list)
            self.assertEqual(run.call_args.args[0][-1], '/usr/local/libexec/phone-camera-turn-credentials')

    def test_invalid_target_and_expired_credentials_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / 'profile.json'
            profile.write_text(json.dumps({'issuer_ssh': 'test@example.com;bad'}), encoding='utf-8')
            with patch('phone_turn.subprocess.run') as run, self.assertRaises(CaptureError):
                configured_ice(profile)
            run.assert_not_called()
            profile.write_text(json.dumps({'issuer_ssh': 'test@example.com'}), encoding='utf-8')
            result = subprocess.CompletedProcess([], 0, stdout=json.dumps({
                'url': 'turn:example.com:3479', 'username': 'x', 'credential': 'y', 'expires': 1}).encode())
            with patch('phone_turn.subprocess.run', return_value=result), self.assertRaises(CaptureError):
                configured_ice(profile)


if __name__ == '__main__':
    unittest.main()
