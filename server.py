import os
import uuid
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

# ==== CORS ====
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# ==== Хранилища ====
USERS = {}       # nick -> {password, avatar}
TOKENS = {}      # token -> nick
CLIENTS = {}     # nick -> websocket
MESSAGES = {}    # nick -> [ {from, to, text} ]

AVATAR_DIR = "avatars"
os.makedirs(AVATAR_DIR, exist_ok=True)

# ==== МОДЕЛИ ====
class AuthModel(BaseModel):
    nickname: str
    password: str

class ChangeNickModel(BaseModel):
    old_nick: str
    new_nick: str
    password: str

# ==== HTTP РОУТЫ ====
@app.post("/register")
async def register(data: AuthModel):
    nick = data.nickname.strip()
    if not nick or not data.password:
        raise HTTPException(400, "Nickname and password required")
    if nick in USERS:
        raise HTTPException(400, "User already exists")
    USERS[nick] = {"password": data.password, "avatar": None}
    MESSAGES[nick] = []
    return {"msg": "Registered"}

@app.post("/login")
async def login(data: AuthModel):
    nick = data.nickname.strip()
    if nick not in USERS or USERS[nick]["password"] != data.password:
        raise HTTPException(400, "Invalid credentials")
    token = str(uuid.uuid4())
    TOKENS[token] = nick
    return {"nickname": nick, "token": token, "avatar": USERS[nick]["avatar"]}

@app.get("/users")
async def get_users():
    """Список всех пользователей с аватарками и статусом"""
    result = []
    for nick, data in USERS.items():
        result.append({
            "nickname": nick,
            "avatar": data["avatar"],
            "online": nick in CLIENTS
        })
    return {"users": result}

@app.post("/change_nick")
async def change_nick(data: ChangeNickModel):
    if data.old_nick not in USERS or USERS[data.old_nick]["password"] != data.password:
        raise HTTPException(400, "Invalid credentials")
    if data.new_nick in USERS:
        raise HTTPException(400, "New nick already exists")
    USERS[data.new_nick] = USERS.pop(data.old_nick)
    MESSAGES[data.new_nick] = MESSAGES.pop(data.old_nick)
    for t, n in TOKENS.items():
        if n == data.old_nick:
            TOKENS[t] = data.new_nick
    if data.old_nick in CLIENTS:
        CLIENTS[data.new_nick] = CLIENTS.pop(data.old_nick)
    return {"msg": "Nick changed"}

@app.post("/upload_avatar")
async def upload_avatar(nick: str = Form(...), file: UploadFile = None):
    if not file:
        raise HTTPException(400, "No file")
    ext = os.path.splitext(file.filename)[1] or ".png"
    path = os.path.join(AVATAR_DIR, f"{nick}{ext}")
    with open(path, "wb") as f:
        f.write(await file.read())
    USERS[nick]["avatar"] = f"/avatar/{nick}{ext}"
    return {"avatar_url": USERS[nick]["avatar"]}

@app.get("/avatar/{filename}")
async def get_avatar(filename: str):
    path = os.path.join(AVATAR_DIR, filename)
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(404, "Avatar not found")

# ==== WEBSOCKET ====
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    token = websocket.query_params.get("token")
    nick = TOKENS.get(token)

    if not nick:
        await websocket.close()
        return

    CLIENTS[nick] = websocket
    print(f"✅ {nick} подключился")

    # Отправляем все непрочитанные сообщения
    for msg in MESSAGES.get(nick, []):
        await websocket.send_json(msg)
    MESSAGES[nick] = []  # очистили

    try:
        while True:
            data = await websocket.receive_json()
            typ = data.get("type")

            if typ == "msg":
                to = data.get("to")
                text = data.get("text")
                msg = {"type": "msg", "from": nick, "to": to, "text": text}

                # только получателю
                if to in CLIENTS:
                    await CLIENTS[to].send_json(msg)
                else:
                    MESSAGES[to].append(msg)

            elif typ == "typing":
                to = data.get("to")
                if to in CLIENTS:
                    await CLIENTS[to].send_json({"type": "typing", "from": nick})

    except WebSocketDisconnect:
        print(f"❌ {nick} отключился")
        CLIENTS.pop(nick, None)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
