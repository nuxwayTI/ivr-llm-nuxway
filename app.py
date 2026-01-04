from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import re

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# =====================================================
# ✅ PBX_DOMAIN (SIP Domain de Twilio que YA funciona)
# =====================================================
# Como en tu log funciona: sip:6049@nuxway.sip.twilio.com
# entonces transferimos a: sip:5000@nuxway.sip.twilio.com
PBX_DOMAIN = "nuxway.sip.twilio.com"

# =====================================================
# ✅ DIDs (usuarios SIP) para tu PBX
# =====================================================
DID_MAP = {
    "pablo": "5000",
    "vladimir": "5001",
    "ingeniero": "5999"
}

# =====================================================
# Helpers
# =====================================================
def say(vr, text):
    vr.say(text, language="es-MX", voice="Polly.Mia")

def normalize(text):
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-záéíóúñ0-9 ]+", "", text)
    return text

def transfer_to_did(vr, did):
    """
    Transfiere a DID usando el mismo SIP Domain que ya funciona.
    Ej: sip:5000@nuxway.sip.twilio.com
    """
    say(vr, "Perfecto, te comunico.")
    d = vr.dial()
    destino = f"sip:{did}@{PBX_DOMAIN}"
    logging.warning(f"TRANSFER -> {destino}")
    d.sip(destino)
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
    say(g, "Hola. ¿Con quién quieres comunicarte? Di Pablo, Vladimir o Ingeniero. También puedes marcar 1, 2 o 0.")
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

# =====================================================
# RUTA PRINCIPAL
# =====================================================
@app.route("/ivr-llm", methods=["GET", "POST"])
def ivr_llm():
    vr = VoiceResponse()

    try:
        # GET sirve para probar en navegador
        if request.method == "GET":
            say(vr, "IVR OK.")
            return Response(str(vr), mimetype="text/xml")

        speech = request.values.get("SpeechResult")
        digits = request.values.get("Digits")
        call_sid = request.values.get("CallSid", "unknown")

        # Primer menú
        if not speech and not digits:
            vr.append(gather_menu())
            return Response(str(vr), mimetype="text/xml")

        text = normalize(speech)
        logging.info(f"[CALL {call_sid}] speech='{text}' digits='{digits}'")

        # ✅ DTMF routing
        if digits == "1":
            return transfer_to_did(vr, DID_MAP["pablo"])
        if digits == "2":
            return transfer_to_did(vr, DID_MAP["vladimir"])
        if digits == "0":
            return transfer_to_did(vr, DID_MAP["ingeniero"])

        # ✅ Voice routing
        if "pablo" in text:
            return transfer_to_did(vr, DID_MAP["pablo"])
        if "vladimir" in text:
            return transfer_to_did(vr, DID_MAP["vladimir"])
        if "ingeniero" in text:
            return transfer_to_did(vr, DID_MAP["ingeniero"])

        # Si no entendió
        vr.append(gather_retry())
        return Response(str(vr), mimetype="text/xml")

    except Exception as e:
        logging.exception(f"ERROR en /ivr-llm: {e}")
        say(vr, "Hubo un problema técnico. Intenta nuevamente.")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

# =====================================================
# Debug
# =====================================================
@app.route("/debug", methods=["GET"])
def debug():
    return f"PBX_DOMAIN='{PBX_DOMAIN}'"

@app.route("/", methods=["GET"])
def home():
    return "OK"

# =====================================================
# RUN (Render)
# =====================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
