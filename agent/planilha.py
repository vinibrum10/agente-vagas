"""Registro automatico na planilha 'Controle de Candidaturas 2026'.
Usa o MESMO OAuth do gmail_alertas.py (escopo Sheets ja incluido em SCOPES).
Se existia token.json antigo, apague e reautorize.
"""
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher

from googleapiclient.discovery import build

from agent.gmail_alertas import _servico  # garante credenciais carregadas

ABA_BANCO = "Banco de Respostas"
COLUNAS_BANCO = ["Pergunta", "Tipo", "Opções", "Resposta", "Sempre_usar", "Ultima_vaga"]
LIMIAR_SIMILARIDADE = 0.6


def _sheets():
    from google.oauth2.credentials import Credentials
    from agent.gmail_alertas import BASE, SCOPES
    creds = Credentials.from_authorized_user_file(str(BASE / "token.json"), SCOPES)
    return build("sheets", "v4", credentials=creds)


def normalizar(texto: str) -> str:
    """minusculas, sem acento, sem pontuacao, espacos normalizados."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ASCII", "ignore").decode("ASCII")
    t = t.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _similaridade(a: str, b: str) -> float:
    """Combina similaridade de sequencia com sobreposicao de palavras (Jaccard)."""
    seq = SequenceMatcher(None, a, b).ratio()
    pa, pb = set(a.split()), set(b.split())
    jaccard = len(pa & pb) / len(pa | pb) if (pa and pb) else 0.0
    return max(seq, jaccard)


def _garantir_aba_banco(sheet_id: str):
    """Cria a aba 'Banco de Respostas' com cabecalho, se ainda nao existir."""
    svc = _sheets()
    meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
    abas = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if ABA_BANCO in abas:
        return
    svc.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": ABA_BANCO}}}]},
    ).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{ABA_BANCO}'!A1:F1",
        valueInputOption="USER_ENTERED",
        body={"values": [COLUNAS_BANCO]},
    ).execute()


def _ler_banco(sheet_id: str) -> list[dict]:
    _garantir_aba_banco(sheet_id)
    resp = _sheets().spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{ABA_BANCO}'!A2:F"
    ).execute()
    registros = []
    for i, linha in enumerate(resp.get("values", []), start=2):
        linha = linha + [""] * (6 - len(linha))
        registros.append({
            "linha": i,
            "pergunta": linha[0],
            "tipo": linha[1],
            "opcoes": linha[2],
            "resposta": linha[3],
            "sempre_usar": linha[4],
            "ultima_vaga": linha[5],
        })
    return registros


def consultar_resposta(sheet_id: str, pergunta_normalizada: str) -> dict | None:
    """Busca por similaridade aproximada no Banco de Respostas (nao precisa ser exata)."""
    melhor, melhor_score = None, 0.0
    for reg in _ler_banco(sheet_id):
        score = _similaridade(normalizar(reg["pergunta"]), pergunta_normalizada)
        if score > melhor_score:
            melhor, melhor_score = reg, score
    return melhor if melhor and melhor_score >= LIMIAR_SIMILARIDADE else None


def registrar_pergunta_pendente(sheet_id: str, pergunta: str, tipo: str, opcoes: str, vaga_titulo: str):
    """Cria a pergunta no banco (Sempre_usar=Nao) apenas se for genuinamente inedita.

    Se a pergunta ja existe (respondida ou nao), NAO escreve nada — nem atualiza
    Ultima_vaga. Qualquer escrita repetida dispararia de novo as Regras de
    notificacao nativas do Google Sheets (que o usuario configura para avisar
    por e-mail a cada linha nova), gerando notificacao duplicada para a mesma
    pergunta pendente.
    """
    pergunta_norm = normalizar(pergunta)
    ja_existe = any(
        _similaridade(normalizar(reg["pergunta"]), pergunta_norm) >= LIMIAR_SIMILARIDADE
        for reg in _ler_banco(sheet_id)
    )
    if ja_existe:
        return
    _sheets().spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{ABA_BANCO}'!A:F",
        valueInputOption="USER_ENTERED",
        body={"values": [[pergunta, tipo, opcoes, "", "Não", vaga_titulo]]},
    ).execute()


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
