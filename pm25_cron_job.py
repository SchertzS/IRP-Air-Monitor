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
Also reads temperature and humidity from a DHT11 sensor.
Author: StephenS
Date: 2024-06-15
Version: 0.1.0

Dependencies:
- adafruit-circuitpython-pm25
- adafruit-circuitpython-dht
- pyserial
- RPi.GPIO
- board
- adafruit-blinka
- Python 3.x

Usage:
- Ensure the PM2.5 sensor is connected to the UART pins.
- Ensure the DHT11 sensor is connected to GPIO pin 4 (D4).
- Run this script as a cron job at desired intervals (e.g., every 10 minutes).
- The script will manage a buffer of readings and write to CSV after reaching a threshold.
- The built-in LED will blink to indicate data reading and writing status.


"""

import csv
import json
import os
import socket
import time
from datetime import datetime, timezone

import RPi.GPIO as GPIO
import adafruit_dht
import board
import paho.mqtt.client as mqtt
import serial
from adafruit_pm25.uart import PM25_UART

# ==== MQTT CONFIG (point to Pi 5 / HA broker) ====
MQTT_HOST = os.getenv("MQTT_HOST", "pi5.local")  # or IP
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DEVICE_ID = os.getenv("DEVICE_ID", "aqnode-zero2w-01")
BASE_TOPIC = f"aq/{DEVICE_ID}"  # e.g., aq/aqnode-zero2w-01


# ==== CONFIG ====
SET_PIN = 17  # GPIO pin to control sensor SET
BUFFER_PATH = "/home/username/pm25_buffer.json"
CSV_PATH = "/home/username/air_quality_log.csv"
CSV_PATH_FAILED_READS = "/home/username/failed_reads.csv"
WRITE_THRESHOLD = 6  # Write to CSV after 6 readings
LED_PATH = "/sys/class/leds/ACT/brightness"
BROKER_HOST = "pi5.local"  # or static IP
BROKER_PORT = 1883
DEVICE_ID = "aqnode-zero2w-01"
BASE_TOPIC = f"aq/{DEVICE_ID}"

#  === LOGGING INITIALIZATION ====
print("\n\n################################") # log separator
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

client = mqtt.Client(client_id=f"{DEVICE_ID}-pub", protocol=mqtt.MQTTv311)
client.will_set(f"{BASE_TOPIC}/status", payload="offline", qos=1, retain=True)


def connect():
    # Retry connect w/ backoff
    while True:
        try:
            client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
            client.loop_start()
            client.publish(f"{BASE_TOPIC}/status", "online", qos=1, retain=True)
            break
        except Exception:
            time.sleep(5)


def publish_reading(pm25, pm10, pm100, t_c=None, rh=None):
    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "timestamp": ts,
        "pm25": pm25, "pm10": pm10, "pm100": pm100,
        "temperature_c": t_c, "humidity_pct": rh,
        "host": socket.gethostname()
    }
    # 1) Running timeseries
    client.publish(f"{BASE_TOPIC}/reading", json.dumps(payload), qos=1, retain=False)
    # 2) A retained "latest" snapshot
    client.publish(f"{BASE_TOPIC}/latest", json.dumps(payload), qos=1, retain=True)


# ==== UART SETUP ====
try:
    # uart = serial.Serial("/dev/ttyAMA0", baudrate=9600, timeout=0.25)
    # uart = serial.Serial("/dev/ttyS0", baudrate=9600, timeout=0.25)
    uart = serial.Serial("/dev/serial0", baudrate=9600, timeout=0.25)
    pm25 = PM25_UART(uart, None)
    # dhtDevice = adafruit_dht.DHT11(board.D4)
    # DEBUG statement to check if sensor is working
    print(f"UART: {uart} | PM25: {pm25}")
    print(f"DHT: {dht_device}")
except serial.SerialException as e:
    print(f"[ERROR] Failed to open UART: {e}")
    exit(1)


# ==== TEMPERATURE MONITORING ====
def cpu_temp_c():
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        return int(f.read().strip()) / 1000.0

# ==== SENSOR SETUP / POWER CONTROL ====
def wake_sensor():
    """
    Wake the PM2.5 sensor by setting the SET_PIN high and allowing time for warm-up.
    :return: none
    """
    GPIO.output(SET_PIN, GPIO.HIGH)
    print("\nSensor waking up...")
    print(".\n.\n.\n.\n.")  # logging
    time.sleep(5)  # sensor warm up: @5 seconds

def sleep_sensor():
    """
    Put the PM2.5 sensor to sleep by setting the SET_PIN low.
    :return: none
    """
    GPIO.output(SET_PIN, GPIO.LOW)
    print("Sensor put to sleep")

# ==== LED CONTROL ====
def blink_builtin_led(times=1, duration=1):
    """
    Blink the built-in LED a specified number of times for an on-site quick visual
    data confirmation.
    :param times: Number of blinks
    :param duration: Length of each blink in seconds
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
    Flash the built-in LED quickly a specified number of times for an on-site quick visual
    :param times: 4
    :param duration: 0.1s
    :return:
    """
    for _ in range(times):
        with open(LED_PATH, "w") as led:
            led.write("1")
        time.sleep(duration)
        with open(LED_PATH, "w") as led:
            led.write("0")
        time.sleep(duration)


# ==== SENSOR READING & DATA HANDLING ====
def read_sensor(retries=10, delay=2):
    """
    Attempt to read data from the PM2.5 and DHT11 sensors with retries and delay.
    :param retries: 10
    :param delay: 2 seconds
    :return: List of readings [timestamp, cpu_temp, pm1.0, pm2.5, pm10.0, temperature_f, humidity] or None if failed
    """
    print("read_sensor()")
    for i in range(retries):
        try:
            time.sleep(delay)  # sensor warm up: @2 seconds
            print(".\n.")  # "." represents delay in seconds for logging
            time.sleep(delay)  # sensor warm up: @2 seconds
            print(".\n.")  # logging

            # ==== READ DATA ====
            print("Attempting to read data...")
            flash_builtin_led()  # LED flashes for data read
            data = pm25.read()
            temperature_c = dht_device.temperature
            temperature_f = temperature_c * (9 / 5) + 32
            humidity = dht_device.humidity
            cpu_temp = cpu_temp_c()
            print(f"Successfully read sensor on attempt: {i + 1}/{retries}")

            time.sleep(delay)  # sensor pause: @2 seconds
            print(".\n.")  # logging
            time.sleep(delay)  # sensor pause: @2 seconds
            print(".\n.")  # logging

            # some DHT return None if timing was off
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            return [timestamp,
                    cpu_temp,
                    data["pm10 standard"],
                    data["pm25 standard"],
                    data["pm100 standard"],
                    temperature_f,
                    humidity]
        except RuntimeError as e:
            # logging
            print(f"Sensor read failed (attempt {i + 1}/{retries}): {e}")
            print(f"Retrying...")
            time.sleep(delay)
            print(".\n.")  # logging
    return None

#==== BUFFER MANAGEMENT (short term storage) ====
"""
Buffer management using a JSON file to store readings temporarily before writing to CSV.
The purpose is to minimize frequent writes to the SD card, extending its lifespan.

