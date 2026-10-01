"""
app.py — Dashboard de acidentes de trânsito.

Para rodar:  streamlit run app.py
"""
import streamlit as st

from dados import ORDEM_FASES, carregar_acidentes, filtrar, usar_coordenadas_corrigidas
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
    corrigidas = st.toggle(
        "Usar coordenadas corrigidas (SIMGeo)", value=True,
        disabled="lat_corrigida" not in df.columns,
        help="Geradas pelo geocodificar.py. Desligue para comparar com as originais.",
    )
    excluir_nao_viarios = st.checkbox(
        "Excluir ocorrências não viárias", value=True,
        help="Ex.: disparo de alarme de incêndio, emergência APP.",
    )

    st.divider()
    # Os dados ficam guardados por 1 hora. Este botão força baixar agora.
    if st.button("🔄 Atualizar dados do Drive", width="stretch"):
        carregar_acidentes.clear()
        st.rerun()

if corrigidas:
    df = usar_coordenadas_corrigidas(df)
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
col_modelo, col_fundo = st.columns([3, 1])
modelo = col_modelo.radio(
    "Modelo de mapa",
    ["1 · Heatmap (Folium)", "2 · Densidade (Plotly)",
     "3 · Hexágonos 3D (Pydeck)", "4 · Agrupamentos (Folium)"],
    horizontal=True,
)
fundo = col_fundo.selectbox("Mapa de fundo", list(mapas.FUNDOS),
                            disabled=modelo.startswith("3"),
                            help="O Pydeck usa o próprio mapa de fundo.")

if modelo.startswith("1"):
    col_a, col_b = st.columns(2)
    raio = col_a.slider("Raio do ponto", 5, 40, 15)
    desfoque = col_b.slider("Desfoque", 5, 40, 15)
    mapas.mapa_calor_folium(com_coordenada, raio, desfoque, fundo)

elif modelo.startswith("2"):
    raio = st.slider("Raio de influência", 3, 30, 8)
    mapas.mapa_densidade_plotly(com_coordenada, raio, fundo)

elif modelo.startswith("3"):
    col_a, col_b, col_c, col_d = st.columns([2, 2, 2, 1])
    tamanho = col_a.select_slider("Tamanho do hexágono", list(mapas.RESOLUCOES_H3),
                                  value="Médio (~350 m)")
    acidentes_hex, zonas = mapas.agregar_em_hexagonos(
        com_coordenada, mapas.RESOLUCOES_H3[tamanho])
    minimo = col_b.slider("Mostrar zonas com pelo menos", 1,
                          int(zonas["acidentes"].max()), 1,
                          help="Esconde as zonas com poucos acidentes para os picos aparecerem.")
    altura = col_c.slider("Escala de altura", 1, 50, 10)
    em_3d = col_d.toggle("3D", value=True)

    visiveis = zonas[zonas["acidentes"] >= minimo]
    st.caption("Passe o mouse para ver o perfil da zona. **Clique num hexágono** "
               "para listar os acidentes dele abaixo do mapa.")
    hex_clicado = mapas.mapa_hexagonos_pydeck(visiveis, altura, em_3d)

    col_rank, col_detalhe = st.columns([2, 3])
    with col_rank:
        st.subheader("Top 10 zonas")
        st.dataframe(
            zonas.head(10)[["ranking", "acidentes", "rua", "bairro", "tipo"]],
            hide_index=True, width="stretch",
            column_config={"ranking": "#", "acidentes": "Acidentes", "rua": "Rua principal",
                           "bairro": "Bairro", "tipo": "Tipo mais comum"},
        )
    with col_detalhe:
        if hex_clicado:
            detalhe = acidentes_hex[acidentes_hex["h3"] == hex_clicado]
            st.subheader(f"Zona selecionada · {len(detalhe)} acidentes")
            st.bar_chart(detalhe["tipo"].value_counts().head(8), horizontal=True, height=220)
            st.dataframe(
                detalhe.sort_values("data", ascending=False)[
                    ["data", "hora", "fase", "tipo", "logradouro", "numero", "referencia"]],
                hide_index=True, width="stretch", height=260,
                column_config={"data": st.column_config.DateColumn("Data", format="DD/MM/YYYY")},
            )
        else:
            st.info("Clique num hexágono do mapa para ver os acidentes daquela zona.")

else:
    mapas.mapa_clusters_folium(com_coordenada, fundo)

sem_coord = len(filtrado) - len(com_coordenada)
if sem_coord:
    st.caption(f"{sem_coord} acidente(s) do filtro não aparecem no mapa por não terem coordenada.")
