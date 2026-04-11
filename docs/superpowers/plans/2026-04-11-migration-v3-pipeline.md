# Migration v3 — Pipeline Ultra-Rapide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le pipeline Azure Speech Translation (lent, 3-5s) par Deepgram Nova-3 + GPT-4.1 mini + TTS hybride parallélisé, pour atteindre une latence < 2 secondes.

**Architecture:** Le backend est découpé en 4 fichiers Python (main.py, deepgram_stt.py, gpt_translator.py, tts_engine.py). L'audio arrive du Raspberry Pi via WebSocket, passe par Deepgram pour la transcription arabe, GPT-4.1 mini pour la traduction avec glossaire scanné, et TTS hybride (ElevenLabs + Azure Neural) en parallèle. Le frontend reste inchangé.

**Tech Stack:** Python/FastAPI, Deepgram SDK v3+, OpenAI SDK (GPT-4.1 mini), ElevenLabs API, Azure Speech SDK, WebSocket

---

## File Structure

```
backend/
  main.py              ← Point d'entrée FastAPI, routes, WebSocket, état global
  deepgram_stt.py      ← Connexion Deepgram Nova-3, streaming STT arabe
  gpt_translator.py    ← Traduction GPT-4.1 mini + scan glossaire
  tts_engine.py        ← Routeur TTS (ElevenLabs Flash + Azure Neural)
  glossary.json        ← Déjà existant (408 termes, 8 langues)
  requirements.txt     ← Mise à jour dépendances
  Dockerfile           ← Mise à jour

frontend/
  index.html           ← Ajout message status Coran/Adhan (petit changement)
  admin.html           ← Ajout boutons Coran/Adhan

scripts/
  audio_capture.py     ← Inchangé
```

---

### Task 1: Créer le module TTS (tts_engine.py)

**Files:**
- Create: `backend/tts_engine.py`

Ce fichier reprend la logique TTS existante de main.py (ElevenLabs Flash v2.5 + Azure Neural TTS) et l'isole dans son propre module.

- [ ] **Step 1: Créer backend/tts_engine.py avec ElevenLabs Flash v2.5**

