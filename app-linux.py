import subprocess
import time
import threading
import socket
import json
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import queue

app = Flask(__name__)
socketio = SocketIO(app)
DATA = {}
# Configuración
DATA_FILE = "ups_data.json"
ERROR_LOG_FILE = "connection_errors.json"

def load_ups_data():
    """Carga los datos de UPS desde un archivo JSON."""
    try:
        with open(DATA_FILE, "r") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_ups_data():
    """Guarda los datos de UPS en un archivo JSON."""
    with open(DATA_FILE, "w") as file:
        json.dump(UPS_IPS, file, indent=4)


def log_connection_error(error_data):
    """Guarda errores de conexión en un archivo JSON."""
    try:
        with open(ERROR_LOG_FILE, "r") as file:
            errors = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        errors = []

    errors.append(error_data)

    with open(ERROR_LOG_FILE, "w") as file:
        json.dump(errors, file, indent=4)

def load_connection_errors():
    """Carga los errores de conexión desde un archivo JSON."""
    try:
        with open(ERROR_LOG_FILE, "r") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []



UPS_IPS = load_ups_data()

SNMP_COMMUNITY = "speyburn"
OIDS = {
    "battery_voltage": ".1.3.6.1.2.1.33.1.2.4.0",
    "input_voltage": "iso.3.6.1.2.1.33.1.2.5.0",
    "output_voltage": "iso.3.6.1.4.1.318.1.1.1.4.2.1.0",
    "serial_number": "iso.3.6.1.4.1.318.1.4.2.2.1.3.1",
    "temperature": ".1.3.6.1.2.1.33.1.2.7.0",
    "ubicacion": "iso.3.6.1.2.1.1.6.0",
    "nombreUPS": "iso.3.6.1.2.1.1.5.0",
    "battery_status": "iso.3.6.1.2.1.33.1.2.3.0",
    "battery_actividad": "iso.3.6.1.2.1.1.3.0",
}

MAX_LATENCY_DISCONNECTED = 60

DATA = {}
task_queue = queue.Queue()
LOCK = threading.Lock()


