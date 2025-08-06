from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI
from dotenv import load_dotenv
import os
import json
import re
from db import init_db, guardar_mensaje
from scheduler import start_scheduler, enviar_mensaje_programado
from utils import obtener_numero_destinatario
from datetime import datetime, timedelta
import redis

# Cargar variables de entorno
load_dotenv()

client = OpenAI(
    api_key=os.getenv("GITHUB_TOKEN"),
    base_url="https://models.github.ai/inference",  # Modificá si cambia tu endpoint
)

model = "deepseek/DeepSeek-V3-0324"

app = Flask(__name__)

# Configuración de Redis
redis_host = os.getenv("REDIS_HOST", "localhost")  # Usa localhost por defecto
redis_port = int(os.getenv("REDIS_PORT", 6379))    # Usa el puerto por defecto
redis_db = int(os.getenv("REDIS_DB", 0))          # Usa la base de datos 0 por defecto
r = redis.StrictRedis(host=redis_host, port=redis_port, db=redis_db, decode_responses=True)

# Clave base para el contexto de recordatorios en Redis
CONTEXTO_KEY_PREFIX = "recordatorio_context:"

def guardar_contexto_recordatorio(remitente, contexto):
    """Guarda el contexto de un recordatorio pendiente de confirmación en Redis"""
    key = f"{CONTEXTO_KEY_PREFIX}{remitente}"
    r.hmset(key, contexto)
    # Opcional: establecer un tiempo de expiración para el contexto
    r.expire(key, 3600) # Expira en 1 hora (ajusta según necesidad)

def obtener_contexto_recordatorio(remitente):
    """Obtiene el contexto de un recordatorio pendiente para este usuario desde Redis"""
    key = f"{CONTEXTO_KEY_PREFIX}{remitente}"
    return r.hgetall(key)

def limpiar_contexto_recordatorio(remitente):
    """Elimina el contexto una vez procesado de Redis"""
    key = f"{CONTEXTO_KEY_PREFIX}{remitente}"
    r.delete(key)

# ... el resto de tu código app.py ...

def interpretar_mensaje(mensaje_usuario):
    hora_actual = datetime.now().isoformat()
    prompt_sistema = (
        f"Tu tarea es detectar si el mensaje del usuario implica que desea un recordatorio.\n"
        f"La hora actual es: {hora_actual}\n"
        "Debes responder exclusivamente con un JSON plano que contenga estas claves:\n"
        "- 'respuesta': una frase breve y amistosa confirmando lo que pidió\n"
        "- 'es_recordatorio': true o false\n"
        "- 'fecha_hora': Una fecha y hora en formato ISO 8601 (por ejemplo: '2025-05-08T13:13:00') si se puede deducir del mensaje. Si no se puede deducir, devolver null\n"
        "- 'tipo_evento': categoriza el evento como 'cita' (médica, importante, reunión formal), 'tarea_rutinaria' (medicamentos, comidas), o 'recordatorio_simple' (otros)\n"
        "- 'tiempo_anticipacion': determina si este tipo de evento se beneficiaría de un recordatorio anticipado. Devuelve true para citas importantes que no son inmediatas, false para tareas rutinarias o recordatorios inmediatos (menos de 2 horas)\n"
        "No uses etiquetas ```json, ni texto adicional, solo JSON."
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": mensaje_usuario}
        ],
        temperature=0.3,
        max_tokens=300
    )

    contenido = response.choices[0].message.content.strip()
    print("Respuesta cruda IA:", contenido)

    # Limpiar posibles ```json o ``` 
    contenido_limpio = re.sub(r"^```json\s*|\s*```$", "", contenido.strip())

    try:
        resultado = json.loads(contenido_limpio)
        # Asegurar que los campos necesarios existan
        if 'tipo_evento' not in resultado:
            resultado['tipo_evento'] = 'recordatorio_simple'
        if 'tiempo_anticipacion' not in resultado:
            resultado['tiempo_anticipacion'] = False
        return resultado
    except Exception as e:
        print("Error interpretando la respuesta IA:", str(e))
        return {
            "respuesta": "No entendí bien si querías un recordatorio 🤔. ¿Podés repetirlo?",
            "es_recordatorio": False,
            "fecha_hora": None,
            "tipo_evento": "recordatorio_simple",
            "tiempo_anticipacion": False
        }

