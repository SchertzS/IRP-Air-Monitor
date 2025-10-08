"""
           _
     /\   (_)
    /  \   _ _ __
   / /\ \ | | '__|
  / ____ \| | |
 /_/  __\_\_|_|      _ _
 |  \/  |           (_) |
 | \  / | ___  _ __  _| |_ ___  _ __
 | |\/| |/ _ \| '_ \| | __/ _ \| '__|
 | |  | | (_) | | | | | || (_) | |
 |_|__|_|\___/|_| |_|_|\__\___/|_|
  / ____|         | |
 | (___  _   _ ___| |_ ___ _ __ ___
  \___ \| | | / __| __/ _ \ '_ ` _ \
  ____) | |_| \__ \ ||  __/ | | | | |
 |_____/ \__, |___/\__\___|_| |_| |_|
          __/ |
         |___/

Script to read from a PM2.5 sensor via UART on a Raspberry Pi Zero W.
Includes buffer management to minimize SD card writes, and logs data to a CSV file.
Also reads temperature and humidity from a DHT11 sensor, and publishes to MQTT
with Home Assistant auto-discovery.

Author: StephenS
Start Date: 2024-06-15
Latest Date: 2025-10-07
Version: 0.2.0
"""

import csv
import json
import os
import socket
import time

import RPi.GPIO as GPIO
import adafruit_dht
import board
import paho.mqtt.client as mqtt
import serial
from adafruit_pm25.uart import PM25_UART

# ==== MQTT CONFIG (point to Pi 5 / HA broker) ====
MQTT_HOST = os.getenv("MQTT_HOST", "pi5.local")  # or static IP
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DEVICE_ID = os.getenv("DEVICE_ID", "device_id")  # e.g., aqnode-zero2w-01
BASE_TOPIC = f"aq/{DEVICE_ID}"  # e.g., aq/aqnode-zero2w-01

# ==== PATHS / IO CONFIG ====
SET_PIN = 17  # GPIO pin to control sensor SET
BUFFER_PATH = "/home/username/pm25_buffer.json"
CSV_PATH = "/home/username/air_quality_log.csv"
CSV_PATH_FAILED_READS = "/home/username/failed_reads.csv"
WRITE_THRESHOLD = 6  # Write to CSV after 6 readings
LED_PATH = "/sys/class/leds/ACT/brightness"

#  === LOGGING INITIALIZATION ====
print("\n\n################################")
print(f"Time: {time.asctime()}")
print(f"\nInitialize configuration: ")
print(f"BUFFER PATH: {BUFFER_PATH}")
print(f"CSV PATH: {CSV_PATH}")
print(f"DATA SAVE THRESHOLD: {WRITE_THRESHOLD}")

# ==== GPIO SETUP ====
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(SET_PIN, GPIO.OUT)
dht_device = adafruit_dht.DHT11(board.D4, use_pulseio=False)

# ==== UART SETUP ====
try:
    # Pick the one that matches your setup; serial0 usually points to the active UART
    uart = serial.Serial("/dev/serial0", baudrate=9600, timeout=0.25)
    pm25 = PM25_UART(uart, None)
    print(f"UART: {uart} | PM25: {pm25}")
    print(f"DHT: {dht_device}")
except serial.SerialException as e:
    print(f"[ERROR] Failed to open UART: {e}")
    raise SystemExit(1)

# ==== TEMPERATURE MONITORING ====
def cpu_temp_c():
    """
    Reads the CPU temperature from the system file.
    :return: CPU temperature in Celsius.
    """
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        return int(f.read().strip()) / 1000.0

# ==== SENSOR POWER CONTROL ====
def wake_sensor():
    """
    Wakes up the PM2.5 sensor by setting the SET_PIN high.
    :return: None
    """
    GPIO.output(SET_PIN, GPIO.HIGH)
    print("\nSensor waking up...")
    print(".\n.\n.\n.\n.")
    time.sleep(5)  # warm-up

def sleep_sensor():
    """
    Puts the PM2.5 sensor to sleep by setting the SET_PIN low.
    :return: None
    """
    GPIO.output(SET_PIN, GPIO.LOW)
    print("Sensor put to sleep")

