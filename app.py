from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import re
import logging

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# ✅ Dominio/IP donde Twilio envía la troncal SIP
PBX_DOMAIN = "TU_PBX_DOMAIN_O_IP"

# ✅ Mapa de DID por destino lógico
DID_MAP = {
    "pablo": "5000",
    "vladimir": "5001",
    "ingeniero": "5999"
}

def say(vr, text):
    vr.say(text, language="es-MX", voice="Polly.Mia")

def transfer_to_did(vr, did):
    say(vr, f"Listo. Te transfiero ahora.")
    d = vr.dial()
    # ✅ Esto es lo que hace que entre como DID al PBX
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

    # ✅ Primera interacción / Recolectar voz o teclas
    if not speech and not digits:
        g = Gather(
            input="speech dtmf",
            language="es-MX",
            timeout=4,
            speech_timeout="auto",
            action="/ivr",
            method="POST",
            bargeIn=True
        )
        say(g, "Hola. Dime Pablo, Vladimir, o Ingeniero. También puedes marcar 1, 2 o 0.")
        vr.append(g)
        return Response(str(vr), mimetype="text/xml")

    text = normalize(speech)
    logging.info(f"USER: speech='{text}' digits='{digits}'")

    # ✅ Ruteo por DTMF
    if digits == "1":
        return transfer_to_did(vr, DID_MAP["pablo"])
    if digits == "2":
        return transfer_to_did(vr, DID_MAP["vladimir"])
    if digits == "0":
        return transfer_to_did(vr, DID_MAP["ingeniero"])

    # ✅ Ruteo por voz (palabras clave)
    if "pablo" in text:
        return transfer_to_did(vr, DID_MAP["pablo"])
    if "vladimir" in text:
        return transfer_to_did(vr, DID_MAP["vladimir"])
    if "ingeniero" in text:
        return transfer_to_did(vr, DID_MAP["ingeniero"])

    # ✅ No se entendió: reintento
    g = Gather(
        input="speech dtmf",
        language="es-MX",
        timeout=4,
        speech_timeout="auto",
        action="/ivr",
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
    app.run(host="0.0.0.0", port=5000)




