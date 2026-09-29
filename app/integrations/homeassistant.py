"""Home Assistant MQTT Auto-Discovery and State Publishing integration."""

import json
import logging
import threading

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

log = logging.getLogger("warden.ha")


class HomeAssistantClient:
    def __init__(self, host: str, port: int = 1883, user: str = "", password: str = "",
                 topic_prefix: str = "warden", discovery_prefix: str = "homeassistant",
                 db=None, guard_engine=None):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.topic_prefix = topic_prefix
        self.discovery_prefix = discovery_prefix
        self.db = db
        self.guard_engine = guard_engine
        self._client = None
        self._connected = False
        self._lock = threading.Lock()

    def start(self) -> None:
        if not self.host or mqtt is None:
            log.info("Home Assistant MQTT integration disabled (no host configured)")
            return

        try:
            self._client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id="warden-service")
            if self.user:
                self._client.username_pw_set(self.user, self.password)

            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.connect_async(self.host, self.port, keepalive=60)
            self._client.loop_start()
            log.info("Started Home Assistant MQTT client connecting to %s:%d", self.host, self.port)
        except Exception as exc:
            log.error("Failed to initialize Home Assistant MQTT: %s", exc)

    def stop(self) -> None:
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            log.info("Connected to MQTT broker at %s:%d", self.host, self.port)
            self._connected = True
            # Subscribe to command topics
            client.subscribe(f"{self.topic_prefix}/+/+/set")
            self.publish_all_discovery()
        else:
            log.warning("MQTT connection failed with code %d", rc)

    def _on_message(self, client, userdata, msg):
        try:
            topic_parts = msg.topic.split("/")
            # Topic format: warden/{device_id}/{entity_type}/set
            if len(topic_parts) >= 4 and topic_parts[0] == self.topic_prefix:
                dev_id = int(topic_parts[1])
                entity = topic_parts[2]
                payload = msg.payload.decode("utf-8").strip()

                log.info("Received MQTT command on %s: %s", msg.topic, payload)

                if entity == "protection":
                    enabled = payload.upper() == "ON"
                    if self.db:
                        self.db.update_guard_settings(dev_id, enabled=enabled)
                elif entity == "snooze" and payload.upper() == "PRESS":
                    if self.guard_engine:
                        self.guard_engine.get_state(dev_id).snooze(1800)
                elif entity == "unsnooze" and payload.upper() == "PRESS":
                    if self.guard_engine:
                        self.guard_engine.get_state(dev_id).unsnooze()
        except Exception as exc:
            log.error("Error processing MQTT message on %s: %s", msg.topic, exc)

    def publish_all_discovery(self) -> None:
        if not self._connected or not self.db:
            return

        for dev in self.db.list_devices():
            self.publish_device_discovery(dev)

    def publish_device_discovery(self, dev: dict) -> None:
        if not self._connected or not self._client:
            return

        dev_id = dev["id"]
        dev_name = dev.get("name") or f"TV-{dev_id}"
        node_id = f"warden_tv_{dev_id}"

        ha_device = {
            "identifiers": [f"warden_device_{dev_id}"],
            "name": f"Warden - {dev_name}",
            "manufacturer": dev.get("identity", {}).get("manufacturer", "Android TV"),
            "model": dev.get("identity", {}).get("model", dev.get("connector", "ADB")),
            "sw_version": "0.1.0",
        }

        # 1. State Sensor
        state_sensor_topic = f"{self.discovery_prefix}/sensor/{node_id}_state/config"
        state_sensor_cfg = {
            "name": f"{dev_name} Warden State",
            "unique_id": f"{node_id}_state",
            "state_topic": f"{self.topic_prefix}/{dev_id}/state",
            "value_template": "{{ value_json.state }}",
            "device": ha_device,
            "icon": "mdi:shield-tv",
        }
        self._client.publish(state_sensor_topic, json.dumps(state_sensor_cfg), retain=True)

        # 2. Current Channel / Media Sensor
        media_sensor_topic = f"{self.discovery_prefix}/sensor/{node_id}_media/config"
        media_sensor_cfg = {
            "name": f"{dev_name} Current Media",
            "unique_id": f"{node_id}_media",
            "state_topic": f"{self.topic_prefix}/{dev_id}/state",
            "value_template": "{{ value_json.title or value_json.current_package or 'None' }}",
            "json_attributes_topic": f"{self.topic_prefix}/{dev_id}/state",
            "device": ha_device,
            "icon": "mdi:television-play",
        }
        self._client.publish(media_sensor_topic, json.dumps(media_sensor_cfg), retain=True)

        # 3. Protection Switch
        switch_topic = f"{self.discovery_prefix}/switch/{node_id}_protection/config"
        switch_cfg = {
            "name": f"{dev_name} Channel Protection",
            "unique_id": f"{node_id}_protection",
            "state_topic": f"{self.topic_prefix}/{dev_id}/state",
            "value_template": "{{ 'ON' if value_json.guard_enabled else 'OFF' }}",
            "command_topic": f"{self.topic_prefix}/{dev_id}/protection/set",
            "device": ha_device,
            "icon": "mdi:shield-check",
        }
        self._client.publish(switch_topic, json.dumps(switch_cfg), retain=True)

        # 4. Snooze Button (30m)
        snooze_btn_topic = f"{self.discovery_prefix}/button/{node_id}_snooze/config"
        snooze_btn_cfg = {
            "name": f"{dev_name} Snooze 30m",
            "unique_id": f"{node_id}_snooze",
            "command_topic": f"{self.topic_prefix}/{dev_id}/snooze/set",
            "device": ha_device,
            "icon": "mdi:alarm-snooze",
        }
        self._client.publish(snooze_btn_topic, json.dumps(snooze_btn_cfg), retain=True)

    def publish_state(self, dev: dict, state) -> None:
        if not self._connected or not self._client:
            return

        dev_id = dev["id"]
        guard_cfg = self.db.get_guard_settings(dev_id) if self.db else {}

        payload = {
            "device_id": dev_id,
            "state": state.state.value,
            "current_package": state.current_package,
            "title": state.current_media.title,
            "subtitle": state.current_media.subtitle,
            "is_playing": state.current_media.is_playing,
            "status_detail": state.status_detail,
            "guard_enabled": guard_cfg.get("enabled", True),
            "is_snoozed": state.is_snoozed,
            "snooze_remaining_s": state.snooze_remaining_s,
            "last_action": state.last_action_name,
            "last_matched_rule": state.last_matched_rule,
        }

        topic = f"{self.topic_prefix}/{dev_id}/state"
        self._client.publish(topic, json.dumps(payload), retain=False)
