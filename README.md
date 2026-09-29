# Warden 🛡️
### Intelligent Governance, Privacy Sanitization, and Channel Guard for Smart TVs

Warden is a unified, low-overhead device governance service for Android TV and Google TV devices (Chromecast, TCL, Sony Bravia, NVIDIA SHIELD). It operates over the local network via ADB and SSH with **zero software installed on the TVs**.

---

## 🎯 Key Capabilities

### 1. 🛡️ Device Privacy & Anti-ACR Sanitization
- **Kills ACR (Automatic Content Recognition)**: Disables Samba Interactive TV (`tv.samba.ssm`), Qterics analytics, and vendor telemetry.
- **Eliminates Recommendation Trackers**: Disables `com.google.android.tvrecommendations` and background viewing trackers.
- **Blocks Launcher Ads**: Neutralizes sponsored rows and promotional banners (`com.google.android.tvlauncher.ads`).
- **Forces OS-Level Private DNS (DoT)**: Enforces encrypted DNS sinkholes (AdGuard Home, NextDNS, Pi-hole DoT) at the Android OS resolver level, defeating app-level DNS bypass.
- **Scheduled Drift Enforcement**: Automatically re-applies debloat settings when TV firmware updates silently re-enable them.

### 2. 🚫 Real-Time Channel Guard (Content Moderation)
- **Zero Flash Wear**: Completely eliminates disk-writing UI dumps (`/sdcard/ui.xml`), preserving TV flash memory and eliminating playback stutter on low-power devices (Chromecast / TCL).
- **Exact Channel Blocking**: Rules list channel names exactly as YouTube TV reports them (e.g. `FOX News`, `FOX Business`, `LiveNOW from FOX`), so FS1 or local FOX stations are unaffected. Regex patterns over program title + channel remain available.
- **Tune Away**: The `tune` action switches YouTube TV to a channel you pick via its `tv.youtube.com/watch/<id>` link (on tv.youtube.com → Live, right-click a channel → Open in new tab). Warden confirms where the TV landed and falls back to Home with an alert if the link has gone stale.
- **Multi-Action Engine**: Supports `tune`, `auto_skip`, `force_stop`, `back` (x2), `home`, and `mute`. Note that YouTube TV on TCL Google TVs ignores channel/D-pad keys, so `auto_skip` has no effect there.
- **Light Polling**: One ~2 KB `dumpsys media_session` read per poll (default every 30s, configurable per device) — no window or power dumps during playback.
- **Smart Standby & Offline Handling**: Gracefully detects when TCL/Chromecast TVs go to sleep or turn off, backing off polling to avoid connection errors or CPU waste.

### 3. 🔍 Live Diagnostic Payload Inspector
- Real-time diagnostic viewer in the web UI to inspect the exact `dumpsys media_session` and `dumpsys window` payloads emitted by YouTube TV for live streams and guide browsing.
- Interactive regex pattern tester to validate rules before saving.

### 4. 🏠 Home Assistant Integration (MQTT)
- Auto-discovery entities for each TV:
  - **Sensors**: TV Power/Guard State (`monitoring`, `idle`, `standby`, `offline`), Active Channel / Media Title, Last Blocked Event.
  - **Switches**: Channel Protection Toggle.
  - **Buttons**: 30-Minute Snooze button.

---

## 🚀 Quickstart

### 1. Run with Docker Compose

```yaml
services:
  warden:
    image: ghcr.io/chateaumac/warden:latest
    container_name: warden
    restart: unless-stopped
    ports:
      - "8484:8484"
    volumes:
      - warden_data:/data
    environment:
      - WARDEN_AUDIT_INTERVAL=900
      # Required: pick an auth mode (see "Security" below and .env.example)
      - WARDEN_AUTH_MODE=basic
      - WARDEN_BASIC_USER=admin
      - WARDEN_BASIC_PASSWORD_HASH=${WARDEN_BASIC_PASSWORD_HASH}
      # Optional Home Assistant MQTT integration
      - MQTT_HOST=192.168.1.50
      - MQTT_PORT=1883
      # Optional ntfy alerting
      - NOTIFY_URL=https://ntfy.sh/my-homelab-alerts
    # network_mode: host # Optional: if mDNS scanning across VLANs requires host mode

volumes:
  warden_data:
```

Generate the password hash first (it prompts for the password):

```bash
docker run --rm -it ghcr.io/chateaumac/warden:latest python -m app.auth hash-password
```

Access the Web UI at `http://<host-ip>:8484`.

---

## 🔐 Security

Warden holds an ADB key that can run shell commands on every TV that authorized it, so:

- **Authentication is mandatory.** `WARDEN_AUTH_MODE` must be `oidc` (Authentik, Keycloak, any OpenID Connect provider), `basic`, or an explicit `none`; Warden will not start otherwise. Every path — UI, static files, API, `/docs`, `/metrics` — requires login except `/healthz`.
- **OIDC**: register a confidential client with redirect URI `<WARDEN_PUBLIC_URL>/auth/callback`, then set `WARDEN_OIDC_ISSUER`, `WARDEN_OIDC_CLIENT_ID`, `WARDEN_OIDC_CLIENT_SECRET`, `WARDEN_PUBLIC_URL` and `WARDEN_SESSION_SECRET`. `WARDEN_OIDC_ALLOWED_GROUPS` limits login to members of those groups.
- **CSRF**: state-changing requests must carry `X-Requested-With: warden` (the web UI does), so other web pages open on your LAN cannot drive your TVs.
- **ADB key**: stored `0600` in a `0700` directory and refused if other users can read it. Set `WARDEN_ADB_KEY_FILE` to supply it from a secret store so TV authorizations survive rebuilds, and `WARDEN_ADB_KEYGEN=false` so a missing key fails loudly instead of silently minting a new identity.
- **Network**: only the Warden host needs to reach the TVs' ADB port (TCP 5555). Firewall it from everything else, and after setup use **Revoke USB debugging authorizations** on each TV so only Warden's key is trusted.
- The container runs as UID 10001. When upgrading a volume created by an older (root) image, `chown -R 10001:10001` it first.

---

## 📺 TV Setup (One-Time)

1. On your Google TV / Android TV:
   - Go to **Settings → System → About** (or **Device Preferences → About**).
   - Scroll to **Android TV OS build** and click it **7 times** until it says *"You are now a developer!"*.
2. In **Settings → System → Developer options**:
   - Enable **USB debugging** (and **Network debugging** / **Wireless debugging** if shown).
3. In the Warden Web Dashboard:
   - Click **＋ Add device** and enter the TV's IP address.
   - Look at the TV screen: check **"Always allow from this computer"** and select **Allow**.
   - Warden will establish pairing and permanently reuse its persistent RSA key.

---

## 📦 Bundled Profiles

- `google-tv.yaml` (Chromecast with Google TV, Google TV Streamer, TCL Google TVs)
- `sony-bravia.yaml` (Sony Bravia Android TVs with Samba ACR removal)
- `nvidia-shield.yaml` (NVIDIA SHIELD TV debloat & launcher cleanup)
- `allwinner-photo-frame.yaml` (Frameo digital frames privacy hardening)
- `generic-android-tv.yaml` (Stock Android TV)
- `linux-ssh-generic.yaml` (Linux devices via SSH)

---

## 📄 License

Warden is free software, licensed under the [GNU General Public License v3.0](LICENSE).
Security issues: see [SECURITY.md](SECURITY.md).
