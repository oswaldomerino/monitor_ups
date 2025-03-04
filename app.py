import subprocess
import os
import time
import threading
import socket
import json
from datetime import datetime
from flask import Flask, render_template,Response,stream_with_context, jsonify, request
from flask_socketio import SocketIO, emit
import queue
import speedtest

import ipaddress

import re

import webbrowser
from threading import Timer

import sys
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtCore import QUrl


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

# Ruta donde estará SnmpGet.exe en tu proyecto
SNMP_PATH = os.path.join(os.getcwd(), "snmp_tools", "SnmpGet.exe")

SNMP_COMMUNITY = "speyburn"
OIDS = {
    "battery_voltage": ".1.3.6.1.2.1.33.1.2.4.0",
    "output_voltage": ".1.3.6.1.4.1.318.1.1.1.3.2.3.0",
    "input_voltage": ".1.3.6.1.4.1.318.1.1.1.4.2.1.0",
   # "serial_number": ".1.3.6.1.4.1.318.1.4.2.2.1.3.1",
    "temperature": ".1.3.6.1.2.1.33.1.2.7.0",
    #"ubicacion": ".1.3.6.1.2.1.1.6.0",
    #"nombreUPS": ".1.3.6.1.2.1.1.5.0",
    "battery_status": ".1.3.6.1.2.1.33.1.2.3.0",
   # "battery_actividad": ".1.3.6.1.2.1.1.3.0"
}

# OIDs Fijos (solo se consultan una vez o en intervalos largos)
STATIC_OIDS = {
    "serial_number": ".1.3.6.1.4.1.318.1.4.2.2.1.3.1",
    "ubicacion": ".1.3.6.1.2.1.1.6.0",
    "nombreUPS": ".1.3.6.1.2.1.1.5.0",
    "battery_actividad": ".1.3.6.1.2.1.1.3.0"
}

# OIDs Dinámicos (se consultan con frecuencia)
DYNAMIC_OIDS = {
    "battery_voltage": ".1.3.6.1.2.1.33.1.2.4.0",
    "output_voltage":        ".1.3.6.1.4.1.318.1.1.1.4.2.1.0",
    "input_voltage":         ".1.3.6.1.4.1.318.1.1.1.3.2.1.0",
    "temperature": ".1.3.6.1.2.1.33.1.2.7.0",
    "battery_status": ".1.3.6.1.4.1.318.1.1.1.2.2.3.0",
}


MAX_LATENCY_DISCONNECTED = 60

DATA = {}
task_queue = queue.Queue()
LOCK = threading.Lock()
DISCONNECTED_DEVICES = set()
VALIDATION_QUEUE = set()

