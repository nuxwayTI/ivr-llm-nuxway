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
# MEMORIA POR LLAMADA
# =========================
conversaciones = defaultdict(list)
saludo_fiestas_enviado = defaultdict(bool)
hint_humano_enviado = defaultdict(bool)
nombre_por_llamada = defaultdict(str)

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
- Si el usuario pregunta “¿quién eres?”, “con quién hablo?”, “de dónde llamas?”:
  responde que eres el asistente con IA de Nuxway Technology y que puedes comunicar con un humano si lo desea.

Flujo:
- Primero saluda y pide el nombre.
- Da un saludo cálido de Navidad/fin de año “de parte de la Familia Nuxway Technology”.
- Luego pregunta: “¿En qué puedo ayudarte hoy?”

# Datos oficiales (NO inventar):
- Sitio web oficial de Nuxway Technology: https://nuxway.net
- Si el usuario pide la web o el dominio, responde exactamente: "nuxway punto net" (sin .com).
- No inventes enlaces, correos, teléfonos, precios, fechas ni compromisos.

Manejo de dudas y preguntas difíciles:
- Si no estás 100% seguro, NO inventes.
- Responde breve: "Para darte una respuesta correcta, prefiero confirmarlo con un especialista."
- Luego ofrece comunicar con un humano o ingeniero.
"""

# =========================
# SAY helper (SSML: habla más lento) + ✅ PRO: énfasis en el saludo
# =========================
def say_slow(vr: VoiceResponse, text: str, language="es-MX", voice="Polly.Mia", rate="95%"):
    """
    Versión PRO:
    - Si el texto empieza con "Hola" o "¡Hola!", le agrega énfasis al saludo para que no suene plano.
    - Mantiene el resto del código igual.
    """
    t = (text or "").strip()

    # Detecta saludo al inicio y lo mejora con SSML
    if t.lower().startswith("hola"):
        # Separa el "Hola" inicial (con o sin signos) del resto
        m = re.match(r"^(¡?hola!?)(.*)$", t, flags=re.IGNORECASE)
        if m:
            hola = m.group(1)
            resto = (m.group(2) or "").strip()
            ssml = (
                f"<speak><prosody rate=\"{rate}\">"
                f"<emphasis level=\"moderate\">{hola}</emphasis>"
                f"{(' ' + resto) if resto else ''}"
                f"</prosody></speak>"
            )
            vr.say(ssml, language=language, voice=voice)
            return

    # Default
    ssml = f"<speak><prosody rate=\"{rate}\">{t}</prosody></speak>"
    vr.say(ssml, language=language, voice=voice)

# =========================
# GPT CALL (tokens dinámicos)
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
        "temperature": 0.2,
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
# TRANSFERENCIA HUMANO
# =========================
AGENT_SIP = "sip:6049@nuxway.sip.twilio.com"

def transferir_a_agente(vr):
    say_slow(vr, "Te comunico con un agente humano. Por favor espera.")
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
# 0.5s de SILENCIO (WAV)
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
@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")
    call_sid = request.values.get("CallSid", "unknown")
    phase = request.args.get("phase", "initial")
    attempt = int(request.args.get("attempt", "1"))

    vr = VoiceResponse()

    # ==============================================================
    # ✅ MANEJO DE SILENCIO (EVITA BUCLES)
    # - initial: mensaje inicial -> si silencio, repetir 1 vez -> si silencio otra vez, colgar
    # - followup: si silencio, repetir 1 vez -> si silencio otra vez, colgar
    # ==============================================================
    if not speech and not digits:

        # 1) PRIMER ARRANQUE: decir saludo inicial y escuchar
        if phase == "initial" and attempt == 1:
            mensaje = (
                "Hola, ¿cómo estás? Te llamamos desde Nuxway Technology "
                "para compartir un saludo de fin de año. "
                "Antes, ¿con quién hablo?"
            )

            base_url = os.getenv("BASE_URL", "").rstrip("/")
            if base_url:
                vr.play(f"{base_url}/silence.wav")

            logging.warning(f"[CALL {call_sid}] ASISTENTE (inicio): {mensaje}")
            say_slow(vr, mensaje)

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

        # 2) SI NO RESPONDEN DESPUÉS DEL MENSAJE INICIAL: repetir 1 vez
        if phase == "initial" and attempt == 2:
            mensaje_rep = "No te escuché. Te lo repito una vez más. ¿Con quién hablo?"
            logging.warning(f"[CALL {call_sid}] ASISTENTE (rep1): {mensaje_rep}")
            say_slow(vr, mensaje_rep)

            g = Gather(
                input="speech dtmf",
                language="es-ES",
                action="/ivr-llm?phase=initial&attempt=3",
                method="POST",
                timeout=3,
                speech_timeout="1",
                action_on_empty_result=True
            )
            vr.append(g)
            return Response(str(vr), mimetype="text/xml")

        # 3) SI SIGUE SILENCIO: colgar (sin bucle)
        if phase == "initial" and attempt >= 3:
            logging.warning(f"[CALL {call_sid}] EVENTO: silencio_final_initial -> hangup")
            say_slow(vr, despedida())
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # FOLLOWUP: repetir 1 vez y luego colgar
        if phase == "followup" and attempt == 1:
            msg = "No te escuché. Si sigues en línea, dime en qué te puedo ayudar."
            logging.warning(f"[CALL {call_sid}] ASISTENTE (followup_rep1): {msg}")
            say_slow(vr, msg)

            g = Gather(
                input="speech dtmf",
                language="es-ES",
                action="/ivr-llm?phase=followup&attempt=2",
                method="POST",
                timeout=3,
                speech_timeout="1",
                action_on_empty_result=True
            )
            vr.append(g)
            return Response(str(vr), mimetype="text/xml")

        if phase == "followup" and attempt >= 2:
            logging.warning(f"[CALL {call_sid}] EVENTO: silencio_final_followup -> hangup")
            say_slow(vr, despedida())
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # Fallback seguro: si llega acá por algún phase raro, cuelga
        logging.warning(f"[CALL {call_sid}] EVENTO: silencio_fallback -> hangup phase={phase} attempt={attempt}")
        say_slow(vr, despedida())
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # ==============================================================
    # Ya hay texto del usuario
    # ==============================================================
    texto = ((speech or "") + " " + (digits or "")).strip().lower()

    if texto:
        logging.warning(f"[CALL {call_sid}] USUARIO: {texto}")

    # Capturar nombre
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
                logging.warning(f"[CALL {call_sid}] NOMBRE_DETECTADO: {nombre_por_llamada[call_sid]}")
                break

    # voicemail → colgar
    if any(v in texto for v in VOICEMAIL_HINTS):
        logging.warning(f"[CALL {call_sid}] EVENTO: voicemail_detectado -> hangup")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # humano
    if digits == "0" or "humano" in texto:
        logging.warning(f"[CALL {call_sid}] EVENTO: transferencia_humano")
        return transferir_a_agente(vr)

    # colgar
    if "colgar" in texto or "nada más" in texto:
        logging.warning(f"[CALL {call_sid}] EVENTO: despedida_y_hangup")
        say_slow(vr, despedida())
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # contacto/web fijo
    contacto_keys = [
        "contacto", "contactar", "correo", "email", "e-mail", "mail",
        "telefono", "teléfono", "celular", "whatsapp", "wsp", "numero", "número",
        "pagina web", "página web", "sitio web", "web", "dominio", "url", "link"
    ]
    if any(k in texto for k in contacto_keys):
        nombre = (nombre_por_llamada.get(call_sid, "") or "").strip()
        prefijo = f"Perfecto, {nombre}. " if nombre else "Perfecto. "

        respuesta_contacto = (
            prefijo +
            "Nuestra página web oficial es nuxway punto net: nuxway punto net. "
            "Si deseas, también puedo comunicarte con un humano o ingeniero; di 'humano' o marca cero."
        )

        logging.warning(f"[CALL {call_sid}] ASISTENTE (contacto): {respuesta_contacto}")
        say_slow(vr, respuesta_contacto)

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

    # GPT – saludo navideño una sola vez (tokens bajos SOLO aquí)
    if not saludo_fiestas_enviado[call_sid]:
        prompt = (
            "INICIO DE LLAMADA.\n"
            f"El usuario dijo: '{texto}'.\n\n"
            "Da un saludo cálido de Navidad y fin de año (2–3 frases), "
            "de parte de la Familia Nuxway Technology, y luego pregunta: "
            "'¿En qué puedo ayudarte hoy?'"
        )
        respuesta = llamar_gpt(call_sid, prompt, max_tokens=80)
        saludo_fiestas_enviado[call_sid] = True
    else:
        respuesta = llamar_gpt(call_sid, texto, max_tokens=220)

    say_slow(vr, respuesta)

    if not hint_humano_enviado[call_sid]:
        hint = "Si deseas hablar con un humano o ingeniero, di 'humano' o marca cero."
        logging.warning(f"[CALL {call_sid}] ASISTENTE (hint): {hint}")
        say_slow(vr, hint)
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



