"""
KhutbaBox — Backend principal v3
Orchestre : Deepgram STT → GPT-4.1 mini → TTS hybride → WebSocket fidèles
"""

import os
import json
import base64
import asyncio
import logging
import time
from datetime import datetime, timezone
from collections import deque
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
import uvicorn

# Modules KhutbaBox
from deepgram_stt import DeepgramSession
import gpt_translator
from gpt_translator import init_translator, charger_glossaire, traduire, warmup
from tts_engine import init_tts, generer_tts

# ── Logger ──
logger = logging.getLogger("khutbabox")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

# ── Configuration ──
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

ADMIN_PIN = os.getenv("ADMIN_PIN", "0000")
MOSQUE_NAME = os.getenv("MOSQUE_NAME", "Mosquée")
BOX_ID = os.getenv("BOX_ID", "khutbabox-001")
AUDIO_STREAM_TOKEN = os.getenv("AUDIO_STREAM_TOKEN", "")  # Token pour authentifier la source audio

# Limite de taille des messages WebSocket (512 Ko = ~16s d'audio PCM 16kHz)
MAX_WS_MESSAGE_BYTES = 512 * 1024

LANGUES = {
    "fr": "français", "en": "anglais", "es": "espagnol", "pt": "portugais",
    "tr": "turc", "ur": "ourdou", "bs": "bosnien", "sq": "albanais",
}

app = FastAPI(title="KhutbaBox", version="3.0.0")


# ============================================================
# ÉTAT GLOBAL
# ============================================================

session = {
    "active": False,
    "started_at": None,
    "mosque_name": MOSQUE_NAME,
    "mode": "live",        # "live", "quran", "adhan"
}

historique = deque(maxlen=50)

# Rolling buffer: 3 dernières phrases (arabe, dict_traductions) pour cohérence
contexte_recent: deque = deque(maxlen=3)

# Clients WebSocket connectés : {id: {"ws": WebSocket, "lang": str, "voice": str}}
clients: dict[str, dict] = {}


# ============================================================
# BROADCAST — Envoi aux smartphones
# ============================================================

async def broadcast_translation(msg_type: str, lang: str, text: str = "", audio_b64: str = "", audio_format: str = ""):
    """Envoie un message WebSocket à tous les clients connectés sur cette langue."""
    payload = {"type": msg_type, "lang": lang}
    if msg_type == "audio_chunk":
        payload["data"] = audio_b64
        payload["format"] = audio_format
    else:
        payload["text"] = text

    message = json.dumps(payload)
    for info in list(clients.values()):
        if info["lang"] == lang:
            try:
                await info["ws"].send_text(message)
            except Exception:
                pass


async def broadcast_status(mode: str):
    """Envoie un changement de mode (live/quran/adhan) à tous les clients."""
    message = json.dumps({"type": "status", "mode": mode})
    for info in list(clients.values()):
        try:
            await info["ws"].send_text(message)
        except Exception:
            pass


# ============================================================
# PIPELINE — Deepgram → GPT → TTS → Broadcast
# ============================================================

async def on_partial_transcript(text: str):
    """Reçoit le texte partiel de Deepgram → broadcast à tous les fidèles."""
    langues_connectees = {info["lang"] for info in clients.values()}
    for lang in langues_connectees:
        await broadcast_translation("partial", lang, text=text)


async def on_final_transcript(text: str):
    """
    Reçoit la phrase complète de Deepgram.
    → Traduit en 8 langues via GPT streaming parallèle
    → Diffuse chaque langue aux fidèles dès qu'elle est prête (texte + TTS)
    → Met à jour le rolling buffer pour cohérence
    """
    if session["mode"] != "live":
        return  # Coran ou Adhan en cours

    langues_connectees = list({info["lang"] for info in clients.values()})
    if not langues_connectees:
        logger.debug("[PIPELINE] Aucun fidèle connecté — pas de traduction")
        return

    # Callback 1 : token-par-token → broadcast "partial_translation"
    async def on_partial_token(lang: str, texte_accumule: str):
        await broadcast_translation("partial_translation", lang, text=texte_accumule)

    # Callback 2 : phrase complète pour une langue → texte final + TTS
    async def on_langue_finie(lang: str, texte_final: str):
        # 1. Texte final (finalise le streaming côté client)
        await broadcast_translation("final_text", lang, text=texte_final)

        # 2. Voix préférée pour cette langue
        voix = "male"
        for info in clients.values():
            if info["lang"] == lang:
                voix = info.get("voice", "male")
                break

        # 3. TTS puis broadcast audio
        try:
            audio, fmt = await generer_tts(texte_final, lang, voix)
        except Exception as e:
            logger.error(f"[TTS] Erreur {lang}: {e}")
            audio, fmt = None, ""

        if audio:
            audio_b64 = base64.b64encode(audio).decode()
            await broadcast_translation("audio_chunk", lang, audio_b64=audio_b64, audio_format=fmt)
        else:
            logger.warning(f"[TTS] Pas d'audio pour {lang}")

    # Appel traduire() avec contexte rolling + callbacks
    traductions = await traduire(
        texte_arabe=text,
        langues=langues_connectees,
        contexte_recent=list(contexte_recent),
        on_partial=on_partial_token,
        on_final=on_langue_finie,
    )

    if not traductions:
        logger.warning("[PIPELINE] Traduction vide")
        return

    # Mettre à jour le rolling buffer (maxlen=3 géré par deque)
    contexte_recent.append((text, traductions))

    # Monitoring
    historique.append({
        "heure": datetime.now(timezone.utc).isoformat(),
        "texte_arabe": text,
        "traductions": traductions,
        "langues_connectees": langues_connectees,
    })


