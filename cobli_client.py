"""Cliente GraphQL para a API interna da Cobli (a mesma usada pelo painel web)."""
from __future__ import annotations

import json
from typing import Any

import requests
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


# Mutation extraída do tráfego real do painel da Cobli.
# Cria uma ou mais rotas e devolve a estrutura completa criada.
CREATE_ROUTES_MUTATION = """
mutation RoutingSettings($routeSettings: RoutingSettings) {
  createRoutes(input: $routeSettings) {
    id
    name
    activities {
      id
      name
      type
      position
      destination {
        coordinate { latitude longitude }
        city
        country
        neighborhood
        postal_code
        state
        street_address
        street_complement
        street_number
      }
      start_time
      end_time
      planned_duration
      planned_distance_in_meters
      route_id
      phone_number
      load_size
    }
    type
    status
    planned_duration
    planned_distance_in_meters
    start_time
    end_time
    vehicle_id
    driver_id
  }
}
""".strip()


class CobliAPIError(Exception):
    def __init__(self, status_code: int, body: Any, headers: dict | None = None, raw_text: str = ""):
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}
        self.raw_text = raw_text
        super().__init__(f"Cobli API retornou {status_code}: {body}")


class CobliGraphQLError(Exception):
    """Erro reportado pelo GraphQL com status 200 mas array `errors` populado."""
    def __init__(self, errors: list[dict]):
        self.errors = errors
        msg = "; ".join(e.get("message", str(e)) for e in errors)
        super().__init__(f"GraphQL errors: {msg}")


class CobliClient:
    """
    Cliente para a API GraphQL da Cobli em https://api.cobli.co/graphql.

    Authenticação: a API espera um session ID no header `cobli-api-sid`, que
    é o mesmo cookie/session da UI logada. Pra obter:
      1. Acesse painel.cobli.co logado
      2. F12 > Application > Cookies > Copie o valor do cookie da sessão
      3. OU abra Network > qualquer requisição > Headers > cobli-api-sid

    Esse SID expira (geralmente 12-24h). Pra automação contínua, considere
    fazer login programático (mais código) ou abrir um ticket com a Cobli
    pedindo se a chave de API REST funciona em GraphQL.
    """

    URL = "https://api.cobli.co/graphql"

    def __init__(
        self,
        sid: str | None = None,
        api_key: str | None = None,
        timeout: int = 60,
        dashboard_version: str = "10.29.0",
    ):
        if not sid and not api_key:
            raise ValueError(
                "Forneça pelo menos um: COBLI_API_SID (do navegador) ou "
                "COBLI_API_KEY (gerada no painel)."
            )

        self.session = requests.Session()
        # Headers comuns que a UI envia
        self.session.headers.update({
            "accept": "*/*",
            "content-type": "application/json",
            "origin": "https://painel.cobli.co",
            "referer": "https://painel.cobli.co/",
            "cobli-dashboard-version": dashboard_version,
        })
        # Tenta primeiro com o sid da UI; se não tiver, manda a key (caso a Cobli aceite)
        if sid:
            self.session.headers["cobli-api-sid"] = sid
        if api_key:
            self.session.headers["cobli-api-key"] = api_key

        self.timeout = timeout

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    )
    def graphql(self, query: str, variables: dict[str, Any], operation_name: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query, "variables": variables}
        if operation_name:
            body["operationName"] = operation_name

        logger.debug(f"POST {self.URL}  (operation: {operation_name})")
        resp = self.session.post(self.URL, json=body, timeout=self.timeout)

        logger.debug(f"Status: {resp.status_code} {resp.reason}")
        logger.debug(f"Response body (raw, len={len(resp.text)}): {resp.text[:1000]!r}")

        try:
            parsed = resp.json()
        except (json.JSONDecodeError, ValueError):
            parsed = None

        if resp.status_code >= 400:
            raise CobliAPIError(
                status_code=resp.status_code,
                body=parsed,
                headers=dict(resp.headers),
                raw_text=resp.text,
            )

        if isinstance(parsed, dict) and parsed.get("errors"):
            raise CobliGraphQLError(parsed["errors"])

        return parsed if isinstance(parsed, dict) else {}

    def create_routes(self, route_settings: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Cria uma ou mais rotas. Recebe o objeto `routeSettings` completo
        (com `activities`, `start_time`, `type`, `optimization`, etc.)
        e devolve a lista de rotas criadas.
        """
        result = self.graphql(
            query=CREATE_ROUTES_MUTATION,
            variables={"routeSettings": route_settings},
            operation_name="RoutingSettings",
        )
        data = result.get("data", {})
        routes = data.get("createRoutes")
        if routes is None:
            raise CobliGraphQLError([{"message": "Resposta sem campo 'createRoutes'."}])
        return routes if isinstance(routes, list) else [routes]
