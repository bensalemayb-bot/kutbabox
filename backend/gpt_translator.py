"""
KhutbaBox — Traduction GPT-4.1 mini (v3 streaming parallèle)
Traduit l'arabe vers chaque langue demandée via des appels GPT parallèles en streaming.
Scanne le glossaire islamique et injecte seulement les termes détectés.
Supporte callbacks on_partial / on_final pour broadcast progressif aux fidèles.
"""

import os
import json
import time
import asyncio
import logging
from typing import Callable, Awaitable

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


# ── Construction des sections dynamiques du prompt ──


def _build_glossaire_section(
    termes_detectes: dict[str, dict], lang_code: str
) -> str:
    """Construit la section glossaire pour une langue donnée."""
    if not termes_detectes:
        return ""

    lignes = []
    for arabe, trads in termes_detectes.items():
        traduction = trads.get(lang_code)
        if not traduction:
            continue
        lignes.append(f'  - {arabe} → "{traduction}"')

    if not lignes:
        return ""

    return (
        "\n\nTERMES DU GLOSSAIRE DÉTECTÉS DANS CETTE PHRASE "
        "(utilise EXACTEMENT ces traductions) :\n"
        + "\n".join(lignes)
        + "\n"
    )


def _build_contexte_section(
    contexte_recent: list[tuple[str, dict[str, str]]] | None, lang_code: str
) -> str:
    """Construit la section contexte récent pour une langue donnée."""
    if not contexte_recent:
        return ""

    lignes = []
    for i, (arabe, trads) in enumerate(contexte_recent, start=1):
        traduction = trads.get(lang_code, "") if trads else ""
        if not traduction:
            continue
        lignes.append(f'{i}. «{arabe}» → "{traduction}"')

    if not lignes:
        return ""

    return (
        "\n\nCONTEXTE RÉCENT DU SERMON "
        "(pour cohérence avec ce qui vient d'être dit) :\n"
        + "\n".join(lignes)
        + "\n"
    )


def _build_system_prompt(
    lang_code: str,
    termes_detectes: dict[str, dict],
    contexte_recent: list[tuple[str, dict[str, str]]] | None,
) -> str:
    """Construit le prompt système v2 pour une langue donnée."""
    lang_nom = LANGUES.get(lang_code, lang_code)
    glossaire_section = _build_glossaire_section(termes_detectes, lang_code)
    contexte_section = _build_contexte_section(contexte_recent, lang_code)

    return (
        f"Tu es un interprète professionnel de sermons islamiques (khutba) en direct.\n\n"
        f"SITUATION : Un imam parle en arabe dans une mosquée. Des fidèles écoutent ta "
        f"traduction via un haut-parleur TTS sur leur smartphone. Tu traduis en {lang_nom}.\n\n"
        f"RÈGLES :\n"
        f"1. Jamais de mot-à-mot. Reformule pour sonner naturel en {lang_nom}, comme un vrai "
        f"imam {lang_nom} parlerait.\n"
        f"2. Phrases COURTES, mots simples. C'est un discours ORAL lu par un robot TTS.\n"
        f"3. Après محمد / النبي / الرسول → ajoute \"(paix et bénédictions sur lui)\" dans la "
        f"langue cible.\n"
        f"4. Le mot الله se traduit TOUJOURS par \"Allah\". Jamais \"Dieu\", \"God\" ou autre "
        f"équivalent.\n"
        f"5. Si la phrase arabe semble coupée → traduis UNIQUEMENT ce qui est présent. "
        f"N'invente jamais la suite.\n"
        f"6. Registre : soutenu mais accessible. Pas de familier.\n"
        f"7. Termes à garder tels quels (translittération) : صلاة→salat, زكاة→zakat, حج→hajj, "
        f"جهاد→jihad, توحيد→tawhid, شهادة→shahada, تقوى→taqwa, سنة→sunna.\n"
        f"8. FORMAT TTS : pas de parenthèses explicatives, pas d'abréviations, pas de sigles. "
        f"Tout en toutes lettres, comme à voix haute.\n"
        f"{glossaire_section}{contexte_section}\n"
        f"EXEMPLES (style attendu, à adapter à {lang_nom}) :\n"
        f"Arabe : الحمد لله رب العالمين\n"
        f"FR : Louange à Allah, Seigneur des mondes.\n\n"
        f"Arabe : اتقوا الله حق تقاته\n"
        f"FR : Craignez Allah comme Il mérite d'être craint.\n\n"
        f"Arabe : إن الصلاة كانت على المؤمنين كتاباً موقوتاً\n"
        f"FR : La salat a été prescrite aux croyants à des heures déterminées.\n\n"
        f"Réponds UNIQUEMENT par la traduction en {lang_nom}. Rien d'autre. Pas de guillemets, "
        f"pas d'explication, pas de JSON."
    )


