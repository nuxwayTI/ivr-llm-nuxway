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
DEFAULT_FALLBACK_CALLER_SIP = f"sip:ivr@{SIP_DOMAIN}"  # ✅ evita cortes cuando no hay +E164

# =========================
# RUTEO (destino SIP por interno)
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
# TUNING PARA GSM / AUDIO LENTO
# =========================
INITIAL_PAUSE_SEC = 1.4     # pausa antes del primer prompt (sube a 2.0 si hace falta)
RETRY_PAUSE_SEC = 0.9       # pausa antes de reintento
GATHER_TIMEOUT = 12         # tiempo para que empiece a hablar / marcar
SPEECH_TIMEOUT = "3"        # espera 3s de silencio antes de cerrar speech (mejor en GSM)
MAX_NOINPUT_ATTEMPTS = 3    # cuántas veces tolerar silencio total
MAX_LLM_TURNS = 3           # igual que antes

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

def is_valid_e164(s: str) -> bool:
    return bool(re.fullmatch(r"\+\d{8,15}", (s or "").strip()))

def build_sip_uri(user: str) -> str:
    user = (user or "").strip()
    return f"sip:{user}@{SIP_DOMAIN}"

def extract_e164(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"\+\d{8,15}", text)
    return m.group(0) if m else ""

def get_original_caller(tw_from: str, sip_headers: dict) -> str:
    # 1) From directo
    num = extract_e164(tw_from)
    if is_valid_e164(num):
        return num

    # 2) Headers (si tu PBX los manda)
    for k in ["P-Asserted-Identity", "Remote-Party-ID", "X-Original-Caller", "X-ANI"]:
        num = extract_e164(sip_headers.get(k, ""))
        if is_valid_e164(num):
            return num

    return ""

def saludo_por_hora():
    tz = pytz.timezone("America/La_Paz")
    h = datetime.now(tz).hour
    if 5 <= h < 12:
        return "Buenos días."
    if 12 <= h < 19:
        return "Buenas tardes."
    return "Buenas noches."

def gather_prompt(action_url: str, prompt_text: str):
    g = Gather(
        input="dtmf speech",
        num_digits=1,
        language="es-MX",
        timeout=GATHER_TIMEOUT,
        speech_timeout=SPEECH_TIMEOUT,
        action=action_url,
        method="POST",
        bargeIn=True,
        action_on_empty_result=True
    )
    say(g, prompt_text)
    return g

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

def transfer_to_user(vr, target_user: str, caller_real_e164: str):
    """
    Transfiere a sip:<target_user>@nuxway.sip.twilio.com

    ✅ callerId:
      - si hay +E164 real => úsalo
      - si NO hay => usa un SIP caller fijo válido (evita cortes)
    """
    sip_target = build_sip_uri(target_user)

    say(vr, "Perfecto, le comunico.")

    caller_for_dial = caller_real_e164 if caller_real_e164 else DEFAULT_FALLBACK_CALLER_SIP
    d = Dial(callerId=caller_for_dial)
    d.sip(sip_target)
    vr.append(d)

    logging.warning(f"TRANSFER -> {sip_target} | callerId={caller_for_dial}")
    return Response(str(vr), mimetype="text/xml")

