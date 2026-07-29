"""Coleta vagas de APIs publicas (sem login, sem scraping fragil)."""
import requests

UA = {"User-Agent": "Mozilla/5.0 (agente-vagas-pessoal)"}


def greenhouse(board: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    r = requests.get(url, headers=UA, timeout=30)
    if r.status_code != 200:
        return []
    vagas = []
    for j in r.json().get("jobs", []):
        vagas.append({
            "fonte": "greenhouse",
            "empresa": board,
            "titulo": j.get("title", ""),
            "local": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "descricao": j.get("content", ""),
            "id": str(j.get("id")),
        })
    return vagas


def gupy(slug: str) -> list[dict]:
    url = f"https://{slug}.gupy.io/api/v1/jobs?jobBoardSource=gupy_portal&limit=100"
    try:
        r = requests.get(url, headers=UA, timeout=30)
        data = r.json().get("data", [])
    except Exception:
        return []
    vagas = []
    for j in data:
        vagas.append({
            "fonte": "gupy",
            "empresa": slug,
            "titulo": j.get("name", ""),
            "local": f"{j.get('city', '')} {j.get('state', '')} {'(remoto)' if j.get('isRemoteWork') else ''}",
            "url": j.get("jobUrl", ""),
            "descricao": j.get("description", "") or "",
            "id": str(j.get("id")),
        })
    return vagas


def filtro_rapido(vaga: dict, cfg: dict) -> bool:
    f = cfg["filtros"]
    t = vaga["titulo"].lower()
    loc = vaga["local"].lower()
    if any(b in t for b in f["niveis_bloqueados"]):
        return False
    if not any(k in t for k in f["keywords_positivas"]):
        return False
    loc_ok = any(l in loc for l in f["localidades"]) or loc.strip() == ""
    return loc_ok


def coletar(cfg: dict) -> list[dict]:
    vagas = []
    for board in cfg["fontes"]["greenhouse_boards"]:
        vagas += greenhouse(board)
    for slug in cfg["fontes"]["gupy_empresas"]:
        vagas += gupy(slug)
    aprovadas = [v for v in vagas if filtro_rapido(v, cfg)]
    print(f"[fetch] {len(vagas)} vagas coletadas, {len(aprovadas)} passaram no filtro rapido")
    return aprovadas
