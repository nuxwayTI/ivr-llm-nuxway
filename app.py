from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import requests
from collections import defaultdict
import uuid
import wave
import io
import re  # ✅ NUEVO

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# =========================
# CONFIG OPENAI
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
session = requests.Session()

# =========================
# MEMORIA POR LLAMADA
# =========================
conversaciones = defaultdict(list)
saludo_fiestas_enviado = defaultdict(bool)
hint_humano_enviado = defaultdict(bool)
nombre_por_llamada = defaultdict(str)  # ✅ NUEVO

# =========================
# PROMPT (MÁS HUMANO)
# =========================
SYSTEM_PROMPT = """
Eres el asistente de soporte y atención de Nuxway Technology.
Atiendes llamadas telefónicas en español con un tono humano, natural y profesional.

Reglas de estilo:
- Suena humano (no robótico).
- Respuestas cortas (2–3 frases) y claras.
- Evita repetir “soy IA” si no te lo preguntan.

Identidad:
- Si el usuario pregunta “¿quién eres?”, “¿con quién hablo?”, “¿de dónde llamas?”:
  responde que eres el asistente con IA de Nuxway Technology y que puedes comunicar con un humano si lo desea.

Flujo:
- Primero saluda y pide el nombre.
- Da un saludo cálido de Navidad/fin de año “de parte de la Familia Nuxway Technology”.
- Luego pregunta: “¿En qué puedo ayudarte hoy?”

# ✅ NUEVO: Datos oficiales y anti-invención
Datos oficiales (NO inventar):
- Sitio web oficial de Nuxway Technology: https://nuxway.net
- Si el usuario pide la web o el dominio, responde exactamente: "nuxway punto net" (sin .com).
- No inventes enlaces, correos, teléfonos, precios, fechas ni compromisos.

Manejo de dudas y preguntas difíciles:
- Si no estás 100% seguro, NO inventes.
- Responde breve: "Para darte una respuesta correcta, prefiero confirmarlo con un especialista."
- Luego ofrece comunicar con un humano o ingeniero.
"""

# =========================
# GPT CALL
# =========================
def llamar_gpt(call_sid: str, prompt_usuario: str) -> str:
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
        "max_tokens": 220,
        "temperature": 0.2,
    }

    r = session.post(OPENAI_URL, json=data, headers=headers, timeout=8)
    if r.status_code != 200:
        return "Tengo problemas con la inteligencia artificial en este momento."

    respuesta = r.json()["choices"][0]["message"]["content"]

    conversaciones[call_sid].append({"role": "user", "content": prompt_usuario})
    conversaciones[call_sid].append({"role": "assistant", "content": respuesta})

    return respuesta

# =========================
# TRANSFERENCIA HUMANO
# =========================
AGENT_SIP = "sip:6049@nuxway.sip.twilio.com"

def transferir_a_agente(vr):
    vr.say("Te comunico con un agente humano. Por favor espera.",
           language="es-ES", voice="Polly.Lupe")
    d = vr.dial()
    d.sip(AGENT_SIP)
    return Response(str(vr), mimetype="text/xml")

# =========================
# UTILIDADES
# =========================
def despedida():
    return "Perfecto. Gracias por la conversación. Hasta luego."

VOICEMAIL_HINTS = [
    "buzón de voz", "buzon de voz", "deje su mensaje", "después del tono",
    "no está disponible", "grabe su mensaje", "leave a message", "after the tone"
]

# =========================
# 1s de SILENCIO (WAV) - evita ringback
# =========================
@app.route("/silence.wav")
def silence_wav():
    # WAV PCM 8kHz mono 16-bit (compatible con telefonía)
    duration_s = 1.0
    framerate = 8000
    nframes = int(duration_s * framerate)
    sampwidth = 2  # 16-bit
    nchannels = 1

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(nchannels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * nframes)  # silencio
    wav_bytes = buf.getvalue()

    return Response(wav_bytes, mimetype="audio/wav")