# ── Traduction streaming pour une langue ──


async def _traduire_une_langue(
    texte_arabe: str,
    lang_code: str,
    termes_detectes: dict[str, dict],
    contexte_recent: list[tuple[str, dict[str, str]]] | None,
    on_partial: Callable[[str, str], Awaitable[None]] | None,
) -> str:
    """
    Traduit une phrase arabe vers UNE langue via un appel GPT streaming.
    Appelle on_partial(lang_code, texte_accumulé) à chaque token reçu.
    Retourne le texte final accumulé (ou "" en cas d'erreur).
    """
    system_prompt = _build_system_prompt(lang_code, termes_detectes, contexte_recent)

    accumulated = ""
    try:
        stream = await _client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": texte_arabe},
            ],
            temperature=0.1,
            max_tokens=300,
            stream=True,
        )

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            token = getattr(delta, "content", None)
            if not token:
                continue
            accumulated += token
            if on_partial is not None:
                try:
                    await on_partial(lang_code, accumulated)
                except Exception as cb_err:
                    logger.warning(
                        f"[GPT] Callback on_partial {lang_code} a échoué : {cb_err}"
                    )

        return accumulated

    except Exception as e:
        logger.error(f"[GPT] Erreur traduction {lang_code}: {e}")
        return ""


# ── Dispatch parallèle ──


async def traduire(
    texte_arabe: str,
    langues: list[str],
    contexte_recent: list[tuple[str, dict[str, str]]] | None = None,
    on_partial: Callable[[str, str], Awaitable[None]] | None = None,
    on_final: Callable[[str, str], Awaitable[None]] | None = None,
) -> dict[str, str]:
    """
    Traduit le texte arabe vers les langues demandées en parallèle (un appel GPT par langue).
    Chaque appel est streaming ; les tokens partiels sont transmis via on_partial, et le
    texte final de chaque langue via on_final.

    Args:
        texte_arabe : phrase complète à traduire.
        langues : liste des codes langues cibles (ex: ["fr", "en", "ur"]).
        contexte_recent : jusqu'à 3 phrases précédentes au format
            [(arabe, {lang_code: traduction}), ...] — pour la cohérence.
        on_partial : callback async appelé à chaque nouveau token :
            on_partial(lang_code, texte_accumulé_jusqu_ici).
        on_final : callback async appelé à la fin de chaque langue :
            on_final(lang_code, texte_final_complet).

    Retourne: {"fr": "traduction...", "en": "translation...", ...}
    Les langues qui échouent sont absentes du dict retourné.
    """
    if not langues:
        return {}

    debut = time.time()

    # Scanner le glossaire une seule fois (partagé par toutes les langues)
    termes_detectes = scanner_glossaire(texte_arabe)

    async def _run_lang(lang: str) -> tuple[str, str]:
        texte = await _traduire_une_langue(
            texte_arabe, lang, termes_detectes, contexte_recent, on_partial
        )
        if texte and on_final is not None:
            try:
                await on_final(lang, texte)
            except Exception as cb_err:
                logger.warning(
                    f"[GPT] Callback on_final {lang} a échoué : {cb_err}"
                )
        return (lang, texte)

    tasks = [asyncio.create_task(_run_lang(lang)) for lang in langues]
    resultats = await asyncio.gather(*tasks, return_exceptions=True)

    traductions: dict[str, str] = {}
    for res in resultats:
        if isinstance(res, Exception):
            logger.error(f"[GPT] Tâche langue a levé une exception : {res}")
            continue
        lang, texte = res
        if texte:
            traductions[lang] = texte

    latence = int((time.time() - debut) * 1000)
    n_success = len(traductions)
    n_total = len(langues)
    n_termes = len(termes_detectes)
    logger.info(
        f"[GPT] {n_success}/{n_total} langues, {n_termes} termes glossaire, {latence}ms"
    )
    return traductions
