"""
dados.py — Carrega e limpa a aba ACIDENTES das planilhas anuais.

Separar a leitura dos dados da interface (app.py) é uma boa prática:
se amanhã os dados vierem de um banco de dados em vez de .ods,
só este arquivo muda.
"""
from pathlib import Path
import re

import pandas as pd
import streamlit as st

# Pasta onde ficam os arquivos Banco_de_Dados_AAAA.ods
PASTA_DADOS = Path(__file__).parent / "dados"

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


def _ler_aba_acidentes(caminho: Path) -> pd.DataFrame:
    """Lê só a aba ACIDENTES de um arquivo .ods."""
    df = pd.read_excel(caminho, sheet_name="ACIDENTES", engine="odf")
    df = df.iloc[:, :12]          # garante só as 12 colunas esperadas
    df.columns = COLUNAS
    df["arquivo"] = caminho.name
    return df


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


def _encontrar_planilhas(pasta: Path) -> list[Path]:
    """Procura as planilhas na pasta 'dados' e, como plano B, na pasta do app.

    Aceita 'Banco_de_Dados_2021.ods' e 'Banco de Dados 2021.ods':
    o padrão [ _] significa "um espaço OU um underline".
    """
    padrao = "Banco[ _]de[ _]Dados[ _]*.ods"
    for local in (pasta, Path(__file__).parent):
        arquivos = sorted(local.glob(padrao))
        if arquivos:
            return arquivos

    # Nada encontrado: mostra o que existe, para facilitar achar o problema
    conteudo = sorted(p.name for p in pasta.iterdir()) if pasta.exists() else "a pasta não existe"
    raise FileNotFoundError(
        f"Nenhuma planilha 'Banco de Dados AAAA.ods' encontrada em {pasta} "
        f"nem em {Path(__file__).parent}. Conteúdo de {pasta.name}/: {conteudo}"
    )


@st.cache_data(show_spinner="Lendo planilhas (só na primeira vez)...")
def carregar_acidentes(pasta: str = str(PASTA_DADOS)) -> pd.DataFrame:
    """Junta todos os anos em uma única tabela limpa.

    @st.cache_data guarda o resultado: ler .ods é lento, então só lemos
    de novo se a função for chamada com argumentos diferentes.
    """
    arquivos = _encontrar_planilhas(Path(pasta))

    df = pd.concat([_ler_aba_acidentes(a) for a in arquivos], ignore_index=True)

    df["data"] = pd.to_datetime(df["data"], errors="coerce")
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
