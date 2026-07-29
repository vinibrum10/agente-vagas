"""Registro automatico na planilha 'Controle de Candidaturas 2026'.
Usa o MESMO OAuth do gmail_alertas.py (escopo Sheets ja incluido em SCOPES).
Se existia token.json antigo, apague e reautorize.
"""
from datetime import datetime
from googleapiclient.discovery import build

from agent.gmail_alertas import _servico  # garante credenciais carregadas


def _sheets():
    from google.oauth2.credentials import Credentials
    from agent.gmail_alertas import BASE, SCOPES
    creds = Credentials.from_authorized_user_file(str(BASE / "token.json"), SCOPES)
    return build("sheets", "v4", credentials=creds)


def registrar(sheet_id: str, vaga: dict, score: int, status: str):
    enviada = status.startswith("ENVIADA")
    linha = [
        datetime.now().strftime("%d/%m/%Y"),
        vaga.get("titulo", ""),
        vaga.get("empresa", ""),
        vaga.get("local", ""),
        vaga.get("fonte", ""),
        score,
        status.splitlines()[0][:120],
        datetime.now().strftime("%d/%m/%Y") if enviada else "",
        "",
        "",
        vaga.get("url", ""),
        "enviada pelo agente (--auto)" if enviada else "aguardando envio manual",
    ]
    _sheets().spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range="A:L",
        valueInputOption="USER_ENTERED",
        body={"values": [linha]},
    ).execute()
