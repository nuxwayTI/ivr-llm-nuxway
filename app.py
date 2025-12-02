from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import requests

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# =========================
#  CONFIG OPENAI
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# =========================
#  CONFIG TRANSFERENCIA A AGENTE / COLA
# =========================
# IMPORTANTE:
# Aquí usamos EXACTAMENTE lo que ya comprobaste que funciona
# en tu TwiML Bin:
#   <Sip>sip:6049@nuxway.sip.twilio.com</Sip>
#
# La PBX (Yeastar) se encarga de que lo que entra por este troncal
# vaya a tu IVR o a la cola, según tu ruta entrante.
#
# Si luego quieres que vaya directo a la cola en lugar del IVR,
# solo cambias la ruta entrante en Yeastar.
AGENT_SIP = "sip:6049@nuxway.sip.twilio.com"
AGENT_NUMBER = ""  # lo dejamos vacío para no usar PSTN aquí


def llamar_gpt(user_text: str) -> str:
    """Llama a la API de OpenAI y devuelve el texto de respuesta."""
    if not OPENAI_API_KEY:
        app.logger.error("OPENAI_API_KEY no está configurada en Render.")
        return "Hay un problema interno con la configuración de la inteligencia artificial."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    data = {
        "model": "gpt-4.1-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres un asistente telefónico de Nuxway Technology. "
                    "Respondes siempre en español, de forma breve, clara y amable. "
                    "Si el usuario pide hablar con un humano o agente, "
                    "puedes decirle que lo transferirás con gusto."
                ),
            },
            {"role": "user", "content": user_text}
        ],
    }

    try:
        resp = requests.post(OPENAI_URL, headers=headers, json=data, timeout=10)
        app.logger.info(f"Status OpenAI: {resp.status_code}")
        app.logger.info(f"Cuerpo OpenAI: {resp.text}")

        if resp.status_code != 200:
            return "Tengo un problema con el servicio de inteligencia artificial. Inténtalo más tarde."

        j = resp.json()
        return j["choices"][0]["message"]["content"]

    except requests.exceptions.RequestException:
        app.logger.exception("Error de red llamando a OpenAI con requests:")
        return "Estoy teniendo problemas de conexión con la inteligencia artificial. Intenta nuevamente."

    except Exception:
        app.logger.exception("Error inesperado procesando respuesta:")
        return "Ocurrió un error interno al procesar la respuesta. Intenta nuevamente."


def transferir_a_agente(vr: VoiceResponse) -> Response:
    """
    Genera TwiML para transferir a un agente humano / cola en la PBX.
    IMPORTANTE: usamos SIP, igual que en tu TwiML Bin funcional.
    """
    vr.say(
        "Te voy a comunicar con un agente humano. Por favor espera.",
        language="es-ES",
        voice="Polly.Lupe",
    )

    # Hacemos un <Dial><Sip>sip:6049@nuxway.sip.twilio.com</Sip></Dial>
    if AGENT_SIP and AGENT_SIP.startswith("sip:"):
        dial = vr.dial()
        dial.sip(AGENT_SIP)
    elif AGENT_NUMBER:
        # Solo se usaría si quisieras marcar a un número PSTN real
        dial = vr.dial()
        dial.number(AGENT_NUMBER)
    else:
        vr.say(
            "No tengo un destino configurado para agentes en este momento.",
            language="es-ES",
            voice="Polly.Lupe"
        )

    return Response(str(vr), mimetype="text/xml")


@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")

    app.logger.info(f"SpeechResult recibido: {speech}")
    app.logger.info(f"Digits recibidos: {digits}")

    vr = VoiceResponse()

    # 1) Primera vuelta: pedir mensaje o DTMF
    if not speech and not digits:
        gather = Gather(
            input="speech dtmf",
            num_digits=1,
            language="es-ES",
            action="/ivr-llm",
            method="POST",
            timeout=5
        )
        gather.say(
            "Hola, soy un asistente de Nuxway Technology con inteligencia artificial. "
            "Puedes decirme cómo puedo ayudarte. "
            "Si quieres hablar con un agente humano, di la palabra 'agente' o presiona la tecla cero.",
            language="es-ES",
            voice="Polly.Lupe",
        )
        vr.append(gather)

        vr.say(
            "No escuché ninguna respuesta. Hasta luego.",
            language="es-ES",
            voice="Polly.Lupe",
        )
        return Response(str(vr), mimetype="text/xml")

    # 2) Detectar si pidió humano
    texto = (speech or "").lower()
    if (digits == "0") or ("agente" in texto) or ("humano" in texto):
        app.logger.info("⚡ Usuario pidió transferir a un agente humano / cola PBX.")
        return transferir_a_agente(vr)

    # 3) GPT para conversación normal
    respuesta = llamar_gpt(speech or "")
    app.logger.info(f"Respuesta GPT: {respuesta}")

    vr.say(
        respuesta,
        language="es-ES",
        voice="Polly.Lupe",
    )

    # 4) Segundo gather para seguir conversando
    gather2 = Gather(
        input="speech dtmf",
        num_digits=1,
        language="es-ES",
        action="/ivr-llm",
        method="POST",
        timeout=5
    )
    gather2.say(
        "¿Puedo ayudarte en algo más? "
        "Recuerda que si quieres un humano puedes decir 'agente' o marcar cero.",
        language="es-ES",
        voice="Polly.Lupe",
    )
    vr.append(gather2)

    return Response(str(vr), mimetype="text/xml")


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Running!"


if __name__ == "__main__":
    # En Render normalmente no se usa debug=True, pero para local está bien.
    app.run(host="0.0.0.0", port=5000, debug=True)