```python
"""
KhutbaBox — Moteur TTS hybride
ElevenLabs Flash v2.5 (fr, en, es, pt, tr) + Azure Neural TTS (ur, bs, sq)
"""

import os
import time
import asyncio
import logging

import httpx
import azure.cognitiveservices.speech as speechsdk

logger = logging.getLogger("khutbabox.tts")

# ── Configuration ──

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "westeurope")

ELEVENLABS_MODEL = "eleven_flash_v2_5"

# Voix ElevenLabs par défaut (Adam — multilingue)
ELEVENLABS_VOICE_MALE = os.getenv("ELEVENLABS_VOICE_MALE", "pNInz6obpgDQGcFmaJgB")
ELEVENLABS_VOICE_FEMALE = os.getenv("ELEVENLABS_VOICE_FEMALE", "EXAVITQu4vr4xnSDxMaL")

# Voix Azure Neural TTS
AZURE_TTS_VOICES_MALE = {
    "ur": "ur-PK-AsadNeural",
    "bs": "bs-BA-GoranNeural",
    "sq": "sq-AL-IlirNeural",
}
AZURE_TTS_VOICES_FEMALE = {
    "ur": "ur-PK-UzmaNeural",
    "bs": "bs-BA-VesnaNeural",
    "sq": "sq-AL-AnilaNeural",
}

# Langues → provider
TTS_PROVIDERS = {
    "fr": "elevenlabs",
    "en": "elevenlabs",
    "es": "elevenlabs",
    "pt": "elevenlabs",
    "tr": "elevenlabs",
    "ur": "azure",
    "bs": "azure",
    "sq": "azure",
}

# Sémaphore ElevenLabs : max 2 appels simultanés (évite erreur 429)
_elevenlabs_semaphore: asyncio.Semaphore | None = None


def init_tts():
    """Initialise le sémaphore. Appeler une fois au démarrage de FastAPI."""
    global _elevenlabs_semaphore
    _elevenlabs_semaphore = asyncio.Semaphore(2)


async def elevenlabs_tts(text: str, lang: str, voice: str = "male") -> bytes | None:
    """
    Génère de l'audio via ElevenLabs Flash v2.5 streaming.
    Retourne les bytes MP3 ou None si erreur.
    """
    voice_id = ELEVENLABS_VOICE_MALE if voice == "male" else ELEVENLABS_VOICE_FEMALE
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

    debut = time.time()
    try:
        async with _elevenlabs_semaphore, httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers={
                    "xi-api-key": ELEVENLABS_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": ELEVENLABS_MODEL,
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                },
                params={"output_format": "mp3_44100_128"},
                timeout=30.0,
            )
            response.raise_for_status()
            audio = response.content
            latence = int((time.time() - debut) * 1000)
            logger.info(f"[TTS ELEVENLABS] {lang} — {len(audio)} bytes, {latence}ms")
            return audio
    except Exception as e:
        logger.error(f"[TTS ELEVENLABS] Erreur {lang} : {e}")
        return None


async def azure_tts(text: str, lang: str, voice: str = "male") -> bytes | None:
    """
    Génère de l'audio via Azure Neural TTS.
    Retourne les bytes WAV ou None si erreur.
    """
    if not AZURE_SPEECH_KEY:
        logger.warning(f"[TTS AZURE] Pas de clé Azure configurée")
        return None

    voices = AZURE_TTS_VOICES_MALE if voice == "male" else AZURE_TTS_VOICES_FEMALE
    voice_name = voices.get(lang)
    if not voice_name:
        logger.error(f"[TTS AZURE] Pas de voix configurée pour {lang}")
        return None

    debut = time.time()
    try:
        def _synthesize():
            speech_config = speechsdk.SpeechConfig(
                subscription=AZURE_SPEECH_KEY,
                region=AZURE_SPEECH_REGION,
            )
            speech_config.speech_synthesis_voice_name = voice_name
            synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=speech_config,
                audio_config=None,
            )
            result = synthesizer.speak_text(text)
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                return result.audio_data
            elif result.reason == speechsdk.ResultReason.Canceled:
                details = result.cancellation_details
                logger.error(f"[TTS AZURE] Annulé : {details.reason} — {details.error_details}")
            return None

        audio = await asyncio.to_thread(_synthesize)
        if audio:
            latence = int((time.time() - debut) * 1000)
            logger.info(f"[TTS AZURE] {lang} ({voice_name}) — {len(audio)} bytes, {latence}ms")
        return audio
    except Exception as e:
        logger.error(f"[TTS AZURE] Erreur {lang} : {e}")
        return None


async def generer_tts(text: str, lang: str, voice: str = "male") -> tuple[bytes | None, str]:
    """
    Route vers le bon provider TTS selon la langue.
    Retourne (audio_bytes, format) ou (None, "").
    format = "mp3" pour ElevenLabs, "wav" pour Azure.
    """
    provider = TTS_PROVIDERS.get(lang, "elevenlabs")

    if provider == "azure":
        audio = await azure_tts(text, lang, voice)
        return (audio, "wav") if audio else (None, "")
    else:
        audio = await elevenlabs_tts(text, lang, voice)
        return (audio, "mp3") if audio else (None, "")
```

- [ ] **Step 2: Vérifier que le fichier est syntaxiquement correct**

Run: `cd /c/Users/bensa/Desktop/khutbabox && python -c "import ast; ast.parse(open('backend/tts_engine.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/tts_engine.py
git commit -m "feat: extract TTS engine to dedicated module (ElevenLabs + Azure)"
```

---

### Task 2: Créer le module traduction (gpt_translator.py)

**Files:**
- Create: `backend/gpt_translator.py`
- Read: `backend/glossary.json` (existant)

Ce fichier gère la traduction via GPT-4.1 mini avec scan du glossaire islamique.

- [ ] **Step 1: Créer backend/gpt_translator.py**

