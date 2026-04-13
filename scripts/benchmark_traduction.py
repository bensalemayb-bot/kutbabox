"""
KhutbaBox — Benchmark traduction
Mesure la latence de gpt_translator.traduire() sur 10 phrases arabes typiques
d'une khutba, en 8 langues, avec le pipeline streaming parallèle.

Usage:
    py scripts/benchmark_traduction.py
"""

import asyncio
import sys
import time
from pathlib import Path

# Charger .env et rendre le module backend importable
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "backend"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from gpt_translator import init_translator, charger_glossaire, traduire


# 10 phrases typiques d'une khutba (arabe standard + citations classiques)
PHRASES_TEST = [
    "الحمد لله رب العالمين",
    "اتقوا الله حق تقاته",
    "إن الصلاة كانت على المؤمنين كتاباً موقوتاً",
    "يا أيها الذين آمنوا اصبروا وصابروا ورابطوا",
    "قال رسول الله صلى الله عليه وسلم إنما الأعمال بالنيات",
    "وما خلقت الجن والإنس إلا ليعبدون",
    "إن الله مع الصابرين",
    "من يتق الله يجعل له مخرجا",
    "الدنيا مزرعة الآخرة",
    "خير الناس أنفعهم للناس",
]

LANGUES = ["fr", "en", "es", "pt", "tr", "ur", "bs", "sq"]


async def main():
    # Init
    init_translator()
    charger_glossaire(str(ROOT / "backend" / "glossary.json"))

    # Warmup (pour amortir le premier appel TLS/TCP vers OpenAI)
    print("Warmup...")
    await traduire(PHRASES_TEST[0], ["fr"])

    print(f"\nBenchmark: {len(PHRASES_TEST)} phrases × {len(LANGUES)} langues\n" + "=" * 60)

    resultats = []

    for i, phrase in enumerate(PHRASES_TEST, start=1):
        first_token_ts: dict[str, float] = {}

        async def on_partial(lang: str, texte: str):
            if lang not in first_token_ts:
                first_token_ts[lang] = time.time()

        debut = time.time()
        traductions = await traduire(
            phrase,
            LANGUES,
            on_partial=on_partial,
        )
        total_ms = int((time.time() - debut) * 1000)

        # Latences 1er token
        ft_values = [
            int((first_token_ts[l] - debut) * 1000)
            for l in LANGUES
            if l in first_token_ts
        ]
        ft_min = min(ft_values) if ft_values else -1
        ft_max = max(ft_values) if ft_values else -1

        resultats.append({
            "phrase": phrase,
            "total_ms": total_ms,
            "ft_min_ms": ft_min,
            "ft_max_ms": ft_max,
            "langues_ok": len(traductions),
            "fr": traductions.get("fr", "[échec]"),
        })

        print(f"[{i:2d}/{len(PHRASES_TEST)}] total={total_ms}ms  1er_token={ft_min}-{ft_max}ms  "
              f"langues_ok={len(traductions)}/{len(LANGUES)}")
        print(f"        AR: {phrase}")
        print(f"        FR: {traductions.get('fr', '[échec]')}\n")

    # ── Résumé ──
    totaux = sorted(r["total_ms"] for r in resultats)
    n = len(totaux)
    moy = sum(totaux) / n
    med = totaux[n // 2]
    p95 = totaux[min(int(n * 0.95), n - 1)]

    ft_mins = sorted(r["ft_min_ms"] for r in resultats if r["ft_min_ms"] > 0)
    if ft_mins:
        ft_moy = sum(ft_mins) / len(ft_mins)
        ft_med = ft_mins[len(ft_mins) // 2]
    else:
        ft_moy = ft_med = -1

    print("=" * 60)
    print(f"RÉSUMÉ — {n} phrases × {len(LANGUES)} langues")
    print(f"  Latence totale (ms)     : moy={moy:.0f}  med={med}  p95={p95}")
    print(f"  Latence 1er token (ms)  : moy={ft_moy:.0f}  med={ft_med}")

    # ── CSV ──
    timestamp = int(time.time())
    csv_path = ROOT / f"bench_traduction_{timestamp}.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write("phrase_arabe,total_ms,first_token_min_ms,first_token_max_ms,langues_ok,traduction_fr\n")
        for r in resultats:
            phrase_csv = r["phrase"].replace('"', '""')
            fr_csv = r["fr"].replace('"', '""').replace("\n", " ")
            f.write(f'"{phrase_csv}",{r["total_ms"]},{r["ft_min_ms"]},{r["ft_max_ms"]},{r["langues_ok"]},"{fr_csv}"\n')
    print(f"\n✓ CSV : {csv_path}")


if __name__ == "__main__":
    asyncio.run(main())
