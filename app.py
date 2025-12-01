from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse
import logging

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Usuario SIP de Twilio al que quieres llamar
AGENT_SIP = "sip:4000@nuxway.sip.twilio.com"


@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    app.logger.info(">>> Llamada entrante a /ivr-llm")

    vr = VoiceResponse()

    # Mensaje al llamante
    vr.say(
        "Te voy a comunicar con un agente de prueba por SIP, usuario cuatro mil.",
        language="es-ES",
        voice="Polly.Lupe"
    )

    # Dial SIP: intentará llamar a sip:4000@nuxway.sip.twilio.com
    app.logger.info(f"Marcando por SIP a {AGENT_SIP}")
    dial = vr.dial()
    dial.sip(AGENT_SIP)

    xml = str(vr)
    app.logger.info(f"TwiML devuelto:\n{xml}")

    return Response(xml, mimetype="text/xml")


@app.route("/", methods=["GET"])
def home():
    return "IVR LLM TEST SIP 4000 - Running!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)