def snmp_query(ip, oids):
    """Consulta SNMP para múltiples OIDs usando snmpget."""
    results = {}
    try:
        for key, oid in oids.items():
            snmp_output = subprocess.run(
                ["snmpget", "-v2c", "-c", SNMP_COMMUNITY, ip, oid],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if snmp_output.returncode == 0:
                results[key] = snmp_output.stdout.split(":")[-1].strip()
            else:
                results[key] = "N/A"
    except Exception as e:
        return {"error": f"Error SNMP: {e}"}
    return results


def ping_and_https(ups):
    """Realiza un ping y verifica el puerto HTTPS."""
    result = {
        "ip": ups["ip"], "name": ups["name"],
        "status": "Desconectada", "latency": None,
        "ping_response": "No respondido", "https_check": "No verificado"
    }

    # Intentar un ping
    try:
        # Ejecutar el comando ping
        ping_output = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ups["ip"]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        if ping_output.returncode == 0:
            # Intentar extraer la latencia del resultado
            try:
                latency = float(ping_output.stdout.split("time=")[1].split(" ")[0])
                result["latency"] = latency

                if latency == 0.0 or latency > MAX_LATENCY_DISCONNECTED:
                    result["status"] = "Desconectada"
                    result["ping_response"] = "Ping fallido por alta latencia"
                    log_connection_error({
                        "timestamp": datetime.now().isoformat(),
                        "ip": ups["ip"],
                        "name": ups["name"],
                        "latency": latency,
                        "error_type": "High latency",
                        "details": "latency " + str(latency) + "ms"
                    })
                else:
                    result["status"] = "Conectada"
                    result["ping_response"] = "Ping Exitoso"
            except (IndexError, ValueError) as e:
                # Manejo de casos donde el formato de salida de ping no es el esperado
                result["ping_response"] = "Ping fallido (análisis de latencia)"
                result["status"] = "Desconectada"
                log_connection_error({
                    "timestamp": datetime.now().isoformat(),
                    "ip": ups["ip"],
                    "name": ups["name"],
                    "error_type": "Latency parse error",
                    "details": f"Error al analizar latencia: {str(e)}"
                })
        else:
            result["ping_response"] = "Ping fallido"
            result["status"] = "Desconectada"
            log_connection_error({
                "timestamp": datetime.now().isoformat(),
                "ip": ups["ip"],
                "name": ups["name"],
                "error_type": "Ping failed",
                "details": "Fallo comunicación con la UPS"
            })

    except Exception as e:
        # Capturar excepciones generales y registrar el error
        result["ping_response"] = f"Error en el ping: {str(e)}"
        result["status"] = "Desconectada"
        log_connection_error({
            "timestamp": datetime.now().isoformat(),
            "ip": ups["ip"],
            "name": ups["name"],
            "error_type": "Ping error",
            "details": "Ping error: " + str(e)
        })

    # Verificar puerto HTTPS
    try:
        with socket.create_connection((ups["ip"], 443), timeout=4) as sock:
            result["https_check"] = "Puerto HTTPS abierto"
            if result["status"] == "Desconectada" and result["ping_response"] == "Ping fallido":
                result["status"] = "Conectada"
    except socket.error:
        result["https_check"] = "Puerto HTTPS cerrado"
    return result


def monitor_ping_and_https():
    """Monitorea el estado de las UPS usando ping y HTTPS."""
    global DATA
    while True:
        for ups in UPS_IPS:
            result = ping_and_https(ups)
            with LOCK:
                if ups["ip"] not in DATA:
                    DATA[ups["ip"]] = result
                else:
                    # Solo actualiza los datos que cambian
                    DATA[ups["ip"]].update({k: v for k, v in result.items() if DATA[ups["ip"]].get(k) != v})
                socketio.emit('new_data', DATA[ups["ip"]])  # Enviar datos a la interfaz
        time.sleep(2)


def monitor_snmp():
    """Realiza consultas SNMP en segundo plano."""
    while True:
        for ups in UPS_IPS:
            if ups["ip"] in DATA and DATA[ups["ip"]]["status"] == "Conectada":
                snmp_data = snmp_query(ups["ip"], OIDS)
                with LOCK:
                    if snmp_data:
                        # Solo actualiza los datos SNMP que cambian
                        DATA[ups["ip"]].update({k: v for k, v in snmp_data.items() if DATA[ups["ip"]].get(k) != v})
                        socketio.emit('new_data', DATA[ups["ip"]])  # Actualizar datos en la interfaz
        time.sleep(2)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/data")
def data():
    """Proporciona datos actuales en formato JSON."""
    with LOCK:
        return jsonify(DATA)


@app.route("/add", methods=["POST"])
def add_ups():
    """Agrega una nueva UPS a la lista."""
    ups_data = request.json
    ups_ip = ups_data["ip"]
    ups_name = ups_data["name"]

    # Verificar si la IP ya existe
    if any(ups["ip"] == ups_ip for ups in UPS_IPS):
        return jsonify({"message": f"The UPS with IP {ups_ip} already exists."}), 400


    UPS_IPS.append({"ip": ups_ip, "name": ups_name})
    save_ups_data()
    log_connection_error({
            "timestamp": datetime.now().isoformat(),
            "ip": ups_ip,
            "name": ups_name,
            "error_type": "UPS Agregada"
        })
    return jsonify({"status": "success"})


@app.route("/remove", methods=["POST"])
def remove_ups():
    """Elimina una UPS de la lista."""
    ups_data = request.json
    ups_ip = ups_data.get("ip")
    ups_name = ups_data.get("name")  # Evita errores si falta el name
    if not ups_ip:
        return jsonify({"status": "error", "message": "IP is required"}), 400
    
    global UPS_IPS
    UPS_IPS = [ups for ups in UPS_IPS if ups["ip"] != ups_ip]
    save_ups_data()  # Asegúrate de guardar los cambios en el archivo JSON
    
    # Registra el evento en el log de errores
    log_connection_error({
        "timestamp": datetime.now().isoformat(),
        "ip": ups_ip,
        "name": ups_name,
        "error_type": "UPS Eliminada"
    })
    
    with LOCK:
        if ups_ip in DATA:
            del DATA[ups_ip]  # Elimina del dict de datos en memoria
    
    return jsonify({"status": "success"})


@app.route("/errors")
def get_errors():
    """Proporciona los errores de conexión en formato JSON."""
    try:
        errors = load_connection_errors()
       # app.logger.info(f"Errors loaded successfully: {len(errors)} entries")
        return jsonify(errors)
    except Exception as e:
        app.logger.error(f"Error loading connection errors: {e}")
        return jsonify([])  # Devuelve una lista vacía si ocurre algún error



if __name__ == "__main__":
    ping_thread = threading.Thread(target=monitor_ping_and_https, daemon=True)
    snmp_thread = threading.Thread(target=monitor_snmp, daemon=True)
    ping_thread.start()
    snmp_thread.start()
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