# ============================================================
# WEBSOCKET FIDÈLES — /ws/listen
# ============================================================

@app.websocket("/ws/listen")
async def ws_listen(ws: WebSocket):
    """Le fidèle se connecte, envoie ses préférences, reçoit les traductions."""
    await ws.accept()
    client_id = str(id(ws))

    try:
        data = await ws.receive_json()
        langue = data.get("lang", "fr")
        voix = data.get("voice", "male")

        if langue not in LANGUES:
            langue = "fr"
        if voix not in ("male", "female"):
            voix = "male"

        clients[client_id] = {"ws": ws, "lang": langue, "voice": voix}
        logger.info(f"[CLIENT] +1 fidèle ({langue}) — total: {len(clients)}")

        # Envoyer le statut initial
        await ws.send_text(json.dumps({
            "type": "status",
            "mode": session["mode"],
            "session_active": session["active"],
        }))

        # Garder la connexion ouverte
        while True:
            msg = await ws.receive_text()
            # Protection : ignorer les messages trop longs (max 1 Ko)
            if len(msg) > 1024:
                continue
            try:
                update = json.loads(msg)
                if "lang" in update and update["lang"] in LANGUES:
                    clients[client_id]["lang"] = update["lang"]
                if "voice" in update and update["voice"] in ("male", "female"):
                    clients[client_id]["voice"] = update["voice"]
            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        pass
    finally:
        clients.pop(client_id, None)
        logger.info(f"[CLIENT] -1 fidèle — total: {len(clients)}")


# ============================================================
# WEBSOCKET AUDIO — /ws/audio-stream
# ============================================================

@app.websocket("/ws/audio-stream")
async def audio_stream_websocket(websocket: WebSocket, token: str = Query(default="")):
    """Reçoit l'audio du Raspberry Pi et le pousse dans Deepgram."""
    # Authentification : si AUDIO_STREAM_TOKEN est configuré, vérifier le token
    if AUDIO_STREAM_TOKEN and token != AUDIO_STREAM_TOKEN:
        await websocket.close(code=4003, reason="Token invalide")
        logger.warning("[WS AUDIO] Connexion refusée — token invalide")
        return

    await websocket.accept()
    logger.info("[WS AUDIO] Source audio connectée")

    stt = DeepgramSession(
        on_partial=on_partial_transcript,
        on_final=on_final_transcript,
    )

    # Phase 1 : Attendre le PREMIER frame audio (sans timeout — le micro peut mettre du temps à s'ouvrir)
    # Puis buffer quelques frames supplémentaires
    # Comme ça Deepgram reçoit de l'audio immédiatement après connexion (pas de timeout)
    try:
        first_frame = await websocket.receive_bytes()
    except WebSocketDisconnect:
        logger.info("[WS AUDIO] Source audio déconnectée avant le premier frame")
        return

    buffer = [first_frame]
    try:
        for _ in range(9):  # ~1 seconde de buffer (réduit de 30 pour gagner ~2s au démarrage)
            data = await asyncio.wait_for(websocket.receive_bytes(), timeout=0.15)
            if data and len(data) <= MAX_WS_MESSAGE_BYTES:
                buffer.append(data)
    except asyncio.TimeoutError:
        pass
    except WebSocketDisconnect:
        logger.info("[WS AUDIO] Source audio déconnectée pendant le buffer")
        return

    logger.info(f"[WS AUDIO] {len(buffer)} frames bufferises, connexion Deepgram...")

    # Phase 2 : Connecter Deepgram et envoyer le buffer d'un coup
    await stt.start(initial_frames=buffer)

    # Phase 3 : Continuer en temps réel
    try:
        while True:
            data = await websocket.receive_bytes()
            if len(data) > MAX_WS_MESSAGE_BYTES:
                continue
            if session["mode"] == "live":
                await stt.send_audio(data)
    except WebSocketDisconnect:
        logger.info("[WS AUDIO] Source audio déconnectée")
    except Exception as e:
        logger.error(f"[WS AUDIO] Erreur : {e}")
    finally:
        await stt.stop()