# Función para realizar consultas SNMP
def snmp_query(ip, oids):
    """Consulta SNMP usando SnmpGet.exe."""
    results = {}
    for key, oid in oids.items():
        try:
            snmp_output = subprocess.run(
                [SNMP_PATH, f"-r:{ip}", "-v:2c", f"-c:{SNMP_COMMUNITY}", f"-o:{oid}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW  # Evitar que se abra una ventana de consola
            )
            if snmp_output.returncode == 0:
                # Procesar la salida para extraer el valor
                value = snmp_output.stdout.split("Value=")[-1].strip()
                results[key] = value
            else:
                results[key] = "N/A"
                log_connection_error({
                    "timestamp": datetime.now().isoformat(),
                    "ip": ip,
                    "name": "Unknown",
                    "error_type": "SNMP Query Failed",
                    "details": f"Failed to retrieve OID {oid}"
                })
        except Exception as e:
            results[key] = f"Error: {e}"
            log_connection_error({
                "timestamp": datetime.now().isoformat(),
                "ip": ip,
                "name": "Unknown",
                "error_type": "SNMP Query Error",
                "details": str(e)
            })
    return results


def execute_ping(ip, timeout):
    """Ejecuta un ping con un timeout específico."""
    return subprocess.run(
        ["ping", "-n", "1", ip],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW
    )


def check_https(ip):
    """Verifica si el puerto HTTPS está abierto."""
    try:
        with socket.create_connection((ip, 443), timeout=2):
            return True
    except socket.error:
        return False


def validate_disconnection(ups, max_attempts=5, timeout=2):
    """Ejecuta intentos adicionales en paralelo solo para dispositivos en la lista de validación."""
    global DATA

    for attempt in range(max_attempts):
        time.sleep(1)
        ping_output = execute_ping(ups["ip"], timeout)

        if ping_output.returncode == 0:
            for line in ping_output.stdout.splitlines():
                if "tiempo=" in line.lower():
                    latency = line.split("tiempo=")[-1].split("ms")[0].strip()

                    with LOCK:
                        if ups["ip"] in DATA:
                            DATA[ups["ip"]]["status"] = "Conectada"
                            DATA[ups["ip"]]["latency"] = float(latency)
                            DATA[ups["ip"]]["ping_response"] = "Ping Exitoso"

                        DISCONNECTED_DEVICES.discard(ups["ip"])
                        VALIDATION_QUEUE.discard(ups["ip"])
#
#                    log_connection_error({
#                        "timestamp": datetime.now().isoformat(),
#                        "ip": ups["ip"],
#                        "name": ups["name"],
#                        "error_type": "Recuperado",
#                        "details": "UPS volvió a responder al ping."
#                    })

                    socketio.emit("new_data", DATA[ups["ip"]])
                    return  

    # Si aún no responde al ping, validar HTTPS nuevamente
    if check_https(ups["ip"]):
        with LOCK:
            if ups["ip"] in DATA:
                DATA[ups["ip"]]["status"] = "Conectada"
                DATA[ups["ip"]]["latency"] = None
                DATA[ups["ip"]]["ping_response"] = "ICMP bloqueado"

            DISCONNECTED_DEVICES.discard(ups["ip"])
            VALIDATION_QUEUE.discard(ups["ip"])

#        log_connection_error({
#            "timestamp": datetime.now().isoformat(),
#            "ip": ups["ip"],
#            "name": ups["name"],
#            "error_type": "Advertencia: ICMP bloqueado",
#            "details": "El Ping falló, pero HTTPS está activo."
#        })

        socketio.emit("new_data", DATA[ups["ip"]])
        return  

    # Si no responde a ping ni HTTPS después de múltiples intentos, marcar como desconectado
    with LOCK:
        if ups["ip"] in DATA:
            DATA[ups["ip"]]["status"] = "Desconectada"
            DATA[ups["ip"]]["latency"] = None
            DATA[ups["ip"]]["ping_response"] = "No respondido"
            log_connection_error({
                "timestamp": datetime.now().isoformat(),
                "ip": ups["ip"],
                "name": ups["name"],
                "error_type": "Confirmado: Desconectado",
                "details": f"No hubo respuesta después de {max_attempts} intentos."
            })

    

    socketio.emit("new_data", DATA[ups["ip"]])


def ping_and_https(ups):
    """Realiza un ping y verifica el puerto HTTPS."""
    result = {
        "ip": ups["ip"],
        "name": ups["name"],
        "status": "Desconectada",
        "latency": None,
        "ping_response": "No respondido",
        "https_check": "No verificado"
    }

    try:
        ping_output = execute_ping(ups["ip"], timeout=2)

        if ping_output.returncode == 0:
            for line in ping_output.stdout.splitlines():
                if "tiempo=" in line.lower():
                 try:
                    latency = line.split("tiempo=")[-1].split("ms")[0].strip()
                    result["latency"] = float(latency)
                    result["status"] = "Conectada"
                    result["ping_response"] = "Ping Exitoso"

                 except ValueError:
                    result["latency"] = "Error al obtener latencia"

                    with LOCK:
                        DISCONNECTED_DEVICES.discard(ups["ip"])
                        VALIDATION_QUEUE.discard(ups["ip"])

        else:
            with LOCK:
                if ups["ip"] not in VALIDATION_QUEUE:
                    VALIDATION_QUEUE.add(ups["ip"])
                    threading.Thread(target=validate_disconnection, args=(ups,), daemon=True).start()

    except Exception as e:
        result["ping_response"] = f"Error en ping: {e}"
        log_connection_error({
            "timestamp": datetime.now().isoformat(),
            "ip": ups["ip"],
            "name": ups["name"],
            "error_type": "Error en Ping",
            "details": str(e)
        })

    # Verificar HTTPS
    try:
        if check_https(ups["ip"]):
            result["https_check"] = "Puerto HTTPS abierto"
            if result["status"] == "Desconectada" and result["ping_response"] == "No respondido":
                result["status"] = "Conectada"
    except Exception as e:
        result["https_check"] = "Error en HTTPS: " + str(e)
        log_connection_error({
            "timestamp": datetime.now().isoformat(),
            "ip": ups["ip"],
            "name": ups["name"],
            "error_type": "Error en HTTPS",
            "details": str(e)
        })

    return result

def monitor_ping_and_https():
    """Monitorea el estado de las UPS, ejecutando verificaciones de ping y HTTPS."""
    global DATA
    while True:
        for ups in UPS_IPS:
            result = ping_and_https(ups)
            with LOCK:
                if ups["ip"] not in DATA:
                    DATA[ups["ip"]] = result
                else:
                    DATA[ups["ip"]].update({k: v for k, v in result.items() if DATA[ups["ip"]].get(k) != v})

                socketio.emit("new_data", DATA[ups["ip"]])

        # Validar dispositivos en la sublista con mayor frecuencia
        for ip in list(VALIDATION_QUEUE):
            ups = next((u for u in UPS_IPS if u["ip"] == ip), None)
            if ups:
                validate_disconnection(ups, max_attempts=3)

        time.sleep(2)  

def monitor_snmp():
    """Realiza consultas SNMP en segundo plano para OIDs dinámicos y verifica si los estáticos están actualizados."""
    while True:
        for ups in UPS_IPS:
            if ups["ip"] in DATA and DATA[ups["ip"]]["status"] == "Conectada":
                print(f"Fetching SNMP data for {ups['ip']}...")  # Debug

                # Consultar solo los OIDs dinámicos con frecuencia
                dynamic_data = snmp_query(ups["ip"], DYNAMIC_OIDS)

                with LOCK:
                    if dynamic_data:
                        # Registrar condiciones críticas en los logs
                        check_snmp_alerts(ups["ip"], dynamic_data)

                        # Actualizar solo los valores que han cambiado
                        DATA[ups["ip"]].update({k: v for k, v in dynamic_data.items() if DATA[ups["ip"]].get(k) != v})
                        socketio.emit('new_data', DATA[ups["ip"]])  # Actualizar UI solo si hubo cambios
                        print(f"Updated dynamic SNMP data for {ups['ip']}: {dynamic_data}")  # Debug

                    # Si los datos estáticos aún no han sido consultados, hacer la consulta una sola vez
                    if "static_data_checked" not in DATA[ups["ip"]]:
                        static_data = snmp_query(ups["ip"], STATIC_OIDS)
                        if static_data:
                            DATA[ups["ip"]].update(static_data)
                            DATA[ups["ip"]]["static_data_checked"] = True  # Marcar como actualizado
                            socketio.emit('new_data', DATA[ups["ip"]])  # Enviar datos a la interfaz
                            print(f"Updated static SNMP data for {ups['ip']}: {static_data}")  # Debug

        time.sleep(10)  # Mantener la consulta rápida para los OIDs dinámicos

# Almacena IPs que ya han sido reportadas como sin SNMP para evitar registros repetidos
SNMP_DISABLED_IPS = set()


# Lista de mensajes de error que se desean ignorar en el log
ALERTS_IGNORAR = [
    "❌ Error al leer el voltaje de entrada",
    "❌ Error al leer la temperatura"
]

def check_snmp_alerts(ip, snmp_data):
    """Verifica condiciones críticas en los datos SNMP y registra errores si es necesario."""
    alerts = []

    # 📌 Detectar si SNMP no está activado o hay un timeout
    if all(value in ["N/A", "Timeout", "Failed to get value of SNMP variable"] or "SnmpGet" in str(value) 
           for value in snmp_data.values()):
        if ip not in SNMP_DISABLED_IPS:
            SNMP_DISABLED_IPS.add(ip)  # Marcar como sin SNMP
 #           log_connection_error({
  #              "timestamp": datetime.now().isoformat(),
   #             "ip": ip,
    #            "name": DATA.get(ip, {}).get("nombreUPS", "Desconocido"),
     #           "error_type": "SNMP Desactivado",
      #          "details": "El dispositivo no responde a consultas SNMP."
       #     })
        return  # No continuar registrando más errores de este dispositivo

    # 🔥 Comprobar temperatura alta y baja con nuevos criterios
    if "temperature" in snmp_data:
        try:
            temp = float(snmp_data["temperature"])
            if temp > 40:
                alerts.append(f"🔥 Temperatura EXTREMADAMENTE ALTA: {temp}°C")
            elif temp > 38:
                alerts.append(f"⚠️ Temperatura alta: {temp}°C")
            elif temp < 12:
                alerts.append(f"⚠️ Temperatura baja: {temp}°C")
            elif temp < 8:
                alerts.append(f"❄️ Temperatura EXTREMADAMENTE BAJA: {temp}°C")
        except ValueError:
            alerts.append("❌ Error al leer la temperatura")

    # ⚡ Comprobar voltaje de entrada con nuevos criterios
    if "input_voltage" in snmp_data:
        try:
            input_v = float(snmp_data["input_voltage"])
            if input_v > 140:
                alerts.append(f"🔴 Voltaje de entrada EXTREMADAMENTE ALTO: {input_v}V")
            elif input_v > 134:
                alerts.append(f"⚠️ Voltaje de entrada alto: {input_v}V")
            elif 105 > input_v > 0:
                alerts.append(f"🔴 Voltaje de entrada EXTREMADAMENTE BAJO: {input_v}V")
            elif 100 > input_v > 0:
                alerts.append(f"⚠️ Voltaje de entrada bajo: {input_v}V")
            elif input_v == 0:
                alerts.append("⚡ UPS funcionando en baterías (Voltaje de entrada: 0V)")
        except ValueError:
            alerts.append("❌ Error al leer el voltaje de entrada")

    # 📝 Registrar todas las alertas encontradas
    # Registrar las alertas que no estén en la lista de ignorados
    for alert in alerts:
        if not any(ignore_msg in alert for ignore_msg in ALERTS_IGNORAR):
            log_connection_error({
                "timestamp": datetime.now().isoformat(),
                "ip": ip,
                "name": DATA.get(ip, {}).get("nombreUPS", "Desconocido"),
                "error_type": "Condición Crítica SNMP",
                "details": alert
            })

    # Si SNMP empieza a responder, eliminamos la IP del conjunto SNMP_DISABLED_IPS
    if ip in SNMP_DISABLED_IPS:
        SNMP_DISABLED_IPS.remove(ip)







def fetch_static_snmp_data():
    """Consulta SNMP de OIDs estáticos y fuerza su actualización al inicio."""
    # Primera consulta al inicio
    for ups in UPS_IPS:
        if ups["ip"] not in DATA:
            DATA[ups["ip"]] = {}

        if ups["ip"] in DATA:
            static_data = snmp_query(ups["ip"], STATIC_OIDS)  # Consulta inicial
            with LOCK:
                DATA[ups["ip"]].update(static_data)
                socketio.emit('new_data', DATA[ups["ip"]])  # Enviar datos a la UI inmediatamente

    # Consulta periódica cada 30 minutos para actualizar estáticos si es necesario
    while True:
        time.sleep(1800)  # Consulta cada 30 minutos
        for ups in UPS_IPS:
            if ups["ip"] in DATA and DATA[ups["ip"]]["status"] == "Conectada":
                print(f"Fetching periodic static SNMP data for {ups['ip']}...")  # Debug
                snmp_data = snmp_query(ups["ip"], STATIC_OIDS)

                with LOCK:
                    if snmp_data:
                        # Solo actualizar los valores que han cambiado
                        DATA[ups["ip"]].update({k: v for k, v in snmp_data.items() if DATA[ups["ip"]].get(k) != v})
                        socketio.emit('new_data', DATA[ups["ip"]])  # Actualizar UI
                        print(f"Updated periodic static SNMP data for {ups['ip']}: {snmp_data}")  # Debug")
    
        


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/devices")
def devices():
    return render_template("devices.html")

@app.route("/network")
def network():
    return render_template("network.html")

@app.route("/monitoring")
def monitoring():
    return render_template("index.html")



@app.route("/data")
def data():
    """Proporciona datos actuales en formato JSON."""
    with LOCK:
        return jsonify(DATA)
    

@app.route("/open_putty")
def open_putty():
    """Abre PuTTY con la IP proporcionada."""
    ip = request.args.get("ip")
    if not ip:
        return jsonify({"status": "error", "message": "IP address is required"}), 400

    # Ruta al ejecutable de PuTTY (ajústala según tu sistema)
    putty_path = os.path.join(os.getcwd(), "snmp_tools", "putty.exe")

    try:
        # Ejecutar PuTTY con los parámetros correctos
        subprocess.Popen([putty_path, "-ssh", ip])
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500



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

def open_browser():
    webbrowser.open_new('http://127.0.0.1:5000/')


#S
@app.route("/internet-speed")
def internet_speed():
    try:
        st = speedtest.Speedtest()
        st.get_best_server()
        download_speed = st.download() / (1024 * 1024)  # Convertir a Mbps
        upload_speed = st.upload() / (1024 * 1024)  # Convertir a Mbps

        speed_data = {
            "download": round(download_speed, 2),
            "upload": round(upload_speed, 2)
        }
        return jsonify(speed_data)
    except Exception as e:
        return jsonify({"error": str(e)})

####################################################################################

# Optimized function to get the MAC address using the "arp" command
# Optimized function to get the MAC address using the "arp" command
def get_mac(ip):
    try:
        # Run the arp command for the specific IP
        result = subprocess.run(['arp', '-a', ip], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        output = result.stdout

        # Check if "No se encontraron entradas ARP" is in the output
        if "No se encontraron entradas ARP" in output:
            return None

        # Process each line to find the MAC address
        for line in output.splitlines():
            if ip in line:
                parts = re.split(r'\s+', line.strip())
                if len(parts) >= 2:
                    mac_address = parts[1]
                    # Validate the MAC address format
                    if re.match(r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$', mac_address):
                        return mac_address
        return None
    except Exception as e:
        print(f"Error getting MAC for {ip}: {e}")
        return None

# Function to determine the device type based on the MAC address
def get_device_type(mac):
    if not mac:
        return "Unknown", "fa-question-circle"
    if mac.startswith("00:1A:2B"):  # Example MAC for cameras
        return "Camera", "fa-video"
    elif mac.startswith("00:1D:7E"):  # Example MAC for printers
        return "Printer", "fa-print"
    elif mac.startswith("00:1F:E2"):  # Example MAC for switches
        return "Switch", "fa-network-wired"
    else:
        return "Device", "fa-desktop"

# Function to check if a specific port is open on an IP address
def is_port_open(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=1) as s:
            return True
    except:
        return False

# Network scanning and real-time data streaming
@app.route('/scan-network')
def scan_network():
    start_ip = request.args.get('start_ip')
    end_ip = request.args.get('end_ip')

    if not start_ip or not end_ip:
        return jsonify({"error": "Invalid IP range."}), 400

    devices = []

    def generate():
        start_octets = start_ip.split('.')
        end_octets = end_ip.split('.')

        for i in range(int(start_octets[-1]), int(end_octets[-1]) + 1):
            ip = f"{'.'.join(start_octets[:-1])}.{i}"
            try:
                ports_to_check = [80, 443, 22, 445, 3389, 21, 25, 53]
                device_online = False

                for port in ports_to_check:
                    if is_port_open(ip, port):
                        device_online = True
                        break

                if device_online:
                    mac = get_mac(ip)
                    device_type, icon = get_device_type(mac)
                    device_name = get_device_name(ip)

                    devices.append({
                        'type': device_type,
                        'icon': icon,
                        'name': device_name,
                        'details': mac if mac else 'N/A',
                        'ip': ip,
                        'status': 'online'
                    })

                    yield f"data: {jsonify({'ip': ip, 'type': device_type, 'icon': icon, 'name': device_name, 'mac': mac, 'status': 'online'}).data.decode()}\n\n"
                else:
                    yield f"data: {jsonify({'ip': ip, 'type': 'Unknown', 'icon': 'fa-question-circle', 'name': ip, 'mac': 'N/A', 'status': 'offline'}).data.decode()}\n\n"
            except Exception as e:
                print(f"Error scanning {ip}: {e}")
                yield f"data: {{'error': '{str(e)}'}}\n\n"

    return Response(stream_with_context(generate()), content_type='text/event-stream')

# Function to get the local IP address in IPv4 format
@app.route('/get-local-ip')
def get_local_ip():
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    return jsonify({"ip": local_ip})

# Optimized function to get the device name
def get_device_name(ip):
    try:
        name = socket.gethostbyaddr(ip)[0]  # Try to get the hostname
        return name if name else ip  # If no name found, return IP as the name
    except (socket.herror, socket.gaierror):
        # In case of error, return the IP as the name
        return ip
    
####################################################################################
@app.route("/real_time_data/<ip>")
def real_time_data(ip):
    """Consulta SNMP en tiempo real para una UPS específica"""
    oids = {

        "ups_name":              ".1.3.6.1.2.1.1.5.0",  # Nombre de la UPS
        "battery_percentage":    ".1.3.6.1.2.1.33.1.2.4.0",
        "battery_voltage":       ".1.3.6.1.2.1.33.1.2.5.0",
        "output_voltage":        ".1.3.6.1.4.1.318.1.1.1.4.2.1.0",
        "input_voltage":         ".1.3.6.1.4.1.318.1.1.1.3.2.1.0",
        "output_frequency":      ".1.3.6.1.4.1.318.1.1.1.4.2.2.0",
        "input_frequency":       ".1.3.6.1.4.1.318.1.1.1.3.2.4.0",
        "battery_status":        ".1.3.6.1.2.1.33.1.2.3.0",
        "battery_runtime":       ".1.3.6.1.4.1.318.1.1.1.2.2.3.0",
        "battery_health":        ".1.3.6.1.4.1.318.1.1.1.2.2.1.0",
        "temperature":           ".1.3.6.1.4.1.318.1.1.1.2.3.2.0",
        "uptime":                ".1.3.6.1.2.1.1.3.0",
        "last_alarm":            ".1.3.6.1.4.1.318.1.1.1.2.2.19.0",
        "ups_model":             ".1.3.6.1.4.1.318.1.1.1.1.1.1.0",
        "ups_serial":            ".1.3.6.1.4.1.318.1.4.2.2.1.3.1",
        "ip_address":            ".1.3.6.1.2.1.4.20.1.1.{}".format(ip),
        "network_status":        ".1.3.6.1.2.1.4.1.0.{}".format(ip),
        "subnet_mask":           ".1.3.6.1.2.1.4.20.1.3.{}".format(ip),
        "ubicacion":             ".1.3.6.1.2.1.1.6.0",
        "mac_address":           ".1.3.6.1.2.1.2.2.1.6.2",

    }
    
# Realizar la consulta SNMP
    result = snmp_query(ip, oids)

    # 🔹 Verificar que tenemos la IP y la máscara para calcular la puerta de enlace
    if "ip_address" in result and "subnet_mask" in result:
        try:
            gateway = calcular_puerta_enlace(result["ip_address"], result["subnet_mask"])
            result["gateway"] = gateway

            # 🔹 Consultar la MAC Address de la puerta de enlace
       #     gateway_oid = f".1.3.6.1.2.1.4.22.1.2.2.{gateway}"
      #      mac_address_result = snmp_query(ip, {"mac_address": gateway_oid})

        #    result["mac_address"] = mac_address_result.get("mac_address", "No disponible")

        except Exception as e:
            result["gateway"] = "Error calculando"
            result["mac_address"] = "Error obteniendo MAC"

    # 🔹 Corregir formato de temperatura
    if "temperature" in result:
        result["temperature"] = f"{float(result['temperature']) / 10:.1f} °C"

    if "battery_voltage" in result:
        result["battery_voltage"] = f"{float(result['battery_voltage']) / 10:.1f}"

    return jsonify(result)


def calcular_puerta_enlace(ip, mascara):
    """ Calcula la puerta de enlace con base en la IP y la máscara de subred. """
    red = ipaddress.IPv4Network(f"{ip}/{mascara}", strict=False)
    puerta_enlace = list(red.hosts())[0]  # Primera IP disponible
    return str(puerta_enlace)
####################################################################################

# GUI using PyQt5
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UPS Monitor")
        self.setGeometry(100, 100, 1024, 768)

        self.browser = QWebEngineView()
        self.browser.setUrl(QUrl("http://127.0.0.1:5000"))
        self.setCentralWidget(self.browser)

def start_flask_app():

    #    """Inicia la aplicación Flask y envía datos iniciales antes de hacer ping."""
    global UPS_IPS
    UPS_IPS = load_ups_data()  # Carga la lista de UPS desde el JSON

    # Asignar estado inicial "Conectando..." y emitir datos a la interfaz
    with LOCK:
        for ups in UPS_IPS:
            DATA[ups["ip"]] = {
                "ip": ups["ip"],
                "name": ups["name"],
                "status": "Conectando...",  # Estado inicial antes del ping
                "latency": "N/A",
                "ping_response": "Haciendo ping...",
                "https_check": "Verificando...",
            }
            socketio.emit('new_data', DATA[ups["ip"]])  # Enviar datos iniciales a la UI

    ping_thread = threading.Thread(target=monitor_ping_and_https, daemon=True)
   # snmp_static_thread = threading.Thread(target=fetch_static_snmp_data, daemon=True)
    snmp_thread = threading.Thread(target=monitor_snmp, daemon=True)
    ping_thread.start()
    #snmp_static_thread.start()
    snmp_thread.start()
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)

def start_gui():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    flask_thread = threading.Thread(target=start_flask_app, daemon=True)
    flask_thread.start()
    start_gui()

if __name__ == "__main1__":
    ping_thread = threading.Thread(target=monitor_ping_and_https, daemon=True)
    snmp_thread = threading.Thread(target=monitor_snmp, daemon=True)
    ping_thread.start()
    snmp_thread.start()
    Timer(1, open_browser).start()
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
