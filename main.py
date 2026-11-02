import os
import sqlite3
from fastapi import FastAPI, File, Form, UploadFile, Response
from groq import Groq

app = FastAPI()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY)

DB_FILE = "assistant_complete.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # טבלת זיכרון שיחות
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            phone TEXT,
            role TEXT,
            content TEXT
        )
    """)
    # טבלת משימות (To-Do List)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            phone TEXT,
            task TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_history(phone: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM memory WHERE phone = ? ORDER BY rowid DESC LIMIT 6", (phone,))
    rows = cursor.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def save_message(phone: str, role: str, content: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO memory (phone, role, content) VALUES (?, ?, ?)", (phone, role, content))
    conn.commit()
    conn.close()

def manage_tasks(phone: str, action: str, task_text: str = ""):
    """ניהול רשימת המשימות של המשתמש"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if action == "add":
        cursor.execute("INSERT INTO tasks (phone, task) VALUES (?, ?)", (phone, task_text))
        conn.commit()
        result = f"ההודיה נוספה בהצלחה למשימות: {task_text}"
    elif action == "list":
        cursor.execute("SELECT task FROM tasks WHERE phone = ? AND status = 'pending'", (phone,))
        rows = cursor.fetchall()
        if rows:
            result = "המשימות הפתוחות שלך הן: " + ", ".join([r[0] for r in rows])
        else:
            result = "אין לך משימות פתוחות כרגע."
    else:
        result = "פעולה לא נγνרה."
        
    conn.close()
    return result

@app.post("/voice")
async def voice_webhook(
    Phone: str = Form(None),
    Api_phone: str = Form(None),
    file: UploadFile = File(None)
):
    phone = Phone or Api_phone or "unknown"

    if not file:
        response_commands = "read=M1000,אנא דבר לאחר הצליל,min=1,max=100,block_keypress=no,api_send_recording=yes"
        return Response(content=response_commands, media_type="text/plain")

    audio_path = f"temp_{phone}.wav"
    with open(audio_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)

    try:
        with open(audio_path, "rb") as audio_file:
            transcript_res = groq_client.audio.transcriptions.create(
                file=(audio_path, audio_file.read()),
                model="whisper-large-v3",
                language="he"
            )
        user_text = transcript_res.text
    except Exception as e:
        user_text = "שגיאה בתמלול הקול"

    if os.path.exists(audio_path):
        os.remove(audio_path)

    # בדיקה מהירה אם המשתמש ביקש לנהל משימות
    action_result = ""
    if "תוסיף למשימות" in user_text or "הוסף למשימה" in user_text:
        task_content = user_text.replace("תוסיף למשימות", "").replace("הוסף למשימה", "").strip()
        action_result = manage_tasks(phone, "add", task_content)
    elif "מה המשימות" in user_text or "הצג משימות" in user_text:
        action_result = manage_tasks(phone, "list")

    # ניהול שיחה עם Llama 3
    history = get_history(phone)
    system_prompt = (
        "אתה עוזר אישי קולי חכם, מדויק וידידותי המדבר בטלפון. "
        "ענה בקצרה, בשפה טבעית ונעימה. "
        f"נתונים ממערכת המשימות אם רלוונטי: {action_result}"
    )
    
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.7
        )
        ai_reply = completion.choices[0].message.content
    except Exception as e:
        ai_reply = "סליחה, אירעה שגיאה בעיבוד הבקשה."

    save_message(phone, "user", user_text)
    save_message(phone, "assistant", ai_reply)

    response_commands = f"text={ai_reply}\nread=M1000,,min=1,max=100,block_keypress=no,api_send_recording=yes"
    return Response(content=response_commands, media_type="text/plain")