# =========================
# IVR PRINCIPAL
# =========================
@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")
    call_sid = request.values.get("CallSid", "unknown")
    phase = request.args.get("phase", "initial")
    attempt = int(request.args.get("attempt", "1"))

    vr = VoiceResponse()

    # ==============================================================
    # MENSAJE INICIAL: ESPERA 1s PERO SIN TONO (silencio real)
    # ==============================================================
    if phase == "initial" and attempt == 1 and not speech and not digits:

        mensaje = (
            "Hola, ¿cómo estás? Te llamamos desde Nuxway Technology "
            "para compartir un saludo de fin de año. "
            "Antes, ¿puedo saber con quién hablo?"
        )

        # ✅ En vez de Pause, reproducimos 1s de audio silencioso
        # IMPORTANTE: debe ser URL pública para Twilio (no localhost)
        base_url = os.getenv("BASE_URL", "").rstrip("/")
        vr.play(f"{base_url}/silence.wav")

        vr.say(mensaje, language="es-ES", voice="Polly.Lupe")

        g = Gather(
            input="speech dtmf",
            language="es-ES",
            action="/ivr-llm?phase=initial&attempt=2",
            method="POST",
            timeout=3,
            speech_timeout="1",
            action_on_empty_result=True
        )
        vr.append(g)

        return Response(str(vr), mimetype="text/xml")

    texto = ((speech or "") + " " + (digits or "")).strip().lower()

    # ✅ NUEVO: capturar nombre si aún no existe
    if not nombre_por_llamada[call_sid]:
        patrones_nombre = [
            r"\bme llamo\s+([a-záéíóúñ]+)\b",
            r"\bmi nombre es\s+([a-záéíóúñ]+)\b",
            r"\bsoy\s+([a-záéíóúñ]+)\b",
        ]
        for pat in patrones_nombre:
            m = re.search(pat, texto, flags=re.IGNORECASE)
            if m:
                nombre_por_llamada[call_sid] = m.group(1).strip().title()
                break

    # voicemail → colgar
    if any(v in texto for v in VOICEMAIL_HINTS):
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # humano
    if digits == "0" or "humano" in texto:
        return transferir_a_agente(vr)

    # colgar
    if "colgar" in texto or "nada más" in texto:
        vr.say(despedida(), language="es-ES", voice="Polly.Lupe")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # ✅ NUEVO: Respuesta fija SOLO para contacto/web (evita inventos) + usa el nombre
    contacto_keys = [
        "contacto", "contactar", "correo", "email", "e-mail", "mail",
        "telefono", "teléfono", "celular", "whatsapp", "wsp", "numero", "número",
        "pagina web", "página web", "sitio web", "web", "dominio", "url", "link"
    ]
    if any(k in texto for k in contacto_keys):
        nombre = (nombre_por_llamada.get(call_sid, "") or "").strip()
        prefijo = f"Perfecto, {nombre}. " if nombre else "Perfecto. "

        vr.say(
            prefijo +
            "Nuestra página web oficial es nuxway punto net: nuxway punto net. "
            "Si deseas, también puedo comunicarte con un humano o ingeniero; di 'humano' o marca cero.",
            language="es-ES",
            voice="Polly.Lupe"
        )

        g2 = Gather(
            input="speech dtmf",
            language="es-ES",
            action="/ivr-llm?phase=followup&attempt=1",
            method="POST",
            timeout=3,
            speech_timeout="1",
            action_on_empty_result=True
        )
        vr.append(g2)

        return Response(str(vr), mimetype="text/xml")

    # GPT – saludo navideño una sola vez
    if not saludo_fiestas_enviado[call_sid]:
        prompt = (
            "INICIO DE LLAMADA.\n"
            f"El usuario dijo: '{texto}'.\n\n"
            "Da un saludo cálido de Navidad y fin de año (2–3 frases), "
            "de parte de la Familia Nuxway Technology, y luego pregunta: "
            "'¿En qué puedo ayudarte hoy?'"
        )
        respuesta = llamar_gpt(call_sid, prompt)
        saludo_fiestas_enviado[call_sid] = True
    else:
        respuesta = llamar_gpt(call_sid, texto)

    vr.say(respuesta, language="es-ES", voice="Polly.Lupe")

    if not hint_humano_enviado[call_sid]:
        vr.say(
            "Si deseas hablar con un humano o ingeniero, di 'humano' o marca cero.",
            language="es-ES",
            voice="Polly.Lupe"
        )
        hint_humano_enviado[call_sid] = True

    g2 = Gather(
        input="speech dtmf",
        language="es-ES",
        action="/ivr-llm?phase=followup&attempt=1",
        method="POST",
        timeout=3,
        speech_timeout="1",
        action_on_empty_result=True
    )
    vr.append(g2)

    return Response(str(vr), mimetype="text/xml")


@app.route("/")
def home():
    return "Nuxway IVR LLM – OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)



