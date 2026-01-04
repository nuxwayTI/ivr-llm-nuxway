from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import requests
from collections import defaultdict
import wave
import io
import re

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# =========================
# CONFIG OPENAI
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
session = requests.Session()

# =========================
# CONFIG PBX / TRONCAL
# =========================
# ✅ Si lo configuras en Render, úsalo:
# PBX_DOMAIN = "190.xxx.xxx.xxx" o "pbx.tudominio.com"
PBX_DOMAIN = os.getenv("PBX_DOMAIN", "").strip()

# ✅ Tu troncal anterior (fallback por si PBX_DOMAIN no existe)
AGENT_SIP_FALLBACK = "sip:6049@nuxway.sip.twilio.com"

# ✅ DIDs lógicos hacia PBX
DID_MAP = {
    "pablo": "5000",
    "vladimir": "5001",
    "ingeniero": "5999"
}

# =========================
# MEMORIA POR LLAMADA
# =========================
conversaciones = defaultdict(list)
saludo_fiestas_enviado = defaultdict(bool)
hint_humano_enviado = defaultdict(bool)
nombre_por_llamada = defaultdict(str)

# =========================
# PROMPT
# =========================
SYSTEM_PROMPT = """
Eres el asistente de soporte y atención de Nuxway Technology.
Atiendes llamadas telefónicas en español con un tono humano, natural y profesional.

Reglas de estilo:
- Suena humano (no robótico).
- Respuestas cortas (2–3 frases) y claras.
- Evita repetir “soy IA” si no te lo preguntan.

Reglas para voz (teléfono):
- Escribe como si estuvieras hablando, no como chat.
- Máximo 2 frases por respuesta.
- Solo 1 pregunta por turno.
- Usa conectores humanos: "perfecto", "claro", "listo", "entiendo" (máx 1 por respuesta).
- Si tengo el nombre del usuario, úsalo 1 vez por respuesta (no más).
- Evita enumeraciones largas; ofrece “¿Quieres que te lo detalle?”.
- Evita sonar como un formulario; usa frases cortas y naturales.

Identidad:
- Si el usuario pregunta “¿quién eres?”:
  responde que eres el asistente con IA de Nuxway Technology y que puedes comunicar con un humano si lo desea.

# Datos oficiales (NO inventar):
- Sitio web oficial: https://nuxway.net
- Si pide la web: responde exactamente: "nuxway punto net"
- No inventes enlaces, correos, teléfonos, precios, fechas ni compromisos.
"""

# =========================
# SAY helper (SSML)
# =========================
def say_slow(vr: VoiceResponse, text: str, language="es-MX", voice="Polly.Mia", rate="105%"):
    t = (text or "").strip()

    def add_breaks(s: str) -> str:
        s = re.sub(r"\.\s+", ".<break time=\"180ms\"/> ", s)
        s = re.sub(r"\?\s+", "?<break time=\"220ms\"/> ", s)
        s = re.sub(r"!\s+", "!<break time=\"200ms\"/> ", s)
        s = re.sub(r",\s+", ",<break time=\"120ms\"/> ", s)
        return s

    t_ssml = add_breaks(t)

    if t.lower().startswith("hola"):
        m = re.match(r"^(¡?hola!?)(.*)$", t, flags=re.IGNORECASE)
        if m:
            hola = add_breaks(m.group(1))
            resto = add_breaks((m.group(2) or "").strip())
            ssml = (
                f"<speak><prosody rate=\"{rate}\">"
                f"<emphasis level=\"moderate\">{hola}</emphasis>"
                f"{('<break time=\"160ms\"/>' + resto) if resto else ''}"
                f"</prosody></speak>"
            )
            vr.say(ssml, language=language, voice=voice)
            return

    ssml = f"<speak><prosody rate=\"{rate}\">{t_ssml}</prosody></speak>"
    vr.say(ssml, language=language, voice=voice)

# =========================
# GPT CALL
# =========================
def llamar_gpt(call_sid: str, prompt_usuario: str, max_tokens: int = 220) -> str:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *conversaciones[call_sid],
        {"role": "user", "content": prompt_usuario},
    ]

    data = {
        "model": "gpt-4.1-mini",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.35,
    }

    r = session.post(OPENAI_URL, json=data, headers=headers, timeout=8)
    if r.status_code != 200:
        logging.warning(f"[CALL {call_sid}] OpenAI error status={r.status_code} body={r.text[:300]}")
        return "Tengo problemas con la inteligencia artificial en este momento."

    respuesta = r.json()["choices"][0]["message"]["content"]

    conversaciones[call_sid].append({"role": "user", "content": prompt_usuario})
    conversaciones[call_sid].append({"role": "assistant", "content": respuesta})

    logging.warning(f"[CALL {call_sid}] ASISTENTE: {respuesta}")
    return respuesta

# =========================
# ✅ TRANSFERENCIA POR DID (NUEVO)
# =========================
def transferir_por_did(vr: VoiceResponse, did: str):
    """
    Si PBX_DOMAIN está configurado, transfiero a sip:<DID>@PBX_DOMAIN
    Si NO está configurado, uso el AGENT_SIP_FALLBACK que ya funcionaba.
    """
    say_slow(vr, "Listo, te transfiero ahora. Por favor espera.")

    d = vr.dial()

    if PBX_DOMAIN:
        destino = f"sip:{did}@{PBX_DOMAIN}"
        logging.warning(f"TRANSFER -> {destino}")
        d.sip(destino)
    else:
        # fallback
        logging.warning("PBX_DOMAIN vacío -> usando fallback AGENT_SIP")
        d.sip(AGENT_SIP_FALLBACK)

    return Response(str(vr), mimetype="text/xml")

