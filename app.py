from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather, Dial
import os
import logging
import re

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# ✅ Endpoint SIP real (el que ya funciona)
SIP_ENDPOINT = "sip:6049@nuxway.sip.twilio.com"

# ✅ CallerID que mandaremos al PBX para enrutar internamente
DID_MAP = {
    "pablo": "5000",
    "gonzalo": "5001",
    "vladimir": "5002",
    "cola": "4999"   # 👈 0 será soporte/cola
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

def transfer_with_callerid(vr, callerid):
    """
    ✅ Siempre transfiere al mismo SIP_ENDPOINT (6049)
    pero cambia el callerId para que el PBX rutee según regla.
    """
    say(vr, "Perfecto, le comunico.")
    d = Dial(callerId=callerid)
    d.sip(SIP_ENDPOINT)
    vr.append(d)

    logging.warning(f"TRANSFER -> {SIP_ENDPOINT} | callerId={callerid}")
    return Response(str(vr), mimetype="text/xml")

def gather_menu(action_url):
    """
    Menú principal profesional
    """
    g = Gather(
        input="speech dtmf",
        language="es-MX",
        timeout=4,
        speech_timeout="auto",
        action=action_url,
        method="POST",
        bargeIn=True,
        action_on_empty_result=True
    )
    say(g, "Gracias por llamar a Nuxway Technology. Di Pablo, Gonzalo o Vladimir, o marque 1, 2 o 3. Para soporte, marque 0.")
    return g

def gather_retry(action_url):
    """
    Reintento (cuando dijo algo pero no coincide)
    """
    g = Gather(
        input="speech dtmf",
        language="es-MX",
        timeout=4,
        speech_timeout="auto",
        action=action_url,
        method="POST",
        bargeIn=True,
        action_on_empty_result=True
    )
    say(g, "Disculpe, no lo entendí. Di Pablo, Gonzalo o Vladimir, o marque 1, 2 o 3. Para soporte, marque 0.")
    return g

# =====================================================
# RUTA PRINCIPAL
# =====================================================
@app.route("/ivr-llm", methods=["GET", "POST"])
def ivr_llm():
    vr = VoiceResponse()

    try:
        # ✅ GET: prueba en navegador
        if request.method == "GET":
            say(vr, "IVR OK.")
            return Response(str(vr), mimetype="text/xml")

        speech = request.values.get("SpeechResult")
        digits = request.values.get("Digits")
        call_sid = request.values.get("CallSid", "unknown")

        # Detectar intento
        attempt = int(request.args.get("attempt", "1"))

        # ✅ Si viene vacío (silencio)
        if not speech and not digits:
            if attempt == 1:
                # Menú 1
                vr.append(gather_menu("/ivr-llm?attempt=2"))
                return Response(str(vr), mimetype="text/xml")
            else:
                # Menú repetido 1 vez y si sigue vacío -> cuelga
                say(vr, "No hemos recibido respuesta. Gracias por llamar a Nuxway Technology. Hasta luego.")
                vr.hangup()
                return Response(str(vr), mimetype="text/xml")

        text = normalize(speech)
        logging.info(f"[CALL {call_sid}] attempt={attempt} speech='{text}' digits='{digits}'")

        # ✅ Ruteo por teclas
        if digits == "1":
            return transfer_with_callerid(vr, DID_MAP["pablo"])
        if digits == "2":
            return transfer_with_callerid(vr, DID_MAP["gonzalo"])
        if digits == "3":
            return transfer_with_callerid(vr, DID_MAP["vladimir"])
        if digits == "0":
            return transfer_with_callerid(vr, DID_MAP["cola"])

        # ✅ Ruteo por voz
        if "pablo" in text:
            return transfer_with_callerid(vr, DID_MAP["pablo"])
        if "gonzalo" in text:
            return transfer_with_callerid(vr, DID_MAP["gonzalo"])
        if "vladimir" in text:
            return transfer_with_callerid(vr, DID_MAP["vladimir"])
        if "soporte" in text or "cola" in text or "ingeniero" in text:
            return transfer_with_callerid(vr, DID_MAP["cola"])

        # ✅ Si dijo algo pero no coincide, reintenta una vez
        if attempt == 1:
            vr.append(gather_retry("/ivr-llm?attempt=2"))
            return Response(str(vr), mimetype="text/xml")

        # ✅ Si ya reintentó y sigue sin coincidir -> manda a soporte
        say(vr, "Lo comunico con soporte.")
        return transfer_with_callerid(vr, DID_MAP["cola"])

    except Exception as e:
        # ✅ Para evitar que Twilio diga "Application error"
        logging.exception(f"ERROR en /ivr-llm: {e}")
        say(vr, "Hubo un problema técnico. Intente nuevamente.")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

# =====================================================
# HOME
# =====================================================
@app.route("/", methods=["GET"])
def home():
    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

