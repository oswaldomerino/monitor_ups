"""Entry point for the UPS monitoring application."""

import asyncio

from monitor.web import create_app
from monitor.ups import monitor_ups

UPS_IPS = [
    {"ip": "10.104.154.136", "name": "UPS falsa"},
    {"ip": "10.105.37.27", "name": "UPS c20"},
    {"ip": "10.104.60.131", "name": "D07 Envasado"},
    {"ip": "10.104.63.145", "name": "D15 Oficinas L18"},
    {"ip": "10.104.63.144", "name": "d15"},
    {"ip": "10.104.61.244", "name": "UPS a10"},
    {"ip": "10.105.35.140", "name": "B06"},
    {"ip": "10.105.35.140", "name": "b06"},
]

MAX_LATENCY_DISCONNECTED = 60  # Latency threshold for disconnect status
DATA = {}

app, socketio = create_app(UPS_IPS, DATA)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(monitor_ups(UPS_IPS, DATA, socketio, MAX_LATENCY_DISCONNECTED))
    socketio.run(app, host="0.0.0.0", port=5000)
