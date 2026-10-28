import os
import sqlite3
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from claude_webapi import ClaudeClient

load_dotenv()

client: ClaudeClient | None = None
current_session_key: str | None = None

class ChatRequest(BaseModel):
    text: str
    session_key: str | None = None

def get_credentials_from_db():
    try:
        db_path = os.environ.get("SQLITE_DB_PATH", "/app/data/sqlite.db")
        if not os.path.exists(db_path):
            return None, None
            
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check column names in settings table to adapt flexibly
        cursor.execute("PRAGMA table_info(settings)")
        columns = [info[1].lower() for info in cursor.fetchall()]
        
        session_key_col = next(
            (c for c in ["claude_session_key", "claude_sessionkey", "session_key", "sessionkey"] if c in columns),
            None
        )
        
        if not session_key_col:
            conn.close()
            return None, None
            
        cursor.execute(f"SELECT {session_key_col} FROM settings LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return row[0], None
    except Exception as e:
        print(f"Error reading SQLite DB: {e}")
        
    return None, None

async def ensure_client(session_key_override: str | None = None):
    global client, current_session_key
    
    session_key = session_key_override
    if not session_key:
        session_key, _ = get_credentials_from_db()
    if not session_key:
        session_key = os.environ.get("CLAUDE_SESSION_KEY") or os.environ.get("SESSION_KEY")

    if not session_key:
        raise RuntimeError("Missing CLAUDE_SESSION_KEY. Please provide it in request body, DB, or .env")

    proxy = os.environ.get("CLAUDE_PROXY")

    # Recreate client if missing or session key changed
    if client is None or session_key != current_session_key:
        print("Session key changed or client missing. Initializing ClaudeClient...")
        
        if client is not None:
            try:
                await client.close()
            except Exception as e:
                print(f"Error closing previous ClaudeClient: {e}")

        # ClaudeClient will auto-discover organization_id automatically
        new_client = ClaudeClient(
            session_key=session_key,
            proxy=proxy,
        )
        
        timeout_sec = int(os.environ.get("CLAUDE_TIMEOUT", 60))
        auto_close = os.environ.get("CLAUDE_AUTO_CLOSE", "true").lower() in ("true", "1")
        close_delay = int(os.environ.get("CLAUDE_CLOSE_DELAY", 300))

        await new_client.init(
            timeout=timeout_sec,
            auto_close=auto_close,
            close_delay=close_delay,
        )

        client = new_client
        current_session_key = session_key
        print(f"ClaudeClient successfully initialized (org: {new_client._organization_id}).")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    try:
        await ensure_client()
        print("ClaudeClient initialized successfully on startup.")
    except Exception as e:
        print(f"Note: ClaudeClient not initialized on startup (waiting for request or config): {e}")
    yield
    if client is not None:
        try:
            await client.close()
            print("ClaudeClient connection closed.")
        except Exception as e:
            print(f"Error during ClaudeClient shutdown: {e}")

app = FastAPI(title="Claude WebAPI Service", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        await ensure_client(session_key_override=req.session_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Client initialization error: {str(e)}")
        
    global client
    if client is None:
        raise HTTPException(status_code=500, detail="Claude client not initialized")

    try:
        response = await client.generate_content(req.text)
        return {"reply": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    try:
        await ensure_client(session_key_override=req.session_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Client initialization error: {str(e)}")

    global client
    if client is None:
        raise HTTPException(status_code=500, detail="Claude client not initialized")

    async def event_generator():
        try:
            async for chunk in client.generate_content_stream(req.text):
                if chunk.text_delta:
                    yield chunk.text_delta
        except Exception as err:
            yield f"\n[Error: {str(err)}]"

    return StreamingResponse(event_generator(), media_type="text/plain")
