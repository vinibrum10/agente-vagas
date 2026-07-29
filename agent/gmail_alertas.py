"""Parser de alertas de vaga do Gmail (Indeed Match + LinkedIn + boards genericos).

Formato REAL dos e-mails do Indeed (verificado jul/2026):
  Remetente: donotreply@match.indeed.com
  Assunto:   "{CARGO} na empresa {EMPRESA}"

Setup: console.cloud.google.com -> ativar Gmail API e Sheets API ->
OAuth Client ID (Desktop) -> credentials.json na raiz. 1a execucao autoriza no navegador.
"""
import base64
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/spreadsheets"]
BASE = Path(__file__).parent.parent
SEEN = BASE / "vagas_vistas.json"

RE_ASSUNTO = re.compile(r"^(?P<cargo>.+?) na empresa (?P<empresa>.+)$")
RE_SALARIO = re.compile(r"R\$\s?([\d.,]+)(?:\s*[–-]\s*R\$\s?([\d.,]+))?\s*por m[eê]s", re.I)
RE_JK = re.compile(r"(?:jk=|/rc/clk\?jk=)([a-f0-9]{12,20})")


def _servico():
    creds = None
    token = BASE / "token.json"
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(BASE / "credentials.json"), SCOPES)
            creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def _corpo_texto(payload) -> str:
    if payload.get("parts"):
        return " ".join(_corpo_texto(p) for p in payload["parts"])
    data = payload.get("body", {}).get("data")
    if not data:
        return ""
    raw = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    if payload.get("mimeType") == "text/html":
        return BeautifulSoup(raw, "html.parser").get_text(" ", strip=True) + " " + raw
    return raw


def _salario_min(texto: str):
    m = RE_SALARIO.search(texto)
    if not m:
        return None
    try:
        return float(m.group(1).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def coletar_alertas(salario_minimo: float, dias: int = 2) -> list[dict]:
    svc = _servico()
    vistos = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    query = f"from:match.indeed.com newer_than:{dias}d"
    resp = svc.users().messages().list(userId="me", q=query, maxResults=50).execute()

    vagas = []
    for ref in resp.get("messages", []):
        if ref["id"] in vistos:
            continue
        msg = svc.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        m = RE_ASSUNTO.match(headers.get("Subject", ""))
        if not m:
            continue

        corpo = _corpo_texto(msg["payload"])
        sal = _salario_min(corpo)
        jk = RE_JK.search(corpo)
        url = f"https://br.indeed.com/viewjob?jk={jk.group(1)}" if jk else ""

        if sal is not None and sal < salario_minimo:
            vistos.add(ref["id"])
            continue

        vagas.append({
            "fonte": "indeed-email",
            "empresa": m.group("empresa").strip(),
            "titulo": m.group("cargo").strip(),
            "local": "",
            "url": url,
            "descricao": corpo[:8000],
            "id": ref["id"],
            "salario_divulgado": sal,
        })
        vistos.add(ref["id"])

    SEEN.write_text(json.dumps(sorted(vistos)))
    print(f"[gmail] {len(vagas)} vagas novas dos alertas Indeed")
    return vagas


RE_LI_VIEW = re.compile(r"https://www\.linkedin\.com/(?:comm/)?jobs/view/(\d+)")


def coletar_alertas_linkedin(dias: int = 2) -> list[dict]:
    svc = _servico()
    vistos = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    query = f"from:(jobs-noreply@linkedin.com OR jobalerts-noreply@linkedin.com) newer_than:{dias}d"
    resp = svc.users().messages().list(userId="me", q=query, maxResults=20).execute()

    vagas, ids_vaga = [], set()
    for ref in resp.get("messages", []):
        if ref["id"] in vistos:
            continue
        msg = svc.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        html = _corpo_texto(msg["payload"])
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            m = RE_LI_VIEW.search(a["href"])
            if not m or m.group(1) in ids_vaga:
                continue
            texto = a.get_text(" ", strip=True)
            if len(texto) < 5:
                continue
            ids_vaga.add(m.group(1))
            vagas.append({
                "fonte": "linkedin-email",
                "empresa": "",
                "titulo": texto[:120],
                "local": "",
                "url": f"https://www.linkedin.com/jobs/view/{m.group(1)}",
                "descricao": texto,
                "id": f"li-{m.group(1)}",
            })
        vistos.add(ref["id"])

    SEEN.write_text(json.dumps(sorted(vistos)))
    print(f"[gmail] {len(vagas)} vagas novas dos alertas LinkedIn")
    return vagas


def coletar_alertas_genericos(boards: list[dict], dias: int = 3) -> list[dict]:
    svc = _servico()
    vistos = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    vagas, urls = [], set()

    for board in boards:
        query = f"from:{board['sender']} newer_than:{dias}d"
        resp = svc.users().messages().list(userId="me", q=query, maxResults=15).execute()
        for ref in resp.get("messages", []):
            if ref["id"] in vistos:
                continue
            msg = svc.users().messages().get(userId="me", id=ref["id"], format="full").execute()
            soup = BeautifulSoup(_corpo_texto(msg["payload"]), "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                texto = a.get_text(" ", strip=True)
                if board["link_contem"] not in href or len(texto) < 8 or href in urls:
                    continue
                urls.add(href)
                vagas.append({
                    "fonte": f"{board['nome']}-email",
                    "empresa": "",
                    "titulo": texto[:120],
                    "local": "",
                    "url": href.split("?utm")[0],
                    "descricao": texto,
                    "id": f"{board['nome']}-{abs(hash(href))}",
                })
            vistos.add(ref["id"])

    SEEN.write_text(json.dumps(sorted(vistos)))
    print(f"[gmail] {len(vagas)} vagas novas de boards genericos")
    return vagas
