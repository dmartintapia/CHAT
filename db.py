import sqlite3

def init_db():
    conn = sqlite3.connect("chatbot.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS historial
                 (id INTEGER PRIMARY KEY, usuario TEXT, mensaje TEXT)''')
    conn.commit()
    conn.close()

def guardar_mensaje(usuario, mensaje):
    conn = sqlite3.connect("chatbot.db")
    c = conn.cursor()
    c.execute("INSERT INTO historial (usuario, mensaje) VALUES (?, ?)", (usuario, mensaje))
    conn.commit()
    conn.close()
