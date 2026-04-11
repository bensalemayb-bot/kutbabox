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
