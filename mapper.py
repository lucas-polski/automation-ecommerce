"""
Transforma cada linha da planilha no formato GraphQL `RoutingSettings` da Cobli.

Estrutura derivada do tráfego real do painel:

{
  "activities": [
    { "duration": 600000, "destination": {...} },                  # depósito (origem)
    { "duration": 600000, "name": "...", "destination": {...},     # cliente 1
      "phone_number": "55..." },
    ...
  ],
  "start_time": 1778256000000,    # Unix ms
  "type": "OPTIMIZED",
  "optimization": {
    "working_hours": {"start_time": ..., "end_time": ...},
    "num_vehicles": 1,
    "objectives": ["MIN_TRANSPORT_TIME"],
    "end_route_destination": {...}                                  # ponto final
  }
}
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone, timedelta
from typing import Any

import pandas as pd
from loguru import logger

from geocoder import Geocoder


# Fuso de Brasília (UTC-3, sem horário de verão desde 2019)
BRT = timezone(timedelta(hours=-3))


@dataclass
class Defaults:
    cidade: str = "Curitiba"
    estado: str = "PR"
    pais: str = "BR"  # ISO-2 conforme Cobli espera
    duracao_parada_min: int = 10  # tempo de serviço por parada (minutos)
    horario_inicio: time = field(default_factory=lambda: time(8, 0))   # 08:00
    horario_fim: time = field(default_factory=lambda: time(18, 0))     # 18:00


def _is_blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def _safe_str(v: Any) -> str | None:
    if _is_blank(v):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _format_phone_br(codigo: Any) -> str | None:
    """
    Cobli espera o telefone com código do país, sem o '+'.
    Ex: '41999299865' (DDD+celular) → '5541999299865'.
    """
    s = _safe_str(codigo)
    if not s:
        return None
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        return None
    # Já tem 55 prefixado?
    if digits.startswith("55") and len(digits) >= 12:
        return digits
    return f"55{digits}"


def _datetime_to_unix_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _build_destination(
    *,
    latitude: float,
    longitude: float,
    street_address: str,
    street_number: str | None = None,
    street_complement: str | None = None,
    city: str,
    state: str,
    country: str = "BR",
    postal_code: str | None = None,
    neighborhood: str | None = None,
) -> dict[str, Any]:
    dest: dict[str, Any] = {
        "coordinate": {"latitude": float(latitude), "longitude": float(longitude)},
        "street_address": street_address,
        "city": city,
        "state": state,
        "country": country,
    }
    if street_number:
        dest["street_number"] = str(street_number)
    if street_complement:
        dest["street_complement"] = street_complement
    if postal_code:
        dest["postal_code"] = postal_code
    if neighborhood:
        dest["neighborhood"] = neighborhood
    return dest


def _validate_coordinate(lat: float, lng: float, contexto: str) -> bool:
    """Garante que as coordenadas estão dentro do globo terrestre."""
    if not (-90 <= lat <= 90):
        logger.error(f"Latitude inválida em {contexto}: {lat} (esperado entre -90 e 90)")
        return False
    if not (-180 <= lng <= 180):
        logger.error(f"Longitude inválida em {contexto}: {lng} (esperado entre -180 e 180)")
        return False
    return True


def _coerce_coord(raw_value: Any) -> float | None:
    """
    Converte um valor cru pra coordenada decimal, lidando com vários formatos:
    - "-25.5053174" → -25.5053174
    - "-25,5053174" → -25.5053174 (vírgula BR)
    - -255053174.0 (sem decimal, do gspread com locale BR) → -25.5053174
    - -25.5053174 (já correto) → -25.5053174

    Retorna None se não conseguir interpretar.
    """
    if _is_blank(raw_value):
        return None

    # 1. Tenta como string, trocando vírgula por ponto
    try:
        s = str(raw_value).strip().replace(",", ".")
        v = float(s)
    except (TypeError, ValueError):
        return None

    # 2. Se já está em range válido, retorna
    if -180 <= v <= 180:
        return v

    # 3. Bug do gspread + locale BR: a vírgula decimal foi tratada como separador
    # de milhares e removida, multiplicando o valor por 10^N (N = casas decimais).
    # O Apps Script usa 7 casas, mas tenta várias potências por segurança.
    for power in [7, 6, 8, 5, 4]:
        candidate = v / (10 ** power)
        if -180 <= candidate <= 180:
            logger.debug(
                f"Coordenada {raw_value} recuperada como {candidate} "
                f"(dividida por 10^{power} — bug locale BR)."
            )
            return candidate

    return None


def _read_lat_lng_from_row(row: pd.Series) -> tuple[float, float] | None:
    """Lê LATITUDE/LONGITUDE da planilha se as colunas existirem e estiverem preenchidas."""
    for lat_col, lng_col in [
        ("LATITUDE", "LONGITUDE"),
        ("Latitude", "Longitude"),
        ("latitude", "longitude"),
        ("LAT", "LNG"),
        ("LAT", "LON"),
    ]:
        if lat_col in row.index and lng_col in row.index:
            lat_raw = row.get(lat_col)
            lng_raw = row.get(lng_col)
            if _is_blank(lat_raw) or _is_blank(lng_raw):
                continue

            lat = _coerce_coord(lat_raw)
            lng = _coerce_coord(lng_raw)

            if lat is None or lng is None:
                logger.warning(
                    f"Não consegui converter {lat_col}={lat_raw!r}, {lng_col}={lng_raw!r}"
                )
                continue

            logger.debug(f"Lendo lat/lng da planilha ({lat_col}/{lng_col}): {lat}, {lng}")
            return lat, lng
    return None


def _row_to_activity(
    row: pd.Series,
    defaults: Defaults,
    geocoder: Geocoder | None,
    incluir_load_size: bool = False,
    incluir_additional_info: bool = False,
) -> dict[str, Any] | None:
    nome = _safe_str(row.get("NOME"))
    rua = _safe_str(row.get("ENDEREÇO"))
    numero = _safe_str(row.get("NUMERO"))
    complemento = _safe_str(row.get("COMPLEMENTO"))
    obs = _safe_str(row.get("Observação"))
    pedido = _safe_str(row.get("Nº Pedido"))
    telefone = _format_phone_br(row.get("CODIGO"))
    volumes = row.get("Volumes")

    if not (nome and rua):
        logger.warning(f"Linha pulada por falta de NOME ou ENDEREÇO: {nome=}, {rua=}")
        return None

    # 1. Tenta lat/lng da própria planilha
    coords = _read_lat_lng_from_row(row)

    # Valida — se vier valor absurdo da planilha, ignora e cai pro geocoder
    if coords:
        lat_test, lng_test = coords
        if not _validate_coordinate(lat_test, lng_test, contexto=f"planilha (linha '{nome}')"):
            logger.warning(
                f"Coordenadas inválidas na planilha pra '{nome}' "
                f"(provavelmente vírgula em vez de ponto decimal). Tentando geocoder."
            )
            coords = None

    # 2. Senão, geocoda
    if not coords:
        if geocoder is None:
            logger.error(
                f"'{nome}' não tem LATITUDE/LONGITUDE válidas na planilha e geocoder está desabilitado. "
                "Linha pulada."
            )
            return None
        addr_para_geocode = ", ".join(filter(None, [
            f"{rua}, {numero}" if numero else rua,
            defaults.cidade,
            defaults.estado,
            defaults.pais,
        ]))
        geo = geocoder.geocode(addr_para_geocode)
        if not geo:
            logger.error(f"Não consegui geocodificar '{nome}' ({addr_para_geocode}). Linha pulada.")
            return None
        coords = (geo["latitude"], geo["longitude"])

    lat, lng = coords

    if not _validate_coordinate(lat, lng, contexto=f"parada '{nome}'"):
        logger.error(
            f"Coordenadas absurdas pra '{nome}' mesmo após fallback. Linha pulada."
        )
        return None

    activity: dict[str, Any] = {
        "duration": defaults.duracao_parada_min * 60 * 1000,  # min → ms
        "name": nome,
        "destination": _build_destination(
            latitude=lat,
            longitude=lng,
            street_address=rua,
            street_number=numero,
            # Nota: COMPLEMENTO NÃO vai aqui. É referência visual pro entregador
            # ("casa verde", "portão azul"), não dado estruturado de endereço.
            # Vai para additional_info abaixo.
            city=defaults.cidade,
            state=defaults.estado,
            country=defaults.pais,
        ),
    }

    if telefone:
        activity["phone_number"] = telefone

    # Notas pro entregador: complemento + observação + nº pedido
    # OBS: o payload original que retornou 200 OK NÃO tinha additional_info.
    # Por isso é desabilitado por padrão — habilita via flag se a Cobli aceitar.
    if incluir_additional_info:
        notes_parts = []
        if complemento:
            notes_parts.append(f"Ref: {complemento}")
        if obs:
            notes_parts.append(obs)
        if pedido:
            notes_parts.append(f"Pedido {pedido}")
        if notes_parts:
            activity["additional_info"] = " | ".join(notes_parts)

    # Volume da carga
    # OBS: enviar load_size SEM definir vehicle_capacity faz o otimizador
    # da Cobli usar capacidade default (provavelmente 0) e rejeitar tudo
    # com reason_code: 3. Desabilitado por padrão.
    if incluir_load_size and volumes is not None and not (isinstance(volumes, float) and math.isnan(volumes)):
        try:
            activity["load_size"] = float(volumes)
        except (TypeError, ValueError):
            pass

    return activity


def _build_depot_activity(
    *,
    deposito_endereco: str,
    deposito_lat: float,
    deposito_lng: float,
    defaults: Defaults,
) -> dict[str, Any]:
    """
    Primeira atividade do array é o ponto de partida (depósito).
    Diferente das paradas, NÃO leva 'name' nem 'phone_number'.
    """
    # Tenta extrair rua/número do endereço configurado, senão joga tudo em street_address
    parts = [p.strip() for p in deposito_endereco.split(",")]
    street_address = parts[0] if parts else deposito_endereco
    street_number = parts[1] if len(parts) > 1 and parts[1].replace("-", "").isdigit() else None

    return {
        "duration": defaults.duracao_parada_min * 60 * 1000,
        "destination": _build_destination(
            latitude=deposito_lat,
            longitude=deposito_lng,
            street_address=street_address,
            street_number=street_number,
            city=defaults.cidade,
            state=defaults.estado,
            country=defaults.pais,
        ),
    }


def dataframe_to_route_settings(
    df: pd.DataFrame,
    *,
    deposito_endereco: str,
    deposito_lat: float,
    deposito_lng: float,
    retorno_endereco: str | None = None,
    retorno_lat: float | None = None,
    retorno_lng: float | None = None,
    data_rota: date,
    defaults: Defaults | None = None,
    num_vehicles: int = 1,
    objectives: list[str] | None = None,
    use_geocoder: bool = True,
    incluir_load_size: bool = False,
    incluir_additional_info: bool = False,
) -> dict[str, Any]:
    """
    Constrói o objeto `routeSettings` (variables.routeSettings da mutation).

    Se `use_geocoder=True` e a planilha não tiver colunas LATITUDE/LONGITUDE,
    geocodifica via Nominatim (OSM, gratuito, ~1 req/seg).
    """
    defaults = defaults or Defaults()
    objectives = objectives or ["MIN_TRANSPORT_TIME"]

    # Datetimes em milissegundos UTC (epoch)
    inicio = datetime.combine(data_rota, defaults.horario_inicio, tzinfo=BRT)
    fim = datetime.combine(data_rota, defaults.horario_fim, tzinfo=BRT)
    start_ms = _datetime_to_unix_ms(inicio)
    end_ms = _datetime_to_unix_ms(fim)

    if not _validate_coordinate(deposito_lat, deposito_lng, contexto="depósito"):
        raise ValueError(
            f"Coordenadas do depósito inválidas: lat={deposito_lat}, lng={deposito_lng}. "
            "Verifica DEPOSITO_LATITUDE e DEPOSITO_LONGITUDE no .env."
        )

    # Filtra linhas válidas
    valid = df.dropna(subset=["NOME", "ENDEREÇO"]).copy()
    if valid.empty:
        raise ValueError("Nenhuma linha válida na planilha (precisa de NOME e ENDEREÇO).")

    # Geocoder lazy: só inicializa se for usar
    has_lat_lng_cols = any(
        c in valid.columns for c in ["LATITUDE", "Latitude", "latitude", "LAT"]
    )
    geocoder: Geocoder | None = None
    if use_geocoder and not has_lat_lng_cols:
        logger.info(
            "Planilha sem colunas LATITUDE/LONGITUDE — usando geocoder (Nominatim/OSM). "
            "Considere adicionar essas colunas para acelerar e dar mais precisão."
        )
        geocoder = Geocoder()

    # Atividade 0 = depósito (origem)
    activities = [
        _build_depot_activity(
            deposito_endereco=deposito_endereco,
            deposito_lat=deposito_lat,
            deposito_lng=deposito_lng,
            defaults=defaults,
        )
    ]

    # Atividades 1..N = clientes
    for _, row in valid.iterrows():
        act = _row_to_activity(
            row, defaults, geocoder,
            incluir_load_size=incluir_load_size,
            incluir_additional_info=incluir_additional_info,
        )
        if act:
            activities.append(act)

    if len(activities) < 2:
        raise ValueError("Nenhuma parada válida foi geocodificada com sucesso.")

    # Ponto de retorno: usa retorno_* se fornecido, senão repete o depósito de saída
    if retorno_lat is not None and retorno_lng is not None and retorno_endereco is not None:
        retorno_parts = [p.strip() for p in retorno_endereco.split(",")]
        end_destination = _build_destination(
            latitude=retorno_lat,
            longitude=retorno_lng,
            street_address=retorno_parts[0] if retorno_parts else retorno_endereco,
            street_number=retorno_parts[1] if len(retorno_parts) > 1 and retorno_parts[1].replace("-", "").isdigit() else None,
            city=defaults.cidade,
            state=defaults.estado,
            country=defaults.pais,
        )
    else:
        end_destination = activities[0]["destination"]

    return {
        "activities": activities,
        "start_time": start_ms,
        "type": "OPTIMIZED",
        "optimization": {
            "working_hours": {
                "start_time": start_ms,
                "end_time": end_ms,
            },
            "num_vehicles": num_vehicles,
            "objectives": objectives,
            "end_route_destination": end_destination,
        },
    }