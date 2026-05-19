"""
Gera etiquetas de entrega em HTML a partir do Google Sheets, sem chamar a API Cobli.
Tamanho da etiqueta: 100mm x 40mm (impressora térmica NiceLabel).

Fluxo:
  1. Busca o N° Pedido informado nas abas "Rota_do_Dia" e "Expressa"
  2. Gera 1 etiqueta por volume (ex: 2 volumes → etiquetas "1/2" e "2/2")

Uso:
  python gerar_etiquetas.py
  python gerar_etiquetas.py --pedido 2054846
  python gerar_etiquetas.py --source xlsx --xlsx-path Teste_rotas.xlsx --pedido 2054846
"""
from __future__ import annotations

import argparse
import html
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from loguru import logger

from sheets_reader import read_google_sheet, read_xlsx

ABAS_BUSCA = ["Loja_Centro"]


# =====================================================================
# Helpers
# =====================================================================

def _safe_str(val) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return ""
    return str(val).strip()


def _format_phone(codigo: str) -> str:
    digits = "".join(c for c in (codigo or "") if c.isdigit())
    if not digits:
        return ""
    if digits.startswith("55") and len(digits) >= 12:
        digits = digits[2:]
    if len(digits) == 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    return digits


def _get_col(row: pd.Series, *keys: str) -> str:
    """Tenta cada chave em ordem e retorna o primeiro valor não-vazio encontrado."""
    for key in keys:
        val = _safe_str(row.get(key))
        if val:
            return val
    return ""


def _parse_volumes(val: str) -> int:
    try:
        n = int(float(val))
        return max(1, n)
    except (ValueError, TypeError):
        return 1


# =====================================================================
# CSS — etiqueta 100mm x 40mm
# =====================================================================

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: Arial, Helvetica, sans-serif;
  background: #d8d8d8;
  color: #000;
}

/* Visualização na tela */
.pagina {
  width: 100mm;
  min-height: 40mm;
  background: #fff;
  margin: 8mm auto;
  padding: 1.5mm 4mm;
  border: 1px solid #999;
  box-shadow: 0 2px 6px rgba(0,0,0,.2);
  display: flex;
  flex-direction: column;
  justify-content: space-evenly;
  position: relative;
}

/* Linha 1: NOME (esquerda) e N° PEDIDO (direita) */
.linha-topo {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 4mm;
}

.linha-topo .campo-nome {
  flex: 1;
  min-width: 0;
}

.linha-topo .campo-pedido {
  white-space: nowrap;
  flex-shrink: 0;
}

.campo {
  font-size: 11pt;
  font-weight: 700;
  line-height: 1.25;
  text-transform: uppercase;
}

.campo .rotulo {
  font-weight: 900;
}

.expressa-badge {
  position: absolute;
  bottom: 2mm;
  right: 3mm;
  font-size: 8pt;
  font-weight: 900;
  text-transform: uppercase;
  border: 1.5px solid #000;
  padding: 0 3px;
  line-height: 1.4;
}