def llamar_openai(call_sid, user_text):
    if not OPENAI_API_KEY:
        logging.error("[OPENAI] OPENAI_API_KEY VACIA")
        return "En este momento no tengo acceso al asistente inteligente. Lo comunico con soporte."

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

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

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
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
        return jsonify({"ok": r.status_code == 200, "status": r.status_code, "body": r.text[:500]}), 200
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

        # intentos:
        # - noinput: reintentos por silencio total
        noinput = int(request.args.get("noinput", "1"))

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

        caller_real = get_original_caller(tw_from, sip_headers)

        logging.warning(
            f"[TWILIO] CallSid={call_sid} From={tw_from} To={tw_to} caller_real={caller_real or '(none)'} "
            f"Direction={direction} Status={call_status} noinput={noinput}"
        )
        if any(sip_headers.values()):
            logging.warning("[SIP_HEADERS] " + " | ".join([f"{k}={v}" for k, v in sip_headers.items() if v]))

        logging.warning(f"[DTMF] digits recibido: {digits}")

        # =========================
        # SILENCIO TOTAL: reintenta sin colgar rápido
        # =========================
        if not speech and not digits:
            if noinput <= MAX_NOINPUT_ATTEMPTS:
                vr.pause(length=RETRY_PAUSE_SEC if noinput > 1 else INITIAL_PAUSE_SEC)

                if noinput == 1:
                    prompt = (
                        f"{saludo_por_hora()} Gracias por llamar a Nuxway Technology. "
                        "Diga el nombre de la persona con la que desea comunicarse. "
                        "Para soporte, marque cero o diga soporte."
                    )
                else:
                    prompt = (
                        "Disculpe, no le escuché bien. "
                        "Diga el nombre de la persona, o marque cero para soporte."
                    )

                vr.append(gather_prompt(f"/ivr-llm?noinput={noinput+1}", prompt))
                return Response(str(vr), mimetype="text/xml")

            # muchos silencios -> NO colgar: mandar a soporte
            say(vr, "Parece que la llamada está con poco audio. Le comunico con soporte para ayudarle.")
            return transfer_to_user(vr, DID_MAP["cola"], caller_real)

        # =========================
        # Normalizamos speech
        # =========================
        text = normalize(speech)

        # Si speech es demasiado corto, reintentar (evita que se vaya a OpenAI por "hola")
        if speech and len(text.split()) <= 1 and digits is None:
            vr.pause(length=0.4)
            vr.append(gather_prompt("/ivr-llm?noinput=1", "Le escuché muy bajito. Dígame el nombre de la persona, por favor."))
            return Response(str(vr), mimetype="text/xml")

        logging.info(f"[CALL {call_sid}] speech='{text}' digits='{digits}' llm_turns={llm_turns[call_sid]}")

        # =========================
        # DTMF routing
        # =========================
        if digits == "4000":
            return transfer_to_user(vr, DID_MAP["pablo"], caller_real)
        if digits == "4001":
            return transfer_to_user(vr, DID_MAP["gonzalo"], caller_real)
        if digits == "4002":
            return transfer_to_user(vr, DID_MAP["vladimir"], caller_real)
        if digits == "4003":
            return transfer_to_user(vr, DID_MAP["paola"], caller_real)
        if digits == "4007":
            return transfer_to_user(vr, DID_MAP["ximena"], caller_real)
        if digits == "0":
            return transfer_to_user(vr, DID_MAP["cola"], caller_real)

        # =========================
        # Voice: soporte
        # =========================
        if any(k in text for k in ["soporte", "support", "ayuda", "mesa", "tecnico", "técnico", "cola"]):
            return transfer_to_user(vr, DID_MAP["cola"], caller_real)

        # =========================
        # Voice por nombre directo
        # =========================
        for name in ["pablo", "gonzalo", "vladimir", "paola", "ximena"]:
            if name in text:
                return transfer_to_user(vr, DID_MAP[name], caller_real)

        # =========================
        # Fuzzy match nombres
        # =========================
        bm, score = detect_name_from_text(text)
        if bm and score >= 0.78:
            logging.warning(f"[CALL {call_sid}] FUZZY_NAME -> '{text}' => '{bm}' score={score:.2f}")
            return transfer_to_user(vr, DID_MAP[bm], caller_real)

        # =========================
        # humano/operador -> soporte
        # =========================
        if any(k in text for k in ["ingeniero", "agente", "humano", "operador"]):
            return transfer_to_user(vr, DID_MAP["cola"], caller_real)

        # =========================
        # OpenAI fallback
        # =========================
        if llm_turns[call_sid] >= MAX_LLM_TURNS:
            say(vr, "Para continuar, le comunico con soporte.")
            return transfer_to_user(vr, DID_MAP["cola"], caller_real)

        llm_turns[call_sid] += 1
        respuesta = llamar_openai(call_sid, text)
        say(vr, respuesta)

        vr.pause(length=0.4)
        vr.append(gather_prompt("/ivr-llm?noinput=1", "Diga el nombre de la persona. Para soporte, marque cero o diga soporte."))
        return Response(str(vr), mimetype="text/xml")

    except Exception as e:
        logging.exception(f"ERROR en /ivr-llm: {e}")
        say(vr, "Hubo un problema técnico. Le comunico con soporte.")
        return transfer_to_user(vr, DID_MAP["cola"], "")

# =========================
# HOME
# =========================
@app.route("/", methods=["GET"])
def home():
    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)




