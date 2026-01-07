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
# RUTEO (callerId)
# =========================
DID_MAP = {
    "pablo": "4000",
    "gonzalo": "4001",
    "vladimir": "5102",
    "paola": "5103",
    "ximena": "5104",
    "cola": "5109"   # ✅ soporte/cola
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
- VoIP: centrales IP, teléfonos IP, gateways, troncales SIP, soluciones móviles.
- Desarrollo a medida: reportes, SMS, automatización de llamadas e integraciones.
- Redes y seguridad: firewalls, routers, switches, access points.
- Diseño/configuración de redes de telefonía, datos y seguridad.
- Consultoría y soporte técnico, mantenimiento, relevamientos, cableado estructurado.
- Web básico/presencia digital, CRM/ERP.

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
    text = re.sub(r"[^a-záéíóúñ0-9 ]+", "", text)
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

def transfer_with_callerid(vr, callerid):
    say(vr, "Perfecto, le comunico.")
    d = Dial(callerId=callerid)
    d.sip(SIP_ENDPOINT)
    vr.append(d)
    logging.warning(f"TRANSFER -> {SIP_ENDPOINT} | callerId={callerid}")
    return Response(str(vr), mimetype="text/xml")

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
        num_digits=1,
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
        "Para soporte, marque cero o diga soporte."
    )
    say(g, msg)
    return g

def gather_retry(action_url):
    g = Gather(
        input="dtmf speech",
        num_digits=1,
        language="es-MX",
        timeout=6,
        speech_timeout="auto",
        action=action_url,
        method="POST",
        bargeIn=True,
        action_on_empty_result=True
    )
    say(g, "Disculpe, no lo entendí. Diga el nombre de la persona. Para soporte, marque cero o diga soporte.")
    return g

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

        speech = request.values.get("SpeechResult")
        digits = request.values.get("Digits")
        call_sid = request.values.get("CallSid", "unknown")
        attempt = int(request.args.get("attempt", "1"))

        logging.warning(f"[DTMF] digits recibido: {digits}")

        if not speech and not digits:
            if attempt == 1:
                vr.append(gather_menu("/ivr-llm?attempt=2"))
                return Response(str(vr), mimetype="text/xml")
            else:
                say(vr, "No hemos recibido respuesta. Gracias por llamar a Nuxway Technology. Hasta luego.")
                vr.hangup()
                return Response(str(vr), mimetype="text/xml")

        text = normalize(speech)
        logging.info(f"[CALL {call_sid}] attempt={attempt} speech='{text}' digits='{digits}' llm_turns={llm_turns[call_sid]}")

        # =========================
        # 1) DTMF routing (se mantiene por compatibilidad)
        # =========================
        if digits == "4000":
            return transfer_with_callerid(vr, DID_MAP["pablo"])
        if digits == "4001":
            return transfer_with_callerid(vr, DID_MAP["gonzalo"])
        if digits == "4002":
            return transfer_with_callerid(vr, DID_MAP["vladimir"])
        if digits == "4003":
            return transfer_with_callerid(vr, DID_MAP["paola"])
        if digits == "4007":
            return transfer_with_callerid(vr, DID_MAP["ximena"])
        if digits == "0":
            return transfer_with_callerid(vr, DID_MAP["cola"])

        # =========================
        # 2) Voice: si dicen "soporte" (explícito) -> soporte SIEMPRE
        # =========================
        if any(k in text for k in ["soporte", "support", "ayuda", "mesa", "tecnico", "técnico", "cola"]):
            return transfer_with_callerid(vr, DID_MAP["cola"])

        # =========================
        # 3) Voice directo por nombre (prioridad sobre "ingeniero")
        # =========================
        for name in ["pablo", "gonzalo", "vladimir", "paola", "ximena"]:
            if name in text:
                return transfer_with_callerid(vr, DID_MAP[name])

        # =========================
        # 4) Fuzzy match para nombres (prioridad sobre "ingeniero")
        # =========================
        bm, score = detect_name_from_text(text)
        if bm and score >= 0.78:
            logging.warning(f"[CALL {call_sid}] FUZZY_NAME -> '{text}' => '{bm}' score={score:.2f}")
            return transfer_with_callerid(vr, DID_MAP[bm])

        # =========================
        # 4.1) Si piden "ingeniero" / "humano" / "operador" genérico -> soporte
        # (pero solo si no detectamos nombre)
        # =========================
        if any(k in text for k in ["ingeniero", "agente", "humano", "operador"]):
            return transfer_with_callerid(vr, DID_MAP["cola"])

        # =========================
        # 5) OpenAI fallback inteligente
        # =========================
        if llm_turns[call_sid] >= MAX_LLM_TURNS:
            say(vr, "Muchas gracias. Para continuar, lo comunico con soporte.")
            return transfer_with_callerid(vr, DID_MAP["cola"])

        llm_turns[call_sid] += 1
        respuesta = llamar_openai(call_sid, text)
        say(vr, respuesta)

        g = Gather(
            input="dtmf speech",
            num_digits=1,
            language="es-MX",
            timeout=6,
            speech_timeout="auto",
            action="/ivr-llm?attempt=1",
            method="POST",
            bargeIn=True,
            action_on_empty_result=True
        )
        say(g, "Diga el nombre de la persona con la que desea comunicarse. Para soporte, marque cero o diga soporte.")
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
