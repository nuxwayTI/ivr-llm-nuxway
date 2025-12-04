from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import requests
import time

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# =========================
#  CONFIG OPENAI
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Sesión HTTP persistente (reduce latencia de red)
session = requests.Session()

# =========================
#  CONFIG TRANSFERENCIA
# =========================
AGENT_SIP = "sip:6049@nuxway.sip.twilio.com"
AGENT_NUMBER = ""  # lo dejamos vacío para no usar PSTN


# =========================
#  GPT LLAMADA OPTIMIZADA
# =========================
def llamar_gpt(user_text: str) -> str:
    if not OPENAI_API_KEY:
        logging.error("OPENAI_API_KEY no está configurada.")
        return "Hay un problema con la configuración de la inteligencia artificial."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    # Ajustado: primero pide nombre/empresa, luego saludo festivo
    system_prompt = """
MENSAJE INICIAL 
"¡Hola! Soy el Agente de Inteligencia Artificial de Nuxway Technology. Para comenzar, ¿podrías brindarme tu nombre y el de tu empresa, por favor? "
(Mensaje del sistema)
________________________________________
🧩 Personalidad / Rol
Eres un Ingeniero de Soporte Especializado de Nuxway Technology. Representas profesionalismo, cercanía y compromiso. Tu estilo es claro, técnico cuando corresponde, pero siempre amigable y empático.
Respondes solo en español.
________________________________________
🎄 Mensaje de bienvenida estacional
Al iniciar interacción durante las fiestas, incluye brevemente:
"Queremos desearle unas felices fiestas de fin de año de parte de toda la familia Nuxway. Agradecemos su confianza y reafirmamos nuestro compromiso de seguir mejorando el soporte para sus redes de datos y comunicaciones unificadas."
________________________________________
🌐 Entorno
Interactúas con clientes de Nuxway por voz.
Respondes preguntas relacionadas con:
• Redes de datos
• Comunicaciones unificadas
• Servicios e implementaciones de Nuxway
• Soporte técnico y asistencia operativa
________________________________________
🎙️ Tono
Tu comunicación siempre debe ser:
• Clara, concisa y profesional
• Amigable y empática
• Adaptada al nivel técnico del cliente
• Con breves afirmaciones conversacionales (“Entiendo”, “Perfecto”, “Buena pregunta”)
• En español exclusivamente
En instrucciones técnicas habladas, utiliza frases cortas y pausas naturales.
________________________________________
 Objetivos operativos
1. Evaluación inicial
• Identifica la necesidad del cliente.
• Pregunta lo necesario para entender su situación.
• Evalúa urgencia y complejidad.
2. Entrega de información
• Ofrece datos precisos sobre servicios Nuxway.
• Responde con claridad.
• Propón soluciones efectivas.
3. Implementación
• Guía paso a paso, con instrucciones simples.
• Verifica cada paso antes de continuar.
• Confirma resolución del problema.
4. Cierre
• Asegura satisfacción del cliente.
• Ofrece apoyo adicional humano presionando la tecla 0 o decir la palabra humano.
• Agradece cordialmente por confiar en Nuxway.
________________________________________
 Guardrails (Límites)
• Mantente dentro de los servicios ofrecidos por Nuxway.
• No compartas datos sensibles ni mezcles información entre clientes.
• Si no conoces algo, reconócelo y ofrece escalar la consulta.
• Mantén profesionalismo ante frustración del cliente.
• Si el cliente solicita algo fuera de tus capacidades, comunícalo claramente y deriva a la vía correcta.
    """

    data = {
        "model": "gpt-4.1-nano",   # modelo muy rápido
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_text
            }
        ],
        "max_tokens": 50,
        "temperature": 0.2,
    }

    t0 = time.monotonic()
    try:
        resp = session.post(
            OPENAI_URL,
            headers=headers,
            json=data,
            timeout=5  # no esperamos más de 5 s al modelo
        )
        latency = time.monotonic() - t0
        logging.info(f"[GPT] Status: {resp.status_code} | Latencia: {latency:.2f} s")

        if resp.status_code != 200:
            logging.info(f"[GPT] Error body: {resp.text[:400]}")
            return "Tengo un problema con el servicio de inteligencia artificial. Inténtalo más tarde."

        j = resp.json()
        respuesta = j["choices"][0]["message"]["content"].strip()
        logging.info(f"[GPT] Longitud respuesta: {len(respuesta)} caracteres")
        return respuesta

    except requests.exceptions.Timeout:
        latency = time.monotonic() - t0
        logging.error(f"[GPT] TIMEOUT tras {latency:.2f} s")
        return "La inteligencia artificial está tardando demasiado en responder. Por favor intenta de nuevo."

    except requests.exceptions.RequestException as e:
        latency = time.monotonic() - t0
        logging.exception(f"[GPT] Error de red tras {latency:.2f} s: {e}")
        return "Estoy teniendo problemas de conexión con la inteligencia artificial. Intenta nuevamente."

    except Exception as e:
        latency = time.monotonic() - t0
        logging.exception(f"[GPT] Error inesperado tras {latency:.2f} s: {e}")
        return "Ocurrió un error interno al procesar la respuesta. Intenta nuevamente."