```python
"""
KhutbaBox — Traduction GPT-4.1 mini
Traduit l'arabe vers les langues demandées en un seul appel.
Scanne le glossaire islamique et injecte seulement les termes détectés.
"""

import os
import json
import time
import logging

from openai import AsyncOpenAI

logger = logging.getLogger("khutbabox.translator")

# ── Configuration ──

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GPT_MODEL = "gpt-4.1-mini"

# Noms complets des langues (pour le prompt GPT)
LANGUES = {
    "fr": "français",
    "en": "anglais",
    "es": "espagnol",
    "pt": "portugais",
    "tr": "turc",
    "ur": "ourdou",
    "bs": "bosnien",
    "sq": "albanais",
}

# ── Client OpenAI ──

_client: AsyncOpenAI | None = None


def init_translator():
    """Initialise le client OpenAI. Appeler une fois au démarrage."""
    global _client
    _client = AsyncOpenAI(api_key=OPENAI_API_KEY)


# ── Glossaire ──

_glossaire: dict = {}
_termes_arabes: list[str] = []


def charger_glossaire(chemin: str):
    """
    Charge le glossaire JSON et extrait les termes arabes pour la recherche.
    Appeler une fois au démarrage.
    """
    global _glossaire, _termes_arabes

    with open(chemin, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Parcourir toutes les catégories du glossaire
    for categorie, termes in data.items():
        if categorie.startswith("_"):
            continue  # Skip metadata
        if not isinstance(termes, dict):
            continue
        for terme_arabe, traductions in termes.items():
            if terme_arabe.startswith("_"):
                continue  # Skip _description
            if isinstance(traductions, dict):
                _glossaire[terme_arabe] = traductions

    # Trier par longueur décroissante pour matcher les expressions longues d'abord
    _termes_arabes = sorted(_glossaire.keys(), key=len, reverse=True)
    logger.info(f"Glossaire chargé : {len(_glossaire)} termes")


def scanner_glossaire(texte_arabe: str) -> dict[str, dict]:
    """
    Scanne le texte arabe et retourne les termes du glossaire détectés.
    Retourne: {terme_arabe: {transliteration, fr, en, ...}}
    """
    termes_trouves = {}
    for terme in _termes_arabes:
        if terme in texte_arabe:
            termes_trouves[terme] = _glossaire[terme]
    return termes_trouves


async def traduire(texte_arabe: str, langues: list[str]) -> dict[str, str]:
    """
    Traduit le texte arabe vers les langues demandées via GPT-4.1 mini.
    Injecte les termes du glossaire détectés dans la phrase.
    
    Retourne: {"fr": "traduction...", "en": "translation...", ...}
    """
    if not langues:
        return {}

    debut = time.time()

    # Scanner le glossaire pour les termes présents dans la phrase
    termes_detectes = scanner_glossaire(texte_arabe)

    # Construire la section glossaire du prompt
    glossaire_prompt = ""
    if termes_detectes:
        lignes = []
        for arabe, trads in termes_detectes.items():
            transliteration = trads.get("transliteration", "")
            exemples = ", ".join(
                f'{code}: "{trads[code]}"'
                for code in langues
                if code in trads
            )
            lignes.append(f'  - {arabe} ({transliteration}) → {exemples}')
        glossaire_prompt = (
            "\n\nTERMES ISLAMIQUES OBLIGATOIRES — utilise EXACTEMENT ces traductions :\n"
            + "\n".join(lignes)
        )

    # Construire les noms de langues demandées
    noms_langues = ", ".join(
        f"{code} ({LANGUES.get(code, code)})"
        for code in langues
    )

    system_prompt = (
        "Tu es un traducteur professionnel spécialisé dans les sermons islamiques (khoutba). "
        "Traduis le texte arabe dans les langues demandées. "
        "Utilise un registre solennel et respectueux. "
        "Aucune traduction créative ni interprétation libre. "
        "Réponds UNIQUEMENT en JSON avec les codes de langue comme clés."
        f"{glossaire_prompt}"
    )

    user_prompt = (
        f"Langues : {noms_langues}\n\n"
        f"Texte arabe : «{texte_arabe}»"
    )

    try:
        response = await _client.chat.completions.create(
            model=GPT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=2000,
        )

        result = json.loads(response.choices[0].message.content)
        latence = int((time.time() - debut) * 1000)

        # Filtrer pour ne garder que les langues demandées
        traductions = {code: result[code] for code in langues if code in result}

        nb_termes = len(termes_detectes)
        logger.info(
            f"[GPT] {len(traductions)} langues, {nb_termes} termes glossaire, {latence}ms"
        )
        return traductions

    except Exception as e:
        logger.error(f"[GPT] Erreur traduction : {e}")
        return {}
```

