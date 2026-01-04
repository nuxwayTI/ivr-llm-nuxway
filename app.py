from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather, Dial
import os
import logging
import re

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# ✅ Tu endpoint SIP real (el que ya funciona)
SIP_ENDPOINT = "sip:6049@nuxway.sip.twilio.com"

# ✅ Mapa de DID lógico que enviaremos como callerId
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

def transfer_with_callerid(vr, did):
    """
    ✅ Transfiere SIEMPRE al SIP_ENDPOINT (6049)
    pero cambia el callerId para que el PBX detecte a qué interno enviar.
    """
    say(vr, "Perfecto, te comunico.")

    # 🔥 callerId = did
    d = Dial(callerId=did)
    d.sip(SIP_ENDPOINT)

    vr.append(d)

    logging.warning(f"TRANSFER -> {SIP_ENDPOINT} | callerId={did}")
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

@app.route("/ivr-llm", methods=["GET", "POST"])
def ivr_llm():
    vr = VoiceResponse()

    if request.method == "GET":
        say(vr, "IVR OK.")
        return Response(str(vr), mimetype="text/xml")

    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")
    call_sid = request.values.get("CallSid", "unknown")

    if not speech and not digits:
        vr.append(gather_menu())
        return Response(str(vr), mimetype="text/xml")

    text = normalize(speech)
    logging.info(f"[CALL {call_sid}] speech='{text}' digits='{digits}'")

    # ✅ DTMF routing
    if digits == "1":
        return transfer_with_callerid(vr, DID_MAP["pablo"])
    if digits == "2":
        return transfer_with_callerid(vr, DID_MAP["vladimir"])
    if digits == "0":
        return transfer_with_callerid(vr, DID_MAP["ingeniero"])

    # ✅ Voice routing
    if "pablo" in text:
        return transfer_with_callerid(vr, DID_MAP["pablo"])
    if "vladimir" in text:
        return transfer_with_callerid(vr, DID_MAP["vladimir"])
    if "ingeniero" in text:
        return transfer_with_callerid(vr, DID_MAP["ingeniero"])

    vr.append(gather_retry())
    return Response(str(vr), mimetype="text/xml")

@app.route("/", methods=["GET"])
def home():
    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

