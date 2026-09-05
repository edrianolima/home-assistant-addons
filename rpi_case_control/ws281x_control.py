import json
import logging
import os
import socket
import textwrap
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt
import psutil
from rpi_ws281x import Color, PixelStrip

try:
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
    from luma.oled.device import ssd1306
    from PIL import ImageFont
except Exception:
    i2c = None
    canvas = None
    ssd1306 = None
    ImageFont = None

OLED_MODES = ("stats", "clock", "custom", "cycle", "off")
OLED_CYCLE_MODES = ("stats", "clock")
OLED_TEXT_MAX_LEN = 120
# /data is the only directory the Supervisor keeps across restarts and updates.
OLED_STATE_FILE = "/data/oled_state.json"
SUPERVISOR_NETWORK_URL = "http://supervisor/network/info"
HOST_IP_TTL_SEC = 300

try:
    from periphery import GPIO as PeripheryGPIO
except Exception:
    PeripheryGPIO = None


@dataclass
class AppConfig:
    mqtt_host: str = "core-mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str = "mqtt"
    mqtt_password: str = ""
    mqtt_topic_prefix: str = "led2mqtt/light/ws281x"
    mqtt_discovery: bool = True
    mqtt_discovery_prefix: str = "homeassistant"
    led_count: int = 8
    led_pin: int = 18
    led_freq_hz: int = 800000
    led_dma: int = 10
    led_brightness: int = 255
    led_invert: bool = False
    led_channel: int = 0
    oled_enabled: bool = True
    oled_i2c_bus: int = 1
    oled_i2c_address: str = "0x3c"
    oled_rotate: int = 0
    oled_interval_sec: int = 3
    oled_mode: str = "stats"
    oled_cycle_sec: int = 10
    state_publish_interval_sec: int = 30
    metrics_publish_interval_sec: int = 30
    fan_enabled: bool = True
    fan_pin: int = 13
    fan_pwm_freq_hz: int = 50
    fan_invert: bool = False
    fan_auto_mode: bool = True
    fan_temp_min_c: int = 42
    fan_temp_max_c: int = 65
    fan_min_percent: int = 25
    fan_max_percent: int = 100
    log_level: str = "INFO"