- [ ] **Step 2: Vérifier la syntaxe**

Run: `cd /c/Users/bensa/Desktop/khutbabox && python -c "import ast; ast.parse(open('backend/gpt_translator.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/gpt_translator.py
git commit -m "feat: add GPT-4.1 mini translator with glossary scanning"
```

---

### Task 3: Créer le module Deepgram STT (deepgram_stt.py)

**Files:**
- Create: `backend/deepgram_stt.py`

Ce fichier gère la connexion streaming à Deepgram Nova-3 pour la transcription arabe.

- [ ] **Step 1: Créer backend/deepgram_stt.py**

```python
"""
KhutbaBox — Deepgram Nova-3 STT streaming
Transcription arabe en temps réel via WebSocket.
"""

import os
import logging
import asyncio
from typing import Callable, Awaitable

from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents

logger = logging.getLogger("khutbabox.stt")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")


class DeepgramSTT:
    """
    Gère une session de transcription streaming avec Deepgram Nova-3.
    
    Usage:
        stt = DeepgramSTT(on_partial=..., on_final=...)
        await stt.start()
        await stt.push_audio(pcm_bytes)
        await stt.stop()
    """

    def __init__(
        self,
        on_partial: Callable[[str], Awaitable[None]],
        on_final: Callable[[str], Awaitable[None]],
    ):
        """
        on_partial: appelé avec le texte partiel (intérimaire)
        on_final: appelé avec la phrase complète (finale)
        """
        self._on_partial = on_partial
        self._on_final = on_final
        self._connection = None
        self._client = None

    async def start(self):
        """Ouvre la connexion WebSocket vers Deepgram Nova-3."""
        self._client = DeepgramClient(DEEPGRAM_API_KEY)
        self._connection = self._client.listen.asyncwebsocket.v("1")

        # Enregistrer les callbacks
        self._connection.on(
            LiveTranscriptionEvents.Transcript, self._on_transcript
        )
        self._connection.on(
            LiveTranscriptionEvents.Error, self._on_error
        )

        options = LiveOptions(
            model="nova-3",
            language="ar",
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            interim_results=True,
            utterance_end_ms=1500,
            endpointing=300,
            smart_format=True,
        )

        started = await self._connection.start(options)
        if started:
            logger.info("[DEEPGRAM] Connexion streaming ouverte (Nova-3, arabe)")
        else:
            logger.error("[DEEPGRAM] Impossible d'ouvrir la connexion")

    async def push_audio(self, audio_data: bytes):
        """Envoie des bytes audio PCM 16kHz 16-bit mono à Deepgram."""
        if self._connection:
            await self._connection.send(audio_data)

    async def stop(self):
        """Ferme proprement la connexion Deepgram."""
        if self._connection:
            await self._connection.finish()
            self._connection = None
            logger.info("[DEEPGRAM] Connexion fermée")

    async def _on_transcript(self, _self, result, **kwargs):
        """Callback Deepgram : reçoit les résultats de transcription."""
        try:
            alternative = result.channel.alternatives[0]
            transcript = alternative.transcript

            if not transcript:
                return

            if result.is_final:
                logger.info(f'[DEEPGRAM FINAL] "{transcript[:80]}"')
                await self._on_final(transcript)
            else:
                logger.debug(f'[DEEPGRAM PARTIEL] "{transcript[:80]}"')
                await self._on_partial(transcript)

        except Exception as e:
            logger.error(f"[DEEPGRAM] Erreur callback : {e}")

    async def _on_error(self, _self, error, **kwargs):
        """Callback Deepgram : erreur."""
        logger.error(f"[DEEPGRAM] Erreur : {error}")
```