@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    mensaje_usuario = request.form.get('Body')
    remitente = request.form.get('From')
    print(f"Mensaje recibido de WhatsApp: {mensaje_usuario}")

    destinatario = obtener_numero_destinatario(mensaje_usuario, remitente)

    guardar_mensaje("Usuario WhatsApp", mensaje_usuario)

    resultado = interpretar_mensaje(mensaje_usuario)

    if resultado["es_recordatorio"] and resultado["fecha_hora"]:
        # Verificar si es un evento que se beneficiaría de un recordatorio anticipado
        if resultado["tiempo_anticipacion"] and resultado["tipo_evento"] == "cita":
            # Preguntar al usuario si desea un recordatorio anticipado
            respuesta_texto = f"{resultado['respuesta']} ¿Querés que te avise también 30 minutos antes? (Responde 'Sí' o 'No')"
            
            # Guardar el contexto de este recordatorio para procesar la siguiente respuesta
            # Esto requiere implementar algún tipo de gestión de estado/sesión
            guardar_contexto_recordatorio(remitente, {
                "fecha_hora": resultado["fecha_hora"],
                "mensaje_original": mensaje_usuario,
                "pendiente_confirmacion": True
            })
        else:
            # Proceder normalmente para recordatorios que no necesitan anticipación
            texto_recordatorio = generar_texto_recordatorio(mensaje_usuario, resultado["fecha_hora"])
            enviar_mensaje_programado(
                destinatario, 
                texto_recordatorio,
                resultado["fecha_hora"]
            )
            respuesta_texto = resultado["respuesta"]
    else:
        # Verificar si es una respuesta a una pregunta de recordatorio anticipado
        contexto = obtener_contexto_recordatorio(remitente)
        if contexto and contexto.get("pendiente_confirmacion") and re.match(r'^(s[iíì]|yes|ok|dale|claro)', mensaje_usuario.lower()):
            # El usuario quiere recordatorio anticipado
            fecha_hora_original = contexto["fecha_hora"]
            fecha_obj = datetime.fromisoformat(fecha_hora_original)
            
            # Calcular 30 minutos antes
            fecha_anticipada = fecha_obj - timedelta(minutes=30)
            fecha_anticipada_iso = fecha_anticipada.isoformat()
            
            # Generar textos para ambos recordatorios
            # Paso 1: Añadir esta nueva función justo después de generar_texto_recordatorio()
            def generar_texto_recordatorio_anticipado(mensaje_usuario):
                """
                Genera un texto personalizado para el recordatorio anticipado basado en el mensaje original del usuario
                """
                # Extraer el propósito del recordatorio
                prompt_sistema = (
                    "Extrae ÚNICAMENTE el propósito o evento principal del recordatorio solicitado.\n"
                    "Responde solo con 2-5 palabras que describan el evento, sin ningún texto adicional.\n"
                    "Ejemplo 1: Si el mensaje es 'Recuérdame mi cita médica mañana a las 3', responde solo 'cita médica'\n"
                    "Ejemplo 2: Si el mensaje es 'Necesito que me avises a las 8pm para llamar a Juan', responde solo 'llamar a Juan'"
                )
                
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": prompt_sistema},
                        {"role": "user", "content": mensaje_usuario}
                    ],
                    temperature=0.3,
                    max_tokens=20
                )
                
                proposito = response.choices[0].message.content.strip()
                
                # Construimos el recordatorio con un formato natural y amigable
                texto_recordatorio = f"Recordatorio anticipado: En 30 minutos tendrás tu {proposito}. ¡Prepárate!"
                
                return texto_recordatorio

            # Paso 2: Reemplazar la línea problemática en la sección donde maneja respuestas positivas a recordatorios anticipados
            # Buscar esta línea:
            # texto_recordatorio_anticipado = f"Recordatorio anticipado: En 30 minutos tendrás {contexto['mensaje_original']}" 
            # Reemplazarla por:
            texto_recordatorio_anticipado = generar_texto_recordatorio_anticipado(contexto["mensaje_original"])

            texto_recordatorio_principal = generar_texto_recordatorio(contexto["mensaje_original"], fecha_hora_original)
            
            # Programar ambos recordatorios
            enviar_mensaje_programado(destinatario, texto_recordatorio_anticipado, fecha_anticipada_iso)
            enviar_mensaje_programado(destinatario, texto_recordatorio_principal, fecha_hora_original)
            
            respuesta_texto = "¡Perfecto! Te avisaré 30 minutos antes y también a la hora exacta."
            
            # Limpiar el contexto
            limpiar_contexto_recordatorio(remitente)
        elif contexto and contexto.get("pendiente_confirmacion"):
            # El usuario no quiere recordatorio anticipado
            texto_recordatorio = generar_texto_recordatorio(contexto["mensaje_original"], contexto["fecha_hora"])
            enviar_mensaje_programado(destinatario, texto_recordatorio, contexto["fecha_hora"])
            respuesta_texto = "Entendido, te avisaré solamente a la hora exacta."
            
            # Limpiar el contexto
            limpiar_contexto_recordatorio(remitente)
        else:
            # Respuesta normal de IA
            respuesta_texto = responder_ai(mensaje_usuario)

    guardar_mensaje("IA", respuesta_texto)

    respuesta = MessagingResponse()
    respuesta.message(respuesta_texto)

    return str(respuesta)