/* ===== IMPRESSÃO ===== */
@media print {
  @page { size: 100mm 40mm; margin: 0; }

  body { background: #fff; }

  .pagina {
    width: 100mm;
    height: 40mm;
    min-height: unset;
    margin: 0;
    padding: 1.5mm 4mm;
    border: none;
    box-shadow: none;
    page-break-after: always;
    break-after: page;
  }

  .pagina:last-child {
    page-break-after: avoid;
    break-after: avoid;
  }
}
"""


# =====================================================================
# Renderização de uma etiqueta
# =====================================================================

def _render_etiqueta(row: pd.Series, vol_atual: int, vol_total: int) -> str:
    nome     = html.escape(_get_col(row, "NOME", "Nome", "nome"))
    endereco = html.escape(_get_col(row, "ENDEREÇO", "Endereço", "Endereco", "endereco"))
    numero   = html.escape(_get_col(row, "NUMERO", "Número", "Numero", "numero"))
    compl    = html.escape(_get_col(row, "COMPLEMENTO", "Complemento", "complemento"))
    telefone = html.escape(_format_phone(_get_col(row, "CODIGO", "Telefone", "telefone", "TELEFONE")))
    pedido   = html.escape(_get_col(row, "Nº Pedido", "N° Pedido", "nº pedido", "N Pedido"))
    tipo     = _get_col(row, "Tipo", "TIPO", "tipo")

    end_str    = endereco
    if numero:
        end_str += f", {numero}"

    volume_str   = f"{vol_atual}/{vol_total}"
    expressa_html = '<span class="expressa-badge">Expressa</span>' if tipo.lower() == "expressa" else ""

    return f"""
<div class="pagina">
  <div class="linha-topo">
    <div class="campo campo-nome"><span class="rotulo">NOME:</span> {nome}</div>
    <div class="campo campo-pedido"><span class="rotulo">N&deg; PEDIDO:</span> {pedido}</div>
  </div>
  <div class="campo"><span class="rotulo">ENDERE&Ccedil;O:</span> {end_str}</div>
  <div class="campo"><span class="rotulo">COMPLEMENTO:</span> {compl}</div>
  <div class="campo"><span class="rotulo">TELEFONE:</span> {telefone}</div>
  <div class="campo"><span class="rotulo">VOLUME:</span> {volume_str}</div>
  {expressa_html}
</div>"""


# =====================================================================
# Geração do HTML completo
# =====================================================================

def gerar_html_etiquetas(row: pd.Series) -> str:
    vol_total = _parse_volumes(_get_col(row, "Volumes", "VOLUMES", "volumes"))
    etiquetas = [_render_etiqueta(row, i, vol_total) for i in range(1, vol_total + 1)]

    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    pedido    = _get_col(row, "Nº Pedido", "N° Pedido", "nº pedido")

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <title>Etiqueta Pedido {html.escape(pedido)} — Pop House</title>
  <style>{_CSS}</style>
</head>
<body>
  {''.join(etiquetas)}
  <!-- Pedido {html.escape(pedido)} | {vol_total} etiqueta(s) | {gerado_em} -->
</body>
</html>"""


# =====================================================================
# Busca do pedido nas abas
# =====================================================================

def _buscar_pedido(pedido: str, dfs: dict[str, pd.DataFrame]) -> tuple[pd.Series | None, str]:
    """Retorna (linha encontrada, nome da aba) ou (None, '')."""
    pedido_norm = pedido.strip()
    for aba, df in dfs.items():
        col = next((c for c in df.columns if "pedido" in str(c).lower()), None)
        if col is None:
            continue
        # Converte para string, remove decimais (ex: "2058132.0" → "2058132")
        normalizado = df[col].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        mask = normalizado == pedido_norm
        if mask.any():
            return df[mask].iloc[0], aba
    return None, ""


# =====================================================================
# CLI
# =====================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gera etiquetas por N° de pedido (sem API).")
    p.add_argument("--source", choices=["xlsx", "sheets"], default="sheets")
    p.add_argument("--xlsx-path", default="")
    p.add_argument("--aba", default="", help="Aba da planilha a consultar (sobrescreve ABAS_BUSCA).")
    p.add_argument("--pedido", default="", help="N° do pedido a imprimir.")
    p.add_argument("--no-abrir", action="store_true")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if args.debug else "INFO")

    abas = [args.aba] if args.aba else ABAS_BUSCA

    print()
    print("=" * 50)
    print(f"  Etiquetas Pop House — {abas[0]}")
    print("=" * 50)
    print()

    # Configuração da fonte (resolvida uma vez só)
    if args.source == "xlsx":
        xlsx_path = args.xlsx_path
        if not xlsx_path:
            xlsx_path = input("Caminho do arquivo xlsx [Teste_rotas.xlsx]: ").strip() or "Teste_rotas.xlsx"
        if not Path(xlsx_path).exists():
            logger.error(f"Arquivo não encontrado: {xlsx_path}")
            return 1
        sheet_id = creds_path = None
    else:
        xlsx_path  = None
        sheet_id   = os.getenv("GOOGLE_SHEET_ID")
        creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "./credentials.json")
        if not sheet_id:
            logger.error("GOOGLE_SHEET_ID não configurado no .env.")
            return 1

    def _carregar_abas() -> dict[str, pd.DataFrame]:
        dfs: dict[str, pd.DataFrame] = {}
        if xlsx_path:
            for aba in abas:
                try:
                    dfs[aba] = read_xlsx(xlsx_path, tab=aba)
                except Exception:
                    logger.warning(f"Aba '{aba}' não encontrada no xlsx, ignorando.")
        else:
            for aba in abas:
                try:
                    dfs[aba] = read_google_sheet(sheet_id, aba, creds_path)
                except Exception as e:
                    logger.warning(f"Aba '{aba}' não acessível: {e}")
        return dfs

    # Loop principal — relê a planilha a cada pedido
    while True:
        pedido = args.pedido or input("\nN° do Pedido (ou ENTER para sair): ").strip()
        if not pedido:
            print("Saindo.")
            return 0

        logger.info("Consultando planilha...")
        dfs = _carregar_abas()
        if not dfs:
            logger.error("Nenhuma aba carregada. Verifique a conexão e tente novamente.")
            args.pedido = ""
            continue

        row, aba_encontrada = _buscar_pedido(pedido, dfs)

        if row is None:
            abas_str = " e ".join(f'"{a}"' for a in dfs)
            logger.warning(f"Pedido {pedido!r} não encontrado nas abas {abas_str}.")
            args.pedido = ""
            continue

        vol_total = _parse_volumes(_safe_str(row.get("Volumes")))
        nome      = _safe_str(row.get("NOME"))
        logger.success(f"Pedido {pedido} encontrado na aba '{aba_encontrada}' — {nome} — {vol_total} volume(s).")

        html_content = gerar_html_etiquetas(row)

        output_dir = Path("rotas")
        output_dir.mkdir(parents=True, exist_ok=True)
        filename   = f"etiqueta_pedido_{pedido}.html"
        output     = output_dir / filename
        output.write_text(html_content, encoding="utf-8")

        logger.success(f"Arquivo gerado: {output.absolute()}")

        if not args.no_abrir:
            webbrowser.open(output.absolute().as_uri())

        if args.pedido:
            return 0  # modo CLI: encerra após o primeiro pedido

        args.pedido = ""  # modo interativo: volta ao topo do loop


if __name__ == "__main__":
    sys.exit(main())