- [ ] **Step 2: Vérifier la syntaxe**

Run: `cd /c/Users/bensa/Desktop/khutbabox && python -c "import ast; ast.parse(open('backend/deepgram_stt.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/deepgram_stt.py
git commit -m "feat: add Deepgram Nova-3 streaming STT module for Arabic"
```

---

### Task 4: Réécrire main.py (orchestrateur)

**Files:**
- Rewrite: `backend/main.py`

Le nouveau main.py utilise les 3 modules (deepgram_stt, gpt_translator, tts_engine) et orchestre le tout. Il garde les routes WebSocket, l'API admin, et ajoute la gestion Coran/Adhan.

- [ ] **Step 1: Réécrire backend/main.py**

```python
"""
KhutbaBox — Backend principal v3
Orchestre : Deepgram STT → GPT-4.1 mini → TTS hybride → WebSocket fidèles
"""

import os
import json
import base64
import asyncio
import logging
from datetime import datetime, timezone
from collections import deque
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File
from fastapi.staticfiles import StaticFiles
import uvicorn

# Modules KhutbaBox
from deepgram_stt import DeepgramSTT
from gpt_translator import init_translator, charger_glossaire, traduire
from tts_engine import init_tts, generer_tts

# ── Logger ──
logger = logging.getLogger("khutbabox")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

# ── Configuration ──
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

ADMIN_PIN = os.getenv("ADMIN_PIN", "0000")
MOSQUE_NAME = os.getenv("MOSQUE_NAME", "Mosquée")
BOX_ID = os.getenv("BOX_ID", "khutbabox-001")

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
    → Traduit via GPT-4.1 mini
    → Envoie texte + TTS aux fidèles.
    """
    if session["mode"] != "live":
        return  # Coran ou Adhan en cours — pas de traduction

    # Langues où au moins 1 fidèle est connecté
    langues_connectees = list({info["lang"] for info in clients.values()})
    if not langues_connectees:
        logger.debug("[PIPELINE] Aucun fidèle connecté — pas de traduction")
        return

    # Étape 1 : Traduire via GPT-4.1 mini
    traductions = await traduire(text, langues_connectees)
    if not traductions:
        logger.warning("[PIPELINE] Traduction vide")
        return

    # Étape 2 : Envoyer le texte final immédiatement + lancer TTS en parallèle
    async def traiter_langue(lang: str, texte_traduit: str):
        # Envoyer le texte tout de suite (le fidèle voit le sous-titre)
        await broadcast_translation("final_text", lang, text=texte_traduit)

        # Trouver la voix préférée pour cette langue (prendre le 1er client trouvé)
        voix = "male"
        for info in clients.values():
            if info["lang"] == lang:
                voix = info.get("voice", "male")
                break

        # Générer le TTS
        audio, fmt = await generer_tts(texte_traduit, lang, voix)
        if audio:
            audio_b64 = base64.b64encode(audio).decode()
            await broadcast_translation("audio_chunk", lang, audio_b64=audio_b64, audio_format=fmt)
        else:
            logger.warning(f"[TTS] Pas d'audio pour {lang}")

    # Lancer TTS en parallèle pour toutes les langues
    taches = [
        traiter_langue(lang, texte)
        for lang, texte in traductions.items()
    ]
    await asyncio.gather(*taches)

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
async def audio_stream_websocket(websocket: WebSocket):
    """Reçoit l'audio du Raspberry Pi et le pousse dans Deepgram."""
    await websocket.accept()
    logger.info("[WS AUDIO] Source audio connectée")

    stt = DeepgramSTT(
        on_partial=on_partial_transcript,
        on_final=on_final_transcript,
    )
    await stt.start()

    try:
        while True:
            data = await websocket.receive_bytes()
            if session["mode"] == "live":
                await stt.push_audio(data)
            # En mode quran/adhan, on reçoit l'audio mais on ne le transcrit pas
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
```

- [ ] **Step 2: Vérifier la syntaxe**

Run: `cd /c/Users/bensa/Desktop/khutbabox && python -c "import ast; ast.parse(open('backend/main.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: rewrite main.py as orchestrator using modular pipeline v3"
```

