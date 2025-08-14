"""Asynchronous monitoring helpers for UPS devices."""

import asyncio
from typing import Dict, List

from .network import ping_and_https_check
from .snmp import snmp_query


async def monitor_single_ups(ups: Dict[str, str], max_latency: float) -> Dict[str, str]:
    """Monitor a single UPS returning a dictionary of metrics."""
    result = {
        "ip": ups["ip"],
        "name": ups["name"],
        "status": "Desconectada",
        "latency": None,
        "battery_voltage": "N/A",
        "input_voltage": "N/A",
        "output_voltage": "N/A",
        "temperature": "N/A",
        "ping_response": "No respondido",
        "https_check": "No verificado",
    }

    try:
        ping_result = await ping_and_https_check(ups["ip"], max_latency)
        snmp_tasks = {
            "battery_voltage": snmp_query(ups["ip"], ".1.3.6.1.2.1.33.1.2.4.0"),
            "input_voltage": snmp_query(ups["ip"], ".1.3.6.1.2.1.33.1.2.5.0"),
            "output_voltage": snmp_query(ups["ip"], ".1.3.6.1.4.1.318.1.4.2.2.1.3.1"),
            "temperature": snmp_query(ups["ip"], ".1.3.6.1.2.1.33.1.2.7.0"),
        }
        snmp_results = await asyncio.gather(*snmp_tasks.values(), return_exceptions=True)
        result.update(ping_result)
        result["battery_voltage"] = snmp_results[0]
        result["input_voltage"] = snmp_results[1]
        result["output_voltage"] = snmp_results[2]
        result["temperature"] = snmp_results[3]
        if result["ping_response"] == "Ping exitoso":
            result["status"] = "Conectada"
    except Exception as e:
        result["status"] = f"Error: {e}"
    return result


async def monitor_ups(ups_list: List[Dict[str, str]], data_store: Dict[str, Dict[str, str]], socketio, max_latency: float) -> None:
    """Continuously monitor all UPS devices and emit updates via Socket.IO."""
    while True:
        tasks = [monitor_single_ups(ups, max_latency) for ups in ups_list]
        results = await asyncio.gather(*tasks)
        data_store.clear()
        data_store.update({res["ip"]: res for res in results})
        socketio.emit("update_data", data_store)
        await asyncio.sleep(5)
