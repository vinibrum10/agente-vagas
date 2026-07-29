"""Candidatura automatica em formularios SEM login (Greenhouse).

- dry-run (padrao): abre, preenche, tira screenshot, NAO envia.
- confirm: mostra resumo e exige digitar SIM antes de cada envio.
- auto: envia direto (main.py ja aplicou a barreira de score).
- LinkedIn/Gupy logado NAO sao automatizados (ToS/risco de ban).

Seguranca adicional (formularios customizados variam por empresa):
- Antes de qualquer envio, verifica campos obrigatorios (required, aria-required
  ou label com "*") que continuam vazios apos o preenchimento padrao. Para cada
  um, consulta o Banco de Respostas (agent/planilha.py); se houver resposta
  salva com Sempre_usar=Sim, preenche. Caso contrario, registra a pergunta como
  pendente (Sempre_usar=Nao por padrao — nunca automatico) e ABORTA sem clicar
  em enviar, em nenhum modo (nem auto).
- Depois de clicar em enviar, so confirma sucesso se houver um indicador real
  na pagina (URL de confirmacao ou texto de agradecimento). Sem isso, marca
  como incerta para revisao manual.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

from agent import planilha

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

  const vistos = new Set();
  const resultado = [];
  candidatos.forEach((el) => {
    if (el.type === 'file' || el.type === 'hidden' || el.disabled) return;
    if (!el.id) return;  // sem id nao da pra re-selecionar com seguranca depois
    if (vistos.has(el.id)) return;
    if (!isEmpty(el)) return;
    vistos.add(el.id);
    let options = [];
    if (el.tagName === 'SELECT') {
      options = [...el.options].map((o) => o.text.trim())
        .filter((t) => t && !/selecion|select\\.\\.\\./i.test(t));
    }
    resultado.push({ id: el.id, tag: el.tagName, type: el.type || '', label: labelPara(el), options });
  });
  return resultado;
}
"""

_INDICADORES_SUCESSO = ["obrigado", "thank you", "application received"]


def _extrair_valores_numericos(texto: str) -> list[float]:
    import re
    texto = texto.replace("R$", "").replace("r$", "")
    valores = []
    for m in re.finditer(r"(\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:\.\d+)?)\s*(k|mil)?", texto, re.I):
        numero, sufixo = m.groups()
        if not numero:
            continue
        limpo = numero.replace(".", "").replace(",", ".")
        try:
            valor = float(limpo)
        except ValueError:
            continue
        if sufixo:
            valor *= 1000
        if valor > 0:
            valores.append(valor)
    return valores


def _melhor_faixa_salarial(opcoes: list[str], minimo: float) -> str | None:
    """Seleciona a faixa com o MENOR minimo que ainda seja >= minimo exigido."""
    qualificadas = []
    for op in opcoes:
        valores = _extrair_valores_numericos(op)
        if valores and min(valores) >= minimo:
            qualificadas.append((min(valores), op))
    if not qualificadas:
        return None
    qualificadas.sort(key=lambda par: par[0])
    return qualificadas[0][1]


def _melhor_opcao(resposta: str, opcoes: list[str]) -> str | None:
    resposta_norm = resposta.strip().lower()
    for op in opcoes:
        if op.strip().lower() == resposta_norm:
            return op
    for op in opcoes:
        ol = op.strip().lower()
        if resposta_norm in ol or ol in resposta_norm:
            return op
    return None


def _preencher_campo(page, campo: dict, resposta: str, cand: dict) -> bool:
    sel = f'#{campo["id"]}'
    eh_salarial = any(k in planilha.normalizar(campo["label"]) for k in ("salari", "salary"))
    try:
        if campo["tag"] == "SELECT":
            opcoes = campo.get("options") or []
            if eh_salarial:
                minimo = cand.get("expectativa_salarial_minima")
                alvo = _melhor_faixa_salarial(opcoes, float(minimo)) if minimo is not None else None
            else:
                alvo = _melhor_opcao(resposta, opcoes)
            if not alvo:
                return False
            page.select_option(sel, label=alvo, timeout=4000)
            return True
        if campo["type"] == "checkbox":
            if resposta.strip().lower() in ("sim", "yes", "true", "1"):
                page.check(sel, timeout=4000)
            else:
                page.uncheck(sel, timeout=4000)
            return True
        page.fill(sel, resposta, timeout=4000)
        return True
    except Exception:
        return False


def aplicar_greenhouse(vaga: dict, cand: dict, cv_pdf: Path, modo: str, pasta: Path,
                        sheet_id: str = "") -> str:
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

        ainda_faltando = []
        for campo in campos_faltando:
            pergunta_norm = planilha.normalizar(campo["label"])
            resposta_banco = None
            if sheet_id:
                try:
                    resposta_banco = planilha.consultar_resposta(sheet_id, pergunta_norm)
                except Exception as e:
                    print(f"[banco-respostas] falha ao consultar: {e}")

            preenchido = False
            if (resposta_banco
                    and (resposta_banco.get("sempre_usar") or "").strip().lower() == "sim"
                    and resposta_banco.get("resposta")):
                preenchido = _preencher_campo(page, campo, resposta_banco["resposta"], cand)

            if not preenchido:
                ainda_faltando.append(campo["label"])
                if sheet_id:
                    try:
                        planilha.registrar_pergunta_pendente(
                            sheet_id, campo["label"], campo["type"],
                            "; ".join(campo.get("options") or []), vaga["titulo"])
                    except Exception as e:
                        print(f"[banco-respostas] falha ao registrar pendente: {e}")

        if ainda_faltando:
            browser.close()
            lista = ", ".join(ainda_faltando)
            return f"INCOMPLETA: aguardando resposta em [{lista}]"

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
