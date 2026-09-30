"""
dados.py — Baixa a planilha de acidentes do Google Drive e limpa os dados.

Separar a leitura dos dados da interface (app.py) é uma boa prática:
a origem mudou de arquivos .ods para o Google Drive e o app.py
nem precisou saber disso.

A planilha continua PRIVADA: ela é compartilhada só com uma conta de
serviço (um "usuário robô" do Google), cuja chave fica nos Secrets
do Streamlit — nunca no GitHub.
"""
import io
import re

import pandas as pd
import streamlit as st
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

# Permissão pedida ao Google: apenas LER arquivos do Drive
ESCOPO = ["https://www.googleapis.com/auth/drive.readonly"]
API_DRIVE = "https://www.googleapis.com/drive/v3/files"
TIPO_GOOGLE_SHEETS = "application/vnd.google-apps.spreadsheet"
TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Nomes internos das 12 colunas, na ordem em que aparecem na planilha.
# Renomeamos por POSIÇÃO porque o cabeçalho varia entre anos
# (ex.: "Georreferencia" em 2021-2025 e "Georreferência" em 2026).
COLUNAS = [
    "id", "data", "dia_semana", "hora", "fase", "tipo", "logradouro",
    "numero", "referencia", "bairro", "geo", "precisao",
]

# Grafias diferentes para o mesmo bairro -> nome padronizado.
# Revise esta lista: você conhece a cidade melhor do que eu.
CORRECOES_BAIRRO = {
    "Saguaçú": "Saguaçu",
    "Ulisses Guimarães": "Ulysses Guimarães",
    "Pirabeiraba (Central)": "Pirabeiraba",
    "Pirabeiraba (pirabeiraba)": "Pirabeiraba",
    "Pirabeiraba Centro": "Pirabeiraba",
    "RIO BONITO (PIRABEIRABA)": "Rio Bonito",
    "ZONA INDUSTRIAL": "Zona Industrial",
    "ZONA INDUSTRIAL NORTE": "Zona Industrial Norte",
}

CORRECOES_TIPO = {
    "Patinete Elétrico x moto": "Patinete Elétrico x Moto",
    "Patinete Elétrico x Obstáculo fixo": "Patinete Elétrico x Obstáculo Fixo",
    "Queda Patinete Elétrico": "Queda de Patinete Elétrico",
}

# Registros que aparecem na aba mas não são acidentes de trânsito
TIPOS_NAO_VIARIOS = {"Disparo de alarme de incêndio", "Emergência APP", "clinica S&R"}

ORDEM_FASES = ["Madrugada", "Manhã", "Tarde", "Noite"]
NOMES_FASES = {"MADRUGADA": "Madrugada", "MANHA": "Manhã", "TARDE": "Tarde", "NOITE": "Noite"}


def _baixar_do_drive(id_arquivo: str) -> bytes:
    """Baixa a planilha usando a conta de serviço guardada nos Secrets."""
    credenciais = service_account.Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]), scopes=ESCOPO
    )
    sessao = AuthorizedSession(credenciais)  # um "requests" já autenticado
    url = f"{API_DRIVE}/{id_arquivo}"

    # Primeiro perguntamos ao Drive que tipo de arquivo é
    resposta = sessao.get(url, params={"fields": "mimeType", "supportsAllDrives": "true"})
    if resposta.status_code == 404:
        raise PermissionError(
            "Planilha não encontrada. Confira o ID e se ela foi compartilhada "
            f"com {credenciais.service_account_email}"
        )
    if resposta.status_code == 403:
        raise PermissionError("Acesso negado. A Google Drive API está ativada no projeto do Google Cloud?")
    resposta.raise_for_status()

    if resposta.json()["mimeType"] == TIPO_GOOGLE_SHEETS:
        # Planilha Google: pedimos uma cópia exportada em .xlsx
        resposta = sessao.get(f"{url}/export", params={"mimeType": TIPO_XLSX})
    else:
        # Arquivo .xlsx/.ods enviado ao Drive: baixamos como está
        resposta = sessao.get(url, params={"alt": "media", "supportsAllDrives": "true"})
    resposta.raise_for_status()
    return resposta.content


def _limpar_bairro(nome) -> str:
    nome = re.sub(r"\s+", " ", str(nome)).strip().rstrip(".")  # espaços duplos e ponto final
    return CORRECOES_BAIRRO.get(nome, nome)


def _limpar_tipo(tipo) -> str:
    tipo = re.sub(r"\s+[xX]\s+", " x ", str(tipo).strip())    # "X" maiúsculo -> "x"
    return CORRECOES_TIPO.get(tipo, tipo)


def _classificar_precisao(texto) -> str:
    texto = str(texto)
    if texto.startswith("Número exato"):
        return "Exata"
    if texto.startswith("Número interpolado"):
        return "Interpolada"
    if texto.startswith("Aproximado"):
        return "Aproximada (centro da rua)"
    return "Não localizado"


# ttl = "time to live": depois de 1 hora o cache vence e a planilha é
# baixada de novo. Assim as atualizações feitas no Drive aparecem sozinhas.
@st.cache_data(ttl="1h", show_spinner="Baixando dados do Google Drive...")
def carregar_acidentes() -> pd.DataFrame:
    config = st.secrets["planilha"]
    conteudo = _baixar_do_drive(config["id"])

    # io.BytesIO faz os bytes baixados se comportarem como um arquivo
    df = pd.read_excel(io.BytesIO(conteudo), sheet_name=config.get("aba", 0))
    df = df.iloc[:, :12]   # renomeia por posição: as 12 colunas originais
    df.columns = COLUNAS
    df = df.dropna(subset=["data"])  # descarta linhas vazias no fim da planilha

    df["data"] = pd.to_datetime(df["data"], errors="coerce", dayfirst=True)
    df["bairro"] = df["bairro"].map(_limpar_bairro)
    df["tipo"] = df["tipo"].map(_limpar_tipo)
    df["fase"] = pd.Categorical(
        df["fase"].map(NOMES_FASES), categories=ORDEM_FASES, ordered=True
    )
    df["nao_viario"] = df["tipo"].isin(TIPOS_NAO_VIARIOS)
    df["precisao_cat"] = df["precisao"].map(_classificar_precisao)

    # "-26.346807, -48.835468" -> duas colunas numéricas.
    # Textos como "Não localizado: ..." viram NaN (vazio).
    coords = df["geo"].astype(str).str.extract(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)")
    df["lat"] = pd.to_numeric(coords[0])
    df["lon"] = pd.to_numeric(coords[1])

    return df


def filtrar(df, periodo, bairros, tipos, fases, excluir_aproximados, excluir_nao_viarios):
    """Aplica os filtros da barra lateral. Lista vazia = sem filtro."""
    inicio, fim = periodo
    mascara = df["data"].between(pd.Timestamp(inicio), pd.Timestamp(fim))
    if bairros:
        mascara &= df["bairro"].isin(bairros)
    if tipos:
        mascara &= df["tipo"].isin(tipos)
    if fases:
        mascara &= df["fase"].isin(fases)
    if excluir_aproximados:
        mascara &= df["precisao_cat"] != "Aproximada (centro da rua)"
    if excluir_nao_viarios:
        mascara &= ~df["nao_viario"]
    return df[mascara]