class UnifiedController:
    def __init__(self, config: AppConfig) -> None:
        self.cfg = config
        self.log = logging.getLogger("ws281x_unified")
        self.strip: Optional[PixelStrip] = None
        self.oled = None
        self.fan_available = False
        self.fan_gpio = None
        self.fan_pwm_thread: Optional[threading.Thread] = None
        self.fan_pwm_stop = threading.Event()
        self.fan_pwm_lock = threading.Lock()
        self._fan_duty = 0
        self.last_oled = 0.0
        self.last_state_publish = 0.0
        self.last_metrics_publish = 0.0
        self.last_fan_state_publish = 0.0

        self.topic_cmd = f"{self.cfg.mqtt_topic_prefix}/cmd"
        self.topic_state = f"{self.cfg.mqtt_topic_prefix}/state"
        self.topic_availability = f"{self.cfg.mqtt_topic_prefix}/availability"
        self.topic_metrics_prefix = f"{self.cfg.mqtt_topic_prefix}/metrics"
        self.topic_fan_cmd = f"{self.cfg.mqtt_topic_prefix}/fan/cmd"
        self.topic_fan_pct_cmd = f"{self.cfg.mqtt_topic_prefix}/fan/percentage_cmd"
        self.topic_fan_mode_cmd = f"{self.cfg.mqtt_topic_prefix}/fan/mode_cmd"
        self.topic_fan_state = f"{self.cfg.mqtt_topic_prefix}/fan/state"
        self.topic_fan_pct_state = f"{self.cfg.mqtt_topic_prefix}/fan/percentage_state"
        self.topic_fan_mode_state = f"{self.cfg.mqtt_topic_prefix}/fan/mode_state"
        self.discovery_topic = (
            f"{self.cfg.mqtt_discovery_prefix}/light/ws281x_tower/config"
        )
        self.discovery_fan_topic = (
            f"{self.cfg.mqtt_discovery_prefix}/fan/ws281x_tower_fan/config"
        )
        self.discovery_sensor_base = (
            f"{self.cfg.mqtt_discovery_prefix}/sensor/ws281x_tower"
        )
        self.topic_oled_mode_cmd = f"{self.cfg.mqtt_topic_prefix}/oled/mode_cmd"
        self.topic_oled_mode_state = f"{self.cfg.mqtt_topic_prefix}/oled/mode_state"
        self.topic_oled_text_cmd = f"{self.cfg.mqtt_topic_prefix}/oled/text_cmd"
        self.topic_oled_text_state = f"{self.cfg.mqtt_topic_prefix}/oled/text_state"
        self.discovery_oled_mode_topic = (
            f"{self.cfg.mqtt_discovery_prefix}/select/ws281x_tower_oled_mode/config"
        )
        self.discovery_oled_text_topic = (
            f"{self.cfg.mqtt_discovery_prefix}/text/ws281x_tower_oled_text/config"
        )
        self.device_info = {
            "ids": ["ws281x_tower_case"],
            "name": "GeeekPi Tower Case",
            "mf": "GeeekPi",
            "mdl": "Mini Tower for Raspberry Pi 4",
            "sw": "ws281x_led_control",
        }

        # `color_mode` is what lets Home Assistant read `color` under the JSON
        # schema; without it the colour wheel stays empty however often the
        # colour is published.
        self.state: Dict[str, Any] = {
            "state": "OFF",
            "color_mode": "rgb",
            "color": {"r": 255, "g": 255, "b": 255},
            "brightness": self.cfg.led_brightness,
        }
        self.fan_state: Dict[str, Any] = {
            "state": "ON",
            "percentage": max(0, min(100, self.cfg.fan_min_percent)),
            "preset_mode": "auto" if self.cfg.fan_auto_mode else "manual",
        }
        self.oled_state: Dict[str, str] = self._load_oled_state()
        self.oled_cycle_index = 0
        self.last_oled_cycle = 0.0
        self._oled_fonts: Dict[int, Any] = {}
        self._host_ip: Optional[str] = None
        self._host_ip_read = 0.0

        self.mqtt = mqtt.Client()
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_disconnect = self._on_disconnect
        self.mqtt.on_message = self._on_message
        self.mqtt.reconnect_delay_set(min_delay=2, max_delay=20)
        if self.cfg.mqtt_username:
            self.mqtt.username_pw_set(self.cfg.mqtt_username, self.cfg.mqtt_password)

    def setup(self) -> None:
        self._setup_leds()
        self._setup_oled()
        self._setup_fan()
        self._connect_mqtt()

    def _setup_leds(self) -> None:
        try:
            self.strip = PixelStrip(
                self.cfg.led_count,
                self.cfg.led_pin,
                self.cfg.led_freq_hz,
                self.cfg.led_dma,
                self.cfg.led_invert,
                self.cfg.led_brightness,
                self.cfg.led_channel,
            )
            self.strip.begin()
            self._apply_led_state()
            self.log.info("WS281x initialized on GPIO %s", self.cfg.led_pin)
        except Exception as exc:
            self.strip = None
            self.log.exception("WS281x initialization failed: %s", exc)

    def _setup_oled(self) -> None:
        if not self.cfg.oled_enabled:
            self.log.info("OLED disabled by configuration")
            return
        if not all([i2c, canvas, ssd1306]):
            self.log.warning("OLED libraries are not available")
            return
        try:
            address = int(str(self.cfg.oled_i2c_address), 0)
            serial = i2c(port=self.cfg.oled_i2c_bus, address=address)
            self.oled = ssd1306(serial, rotate=self.cfg.oled_rotate)
            self.log.info(
                "OLED initialized on i2c-%s addr=%s",
                self.cfg.oled_i2c_bus,
                self.cfg.oled_i2c_address,
            )
        except Exception as exc:
            self.oled = None
            self.log.exception("OLED initialization failed: %s", exc)

    def _setup_fan(self) -> None:
        if not self.cfg.fan_enabled:
            self.log.info("Fan disabled by configuration")
            return
        if PeripheryGPIO is None:
            self.log.warning("python-periphery is not available; fan control disabled")
            return
        try:
            self.fan_gpio = PeripheryGPIO("/dev/gpiochip0", self.cfg.fan_pin, "out")
            self.fan_available = True
            self.fan_pwm_stop.clear()
            self.fan_pwm_thread = threading.Thread(
                target=self._fan_pwm_loop, name="fan-pwm", daemon=True
            )
            self.fan_pwm_thread.start()
            self.log.info(
                "Fan control initialized on GPIO %s (%sHz)",
                self.cfg.fan_pin,
                self.cfg.fan_pwm_freq_hz,
            )
        except Exception as exc:
            self.fan_available = False
            self.log.exception("Fan initialization failed: %s", exc)

    def _gpio_write(self, high: bool) -> None:
        if self.fan_gpio is None:
            return
        value = bool(high)
        if self.cfg.fan_invert:
            value = not value
        self.fan_gpio.write(value)

    def _fan_pwm_loop(self) -> None:
        freq = max(1, int(self.cfg.fan_pwm_freq_hz))
        period = 1.0 / float(freq)
        while not self.fan_pwm_stop.is_set():
            with self.fan_pwm_lock:
                duty = max(0, min(100, int(self._fan_duty)))
            try:
                if duty <= 0:
                    self._gpio_write(False)
                    time.sleep(0.2)
                    continue
                if duty >= 100:
                    self._gpio_write(True)
                    time.sleep(0.2)
                    continue
                on_time = period * (duty / 100.0)
                off_time = max(period - on_time, 0.0005)
                self._gpio_write(True)
                time.sleep(on_time)
                self._gpio_write(False)
                time.sleep(off_time)
            except Exception as exc:
                self.log.exception("Fan PWM loop failed: %s", exc)
                time.sleep(1.0)

    def _connect_mqtt(self) -> None:
        self.log.info(
            "Connecting to MQTT broker %s:%s", self.cfg.mqtt_host, self.cfg.mqtt_port
        )
        self.mqtt.connect(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=60)
        self.mqtt.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.log.info("MQTT connected: rc=%s", reason_code)
        client.subscribe(self.topic_cmd)
        client.subscribe(self.topic_fan_cmd)
        client.subscribe(self.topic_fan_pct_cmd)
        client.subscribe(self.topic_fan_mode_cmd)
        client.subscribe(self.topic_oled_mode_cmd)
        client.subscribe(self.topic_oled_text_cmd)
        client.publish(self.topic_availability, "online", retain=True)
        if self.cfg.mqtt_discovery:
            self._publish_discovery()
            self._publish_fan_discovery()
            if self.cfg.fan_enabled and not self.fan_available:
                client.publish(self.discovery_fan_topic, "", retain=True)
            self._publish_sensor_discovery()
            self._publish_oled_discovery()
        self._publish_state(force=True)
        self._publish_fan_state(force=True)
        self._publish_oled_state()
        self._publish_metrics(force=True)

    def _on_disconnect(self, client, userdata, reason_code, properties=None):
        self.log.warning("MQTT disconnected: rc=%s", reason_code)

    def _on_message(self, client, userdata, msg):
        if msg.topic in {
            self.topic_fan_cmd,
            self.topic_fan_pct_cmd,
            self.topic_fan_mode_cmd,
        }:
            self._handle_fan_message(msg)
            return
        if msg.topic in {self.topic_oled_mode_cmd, self.topic_oled_text_cmd}:
            self._handle_oled_message(msg)
            return
        payload_text = msg.payload.decode("utf-8", errors="ignore").strip()
        updates: Dict[str, Any] = {}

        if payload_text in {"ON", "OFF"}:
            updates["state"] = payload_text
        else:
            try:
                data = json.loads(payload_text)
            except json.JSONDecodeError:
                self.log.warning("Ignoring invalid payload: %s", payload_text)
                return

            if isinstance(data, dict):
                updates = data
            else:
                self.log.warning("Ignoring non-object payload: %s", payload_text)
                return

        self._merge_state(updates)
        self._apply_led_state()
        self._publish_state(force=True)

    def _handle_fan_message(self, msg) -> None:
        payload = msg.payload.decode("utf-8", errors="ignore").strip()
        if msg.topic == self.topic_fan_cmd:
            if payload.upper() in {"ON", "OFF"}:
                self.fan_state["state"] = payload.upper()
                if (
                    self.fan_state["state"] == "ON"
                    and int(self.fan_state["percentage"]) <= 0
                ):
                    self.fan_state["percentage"] = max(1, self.cfg.fan_min_percent)
        elif msg.topic == self.topic_fan_pct_cmd:
            try:
                self.fan_state["percentage"] = max(0, min(100, int(float(payload))))
                self.fan_state["state"] = (
                    "OFF" if int(self.fan_state["percentage"]) == 0 else "ON"
                )
                self.fan_state["preset_mode"] = "manual"
            except Exception:
                self.log.warning("Ignoring invalid fan percentage payload: %s", payload)
                return
        elif msg.topic == self.topic_fan_mode_cmd:
            mode = payload.lower()
            if mode in {"auto", "manual"}:
                self.fan_state["preset_mode"] = mode
            else:
                self.log.warning("Ignoring invalid fan mode payload: %s", payload)
                return
        self._apply_fan_state()
        self._publish_fan_state(force=True)

    def _load_oled_state(self) -> Dict[str, str]:
        """Read the mode and text back after a restart or an add-on update.

        `oled_mode` in the add-on options only seeds the very first run; once
        the mode has been changed from Home Assistant the stored value wins.
        """
        default_mode = (
            self.cfg.oled_mode if self.cfg.oled_mode in OLED_MODES else "stats"
        )
        try:
            with open(OLED_STATE_FILE, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {"mode": default_mode, "text": ""}
        if not isinstance(stored, dict):
            return {"mode": default_mode, "text": ""}
        mode = stored.get("mode")
        text = stored.get("text")
        return {
            "mode": mode if mode in OLED_MODES else default_mode,
            "text": text[:OLED_TEXT_MAX_LEN] if isinstance(text, str) else "",
        }

    def _save_oled_state(self) -> None:
        try:
            with open(OLED_STATE_FILE, "w", encoding="utf-8") as handle:
                json.dump(self.oled_state, handle)
        except OSError as exc:
            self.log.warning("Could not persist OLED state: %s", exc)

    def _handle_oled_message(self, msg) -> None:
        payload = msg.payload.decode("utf-8", errors="ignore").strip()
        if msg.topic == self.topic_oled_mode_cmd:
            if payload not in OLED_MODES:
                self.log.warning("Ignoring unknown OLED mode: %s", payload)
                return
            self.oled_state["mode"] = payload
        else:
            self.oled_state["text"] = payload[:OLED_TEXT_MAX_LEN]
            # Writing text with the screen showing something else looks broken,
            # so the text box doubles as a way into custom mode.
            if self.oled_state["text"]:
                self.oled_state["mode"] = "custom"
        self._save_oled_state()
        self._publish_oled_state()
        self._render_oled()

    def _publish_oled_discovery(self) -> None:
        if not self.oled:
            return
        common = {
            "avty_t": self.topic_availability,
            "pl_avail": "online",
            "pl_not_avail": "offline",
            "device": self.device_info,
            "ent_cat": "config",
        }
        self.mqtt.publish(
            self.discovery_oled_mode_topic,
            json.dumps({
                "name": "OLED Mode",
                "uniq_id": "ws281x_tower_oled_mode",
                "cmd_t": self.topic_oled_mode_cmd,
                "stat_t": self.topic_oled_mode_state,
                "options": list(OLED_MODES),
                "icon": "mdi:monitor-dashboard",
                **common,
            }),
            retain=True,
        )
        self.mqtt.publish(
            self.discovery_oled_text_topic,
            json.dumps({
                "name": "OLED Text",
                "uniq_id": "ws281x_tower_oled_text",
                "cmd_t": self.topic_oled_text_cmd,
                "stat_t": self.topic_oled_text_state,
                "max": OLED_TEXT_MAX_LEN,
                "icon": "mdi:form-textbox",
                **common,
            }),
            retain=True,
        )

    def _publish_oled_state(self) -> None:
        self.mqtt.publish(
            self.topic_oled_mode_state, self.oled_state["mode"], retain=True
        )
        self.mqtt.publish(
            self.topic_oled_text_state, self.oled_state["text"], retain=True
        )

    def _publish_discovery(self) -> None:
        payload = {
            "name": "WS281x Tower Light",
            "uniq_id": "ws281x_tower_light",
            "schema": "json",
            "cmd_t": self.topic_cmd,
            "stat_t": self.topic_state,
            "avty_t": self.topic_availability,
            "pl_avail": "online",
            "pl_not_avail": "offline",
            "brightness": True,
            "supported_color_modes": ["rgb"],
            "device": self.device_info,
        }
        self.mqtt.publish(self.discovery_topic, json.dumps(payload), retain=True)

    def _publish_fan_discovery(self) -> None:
        if not self.cfg.fan_enabled or not self.fan_available:
            return
        payload = {
            "name": "Tower Fan",
            "uniq_id": "ws281x_tower_fan",
            "cmd_t": self.topic_fan_cmd,
            "stat_t": self.topic_fan_state,
            "pct_cmd_t": self.topic_fan_pct_cmd,
            "pct_stat_t": self.topic_fan_pct_state,
            "pr_mode_cmd_t": self.topic_fan_mode_cmd,
            "pr_mode_stat_t": self.topic_fan_mode_state,
            "pr_modes": ["auto", "manual"],
            "avty_t": self.topic_availability,
            "pl_avail": "online",
            "pl_not_avail": "offline",
            "pl_on": "ON",
            "pl_off": "OFF",
            "device": self.device_info,
        }
        self.mqtt.publish(self.discovery_fan_topic, json.dumps(payload), retain=True)

    def _sensor_definitions(self) -> Dict[str, Dict[str, str]]:
        return {
            "cpu_temp": {
                "name": "CPU Temperature",
                "unit": "°C",
                "dev_cla": "temperature",
                "stat_cla": "measurement",
            },
            "load_1m": {
                "name": "CPU Load 1m",
                "unit": "",
                "icon": "mdi:gauge",
                "stat_cla": "measurement",
            },
            "ram_used_percent": {
                "name": "RAM Used",
                "unit": "%",
                "icon": "mdi:memory",
                "stat_cla": "measurement",
            },
            "disk_used_percent": {
                "name": "Disk Used",
                "unit": "%",
                "icon": "mdi:harddisk",
                "stat_cla": "measurement",
            },
            "ip_address": {
                "name": "IP Address",
                "unit": "",
                "icon": "mdi:ip-network",
                "ent_cat": "diagnostic",
            },
            # Boot time rather than elapsed seconds: the `duration` device class
            # must not change through time passing alone, and the frontend renders
            # a timestamp as "3 days ago" instead of a six-digit second count.
            "last_boot": {
                "name": "Last Boot",
                "dev_cla": "timestamp",
                "icon": "mdi:timer-outline",
                "ent_cat": "diagnostic",
            },
        }

    def _publish_sensor_discovery(self) -> None:
        for key, meta in self._sensor_definitions().items():
            topic = f"{self.discovery_sensor_base}_{key}/config"
            state_topic = f"{self.topic_metrics_prefix}/{key}"
            payload: Dict[str, Any] = {
                "name": meta["name"],
                "uniq_id": f"ws281x_tower_{key}",
                "stat_t": state_topic,
                "avty_t": self.topic_availability,
                "pl_avail": "online",
                "pl_not_avail": "offline",
                "device": self.device_info,
            }
            if meta.get("unit"):
                payload["unit_of_meas"] = meta["unit"]
            if meta.get("dev_cla"):
                payload["dev_cla"] = meta["dev_cla"]
            if meta.get("stat_cla"):
                payload["stat_cla"] = meta["stat_cla"]
            if meta.get("icon"):
                payload["icon"] = meta["icon"]
            if meta.get("ent_cat"):
                payload["ent_cat"] = meta["ent_cat"]
            self.mqtt.publish(topic, json.dumps(payload), retain=True)

    def _merge_state(self, updates: Dict[str, Any]) -> None:
        if "state" in updates and updates["state"] in {"ON", "OFF"}:
            self.state["state"] = updates["state"]

        if "brightness" in updates:
            self.state["brightness"] = max(0, min(255, int(updates["brightness"])))

        if "color" in updates and isinstance(updates["color"], dict):
            color = self.state["color"].copy()
            for channel in ("r", "g", "b"):
                if channel in updates["color"]:
                    color[channel] = max(0, min(255, int(updates["color"][channel])))
            self.state["color"] = color

    def _apply_led_state(self) -> None:
        if not self.strip:
            return
        if self.state["state"] == "OFF":
            color = Color(0, 0, 0)
        else:
            rgb = self.state["color"]
            brightness = self.state["brightness"]
            color = Color(
                int(rgb["r"] * brightness / 255),
                int(rgb["g"] * brightness / 255),
                int(rgb["b"] * brightness / 255),
            )
        for i in range(self.strip.numPixels()):
            self.strip.setPixelColor(i, color)
        self.strip.show()

    def _publish_state(self, force: bool = False) -> None:
        now = time.time()
        if (
            not force
            and now - self.last_state_publish < self.cfg.state_publish_interval_sec
        ):
            return
        self.last_state_publish = now
        self.mqtt.publish(self.topic_state, json.dumps(self.state), retain=True)

    def _auto_fan_percentage(self) -> int:
        temp_c = self._read_cpu_temp_c()
        if temp_c is None:
            return max(0, min(100, self.cfg.fan_min_percent))
        t_min = float(self.cfg.fan_temp_min_c)
        t_max = float(self.cfg.fan_temp_max_c)
        if t_max <= t_min:
            t_max = t_min + 1.0
        p_min = max(0, min(100, int(self.cfg.fan_min_percent)))
        p_max = max(p_min, min(100, int(self.cfg.fan_max_percent)))
        if temp_c <= t_min:
            return p_min
        if temp_c >= t_max:
            return p_max
        ratio = (temp_c - t_min) / (t_max - t_min)
        return int(p_min + ratio * (p_max - p_min))

    def _apply_fan_state(self) -> None:
        if not self.fan_available:
            return
        if self.fan_state["state"] != "ON":
            duty = 0
        elif self.fan_state["preset_mode"] == "auto":
            duty = self._auto_fan_percentage()
            self.fan_state["percentage"] = duty
        else:
            duty = max(0, min(100, int(self.fan_state["percentage"])))
        with self.fan_pwm_lock:
            self._fan_duty = duty

    def _publish_fan_state(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_fan_state_publish < self.cfg.state_publish_interval_sec:
            return
        self.last_fan_state_publish = now
        self.mqtt.publish(self.topic_fan_state, self.fan_state["state"], retain=True)
        self.mqtt.publish(
            self.topic_fan_pct_state,
            str(int(self.fan_state["percentage"])),
            retain=True,
        )
        self.mqtt.publish(self.topic_fan_mode_state, self.fan_state["preset_mode"], retain=True)

    def _read_cpu_temp_c(self) -> Optional[float]:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as f:
                value = int(f.read().strip())
            return round(value / 1000, 1)
        except Exception:
            return None

    def _collect_metrics(self) -> Dict[str, Any]:
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        load1, _, _ = os.getloadavg()
        cpu_temp = self._read_cpu_temp_c()
        if cpu_temp is None:
            cpu_temp = 0.0
        return {
            "cpu_temp": cpu_temp,
            "load_1m": round(load1, 2),
            "ram_used_percent": round(mem.percent, 1),
            "disk_used_percent": round(disk.percent, 1),
            "ip_address": self._get_ip(),
            "last_boot": datetime.fromtimestamp(
                psutil.boot_time(), tz=timezone.utc
            ).replace(microsecond=0).isoformat(),
        }

    def _publish_metrics(self, force: bool = False) -> None:
        now = time.time()
        if (
            not force
            and now - self.last_metrics_publish < self.cfg.metrics_publish_interval_sec
        ):
            return
        self.last_metrics_publish = now
        for key, value in self._collect_metrics().items():
            topic = f"{self.topic_metrics_prefix}/{key}"
            self.mqtt.publish(topic, str(value), retain=True)

    def _read_cpu_temp(self) -> str:
        value = self._read_cpu_temp_c()
        if value is None:
            return "n/a"
        return f"{value:.1f}C"

    def _get_ip(self) -> str:
        now = time.time()
        if self._host_ip and now - self._host_ip_read < HOST_IP_TTL_SEC:
            return self._host_ip
        self._host_ip_read = now
        self._host_ip = self._supervisor_ip() or self._container_ip()
        return self._host_ip

    @staticmethod
    def _supervisor_ip() -> Optional[str]:
        """The address the host answers on, which is the one worth displaying.

        Asking the container itself returns its 172.30.x.x bridge address —
        correct, and useless to anyone reading the panel.
        """
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            return None
        request = urllib.request.Request(
            SUPERVISOR_NETWORK_URL, headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                interfaces = json.load(response)["data"]["interfaces"]
        except Exception:
            logging.debug("Supervisor network info unavailable", exc_info=True)
            return None
        for interface in interfaces:
            if not interface.get("primary"):
                continue
            for address in (interface.get("ipv4") or {}).get("address", []):
                return address.split("/")[0]
        return None

    @staticmethod
    def _container_ip() -> str:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            sock.close()
            return ip
        except Exception:
            return "n/a"

    def _render_oled(self) -> None:
        if not self.oled:
            return
        mode = self.oled_state["mode"]
        if mode == "cycle":
            mode = self._current_cycle_mode()
        if mode == "off":
            self.oled.clear()
            return
        if mode == "clock":
            self._draw_clock()
            return
        if mode == "custom":
            self._draw_custom()
            return
        self._draw_stats()

    def _current_cycle_mode(self) -> str:
        now = time.time()
        if now - self.last_oled_cycle >= self.cfg.oled_cycle_sec:
            self.last_oled_cycle = now
            self.oled_cycle_index = (self.oled_cycle_index + 1) % len(OLED_CYCLE_MODES)
        return OLED_CYCLE_MODES[self.oled_cycle_index]

    def _stats_lines(self) -> list:
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        load1, _, _ = os.getloadavg()
        return [
            f"Temp {self._read_cpu_temp()}",
            f"Load {load1:.2f}",
            f"RAM  {mem.percent:.0f}%",
            f"Disk {disk.percent:.0f}%",
            f"IP {self._get_ip()}",
        ]

    def _draw_stats(self) -> None:
        """Spread the lines over the panel, centred within their own slot.

        A fixed size clipped the first and last lines, because five lines of
        an 11 px face are taller than the 64 px the SSD1306 has.
        """
        lines = self._stats_lines()
        with canvas(self.oled) as draw:
            font = self._fit_lines(draw, lines)
            slot = self.oled.height / len(lines)
            top = max(0, (slot - self._glyph_height(font)) / 2)
            for index, line in enumerate(lines):
                draw.text((2, top + index * slot), line, font=font, fill="white")

    def _fit_lines(self, draw, lines: list):
        """Largest size at which every line fits the panel without wrapping."""
        slot = self.oled.height / len(lines)
        usable_width = self.oled.width - 4
        for size in (14, 13, 12, 11, 10, 9, 8):
            font = self._oled_font(size)
            if self._glyph_height(font) > slot:
                continue
            if all(draw.textlength(line, font=font) <= usable_width for line in lines):
                return font
        return self._oled_font(8)

    def _draw_custom(self) -> None:
        """Centre the message and scale it to whatever the panel can hold.

        A short message deserves the whole 128x64; the same code left at one
        fixed size puts three words in a corner and looks broken.
        """
        text = self.oled_state["text"] or "(no text set)"
        with canvas(self.oled) as draw:
            font, lines = self._fit_text(draw, text)
            line_height = self._line_height(font)
            top = max(0, (self.oled.height - line_height * len(lines)) // 2)
            for index, line in enumerate(lines):
                self._draw_centered(draw, line, top + index * line_height, font)

    def _fit_text(self, draw, text: str):
        """Largest font size at which the text still fits the panel."""
        for size in (30, 24, 19, 15, 12, 10):
            font = self._oled_font(size)
            char_width = draw.textlength("n", font=font) or 1
            lines = textwrap.wrap(text, width=max(1, int(self.oled.width / char_width)))
            if not lines:
                continue
            fits_height = self._line_height(font) * len(lines) <= self.oled.height
            fits_width = all(
                draw.textlength(line, font=font) <= self.oled.width for line in lines
            )
            if fits_height and fits_width:
                return font, lines
        smallest = self._oled_font(10)
        return smallest, textwrap.wrap(text, width=24)[:5]

    @staticmethod
    def _glyph_height(font) -> int:
        return font.getbbox("Ag")[3]

    @classmethod
    def _line_height(cls, font) -> int:
        return cls._glyph_height(font) + 2

    def _draw_clock(self) -> None:
        now = datetime.now()
        with canvas(self.oled) as draw:
            self._draw_centered(draw, now.strftime("%H:%M"), 6, self._oled_font(28))
            self._draw_centered(
                draw, now.strftime("%a %d %b"), 42, self._oled_font(13)
            )

    def _draw_centered(self, draw, text: str, y: int, font) -> None:
        width = draw.textlength(text, font=font)
        draw.text(((self.oled.width - width) / 2, y), text, font=font, fill="white")

    def _oled_font(self, size: int):
        """Pillow gained a size argument on load_default() in 10.1; older
        builds fall back to the fixed bitmap face rather than failing."""
        if ImageFont is None:
            return None
        if size not in self._oled_fonts:
            try:
                self._oled_fonts[size] = ImageFont.load_default(size=size)
            except TypeError:
                self._oled_fonts[size] = ImageFont.load_default()
        return self._oled_fonts[size]

    def run(self) -> None:
        self.setup()
        self._apply_fan_state()
        try:
            while True:
                now = time.time()
                self._publish_state(force=False)
                self._apply_fan_state()
                self._publish_fan_state(force=False)
                self._publish_metrics(force=False)
                if self.oled and now - self.last_oled >= self.cfg.oled_interval_sec:
                    self.last_oled = now
                    try:
                        self._render_oled()
                    except Exception as exc:
                        self.log.exception("OLED render failed: %s", exc)
                time.sleep(0.2)
        except KeyboardInterrupt:
            self.log.info("Stopping controller")
        finally:
            self.mqtt.publish(self.topic_availability, "offline", retain=True)
            self.mqtt.loop_stop()
            self.fan_pwm_stop.set()
            if self.fan_available:
                try:
                    with self.fan_pwm_lock:
                        self._fan_duty = 0
                    self._gpio_write(False)
                except Exception:
                    pass
            if self.fan_gpio is not None:
                try:
                    self.fan_gpio.close()
                except Exception:
                    pass
            if self.strip:
                for i in range(self.strip.numPixels()):
                    self.strip.setPixelColor(i, Color(0, 0, 0))
                self.strip.show()


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in {"true", "1", "yes", "on"}:
            return True
        if value.lower() in {"false", "0", "no", "off"}:
            return False
    return default


def load_config() -> AppConfig:
    options: Dict[str, Any] = {}
    try:
        with open("/data/options.json", "r", encoding="utf-8") as fp:
            options = json.load(fp)
    except FileNotFoundError:
        pass
    except Exception as exc:
        logging.getLogger("ws281x_unified").warning("Could not read options.json: %s", exc)

    return AppConfig(
        mqtt_host=str(options.get("mqtt_host", "core-mosquitto")),
        mqtt_port=_to_int(options.get("mqtt_port", 1883), 1883),
        mqtt_username=str(options.get("mqtt_username", "mqtt")),
        mqtt_password=str(options.get("mqtt_password", "")),
        mqtt_topic_prefix=str(options.get("mqtt_topic_prefix", "led2mqtt/light/ws281x")),
        mqtt_discovery=_to_bool(options.get("mqtt_discovery", True), True),
        mqtt_discovery_prefix=str(options.get("mqtt_discovery_prefix", "homeassistant")),
        led_count=_to_int(options.get("led_count", 8), 8),
        led_pin=_to_int(options.get("led_pin", 18), 18),
        led_freq_hz=_to_int(options.get("led_freq_hz", 800000), 800000),
        led_dma=_to_int(options.get("led_dma", 10), 10),
        led_brightness=_to_int(options.get("led_brightness", 255), 255),
        led_invert=_to_bool(options.get("led_invert", False), False),
        led_channel=_to_int(options.get("led_channel", 0), 0),
        oled_enabled=_to_bool(options.get("oled_enabled", True), True),
        oled_i2c_bus=_to_int(options.get("oled_i2c_bus", 1), 1),
        oled_i2c_address=str(options.get("oled_i2c_address", "0x3c")),
        oled_rotate=_to_int(options.get("oled_rotate", 0), 0),
        oled_interval_sec=_to_int(options.get("oled_interval_sec", 3), 3),
        oled_mode=str(options.get("oled_mode", "stats")),
        oled_cycle_sec=_to_int(options.get("oled_cycle_sec", 10), 10),
        state_publish_interval_sec=_to_int(
            options.get("state_publish_interval_sec", 30), 30
        ),
        metrics_publish_interval_sec=_to_int(
            options.get("metrics_publish_interval_sec", 30), 30
        ),
        fan_enabled=_to_bool(options.get("fan_enabled", True), True),
        fan_pin=_to_int(options.get("fan_pin", 13), 13),
        fan_pwm_freq_hz=_to_int(options.get("fan_pwm_freq_hz", 50), 50),
        fan_invert=_to_bool(options.get("fan_invert", False), False),
        fan_auto_mode=_to_bool(options.get("fan_auto_mode", True), True),
        fan_temp_min_c=_to_int(options.get("fan_temp_min_c", 42), 42),
        fan_temp_max_c=_to_int(options.get("fan_temp_max_c", 65), 65),
        fan_min_percent=_to_int(options.get("fan_min_percent", 25), 25),
        fan_max_percent=_to_int(options.get("fan_max_percent", 100), 100),
        log_level=str(options.get("log_level", "INFO")),
    )


def main() -> None:
    config = load_config()
    log_level = getattr(logging, str(config.log_level).upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    UnifiedController(config).run()


if __name__ == "__main__":
    main()
