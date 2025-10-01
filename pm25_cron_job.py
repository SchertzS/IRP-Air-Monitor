"""
Script to read from a PM2.5 sensor via UART on a Raspberry Pi Zero W.
"""

import os
import csv
import json
import time
from adafruit_pm25.uart import PM25_UART
import serial
import RPi.GPIO as GPIO


# ==== CONFIG ====
SET_PIN = 17  # GPIO pin to control sensor SET
BUFFER_PATH = "/home/raspberryzero-two/pm25_buffer.json"
CSV_PATH = "/home/raspberryzero-two/air_quality_log.csv"
WRITE_THRESHOLD = 6  # Write to CSV after 6 readings
LED_PATH = "/sys/class/leds/ACT/brightness"

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


# ==== UART SETUP ====
try:
    # uart = serial.Serial("/dev/ttyAMA0", baudrate=9600, timeout=0.25)
    # uart = serial.Serial("/dev/ttyS0", baudrate=9600, timeout=0.25)
    uart = serial.Serial("/dev/serial0", baudrate=9600, timeout=0.25)
    pm25 = PM25_UART(uart, None)
    # DEBUG statement to check if sensor is working
    print(f"UART: {uart} | PM25: {pm25}")
except serial.SerialException as e:
    print(f"[ERROR] Failed to open UART: {e}")
    exit(1)


# ==== SENSOR SETUP / POWER CONTROL ====
def wake_sensor():
    """
    Wake the PM2.5 sensor by setting the SET_PIN high and allowing time for warm-up.
    :return: none
    """
    GPIO.output(SET_PIN, GPIO.HIGH)
    print("\nSensor waking up...")
    time.sleep(5)  # Allow sensor to warm up

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

# ==== SENSOR READING & DATA HANDLING ====
def read_sensor(retries=5, delay=2):
    """
    Attempt to read from the PM2.5 sensor with specified retries.
    :param retries: Number of read attempts
    :param delay: Delay between attempts in seconds
    :return: List of [timestamp, pm1.0, pm2.5, pm10.0] or None on failure
    """
    for i in range(retries):
        try:
            time.sleep(delay)
            data = pm25.read()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            blink_builtin_led(1)  # LED blink on success
            print(f"Successfully read sensor on (attempt {i + 1}/{retries})")
            return [timestamp,
                    data["pm10 standard"],
                    data["pm25 standard"],
                    data["pm100 standard"]]
        except RuntimeError as e:
            # Logging
            print(f"Sensor read failed (attempt {i+1}/{retries}): {e}")
            print(f"Retrying...")
            time.sleep(delay)
            print(e)
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

""" ==== MAIN ==== """
try:
    wake_sensor()
    reading = read_sensor()
    sleep_sensor()

    if not reading:
        print("No reading. Skipping write.")
        exit()

    print(f"Sensor reading: {reading}")
    buffer = load_buffer()
    buffer.append(reading)
    print(f"\nBUFFER:")
    for _,value in enumerate(buffer):
        print(f"{_}.", value)
    blink_builtin_led(2)

    if len(buffer) >= WRITE_THRESHOLD:
        write_to_csv(buffer)
        blink_builtin_led(3)
        buffer = []

    save_buffer(buffer)

    # Debugging
    if buffer:
        # print(f"Last reading: {buffer[-1]}")
        print(f"\nCURRENT THRESHOLD: {len(buffer)}")
    else:
        print("BUFFER is empty")

except Exception as e:
    print(f"[ERROR] Unexpected error occurred: {e}")