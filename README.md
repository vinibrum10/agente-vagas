# Agente de Vagas — Vinicius Brum

Pipeline: coleta vagas (Greenhouse/Gupy/alertas Gmail) -> pontua via Claude -> adapta CV (formula XYZ) -> candidata (Greenhouse sem login) ou prepara pacote -> registra na planilha Google.

## Comandos
python main.py            (dry-run: prepara tudo, envia nada)
python main.py --confirm  (pede SIM antes de cada envio Greenhouse)
python main.py --auto     (envia sozinho: Greenhouse sem login + score >= 85; usado pelo agendador)
Flag --gmail forca leitura dos alertas de e-mail em qualquer modo.

## Setup local
1. pip install -r requirements.txt ; playwright install chromium ; instalar pandoc + xelatex
2. Copiar .env.exemplo para .env e preencher ANTHROPIC_API_KEY
3. Google Cloud Console: ativar Gmail API + Sheets API, OAuth Client (Desktop), credentials.json na raiz
4. Rodar python main.py --gmail uma vez para autorizar (gera token.json)

## Nuvem (GitHub Actions) — PC pode ficar desligado
1. Repo privado; subir esta pasta (.gitignore protege segredos)
2. Secrets: ANTHROPIC_API_KEY, GOOGLE_CREDENTIALS_B64, GOOGLE_TOKEN_B64 (base64 dos json)
3. Workflow roda ter/qui 8h Brasilia; botao Run workflow dispara extra pelo app do GitHub

## Regras de seguranca (por design)
- LinkedIn e Gupy logado nunca sao automatizados (ToS/risco de ban)
- Modo auto so envia Greenhouse sem login com score >= score_minimo_auto
- Rode 2-3 ciclos em --confirm antes de ligar o --auto