"""
def load_buffer():
    """
    Load the buffer from a JSON file. If the file doesn't exist or is corrupted, return an empty list.
    :return: List of buffered readings
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
    Save the current buffer to a JSON file.
    :param buffer: List of readings to save
    :return: none
    """
    with open(BUFFER_PATH, "w") as f:
        json.dump(buffer, f)

# ==== SAVE TO CSV (long-term storage) ====
def write_to_csv(data_list):
    """
    Write a list of readings to the CSV file. Each reading is a list of [timestamp, pm1.0, pm2.5, pm10.0].
    :param data_list: List of readings to write
    :return: none
    """

    # Ensure CSV file exists, if not create and add header
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.writer(f)
        # ROWS: Data
        writer.writerows(data_list)
    os.sync()
    # Logging
    print(f"Wrote {len(data_list)} readings to {CSV_PATH}")


# ==== MQTT PUBLISHING ====
def mqtt_client():
    """
    Initialize and return an MQTT client with Last Will set.
    :return:
    """
    c = mqtt.Client(client_id=f"{DEVICE_ID}-pub", protocol=mqtt.MQTTv311)
    # Last Will tells HA that we went offline if the process dies
    c.will_set(f"{BASE_TOPIC}/status", payload="offline", qos=1, retain=True)
    return c


def mqtt_connect(c: mqtt.Client):
    """
    Connect to the MQTT broker with retries and exponential backoff.
    :param c: mqtt.Client
    :return:
    """
    backoff = 2
    while True:
        try:
            c.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            c.loop_start()
            # we’re online now
            c.publish(f"{BASE_TOPIC}/status", "online", qos=1, retain=True)
            print(f"[MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}")
            return c
        except Exception as e:
            print(f"[MQTT] connect failed: {e} (retrying in {backoff}s)")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


def mqtt_publish_reading(c: mqtt.Client, payload: dict):
    """
    Publish the sensor reading to both a streaming topic and a retained snapshot topic.
    :param c: mqtt.Client
    :param payload: dict with sensor data
    :return:
    """
    # 1) streaming topic (not retained)
    c.publish(f"{BASE_TOPIC}/reading", json.dumps(payload), qos=1, retain=False)
    # 2) snapshot topic (retained) so HA always has the latest
    c.publish(f"{BASE_TOPIC}/latest", json.dumps(payload), qos=1, retain=True)


def mqtt_publish_discovery(c: mqtt.Client):
    """
    Publish HA discovery configs so entities show up automatically.
    We bind them to the retained 'latest' snapshot.
    :param c: mqtt.Client
    :return: none
    """
    disc_base = f"homeassistant/sensor/{DEVICE_ID}"
    device = {
        "identifiers": [DEVICE_ID],
        "name": "Air Quality Node",
        "model": "Pi Zero 2 W",
        "manufacturer": "Custom",
    }

    sensors = [
        # (key, name, unit, value_json_field)
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


""" ==== MAIN ==== """
try:
    wake_sensor()
    reading = read_sensor()
    sleep_sensor()

    if not reading:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"No reading. Skipping write.\nTime: {timestamp}")
        with open(CSV_PATH_FAILED_READS, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(timestamp)
        print(f"Wrote failed reading timestamp ({timestamp}) to {CSV_PATH_FAILED_READS}")
        os.sync()
        exit()

    print(f"Sensor reading: {reading}")

    # ==== MQTT PUBLISH ====
    client = mqtt_connect(mqtt_client())
    payload = {
        "timestamp": reading[0],
        "cpu_temp_c": reading[1],  # you logged °C for CPU (correct from cpu_temp_c)
        "pm10_standard": reading[2],
        "pm25_standard": reading[3],
        "pm100_standard": reading[4],
        "temperature_f": reading[5],
        "humidity_percent": reading[6],
        "host": socket.gethostname(),
    }
    mqtt_publish_reading(client, payload)

    buffer = load_buffer()
    buffer.append(reading)
    print(f"\nBUFFER:")
    for _,value in enumerate(buffer):
        print(f"{_}.", value)
    # blink for saving buffer
    blink_builtin_led(2)

    if len(buffer) >= WRITE_THRESHOLD:
        write_to_csv(buffer)
        blink_builtin_led(3)
        buffer = []

    save_buffer(buffer)

    # Debugging
    if buffer:
        if len(buffer) < 5:
            # print(f"Last reading: {buffer[-1]}")
            print(f"\nCURRENT THRESHOLD: {len(buffer)}/6")
        elif len(buffer) == 5:
            print(f"\nCURRENT THRESHOLD: {len(buffer)}/6")
            print(f"\nPreparing write data to .csv next read...")
        else:
            print("BUFFER is empty")

except Exception as e:
    print(f"[ERROR] Unexpected error occurred: {e}")