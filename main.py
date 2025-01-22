import asyncio
import subprocess
import socket
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import pysnmp.hlapi.asyncio as snmp

# Configuración inicial
app = Flask(__name__)
socketio = SocketIO(app, async_mode='eventlet')

# Configuración
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

SNMP_COMMUNITY = "speyburn"
MAX_LATENCY_DISCONNECTED = 60  # Latencia máxima para marcar desconectado

DATA = {}  # Global dictionary to store UPS data

# Consultar SNMP de forma asíncrona
async def snmp_query(ip, oid):
    try:
        iterator = snmp.getCmd(
            snmp.SnmpEngine(),
            snmp.CommunityData("public"),
            snmp.UdpTransportTarget((ip, 161)),
            snmp.ContextData(),
            snmp.ObjectType(snmp.ObjectIdentity(oid))
        )
        errorIndication, errorStatus, errorIndex, varBinds = await iterator

        if errorIndication:
            return "Error"
        elif errorStatus:
            return "Error"
        else:
            for varBind in varBinds:
                return str(varBind[1])
    except Exception as e:
        return "Error"

# Función para hacer ping y verificar el puerto HTTPS
async def ping_and_https_check(ip):
    result = {"ping_response": "No respondido", "https_check": "No verificado", "latency": None}
    try:
        ping_output = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "1", ip,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await ping_output.communicate()

        if ping_output.returncode == 0:
            latency = float(stdout.decode().split("time=")[1].split("ms")[0])
            result["latency"] = latency
            result["ping_response"] = "Ping exitoso" if latency < MAX_LATENCY_DISCONNECTED else "Alta latencia"
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

# Monitorear una UPS
async def monitor_single_ups(ups):
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
        "https_check": "No verificado"
    }

    try:
        # Realizar consultas concurrentes
        ping_result = await ping_and_https_check(ups["ip"])
        snmp_tasks = {
            "battery_voltage": snmp_query(ups["ip"], ".1.3.6.1.2.1.33.1.2.4.0"),
            "input_voltage": snmp_query(ups["ip"], ".1.3.6.1.2.1.33.1.2.5.0"),
            "output_voltage": snmp_query(ups["ip"], ".1.3.6.1.4.1.318.1.4.2.2.1.3.1"),
            "temperature": snmp_query(ups["ip"], ".1.3.6.1.2.1.33.1.2.7.0")
        }

        snmp_results = await asyncio.gather(*snmp_tasks.values(), return_exceptions=True)

        # Actualizar resultados
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

# Monitorear todas las UPS
async def monitor_ups():
    global DATA
    while True:
        tasks = [monitor_single_ups(ups) for ups in UPS_IPS]
        results = await asyncio.gather(*tasks)
        DATA = {res["ip"]: res for res in results}
        socketio.emit("update_data", DATA)
        await asyncio.sleep(5)

# Rutas de Flask
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/add_ups", methods=["POST"])
def add_ups():
    data = request.json
    UPS_IPS.append(data)
    return jsonify({"status": "success", "message": "UPS agregada"})

@app.route("/get_data", methods=["GET"])
def get_data():
    return jsonify(DATA)

# Iniciar la aplicación
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(monitor_ups())
    socketio.run(app, host="0.0.0.0", port=5000)
