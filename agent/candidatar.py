"""Candidatura automatica em formularios SEM login (Greenhouse).

- dry-run (padrao): abre, preenche, tira screenshot, NAO envia.
- confirm: mostra resumo e exige digitar SIM antes de cada envio.
- auto: envia direto (main.py ja aplicou a barreira de score).
- LinkedIn/Gupy logado NAO sao automatizados (ToS/risco de ban).

Seguranca adicional (formularios customizados variam por empresa):
- Antes de qualquer envio, verifica campos obrigatorios (required, aria-required
  ou label com "*") que continuam vazios apos o preenchimento padrao. Se houver,
  ABORTA sem clicar em enviar — nunca em nenhum modo, nem auto.
- Depois de clicar em enviar, so confirma sucesso se houver um indicador real
  na pagina (URL de confirmacao ou texto de agradecimento). Sem isso, marca
  como incerta para revisao manual em vez de assumir sucesso.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

_SCAN_CAMPOS_OBRIGATORIOS = """
() => {
  const isEmpty = (el) => {
    if (el.tagName === 'SELECT') return !el.value || el.selectedIndex <= 0;
    if (el.type === 'checkbox' || el.type === 'radio') return !el.checked;
    return !el.value || el.value.trim() === '';
  };
  const labelPara = (el) => {
    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl && lbl.innerText.trim()) return lbl.innerText.trim();
    }
    const labelPai = el.closest('label');
    if (labelPai && labelPai.innerText.trim()) return labelPai.innerText.trim();
    const container = el.closest('.field, [class*="field"], div');
    if (container) {
      const lbl = container.querySelector('label');
      if (lbl && lbl.innerText.trim()) return lbl.innerText.trim();
    }
    return el.name || el.id || el.placeholder || 'campo sem nome identificavel';
  };

  const candidatos = new Set();
  document.querySelectorAll('input[required], select[required], textarea[required], [aria-required="true"]')
    .forEach((el) => candidatos.add(el));
  document.querySelectorAll('label').forEach((lbl) => {
    if (!lbl.innerText || !lbl.innerText.includes('*')) return;
    let el = null;
    if (lbl.htmlFor) el = document.getElementById(lbl.htmlFor);
    if (!el) el = lbl.querySelector('input, select, textarea');
    if (!el) {
      const container = lbl.closest('.field, [class*="field"], div');
      if (container) el = container.querySelector('input, select, textarea');
    }
    if (el) candidatos.add(el);
  });

  const vazios = [];
  candidatos.forEach((el) => {
    if (el.type === 'file' || el.type === 'hidden' || el.disabled) return;
    if (isEmpty(el)) vazios.push(labelPara(el));
  });
  return [...new Set(vazios)];
}
"""

_INDICADORES_SUCESSO = ["obrigado", "thank you", "application received"]


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

        campos_faltando = page.evaluate(_SCAN_CAMPOS_OBRIGATORIOS)
        if campos_faltando:
            browser.close()
            lista = ", ".join(campos_faltando)
            return (f"INCOMPLETA: requer preenchimento manual — "
                     f"campos customizados nao suportados: [{lista}]")

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
            shot_final = pasta / "confirmacao_envio.png"
            page.screenshot(path=str(shot_final), full_page=True)

            url_final = page.url.lower()
            texto_final = page.locator("body").inner_text().lower()
            sucesso = "confirmation" in url_final or any(
                ind in texto_final for ind in _INDICADORES_SUCESSO)

            browser.close()
            if sucesso:
                return "ENVIADA com sucesso."
            return f"INCERTA: verifique manualmente. Screenshot: {shot_final}"
        except Exception as e:
            browser.close()
            return f"Falha no envio: {e}. Candidate-se manualmente pelo link."


def instrucao_manual(vaga: dict) -> str:
    return (f"Fonte '{vaga['fonte']}' exige login — candidatura manual:\n"
            f"  1. Abra {vaga['url']}\n"
            f"  2. Anexe o CV_adaptado.pdf desta pasta\n"
            f"  3. Cole a mensagem de mensagem_recrutador.txt se houver campo")
