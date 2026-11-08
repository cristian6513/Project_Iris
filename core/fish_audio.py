"""Fish Audio hosted API. Separate from the user's local Fish Speech server.

Native REST contract checked 2026-10-21 against docs.fish.audio and the
official fishaudio SDK. No extra dependencies, automatic retries or downloads.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

import httpx

FISH_AUDIO_MODELS = frozenset({'s2.1-pro-free', 's2.1-pro', 's2-pro'})
FISH_AUDIO_DEFAULT_MODEL = 's2.1-pro-free'
# Catalog language filter; Fish tags voices with ISO 639-1 codes ("es", "en").
FISH_AUDIO_LANGUAGES = frozenset({'es', 'en', 'pt', 'fr', 'de', 'it', 'ja', 'ko', 'zh', 'ru', 'ar'})
PAGE_SIZE = 20
MAX_PAGE = 50   # Fish serves the first 1000 results of a query
_VOICE_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_LANGUAGE_TAG = re.compile(r'[a-z]{2,3}(-[A-Za-z]{2,4})?\Z')
_BASE_URL = 'https://api.fish.audio'
# Published voice samples: the catalog links them on Fish's CDN, a single voice
# as a signed link to Fish's own storage bucket. Nothing else is fetched.
_SAMPLE_HOST = 'platform.r2.fish.audio'
_SAMPLE_BUCKET_HOST_SUFFIX = '.r2.cloudflarestorage.com'
_SAMPLE_BUCKET_PATH = '/fish-platform-data/'
_SAMPLE_MAX_BYTES = 4 * 1024 * 1024


class FishAudioError(ValueError):
    """An actionable message safe to show without returning provider bodies."""


class FishKeyRejected(FishAudioError):
    """Fish answered that this key is no good, as opposed to not answering at all."""


def validate_voice_id(value: str) -> str:
    value = value.strip()
    if not _VOICE_ID.fullmatch(value):
        raise FishAudioError('Selecciona una voz de Fish Audio o clona una antes de continuar.')
    return value


def validate_model(model: str) -> str:
    if model not in FISH_AUDIO_MODELS:
        # An unknown/missing header on Fish falls back to a billed model.
        raise FishAudioError('Selecciona un modelo de Fish Audio admitido por Iris.')
    return model


def validate_language(language: str) -> str:
    """'' searches every language."""
    if language and language not in FISH_AUDIO_LANGUAGES:
        raise FishAudioError('Selecciona un idioma de la lista para buscar voces de Fish Audio.')
    return language


def _headers(api_key: str) -> dict[str, str]:
    key = api_key.strip()
    if not key:
        raise FishAudioError('Introduce tu API key de Fish Audio para usar sus voces.')
    if len(key) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise FishAudioError('La API key de Fish Audio tiene un formato inválido.')
    return {'Authorization': f'Bearer {key}'}


def _check(response: httpx.Response) -> None:
    if 200 <= response.status_code < 300:
        return
    reasons = {
        401: 'La API key de Fish Audio no es válida o caducó.',
        402: 'Fish Audio indica que falta saldo o acceso al modelo elegido.',
        403: 'Tu cuenta de Fish Audio no tiene permiso para esta operación o voz.',
        404: 'Fish Audio no encuentra esa voz; puede haberse retirado.',
        422: 'Fish Audio rechazó los datos de la voz o la muestra.',
        429: 'Fish Audio alcanzó su límite de solicitudes. Espera antes de volver a intentar.',
    }
    if response.status_code == 401:
        raise FishKeyRejected(reasons[401])
    raise FishAudioError(reasons.get(response.status_code, f'Fish Audio no pudo completar la operación (HTTP {response.status_code}).'))


async def _request(method: str, path: str, api_key: str, **kwargs: Any) -> httpx.Response:
    headers = {**_headers(api_key), **kwargs.pop('headers', {})}
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            response = await client.request(method, _BASE_URL + path, headers=headers, **kwargs)
    except httpx.TimeoutException:
        raise FishAudioError('Fish Audio tardó demasiado. La operación no se reintentó automáticamente; si estabas clonando, revisa tus voces antes de repetirla.') from None
    except httpx.HTTPError:
        raise FishAudioError('No se pudo conectar con Fish Audio. Comprueba tu conexión.') from None
    _check(response)
    return response


async def synthesize(api_key: str, voice_id: str, model: str, text: str) -> bytes:
    model = validate_model(model)
    voice_id = validate_voice_id(voice_id)
    response = await _request('POST', '/v1/tts', api_key, headers={'model': model}, json={
        'text': text, 'reference_id': voice_id, 'format': 'mp3', 'latency': 'normal',
    })
    audio = response.content
    is_mp3 = audio.startswith(b'ID3') or (len(audio) >= 2 and audio[0] == 255 and audio[1] & 0xE0 == 0xE0)
    if not is_mp3 or len(audio) < 16:
        raise FishAudioError('Fish Audio respondió sin un audio MP3 válido.')
    return audio


async def create_voice(api_key: str, name: str, audio: bytes, transcript: str = '') -> str:
    fields = {'type': 'tts', 'title': name, 'train_mode': 'fast', 'visibility': 'private',
              'generate_sample': 'false'}
    if transcript:
        # Without one, Fish transcribes the sample itself.
        fields['texts'] = transcript
    response = await _request('POST', '/model', api_key, data=fields,
                              files={'voices': ('voice_sample.wav', audio, 'audio/wav')})
    try:
        data = response.json()
        if not isinstance(data, dict) or data.get('visibility') != 'private' or data.get('state') == 'failed':
            raise ValueError('unconfirmed private voice')
        return validate_voice_id(data.get('_id', ''))
    except (ValueError, TypeError, AttributeError):
        raise FishAudioError('Fish Audio no confirmó una voz privada válida. Revisa tus voces en Fish antes de repetir la clonación.') from None


async def check_key(api_key: str) -> None:
    """The public catalog answers any key; the account's own voices need a valid one."""
    await _request('GET', '/model', api_key, params={'self': 'true', 'page_size': 1})


