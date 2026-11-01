# Independent TURN issuer

`install_ephemeral_turn.py` installs a separate systemd service named `phone-camera-turn.service` and an SSH-only credential issuer at `/usr/local/libexec/phone-camera-turn-credentials`. It does not modify an existing coturn configuration or service. The signing secret is generated on the server and stored in `/etc/phone-camera-turn/turnserver.conf` (root-owned, readable by the `coturn` group); it is not returned by the issuer.

Prerequisites: Linux with systemd, the `coturn` package and system account, Python 3, a stable public IPv4, and permission to open TCP/UDP 3479 and UDP 49224–49287 in the cloud security group and host firewall. The installer refuses to overwrite an existing service or paths and checks that port 3479 is free. Run as root on the target server:

```sh
python3 install_ephemeral_turn.py PUBLIC_IPV4
systemctl status phone-camera-turn.service
```

The issuer gives out TURN REST credentials valid for 12 hours. Keep root SSH restricted to trusted administrators. Anyone with a valid paired room can obtain the temporary credential, so treat the room code and QR code as private. For a session longer than 12 hours, restart capture to obtain new credentials.

To remove only this project's service after it is no longer needed, an administrator can run:

```sh
systemctl disable --now phone-camera-turn.service
rm -f /etc/systemd/system/phone-camera-turn.service
rm -f /usr/local/libexec/phone-camera-turn-credentials
rm -f /etc/phone-camera-turn/turnserver.conf
rmdir /etc/phone-camera-turn
systemctl daemon-reload
```

Revoke the associated cloud security-group rules separately if they are no longer needed. This does not uninstall coturn or stop the other coturn service.

## Fixed HTTPS page

The optional fixed page uses a separate nginx service on ports 80 and 443, and an SSH reverse tunnel bound to server loopback port 18765 while the computer is capturing. The web server serves the three files in `phone_web` independently; `/api/` is proxied to the computer through that loopback tunnel. Without an active room the page is available but pairing fails. Only one computer can own the fixed reverse port at a time.

For a public IPv4 without a domain, the provided scripts use Let's Encrypt's short-lived IP certificates. Install nginx, Python 3.11 and Certbot 5.4 or later into `/opt/phone-camera-certbot`. Put `index.html`, `phone.js` and `phone.css` under `/opt/phone-camera-web/staging`, then run `python3 issue_ip_certificate.py PUBLIC_IPV4` followed by `python3 install_fixed_site.py PUBLIC_IPV4` as root. Ports 80 and 443 must be free and publicly reachable. The installer creates a dedicated web service and certificate renewal timer without modifying the system nginx configuration. The timer checks renewal every six hours, serves ACME challenges from `/opt/phone-camera-web/acme`, and reloads the web service after renewal. Verify with:

```sh
systemctl status phone-camera-web.service phone-camera-cert-renew.timer
/opt/phone-camera-certbot/bin/certbot renew --cert-name phone-camera-ip --dry-run --run-deploy-hooks --non-interactive --no-random-sleep-on-renew --deploy-hook /usr/local/libexec/phone-camera-reload-web
```

The IP certificate lasts about six days, so the timer, port 80, and CA connectivity must remain working. Updating the phone webpage requires deploying the changed static assets to `/opt/phone-camera-web/www`; changing the desktop files alone does not update the server copy.
