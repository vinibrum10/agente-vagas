"""Registro de candidaturas e banco de respostas de perguntas customizadas.

Backend principal: Supabase (tabelas vagas_candidaturas e vagas_respostas_banco
do projeto SGP/central-pessoal-adapta), com fallback automatico para o Google
Sheets ("Controle de Candidaturas 2026") se a escrita no Supabase falhar —
temporario, ate a migracao ficar estavel (depois removemos o Sheets de vez).
"""
import os
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher

from googleapiclient.discovery import build

from agent.gmail_alertas import _servico  # garante credenciais Google carregadas

ABA_BANCO = "Banco de Respostas"
COLUNAS_BANCO = ["Pergunta", "Tipo", "Opções", "Resposta", "Sempre_usar", "Ultima_vaga",
                 "Possível_Duplicata_De"]
N_COLUNAS = len(COLUNAS_BANCO)
LIMIAR_SUGESTAO = 0.6  # so para sugestao manual — nunca decide reaproveitar resposta sozinho

TABELA_CANDIDATURAS = "vagas_candidaturas"
TABELA_BANCO = "vagas_respostas_banco"
TABELA_PERFIL_FIXO = "vagas_perfil_fixo"


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


def classificar_campo_perfil(pergunta_normalizada: str) -> str | None:
    """Classifica uma pergunta pendente para um campo do "Meu Perfil de
    Candidatura" (fatos fixos, ja verificados pelo usuario).

    Aceita sinonimos bem mais amplos que o banco de respostas reativo: aqui
    sao so 8 campos conhecidos com resposta ja confirmada, nao um universo
    aberto de perguntas parecidas — o risco de uma classificacao errada e
    baixo e so afeta esses 8 conceitos especificos.
    """
    p = pergunta_normalizada
    palavras_p = set(p.split())

    def tem(*palavras: str) -> bool:
        """Frases (com espaco) casam por substring; palavra unica exige match
        exato de token — evita "escola" casar dentro de "escolaridade"."""
        for alvo in palavras:
            if " " in alvo:
                if alvo in p:
                    return True
            elif alvo in palavras_p:
                return True
        return False

    if tem("linkedin"):
        return "linkedin_url"
    if tem("salarial", "salario", "salary", "salaries"):
        return "piso_salarial"
    if tem("ingles", "english"):
        return "nivel_ingles"
    if tem("autorizado a trabalhar", "authorized to work", "work authorization",
           "documentacao para trabalhar", "elegivel para trabalhar", "legal to work"):
        return "autorizado_trabalhar_brasil"
    if tem("ano", "year") and tem("inicio", "start"):
        return "ano_inicio"
    if tem("ano", "year") and tem("termino", "fim", "conclusao", "end", "graduation"):
        return "ano_termino"
    if tem("escola", "instituicao", "universidade", "faculdade", "institution", "school"):
        return "escola"
    if tem("curso", "disciplina", "major", "field of study", "area de estudo", "course"):
        return "curso"
    return None


# ═══════════════════════ Supabase (backend principal) ═══════════════════════

