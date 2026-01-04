from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import re
import logging
import os

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# ✅ Variables para Render
PBX_DOMAIN = os.getenv("PBX_DOMAIN", "").strip()
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

# ✅ Mapa de DID
DID_MAP = {
    "pablo": "5000",
    "vladimir": "5001",
    "ingeniero": "5999"
}

def say(vr, text):
    vr.say(text, language="es-MX", voice="Polly.Mia")

def transfer_to_did(vr, did):
    say(vr, "Listo. Te transfiero ahora.")
    d = vr.dial()
    d.sip(f"sip:{did}@{PBX_DOMAIN}")
    return Response(str(vr), mimetype="text/xml")

def normalize(text):
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-záéíóúñ0-9 ]+", "", text)
    return text

@app.route("/ivr", methods=["POST"])
def ivr():
    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")

    vr = VoiceResponse()

    # ✅ Validaciones clave para evitar crasheos
    if not PBX_DOMAIN:
        say(vr, "Error de configuración. Falta PBX_DOMAIN.")
        return Response(str(vr), mimetype="text/xml")

    if not BASE_URL:
        say(vr, "Error de configuración. Falta BASE_URL.")
        return Response(str(vr), mimetype="text/xml")

    # ✅ Primer menú
    if not speech and not digits:
        g = Gather(
            input="speech dtmf",
            language="es-MX",
            timeout=4,
            speech_timeout="auto",
            action=f"{BASE_URL}/ivr",
            method="POST",
            bargeIn=True
        )
        say(g, "Hola. Dime Pablo, Vladimir o Ingeniero. También puedes marcar 1, 2 o 0.")
        vr.append(g)
        return Response(str(vr), mimetype="text/xml")

    text = normalize(speech)
    logging.info(f"USER: speech='{text}' digits='{digits}'")

    # ✅ DTMF
    if digits == "1":
        return transfer_to_did(vr, DID_MAP["pablo"])
    if digits == "2":
        return transfer_to_did(vr, DID_MAP["vladimir"])
    if digits == "0":
        return transfer_to_did(vr, DID_MAP["ingeniero"])

    # ✅ Voz
    if "pablo" in text:
        return transfer_to_did(vr, DID_MAP["pablo"])
    if "vladimir" in text:
        return transfer_to_did(vr, DID_MAP["vladimir"])
    if "ingeniero" in text:
        return transfer_to_did(vr, DID_MAP["ingeniero"])

    # ✅ Reintento
    g = Gather(
        input="speech dtmf",
        language="es-MX",
        timeout=4,
        speech_timeout="auto",
        action=f"{BASE_URL}/ivr",
        method="POST",
        bargeIn=True
    )
    say(g, "Perdón, no entendí. Dime Pablo, Vladimir o Ingeniero. O marca 1, 2 o 0.")
    vr.append(g)
    return Response(str(vr), mimetype="text/xml")

@app.route("/")
def home():
    return "IVR OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)




