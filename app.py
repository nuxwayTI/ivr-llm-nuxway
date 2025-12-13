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
# PROMPT OPTIMIZADO
# =========================
SYSTEM_PROMPT = """
Eres el Agente de Soporte con Inteligencia Artificial de Nuxway Technology.
Atiendes llamadas telefónicas y respondes SOLO en español.

Estilo:
- Frases cortas y claras (2–3 frases).
- Tono profesional, amable y seguro.
- Lenguaje natural, como un ingeniero de soporte real.

Comportamiento:
- Antes de dar una solución, realiza 1 o 2 preguntas para entender el caso.
- Si el usuario hace varias preguntas, respóndelas todas de forma breve y ordenada.
- No inventes información. Si algo no lo sabes, dilo con honestidad.

Derivación:
- Si el caso es complejo o el usuario lo solicita, sugiere comunicarlo con un agente humano.

Áreas:
- Telefonía IP y PBX.
- Contact center y call center.
- Redes IP, WiFi empresarial y VPN.
- Soluciones Nuxway: Cloud PBX, NuxCaller y NuxGATE.
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
    vr.say("Te voy a comunicar con un agente humano. Por favor espera.",
           language="es-ES", voice="Polly.Lupe")
    d = vr.dial()
    d.sip(AGENT_SIP)
    return Response(str(vr), mimetype="text/xml")


# =========================
# UTILIDADES
# =========================
def parece_nombre_o_empresa(texto: str) -> bool:
    t = (texto or "").lower().strip()
    if t in {"hola", "buenas", "buenos dias", "buen día", "buen dia",
             "buenas tardes", "buenas noches", "alo", "aló", "hello"}:
        return False
    if re.search(r"\bde\b\s+\w+", t):
        return True
    return len(t.split()) >= 2


def despedida():
    return "Perfecto. Gracias por la conversación. Hasta luego."


SALUDOS_SOLOS = {
    "hola", "buenas", "buenos dias", "buen día", "buen dia",
    "buenas tardes", "buenas noches", "alo", "aló", "hello"
}


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

    # ---------- SILENCIO ----------
    if not speech and not digits:
        if attempt >= 3:
            vr.say(despedida(), language="es-ES", voice="Polly.Lupe")
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        g = Gather(
            input="speech dtmf",
            language="es-ES",
            action=f"/ivr-llm?phase={phase}&attempt={attempt+1}",
            timeout=2,
            speech_timeout="1",
            action_on_empty_result=True
        )
        vr.append(g)
        return Response(str(vr), mimetype="text/xml")

    # Texto normalizado (incluye digits)
    texto = ((speech or "") + " " + (digits or "")).strip().lower()

    # ---------- HUMANO ----------
    if digits == "0" or any(x in texto for x in ["humano", "ingeniero", "persona", "agente", "representante"]):
        return transferir_a_agente(vr)

    # ---------- COLGAR / FINALIZAR ----------
    if any(x in texto for x in [
        "colgar", "cuelga", "cuelgue", "finalizar", "finaliza", "terminar", "termina",
        "cortar", "corta", "ya no quiero ayuda", "no quiero ayuda", "no necesito ayuda", "nada más", "nada mas"
    ]):
        vr.say(despedida(), language="es-ES", voice="Polly.Lupe")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # ✅ Si es el primer turno y el usuario solo dijo un saludo, pedir nombre/empresa
    if phase == "initial" and attempt == 1 and texto.strip() in SALUDOS_SOLOS:
        mensaje = (
            "Hola, soy el Agente con Inteligencia Artificial General de Nuxway Technology. "
            "Para comenzar y poder darte un mensaje adecuado, "
            "¿podrías decirme tu nombre y el de tu empresa, por favor?"
        )
        g = Gather(
            input="speech dtmf",
            language="es-ES",
            action="/ivr-llm?phase=initial&attempt=2",
            timeout=3,
            speech_timeout="1",
            action_on_empty_result=True,
            barge_in=False  # ✅ CAMBIO: que el mensaje se diga completo sin interrupción
        )
        g.say(mensaje, language="es-ES", voice="Polly.Lupe")
        vr.append(g)
        return Response(str(vr), mimetype="text/xml")

    # ---------- GPT ----------
    if not saludo_fiestas_enviado[call_sid] and parece_nombre_o_empresa(texto):
        prompt = (
            "INICIO DE LLAMADA (SALUDO NAVIDEÑO).\n"
            f"El usuario dijo: '{texto}'.\n\n"
            "Instrucciones obligatorias (sin copiar frases del usuario):\n"
            "1) Si hay nombre y/o empresa, menciónalos.\n"
            "2) Redacta un saludo cálido de Navidad y fin de año (no genérico), en 2–3 frases.\n"
            "3) Debe incluir explícitamente: 'Navidad' y/o 'fin de año', y 'de parte de la Familia Nuxway Technology'.\n"
            "4) Incluye buenos deseos para su equipo (ej. éxito, tranquilidad, crecimiento) con un toque humano.\n"
            "5) Evita respuestas de una sola frase. Mínimo ~25 palabras.\n"
            "6) Cierra con: '¿En qué puedo ayudarte hoy?'\n"
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
        timeout=3,
        speech_timeout="1",
        action_on_empty_result=True
    )
    vr.append(g2)

    return Response(str(vr), mimetype="text/xml")


# =========================
# HOME
# =========================
@app.route("/")
def home():
    return "Nuxway IVR LLM – OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
