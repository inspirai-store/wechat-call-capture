#!/usr/bin/env python3
"""Install a separate nginx site and renewal timer after obtaining an IP certificate."""
import ipaddress
import os
from pathlib import Path
import shutil
import subprocess
import sys

if os.geteuid() != 0 or len(sys.argv) != 2:
    raise SystemExit('Run as root with the public IPv4 address.')
ip = str(ipaddress.IPv4Address(sys.argv[1]))
base = Path('/opt/phone-camera-web')
staging, www = base / 'staging', base / 'www'
config_dir = Path('/etc/phone-camera-web')
web_unit = Path('/etc/systemd/system/phone-camera-web.service')
renew_unit = Path('/etc/systemd/system/phone-camera-cert-renew.service')
timer_unit = Path('/etc/systemd/system/phone-camera-cert-renew.timer')
hook = Path('/usr/local/libexec/phone-camera-reload-web')
certificate = Path('/etc/letsencrypt/live/phone-camera-ip/fullchain.pem')
if not certificate.is_file():
    raise SystemExit('Public IP certificate not found.')
if any(x.exists() for x in (www, config_dir, web_unit, renew_unit, timer_unit, hook)):
    raise SystemExit('A target path exists; refusing to overwrite.')
for name in ('index.html', 'phone.js', 'phone.css'):
    if not (staging / name).is_file():
        raise SystemExit('Missing staged static file: ' + name)
for name in ('phone-camera-web.service', 'phone-camera-cert-renew.service', 'phone-camera-cert-renew.timer'):
    state = subprocess.run(['systemctl', 'show', '--property=LoadState', '--value', name],
                           capture_output=True, text=True)
    if state.stdout.strip() not in ('', 'not-found'):
        raise SystemExit('Service already exists: ' + name)

nginx = f'''worker_processes 1;
pid /run/phone-camera-web.pid;
events {{ worker_connections 1024; }}
http {{
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log off;
    sendfile on;
    server {{
        listen 80;
        server_name _;
        location ^~ /.well-known/acme-challenge/ {{ root /opt/phone-camera-web/acme; }}
        location / {{ return 308 https://{ip}$request_uri; }}
    }}
    server {{
        listen 443 ssl;
        server_name _;
        ssl_certificate /etc/letsencrypt/live/phone-camera-ip/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/phone-camera-ip/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        client_max_body_size 160k;
        add_header Cache-Control "no-store" always;
        add_header Referrer-Policy "no-referrer" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'" always;
        location /api/ {{
            proxy_pass http://127.0.0.1:18765;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-Proto https;
            proxy_read_timeout 60s;
        }}
        location / {{ root /opt/phone-camera-web/www; try_files $uri $uri/ =404; }}
    }}
}}
'''
web = '''[Unit]
Description=Phone camera fixed HTTPS page
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
PIDFile=/run/phone-camera-web.pid
ExecStartPre=/usr/sbin/nginx -t -c /etc/phone-camera-web/nginx.conf
ExecStart=/usr/sbin/nginx -c /etc/phone-camera-web/nginx.conf
ExecReload=/usr/sbin/nginx -c /etc/phone-camera-web/nginx.conf -s reload
ExecStop=/usr/sbin/nginx -c /etc/phone-camera-web/nginx.conf -s quit
PrivateTmp=true

[Install]
WantedBy=multi-user.target
'''
renew = '''[Unit]
Description=Renew phone camera IP certificate
After=phone-camera-web.service
Requires=phone-camera-web.service

[Service]
Type=oneshot
ExecStart=/opt/phone-camera-certbot/bin/certbot renew --cert-name phone-camera-ip --non-interactive --no-random-sleep-on-renew --deploy-hook /usr/local/libexec/phone-camera-reload-web
'''
timer = '''[Unit]
Description=Regularly check phone camera IP certificate renewal

[Timer]
OnCalendar=*-*-* 00,06,12,18:25:00
Persistent=true
RandomizedDelaySec=10min

[Install]
WantedBy=timers.target
'''

def create(path, text, mode=0o644):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
    os.chmod(path, mode)

created = []
created_www = created_config_dir = created_config = False
try:
    www.mkdir(mode=0o755)
    created_www = True
    for name in ('index.html', 'phone.js', 'phone.css'):
        shutil.copyfile(staging / name, www / name)
        os.chmod(www / name, 0o644)
    config_dir.mkdir(mode=0o755)
    created_config_dir = True
    create(config_dir / 'nginx.conf', nginx)
    created_config = True
    for path, content, mode in ((web_unit, web, 0o644), (renew_unit, renew, 0o644),
                                (timer_unit, timer, 0o644),
                                (hook, '#!/bin/sh\nexec /usr/bin/systemctl reload phone-camera-web.service\n', 0o755)):
        path.parent.mkdir(parents=True, exist_ok=True)
        create(path, content, mode)
        created.append(path)
    subprocess.run(['/usr/sbin/nginx', '-t', '-c', str(config_dir / 'nginx.conf')], check=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'phone-camera-web.service', 'phone-camera-cert-renew.timer'], check=True)
    subprocess.run(['systemctl', 'is-active', '--quiet', 'phone-camera-web.service'], check=True)
    subprocess.run(['systemctl', 'is-active', '--quiet', 'phone-camera-cert-renew.timer'], check=True)
    print('Fixed HTTPS site and renewal timer installed.')
except Exception:
    names = [name for path, name in ((timer_unit, 'phone-camera-cert-renew.timer'),
                                      (web_unit, 'phone-camera-web.service')) if path in created]
    if names:
        subprocess.run(['systemctl', 'disable', '--now'] + names, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for path in reversed(created): path.unlink(missing_ok=True)
    if created_config: (config_dir / 'nginx.conf').unlink(missing_ok=True)
    if created_config_dir: config_dir.rmdir()
    if created_www: shutil.rmtree(www)
    subprocess.run(['systemctl', 'daemon-reload'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raise
