"""
app.py — Dashboard de acidentes de trânsito.

Para rodar:  streamlit run app.py
"""
import streamlit as st

from dados import ORDEM_FASES, carregar_acidentes, filtrar
import mapas

st.set_page_config(page_title="Acidentes de Trânsito", page_icon="🚦", layout="wide")

df = carregar_acidentes()

# =================================================================== FILTROS
with st.sidebar:
    st.header("Filtros")

    data_min, data_max = df["data"].min().date(), df["data"].max().date()
    periodo = st.date_input(
        "Período",
        value=(data_min, data_max),
        min_value=data_min,
        max_value=data_max,
        format="DD/MM/YYYY",
    )
    # Enquanto o usuário escolhe, o date_input devolve só a data inicial
    if len(periodo) != 2:
        st.info("Selecione a data final.")
        st.stop()

    bairros = st.multiselect(
        "Bairro", sorted(df["bairro"].unique()), placeholder="Todos"
    )
    tipos = st.multiselect(
        "Tipo de acidente",
        df["tipo"].value_counts().index.tolist(),  # mais frequentes primeiro
        placeholder="Todos",
    )
    fases = st.pills("Fase do dia", ORDEM_FASES, selection_mode="multi")

    st.divider()
    st.caption("Qualidade dos dados")
    excluir_aproximados = st.checkbox(
        "Excluir localizações aproximadas",
        help="Pontos colocados no centro da rua (sem número) podem criar "
             "falsas zonas de calor.",
    )
    excluir_nao_viarios = st.checkbox(
        "Excluir ocorrências não viárias", value=True,
        help="Ex.: disparo de alarme de incêndio, emergência APP.",
    )

filtrado = filtrar(df, periodo, bairros, tipos, fases, excluir_aproximados, excluir_nao_viarios)
com_coordenada = filtrado.dropna(subset=["lat", "lon"])

# =================================================================== RESUMO
st.title("🚦 Acidentes de trânsito — zonas de calor")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Acidentes no filtro", f"{len(filtrado):,}".replace(",", "."))
c2.metric(
    "Com coordenada",
    f"{len(com_coordenada) / len(filtrado):.1%}" if len(filtrado) else "—",
)
c3.metric("Bairro com mais registros",
          filtrado["bairro"].mode().iat[0] if len(filtrado) else "—")
c4.metric("Tipo mais comum",
          filtrado["tipo"].mode().iat[0] if len(filtrado) else "—")

if com_coordenada.empty:
    st.warning("Nenhum acidente com coordenada para os filtros escolhidos.")
    st.stop()

# =================================================================== MAPAS
# Usamos um seletor em vez de abas: só o mapa escolhido é desenhado,
# o que deixa o app mais rápido.
modelo = st.radio(
    "Modelo de mapa",
    ["1 · Heatmap (Folium)", "2 · Densidade (Plotly)",
     "3 · Hexágonos 3D (Pydeck)", "4 · Agrupamentos (Folium)"],
    horizontal=True,
)

if modelo.startswith("1"):
    col_a, col_b = st.columns(2)
    raio = col_a.slider("Raio do ponto", 5, 40, 15)
    desfoque = col_b.slider("Desfoque", 5, 40, 15)
    mapas.mapa_calor_folium(com_coordenada, raio, desfoque)

elif modelo.startswith("2"):
    raio = st.slider("Raio de influência", 3, 30, 8)
    mapas.mapa_densidade_plotly(com_coordenada, raio)

elif modelo.startswith("3"):
    col_a, col_b = st.columns(2)
    raio_hex = col_a.slider("Tamanho do hexágono (metros)", 50, 500, 150, step=25)
    altura = col_b.slider("Escala de altura", 1, 20, 4)
    mapas.mapa_hexagonos_pydeck(com_coordenada, raio_hex, altura)

else:
    mapas.mapa_clusters_folium(com_coordenada)

sem_coord = len(filtrado) - len(com_coordenada)
if sem_coord:
    st.caption(f"{sem_coord} acidente(s) do filtro não aparecem no mapa por não terem coordenada.")
