from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import re
import logging
import os

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# =========================
# CONFIGURACIÓN (Render ENV)
# =========================
# En Render, pon estas variables:
# PBX_DOMAIN = tu_ip_o_dominio_del_pbx
# Ej: PBX_DOMAIN=190.xxx.xxx.xxx  ó PBX_DOMAIN=pbx.tudominio.com
PBX_DOMAIN = os.getenv("PBX_DOMAIN", "").strip()

# =========================
# MAPEO DE DIDs
# =========================
DID_MAP = {
    "pablo": "5000",
    "vladimir": "5001",
    "ingeniero": "5999"
}

# =========================
# HELPERS
# =========================
def say(vr, text):
    vr.say(text, language="es-MX", voice="Polly.Mia")

def normalize(text):
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-záéíóúñ0-9 ]+", "", text)
    return text

def transfer_to_did(vr, did):
    """
    Transfiere por troncal SIP enviando el DID en el SIP URI:
    INVITE sip:5000@PBX_DOMAIN
    """
    say(vr, "Listo. Te transfiero ahora.")
    d = vr.dial()
    d.sip(f"sip:{did}@{PBX_DOMAIN}")
    return Response(str(vr), mimetype="text/xml")

def gather_menu():
    """
    Retorna un Gather listo para pedir voz o DTMF.
    """
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
    """
    Reintento cuando no entendió.
    """
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

# =========================
# RUTA PRINCIPAL IVR
# =========================
@app.route("/ivr-llm", methods=["GET", "POST"])
def ivr_llm():
    vr = VoiceResponse()

    # ✅ SIEMPRE RESPONDER TwiML (Evita application error)
    try:
        call_sid = request.values.get("CallSid", "unknown")
        speech = request.values.get("SpeechResult")
        digits = request.values.get("Digits")

        logging.info(f"[CALL {call_sid}] Incoming | Speech='{speech}' Digits='{digits}'")

        # Validación de PBX_DOMAIN
        if not PBX_DOMAIN:
            say(vr, "Error. Falta configurar PBX DOMAIN en el servidor.")
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # Si es primera vez, no hay speech ni digits
        if not speech and not digits:
            vr.append(gather_menu())
            return Response(str(vr), mimetype="text/xml")

        # Normaliza speech (puede venir None)
        text = normalize(speech)

        logging.info(f"[CALL {call_sid}] USER normalized speech='{text}' digits='{digits}'")

        # ====== RUTE0 POR DTMF ======
        if digits == "1":
            logging.info(f"[CALL {call_sid}] Route -> Pablo (DID 5000)")
            return transfer_to_did(vr, DID_MAP["pablo"])

        if digits == "2":
            logging.info(f"[CALL {call_sid}] Route -> Vladimir (DID 5001)")
            return transfer_to_did(vr, DID_MAP["vladimir"])

        if digits == "0":
            logging.info(f"[CALL {call_sid}] Route -> Ingeniero (DID 5999)")
            return transfer_to_did(vr, DID_MAP["ingeniero"])

        # ====== RUTEO POR VOZ ======
        if "pablo" in text:
            logging.info(f"[CALL {call_sid}] Route -> Pablo (DID 5000)")
            return transfer_to_did(vr, DID_MAP["pablo"])

        if "vladimir" in text:
            logging.info(f"[CALL {call_sid}] Route -> Vladimir (DID 5001)")
            return transfer_to_did(vr, DID_MAP["vladimir"])

        if "ingeniero" in text:
            logging.info(f"[CALL {call_sid}] Route -> Ingeniero (DID 5999)")
            return transfer_to_did(vr, DID_MAP["ingeniero"])

        # ====== NO ENTENDIÓ ======
        vr.append(gather_retry())
        return Response(str(vr), mimetype="text/xml")

    except Exception as e:
        # ✅ Nunca dejar que Flask tire 500 sin TwiML
        logging.exception(f"ERROR en /ivr-llm: {e}")
        say(vr, "Hubo un problema técnico. Intenta nuevamente.")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

# =========================
# HOME HEALTHCHECK
# =========================
@app.route("/", methods=["GET"])
def home():
    return "IVR TEST OK"

# =========================
# RUN LOCAL / RENDER
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)



