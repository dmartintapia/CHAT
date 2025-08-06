from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from twilio.rest import Client
from dotenv import load_dotenv
import os
from datetime import datetime

load_dotenv()

account_sid = os.getenv("TWILIO_SID")
auth_token = os.getenv("TWILIO_TOKEN")
from_number = os.getenv("TWILIO_FROM")
client_twilio = Client(account_sid, auth_token)

def enviar_mensaje_programado(to_number, mensaje, fecha_hora=None):
    """
    Envía el mensaje programado y devuelve confirmación con la hora.
    Args:
        to_number (str): Número destino (WhatsApp).
        mensaje (str): Contenido del recordatorio (ya generado por la IA).
        fecha_hora (str, optional): Fecha/hora en ISO 8601. Si es None, se usa intervalo fijo.
    """
    if fecha_hora:
        # Convertir fecha_hora (ISO 8601) a objeto datetime
        fecha_obj = datetime.fromisoformat(fecha_hora)
        
        # Añadimos emoji al recordatorio final
        mensaje_recordatorio = f"🔔 {mensaje}"
        
        # Programar mensaje para la fecha exacta con el texto de recordatorio generado por la IA
        scheduler.add_job(
            lambda: client_twilio.messages.create(
                body=mensaje_recordatorio,
                from_=from_number,
                to=to_number
            ),
            trigger=DateTrigger(run_date=fecha_obj),
            id=f"reminder_{to_number}_{fecha_obj.timestamp()}"
        )
        
        # Enviar confirmación inmediata con la hora
        confirmacion = f"✅ Recordatorio programado para el {fecha_obj.strftime('%d/%m/%Y a las %H:%M')}."
    else:
        # Lógica existente (intervalo fijo)
        confirmacion = "⏳ Recordatorio programado (sin hora específica)."

    # Enviar confirmación por WhatsApp
    client_twilio.messages.create(
        body=confirmacion,
        from_=from_number,
        to=to_number
    )

scheduler = BackgroundScheduler()

def start_scheduler():
    scheduler.start()