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
# TWILIO -> PBX (SIP)
# =========================
SIP_ENDPOINT = "sip:6049@nuxway.sip.twilio.com"

# =========================
# RUTEO (callerId interno para que Yeastar matchee)
# =========================
DID_MAP = {
    "pablo": "5100",
    "gonzalo": "5101",
    "vladimir": "5102",
    "paola": "5103",
    "ximena": "5104",
    "cola": "5109"   # soporte/cola
}

# Extensiones marcables por DTMF/voz -> persona
EXT_MAP = {
    "4000": "pablo",
    "4001": "gonzalo",
    "4002": "vladimir",
    "4003": "paola",
    "4007": "ximena",
    "0": "cola",
}

# =========================
# OPENAI
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
session = requests.Session()

# =========================
# MEMORIA / LIMITES
# =========================
conversaciones = defaultdict(list)
llm_turns = defaultdict(int)
MAX_LLM_TURNS = 3

# =========================
# PROMPT SISTEMA (Nuxway)
# =========================
SYSTEM_PROMPT = """
Eres el asistente telefónico de Nuxway Technology S.R.L.
Atiendes llamadas en español, tono profesional y humano, estilo IVR moderno.

Reglas:
- Respuestas cortas: 1 a 2 frases.
- Máximo 1 pregunta por turno.
- No suenes robótico.
- Si el usuario pide hablar con una persona específica (por nombre), transfiere con esa persona.
- Si el usuario pide soporte o un ingeniero (genérico), transfiere a soporte.

Información real (no inventar):
- Web: nuxway punto net
- Email: ventas@nuxway.net
- Teléfono: (591-4) 448362
- Celular: (591) 61786583
- Dirección: Calle Las Jarkas #204, Zona Mirador, Cochabamba-Bolivia

Servicios (resumir, no listar todo salvo que lo pidan):
- VoIP: centrales IP, teléfonos IP, gateways, troncales SIP, soluciones móviles, IVR inteligentes.
- Desarrollo a medida: reportes, SMS, automatización de llamadas e integraciones.
- Redes y seguridad: firewalls, routers, switches, access points.
- Diseño/configuración de redes de telefonía, datos y seguridad.
- Consultoría y soporte técnico, mantenimiento, relevamientos, cableado estructurado.
- Web básico/presencia digital, CRM/ERP.
- Diseño/configuración paginas web.
- Programacion de sistemas/app Web.

Cuando el usuario pregunte "qué hacen" o "servicios":
- responde con 1 resumen corto y ofrece ampliar si desea.

Si no estás seguro:
- “Para darle una respuesta correcta, prefiero comunicarlo con soporte.”
"""

# =========================
# HELPERS
# =========================
def say(vr, text):
    vr.say(text, language="es-MX", voice="Polly.Mia")

def normalize(text):
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-záéíóúñ0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()

# ✅ Alias nombres
NAME_ALIASES = {
    "pablo": ["pablo", "pavlo", "pabloo", "palo", "pabloh"],
    "gonzalo": ["gonzalo", "gonza", "gonsalo", "consalo", "gonzal", "gonzaloz"],
    "vladimir": ["vladimir", "bladimir", "pladimir", "vlad", "vladimír", "vladmir"],
    "paola": ["paola", "paula", "pa ola", "pau la", "pao la", "paolla"],
    "ximena": ["ximena", "xime", "xime na", "xi mena", "xim ena", "ximen a"],
}

def detect_name_from_text(text):
    words = text.split()
    candidates = words + [text]
    best_name = None
    best_score = 0.0

    for name, aliases in NAME_ALIASES.items():
        for alias in aliases:
            for c in candidates:
                score = similarity(c, alias)
                if score > best_score:
                    best_score = score
                    best_name = name

    return best_name, best_score

# Palabras -> dígitos (para "cuatro cero cero uno")
NUM_WORDS = {
    "cero": "0",
    "zero": "0",
    "uno": "1", "un": "1", "una": "1",
    "dos": "2",
    "tres": "3",
    "cuatro": "4",
    "cinco": "5",
    "seis": "6",
    "siete": "7",
    "ocho": "8",
    "nueve": "9",
}

def extract_extension_from_speech(text):
    """
    Acepta:
      - "4001"
      - "4 0 0 1"
      - "cuatro cero cero uno"
    Devuelve un string de dígitos (ej "4001") o None
    """
    if not text:
        return None

    # 1) bloque de dígitos: 4001, 4000, etc.
    m = re.search(r"\b\d{2,6}\b", text)
    if m:
        return m.group(0)

    tokens = text.split()

    # 2) tokens tipo "4 0 0 1"
    digit_tokens = [t for t in tokens if t.isdigit() and len(t) == 1]
    if len(digit_tokens) >= 2:
        return "".join(digit_tokens)

    # 3) palabras "cuatro cero cero uno"
    mapped = []
    for t in tokens:
        if t in NUM_WORDS:
            mapped.append(NUM_WORDS[t])
    if len(mapped) >= 2:
        return "".join(mapped)

    return None

