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
session = requests.Session()  # mantener conexiones HTTP abiertas


# =========================
# PROMPT DEL AGENTE IA
# =========================
SYSTEM_PROMPT = """
Eres un Ingeniero de Soporte Especializado de Nuxway Technology.
Respondes SOLO en español.

🎯 Objetivo general
Brindar soporte y orientación a clientes de Nuxway por teléfono, de forma profesional, clara y amable.

🎙 Primera interacción (muy importante)
En la PRIMERA respuesta al cliente (cuando aún no sabes su nombre):
1) Preséntate como: "Hola, soy el Agente de Inteligencia Artificial de Nuxway Technology."
2) Felicita brevemente por las fiestas de fin de año y Año Nuevo.
3) Luego pide de forma amable el nombre de la persona y el de su empresa.
4) Integra de forma natural (solo UNA vez) el siguiente mensaje:

"Queremos desearle unas felices fiestas de fin de año de parte de toda la familia Nuxway. Agradecemos su confianza y reafirmamos nuestro compromiso de seguir mejorando el soporte para sus redes de datos y comunicaciones unificadas."

No lo repitas en cada turno; solo en el saludo inicial.

🎛 Estilo de comunicación
- Profesional, claro y amable.
- Frases cortas, adecuadas para ser escuchadas por teléfono.
- Tono empático, paciente y tranquilo.
- Usa lenguaje simple cuando el cliente no parece técnico.

📚 Contexto de la empresa (información de fondo)
Nuxway Technology SRL es una empresa boliviana especializada en soluciones tecnológicas
para comunicaciones empresariales, call centers, contact centers y redes de datos.
Impulsamos la transformación digital de las organizaciones mediante infraestructura y
software profesional, permitiendo que las comunicaciones de nuestros clientes sean
escalables, eficientes, de menor costo y fáciles de administrar.

1. Productos e Infraestructura Tecnológica

Infraestructura de Red y Comunicaciones
- Venta e implementación de infraestructura de red con cobertura nacional.
- Equipamiento profesional para telecomunicaciones:
  • Telefonía IP, VoIP y PBX
  • Switches, routers y firewalls empresariales
  • Soluciones de comunicaciones unificadas

Representantes oficiales de Yeastar en Bolivia, con soporte certificado y equipamiento original.

Soluciones Propietarias (Nuxway Services)
- NuxCaller: Plataforma de discado automático (predictivo, progresivo y preview)
  para campañas masivas.

Gateways y Conectividad
- NuxGATE: Gateways para líneas SIP, E1/PRI y GSM, integrables con plataformas
  corporativas y operadores telco.

Telefonía en la Nube
- Cloud PBX: Central telefónica virtual, escalable, segura y administrable
  completamente desde la nube.

Soluciones para Contact Center
- Contact Center Nuxway: Plataforma integral para centros de contacto con:
  • Campañas entrantes y salientes
  • Marcador predictivo
  • Reportes en tiempo real
  • Integración con CRM y sistemas externos
  • Chat y llamadas web directamente desde la página web del cliente

2. Servicios de Consultoría, Integración y Soporte
- Diseño estratégico y planificación de proyectos TIC.
- Integración y desarrollo de soluciones a medida para cada cliente.
- Instalación de redes cableadas e inalámbricas.
- Diseño y despliegue de redes WiFi empresariales.
- Configuración de VPN y redes seguras.

Soporte y Mantenimiento
- Soporte técnico especializado en infraestructura, telefonía IP y servicios en la nube.
- Monitoreo y mantenimiento preventivo y correctivo.
- Proyectos llave en mano: diseño, dimensionamiento, implementación y acompañamiento post-venta.

Para más información:
- Sitios web: nuxway.net | nuxway.services
- Redes sociales: Facebook y LinkedIn como Nuxway Technology.

📏 Reglas operativas
- Siempre intenta entender primero la necesidad del cliente (haz 1 o 2 preguntas claras).
- Acompaña paso a paso cuando des instrucciones técnicas.
- Si el cliente pide hablar con un humano o la situación lo requiere, sugiere derivar a un agente humano.
- Nunca inventes información técnica; si no sabes algo, dilo con honestidad y sugiere escalar el caso.
"""


