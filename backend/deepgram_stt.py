"""
KhutbaBox — Deepgram Nova-3 STT streaming
Transcription arabe en temps réel via WebSocket.
Compatible avec deepgram-sdk v6.x (async context manager API)
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


async def run_deepgram_session(
    on_partial: Callable[[str], Awaitable[None]],
    on_final: Callable[[str], Awaitable[None]],
    audio_queue: asyncio.Queue,
    stop_event: asyncio.Event,
):
    """
    Ouvre une session Deepgram Nova-3 et consomme l'audio depuis la queue.
    S'arrête quand stop_event est déclenché.

    Usage depuis main.py:
        queue = asyncio.Queue()
        stop = asyncio.Event()
        task = asyncio.create_task(run_deepgram_session(on_partial, on_final, queue, stop))
        # Pour envoyer de l'audio:
        await queue.put(audio_bytes)
        # Pour arrêter:
        stop.set()
        await task
    """
    os.environ["DEEPGRAM_API_KEY"] = DEEPGRAM_API_KEY
    client = AsyncDeepgramClient()

    def on_message(message):
        """Callback Deepgram : reçoit les résultats de transcription."""
        try:
            # Convertir le message en dict
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
                asyncio.ensure_future(on_final(transcript))
            else:
                logger.debug(f'[DEEPGRAM PARTIEL] "{transcript[:80]}"')
                asyncio.ensure_future(on_partial(transcript))

        except Exception as e:
            logger.error(f"[DEEPGRAM] Erreur callback : {e}")

    def on_error(error):
        """Callback Deepgram : erreur."""
        logger.error(f"[DEEPGRAM] Erreur : {error}")

    try:
        async with client.listen.v1.connect(
            model="nova-3-general",
            language="ar",
            encoding="linear16",
            sample_rate=16000,
            interim_results=True,
            utterance_end_ms=1500,
            endpointing=300,
            smart_format=True,
        ) as socket:
            socket.on(EventType.MESSAGE, on_message)
            socket.on(EventType.ERROR, on_error)

            await socket.start_listening()
            logger.info("[DEEPGRAM] Connexion streaming ouverte (Nova-3, arabe)")

            # Boucle : lire l'audio de la queue et l'envoyer à Deepgram
            while not stop_event.is_set():
                try:
                    audio_data = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                    socket.send_media(audio_data)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"[DEEPGRAM] Erreur envoi audio : {e}")
                    break

            # Fermeture propre
            try:
                socket.send_finalize()
            except Exception:
                pass
            logger.info("[DEEPGRAM] Session terminée")

    except Exception as e:
        logger.error(f"[DEEPGRAM] Erreur connexion : {e}")