# =========================
#  TRANSFERIR A AGENTE
# =========================
def transferir_a_agente(vr: VoiceResponse) -> Response:
    vr.say(
        "Te voy a comunicar con un agente humano. Por favor espera.",
        language="es-ES",
        voice="Polly.Lupe",
    )

    if AGENT_SIP and AGENT_SIP.startswith("sip:"):
        dial = vr.dial()
        dial.sip(AGENT_SIP)
    elif AGENT_NUMBER:
        dial = vr.dial()
        dial.number(AGENT_NUMBER)
    else:
        vr.say(
            "No tengo un destino configurado para agentes en este momento.",
            language="es-ES",
            voice="Polly.Lupe"
        )

    return Response(str(vr), mimetype="text/xml")


# =========================
#  IVR PRINCIPAL
# =========================
@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    """
    Webhook que Twilio llama con SpeechResult / Digits.
    Usa un parámetro 'phase' para saber si es primera vez o seguimiento.
    """
    t_inicio = time.monotonic()

    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")
    phase = request.args.get("phase", "initial")  # "initial" o "followup"

    logging.info(f"[IVR] Phase: {phase} | SpeechResult: {speech} | Digits: {digits}")

    vr = VoiceResponse()

    # 1) Sin input (tanto en initial como followup)
    if not speech and not digits:
        # Si es followup y no respondió, colgamos elegante
        if phase == "followup":
            vr.say(
                "No recibí ninguna respuesta. Muchas gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe",
            )
            vr.hangup()
            t_fin = time.monotonic()
            logging.info(f"[IVR] Sin respuesta en followup, llamada terminada. Handler tomó: {t_fin - t_inicio:.2f} s")
            return Response(str(vr), mimetype="text/xml")

        # Primera vez: mensaje inicial DEL PROMPT
        gather = Gather(
            input="speech dtmf",
            num_digits=1,
            language="es-ES",
            action="/ivr-llm",   # sigue yendo a /ivr-llm (phase=initial)
            method="POST",
            timeout=4,
            speech_timeout="auto"
        )
        gather.say(
            "¡Hola! Soy el Agente de Inteligencia Artificial de Nuxway Technology. "
            "Para comenzar, ¿podrías brindarme tu nombre y el de tu empresa, por favor? ",
            language="es-ES",
            voice="Polly.Lupe",
        )
        vr.append(gather)

        vr.say(
            "No escuché ninguna respuesta. Hasta luego.",
            language="es-ES",
            voice="Polly.Lupe",
        )

        t_fin = time.monotonic()
        logging.info(f"[IVR] Sin input en fase initial, handler tomó: {t_fin - t_inicio:.2f} s")
        return Response(str(vr), mimetype="text/xml")

    # 2) Detectar si pidió humano
    texto = (speech or "").lower()
    if (digits == "0") or ("agente" in texto) or ("humano" in texto):
        logging.info("[IVR] Usuario pidió agente humano.")
        t_fin = time.monotonic()
        logging.info(f"[IVR] Tiempo hasta transferir a agente: {t_fin - t_inicio:.2f} s")
        return transferir_a_agente(vr)

    # 3) GPT para conversación normal
    t_gpt_ini = time.monotonic()
    respuesta = llamar_gpt(speech or "")
    t_gpt_fin = time.monotonic()
    logging.info(f"[IVR] llamar_gpt() tardó: {t_gpt_fin - t_gpt_ini:.2f} s")
    logging.info(f"[IVR] Respuesta GPT: {respuesta}")

    vr.say(
        respuesta,
        language="es-ES",
        voice="Polly.Lupe",
    )

    # 4) Segundo gather para continuar la conversación
    #    Ahora marcamos phase=followup para que si NO responde, cuelgue.
    gather2 = Gather(
        input="speech dtmf",
        num_digits=1,
        language="es-ES",
        action="/ivr-llm?phase=followup",
        method="POST",
        timeout=4,
        speech_timeout="auto"
    )
    gather2.say(
        "¿Puedo ayudarte en algo más? "
        "Recuerda que si quieres un humano puedes decir la palabra humano o marcar cero. "
        "Si no respondes, finalizaré la llamada.",
        language="es-ES",
        voice="Polly.Lupe",
    )
    vr.append(gather2)

    t_fin = time.monotonic()
    logging.info(f"[IVR] Handler /ivr-llm total: {t_fin - t_inicio:.2f} s")

    return Response(str(vr), mimetype="text/xml")


# =========================
#  TEST DE LATENCIA DIRECTA
# =========================
@app.route("/test-gpt", methods=["GET"])
def test_gpt():
    """
    Endpoint de prueba para medir solo Render + GPT, sin Twilio.
    """
    t0 = time.monotonic()
    respuesta = llamar_gpt("Responde brevemente: ¿qué es Nuxway Technology?")
    t1 = time.monotonic()
    return (
        f"Respuesta GPT: {respuesta}\n"
        f"Tiempo total en servidor (Render + GPT): {t1 - t0:.2f} s\n"
    )


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Ingeniero de Soporte IA 🚀"


if __name__ == "__main__":
    # Para local está bien debug=True. En Render normalmente no.
    app.run(host="0.0.0.0", port=5000, debug=True)


