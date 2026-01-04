from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import re

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# ✅ Obligatorio: IP/Dominio del PBX al que Twilio debe mandar la llamada SIP
PBX_DOMAIN = os.getenv("PBX_DOMAIN", "").strip()

# ✅ Mapear nombres a DIDs
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
    say(vr, "Perfecto, te comunico.")
    d = vr.dial()
    destino = f"sip:{did}@{PBX_DOMAIN}"
    logging.warning(f"TRANSFER -> {destino}")
    d.sip(destino)
    return Response(str(vr), mimetype="text/xml")

@app.route("/ivr-llm", methods=["POST", "GET"])
def ivr_llm():
    vr = VoiceResponse()

    if request.method == "GET":
        say(vr, "IVR OK.")
        return Response(str(vr), mimetype="text/xml")

    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")
    call_sid = request.values.get("CallSid", "unknown")

    if not PBX_DOMAIN:
        say(vr, "Falta configurar PBX DOMAIN en el servidor.")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # Primera vez -> pedir voz o teclas
    if not speech and not digits:
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
        say(g, "Hola. ¿Con quién quieres comunicarte? Di: Pablo, Vladimir o Ingeniero. También puedes marcar 1, 2 o 0.")
        vr.append(g)
        return Response(str(vr), mimetype="text/xml")

    text = normalize(speech)
    logging.info(f"[CALL {call_sid}] speech='{text}' digits='{digits}'")

    # DTMF
    if digits == "1":
        return transfer_to_did(vr, DID_MAP["pablo"])
    if digits == "2":
        return transfer_to_did(vr, DID_MAP["vladimir"])
    if digits == "0":
        return transfer_to_did(vr, DID_MAP["ingeniero"])

    # Voz
    if "pablo" in text:
        return transfer_to_did(vr, DID_MAP["pablo"])
    if "vladimir" in text:
        return transfer_to_did(vr, DID_MAP["vladimir"])
    if "ingeniero" in text:
        return transfer_to_did(vr, DID_MAP["ingeniero"])

    # Reintento
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
    vr.append(g)
    return Response(str(vr), mimetype="text/xml")

@app.route("/debug", methods=["GET"])
def debug():
    return f"PBX_DOMAIN='{PBX_DOMAIN}'"

@app.route("/")
def home():
    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
