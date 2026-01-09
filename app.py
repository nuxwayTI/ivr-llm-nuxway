from flask import Flask, request, Response, jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather, Dial
import os
import logging
import re
import requests
from collections import defaultdict
from difflib import SequenceMatcher
from datetime import datetime
import pytz

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# =========================
# TWILIO SIP DOMAIN
# =========================
SIP_DOMAIN = "nuxway.sip.twilio.com"

# =========================
# CALLER ID FALLBACKS
# =========================
DEFAULT_EXTERNAL_FALLBACK_CALLER = f"sip:ivr@{SIP_DOMAIN}"
DEFAULT_INTERNAL_FALLBACK_CALLER = "5109"

INTERNAL_EXT_MIN_LEN = 2
INTERNAL_EXT_MAX_LEN = 6
EXTERNAL_NUM_MIN_LEN = 7
EXTERNAL_NUM_MAX_LEN = 15

# =========================
# RUTEO (DESTINO SIP)
# =========================
DID_MAP = {
    "pablo": "5100",
    "gonzalo": "5101",
    "vladimir": "5102",
    "paola": "5103",
    "ximena": "5104",
    "cola": "6049"
}

# =========================
# TUNING AUDIO / GSM
# =========================
INITIAL_PAUSE_SEC = 1.4
RETRY_PAUSE_SEC = 0.9
GATHER_TIMEOUT = 15
SPEECH_TIMEOUT = "auto"
MAX_NOINPUT_ATTEMPTS = 3
MAX_LLM_TURNS = 3

# =========================
# OPENAI
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
session = requests.Session()

# =========================
# MEMORIA
# =========================
conversaciones = defaultdict(list)
llm_turns = defaultdict(int)

# =========================
# PROMPT SISTEMA (MEJORADO)
# =========================
SYSTEM_PROMPT = """
Eres el asistente telefónico de Nuxway Technology S.R.L.
Atiendes llamadas en español, con tono profesional, humano y claro.
Eres un IVR moderno, paciente y colaborativo.

Reglas de conversación:
- Respuestas cortas (1 a 2 frases).
- Máximo una pregunta por turno.
- No suenes robótico.
- Si el usuario pide una persona por nombre, transfiere directamente.
- Si pide soporte, ingeniero u operador, transfiere a soporte.

Información de la empresa:
- Nuxway Technology S.R.L. es distribuidor oficial de Yeastar.
- Vendemos y configuramos servidores de comunicaciones unificadas Yeastar.
  - Series Yeastar P y Yeastar S.
  - Soluciones Cloud y On-Premise.
  - Más información: yeastar.com

Infraestructura de red:
- Routers, firewalls, switches y access points (AP).
- Cableado estructurado.
- Diseño, implementación y mantenimiento de redes de datos IP.

Soporte y servicios:
- Soporte técnico especializado.
- Mantenimiento preventivo y correctivo.
- Consultoría en comunicaciones empresariales.

Innovación y desarrollo:
- Área de innovación y desarrollo propio.
- Desarrollo de aplicaciones web.
- Integraciones y automatización para mejorar las comunicaciones empresariales.
- Integración avanzada con servidores Yeastar.

Si el usuario pregunta “qué hacen” o “servicios”:
- Responde con un resumen corto y ofrece ampliar.

Si no estás seguro de algo:
- “Para darle una respuesta correcta, prefiero comunicarlo con soporte.”
"""

# =========================
# HELPERS
# =========================
def say(vr, text):
    vr.say(text, language="es-MX", voice="Polly.Mia")

def normalize(text):
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-záéíóúñ0-9 ]+", "", text)
    return text

def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()

def build_sip_uri(user):
    return f"sip:{user}@{SIP_DOMAIN}"

def extract_digits_from_sip_from(tw_from):
    m = re.search(r"^sip:(\d+)@", tw_from or "")
    return m.group(1) if m else ""

def get_original_caller(tw_from):
    digits = extract_digits_from_sip_from(tw_from)
    if digits and len(digits) >= EXTERNAL_NUM_MIN_LEN:
        return digits
    return ""