def _supabase():
    """Cliente Supabase (service role, bypassa RLS) ou None se nao configurado."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key or key.startswith("coloque-"):
        return None
    from supabase import create_client
    return create_client(url, key)


def _supabase_user_id() -> str:
    user_id = os.environ.get("SUPABASE_USER_ID", "")
    if not user_id or user_id.startswith("coloque-"):
        raise RuntimeError("SUPABASE_USER_ID nao configurado no .env")
    return user_id


def _texto_observacoes(status: str) -> str:
    if status.startswith("ENVIADA"):
        return "enviada pelo agente (--auto)"
    if status.startswith("PRONTA_PARA_ENVIO"):
        return "aguardando confirmação no app"
    return "aguardando envio manual"


def _registrar_supabase(vaga: dict, score: int, status: str):
    client = _supabase()
    if client is None:
        raise RuntimeError("Supabase nao configurado (SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY)")
    user_id = _supabase_user_id()
    enviada = status.startswith("ENVIADA")
    link = vaga.get("url", "")
    payload = {
        "user_id": user_id,
        "data_prep": datetime.now().strftime("%Y-%m-%d"),
        "vaga": vaga.get("titulo", ""),
        "empresa": vaga.get("empresa", ""),
        "local": vaga.get("local", ""),
        "fonte": vaga.get("fonte", ""),
        "score": score,
        "status": status.splitlines()[0][:500],
        "data_envio": datetime.now().strftime("%Y-%m-%d") if enviada else None,
        "retorno": "",
        "link": link,
        "observacoes": _texto_observacoes(status),
    }

    # "Upsert" feito em Python (por link) — evita duplicar a mesma vaga a cada
    # rodada, sem exigir constraint unique nova no schema do SGP. Vagas sem
    # link (algumas vindas de alerta de e-mail) sempre viram linha nova, pois
    # nao ha chave estavel pra deduplicar.
    existente = None
    if link:
        resp = (client.table(TABELA_CANDIDATURAS).select("id")
                .eq("user_id", user_id).eq("link", link).limit(1).execute())
        if resp.data:
            existente = resp.data[0]["id"]

    if existente:
        client.table(TABELA_CANDIDATURAS).update(payload).eq("id", existente).execute()
    else:
        client.table(TABELA_CANDIDATURAS).insert(payload).execute()


def _buscar_perfil_fixo_supabase() -> dict | None:
    client = _supabase()
    if client is None:
        raise RuntimeError("Supabase nao configurado (SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY)")
    user_id = _supabase_user_id()
    resp = (client.table(TABELA_PERFIL_FIXO).select("*")
            .eq("user_id", user_id).limit(1).execute())
    return resp.data[0] if resp.data else None


def _ler_banco_supabase(client, user_id: str) -> list[dict]:
    resp = client.table(TABELA_BANCO).select("*").eq("user_id", user_id).execute()
    registros = []
    for row in resp.data or []:
        registros.append({
            "id": row["id"],
            "pergunta": row.get("pergunta", ""),
            "tipo": row.get("tipo", ""),
            "opcoes": row.get("opcoes", ""),
            "resposta": row.get("resposta", ""),
            "sempre_usar": bool(row.get("sempre_usar")),
            "vaga_origem": row.get("vaga_origem", ""),
            "empresa_origem": row.get("empresa_origem", ""),
            "possivel_duplicata": row.get("possivel_duplicata_de", ""),
        })
    return registros


def _consultar_resposta_supabase(pergunta_normalizada: str) -> dict | None:
    client = _supabase()
    if client is None:
        raise RuntimeError("Supabase nao configurado (SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY)")
    user_id = _supabase_user_id()
    for reg in _ler_banco_supabase(client, user_id):
        if normalizar(reg["pergunta"]) == pergunta_normalizada:
            return reg
    return None


def _registrar_pergunta_pendente_supabase(pergunta: str, tipo: str, opcoes: str, vaga_titulo: str, empresa: str):
    client = _supabase()
    if client is None:
        raise RuntimeError("Supabase nao configurado (SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY)")
    user_id = _supabase_user_id()
    pergunta_norm = normalizar(pergunta)
    registros = _ler_banco_supabase(client, user_id)
    if any(normalizar(reg["pergunta"]) == pergunta_norm for reg in registros):
        return

    melhor, melhor_score = None, 0.0
    for reg in registros:
        score = _similaridade(normalizar(reg["pergunta"]), pergunta_norm)
        if score > melhor_score:
            melhor, melhor_score = reg, score
    sugestao = melhor["pergunta"] if melhor and melhor_score >= LIMIAR_SUGESTAO else ""

    client.table(TABELA_BANCO).insert({
        "user_id": user_id,
        "pergunta": pergunta,
        "tipo": tipo,
        "opcoes": opcoes,
        "resposta": "",
        "sempre_usar": False,
        "vaga_origem": vaga_titulo,
        "empresa_origem": empresa,
        "possivel_duplicata_de": sugestao,
    }).execute()


# ═══════════════════════ Google Sheets (fallback temporario) ════════════════

def _sheets():
    from google.oauth2.credentials import Credentials
    from agent.gmail_alertas import BASE, SCOPES
    creds = Credentials.from_authorized_user_file(str(BASE / "token.json"), SCOPES)
    return build("sheets", "v4", credentials=creds)


def _garantir_aba_banco_sheets(sheet_id: str):
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
        range=f"'{ABA_BANCO}'!A1:G1",
        valueInputOption="USER_ENTERED",
        body={"values": [COLUNAS_BANCO]},
    ).execute()


def _ler_banco_sheets(sheet_id: str) -> list[dict]:
    _garantir_aba_banco_sheets(sheet_id)
    resp = _sheets().spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{ABA_BANCO}'!A2:G"
    ).execute()
    registros = []
    for i, linha in enumerate(resp.get("values", []), start=2):
        linha = linha + [""] * (N_COLUNAS - len(linha))
        registros.append({
            "linha": i,
            "pergunta": linha[0],
            "tipo": linha[1],
            "opcoes": linha[2],
            "resposta": linha[3],
            "sempre_usar": (linha[4] or "").strip().lower() == "sim",
            "vaga_origem": linha[5],
            "empresa_origem": "",  # Sheets nunca guardou empresa separada — so o Supabase tem
            "possivel_duplicata": linha[6],
        })
    return registros


def _consultar_resposta_sheets(sheet_id: str, pergunta_normalizada: str) -> dict | None:
    for reg in _ler_banco_sheets(sheet_id):
        if normalizar(reg["pergunta"]) == pergunta_normalizada:
            return reg
    return None


def _registrar_pergunta_pendente_sheets(sheet_id: str, pergunta: str, tipo: str, opcoes: str, vaga_titulo: str,
                                         empresa: str):
    # Sheets nao tem coluna propria de empresa (fallback temporario) — ignorado aqui.
    pergunta_norm = normalizar(pergunta)
    registros = _ler_banco_sheets(sheet_id)
    if any(normalizar(reg["pergunta"]) == pergunta_norm for reg in registros):
        return

    melhor, melhor_score = None, 0.0
    for reg in registros:
        score = _similaridade(normalizar(reg["pergunta"]), pergunta_norm)
        if score > melhor_score:
            melhor, melhor_score = reg, score
    sugestao = melhor["pergunta"] if melhor and melhor_score >= LIMIAR_SUGESTAO else ""

    _sheets().spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{ABA_BANCO}'!A:G",
        valueInputOption="USER_ENTERED",
        body={"values": [[pergunta, tipo, opcoes, "", "Não", vaga_titulo, sugestao]]},
    ).execute()


def _registrar_sheets(sheet_id: str, vaga: dict, score: int, status: str):
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
        _texto_observacoes(status),
    ]
    _sheets().spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range="A:L",
        valueInputOption="USER_ENTERED",
        body={"values": [linha]},
    ).execute()


# ═══════════════════════ API publica (Supabase com fallback) ════════════════

def buscar_perfil_fixo() -> dict | None:
    """Busca o Meu Perfil de Candidatura (fatos fixos verificados) no Supabase.
    Sem fallback no Sheets — e um conceito novo, so existe no Supabase. Retorna
    None se nao configurado, sem perfil salvo, ou qualquer falha de rede
    (o chamador simplesmente cai no fluxo reativo do banco de respostas)."""
    try:
        return _buscar_perfil_fixo_supabase()
    except Exception as e:
        print(f"[planilha] falha ao buscar perfil fixo (segue sem ele): {e}")
        return None


def registrar(sheet_id: str, vaga: dict, score: int, status: str):
    """Registra a candidatura em vagas_candidaturas (Supabase). Se falhar, cai
    no Google Sheets — fallback temporario ate a migracao ficar estavel."""
    try:
        _registrar_supabase(vaga, score, status)
        return
    except Exception as e:
        print(f"[planilha] Supabase falhou ao registrar, tentando Sheets como fallback: {e}")
    _registrar_sheets(sheet_id, vaga, score, status)


def consultar_resposta(sheet_id: str, pergunta_normalizada: str) -> dict | None:
    """Correspondencia EXATA apos normalizar — nunca aproximada. String curta e
    estruturalmente parecida (ex. "Escola" vs "Escolaridade") pontua alto em
    similaridade generica mesmo sendo campos distintos, entao similaridade
    nunca decide reaproveitar uma resposta sozinha."""
    try:
        return _consultar_resposta_supabase(pergunta_normalizada)
    except Exception as e:
        print(f"[planilha] Supabase falhou ao consultar, tentando Sheets como fallback: {e}")
    return _consultar_resposta_sheets(sheet_id, pergunta_normalizada)


def registrar_pergunta_pendente(sheet_id: str, pergunta: str, tipo: str, opcoes: str, vaga_titulo: str,
                                 empresa: str = ""):
    """Cria a pergunta pendente (Sempre_usar=Nao) so se for genuinamente inedita
    (match exato) — nunca reescreve linha ja existente, respondida ou nao."""
    try:
        _registrar_pergunta_pendente_supabase(pergunta, tipo, opcoes, vaga_titulo, empresa)
        return
    except Exception as e:
        print(f"[planilha] Supabase falhou ao registrar pendente, tentando Sheets como fallback: {e}")
    _registrar_pergunta_pendente_sheets(sheet_id, pergunta, tipo, opcoes, vaga_titulo, empresa)