# ==== LED CONTROL ====
def blink_builtin_led(times=1, duration=1):
    """
    Blink the built-in LED a specified number of times with a given duration.
    :param times: 1
    :param duration: 1 second
    :return: None
    """
    for _ in range(times):
        with open(LED_PATH, "w") as led:
            led.write("1")
        time.sleep(duration)
        with open(LED_PATH, "w") as led:
            led.write("0")
        time.sleep(duration)

def flash_builtin_led(times=4, duration=0.1):
    """
    Flash the built-in LED quickly to indicate activity.
    :param times: 4
    :param duration: 0.1 second
    :return: None
    """
    for _ in range(times):
        with open(LED_PATH, "w") as led:
            led.write("1")
        time.sleep(duration)
        with open(LED_PATH, "w") as led:
            led.write("0")
        time.sleep(duration)

# ==== SENSOR READ ====
def read_sensor(retries=10, delay=2):
    """
    Attempts to read data from the PM2.5 sensor and DHT11 sensor with retries.
    Data example: [timestamp, cpu_temp_c, pm10, pm25, pm100, ambient_temp_f, humidity]
    Output example: ['2024-06-15 12:34:56', 45.2, 12, 8, 15, 72.5, 55]

    total sleep time = (delay * 2) + (delay * 2) = 8 seconds per attempt
    10 attempts = 80 seconds max


    :param retries: Number of read attempts
    :param delay: Delay between attempts in seconds
    :return: List of readings or None if all attempts fail
    """
    print("read_sensor()")
    for i in range(retries):
        try:
            # Initial delay before read (4 second delay total)
            time.sleep(delay);
            print(".\n.")
            time.sleep(delay);
            print(".\n.")

            print("Attempting to read data...")
            flash_builtin_led()
            data = pm25.read()

            # DHT can return None occasionally; guard it
            t_c = dht_device.temperature
            h = dht_device.humidity
            ambient_temp_f = (t_c * 9 / 5 + 32) if (t_c is not None) else None
            humidity = h if (h is not None) else None

            cpu_t = cpu_temp_c()
            print(f"Successfully read sensor on attempt: {i + 1}/{retries}")

            # Second delay after read (4 second delay total)
            time.sleep(delay);
            print(".\n.")
            time.sleep(delay);
            print(".\n.")

            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            return [
                timestamp,
                cpu_t,
                data["pm10 standard"],
                data["pm25 standard"],
                data["pm100 standard"],
                ambient_temp_f,
                humidity,
            ]
        except RuntimeError as e:
            print(f"Sensor read failed (attempt {i + 1}/{retries}): {e}")
            print("Retrying...")
            time.sleep(delay);
            print(".\n.")
    return None

