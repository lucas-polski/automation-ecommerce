"""
Leitor da planilha. Duas fontes:
- xlsx local (útil pra teste/debug)
- Google Sheets via gspread + service account (produção)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def read_xlsx(path: str | Path, tab: str = "Rota_do_Dia") -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=tab)
    # Remove colunas auxiliares (Unnamed, Data Filtro e a coluna com a data)
    drop_cols = [
        c for c in df.columns
        if str(c).startswith("Unnamed")
        or str(c) == "Data Filtro"
        or hasattr(c, "year")  # datetime usado como header
    ]
    return df.drop(columns=drop_cols, errors="ignore")


def read_google_sheet(
    sheet_id: str,
    tab: str,
    credentials_path: str | Path,
) -> pd.DataFrame:
    """
    Lê uma aba do Google Sheets via service account.

    Pré-requisitos:
      1. Criar service account no Google Cloud Console
      2. Habilitar a Google Sheets API
      3. Compartilhar a planilha com o e-mail da service account (permissão de leitura)
      4. Salvar o JSON de credenciais em GOOGLE_CREDENTIALS_PATH
    """
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds = Credentials.from_service_account_file(str(credentials_path), scopes=scopes)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(tab)
    
    data = ws.get_all_values()
    
    if not data:
        return pd.DataFrame()

    # 2. Criamos o DataFrame usando a primeira linha como cabeçalho
    # O Pandas automaticamente nomeia colunas vazias como "Unnamed: X" 
    # e resolve duplicados adicionando .1, .2, etc.
    df = pd.DataFrame(data[1:], columns=data[0])

    # 3. Removemos colunas totalmente vazias ou sem nome (o erro [''] que você teve)
    # Isso limpa aquelas colunas "fantasmas" à direita dos dados
    df = df.loc[:, ~df.columns.str.contains('^$|^Unnamed')]

    return df
    
    return pd.DataFrame(rows)
