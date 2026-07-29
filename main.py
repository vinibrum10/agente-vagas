"""Agente de Vagas — pipeline: coletar -> pontuar -> adaptar CV -> candidatar/preparar.

Uso:
  python main.py                  # dry-run: prepara pacotes, nao envia nada
  python main.py --confirm        # pergunta SIM antes de cada envio (Greenhouse)
  python main.py --auto           # envia sozinho (Greenhouse sem login + score alto)
"""
import sys
import os
import json
import re
import subprocess
from pathlib import Path
import yaml

BASE = Path(__file__).parent

# Carrega .env ANTES de importar agent.inteligencia — esse modulo cria o
# client Anthropic no import (le ANTHROPIC_API_KEY do ambiente nesse momento).
# cron/Agendador NAO herda variaveis do seu terminal, entao sem isso o client
# sobe sem chave e toda chamada falha silenciosamente (score cai para 0).
_env = BASE / ".env"
if _env.exists():
    for _l in _env.read_text().splitlines():
        if "=" in _l and not _l.strip().startswith("#"):
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from agent import fetch_jobs, inteligencia, candidatar


def md_para_pdf(md_path: Path, pdf_path: Path):
    """Converte o CV markdown em PDF via pandoc."""
    try:
        subprocess.run(["pandoc", str(md_path), "-o", str(pdf_path),
                        "--pdf-engine=xelatex", "-V", "geometry:margin=2cm"],
                       check=True, capture_output=True)
        return True
    except Exception:
        return False


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60]


def main():
    cfg = yaml.safe_load((BASE / "config.yaml").read_text())
    if "--auto" in sys.argv:
        modo = "auto"
    elif "--confirm" in sys.argv:
        modo = "confirm"
    else:
        modo = cfg["execucao"]["modo"]
    modelo = cfg["execucao"]["modelo_claude"]
    cand = cfg["candidato"]
    cv_master = (BASE / cand["cv_master_pt"]).read_text()

    vagas = fetch_jobs.coletar(cfg)
    if "--gmail" in sys.argv or modo == "auto":
        from agent import gmail_alertas
        alertas = gmail_alertas.coletar_alertas(cfg["filtros"]["salario_minimo_brl"])
        alertas += gmail_alertas.coletar_alertas_linkedin()
        alertas += gmail_alertas.coletar_alertas_genericos(cfg.get("email_boards", []))
        vagas += [v for v in alertas if fetch_jobs.filtro_rapido(v, cfg)]
    resultados = []

    for vaga in vagas:
        av = inteligencia.pontuar(vaga, cv_master, modelo)
        print(f"[score {av['score']:>3}] {vaga['titulo']} @ {vaga['empresa']}")
        if av["score"] < cfg["filtros"]["score_minimo_para_candidatar"] or not av.get("nivel_ok"):
            continue

        pasta = BASE / cfg["execucao"]["pasta_saida"] / f"{slug(vaga['empresa'])}__{slug(vaga['titulo'])}"
        pasta.mkdir(parents=True, exist_ok=True)

        cv_md = pasta / "CV_adaptado.md"
        cv_md.write_text(inteligencia.adaptar_cv(vaga, cv_master, av, modelo))
        cv_pdf = pasta / "CV_adaptado.pdf"
        tem_pdf = md_para_pdf(cv_md, cv_pdf)

        (pasta / "mensagem_recrutador.txt").write_text(
            inteligencia.carta_recrutador(vaga, av, modelo))
        (pasta / "vaga.json").write_text(json.dumps({**vaga, "avaliacao": av},
                                                    ensure_ascii=False, indent=2))

        if vaga["fonte"] == "greenhouse" and tem_pdf:
            modo_vaga = modo
            if modo == "auto" and av["score"] < cfg["execucao"]["score_minimo_auto"]:
                modo_vaga = "dry-run"
            status = candidatar.aplicar_greenhouse(
                vaga, cand, cv_pdf, modo_vaga, pasta, cfg.get("planilha_id", ""))
        else:
            status = candidatar.instrucao_manual(vaga)
        (pasta / "STATUS.txt").write_text(status)
        sheet_id = cfg.get("planilha_id", "")
        if sheet_id:
            try:
                from agent import planilha
                planilha.registrar(sheet_id, vaga, av["score"], status)
            except Exception as e:
                print(f"[planilha] falha ao registrar (segue normal): {e}")
        resultados.append((av["score"], vaga["titulo"], vaga["empresa"], status.splitlines()[0]))

        if len(resultados) >= cfg["execucao"]["max_candidaturas_dia"]:
            break

    print("\n===== RELATORIO =====")
    linhas = []
    for score, titulo, empresa, status in sorted(resultados, reverse=True):
        linha = f"  {score:>3} | {titulo} @ {empresa} -> {status}"
        print(linha)
        linhas.append(linha)
    from datetime import datetime
    pasta_saida = BASE / cfg["execucao"]["pasta_saida"]
    pasta_saida.mkdir(parents=True, exist_ok=True)
    rel = pasta_saida / f"RELATORIO_{datetime.now():%Y-%m-%d_%H%M}.md"
    rel.write_text(f"# Execucao {datetime.now():%d/%m/%Y %H:%M} (modo: {modo})\n\n" + "\n".join(linhas) + "\n")
    print(f"\nPacotes em: {cfg['execucao']['pasta_saida']}/ | Relatorio: {rel.name}")


if __name__ == "__main__":
    main()
