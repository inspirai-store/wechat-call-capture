#!/usr/bin/env python3
"""Install a separate TURN REST instance; never changes the existing coturn service.

Run as root on the server: python3 install_ephemeral_turn.py PUBLIC_IPV4
This creates its own configuration, signer, and systemd unit. The public IP
and network ports are not secrets; the generated signing secret stays on host.
"""
import grp
import ipaddress
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys

if os.geteuid() != 0 or len(sys.argv) != 2:
    raise SystemExit('Run as root with exactly one public IPv4 argument.')
public_ip = str(ipaddress.IPv4Address(sys.argv[1]))
config_dir = Path('/etc/phone-camera-turn')
unit = Path('/etc/systemd/system/phone-camera-turn.service')
issuer = Path('/usr/local/libexec/phone-camera-turn-credentials')
if any(path.exists() for path in (config_dir, unit, issuer)):
    raise SystemExit('Target already exists; refusing to overwrite.')
unit_state = subprocess.run(['systemctl', 'show', '--property=LoadState', '--value',
                             'phone-camera-turn.service'], capture_output=True, text=True)
if unit_state.stdout.strip() not in ('', 'not-found'):
    raise SystemExit('A service with this name already exists; refusing to overwrite.')
group = grp.getgrnam('coturn')
for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
    with socket.socket(socket.AF_INET, kind) as test:
        test.bind(('0.0.0.0', 3479))
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as test:
    test.connect(('1.1.1.1', 53))
    private_ip = test.getsockname()[0]
secret = secrets.token_urlsafe(48)
conf = f'''listening-port=3479
listening-ip=0.0.0.0
relay-ip={private_ip}
external-ip={public_ip}/{private_ip}
realm=phone-camera-turn
use-auth-secret
static-auth-secret={secret}
min-port=49224
max-port=49287
user-quota=4
total-quota=8
no-cli
no-tls
no-dtls
no-loopback-peers
no-multicast-peers
no-tcp-relay
pidfile=/tmp/phone-camera-turn.pid
denied-peer-ip=0.0.0.0-0.255.255.255
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=100.64.0.0-100.127.255.255
denied-peer-ip=127.0.0.0-127.255.255.255
denied-peer-ip=169.254.0.0-169.254.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
log-file=stdout
'''
signer = f'''#!/usr/bin/env python3
import base64, hashlib, hmac, json, secrets, time
from pathlib import Path
cfg = Path('/etc/phone-camera-turn/turnserver.conf').read_text()
secret = next(line.split('=', 1)[1] for line in cfg.splitlines() if line.startswith('static-auth-secret='))
expires = int(time.time()) + 12 * 3600
username = str(expires) + ':phone-' + secrets.token_hex(8)
credential = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
print(json.dumps({{'url': 'turn:{public_ip}:3479?transport=udp', 'username': username, 'credential': credential, 'expires': expires}}))
'''
service = '''[Unit]
Description=Phone Camera temporary credential TURN server
After=network-online.target
Wants=network-online.target

[Service]
User=coturn
Group=coturn
ExecStart=/usr/bin/turnserver -c /etc/phone-camera-turn/turnserver.conf
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
'''

def write_exclusive(path, text, mode):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as output:
        output.write(text)

created_dir = created_config = created_issuer = created_unit = False
try:
    config_dir.mkdir(mode=0o750)
    created_dir = True
    os.chown(config_dir, 0, group.gr_gid)
    os.chmod(config_dir, 0o750)
    config = config_dir / 'turnserver.conf'
    write_exclusive(config, conf, 0o640)
    created_config = True
    os.chown(config, 0, group.gr_gid)
    os.chmod(config, 0o640)
    issuer.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    write_exclusive(issuer, signer, 0o700)
    created_issuer = True
    os.chmod(issuer, 0o700)
    write_exclusive(unit, service, 0o644)
    created_unit = True
    os.chmod(unit, 0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'phone-camera-turn.service'], check=True)
    subprocess.run(['systemctl', 'is-active', '--quiet', 'phone-camera-turn.service'], check=True)
    print('Installed separate phone-camera-turn service (3479 TCP/UDP, relay UDP 49224-49287).')
except Exception:
    # Roll back only the paths this installer just created.
    if created_unit:
        subprocess.run(['systemctl', 'disable', '--now', 'phone-camera-turn.service'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        unit.unlink(missing_ok=True)
    if created_issuer: issuer.unlink(missing_ok=True)
    if created_config: (config_dir / 'turnserver.conf').unlink(missing_ok=True)
    if created_dir: config_dir.rmdir()
    subprocess.run(['systemctl', 'daemon-reload'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raise
