"""Score de compatibilidade + adaptacao do CV via Claude API."""
import json
import anthropic

client = anthropic.Anthropic()


def _chamar(modelo: str, prompt: str, max_tokens: int = 3000) -> str:
    msg = client.messages.create(
        model=modelo,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def pontuar(vaga: dict, cv_master: str, modelo: str) -> dict:
    prompt = f"""Voce e um recrutador senior de EHS/HSE. Avalie a compatibilidade entre o candidato e a vaga.

CURRICULO MASTER:
{cv_master[:6000]}

VAGA: {vaga['titulo']} | {vaga['empresa']} | {vaga['local']}
DESCRICAO:
{vaga['descricao'][:6000]}

Responda APENAS com JSON valido, sem markdown:
{{"score": <0-100>, "motivos": ["...", "..."], "gaps": ["..."], "idioma": "pt ou en conforme o idioma da vaga", "nivel_ok": true/false}}
nivel_ok = false se a vaga for claramente abaixo de gestor/especialista senior (ex: analista jr, tecnico)."""
    try:
        raw = _chamar(modelo, prompt, 800).strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(raw)
    except Exception as e:
        return {"score": 0, "motivos": [f"erro: {e}"], "gaps": [], "idioma": "pt", "nivel_ok": False}


def adaptar_cv(vaga: dict, cv_master: str, avaliacao: dict, modelo: str) -> str:
    prompt = f"""Voce e um recrutador senior. Adapte o curriculo master abaixo para a vaga especifica.

REGRAS ABSOLUTAS:
1. NAO invente fatos, numeros, empresas ou datas. Use somente o que esta no master.
2. Mantenha a formula Google XYZ em todos os bullets (Realizei X, medido por Y, fazendo Z).
3. Espelhe os termos EXATOS da descricao da vaga nas secoes Titulo, Resumo e Competencias (ATS).
4. Selecione os 5 resultados-chave mais aderentes a esta vaga.
5. Idioma do CV: {avaliacao.get('idioma', 'pt')}.
6. Saida: apenas o curriculo em markdown, sem comentarios.

CURRICULO MASTER:
{cv_master[:8000]}

VAGA: {vaga['titulo']} | {vaga['empresa']}
DESCRICAO:
{vaga['descricao'][:6000]}"""
    return _chamar(modelo, prompt, 3000)


def carta_recrutador(vaga: dict, avaliacao: dict, modelo: str) -> str:
    idioma = avaliacao.get("idioma", "pt")
    prompt = f"""Escreva uma mensagem curta (max 120 palavras) para o recrutador da vaga
"{vaga['titulo']}" na {vaga['empresa']}, em {'ingles' if idioma == 'en' else 'portugues'}.
Assinada por Vinicius. Tom: direto, senior, sem bajulacao. Cite 2 resultados concretos:
reducao de 15% nos indices de frequencia/gravidade (2024) e economia de R$1,2M/ano em treinamentos.
Mencione a dupla formacao (Eng. Eletricista + Eng. Seguranca) se a vaga for tecnica.
Saida: apenas a mensagem."""
    rascunho = _chamar(modelo, prompt, 400)
    return _revisar_texto(rascunho, modelo)


def _revisar_texto(texto: str, modelo: str) -> str:
    """Segunda passada: revisa so erros de digitacao/gramatica, sem mudar conteudo/tom.
    Rede de seguranca para texto que vai direto para a caixa de um recrutador."""
    prompt = f"""Revise o texto abaixo apenas para erros de digitacao e gramatica.
Nao altere conteudo, tom, estrutura ou informacoes. Nao adicione nem remova nada alem
de correcoes ortograficas/gramaticais. Saida: apenas a versao corrigida, sem comentarios.

TEXTO:
{texto}"""
    return _chamar(modelo, prompt, 400)