---

### Task 5: Mettre à jour requirements.txt

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Réécrire backend/requirements.txt**

```
# KhutbaBox v3 — dépendances Python
fastapi>=0.100.0
uvicorn>=0.23.0
python-dotenv>=1.0.0
httpx>=0.24.0

# STT — Deepgram Nova-3
deepgram-sdk>=3.0.0

# Traduction — GPT-4.1 mini
openai>=1.0.0

# TTS — Azure Neural (ur, bs, sq)
azure-cognitiveservices-speech>=1.40.0

# Audio capture (Windows/Raspberry Pi uniquement, pas dans Docker)
# sounddevice>=0.4.6
# numpy>=1.24.0
# websockets>=12.0
```

- [ ] **Step 2: Commit**

```bash
git add backend/requirements.txt
git commit -m "chore: update requirements.txt for v3 pipeline"
```

---

### Task 6: Ajouter boutons Coran/Adhan dans admin.html

**Files:**
- Modify: `frontend/admin.html`

- [ ] **Step 1: Ajouter les boutons Coran et Adhan dans le dashboard admin**

Ajouter dans la section de contrôle de session de admin.html, après les boutons Start/Stop existants :

```html
<!-- Boutons Coran / Adhan -->
<div style="display: flex; gap: 10px; margin-top: 16px;">
  <button id="btn-quran" onclick="setMode('quran')" 
    style="flex:1; padding:14px; background:#065f46; color:#fff; border:none; border-radius:10px; font-size:16px; font-weight:600; cursor:pointer;">
    Coran
  </button>
  <button id="btn-adhan" onclick="setMode('adhan')"
    style="flex:1; padding:14px; background:#1e3a5f; color:#fff; border:none; border-radius:10px; font-size:16px; font-weight:600; cursor:pointer;">
    Adhan
  </button>
  <button id="btn-live" onclick="setMode('live')"
    style="flex:1; padding:14px; background:#c9a84c; color:#06101d; border:none; border-radius:10px; font-size:16px; font-weight:600; cursor:pointer;">
    Reprendre
  </button>
</div>
```

Ajouter le JavaScript correspondant :

```javascript
async function setMode(mode) {
  try {
    const res = await fetch(`/api/admin/mode/${mode}`, {
      method: 'POST',
      headers: { 'X-Admin-Pin': pin },
    });
    const data = await res.json();
    if (res.ok) {
      updateModeButtons(mode);
    } else {
      alert(data.detail || 'Erreur');
    }
  } catch (e) {
    alert('Erreur réseau');
  }
}

function updateModeButtons(mode) {
  document.getElementById('btn-quran').style.opacity = mode === 'quran' ? '1' : '0.5';
  document.getElementById('btn-adhan').style.opacity = mode === 'adhan' ? '1' : '0.5';
  document.getElementById('btn-live').style.opacity = mode === 'live' ? '1' : '0.5';
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/admin.html
git commit -m "feat: add Quran/Adhan pause buttons to admin dashboard"
```

---

### Task 7: Ajouter message status Coran/Adhan dans index.html

**Files:**
- Modify: `frontend/index.html`

- [ ] **Step 1: Ajouter la gestion du message status dans le handler WebSocket**

Dans `frontend/index.html`, dans le `ws.onmessage`, ajouter un cas dans le switch pour le mode status :

```javascript
case 'status':
  if (msg.mode === 'quran') {
    afficherMessageSpecial('Récitation du Saint Coran en cours...');
  } else if (msg.mode === 'adhan') {
    afficherMessageSpecial("Appel à la prière (Adhan) en cours...");
  } else if (msg.mode === 'live') {
    masquerMessageSpecial();
  }
  break;
```

Ajouter la fonction `afficherMessageSpecial` et le HTML correspondant :

```html
<!-- Ajouter dans le div ecran-live, avant zone-traduction -->
<div class="message-special cache" id="message-special"
  style="text-align:center; padding:30px 20px; margin-bottom:20px;
         background:rgba(201,168,76,0.1); border:1px solid rgba(201,168,76,0.3);
         border-radius:14px;">
  <div style="font-size:32px; margin-bottom:12px;">🕌</div>
  <div id="message-special-texte" 
    style="font-size:18px; color:#c9a84c; font-weight:500;"></div>
</div>
```

