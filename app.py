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
    "pablo": "5100",
    "gonzalo": "5101",
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

def is_valid_callerid(s: str) -> bool:
    s = (s or "").strip()
    # 1) E.164: +591...
    if re.fullmatch(r"\+\d{8,15}", s):
        return True
    # 2) Extensiones: 2 a 8 dígitos (5100, 22, etc.)
    if re.fullmatch(r"\d{2,8}", s):
        return True
    # 3) SIP URI: sip:algo@dominio
    if re.fullmatch(r"sip:[^@;\s]+@[^;\s]+.*", s, flags=re.IGNORECASE):
        return True
    return False

def extract_number_from_sip_header(value: str) -> str:
    """
    Saca +NNN... de cosas tipo:
      <sip:+5917xxxxxxx@...>
      sip:+5917xxxxxxx@...
      "+5917xxxxxxx" <sip:...>
    """
    if not value:
        return ""
    m = re.search(r"\+\d{8,15}", value)
    return m.group(0) if m else ""

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

def transfer_with_callerid(vr, callerid=None, preserve_from=None, preserve_headers=None):
    """
    callerid:
      - si viene None => no seteamos callerId
      - si viene válido (E.164, extensión numérica, o SIP URI) => lo seteamos

    preserve_from:
      - valor request.values["From"] (por si quieres preservar)
    preserve_headers:
      - dict con headers tipo PAI/RPID/X-Original-Caller (por si quieres extraer +E164)
    """
    say(vr, "Perfecto, le comunico.")

    chosen_callerid = None

    # 1) Si callerid explícito es válido, úsalo
    if callerid is not None:
        c = str(callerid).strip()
        if is_valid_callerid(c):
            chosen_callerid = c
        else:
            logging.warning(f"[TRANSFER] callerId inválido recibido='{c}' -> NO se setea (evita corte)")

    # 2) Si NO hay callerid válido y quieres preservar, intenta sacar E.164 de headers
    if not chosen_callerid and preserve_headers:
        for k in ["X-Original-Caller", "X-ANI", "P-Asserted-Identity", "Remote-Party-ID"]:
            num = extract_number_from_sip_header(preserve_headers.get(k, ""))
            if is_valid_callerid(num):  # acá num sería +E164
                chosen_callerid = num
                logging.warning(f"[TRANSFER] callerId preservado desde header {k} => {chosen_callerid}")
                break

    # 3) Si aún no hay, y preserve_from trae un +E164, úsalo
    if not chosen_callerid and preserve_from:
        num = extract_number_from_sip_header(preserve_from)
        if is_valid_callerid(num):
            chosen_callerid = num
            logging.warning(f"[TRANSFER] callerId preservado desde From => {chosen_callerid}")

    # Crear Dial
    if chosen_callerid:
        d = Dial(callerId=chosen_callerid)
    else:
        d = Dial()

    d.sip(SIP_ENDPOINT)
    vr.append(d)

    logging.warning(f"TRANSFER -> {SIP_ENDPOINT} | callerId={chosen_callerid if chosen_callerid else '(default/none)'}")
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

        # ✅ LOG: caller según Twilio + headers SIP reenviados
        tw_from = request.values.get("From") or ""
        tw_to = request.values.get("To") or ""
        direction = request.values.get("Direction") or ""
        call_status = request.values.get("CallStatus") or ""

        sip_headers = {
            "X-Original-Caller": request.values.get("SipHeader_X-Original-Caller") or "",
            "X-ANI": request.values.get("SipHeader_X-ANI") or "",
            "P-Asserted-Identity": request.values.get("SipHeader_P-Asserted-Identity") or "",
            "Remote-Party-ID": request.values.get("SipHeader_Remote-Party-ID") or "",
        }

        logging.warning(
            f"[TWILIO] CallSid={call_sid} From={tw_from} To={tw_to} Direction={direction} Status={call_status}"
        )

        if any(sip_headers.values()):
            logging.warning(
                "[SIP_HEADERS] " +
                " | ".join([f"{k}={v}" for k, v in sip_headers.items() if v])
            )

        logging.info(f"[TWILIO][RAW_KEYS] {sorted(list(request.values.keys()))}")
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
        # 1) DTMF routing
        # =========================
        if digits == "4000":
            return transfer_with_callerid(vr, DID_MAP["pablo"], preserve_from=tw_from, preserve_headers=sip_headers)
        if digits == "4001":
            return transfer_with_callerid(vr, DID_MAP["gonzalo"], preserve_from=tw_from, preserve_headers=sip_headers)
        if digits == "4002":
            return transfer_with_callerid(vr, DID_MAP["vladimir"], preserve_from=tw_from, preserve_headers=sip_headers)
        if digits == "4003":
            return transfer_with_callerid(vr, DID_MAP["paola"], preserve_from=tw_from, preserve_headers=sip_headers)
        if digits == "4007":
            return transfer_with_callerid(vr, DID_MAP["ximena"], preserve_from=tw_from, preserve_headers=sip_headers)
        if digits == "0":
            return transfer_with_callerid(vr, DID_MAP["cola"], preserve_from=tw_from, preserve_headers=sip_headers)

        # =========================
        # 2) Voice: soporte
        # =========================
        if any(k in text for k in ["soporte", "support", "ayuda", "mesa", "tecnico", "técnico", "cola"]):
            return transfer_with_callerid(vr, DID_MAP["cola"], preserve_from=tw_from, preserve_headers=sip_headers)

        # =========================
        # 3) Voice directo por nombre
        # =========================
        for name in ["pablo", "gonzalo", "vladimir", "paola", "ximena"]:
            if name in text:
                return transfer_with_callerid(vr, DID_MAP[name], preserve_from=tw_from, preserve_headers=sip_headers)

        # =========================
        # 4) Fuzzy match nombres
        # =========================
        bm, score = detect_name_from_text(text)
        if bm and score >= 0.78:
            logging.warning(f"[CALL {call_sid}] FUZZY_NAME -> '{text}' => '{bm}' score={score:.2f}")
            return transfer_with_callerid(vr, DID_MAP[bm], preserve_from=tw_from, preserve_headers=sip_headers)

        # =========================
        # 4.1) humano/operador -> soporte
        # =========================
        if any(k in text for k in ["ingeniero", "agente", "humano", "operador"]):
            return transfer_with_callerid(vr, DID_MAP["cola"], preserve_from=tw_from, preserve_headers=sip_headers)

        # =========================
        # 5) OpenAI fallback
        # =========================
        if llm_turns[call_sid] >= MAX_LLM_TURNS:
            say(vr, "Muchas gracias. Para continuar, lo comunico con soporte.")
            return transfer_with_callerid(vr, DID_MAP["cola"], preserve_from=tw_from, preserve_headers=sip_headers)

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



