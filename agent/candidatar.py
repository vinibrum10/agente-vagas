"""Candidatura automatica em formularios SEM login (Greenhouse).

- dry-run (padrao): abre, preenche, tira screenshot, NAO envia.
- confirm: mostra resumo e exige digitar SIM antes de cada envio.
- auto: envia direto (main.py ja aplicou a barreira de score).
- LinkedIn/Gupy logado NAO sao automatizados (ToS/risco de ban).
"""
from pathlib import Path
from playwright.sync_api import sync_playwright


def aplicar_greenhouse(vaga: dict, cand: dict, cv_pdf: Path, modo: str, pasta: Path) -> str:
    url = vaga["url"]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=60000)
        page.wait_for_load_state("networkidle")

        def fill(sel, valor):
            try:
                page.fill(sel, valor, timeout=4000)
            except Exception:
                pass

        nomes = cand["nome"].split()
        fill("#first_name", nomes[0])
        fill("#last_name", " ".join(nomes[1:]))
        fill("#email", cand["email"])
        fill("#phone", cand["telefone"])
        try:
            page.set_input_files("input[type=file]", str(cv_pdf), timeout=6000)
        except Exception:
            pass

        shot = pasta / "preview_formulario.png"
        page.screenshot(path=str(shot), full_page=True)

        if modo == "dry-run":
            browser.close()
            return f"DRY-RUN: formulario preenchido e salvo em {shot}. Nada foi enviado."

        if modo == "confirm":
            print(f"\n>>> Pronto para ENVIAR: {vaga['titulo']} @ {vaga['empresa']}")
            print(f">>> Confira o preview: {shot}")
            if input(">>> Digite SIM para enviar: ").strip().upper() != "SIM":
                browser.close()
                return "Cancelado pelo usuario."

        try:
            page.click("input[type=submit], button[type=submit]", timeout=8000)
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(pasta / "confirmacao_envio.png"), full_page=True)
            browser.close()
            return "ENVIADA com sucesso."
        except Exception as e:
            browser.close()
            return f"Falha no envio: {e}. Candidate-se manualmente pelo link."


def instrucao_manual(vaga: dict) -> str:
    return (f"Fonte '{vaga['fonte']}' exige login — candidatura manual:\n"
            f"  1. Abra {vaga['url']}\n"
            f"  2. Anexe o CV_adaptado.pdf desta pasta\n"
            f"  3. Cole a mensagem de mensagem_recrutador.txt se houver campo")