def saludo_por_hora():
    tz = pytz.timezone("America/La_Paz")
    h = datetime.now(tz).hour
    if 5 <= h < 12:
        return "Buenos días."
    if 12 <= h < 19:
        return "Buenas tardes."
    return "Buenas noches."

def gather_menu(action_url):
    g = Gather(
        input="dtmf speech",
        num_digits=10,      # captura variable
        finish_on_key="#",  # termina con #
        language="es-MX",
        timeout=6,
        speech_timeout="auto",
        action=action_url,
        method="POST",
        bargeIn=True,
        action_on_empty_result=True
    )
    msg = (
        f"{saludo_por_hora()} Gracias por llamar a Nuxway Technology. "
        "Diga el nombre de la persona con la que desea comunicarse. "
        "Para soporte, marque cero o diga soporte. "
        "Si desea marcar una extensión, márquela y presione numeral."
    )
    say(g, msg)
    return g

def gather_retry(action_url):
    g = Gather(
        input="dtmf speech",
        num_digits=10,
        finish_on_key="#",
        language="es-MX",
        timeout=7,
        speech_timeout="auto",
        action=action_url,
        method="POST",
        bargeIn=True,
        action_on_empty_result=True
    )
    say(g, "Disculpe, no lo entendí. Diga el nombre, o para soporte marque cero o diga soporte. Para extensión, márquela y presione numeral.")
    return g

def transfer_legacy(vr, internal_callerid, original_from, call_sid):
    """
    LEGACY:
    - callerId = internal_callerid (5100/5101/..): así Yeastar enruta como lo tenías.
    - intenta adjuntar el caller real como parámetro SIP para debug/registro.
    """
    say(vr, "Perfecto, le comunico.")
    d = Dial(callerId=internal_callerid)

    sip = d.sip(SIP_ENDPOINT)
    # Si Yeastar lo soporta, puedes verlo; si no, no afecta.
    sip.parameter(name="X-Orig-From", value=str(original_from))
    sip.parameter(name="X-CallSid", value=str(call_sid))

    vr.append(d)
    logging.warning(
        f"TRANSFER LEGACY -> {SIP_ENDPOINT} | callerId(internal)={internal_callerid} | orig_from={original_from} | callSid={call_sid}"
    )
    return Response(str(vr), mimetype="text/xml")

def llamar_openai(call_sid, user_text):
    if not OPENAI_API_KEY:
        logging.error("[OPENAI] OPENAI_API_KEY VACIA")
        return "En este momento no tengo acceso al asistente inteligente. Lo comunico con soporte."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += conversaciones[call_sid]
    messages.append({"role": "user", "content": user_text})

    data = {
        "model": "gpt-4.1-mini",
        "messages": messages,
        "max_tokens": 140,
        "temperature": 0.25,
    }

    try:
        r = session.post(OPENAI_URL, json=data, headers=headers, timeout=15)
        logging.warning(f"[OPENAI] status={r.status_code} body={r.text[:200]}")

        if r.status_code != 200:
            return "Tengo un inconveniente técnico. Lo comunico con soporte."

        respuesta = r.json()["choices"][0]["message"]["content"].strip()

        conversaciones[call_sid].append({"role": "user", "content": user_text})
        conversaciones[call_sid].append({"role": "assistant", "content": respuesta})

        return respuesta

    except Exception as e:
        logging.exception(f"[OPENAI] EXCEPTION: {e}")
        return "Estoy teniendo un inconveniente técnico. Lo comunico con soporte."

# =========================
# DEBUG ENDPOINTS
# =========================
@app.route("/debug-openai", methods=["GET"])
def debug_openai():
    if not OPENAI_API_KEY:
        return "OPENAI_API_KEY NO CARGADA", 200
    return "OPENAI_API_KEY OK", 200

