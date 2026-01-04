from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import re
import logging
import os

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

PBX_DOMAIN = os.getenv("PBX_DOMAIN", "").strip()

DID_MAP = {
    "pablo": "5000",
    "vladimir": "5001",
    "ingeniero": "5999"
}

def say(vr, text):
    vr.say(text, language="es-MX", voice="Polly.Mia")

def normalize(text):
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-záéíóúñ0-9 ]+", "", text)
    return text

def transfer_to_did(vr, did):
    say(vr, "Listo. Te transfiero ahora.")
    d = vr.dial()
    d.sip(f"sip:{did}@{PBX_DOMAIN}")
    return Response(str(vr), mimetype="text/xml")

def gather_menu():
    g = Gather(
        input="speech dtmf",
        language="es-MX",
        timeout=4,
        speech_timeout="auto",
        action="/ivr-llm",
        method="POST",
        bargeIn=True,
        action_on_empty_result=True
    )
    say(g, "Hola. Dime Pablo, Vladimir o Ingeniero. También puedes marcar 1, 2 o 0.")
    return g

def gather_retry():
    g = Gather(
        input="speech dtmf",
        language="es-MX",
        timeout=4,
        speech_timeout="auto",
        action="/ivr-llm",
        method="POST",
        bargeIn=True,
        action_on_empty_result=True
    )
    say(g, "No te entendí. Di Pablo, Vladimir o Ingeniero. O marca 1, 2 o 0.")
    return g

@app.route("/ivr-llm", methods=["GET", "POST"])
def ivr_llm():
    vr = VoiceResponse()

    try:
        call_sid = request.values.get("CallSid", "unknown")
        speech = request.values.get("SpeechResult")
        digits = request.values.get("Digits")

        logging.info(f"[CALL {call_sid}] Incoming | Speech='{speech}' Digits='{digits}'")

        if not PBX_DOMAIN:
            say(vr, "Error de configuración. Falta PBX DOMAIN.")
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        if not speech and not digits:
            vr.append(gather_menu())
            return Response(str(vr), mimetype="text/xml")

        text = normalize(speech)
        logging.info(f"[CALL {call_sid}] USER normalized speech='{text}' digits='{digits}'")

        if digits == "1" or "pablo" in text:
            return transfer_to_did(vr, DID_MAP["pablo"])
        if digits == "2" or "vladimir" in text:
            return transfer_to_did(vr, DID_MAP["vladimir"])
        if digits == "0" or "ingeniero" in text:
            return transfer_to_did(vr, DID_MAP["ingeniero"])

        vr.append(gather_retry())
        return Response(str(vr), mimetype="text/xml")

    except Exception as e:
        logging.exception(f"ERROR en /ivr-llm: {e}")
        say(vr, "Hubo un problema técnico.")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

@app.route("/debug", methods=["GET"])
def debug():
    return f"PBX_DOMAIN='{PBX_DOMAIN}'"

@app.route("/", methods=["GET"])
def home():
    return "IVR TEST OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)



