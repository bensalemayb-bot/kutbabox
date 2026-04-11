"""
KhutbaBox — Deepgram Nova-3 STT streaming
Transcription arabe en temps réel via WebSocket brut (sans SDK).
Plus fiable que le SDK v6 avec FastAPI (pas de bugs de handshake).
"""

import os
import json
import logging
import asyncio
from typing import Callable, Awaitable

import websockets

logger = logging.getLogger("khutbabox.stt")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")

DEEPGRAM_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?model=nova-3-general"
    "&language=ar"
    "&encoding=linear16"
    "&sample_rate=16000"
    "&interim_results=true"
    "&utterance_end_ms=1500"
    "&endpointing=300"
    "&smart_format=true"
)


class DeepgramSession:
    """
    Gère une session Deepgram Nova-3 via WebSocket brut.
    Plus fiable que le SDK v6 avec FastAPI.

    Usage:
        stt = DeepgramSession(on_partial=..., on_final=...)
        await stt.start()
        await stt.send_audio(pcm_bytes)
        await stt.stop()
    """

    def __init__(
        self,
        on_partial: Callable[[str], Awaitable[None]],
        on_final: Callable[[str], Awaitable[None]],
    ):
        self._on_partial = on_partial
        self._on_final = on_final
        self._ws = None
        self._receiver_task = None
        self._nb_frames = 0

    async def start(self, initial_frames: list[bytes] = None):
        """Ouvre la connexion Deepgram et envoie immédiatement les frames bufferisés."""
        headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

        self._ws = await websockets.connect(DEEPGRAM_URL, additional_headers=headers)

        # Envoyer les frames bufferisés IMMÉDIATEMENT après connexion
        if initial_frames:
            for frame in initial_frames:
                await self._ws.send(frame)
            logger.info(f"[DEEPGRAM] Connecté + {len(initial_frames)} frames envoyés immédiatement")

        # Lancer la réception des résultats en tâche de fond
        self._receiver_task = asyncio.create_task(self._receive_loop())
        logger.info("[DEEPGRAM] Connexion streaming ouverte (Nova-3, arabe)")

    @property
    def is_connected(self):
        if self._ws is None:
            return False
        try:
            return self._ws.state.name == "OPEN"
        except Exception:
            return self._ws is not None

    async def send_audio(self, audio_data: bytes):
        """Envoie un frame audio PCM à Deepgram."""
        if not self.is_connected or not audio_data:
            return
        try:
            await self._ws.send(audio_data)
            self._nb_frames += 1
            if self._nb_frames == 1:
                logger.info(f"[DEEPGRAM] Premier frame envoye — {len(audio_data)} bytes")
            elif self._nb_frames % 500 == 0:
                logger.info(f"[DEEPGRAM] {self._nb_frames} frames envoyees")
        except websockets.ConnectionClosed:
            logger.warning("[DEEPGRAM] Connexion fermee — arret envoi")
            self._ws = None
        except Exception as e:
            logger.error(f"[DEEPGRAM] Erreur envoi : {e}")
            self._ws = None

    async def stop(self):
        """Ferme proprement la connexion Deepgram."""
        if self._ws:
            try:
                # Signal de fermeture propre
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await asyncio.sleep(0.3)
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._receiver_task:
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass
            self._receiver_task = None

        logger.info("[DEEPGRAM] Session terminée")

    async def _receive_loop(self):
        """Boucle de réception des résultats Deepgram."""
        try:
            async for msg in self._ws:
                try:
                    data = json.loads(msg)

                    # Ignorer les messages qui ne sont pas des résultats
                    msg_type = data.get("type", "")
                    if msg_type != "Results":
                        continue

                    channel = data.get("channel", {})
                    alternatives = channel.get("alternatives", [])
                    if not alternatives:
                        continue

                    transcript = alternatives[0].get("transcript", "")
                    if not transcript:
                        continue

                    is_final = data.get("is_final", False)

                    if is_final:
                        logger.info(f'[DEEPGRAM FINAL] "{transcript[:80]}"')
                        await self._on_final(transcript)
                    else:
                        logger.debug(f'[DEEPGRAM PARTIEL] "{transcript[:80]}"')
                        await self._on_partial(transcript)

                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    logger.error(f"[DEEPGRAM] Erreur traitement message : {e}")

        except websockets.ConnectionClosed as e:
            logger.warning(f"[DEEPGRAM] Connexion fermée : {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[DEEPGRAM] Erreur réception : {e}")
