import os
import sqlite3
import time
import traceback
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from claude_webapi import ClaudeClient, set_log_level
from claude_webapi.constants import Model

load_dotenv()

# Configure logging level from env (default INFO)
log_level = os.environ.get("LOG_LEVEL", "INFO")
set_log_level(log_level)

client: ClaudeClient | None = None
current_session_key: str | None = None

class ChatRequest(BaseModel):
    text: str
    session_key: str | None = None
    model: str | None = None

def resolve_model_name(name: str | None) -> str | None:
    """Map friendly names like 'haiku', 'sonnet', 'opus' to internal web model strings."""
    if not name:
        name = os.environ.get("CLAUDE_MODEL", "")
    if not name:
        return None
    cleaned = name.lower().strip()
    if "haiku" in cleaned:
        return Model.HAIKU.value
    if "opus" in cleaned:
        return Model.OPUS.value
    if "sonnet" in cleaned:
        return Model.SONNET.value
    return name

def get_credentials_from_db():
    try:
        db_path = os.environ.get("SQLITE_DB_PATH", "/app/data/sqlite.db")
        if not os.path.exists(db_path):
            return None, None
            
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
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
        print(f"[DB Warning] Error reading SQLite DB: {e}")
        
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

    # Check if existing client session is still alive
    session_is_closed = False
    if client is not None:
        try:
            session_is_closed = client._session is None or client._session.closed
        except Exception:
            session_is_closed = True

    # Recreate client if missing, session was closed, or session key changed
    if client is None or session_is_closed or session_key != current_session_key:
        print("[Client] Session key changed or session closed. Initializing ClaudeClient...")
        
        if client is not None:
            try:
                await client.close()
            except Exception as e:
                print(f"[Client Warning] Error closing previous ClaudeClient: {e}")

        # ClaudeClient will auto-discover organization_id automatically
        new_client = ClaudeClient(
            session_key=session_key,
            proxy=proxy,
        )
        
        timeout_sec = int(os.environ.get("CLAUDE_TIMEOUT", 180))
        auto_close = os.environ.get("CLAUDE_AUTO_CLOSE", "false").lower() in ("true", "1")
        close_delay = int(os.environ.get("CLAUDE_CLOSE_DELAY", 600))

        await new_client.init(
            timeout=timeout_sec,
            auto_close=auto_close,
            close_delay=close_delay,
        )

        client = new_client
        current_session_key = session_key
        print(f"[Client] ClaudeClient successfully initialized (org: {new_client._organization_id}).")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    try:
        await ensure_client()
        print("[Startup] ClaudeClient initialized successfully.")
    except Exception as e:
        print(f"[Startup Note] ClaudeClient not pre-initialized on startup (will initialize on first request): {e}")
    yield
    if client is not None:
        try:
            await client.close()
            print("[Shutdown] ClaudeClient connection closed.")
        except Exception as e:
            print(f"[Shutdown Error] Error during ClaudeClient shutdown: {e}")

app = FastAPI(title="Claude WebAPI Service", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/chat")
async def chat(req: ChatRequest):
    t0 = time.time()
    target_model = resolve_model_name(req.model)
    print(f"\n[Chat] Received request ({len(req.text)} chars, target_model: {target_model or 'default'})")
    
    try:
        await ensure_client(session_key_override=req.session_key)
    except Exception as e:
        print(f"[Chat Error] Client init failed: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Client initialization error: {str(e)}")
        
    global client
    if client is None:
        print("[Chat Error] Claude client is None after ensure_client()")
        raise HTTPException(status_code=500, detail="Claude client not initialized")

    try:
        print(f"[Chat] Sending prompt to Claude.ai ({target_model or 'default'})...")
        if target_model:
            response = await client.generate_content(req.text, model=target_model)
        else:
            response = await client.generate_content(req.text)
            
        duration = round(time.time() - t0, 2)
        print(f"[Chat] SUCCESS! Received reply ({len(response.text)} chars) in {duration}s")
        return {"reply": response.text}
    except Exception as e:
        duration = round(time.time() - t0, 2)
        print(f"[Chat Error] generate_content failed after {duration}s: {e}")
        traceback.print_exc()
        
        # Reset client so next request starts fresh
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
            client = None
            
        raise HTTPException(status_code=500, detail=f"Claude request failed ({type(e).__name__}): {str(e)}")

@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    target_model = resolve_model_name(req.model)
    print(f"\n[Chat Stream] Received streaming request ({len(req.text)} chars, target_model: {target_model or 'default'})")
    try:
        await ensure_client(session_key_override=req.session_key)
    except Exception as e:
        print(f"[Chat Stream Error] Client init failed: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Client initialization error: {str(e)}")

    global client
    if client is None:
        raise HTTPException(status_code=500, detail="Claude client not initialized")

    async def event_generator():
        try:
            async for chunk in client.generate_content_stream(req.text, model=target_model):
                if chunk.text_delta:
                    yield chunk.text_delta
        except Exception as err:
            print(f"[Chat Stream Error] Streaming failed: {err}")
            traceback.print_exc()
            yield f"\n[Error: {str(err)}]"

    return StreamingResponse(event_generator(), media_type="text/plain")
