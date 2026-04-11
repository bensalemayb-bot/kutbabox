"""
KhutbaBox — Deepgram Nova-3 STT streaming
Transcription arabe en temps réel via WebSocket.
Compatible avec deepgram-sdk v6.x
"""

import os
import json
import logging
import asyncio
from typing import Callable, Awaitable

from deepgram import AsyncDeepgramClient, ListenV1Response
from deepgram.core.events import EventType

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
        self._socket = None
        self._client = None
        self._listening = False

    async def start(self):
        """Ouvre la connexion WebSocket vers Deepgram Nova-3."""
        os.environ["DEEPGRAM_API_KEY"] = DEEPGRAM_API_KEY
        self._client = AsyncDeepgramClient()

        # Ouvrir la connexion avec les paramètres
        self._socket = self._client.listen.v1.connect(
            model="nova-3",
            language="ar",
            encoding="linear16",
            sample_rate=16000,
            interim_results=True,
            utterance_end_ms=1500,
            endpointing=300,
            smart_format=True,
        )

        # Enregistrer les callbacks
        self._socket.on(EventType.MESSAGE, self._on_message)
        self._socket.on(EventType.ERROR, self._on_error)

        # Démarrer l'écoute
        await self._socket.start_listening()
        self._listening = True
        logger.info("[DEEPGRAM] Connexion streaming ouverte (Nova-3, arabe)")

    async def push_audio(self, audio_data: bytes):
        """Envoie des bytes audio PCM 16kHz 16-bit mono à Deepgram."""
        if self._socket and self._listening:
            self._socket.send_media(audio_data)

    async def stop(self):
        """Ferme proprement la connexion Deepgram."""
        if self._socket and self._listening:
            try:
                self._socket.send_finalize()
                self._socket.send_close_stream()
            except Exception:
                pass
            self._listening = False
            self._socket = None
            logger.info("[DEEPGRAM] Connexion fermée")

    def _on_message(self, message):
        """Callback Deepgram : reçoit les résultats de transcription."""
        try:
            # Le message peut être un dict ou un objet Pydantic
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
                # Lancer la coroutine dans la boucle asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self._on_final(transcript))
                else:
                    loop.run_until_complete(self._on_final(transcript))
            else:
                logger.debug(f'[DEEPGRAM PARTIEL] "{transcript[:80]}"')
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self._on_partial(transcript))

        except Exception as e:
            logger.error(f"[DEEPGRAM] Erreur callback : {e}")

    def _on_error(self, error):
        """Callback Deepgram : erreur."""
        logger.error(f"[DEEPGRAM] Erreur : {error}")