# Variables globales para gestión de contexto (en producción, deberías usar Redis o una base de datos)
_contextos_recordatorios = {}

def guardar_contexto_recordatorio(remitente, contexto):
    """Guarda el contexto de un recordatorio pendiente de confirmación"""
    _contextos_recordatorios[remitente] = contexto

def obtener_contexto_recordatorio(remitente):
    """Obtiene el contexto de un recordatorio pendiente para este usuario"""
    return _contextos_recordatorios.get(remitente)

def limpiar_contexto_recordatorio(remitente):
    """Elimina el contexto una vez procesado"""
    if remitente in _contextos_recordatorios:
        del _contextos_recordatorios[remitente]

def generar_texto_recordatorio(mensaje_usuario, fecha_hora):
    """
    Genera un texto personalizado para el recordatorio basado en el mensaje original del usuario
    """
    # Convertir fecha_hora a formato legible
    fecha_obj = datetime.fromisoformat(fecha_hora)
    fecha_formateada = fecha_obj.strftime('%H:%M')
    
    prompt_sistema = (
        "Tu tarea es crear un mensaje de recordatorio basado en la solicitud del usuario.\n"
        "IMPORTANTE: El usuario recibirá este mensaje EXACTAMENTE a la hora indicada, no después.\n"
        "Por ejemplo, si el recordatorio es para las 20:00, el usuario lo recibirá a las 20:00 exactas.\n"
        "No añadas frases como 'en X minutos' o referencias a cuánto tiempo falta.\n\n"
        "Reglas adicionales:\n"
        "1. Sé breve y conciso, máximo 1-2 líneas\n"
        "2. Incluye la hora del evento y su propósito\n"
        "3. Asume que la hora del recordatorio ES la hora del evento mencionado\n"
        "4. No uses emojis, el sistema ya los añadirá\n"
        "Ejemplo correcto: 'Recordatorio: Tu cita médica es ahora, a las 15:30'\n"
        "Ejemplo INCORRECTO: 'Tu cita médica es a las 15:30, ¡en 5 minutos!'"
    )
    
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": f"Solicitud original: '{mensaje_usuario}'. La hora del recordatorio es: {fecha_formateada}"}
        ],
        temperature=0.7,
        max_tokens=150
    )
    
    texto_recordatorio = response.choices[0].message.content.strip()
    return texto_recordatorio

def responder_ai(mensaje_usuario):
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": ""},
            {"role": "user", "content": mensaje_usuario}
        ],
        temperature=0.8,
        top_p=0.1,
        max_tokens=2048
    )
    return response.choices[0].message.content

if __name__ == "__main__":
    init_db()
    start_scheduler()
    app.run(port=5000)