def _sample_url(item: dict[str, Any]) -> str:
    samples = item.get('samples')
    for sample in samples if isinstance(samples, list) else []:
        url = sample.get('audio') if isinstance(sample, dict) else None
        if not isinstance(url, str):
            continue
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.port is not None or parts.username or not parts.hostname:
            continue
        on_cdn = parts.hostname == _SAMPLE_HOST
        in_bucket = parts.hostname.endswith(_SAMPLE_BUCKET_HOST_SUFFIX) and parts.path.startswith(_SAMPLE_BUCKET_PATH)
        if on_cdn or in_bucket:
            return url
    return ''


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _voice_entry(item: Any) -> dict[str, Any] | None:
    """Name, languages and popularity only: images and sample URLs never reach the page."""
    if not isinstance(item, dict) or item.get('dmca_taken_down'):
        return None
    # A voice-conversion model cannot read text; a failed or training one cannot speak yet.
    if item.get('type', 'tts') != 'tts' or item.get('state') in {'failed', 'training'}:
        return None
    try:
        identifier = validate_voice_id(item.get('_id', ''))
    except (FishAudioError, AttributeError):
        return None
    languages = item.get('languages')
    return {
        'id': identifier,
        'name': str(item.get('title') or identifier)[:120],
        'languages': [code for code in languages if isinstance(code, str) and _LANGUAGE_TAG.fullmatch(code)][:4]
        if isinstance(languages, list) else [],
        'likes': _count(item.get('like_count')),
        'uses': _count(item.get('task_count')),
        'has_sample': bool(_sample_url(item)),
    }


async def list_voices(api_key: str, query: str = '', self_only: bool = False, language: str = '',
                      page: int = 1) -> dict[str, Any]:
    # Like the official SDK: empty filters are left out rather than sent blank.
    params: dict[str, Any] = {'page_size': PAGE_SIZE, 'page_number': page}
    if query:
        params['title'] = query[:80]
    if self_only:
        params['self'] = 'true'
    if validate_language(language):
        params['language'] = language
    response = await _request('GET', '/model', api_key, params=params)
    try:
        data = response.json()
        items = data['items']
        if not isinstance(items, list):
            raise ValueError('invalid items')
        voices = [entry for entry in map(_voice_entry, items[:PAGE_SIZE]) if entry]
        more = data.get('has_more')
        if not isinstance(more, bool):
            total = data.get('total')
            more = isinstance(total, int) and page * PAGE_SIZE < total
        return {'voices': voices, 'has_more': more and page < MAX_PAGE}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise FishAudioError('Fish Audio devolvió un catálogo que Iris no pudo leer.') from None


async def sample_audio(api_key: str, voice_id: str) -> bytes:
    """The sample Fish publishes with a voice: no synthesis, so no credit is used."""
    voice_id = validate_voice_id(voice_id)
    response = await _request('GET', f'/model/{voice_id}', api_key)
    try:
        url = _sample_url(response.json())
    except (ValueError, TypeError, AttributeError):
        url = ''
    if not url:
        raise FishAudioError('Esta voz no tiene una muestra publicada en Fish. «Escuchar voz seleccionada» '
                             'genera tu frase con ella y usa tu cuota.')
    # The CDN gets no credentials and no redirects.
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            async with client.stream('GET', url) as sample:
                if sample.status_code != 200 or not sample.headers.get('content-type', '').startswith('audio/'):
                    raise FishAudioError('Fish Audio no entregó la muestra de esta voz.')
                audio = bytearray()
                async for chunk in sample.aiter_bytes():
                    audio += chunk
                    if len(audio) > _SAMPLE_MAX_BYTES:
                        raise FishAudioError('La muestra de esta voz es demasiado grande para escucharla aquí.')
    except httpx.TimeoutException:
        raise FishAudioError('Fish Audio tardó demasiado en entregar la muestra.') from None
    except httpx.HTTPError:
        raise FishAudioError('No se pudo descargar la muestra de Fish Audio. Comprueba tu conexión.') from None
    is_mp3 = audio.startswith(b'ID3') or (len(audio) >= 2 and audio[0] == 255 and audio[1] & 0xE0 == 0xE0)
    if not is_mp3 or len(audio) < 16:
        raise FishAudioError('Fish Audio no entregó la muestra de esta voz.')
    return bytes(audio)
