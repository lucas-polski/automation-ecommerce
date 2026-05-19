"""
Orquestrador da automação Cobli — Pophouse.

Modos de uso:

  1. Interativo (recomendado para o operacional do dia a dia):
       python main.py
     O script pergunta data, loja de saída/retorno e geocoder.

  2. Linha de comando (recomendado pra automação):
       python main.py --data 12/05/2026 --loja 1 --no-geocoder

  3. Flags úteis:
       --dry-run             só monta o payload, não chama a API
       --debug               log detalhado
       --no-abrir            não abre o HTML automaticamente
       --com-load-size       envia volumes pra Cobli (cuidado: pode dar 422)
       --com-notas           envia observações como notas pro motorista
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from loguru import logger

from cobli_client import CobliAPIError, CobliClient, CobliGraphQLError
from folha_rota import gerar_folha_rota
from lojas import LOJAS
from mapper import Defaults, dataframe_to_route_settings
from sheets_reader import read_google_sheet, read_xlsx

PERIODOS = {
    "manha": {
        "nome": "Manhã",
        "horario_inicio": "08:00",
        "horario_fim": "13:00",
        "valores": ["manhã", "manha"],
    },
    "tarde": {
        "nome": "Tarde",
        "horario_inicio": "13:00",
        "horario_fim": "18:00",
        "valores": ["tarde"],
    },
}


# =====================================================================
# Modo interativo
# =====================================================================

def _print_banner() -> None:
    print()
    print("=" * 60)
    print("  Roteirização Cobli — Pophouse")
    print("=" * 60)
    print()


def _ask_data() -> date:
    """Pergunta a data da rota, com hoje como sugestão default."""
    hoje = date.today()
    sugestao = hoje.strftime("%d/%m/%Y")
    while True:
        resp = input(f"Data da rota (DD/MM/AAAA) [{sugestao}]: ").strip()
        if not resp:
            return hoje
        try:
            return datetime.strptime(resp, "%d/%m/%Y").date()
        except ValueError:
            print("  ✗ Data inválida. Use DD/MM/AAAA (ex: 12/05/2026).\n")


def _ask_loja(titulo: str, default: str = "1") -> dict:
    """Pergunta qual loja para o ponto indicado."""
    print()
    print(titulo)
    for chave, loja in LOJAS.items():
        print(f"  {chave}) {loja['nome']}  ({loja['endereco'].split(',')[0]}, {loja['endereco'].split(',')[1].strip()})")
    print()

    while True:
        resp = input(f"Opção [{default}]: ").strip() or default
        if resp in LOJAS:
            return LOJAS[resp]
        print(f"  ✗ Opção inválida. Escolha entre {', '.join(LOJAS.keys())}.\n")


def _ask_periodo() -> dict:
    """Pergunta o período da rota e retorna a config do período."""
    print()
    print("Período da rota:")
    for chave, p in PERIODOS.items():
        print(f"  {'1' if chave == 'manha' else '2'}) {p['nome']}  ({p['horario_inicio']} às {p['horario_fim']})")
    print()
    opcoes = {"1": "manha", "m": "manha", "manhã": "manha", "manha": "manha",
              "2": "tarde", "t": "tarde", "tarde": "tarde"}
    while True:
        resp = input("Opção [1]: ").strip().lower() or "1"
        if resp in opcoes:
            return PERIODOS[opcoes[resp]]
        print("  ✗ Opção inválida. Digite 1 (Manhã) ou 2 (Tarde).\n")


def _ask_sim_nao(pergunta: str, default: bool = False) -> bool:
    """Pergunta sim/não com default explícito."""
    suf = "s/N" if not default else "S/n"
    while True:
        resp = input(f"{pergunta} ({suf}): ").strip().lower()
        if not resp:
            return default
        if resp in ("s", "sim", "y", "yes"):
            return True
        if resp in ("n", "nao", "não", "no"):
            return False
        print("  ✗ Responda com 's' ou 'n'.\n")


def coletar_inputs_interativo() -> dict:
    """Roda o modo interativo e devolve um dict com as escolhas."""
    _print_banner()
    data_rota    = _ask_data()
    loja_saida   = _ask_loja("Ponto de saída:")
    loja_retorno = _ask_loja("Ponto de retorno:", default="1")
    periodo      = _ask_periodo()

    print()
    print(f"✓ Data:    {data_rota.strftime('%d/%m/%Y')}")
    print(f"✓ Saída:   {loja_saida['nome']}")
    print(f"✓ Retorno: {loja_retorno['nome']}")
    print(f"✓ Período: {periodo['nome']}  ({periodo['horario_inicio']} às {periodo['horario_fim']})")
    print()

    if not _ask_sim_nao("Confirmar e criar a rota na Cobli?", default=True):
        print("Operação cancelada.")
        sys.exit(0)

    return {"data_rota": data_rota, "loja": loja_saida, "loja_retorno": loja_retorno, "periodo": periodo}


# =====================================================================
# CLI
# =====================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cria rotas otimizadas na Cobli a partir de uma planilha.")
    p.add_argument(
        "--source", choices=["xlsx", "sheets"], default="sheets",
        help="Fonte dos dados (default: sheets).",
    )
    p.add_argument("--xlsx-path", help="Caminho do xlsx (quando --source xlsx).")
    p.add_argument("--tab", default="Rota_do_Dia", help="Nome da aba.")
    p.add_argument("--data", help="Data da rota DD/MM/AAAA (se omitido, entra em modo interativo).")
    p.add_argument(
        "--loja", choices=list(LOJAS.keys()),
        help="Loja de saída: " + ", ".join(f"{k}={v['nome']}" for k, v in LOJAS.items()),
    )
    p.add_argument(
        "--loja-retorno", choices=list(LOJAS.keys()), default="1",
        help="Loja de retorno (default: 1=Centro): " + ", ".join(f"{k}={v['nome']}" for k, v in LOJAS.items()),
    )
    p.add_argument("--periodo", choices=["manha", "tarde"], help="Período: manha (08:00-12:20) ou tarde (13:00-18:00).")
    p.add_argument("--horario-inicio", default="", help="HH:MM — sobrescreve o horário do período.")
    p.add_argument("--horario-fim", default="", help="HH:MM — sobrescreve o horário do período.")
    p.add_argument("--duracao-parada", type=int, default=10, help="Min por parada (default 10).")
    p.add_argument("--num-veiculos", type=int, default=1, help="Quantos veículos (default 1).")
    p.add_argument("--dry-run", action="store_true", help="Não chama a API, só imprime o payload.")
    p.add_argument("--no-geocoder", action="store_true", help="Exige LATITUDE/LONGITUDE na planilha.")
    p.add_argument("--com-load-size", action="store_true", help="Envia Volumes na API (pode dar 422).")
    p.add_argument("--com-notas", action="store_true", help="Envia Observação/Pedido/Complemento como notas.")
    p.add_argument("--no-abrir", action="store_true", help="Não abrir o HTML automaticamente.")
    p.add_argument("--debug", action="store_true", help="Log detalhado.")
    return p.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if args.debug else "INFO")

    # Decide modo interativo vs CLI
    if args.data and args.loja:
        try:
            data_rota = datetime.strptime(args.data, "%d/%m/%Y").date()
        except ValueError:
            logger.error("Data inválida. Use DD/MM/AAAA.")
            return 2
        loja         = LOJAS[args.loja]
        loja_retorno = LOJAS[args.loja_retorno] if args.loja_retorno else LOJAS["1"]
        periodo      = PERIODOS[args.periodo] if args.periodo else None
    else:
        escolhas     = coletar_inputs_interativo()
        data_rota    = escolhas["data_rota"]
        loja         = escolhas["loja"]
        loja_retorno = escolhas["loja_retorno"]
        periodo      = escolhas["periodo"]

    # Lê DataFrame
    if args.source == "xlsx":
        if not args.xlsx_path:
            logger.error("--xlsx-path é obrigatório quando --source xlsx.")
            return 2
        df = read_xlsx(args.xlsx_path, tab=args.tab)
    else:
        sheet_id = os.getenv("GOOGLE_SHEET_ID")
        creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "./credentials.json")
        if not sheet_id:
            logger.error("GOOGLE_SHEET_ID não configurado no .env.")
            return 2
        df = read_google_sheet(sheet_id, args.tab, creds_path)

    logger.info(f"Carregadas {len(df)} linhas da aba '{args.tab}'.")

    # Filtra por período se a coluna existir
    col_periodo = next((c for c in df.columns if c.strip().lower() in ("período", "periodo")), None)
    if col_periodo and periodo:
        df_filtrado = df[
            df[col_periodo].astype(str).str.strip().str.lower().str.normalize("NFKD")
            .str.encode("ascii", errors="ignore").str.decode("ascii")
            .isin([v.replace("ã", "a") for v in periodo["valores"]])
        ].copy()
        n_total = len(df[df["NOME"].notna() & (df["NOME"].astype(str).str.strip() != "")])
        logger.info(
            f"Período {periodo['nome']}: {len(df_filtrado)} entrega(s) filtrada(s) "
            f"(de {n_total} na planilha)."
        )
        df = df_filtrado
    elif not col_periodo:
        logger.warning("Coluna 'Período' não encontrada — usando todas as linhas.")

    if df.empty:
        logger.error(f"Nenhuma entrega encontrada para o período {periodo['nome'] if periodo else 'selecionado'}.")
        return 1

    n_esperado = len(df[df["NOME"].notna() & (df["NOME"].astype(str).str.strip() != "")])

    # Horários: período > CLI > default
    h_ini_str = args.horario_inicio or (periodo["horario_inicio"] if periodo else "08:00")
    h_fim_str = args.horario_fim    or (periodo["horario_fim"]    if periodo else "18:00")
    try:
        h_ini = datetime.strptime(h_ini_str, "%H:%M").time()
        h_fim = datetime.strptime(h_fim_str, "%H:%M").time()
    except ValueError:
        logger.error("Horários inválidos. Use HH:MM (ex: 08:00).")
        return 2

    defaults = Defaults(
        cidade=os.getenv("CIDADE_PADRAO", "Curitiba"),
        estado=os.getenv("ESTADO_PADRAO", "PR"),
        pais=os.getenv("PAIS_PADRAO", "BR"),
        duracao_parada_min=args.duracao_parada,
        horario_inicio=h_ini,
        horario_fim=h_fim,
    )

    try:
        route_settings = dataframe_to_route_settings(
            df,
            deposito_endereco=loja["endereco"],
            deposito_lat=loja["latitude"],
            deposito_lng=loja["longitude"],
            retorno_endereco=loja_retorno["endereco"],
            retorno_lat=loja_retorno["latitude"],
            retorno_lng=loja_retorno["longitude"],
            data_rota=data_rota,
            defaults=defaults,
            num_vehicles=args.num_veiculos,
            use_geocoder=not args.no_geocoder,
            incluir_load_size=args.com_load_size,
            incluir_additional_info=args.com_notas,
        )
    except ValueError as e:
        logger.error(f"Erro ao montar payload: {e}")
        return 1

    n_paradas = len(route_settings["activities"]) - 1
    logger.info(f"Payload com {n_paradas} parada(s) construído (saindo de {loja['nome']}).")

    if args.debug:
        print(json.dumps(route_settings, indent=2, ensure_ascii=False))

    if args.dry_run:
        logger.warning("Modo --dry-run: nada foi enviado para a Cobli.")
        return 0

    sid = os.getenv("COBLI_API_SID")
    api_key = os.getenv("COBLI_API_KEY")
    if not (sid or api_key):
        logger.error("Configure COBLI_API_SID (preferido) ou COBLI_API_KEY no .env.")
        return 2

    client = CobliClient(sid=sid, api_key=api_key)

    try:
        rotas = client.create_routes(route_settings)
        logger.success(f"{len(rotas)} rota(s) criada(s) na Cobli!")
        for r in rotas:
            logger.info(f"  ID: {r.get('id')}  Status: {r.get('status')}  Paradas: {len(r.get('activities', []))}")

        # Identifica entregas que não foram incluídas na rota
        phones_retornados = {
            "".join(c for c in (a.get("phone_number") or "") if c.isdigit())
            for r in rotas
            for a in r.get("activities", [])
            if a.get("type") == "STOP"
        }

        def _norm_codigo(val) -> str:
            digits = "".join(c for c in str(val or "") if c.isdigit())
            return ("55" + digits) if digits and not digits.startswith("55") else digits

        nao_incluidos = [
            row for _, row in df.iterrows()
            if str(row.get("NOME", "") or "").strip()
            and _norm_codigo(row.get("CODIGO", "")) not in phones_retornados
        ]

        if nao_incluidos:
            periodo_nome   = periodo["nome"] if periodo else "selecionado"
            h_fim_display  = periodo["horario_fim"] if periodo else h_fim_str
            print()
            logger.warning("=" * 60)
            logger.warning(
                f"  ATENÇÃO: {len(nao_incluidos)} entrega(s) não cabem no período "
                f"{periodo_nome} (limite {h_fim_display})."
            )
            logger.warning("  Mova os pedidos abaixo para a próxima rota:")
            logger.warning("-" * 60)
            for row in nao_incluidos:
                nome    = str(row.get("NOME", "") or "").strip()
                end_str = str(row.get("ENDEREÇO", "") or "").strip()
                numero  = str(row.get("NUMERO", "") or "").strip()
                pedido  = str(row.get("Nº Pedido", "") or "").strip()
                if numero:
                    end_str = f"{end_str}, {numero}"
                logger.warning(f"  • {nome}  |  {end_str}  |  Pedido: {pedido}")
            logger.warning("=" * 60)
            print()

        # Gera a folha de rota imprimível pro motorista
        try:
            output_path = gerar_folha_rota(
                rotas=rotas,
                df_original=df,
                data_rota=data_rota,
            )
            logger.info(f"Folha gerada: {output_path.absolute()}")
            if not args.no_abrir:
                webbrowser.open(output_path.absolute().as_uri())
        except Exception as e:
            logger.warning(f"Rota criada mas falhei ao gerar a folha: {e}")

        return 0
    except CobliGraphQLError as e:
        logger.error("API retornou erros GraphQL:")
        for err in e.errors:
            logger.error(f"  - {err.get('message', err)}")
        return 1
    except CobliAPIError as e:
        logger.error(f"API retornou {e.status_code}.")
        logger.error(f"Body cru ({len(e.raw_text)} chars): {e.raw_text[:500]!r}")
        if e.body is not None:
            print(json.dumps(e.body, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())