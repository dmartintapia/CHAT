# utils.py
from contactos import contactos

def obtener_numero_destinatario(texto_usuario, remitente):
    texto = texto_usuario.lower()
    for nombre in contactos:
        if nombre in texto:
            numero = contactos[nombre]
            return f"whatsapp:{numero}" if numero else remitente
    return remitente  # Por defecto, responder al mismo usuario