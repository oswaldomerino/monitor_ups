"""Flask web application and routes."""

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO


def create_app(ups_list, data_store):
    """Create the Flask application and register routes."""
    app = Flask(__name__)
    socketio = SocketIO(app, async_mode="eventlet")

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/add_ups", methods=["POST"])
    def add_ups():
        data = request.json
        ups_list.append(data)
        return jsonify({"status": "success", "message": "UPS agregada"})

    @app.route("/get_data", methods=["GET"])
    def get_data():
        return jsonify(data_store)

    return app, socketio
