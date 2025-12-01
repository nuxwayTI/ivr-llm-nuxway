from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse
import os
import logging

# Logs para ver todo en Render
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# ⚠️ Estas variables las pones en Render → Environment
# TWILIO_CALLER_ID: un número Twilio comprado (ej: +12025550123)
# AGENT_NUMBER: tu DID en la PBX que ya enruta a 4000 / 6200 (ej: +59144XXXXXX)
TWILIO_CALLER_ID = os.getenv("TWILIO_CALLER_ID", "").strip()
AGENT_NUMBER = os.getenv("AGENT_NUMBER", "").strip()


@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    app.logger.info(">>> Llamada entrante a /ivr-llm")
    app.logger.info(f"TWILIO_CALLER_ID = {TWILIO_CALLER_ID!r}")
    app.logger.info(f"AGENT_NUMBER    = {AGENT_NUMBER!r}")

    vr = VoiceResponse()

    # Mensaje al llamante
    vr.say(
        "Te voy a comunicar con un agente de prueba.",
        language="es-ES",
        voice="Polly.Lupe",
    )

    if AGENT_NUMBER:
        # Si tenemos número de agente, hacemos Dial
        if TWILIO_CALLER_ID:
            app.logger.info(f"Marcando a {AGENT_NUMBER} con callerId {TWILIO_CALLER_ID}")
            vr.dial(AGENT_NUMBER, caller_id=TWILIO_CALLER_ID)
        else:
            app.logger.warning(
                "⚠️ TWILIO_CALLER_ID NO CONFIGURADO — Twilio usará el callerId de la llamada SIP."
            )
            vr.dial(AGENT_NUMBER)
    else:
        # Si AGENT_NUMBER está vacío, TwiML quedará con <Dial /> y Twilio no marcará a nadie
        app.logger.error("❌ AGENT_NUMBER NO CONFIGURADO — <Dial> quedará vacío.")

    xml = str(vr)
    app.logger.info(f"TwiML devuelto:\n{xml}")

    return Response(xml, mimetype="text/xml")


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Transferencia a DID - Running!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

