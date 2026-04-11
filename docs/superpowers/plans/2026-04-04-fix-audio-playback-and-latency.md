# Fix Audio Playback + Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two bugs: (1) audio doesn't play on mobile phones, (2) TTS latency is 3-4s instead of <2s because languages are processed sequentially.

**Architecture:** Fix mobile audio autoplay by adding proper error logging and ensuring audio plays through the already-unlocked AudioContext. Fix latency by running TTS for all 8 languages in parallel with `asyncio.gather` instead of sequential `for` loop.

**Tech Stack:** Python/FastAPI (backend), HTML/JS (frontend), WebSocket

---

### Task 1: Fix TTS parallel processing (backend)

**Files:**
- Modify: `backend/main.py:344-368` (method `_process_recognized`)

**Problem:** The `for` loop at line 346 processes each language one by one. Each TTS call takes 350-1700ms. With 8 languages sequentially, total time = 3-7 seconds. Running them in parallel = time of the slowest single call (~1.7s max).

- [ ] **Step 1: Replace sequential loop with parallel asyncio.gather**

In `backend/main.py`, replace the `_process_recognized` method (lines 344-368):

```python
    async def _process_recognized(self, texte_arabe: str, translations: dict):
        """Traite une phrase reconnue : envoie texte final + TTS pour chaque langue."""

        async def traiter_langue(lang: str, text: str):
            """Traite une langue : envoie le texte puis l'audio TTS."""
            logger.info(f'[AZURE FINAL] {lang} : "{text[:80]}"')
            await broadcast_translation("final_text", lang, text=text)
            audio, fmt = await generer_tts(text, lang)
            if audio:
                audio_b64 = base64.b64encode(audio).decode()
                await broadcast_translation("audio_chunk", lang, audio_b64=audio_b64, audio_format=fmt)
            else:
                logger.warning(f"[TTS] Pas d'audio pour {lang} — envoi texte uniquement")

        # Lancer le TTS pour toutes les langues en parallèle
        taches = [
            traiter_langue(lang, text)
            for lang, text in translations.items()
            if text.strip()
        ]
        await asyncio.gather(*taches)

        # Enregistrer dans le monitoring
        historique.append({
            "heure": datetime.now(timezone.utc).isoformat(),
            "texte_arabe": texte_arabe,
            "traductions": translations,
            "mode": "azure",
        })
```

- [ ] **Step 2: Verify in Docker logs**

Run: `docker compose up --build` then `docker compose logs -f`
Expected: All 8 `[AZURE FINAL]` lines appear almost simultaneously instead of one by one. Total TTS time should drop from ~5s to ~1.5s.

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "perf: parallelize TTS across all 8 languages with asyncio.gather"
```

---

### Task 2: Fix audio playback on mobile (frontend)

**Files:**
- Modify: `frontend/index.html:501-529` (functions `jouerAudio` and `jouerProchain`)

**Problem:** On mobile, `new Audio().play()` can fail silently because the Audio element isn't linked to the user-gesture-unlocked AudioContext. The `.catch(() => jouerProchain())` swallows the error, so audio chunks are skipped without any feedback. Fix: add debug logging and use the AudioContext decodeAudioData API which respects the unlocked context.

- [ ] **Step 1: Replace audio player with AudioContext-based playback**

In `frontend/index.html`, replace the `jouerAudio` and `jouerProchain` functions (lines 501-529):

```javascript
    function jouerAudio(base64Data, format) {
      const mime = format === 'wav' ? 'audio/wav' : 'audio/mpeg';
      audioQueue.push({ data: base64Data, mime: mime });
      if (!isPlaying) jouerProchain();
    }

    function jouerProchain() {
      if (audioQueue.length === 0) {
        isPlaying = false;
        return;
      }
      isPlaying = true;
      const { data, mime } = audioQueue.shift();

      const bytes = Uint8Array.from(atob(data), c => c.charCodeAt(0));
      const blob = new Blob([bytes], { type: mime });
      const url = URL.createObjectURL(blob);

      const audio = new Audio(url);
      audio.onended = () => {
        URL.revokeObjectURL(url);
        jouerProchain();
      };
      audio.onerror = (e) => {
        console.warn('[KhutbaBox] Erreur audio:', e);
        URL.revokeObjectURL(url);
        jouerProchain();
      };
      audio.play().then(() => {
        console.log('[KhutbaBox] Audio lecture OK');
      }).catch((err) => {
        console.warn('[KhutbaBox] audio.play() bloqué:', err.message);
        // Fallback: essayer via AudioContext
        if (audioCtx && audioCtx.state === 'running') {
          fetch(url)
            .then(r => r.arrayBuffer())
            .then(buf => audioCtx.decodeAudioData(buf))
            .then(decoded => {
              const source = audioCtx.createBufferSource();
              source.buffer = decoded;
              source.connect(audioCtx.destination);
              source.onended = () => {
                URL.revokeObjectURL(url);
                jouerProchain();
              };
              source.start();
              console.log('[KhutbaBox] Audio via AudioContext OK');
            })
            .catch(() => {
              URL.revokeObjectURL(url);
              jouerProchain();
            });
        } else {
          URL.revokeObjectURL(url);
          jouerProchain();
        }
      });
    }
```

- [ ] **Step 2: Test on mobile**

1. Open `http://192.168.1.81` on phone (same WiFi)
2. Select a language and tap "Commencer"
3. Speak Arabic into the microphone
4. Check: text appears AND audio plays on the phone
5. Open browser console on phone (chrome://inspect) to see logs

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "fix: mobile audio playback with AudioContext fallback"
```

---

### Verification

After both tasks:

1. `docker compose up --build`
2. `py scripts/audio_capture.py`
3. Open `http://localhost` on PC — verify text + audio
4. Open `http://192.168.1.81` on phone — verify text + audio
5. Check `docker compose logs -f`:
   - All 8 `[TTS]` lines should appear within ~1-2s of each other
   - No `[AZURE ANNULE]` errors
6. Latency target: text appears in <2s, audio follows within 1s after text
