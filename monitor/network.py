"""Network validation helpers."""

import asyncio
import socket

async def ping_and_https_check(ip: str, max_latency: float = 60) -> dict:
    """Ping the IP and verify that HTTPS port 443 is open.

    Returns a dictionary with keys ``ping_response``, ``https_check`` and ``latency``.
    """
    result = {"ping_response": "No respondido", "https_check": "No verificado", "latency": None}
    try:
        ping_output = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "1", ip,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await ping_output.communicate()
        if ping_output.returncode == 0:
            latency = float(stdout.decode().split("time=")[1].split("ms")[0])
            result["latency"] = latency
            result["ping_response"] = "Ping exitoso" if latency < max_latency else "Alta latencia"
        else:
            result["ping_response"] = "Ping fallido"
    except Exception:
        result["ping_response"] = "Ping fallido"

    try:
        with socket.create_connection((ip, 443), timeout=1):
            result["https_check"] = "Puerto HTTPS abierto"
    except socket.error:
        result["https_check"] = "Puerto HTTPS cerrado"

    return result
