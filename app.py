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
#  CONFIG OPENAI
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

session = requests.Session()  # menor latencia


# =========================
# MEMORIA POR LLAMADA
# =========================
conversaciones = defaultdict(list)

# Para forzar el mensaje de fiestas SOLO una vez por llamada
saludo_fiestas_enviado = defaultdict(bool)

# Para decir el recordatorio de humano/ingeniero SOLO una vez por llamada
hint_humano_enviado = defaultdict(bool)


# =========================
# CONTEXTO DE LLAMADA (OUTBOUND)
# =========================
OUTBOUND_CALL = True  # tú estás llamando al cliente


def despedida_corta_outbound() -> str:
    # Despedida neutra para llamadas salientes
    return "Perfecto. Gracias por atender la llamada. Hasta luego."


def despedida_sin_respuesta_outbound() -> str:
    return "No logré escucharte. Gracias por atender la llamada. Hasta luego."


def despedida_sin_respuesta_inicial_outbound() -> str:
    return "No escuché ninguna respuesta. Gracias por atender la llamada. Hasta luego."


# =========================
# PROMPT DEL AGENTE IA (OPTIMIZADO)
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

Áreas que puedes atender:
- Telefonía IP y PBX.
- Contact center y call center.
- Redes IP, WiFi empresarial y VPN.
- Soluciones de Nuxway: Cloud PBX, NuxCaller y NuxGATE.
"""


# =========================
#  GPT CALL
# =========================
def llamar_gpt(call_sid: str, prompt_usuario: str) -> str:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    historial = conversaciones[call_sid]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *historial,
        {"role": "user", "content": prompt_usuario},
    ]

    data = {
        "model": "gpt-4.1-mini",
        "messages": messages,
        "max_tokens": 140,
        "temperature": 0.2,
    }

    try:
        r = session.post(OPENAI_URL, json=data, headers=headers, timeout=8)

        if r.status_code != 200:
            logging.error(f"OPENAI ERROR: {r.status_code} {r.text}")
            return "Tengo problemas con la inteligencia artificial en este momento."

        respuesta = r.json()["choices"][0]["message"]["content"]

        conversaciones[call_sid].append({"role": "user", "content": prompt_usuario})
        conversaciones[call_sid].append({"role": "assistant", "content": respuesta})

        return respuesta

    except Exception:
        logging.exception("GPT ERROR")
        return "Hubo un problema con la inteligencia artificial, intenta nuevamente."


# =========================
#  TRANSFERENCIA A AGENTE HUMANO
# =========================
AGENT_SIP = "sip:6049@nuxway.sip.twilio.com"

def transferir_a_agente(vr):
    vr.say(
        "Te voy a comunicar con un agente humano. Por favor espera.",
        language="es-ES",
        voice="Polly.Lupe"
    )
    d = vr.dial()
    d.sip(AGENT_SIP)
    return Response(str(vr), mimetype="text/xml")


# =========================
#  HEURÍSTICA: ¿parece que dio nombre/empresa?
# =========================
def parece_nombre_o_empresa(texto: str) -> bool:
    t = (texto or "").strip().lower()
    if not t:
        return False

    saludos = {
        "hola", "buenas", "buenos dias", "buen día", "buen dia",
        "buenas tardes", "buenas noches", "alo", "aló", "hello"
    }
    if t in saludos:
        return False

    # "Carlos de Pertec"
    if re.search(r"\bde\b\s+\w+", t):
        return True

    # 2+ palabras suele ser nombre/empresa
    palabras = re.findall(r"\w+", t)
    if len(palabras) >= 2:
        return True

    return False


# =========================
#  IVR PRINCIPAL
# =========================
@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")
    call_sid = request.values.get("CallSid", "unknown-call")

    phase = request.args.get("phase", "initial")
    attempt = int(request.args.get("attempt", "1"))

    logging.info(f"[IVR] call_sid={call_sid} phase={phase} attempt={attempt} speech={speech} digits={digits}")

    vr = VoiceResponse()

    # ==============================================================
    # 0. WARMUP (ESCUCHAR ANTES DEL MENSAJE INICIAL)
    # ==============================================================
    if phase == "warmup":
        text_lower = (speech or "").lower()

        # intenciones incluso en warmup
        if (
            digits == "0"
            or "humano" in text_lower
            or "agente" in text_lower
            or "ingeniero" in text_lower
            or "persona" in text_lower
            or "representante" in text_lower
        ):
            return transferir_a_agente(vr)

        if (
            "colgar" in text_lower
            or "cuelga" in text_lower
            or "cuelgue" in text_lower
            or "no quiero que me ayudes con nada" in text_lower
            or "no necesito ayuda" in text_lower
            or "ya no quiero ayuda" in text_lower
            or "nada más" in text_lower
        ):
            vr.say(despedida_corta_outbound(), language="es-ES", voice="Polly.Lupe")
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # Mensaje fijo para pedir nombre/empresa
        # (si quieres lo hacemos más outbound luego: "Te llamo de Nuxway..." pero no lo toco más de lo necesario)
        mensaje = (
            "Hola, soy el Agente con Inteligencia Artificial de Nuxway Technology. "
            "Para comenzar, ¿podrías decirme tu nombre y el de tu empresa, por favor?"
        )

        gather = Gather(
            input="speech dtmf",
            language="es-ES",
            action="/ivr-llm?phase=initial&attempt=2",
            method="POST",
            timeout=3,
            speech_timeout="1",
            action_on_empty_result=True
        )
        gather.say(mensaje, language="es-ES", voice="Polly.Lupe")
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # ==============================================================
    # 1. NO INPUT (Silencio)
    # ==============================================================
    if not speech and not digits:

        # -------- FOLLOWUP ----------
        if phase == "followup":

            if attempt >= 3:
                vr.say(despedida_sin_respuesta_outbound(), language="es-ES", voice="Polly.Lupe")
                vr.hangup()
                return Response(str(vr), mimetype="text/xml")

            if attempt == 1:
                mensaje = (
                    "No te escuché. "
                    "Si deseas hablar con un humano o un ingeniero, di 'humano' o marca cero."
                )
            else:
                mensaje = (
                    "Sigo sin escucharte. "
                    "Di 'humano' si deseas que te transfiera, o marca cero."
                )

            next_attempt = attempt + 1

            gather = Gather(
                input="speech dtmf",
                language="es-ES",
                action=f"/ivr-llm?phase=followup&attempt={next_attempt}",
                method="POST",
                timeout=3,
                speech_timeout="1",
                action_on_empty_result=True
            )
            gather.say(mensaje, language="es-ES", voice="Polly.Lupe")
            vr.append(gather)
            return Response(str(vr), mimetype="text/xml")

        # -------- INICIO ----------
        # Primer intento: warmup silencioso
        if attempt == 1:
            gather_warmup = Gather(
                input="speech dtmf",
                language="es-ES",
                action="/ivr-llm?phase=warmup&attempt=1",
                method="POST",
                timeout=2,
                speech_timeout="1",
                action_on_empty_result=True
            )
            vr.append(gather_warmup)
            return Response(str(vr), mimetype="text/xml")

        if attempt >= 3:
            vr.say(despedida_sin_respuesta_inicial_outbound(), language="es-ES", voice="Polly.Lupe")
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        if attempt == 2:
            mensaje = (
                "Hola, soy el Agente con Inteligencia Artificial de Nuxway Technology. "
                "Para comenzar, ¿podrías decirme tu nombre y el de tu empresa, por favor?"
            )
        else:
            mensaje = (
                "No logré escucharte. "
                "Por favor dime tu nombre y el de tu empresa."
            )

        next_attempt = attempt + 1

        gather = Gather(
            input="speech dtmf",
            language="es-ES",
            action=f"/ivr-llm?phase=initial&attempt={next_attempt}",
            method="POST",
            timeout=3,
            speech_timeout="1",
            action_on_empty_result=True
        )
        gather.say(mensaje, language="es-ES", voice="Polly.Lupe")
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # ==============================================================
    # 2. INTENCIONES ESPECIALES: HUMANO O COLGAR
    # ==============================================================
    text_lower = (speech or "").lower()

    if (
        digits == "0"
        or "humano" in text_lower
        or "agente" in text_lower
        or "ingeniero" in text_lower
        or "persona" in text_lower
        or "representante" in text_lower
    ):
        return transferir_a_agente(vr)

    if (
        "colgar" in text_lower
        or "cuelga" in text_lower
        or "cuelgue" in text_lower
        or "no quiero que me ayudes con nada" in text_lower
        or "no necesito ayuda" in text_lower
        or "ya no quiero ayuda" in text_lower
        or "nada más" in text_lower
    ):
        vr.say(despedida_corta_outbound(), language="es-ES", voice="Polly.Lupe")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # ==============================================================
    # 3. GPT — Responder consulta
    # ==============================================================
    texto_usuario = speech or digits or ""
    logging.info(f"[IVR] Texto para GPT: {texto_usuario}")

    # Forzar saludo cálido de fiestas SOLO cuando ya parece que dio nombre/empresa
    if (not saludo_fiestas_enviado[call_sid]) and parece_nombre_o_empresa(texto_usuario):
        prompt_forzado = (
            "INICIO DE LLAMADA (SALUDO DE FIESTAS).\n"
            f"El usuario dijo: '{texto_usuario}'.\n\n"
            "Instrucciones obligatorias:\n"
            "1) Usa el nombre y la empresa tal cual aparecen (si están).\n"
            "2) Da un mensaje breve, cálido y profesional de felicitación por las fiestas de fin de año "
            "similar a: 'Hola Carlos de Pertec... felices fiestas... Familia Nuxway'.\n"
            "3) Termina con una sola pregunta: '¿En qué puedo ayudarte hoy?'\n"
            "4) Máximo 2–3 frases.\n"
        )
        respuesta_gpt = llamar_gpt(call_sid, prompt_forzado)
        saludo_fiestas_enviado[call_sid] = True
    else:
        respuesta_gpt = llamar_gpt(call_sid, texto_usuario)

    vr.say(respuesta_gpt, language="es-ES", voice="Polly.Lupe")

    # Recordatorio de humano/ingeniero (solo 1 vez por llamada)
    if not hint_humano_enviado[call_sid]:
        vr.say(
            "Si deseas hablar con un humano o un ingeniero, di 'humano' o marca cero.",
            language="es-ES",
            voice="Polly.Lupe"
        )
        hint_humano_enviado[call_sid] = True

    # ==============================================================
    # 4. FOLLOWUP – Escuchar sin mensaje fijo extra
    # ==============================================================
    gather2 = Gather(
        input="speech dtmf",
        language="es-ES",
        action="/ivr-llm?phase=followup&attempt=1",
        method="POST",
        timeout=3,
        speech_timeout="1",
        action_on_empty_result=True
    )
    vr.append(gather2)

    return Response(str(vr), mimetype="text/xml")


# =========================
#  HOME
# =========================
@app.route("/")
def home():
    return "Nuxway IVR LLM – Soporte IA activo ✔"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)


