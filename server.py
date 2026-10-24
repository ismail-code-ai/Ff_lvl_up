# -*- coding: utf-8 -*-
"""
FreeFire BR Bot - Web Panel server (FastAPI).

Run:  python server.py   (or: uvicorn server:app --host 0.0.0.0 --port 8000)

Includes:
  * login / register / logout with cookie sessions (see users.py)
  * per-user account scoping (each panel user only sees their own UIDs)
  * a dashboard endpoint with aggregate stats
  * WebSocket live updates filtered by owner
"""
import sys
import asyncio
import secrets

# ---------------------------------------------------------------------------
# Windows compatibility (must run BEFORE any event loop is created):
# the default ProactorEventLoop does not implement loop.sock_sendto()/
# loop.sock_recvfrom(), which the UDP match session needs. Force a Selector loop.
# ---------------------------------------------------------------------------
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

import os

from fastapi import (FastAPI, WebSocket, WebSocketDisconnect, HTTPException,
                     Request, Depends)
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from manager import AccountManager, VALID_REGIONS
from users import UserStore, AuthError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

SESSION_COOKIE = "ff_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

app = FastAPI(title="FreeFire BR Bot Panel")
manager = AccountManager()
users = UserStore()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ----------------------------- models -----------------------------
class AddAccountReq(BaseModel):
    uid: str
    password: str
    region: str = "ME"


class AuthReq(BaseModel):
    username: str
    password: str
    setup_token: str = ""


# ----------------------------- auth helpers -----------------------
def current_user(request: Request):
    """Return the logged-in user dict (or None) from the session cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    return users.get_user_by_token(token)


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def owner_of(user: dict = Depends(require_user)) -> str:
    """The owner key used to scope accounts to the logged-in user."""
    return user["username"].lower()


def _set_session_cookie(resp: JSONResponse, token: str, secure: bool) -> JSONResponse:
    # Only send the session cookie over HTTPS when the request uses HTTPS.
    # If TLS terminates at a reverse proxy, configure the ASGI proxy headers correctly.
    resp.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_MAX_AGE,
        httponly=True, secure=secure, samesite="lax", path="/",
    )
    return resp


# ----------------------------- pages ------------------------------
def _page(name: str) -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, name))


@app.get("/")
async def index(request: Request):
    if not current_user(request):
        return RedirectResponse(url="/login")
    return _page("index.html")


@app.get("/login")
async def login_page(request: Request):
    if current_user(request):
        return RedirectResponse(url="/")
    return _page("login.html")


@app.get("/dashboard")
async def dashboard_page(request: Request):
    if not current_user(request):
        return RedirectResponse(url="/login")
    return _page("dashboard.html")


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse({}, status_code=204)


# ----------------------------- auth API ---------------------------
@app.post("/api/auth/register")
async def auth_register(req: AuthReq, request: Request):
    setup_token = os.environ.get("FF_SETUP_TOKEN", "")
    if not setup_token:
        raise HTTPException(status_code=503, detail="Initial registration is disabled until FF_SETUP_TOKEN is configured")
    if not secrets.compare_digest(req.setup_token, setup_token):
        raise HTTPException(status_code=403, detail="Invalid setup token")
    try:
        user = users.register(req.username, req.password)
    except AuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    token = users.create_session(user["username"])
    return _set_session_cookie(JSONResponse({"ok": True, "user": user}), token, request.url.scheme == "https")


@app.post("/api/auth/login")
async def auth_login(req: AuthReq, request: Request):
    try:
        user = users.authenticate(req.username, req.password)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    token = users.create_session(user["username"])
    return _set_session_cookie(JSONResponse({"ok": True, "user": user}), token, request.url.scheme == "https")


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    users.destroy_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
async def auth_me(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"user": user}


# ----------------------------- API --------------------------------
@app.get("/api/regions")
async def regions():
    return {"regions": VALID_REGIONS}


@app.get("/api/accounts")
async def list_accounts(owner: str = Depends(owner_of)):
    return {"accounts": manager.list_public(owner)}


@app.post("/api/accounts")
async def add_account(req: AddAccountReq, owner: str = Depends(owner_of)):
    try:
        acc = await manager.add_account(owner, req.uid, req.password, req.region)
        return {"ok": True, "account": acc}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/accounts/{uid}")
async def remove_account(uid: str, owner: str = Depends(owner_of)):
    if not manager.get(owner, uid):
        raise HTTPException(status_code=404, detail="Account not found")
    await manager.remove_account(owner, uid)
    return {"ok": True}


@app.post("/api/accounts/{uid}/login")
async def login_account(uid: str, owner: str = Depends(owner_of)):
    if not manager.get(owner, uid):
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        acc = await manager.login_account(owner, uid)
        return {"ok": True, "account": acc}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/accounts/{uid}/start")
async def start_match(uid: str, owner: str = Depends(owner_of)):
    if not manager.get(owner, uid):
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        acc = await manager.start_match(owner, uid)
        return {"ok": True, "account": acc}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/accounts/{uid}/stop")
async def stop_match(uid: str, owner: str = Depends(owner_of)):
    if not manager.get(owner, uid):
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        acc = await manager.stop_match(owner, uid)
        return {"ok": True, "account": acc}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/dashboard")
async def dashboard(owner: str = Depends(owner_of)):
    return manager.dashboard(owner)


# ----------------------------- websocket --------------------------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    token = ws.cookies.get(SESSION_COOKIE)
    user = users.get_user_by_token(token)
    if not user:
        await ws.close(code=4401)
        return
    owner = user["username"].lower()

    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue()

    async def listener(msg):
        # only forward messages that belong to this user
        mowner = msg.get("owner")
        if mowner is not None and str(mowner).lower() != owner:
            return
        await queue.put(msg)

    manager.add_listener(listener)
    try:
        await ws.send_json({"type": "snapshot", "accounts": manager.list_public(owner)})
        while True:
            msg = await queue.get()
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        manager.remove_listener(listener)


@app.on_event("shutdown")
async def _shutdown():
    await manager.shutdown()


if __name__ == "__main__":
    import uvicorn
    # loop="asyncio" keeps our SelectorEventLoop policy (needed for UDP on Windows)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info", loop="asyncio")