# ==== BUFFER MANAGEMENT ====
def load_buffer():
    """
    Loads the buffer from a JSON file if it exists; otherwise, returns an empty list.
    :return: Either a list of buffered readings or an empty list.
    """
    if os.path.exists(BUFFER_PATH):
        try:
            with open(BUFFER_PATH, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("[WARN] Buffer file corrupted or empty. Starting fresh.")
            return []
    return []

def save_buffer(buffer):
    """
    Saves the current buffer to a JSON file.
    :param buffer: List of buffered readings.
    :return: None
    """
    with open(BUFFER_PATH, "w") as f:
        json.dump(buffer, f)

# ==== CSV WRITE ====
def write_to_csv(data_list):
    """
    Appends a list of readings to the CSV file.
    :param data_list: List of readings to write.
    :return: None
    """
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(data_list)
    os.sync()
    print(f"Wrote {len(data_list)} readings to {CSV_PATH}")

# ==== MQTT HELPERS ====
def mqtt_client():
    """
    Initializes and returns an MQTT client with LWT configured.
    :return: MQTT client instance.
    """
    c = mqtt.Client(client_id=f"{DEVICE_ID}-pub", protocol=mqtt.MQTTv311)
    c.username_pw_set(MQTT_USER, MQTT_PASS)

    # c.will_set(f"{BASE_TOPIC}/status", payload="offline", qos=1, retain=True)
    return c

def mqtt_connect(c: mqtt.Client):
    """
    Connects to the MQTT broker with exponential backoff on failure.
    :param c: MQTT client instance.
    :return:  Connected MQTT client instance.
    """
    backoff = 2
    while True:
        try:
            c.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            c.loop_start()
            c.publish(f"{BASE_TOPIC}/status", "online", qos=1, retain=True)
            print(f"[MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}")
            return c
        except Exception as e:
            print(f"[MQTT] connect failed: {e} (retrying in {backoff}s)")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

def mqtt_publish_reading(c: mqtt.Client, payload: dict):
    """
    Publishes the sensor reading to MQTT topics.
    :param c: MQTT client instance.
    :param payload: Dictionary containing sensor data.
    :return: None
    """
    c.publish(f"{BASE_TOPIC}/reading", json.dumps(payload), qos=1, retain=False)
    c.publish(f"{BASE_TOPIC}/latest", json.dumps(payload), qos=1, retain=True)

def mqtt_publish_discovery(c: mqtt.Client):
    """
    Publishes Home Assistant MQTT discovery configuration for the sensors.
    :param c: MQTT client instance.
    :return: None
    """
    disc_base = f"homeassistant/sensor/{DEVICE_ID}"
    device = {
        "identifiers": [DEVICE_ID],
        "name": "Air Quality Node",
        "model": "Pi Zero 2 W",
        "manufacturer": "Custom",
    }
    sensors = [
        ("pm25", "PM2.5", "µg/m³", "pm25_standard"),
        ("pm10", "PM10", "µg/m³", "pm10_standard"),
        ("pm100", "PM100", "µg/m³", "pm100_standard"),
        ("temp", "Ambient Temp", "°F", "temperature_f"),
        ("humid", "Humidity", "%", "humidity_percent"),
        ("cput", "CPU Temp", "°C", "cpu_temp_c"),
    ]
    for key, name, unit, field in sensors:
        cfg_topic = f"{disc_base}/{key}/config"
        cfg = {
            "name": name,
            "state_topic": f"{BASE_TOPIC}/latest",
            "unit_of_measurement": unit,
            "value_template": f"{{{{ value_json.{field} }}}}",
            "unique_id": f"{DEVICE_ID}_{key}",
            "device": device,
            "availability_topic": f"{BASE_TOPIC}/status",
            "payload_available": "online",
            "payload_not_available": "offline",
        }
        c.publish(cfg_topic, json.dumps(cfg), qos=1, retain=True)
    print("[MQTT] Published HA discovery")

# ==== MAIN ====
try:
    wake_sensor()
    reading = read_sensor()
    sleep_sensor()

    if not reading:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"No reading. Skipping write.\nTime: {timestamp}")
        with open(CSV_PATH_FAILED_READS, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([timestamp, "failed"])  # fixed
        os.sync()
        raise SystemExit(0)

    print(f"Sensor reading: {reading}")

    # Buffer → CSV batch
    buffer = load_buffer()
    buffer.append(reading)
    blink_builtin_led(2)
    print("\nBUFFER:")
    for i, value in enumerate(buffer):
        print(f"{i}. {value}")

    if len(buffer) >= WRITE_THRESHOLD:
        write_to_csv(buffer)
        blink_builtin_led(3)
        buffer = []

    print("\nSaving buffer...")
    save_buffer(buffer)

    # BUFFER LOGS
    if buffer:
        if len(buffer) < WRITE_THRESHOLD:
            print(f"\nCURRENT THRESHOLD: {len(buffer)}/{WRITE_THRESHOLD}")
        elif len(buffer) == WRITE_THRESHOLD - 1:
            print(f"\nCURRENT THRESHOLD: {len(buffer)}/{WRITE_THRESHOLD}")
            print("\nPreparing write data to .csv next read...")
    else:
        print("BUFFER is empty")

    # MQTT: connect → publish discovery (once) → publish reading
    client = mqtt_connect(mqtt_client())
    mqtt_publish_discovery(client)  # call this so HA auto-creates entities

    payload = {
        "timestamp": reading[0],
        "cpu_temp_c": reading[1],
        "pm10_standard": reading[2],
        "pm25_standard": reading[3],
        "pm100_standard": reading[4],
        "temperature_f": reading[5],
        "humidity_percent": reading[6],
        "host": socket.gethostname(),
    }
    mqtt_publish_reading(client, payload)


except Exception as e:
    print(f"[ERROR] Unexpected error occurred: {e}")

"""
TODO
- add most recent timestamp reading as an entity in HA
                        - or -
- add overall logs as an entity in HA
"""
