"""
KhutbaBox — Deepgram Nova-3 STT streaming
Transcription arabe en temps réel via WebSocket.
Compatible avec deepgram-sdk v6.x

Approche directe : pas de queue, l'audio est envoyé directement à Deepgram
depuis le handler WebSocket via une référence partagée au socket.
"""

import os
import json
import logging
import asyncio
from typing import Callable, Awaitable

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

logger = logging.getLogger("khutbabox.stt")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")


class DeepgramSession:
    """
    Gère une session Deepgram Nova-3.
    L'audio est envoyé directement via send_audio(), pas via une queue.
    """

    def __init__(
        self,
        on_partial: Callable[[str], Awaitable[None]],
        on_final: Callable[[str], Awaitable[None]],
    ):
        self._on_partial = on_partial
        self._on_final = on_final
        self._socket = None
        self._client = None
        self._context = None
        self._ready = False
        self._nb_frames = 0

    async def start(self):
        """Ouvre la connexion Deepgram et la garde ouverte."""
        os.environ["DEEPGRAM_API_KEY"] = DEEPGRAM_API_KEY
        self._client = AsyncDeepgramClient()

        # Ouvrir la connexion
        self._context = self._client.listen.v1.connect(
            model="nova-3-general",
            language="ar",
            encoding="linear16",
            sample_rate=16000,
            interim_results="true",
            utterance_end_ms="1500",
            endpointing=300,
            smart_format="true",
        )
        self._socket = await self._context.__aenter__()

        # Callbacks
        self._socket.on(EventType.MESSAGE, self._on_message)
        self._socket.on(EventType.ERROR, self._on_error)

        # Envoyer du silence pour initialiser la connexion
        silent = b'\x00' * 3200
        for _ in range(10):
            await self._socket.send_media(silent)
            await asyncio.sleep(0.01)

        self._ready = True
        logger.info("[DEEPGRAM] Connexion streaming ouverte (Nova-3, arabe)")

    async def send_audio(self, audio_data: bytes):
        """Envoie un frame audio directement à Deepgram."""
        if not self._ready or not self._socket:
            return
        try:
            await self._socket.send_media(audio_data)
            self._nb_frames += 1
            if self._nb_frames == 1:
                logger.info(f"[DEEPGRAM] Premier frame audio envoye — {len(audio_data)} bytes")
            elif self._nb_frames % 500 == 0:
                logger.info(f"[DEEPGRAM] {self._nb_frames} frames envoyees")
        except Exception as e:
            logger.error(f"[DEEPGRAM] Erreur envoi : {e}")
            self._ready = False

    async def stop(self):
        """Ferme la connexion Deepgram."""
        self._ready = False
        if self._socket and self._context:
            try:
                await self._socket.send_finalize()
            except Exception:
                pass
            try:
                await self._context.__aexit__(None, None, None)
            except Exception:
                pass
            self._socket = None
            self._context = None
            logger.info("[DEEPGRAM] Session terminée")

    def _on_message(self, message):
        """Callback Deepgram : résultats de transcription."""
        try:
            if isinstance(message, dict):
                data = message
            elif hasattr(message, "model_dump"):
                data = message.model_dump()
            else:
                data = json.loads(str(message))

            results = data.get("results")
            if not results:
                return

            channels = results.get("channels", [])
            if not channels:
                return

            alternatives = channels[0].get("alternatives", [])
            if not alternatives:
                return

            transcript = alternatives[0].get("transcript", "")
            if not transcript:
                return

            is_final = results.get("is_final", False)

            if is_final:
                logger.info(f'[DEEPGRAM FINAL] "{transcript[:80]}"')
                asyncio.ensure_future(self._on_final(transcript))
            else:
                logger.debug(f'[DEEPGRAM PARTIEL] "{transcript[:80]}"')
                asyncio.ensure_future(self._on_partial(transcript))

        except Exception as e:
            logger.error(f"[DEEPGRAM] Erreur callback : {e}")

    def _on_error(self, error):
        """Callback Deepgram : erreur."""
        logger.error(f"[DEEPGRAM] Erreur : {error}")
