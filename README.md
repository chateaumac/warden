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
- **Auto-Advance / Skip**: Automatically advances past restricted channels (e.g. Fox News on YouTube TV) using remote key emulation (`KEYCODE_CHANNEL_UP` or D-pad sequences) instead of just crashing the app.
- **Multi-Action Engine**: Supports `auto_skip`, `force_stop`, `back` (x2), `home`, and `mute`.
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
      - WARDEN_GUARD_INTERVAL=1.2
      # Optional Home Assistant MQTT integration
      - MQTT_HOST=10.10.10.50
      - MQTT_PORT=1883
      # Optional ntfy alerting
      - NOTIFY_URL=https://ntfy.sh/my-homelab-alerts
    # network_mode: host # Optional: if mDNS scanning across VLANs requires host mode

volumes:
  warden_data:
```

Access the Web UI at `http://<host-ip>:8484`.

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