```javascript
function afficherMessageSpecial(texte) {
  const el = document.getElementById('message-special');
  document.getElementById('message-special-texte').textContent = texte;
  el.classList.remove('cache');
  document.getElementById('zone-traduction').classList.add('cache');
}

function masquerMessageSpecial() {
  document.getElementById('message-special').classList.add('cache');
  document.getElementById('zone-traduction').classList.remove('cache');
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/index.html
git commit -m "feat: display Quran/Adhan status messages on listener page"
```

---

### Task 8: Mettre à jour le Dockerfile

**Files:**
- Modify: `backend/Dockerfile`

- [ ] **Step 1: Mettre à jour le Dockerfile pour v3**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Dépendances système pour Azure Speech SDK
RUN apt-get update && apt-get install -y \
    libssl-dev \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Commit**

```bash
git add backend/Dockerfile
git commit -m "chore: update Dockerfile for v3 dependencies"
```

---

### Task 9: Mettre à jour .env.example

**Files:**
- Create: `.env.example`

- [ ] **Step 1: Créer .env.example avec les nouvelles clés**

```bash
# KhutbaBox v3 — Configuration
# Copier ce fichier en .env et remplir les valeurs

# Deepgram Nova-3 — STT arabe streaming
DEEPGRAM_API_KEY=

# OpenAI — GPT-4.1 mini traduction
OPENAI_API_KEY=

# ElevenLabs — TTS 5 langues (fr, en, es, pt, tr)
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_MALE=pNInz6obpgDQGcFmaJgB
ELEVENLABS_VOICE_FEMALE=EXAVITQu4vr4xnSDxMaL

# Azure — TTS 3 langues uniquement (ur, bs, sq)
AZURE_SPEECH_KEY=
AZURE_SPEECH_REGION=westeurope

# Admin
ADMIN_PIN=0000
MOSQUE_NAME=Mosquée
BOX_ID=khutbabox-001
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "chore: add .env.example for v3 configuration"
```

---

### Task 10: Audit de sécurité

**Files:**
- Review: tous les fichiers backend et frontend

Cet audit vérifie les failles de sécurité potentielles dans tout le code v3.

- [ ] **Step 1: Vérifier les points de sécurité suivants**

1. **Clés API** — Vérifier qu'aucune clé n'est en dur dans le code (tout via .env)
2. **Injection WebSocket** — Vérifier que les messages JSON reçus des clients sont validés
3. **PIN admin** — Vérifier que toutes les routes admin vérifient le PIN
4. **CORS** — Vérifier que FastAPI n'a pas de wildcard CORS en production
5. **Input validation** — Vérifier que les langues/voix sont validées contre une liste blanche
6. **Rate limiting** — Vérifier le sémaphore ElevenLabs et les protections contre l'abus
7. **.env dans .gitignore** — Vérifier que les secrets ne sont pas commités
8. **Dépendances** — Vérifier qu'il n'y a pas de dépendances connues vulnérables
9. **WebSocket authentification** — Le flux audio /ws/audio-stream est-il protégé ?
10. **XSS** — Vérifier que le frontend n'injecte pas de HTML non-sanitisé

- [ ] **Step 2: Appliquer les corrections de sécurité identifiées**

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "security: fix vulnerabilities found in v3 audit"
```

---

### Verification

Après toutes les tâches :

1. `docker compose up --build` — vérifier que le backend démarre sans erreur
2. Ouvrir `http://localhost` — vérifier que la page fidèles s'affiche
3. Ouvrir `http://localhost/admin.html` — vérifier les boutons Coran/Adhan
4. `py scripts/audio_capture.py` — vérifier la connexion WebSocket
5. Parler en arabe → vérifier texte + audio sur le navigateur
6. Tester bouton Coran → vérifier message sur la page fidèles
7. Tester bouton Reprendre → vérifier reprise traduction
8. Vérifier `docker compose logs -f` : pas d'erreurs, latence < 2s