# ============================================================
# API ADMIN — Protégée par PIN
# ============================================================

def verifier_pin(request: Request):
    """Vérifie le header X-Admin-Pin."""
    pin = request.headers.get("X-Admin-Pin", "")
    if pin != ADMIN_PIN:
        raise HTTPException(status_code=403, detail="PIN incorrect")


@app.post("/api/admin/session/start")
async def admin_start_session(request: Request):
    """Démarre une session de traduction."""
    verifier_pin(request)
    session["active"] = True
    session["mode"] = "live"
    session["started_at"] = datetime.now(timezone.utc).isoformat()
    await broadcast_status("live")
    return {"status": "session démarrée", "session": session}


@app.post("/api/admin/session/stop")
async def admin_stop_session(request: Request):
    """Arrête la session de traduction."""
    verifier_pin(request)
    session["active"] = False
    session["mode"] = "live"
    session["started_at"] = None
    await broadcast_status("stopped")
    return {"status": "session arrêtée", "session": session}


@app.post("/api/admin/mode/{mode}")
async def admin_set_mode(mode: str, request: Request):
    """Change le mode : live, quran, adhan."""
    verifier_pin(request)
    if mode not in ("live", "quran", "adhan"):
        raise HTTPException(status_code=400, detail="Mode invalide (live/quran/adhan)")
    session["mode"] = mode
    await broadcast_status(mode)
    logger.info(f"[ADMIN] Mode changé → {mode}")
    return {"status": f"mode {mode} activé", "session": session}


@app.get("/api/status")
async def get_status():
    """Statut en temps réel du système."""
    return {
        "session": session,
        "clients_connectes": len(clients),
        "box_id": BOX_ID,
    }


@app.get("/api/admin/history")
async def admin_history(request: Request):
    """Historique des 50 dernières traductions."""
    verifier_pin(request)
    return {"historique": list(historique)}


@app.get("/api/health")
async def health_check():
    """Vérifie que le système est opérationnel."""
    return {
        "status": "ok",
        "mosque": MOSQUE_NAME,
        "box_id": BOX_ID,
        "apis": {
            "deepgram": bool(os.getenv("DEEPGRAM_API_KEY")),
            "openai": bool(os.getenv("OPENAI_API_KEY")),
            "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY")),
            "azure_tts": bool(os.getenv("AZURE_SPEECH_KEY")),
        },
    }


# ============================================================
# DÉMARRAGE
# ============================================================

async def _keepalive_gpt_loop():
    """Garde la connexion GPT chaude en envoyant un warmup toutes les ~30s d'inactivité."""
    while True:
        await asyncio.sleep(10)
        if not session["active"]:
            continue
        depuis_dernier = time.time() - gpt_translator._last_gpt_call
        if depuis_dernier > 30:
            logger.debug("[KEEPALIVE] Connexion GPT inactive >30s — warmup...")
            await warmup()


@app.on_event("startup")
async def on_startup():
    """Initialise tous les modules au démarrage."""
    # Initialiser les modules
    init_tts()
    init_translator()

    # Charger le glossaire
    glossaire_path = Path(__file__).parent / "glossary.json"
    if glossaire_path.exists():
        charger_glossaire(str(glossaire_path))
    else:
        logger.warning("glossary.json introuvable — traduction sans glossaire")

    # Préchauffer la connexion GPT (évite le cold start de ~5s sur la 1ère phrase)
    await warmup()

    # Lancer la boucle keepalive GPT en tâche de fond
    asyncio.create_task(_keepalive_gpt_loop())

    logger.info(f"KhutbaBox v3 démarré — {MOSQUE_NAME}")
    logger.info(f"Langues : {', '.join(LANGUES.keys())} ({len(LANGUES)} langues)")
    logger.info("En attente de la voix de l'imam...")


# Servir le frontend
_backend_dir = os.path.dirname(os.path.abspath(__file__))
_candidate1 = os.path.join(_backend_dir, "..", "frontend")
_candidate2 = os.path.join(_backend_dir, "frontend")
FRONTEND_DIR = None
for _path in [_candidate1, _candidate2]:
    if os.path.isdir(_path) and os.listdir(_path):
        FRONTEND_DIR = _path
        break

if FRONTEND_DIR:
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    logger.warning("Dossier frontend introuvable")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
