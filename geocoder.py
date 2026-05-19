"""
Geocodificação de endereços brasileiros via Nominatim (OpenStreetMap), gratuito.

A Cobli precisa de latitude/longitude por parada — a planilha não tem isso,
então geramos via geocoding.

- Limite de 1 req/seg do Nominatim (respeitamos com geopy.RateLimiter)
- Cache em arquivo JSON pra acelerar reruns (evita rebater no mesmo endereço)
- Fallback: pode adicionar Google Maps Geocoding API depois, se quiser mais precisão
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger


CACHE_PATH = Path(".geocode_cache.json")


def _load_cache() -> dict[str, dict[str, Any]]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict[str, dict[str, Any]]) -> None:
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


class Geocoder:
    """Geocoder com cache e rate-limit. Usa Nominatim (OSM) por padrão."""

    def __init__(self, user_agent: str = "roteirizacao_cobli/1.0"):
        # Import lazy: só carrega geopy se for usado
        from geopy.extra.rate_limiter import RateLimiter
        from geopy.geocoders import Nominatim

        self._geocoder = Nominatim(user_agent=user_agent, timeout=10)
        # 1 req/seg conforme política do Nominatim
        self._geocode = RateLimiter(self._geocoder.geocode, min_delay_seconds=1.0)
        self._cache = _load_cache()

    def geocode(self, address: str) -> dict[str, Any] | None:
        """
        Retorna um dict com {latitude, longitude, raw} ou None se falhar.
        Usa cache pra evitar bater na API repetido.
        """
        if not address or not address.strip():
            return None

        key = address.strip().lower()
        if key in self._cache:
            logger.debug(f"Cache hit: {address}")
            return self._cache[key]

        logger.info(f"Geocodificando: {address}")
        try:
            location = self._geocode(address, country_codes="br")
        except Exception as e:
            logger.warning(f"Falha geocoding '{address}': {e}")
            return None

        if not location:
            logger.warning(f"Não encontrou: {address}")
            self._cache[key] = None  # cacheia falha pra não retentar
            _save_cache(self._cache)
            return None

        result = {
            "latitude": float(location.latitude),
            "longitude": float(location.longitude),
            "display_name": location.address,
        }
        self._cache[key] = result
        _save_cache(self._cache)
        return result