# =========================
# UTILIDADES
# =========================
def despedida():
    return "Perfecto. Gracias por la conversación. Hasta luego."

def despedida_con_escucha(vr: VoiceResponse, action_url: str):
    g = Gather(
        input="speech dtmf",
        language="es-MX",
        action=action_url,
        method="POST",
        timeout=2,
        speech_timeout="auto",
        bargeIn=True,
        action_on_empty_result=True
    )
    say_slow(g, despedida())
    vr.append(g)

# =========================
# DETECCIÓN DE BUZÓN
# =========================
VOICEMAIL_HINTS = [
    "buzón de voz", "buzon de voz", "correo de voz", "buzon",
    "para dejar un mensaje", "deje su mensaje", "deje un mensaje",
    "después del tono", "no está disponible", "no esta disponible",
    "leave a message", "after the tone", "voicemail"
]

def parece_buzon(texto: str) -> bool:
    t = (texto or "").strip().lower()
    patrones = [
        r"para dejar( un)? mensaje",
        r"deje( su| un)? mensaje",
        r"despu[eé]s del tono",
        r"correo de voz|buz[oó]n de voz|voicemail",
        r"no (est[aá]|se encuentra) disponible",
    ]
    if any(re.search(p, t) for p in patrones):
        return True
    return any(k in t for k in VOICEMAIL_HINTS)

# =========================
# SILENCIO WAV
# =========================
@app.route("/silence.wav")
def silence_wav():
    duration_s = 0.5
    framerate = 8000
    nframes = int(duration_s * framerate)
    sampwidth = 2
    nchannels = 1

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(nchannels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * nframes)
    wav_bytes = buf.getvalue()

    return Response(wav_bytes, mimetype="audio/wav")

# =========================
# IVR PRINCIPAL
# =========================
@app.route("/ivr-llm", methods=["POST", "GET"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")
    call_sid = request.values.get("CallSid", "unknown")
    phase = request.args.get("phase", "initial")
    attempt = int(request.args.get("attempt", "1"))

    vr = VoiceResponse()

    # Si llega por GET (prueba navegador), solo responde OK
    if request.method == "GET":
        say_slow(vr, "IVR OK.")
        return Response(str(vr), mimetype="text/xml")

    if not speech and not digits:

        if phase == "goodbye" and attempt >= 2:
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        if phase == "initial" and attempt == 1:
            parte_protegida = (
                "Hola, ¿cómo estás? Te llamamos desde Nuxway Technology "
                "para compartir un saludo de fin de año."
            )
            parte_escuchable = "Antes, ¿con quién tengo el gusto?"

            base_url = os.getenv("BASE_URL", "").rstrip("/")
            if base_url:
                vr.play(f"{base_url}/silence.wav")

            say_slow(vr, parte_protegida)

            g = Gather(
                input="speech dtmf",
                language="es-MX",
                action="/ivr-llm?phase=initial&attempt=2",
                method="POST",
                timeout=3,
                speech_timeout="auto",
                action_on_empty_result=True,
                bargeIn=True
            )
            say_slow(g, parte_escuchable)
            vr.append(g)
            return Response(str(vr), mimetype="text/xml")

        if phase == "initial" and attempt == 2:
            mensaje_rep = "No te escuché. Te lo repito una vez más. ¿Con quién tengo el gusto?"

            g = Gather(
                input="speech dtmf",
                language="es-MX",
                action="/ivr-llm?phase=initial&attempt=3",
                method="POST",
                timeout=3,
                speech_timeout="auto",
                action_on_empty_result=True,
                bargeIn=True
            )
            say_slow(g, mensaje_rep)
            vr.append(g)
            return Response(str(vr), mimetype="text/xml")

        if phase == "initial" and attempt >= 3:
            despedida_con_escucha(vr, "/ivr-llm?phase=goodbye&attempt=2")
            return Response(str(vr), mimetype="text/xml")

        if phase == "followup" and attempt >= 1:
            despedida_con_escucha(vr, "/ivr-llm?phase=goodbye&attempt=2")
            return Response(str(vr), mimetype="text/xml")

        despedida_con_escucha(vr, "/ivr-llm?phase=goodbye&attempt=2")
        return Response(str(vr), mimetype="text/xml")

    texto = ((speech or "") + " " + (digits or "")).strip().lower()
    logging.warning(f"[CALL {call_sid}] USUARIO: {texto}")

    # buzón
    if parece_buzon(texto):
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # ==============================================
    # ✅ NUEVO: RUTEO POR NOMBRE HACIA DID
    # ==============================================
    # DTMF:
    # 1 -> Pablo (5000)
    # 2 -> Vladimir (5001)
    # 0 -> Ingeniero (5999)
    if digits == "1" or "pablo" in texto:
        logging.warning(f"[CALL {call_sid}] EVENTO: transferir_pablo -> DID 5000")
        return transferir_por_did(vr, DID_MAP["pablo"])

    if digits == "2" or "vladimir" in texto:
        logging.warning(f"[CALL {call_sid}] EVENTO: transferir_vladimir -> DID 5001")
        return transferir_por_did(vr, DID_MAP["vladimir"])

    if digits == "0" or "ingeniero" in texto:
        logging.warning(f"[CALL {call_sid}] EVENTO: transferir_ingeniero -> DID 5999")
        return transferir_por_did(vr, DID_MAP["ingeniero"])

    # colgar