@app.route("/test-openai", methods=["GET"])
def test_openai():
    if not OPENAI_API_KEY:
        return jsonify({"ok": False, "error": "OPENAI_API_KEY NO CARGADA"}), 200

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "gpt-4.1-mini",
        "messages": [
            {"role": "system", "content": "Responde solo con 'OK'."},
            {"role": "user", "content": "test"}
        ],
        "max_tokens": 5,
        "temperature": 0
    }

    try:
        r = session.post(OPENAI_URL, json=data, headers=headers, timeout=15)
        return jsonify({
            "ok": r.status_code == 200,
            "status": r.status_code,
            "body": r.text[:500]
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200

# =========================
# MAIN IVR
# =========================
@app.route("/ivr-llm", methods=["GET", "POST"])
def ivr_llm():
    vr = VoiceResponse()

    try:
        if request.method == "GET":
            say(vr, "IVR OK.")
            return Response(str(vr), mimetype="text/xml")

        speech = request.values.get("SpeechResult") or ""
        digits = (request.values.get("Digits") or "").strip()
        call_sid = request.values.get("CallSid", "unknown")
        attempt = int(request.args.get("attempt", "1"))

        original_from = request.values.get("From", "")  # caller real (lo guardamos)
        text = normalize(speech)

        logging.warning(f"[DTMF] digits recibido: '{digits}'")
        logging.info(f"[CALL {call_sid}] attempt={attempt} from='{original_from}' speech='{text}' digits='{digits}' llm_turns={llm_turns[call_sid]}")

        if not text and not digits:
            if attempt == 1:
                vr.append(gather_menu("/ivr-llm?attempt=2"))
                return Response(str(vr), mimetype="text/xml")
            else:
                say(vr, "No hemos recibido respuesta. Gracias por llamar a Nuxway Technology. Hasta luego.")
                vr.hangup()
                return Response(str(vr), mimetype="text/xml")

        # =========================
        # 1) DTMF: extensiones completas (4000, 4001...) o 0
        # =========================
        if digits:
            if digits in EXT_MAP:
                who = EXT_MAP[digits]
                internal_callerid = DID_MAP[who]
                return transfer_legacy(vr, internal_callerid, original_from, call_sid)

        # =========================
        # 1.1) Voz: si dicen números (4001 / "4 0 0 1" / "cuatro cero...")
        # =========================
        ext_spoken = extract_extension_from_speech(text)
        if ext_spoken and ext_spoken in EXT_MAP:
            who = EXT_MAP[ext_spoken]
            internal_callerid = DID_MAP[who]
            return transfer_legacy(vr, internal_callerid, original_from, call_sid)

        # =========================
        # 2) Voz: "soporte" explícito -> soporte siempre
        # =========================
        if any(k in text for k in ["soporte", "support", "ayuda", "mesa", "tecnico", "técnico", "cola"]):
            return transfer_legacy(vr, DID_MAP["cola"], original_from, call_sid)

        # =========================
        # 3) Voz directo por nombre
        # =========================
        for name in ["pablo", "gonzalo", "vladimir", "paola", "ximena"]:
            if name in text:
                return transfer_legacy(vr, DID_MAP[name], original_from, call_sid)

        # =========================
        # 4) Fuzzy match para nombres
        # =========================
        bm, score = detect_name_from_text(text)
        if bm and score >= 0.78:
            logging.warning(f"[CALL {call_sid}] FUZZY_NAME -> '{text}' => '{bm}' score={score:.2f}")
            return transfer_legacy(vr, DID_MAP[bm], original_from, call_sid)

        # =========================
        # 4.1) "ingeniero" genérico -> soporte (si no hay nombre)
        # =========================
        if any(k in text for k in ["ingeniero", "agente", "humano", "operador"]):
            return transfer_legacy(vr, DID_MAP["cola"], original_from, call_sid)

        # =========================
        # 5) OpenAI fallback inteligente
        # =========================
        if llm_turns[call_sid] >= MAX_LLM_TURNS:
            say(vr, "Muchas gracias. Para continuar, lo comunico con soporte.")
            return transfer_legacy(vr, DID_MAP["cola"], original_from, call_sid)

        llm_turns[call_sid] += 1
        respuesta = llamar_openai(call_sid, text)
        say(vr, respuesta)

        g = Gather(
            input="dtmf speech",
            num_digits=10,
            finish_on_key="#",
            language="es-MX",
            timeout=7,
            speech_timeout="auto",
            action="/ivr-llm?attempt=1",
            method="POST",
            bargeIn=True,
            action_on_empty_result=True
        )
        say(g, "Diga el nombre. Para soporte marque cero o diga soporte. Para extensión, márquela y presione numeral.")
        vr.append(g)
        return Response(str(vr), mimetype="text/xml")

    except Exception as e:
        logging.exception(f"ERROR en /ivr-llm: {e}")
        say(vr, "Hubo un problema técnico. Gracias por llamar a Nuxway Technology.")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

# =========================
# HOME
# =========================
@app.route("/", methods=["GET"])
def home():
    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