def is_internal_sip_from(tw_from):
    digits = extract_digits_from_sip_from(tw_from)
    return digits and len(digits) <= INTERNAL_EXT_MAX_LEN

def choose_caller_for_transfer(caller_real, tw_from):
    if caller_real:
        return caller_real
    if is_internal_sip_from(tw_from):
        return DEFAULT_INTERNAL_FALLBACK_CALLER
    return DEFAULT_EXTERNAL_FALLBACK_CALLER

# =========================
# ALIAS DE NOMBRES
# =========================
NAME_ALIASES = {
    "pablo": ["pablo", "pavlo", "pabloo"],
    "gonzalo": ["gonzalo", "gonza"],
    "vladimir": ["vladimir", "vlad"],
    "paola": ["paola", "paula"],
    "ximena": ["ximena", "xime"],
}

def detect_name_from_text(text):
    best_name, best_score = None, 0
    for name, aliases in NAME_ALIASES.items():
        for a in aliases:
            s = similarity(text, a)
            if s > best_score:
                best_name, best_score = name, s
    return best_name, best_score

def saludo_por_hora():
    h = datetime.now(pytz.timezone("America/La_Paz")).hour
    if h < 12:
        return "Buenos días."
    if h < 19:
        return "Buenas tardes."
    return "Buenas noches."

def gather_prompt(action_url, prompt):
    g = Gather(
        input="dtmf speech",
        language="es-MX",
        timeout=GATHER_TIMEOUT,
        speech_timeout=SPEECH_TIMEOUT,
        action=action_url,
        method="POST",
        bargeIn=True,
        action_on_empty_result=True,
        speech_model="phone_call",
        enhanced=True
    )
    say(g, prompt)
    return g

def transfer_to_user(vr, target_user, caller_real, tw_from):
    sip_target = build_sip_uri(target_user)
    say(vr, "Perfecto, le comunico.")
    caller_id = choose_caller_for_transfer(caller_real, tw_from)
    d = Dial(callerId=caller_id)
    d.sip(sip_target)
    vr.append(d)
    logging.warning(f"TRANSFER -> {sip_target} | callerId={caller_id}")
    return Response(str(vr), mimetype="text/xml")

# =========================
# MAIN IVR
# =========================
@app.route("/ivr-llm", methods=["GET", "POST"])
def ivr_llm():
    vr = VoiceResponse()

    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")
    confidence = request.values.get("Confidence")
    call_sid = request.values.get("CallSid", "unknown")
    noinput = int(request.args.get("noinput", "1"))

    tw_from = request.values.get("From") or ""
    caller_real = get_original_caller(tw_from)

    logging.info(f"[STT] speech='{speech}' confidence={confidence}")

    if not speech and not digits:
        if noinput <= MAX_NOINPUT_ATTEMPTS:
            vr.pause(length=INITIAL_PAUSE_SEC if noinput == 1 else RETRY_PAUSE_SEC)
            vr.append(gather_prompt(
                f"/ivr-llm?noinput={noinput+1}",
                f"{saludo_por_hora()} Dígame el nombre de la persona o diga soporte."
            ))
            return Response(str(vr), mimetype="text/xml")
        return transfer_to_user(vr, DID_MAP["cola"], caller_real, tw_from)

    text = normalize(speech)

    for name in DID_MAP:
        if name in text:
            return transfer_to_user(vr, DID_MAP[name], caller_real, tw_from)

    bm, score = detect_name_from_text(text)
    if bm and score >= 0.78:
        return transfer_to_user(vr, DID_MAP[bm], caller_real, tw_from)

    say(vr, "Disculpe, ¿con quién desea comunicarse?")
    vr.append(gather_prompt("/ivr-llm?noinput=1", "Diga el nombre o diga soporte."))
    return Response(str(vr), mimetype="text/xml")

# =========================
# HOME
# =========================
@app.route("/", methods=["GET"])
def home():
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))