# =========================
#  LLAMADA A GPT
# =========================
def llamar_gpt(prompt_usuario: str) -> str:
    if not OPENAI_API_KEY:
        logging.error("OPENAI_API_KEY no configurada")
        return "Hay un problema con la configuración de la inteligencia artificial."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "gpt-4.1-nano",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt_usuario}
        ],
        "max_tokens": 80,
        "temperature": 0.2
    }

    t0 = time.monotonic()
    try:
        r = session.post(OPENAI_URL, json=data, headers=headers, timeout=6)
        lat = time.monotonic() - t0
        logging.info(f"[GPT] {r.status_code} | {lat:.2f} s")

        if r.status_code != 200:
            logging.error(f"[GPT] Error body: {r.text[:300]}")
            return "Tengo problemas con la inteligencia artificial en este momento."

        return r.json()["choices"][0]["message"]["content"]

    except Exception:
        logging.exception("[GPT] Error")
        return "Hubo un problema con la inteligencia artificial, intenta nuevamente."


# =========================
#  TRANSFERENCIA A HUMANO
# =========================
AGENT_SIP = "sip:6049@nuxway.sip.twilio.com"

def transferir_a_agente(vr: VoiceResponse) -> Response:
    vr.say(
        "Te voy a comunicar con un agente humano. Por favor espera.",
        language="es-ES",
        voice="Polly.Lupe"
    )
    dial = vr.dial()
    dial.sip(AGENT_SIP)
    return Response(str(vr), mimetype="text/xml")


# =========================
#  IVR PRINCIPAL
# =========================
@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")

    phase = request.args.get("phase", "initial")  # "initial" / "followup"
    attempt_param = request.args.get("attempt", "1")
    attempt = int(attempt_param)

    logging.info(f"[IVR] phase={phase} attempt={attempt} speech={speech} digits={digits}")

    vr = VoiceResponse()

    # ==========================================
    # 1) SILENCIO / SIN INPUT
    # ==========================================
    if not speech and not digits:

        # FOLLOWUP: si no responde → colgar directo
        if phase == "followup":
            vr.say(
                "No recibí ninguna respuesta. Gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # INITIAL: repetir mensaje 2 veces, en la 3ra colgar
        if attempt >= 3:
            vr.say(
                "No escuché ninguna respuesta. Muchas gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        next_attempt = attempt + 1

        # Mensaje inicial / repetido
        if attempt == 1:
            mensaje = (
                "Hola, soy el Agente de Inteligencia Artificial de Nuxway Technology. "
                "Para comenzar, por favor dime tu nombre y el de tu empresa después de este mensaje."
            )
        else:  # attempt == 2
            mensaje = (
                "Parece que no logré escucharte. Te repito nuevamente el mensaje. "
                "Por favor, dime tu nombre y el de tu empresa después de este mensaje."
            )

        gather = Gather(
            input="speech",  # solo voz, nada de DTMF
            language="es-ES",
            action=f"/ivr-llm?phase=initial&attempt={next_attempt}",
            method="POST",
            timeout=7,        # tiempo total para que hable
            speech_timeout="3"  # 3 segundos de silencio antes de cortar
        )
        gather.say(mensaje, language="es-ES", voice="Polly.Lupe")
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # ==========================================
    # 2) PIDIÓ HUMANO
    # ==========================================
    text_lower = (speech or "").lower()
    if digits == "0" or "humano" in text_lower or "agente" in text_lower:
        return transferir_a_agente(vr)

    # ==========================================
    # 3) GPT RESPONDE
    # ==========================================
    respuesta_gpt = llamar_gpt(speech or "")
    vr.say(respuesta_gpt, language="es-ES", voice="Polly.Lupe")

    # ==========================================
    # 4) FOLLOWUP
    # ==========================================
    gather2 = Gather(
        input="speech",
        language="es-ES",
        action="/ivr-llm?phase=followup&attempt=1",
        method="POST",
        timeout=7,
        speech_timeout="3"
    )
    gather2.say(
        "¿Puedo ayudarte en algo más? "
        "Si necesitas un humano, di 'humano' o marca cero. "
        "Si no me respondes, finalizaré la llamada.",
        language="es-ES",
        voice="Polly.Lupe"
    )
    vr.append(gather2)

    return Response(str(vr), mimetype="text/xml")


# =========================
#  HOME
# =========================
@app.route("/")
def home():
    return "Nuxway IVR LLM – Soporte IA activo."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)


