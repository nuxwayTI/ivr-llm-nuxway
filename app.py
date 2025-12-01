from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse
import os
import logging

# Logging básico para que veas todo en Render
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Variables desde Render
TWILIO_CALLER_ID = os.getenv("TWILIO_CALLER_ID", "").strip()
AGENT_NUMBER = os.getenv("AGENT_NUMBER", "").strip()


@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    app.logger.info(">>> Llamada entrante a /ivr-llm")

    vr = VoiceResponse()

    # Mensaje simple
    vr.say(
        "Te voy a comunicar con un agente de prueba.",
        language="es-ES",
        voice="Polly.Lupe"
    )

    # Dial simple
    if TWILIO_CALLER_ID:
        app.logger.info(f"Marcando a {AGENT_NUMBER} con callerId {TWILIO_CALLER_ID}")
        vr.dial(AGENT_NUMBER, caller_id=TWILIO_CALLER_ID)
    else:
        app.logger.warning("⚠️ TWILIO_CALLER_ID NO CONFIGURADO — Twilio usará el callerId de la llamada SIP.")
        vr.dial(AGENT_NUMBER)

    xml = str(vr)
    app.logger.info(f"TwiML devuelto:\n{xml}")

    return Response(xml, mimetype="text/xml")


@app.route("/", methods=["GET"])
def home():
    return "IVR LLM TEST - Running!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

