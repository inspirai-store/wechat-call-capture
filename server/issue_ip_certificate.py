#!/usr/bin/env python3
"""Obtain a webroot IP certificate without touching other web services."""
import argparse
import ipaddress
from pathlib import Path
import socket
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('public_ip')
parser.add_argument('--staging', action='store_true')
args = parser.parse_args()
ip = str(ipaddress.IPv4Address(args.public_ip))
webroot = Path('/opt/phone-camera-web/acme')
webroot.mkdir(parents=True, exist_ok=True)
with socket.socket() as test:
    test.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    test.bind(('0.0.0.0', 80))
server = subprocess.Popen([sys.executable, '-m', 'http.server', '80', '--bind', '0.0.0.0',
                           '--directory', str(webroot)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(.5)
    if server.poll() is not None:
        raise RuntimeError('Temporary ACME HTTP service could not start.')
    command = ['/opt/phone-camera-certbot/bin/certbot', 'certonly', '--non-interactive', '--agree-tos',
               '--register-unsafely-without-email', '--preferred-profile', 'shortlived', '--webroot',
               '--webroot-path', str(webroot), '--ip-address', ip, '--cert-name',
               'phone-camera-ip-staging' if args.staging else 'phone-camera-ip']
    if args.staging:
        command.append('--staging')
    subprocess.run(command, check=True)
finally:
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()
