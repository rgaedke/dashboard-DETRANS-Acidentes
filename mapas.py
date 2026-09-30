"""
mapas.py — Os 4 modelos de mapa em avaliação.

Cada função recebe o DataFrame já filtrado (com colunas lat/lon)
e desenha o mapa na tela. Assim o app.py não precisa saber
os detalhes de cada biblioteca.
"""
from html import escape

import folium
import pandas as pd
import plotly.express as px
import pydeck as pdk
import streamlit as st
from folium.plugins import FastMarkerCluster, HeatMap
from streamlit_folium import st_folium

ALTURA = 620
ZOOM_INICIAL = 12


def _centro(df: pd.DataFrame) -> list[float]:
    return [df["lat"].mean(), df["lon"].mean()]


# ---------------------------------------------------------------- 1. Folium
def mapa_calor_folium(df: pd.DataFrame, raio: int, desfoque: int):
    """Heatmap clássico (Leaflet). Leve, familiar, fácil de exportar em HTML."""
    mapa = folium.Map(location=_centro(df), zoom_start=ZOOM_INICIAL, tiles="CartoDB positron")
    HeatMap(
        df[["lat", "lon"]].values.tolist(),
        radius=raio,
        blur=desfoque,
        min_opacity=0.3,
    ).add_to(mapa)
    # returned_objects=[] evita que o Streamlit recarregue a página a cada zoom
    st_folium(mapa, height=ALTURA, use_container_width=True, returned_objects=[])


# ---------------------------------------------------------------- 2. Plotly
def mapa_densidade_plotly(df: pd.DataFrame, raio: int):
    """Densidade suave com tooltip. Mesmo estilo visual dos gráficos Plotly."""
    fig = px.density_map(
        df,
        lat="lat",
        lon="lon",
        radius=raio,
        center={"lat": df["lat"].mean(), "lon": df["lon"].mean()},
        zoom=ZOOM_INICIAL - 0.5,
        map_style="carto-positron",
        color_continuous_scale="YlOrRd",
        hover_data={"tipo": True, "bairro": True, "lat": False, "lon": False},
    )
    fig.update_layout(height=ALTURA, margin=dict(l=0, r=0, t=0, b=0))
    st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------- 3. Pydeck
def mapa_hexagonos_pydeck(df: pd.DataFrame, raio_metros: int, escala_altura: int):
    """Hexágonos 3D: altura e cor = número de acidentes na célula.
    Segure Ctrl (ou botão direito) e arraste para girar o mapa."""
    camada = pdk.Layer(
        "HexagonLayer",
        data=df[["lat", "lon"]],
        get_position="[lon, lat]",
        radius=raio_metros,
        elevation_scale=escala_altura,
        elevation_range=[0, 1000],
        extruded=True,
        coverage=0.9,
        pickable=True,
        auto_highlight=True,
    )
    vista = pdk.ViewState(
        latitude=df["lat"].mean(), longitude=df["lon"].mean(),
        zoom=ZOOM_INICIAL - 0.5, pitch=45, bearing=0,
    )
    deck = pdk.Deck(
        layers=[camada],
        initial_view_state=vista,
        map_provider="carto",
        map_style="light",
        tooltip={"text": "{elevationValue} acidentes nesta célula"},
    )
    st.pydeck_chart(deck, height=ALTURA)


# ------------------------------------------------------- 4. Folium clusters
# Função JavaScript que o navegador executa para cada ponto.
# Montar os marcadores no navegador é o que deixa 20 mil pontos rápidos.
_CRIAR_MARCADOR_JS = """
function (linha) {
    var marcador = L.circleMarker(new L.LatLng(linha[0], linha[1]),
                                  {radius: 5, color: '#c0392b', fillOpacity: 0.7});
    marcador.bindPopup(linha[2]);
    return marcador;
}
"""


def mapa_clusters_folium(df: pd.DataFrame):
    """Agrupamentos numerados que se abrem com o zoom. Clicando no ponto,
    aparecem os detalhes do acidente — bom para investigar um hotspot."""
    mapa = folium.Map(location=_centro(df), zoom_start=ZOOM_INICIAL, tiles="CartoDB positron")

    popups = (
        "<b>" + df["tipo"].map(escape) + "</b><br>"
        + df["data"].dt.strftime("%d/%m/%Y") + " · " + df["fase"].astype(str) + "<br>"
        + df["logradouro"].astype(str).map(escape) + " — " + df["bairro"].map(escape)
    )
    linhas = list(zip(df["lat"], df["lon"], popups))
    FastMarkerCluster(data=linhas, callback=_CRIAR_MARCADOR_JS).add_to(mapa)

    st_folium(mapa, height=ALTURA, use_container_width=True, returned_objects=[])
