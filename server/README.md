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
