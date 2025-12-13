from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import requests
from collections import defaultdict

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


# =========================
# PROMPT DEL AGENTE IA
# =========================
SYSTEM_PROMPT = """
Eres el Agente de Inteligencia Artificial General de Nuxway Technology.
Respondes SOLO en español y atiendes llamadas telefónicas.

======================================================
🎄 OBJETIVO PRICIPAL 1: MENSAJE DE BIENVENIDA Y FIESTAS
======================================================
Cada vez que inicia una llamada:
- Da un mensaje breve, cálido y profesional de felicitación por las fiestas de fin de año utilizando el nombre brindado por el usuario.
- Mantén el saludo corto, amable y claro.

Ejemplo orientativo (NO repitas siempre igual):att
"Hola Carlos de Pertec.Queremos desearte unas felices fiestas llenas de paz, alegría y nuevos comienzos. ¡Un cálido saludo y nuestros mejores deseos! de la Familia Nuxway". 

======================================================
🧑‍💼 USO DEL NOMBRE Y EMPRESA
======================================================
Si el usuario dice su nombre o empresa:
- Respóndele usando ambos en la misma contestación.
  Ejemplo: "Gracias Carlos de Nuxway, con gusto te ayudo..."

Si NO lo dice o la respuesta es incompleta:
- Pídeselo nuevamente de forma clara y respetuosa.
  Ejemplo: "Para comenzar, ¿podrías brindarme tu nombre y el de tu empresa, por favor?"

======================================================
📏 ESTILO Y REGLAS GENERALES
======================================================
1. Usa frases cortas y muy claras (máx. 2–3 frases por respuesta).
2. Tono siempre profesional, amable y seguro.
3. Antes de dar una solución técnica, realiza 1 o 2 preguntas para entender mejor el caso.
4. Si el caso es complejo o el cliente pide hablar con un humano:
   - Sugiere amablemente derivarlo a un agente humano.
5. No inventes información. Si algo no lo sabes:
   - Dilo con honestidad y ofrece escalar el caso o derivarlo a soporte humano.
6. Explica de manera simple; entra en detalles técnicos solo si el cliente lo solicita.
7. Siempre suena como un ingeniero de soporte real, práctico y directo.

======================================================
🧠 MANEJO DE VARIAS PREGUNTAS A LA VEZ
======================================================
Si el usuario hace 2 o más preguntas simples en una sola frase:
- Respóndelas TODAS, de forma breve, ordenada y priorizando la claridad.
- NO ignores ninguna pregunta.
- Si necesitas entender algo antes de responder:
  - Haz una sola pregunta aclaratoria y luego responde cada punto.

Ejemplo:
Usuario: "¿Cómo reinicio mi PBX y cuánto tarda?"
Agente:
"Perfecto Carlos de Nuxway. Para ayudarte mejor, ¿tu PBX está en la nube o en sitio?
En general, el reinicio se hace desde el panel y suele tardar entre 1 y 3 minutos."

======================================================
🧮 CÁLCULOS Y RESPUESTAS CON NÚMEROS
======================================================
Si el usuario te pide operaciones numéricas simples (sumar, restar, multiplicar, dividir):
- Calcula el resultado con precisión.
- Responde de forma breve indicando el resultado explícito.

Ejemplo:
Usuario: "¿Cuánto es 35 + 7?"
Agente:
"35 más 7 es igual a 42."

======================================================
🎯 TEMAS PRINCIPALES QUE PUEDES ATENDER
======================================================
Puedes ayudar al cliente con temas de:
- Comunicaciones unificadas.
- Telefonía IP y PBX IP.
- Contact center y call center.
- Redes IP, WiFi empresarial y VPN.
- Soluciones de Nuxway: Cloud PBX, NuxCaller y NuxGATE.

Siempre mantén el foco en ayudar al usuario de forma clara, rápida y profesional.
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
        "max_tokens": 120,
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
    #    - deja que el cliente diga "hola" sin cortarle el audio
    #    - luego recién pides nombre/empresa
    # ==============================================================
    if phase == "warmup":
        text_lower = (speech or "").lower()

        # (mantener mismas intenciones incluso en warmup)
        if digits == "0" or "humano" in text_lower or "agente" in text_lower or "ingeniero" in text_lower or "persona" in text_lower or "representante" in text_lower:
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
            vr.say(
                "Perfecto, cierro la atención. Gracias por comunicarte con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # Aquí NO mandamos a GPT aunque haya dicho "hola".
        # Simplemente damos el mensaje inicial y pedimos nombre/empresa.
        mensaje = (
         "Hola, soy el Agente con Inteligencia Artificial General de Nuxway Technology"
         "Para comenzar y poder darte un mensaje adecuado, ¿podrías decirme tu nombre y el de tu empresa, por favor?"

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

        # -------- FOLLOWUP (usuario en segunda ronda) ----------
        if phase == "followup":

            # ahora cortamos antes: máximo 1 "Sigo sin escucharte"
            if attempt >= 3:
                vr.say(
                    "No logré escucharte. Gracias por comunicarte con Nuxway Technology. Hasta luego.",
                    language="es-ES",
                    voice="Polly.Lupe"
                )
                vr.hangup()
                return Response(str(vr), mimetype="text/xml")

            # mensaje según intento
            if attempt == 1:
                mensaje = (
                    "No te escuché. ¿Puedo ayudarte en algo más? "
                    "Si necesitas hablar con un humano, di 'humano' o marca cero."
                )
            else:  # attempt == 2
                mensaje = (
                    "Sigo sin escucharte. "
                    "¿Puedo ayudarte en algo más? Di 'humano' si deseas que te transfiera."
                )

            next_attempt = attempt + 1

            # MODO AHORRO: timeouts más cortos
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

        # -------- INICIO (ANTES: pedir nombre/empresa) ----------
        # NUEVO: en el primer intento, primero escuchamos un momento en silencio (warmup)
        if attempt == 1:
            gather_warmup = Gather(
                input="speech dtmf",
                language="es-ES",
                action="/ivr-llm?phase=warmup&attempt=1",
                method="POST",
                timeout=2,            # 2s para que el cliente diga "hola"
                speech_timeout="1",
                action_on_empty_result=True
            )
            # No decimos nada: solo escuchamos para no cortar al cliente
            vr.append(gather_warmup)
            return Response(str(vr), mimetype="text/xml")

        # Si ya estamos en intentos posteriores, seguimos tu lógica normal
        if attempt >= 3:
            vr.say(
                "No escuché ninguna respuesta. Gracias por su llamada. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        if attempt == 2:
            mensaje = (
          "Hola, soy el Agente con Inteligencia Artificial General de Nuxway Technology"
          "Para comenzar y poder darte un mensaje adecuado, ¿podrías decirme tu nombre y el de tu empresa, por favor?"
            )
        else:
            mensaje = (
                "No logré escucharte. Te repito nuevamente. "
                "Por favor dime tu nombre y el de tu empresa."
            )

        next_attempt = attempt + 1

        # MODO AHORRO: timeouts más cortos
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

    # pedir hablar con humano (AMPLIADO)
    if (
        digits == "0"
        or "humano" in text_lower
        or "agente" in text_lower
        or "ingeniero" in text_lower
        or "persona" in text_lower
        or "representante" in text_lower
    ):
        return transferir_a_agente(vr)

    # pedir colgar / no seguir ayudando
    if (
        "colgar" in text_lower
        or "cuelga" in text_lower
        or "cuelgue" in text_lower
        or "no quiero que me ayudes con nada" in text_lower
        or "no necesito ayuda" in text_lower
        or "ya no quiero ayuda" in text_lower
        or "nada más" in text_lower
    ):
        vr.say(
            "Perfecto, cierro la atención. Gracias por comunicarte con Nuxway Technology. Hasta luego.",
            language="es-ES",
            voice="Polly.Lupe"
        )
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # ==============================================================
    # 3. GPT — Responder consulta
    # ==============================================================
    texto_usuario = speech or digits or ""
    logging.info(f"[IVR] Texto para GPT: {texto_usuario}")

    respuesta_gpt = llamar_gpt(call_sid, texto_usuario)

    vr.say(respuesta_gpt, language="es-ES", voice="Polly.Lupe")

    # ==============================================================
    # 4. FOLLOWUP – Escuchar sin mensaje fijo extra
    # ==============================================================
    # MODO AHORRO + sin "¿Puedo ayudarte en algo más?" automático
    gather2 = Gather(
        input="speech dtmf",
        language="es-ES",
        action="/ivr-llm?phase=followup&attempt=1",
        method="POST",
        timeout=3,
        speech_timeout="1",
        action_on_empty_result=True
    )
    # No decimos nada, solo escuchamos para que el usuario siga hablando o pida humano/colgar
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

