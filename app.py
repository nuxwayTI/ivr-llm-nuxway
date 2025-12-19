from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import requests
from collections import defaultdict
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
- Primero saluda y pide el nombre (y si aplica, empresa).
- Da un saludo cálido de Navidad/fin de año “de parte de la Familia Nuxway Technology”.
- Luego pregunta: “¿En qué puedo ayudarte hoy?”
- Antes de dar soluciones técnicas, haz 1–2 preguntas.
- Si el caso es complejo o el usuario lo solicita, sugiere transferir a un agente humano.
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

SALUDOS_SOLOS = {
    "hola", "buenas", "buenos dias", "buen día", "buen dia",
    "buenas tardes", "buenas noches", "alo", "aló", "hello",
    "si", "sí", "diga", "mande"
}

PREGUNTAS_IDENTIDAD = [
    "quien eres", "quién eres", "con quien hablo", "con quién hablo",
    "de donde llamas", "de dónde llamas", "de donde eres", "de dónde eres",
    "que eres", "qué eres"
]

VOICEMAIL_HINTS = [
    "buzón de voz", "buzon de voz", "deje su mensaje", "deja su mensaje",
    "después del tono", "despues del tono", "después de la señal", "despues de la señal",
    "no puede atender", "no puedo atender", "no está disponible", "no esta disponible",
    "grabe su mensaje", "graba su mensaje", "para dejar un mensaje",
    "the person you are trying to reach", "is not available", "leave a message", "after the tone"
]

BEEP_HINTS = {"beep", "bip", "tono", "tone"}


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
    # WARMUP: escuchar 2s y luego pedir nombre/empresa (mensaje corto)
    # ==============================================================
    if phase == "warmup":
        text_lower = ((speech or "") + " " + (digits or "")).strip().lower()

        if digits == "0" or any(x in text_lower for x in ["humano", "ingeniero", "persona", "agente", "representante"]):
            return transferir_a_agente(vr)

        if any(x in text_lower for x in [
            "colgar", "cuelga", "cuelgue", "finalizar", "finaliza", "terminar", "termina",
            "cortar", "corta", "ya no quiero ayuda", "no quiero ayuda", "no necesito ayuda", "nada más", "nada mas"
        ]):
            vr.say(despedida(), language="es-ES", voice="Polly.Lupe")
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        mensaje = (
            "Hola, ¿cómo estás? Te habla Nuxway Technology. "
            "¿Me podrías decir tu nombre y tu empresa, por favor?"
        )

        g = Gather(
            input="speech dtmf",
            language="es-ES",
            action="/ivr-llm?phase=initial&attempt=2",
            method="POST",
            timeout=3,
            speech_timeout="1",
            action_on_empty_result=True,
            barge_in=False
        )
        g.say(mensaje, language="es-ES", voice="Polly.Lupe")
        vr.append(g)
        return Response(str(vr), mimetype="text/xml")

    # ==============================================================
    # SILENCIO
    # ==============================================================
    if not speech and not digits:

        if phase == "initial" and attempt == 1:
            g_warmup = Gather(
                input="speech dtmf",
                language="es-ES",
                action="/ivr-llm?phase=warmup&attempt=1",
                method="POST",
                timeout=2,
                speech_timeout="1",
                action_on_empty_result=True
            )
            vr.append(g_warmup)
            return Response(str(vr), mimetype="text/xml")

        if phase == "initial" and attempt == 2:
            mensaje_rep = (
                "No logré escucharte. Te lo repito una vez más: "
                "¿me dices tu nombre y tu empresa, por favor?"
            )
            g_rep = Gather(
                input="speech dtmf",
                language="es-ES",
                action="/ivr-llm?phase=initial&attempt=3",
                method="POST",
                timeout=3,
                speech_timeout="1",
                action_on_empty_result=True,
                barge_in=False
            )
            g_rep.say(mensaje_rep, language="es-ES", voice="Polly.Lupe")
            vr.append(g_rep)
            return Response(str(vr), mimetype="text/xml")

        # luego del segundo mensaje, si sigue silencio -> colgar
        if attempt >= 3:
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        g = Gather(
            input="speech dtmf",
            language="es-ES",
            action=f"/ivr-llm?phase={phase}&attempt={attempt+1}",
            method="POST",
            timeout=2,
            speech_timeout="1",
            action_on_empty_result=True
        )
        vr.append(g)
        return Response(str(vr), mimetype="text/xml")

    texto = ((speech or "") + " " + (digits or "")).strip().lower()

    # voicemail -> cuelga
    if any(h in texto for h in VOICEMAIL_HINTS):
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # beep/tono -> solo después de haber repetido (attempt>=3)
    texto_min = re.sub(r"[^a-záéíóúñü]+", " ", texto).strip()
    if attempt >= 3 and ((texto_min in BEEP_HINTS) or (len(texto_min) <= 3)):
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # humano
    if digits == "0" or any(x in texto for x in ["humano", "ingeniero", "persona", "agente", "representante"]):
        return transferir_a_agente(vr)

    # colgar
    if any(x in texto for x in [
        "colgar", "cuelga", "cuelgue", "finalizar", "finaliza", "terminar", "termina",
        "cortar", "corta", "ya no quiero ayuda", "no quiero ayuda", "no necesito ayuda", "nada más", "nada mas"
    ]):
        vr.say(despedida(), language="es-ES", voice="Polly.Lupe")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # identidad (sin GPT)
    if any(p in texto for p in PREGUNTAS_IDENTIDAD):
        vr.say(
            "Soy el asistente con inteligencia artificial de Nuxway Technology. "
            "Puedo ayudarte ahora mismo o, si prefieres, te paso con un agente humano. "
            "¿Cómo te llamas y de qué empresa nos atiendes?",
            language="es-ES",
            voice="Polly.Lupe"
        )
        g_id = Gather(
            input="speech dtmf",
            language="es-ES",
            action="/ivr-llm?phase=initial&attempt=2",
            method="POST",
            timeout=3,
            speech_timeout="1",
            action_on_empty_result=True
        )
        vr.append(g_id)
        return Response(str(vr), mimetype="text/xml")

    # ==============================================================
    # GPT: SALUDO NAVIDEÑO SIEMPRE UNA VEZ (con o sin nombre)
    # ==============================================================
    if not saludo_fiestas_enviado[call_sid]:
        prompt = (
            "INICIO DE LLAMADA (SALUDO NAVIDEÑO).\n"
            f"El usuario dijo: '{texto}'.\n\n"
            "Objetivo:\n"
            "- Da un saludo cálido de Navidad y fin de año (2–3 frases) y luego pregunta: "
            "'¿En qué puedo ayudarte hoy?'\n\n"
            "Reglas:\n"
            "1) Si el usuario dio nombre/empresa, úsalo.\n"
            "2) Si NO dio nombre/empresa, usa un saludo genérico (sin nombre) igualmente cálido.\n"
            "3) Debe decir: 'de parte de la Familia Nuxway Technology'.\n"
            "4) NO digas 'soy IA' a menos que te lo pregunten.\n"
        )
        respuesta = llamar_gpt(call_sid, prompt)
        saludo_fiestas_enviado[call_sid] = True
    else:
        respuesta = llamar_gpt(call_sid, texto)

    vr.say(respuesta, language="es-ES", voice="Polly.Lupe")

    if not hint_humano_enviado[call_sid]:
        vr.say("Si deseas hablar con un humano o ingeniero, di 'humano' o marca cero.",
               language="es-ES", voice="Polly.Lupe")
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
