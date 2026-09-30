"""
mapas.py — Os 4 modelos de mapa em avaliação.

Cada função recebe o DataFrame já filtrado (com colunas lat/lon)
e desenha o mapa na tela. Assim o app.py não precisa saber
os detalhes de cada biblioteca.
"""
from html import escape

import folium
import h3
import pandas as pd
import plotly.express as px
import pydeck as pdk
import streamlit as st
from folium.plugins import FastMarkerCluster, HeatMap
from streamlit_folium import st_folium

ALTURA = 620
ZOOM_INICIAL = 12

# ------------------------------------------------------------ mapas de fundo
# Os mapas da CARTO passaram a exigir chave de API (a marca d'água que apareceu).
# Estes três são gratuitos e não precisam de chave.
#   url           -> endereço das "telhas" (imagens) do mapa
#   atribuicao    -> crédito obrigatório do provedor
#   zoom_nativo   -> zoom máximo que o provedor oferece; acima disso, amplia a imagem
FUNDOS = {
    "Cinza claro (Esri)": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "atribuicao": "Tiles &copy; Esri",
        "zoom_nativo": 16,
    },
    "Ruas (OpenStreetMap)": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "atribuicao": "&copy; OpenStreetMap contributors",
        "zoom_nativo": 19,
    },
    "Satélite (Esri)": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "atribuicao": "Tiles &copy; Esri",
        "zoom_nativo": 19,
    },
}


def _centro(df: pd.DataFrame) -> list[float]:
    return [df["lat"].mean(), df["lon"].mean()]


def _mapa_folium_base(df: pd.DataFrame, fundo: str) -> folium.Map:
    """Cria um mapa Folium vazio com o fundo escolhido."""
    cfg = FUNDOS[fundo]
    mapa = folium.Map(location=_centro(df), zoom_start=ZOOM_INICIAL, tiles=None)
    folium.TileLayer(
        tiles=cfg["url"], attr=cfg["atribuicao"], name=fundo,
        max_native_zoom=cfg["zoom_nativo"], max_zoom=19,
    ).add_to(mapa)
    return mapa


# ---------------------------------------------------------------- 1. Folium
def mapa_calor_folium(df: pd.DataFrame, raio: int, desfoque: int, fundo: str):
    """Heatmap clássico (Leaflet). Leve, familiar, fácil de exportar em HTML."""
    mapa = _mapa_folium_base(df, fundo)
    HeatMap(
        df[["lat", "lon"]].values.tolist(),
        radius=raio,
        blur=desfoque,
        min_opacity=0.3,
    ).add_to(mapa)
    # returned_objects=[] evita que o Streamlit recarregue a página a cada zoom
    st_folium(mapa, height=ALTURA, use_container_width=True, returned_objects=[])


# ---------------------------------------------------------------- 2. Plotly
def mapa_densidade_plotly(df: pd.DataFrame, raio: int, fundo: str):
    """Densidade suave com tooltip. Mesmo estilo visual dos gráficos Plotly."""
    cfg = FUNDOS[fundo]
    fig = px.density_map(
        df,
        lat="lat",
        lon="lon",
        radius=raio,
        center={"lat": df["lat"].mean(), "lon": df["lon"].mean()},
        zoom=ZOOM_INICIAL - 0.5,
        map_style="white-bg",  # fundo em branco; o mapa real vem da camada abaixo
        color_continuous_scale="YlOrRd",
        hover_data={"tipo": True, "bairro": True, "lat": False, "lon": False},
    )
    fig.update_layout(
        map_layers=[{
            "below": "traces", "sourcetype": "raster",
            "source": [cfg["url"]], "sourceattribution": cfg["atribuicao"],
        }],
        height=ALTURA, margin=dict(l=0, r=0, t=0, b=0),
    )
    st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------- 3. Pydeck
# Resolução H3 -> tamanho aproximado do hexágono
RESOLUCOES_H3 = {"Grande (~1 km)": 8, "Médio (~350 m)": 9, "Pequeno (~130 m)": 10}

# Rampa de cores do amarelo (poucos acidentes) ao vermelho escuro (muitos)
_CORES = [(255, 237, 160), (254, 178, 76), (240, 59, 32), (128, 0, 38)]


def _cor(fracao: float) -> list[int]:
    """Converte um valor de 0 a 1 numa cor RGB da rampa acima."""
    pos = fracao * (len(_CORES) - 1)
    i = min(int(pos), len(_CORES) - 2)
    t = pos - i
    return [round(a + (b - a) * t) for a, b in zip(_CORES[i], _CORES[i + 1])]


def _mais_comum(df: pd.DataFrame, coluna: str) -> pd.Series:
    """Para cada hexágono, o valor mais frequente da coluna (ex.: a rua)."""
    contagem = df.groupby(["h3", coluna], observed=True).size().rename("n").reset_index()
    return (contagem.sort_values("n", ascending=False)
                    .drop_duplicates("h3")
                    .set_index("h3")[coluna])


def agregar_em_hexagonos(df: pd.DataFrame, resolucao: int):
    """Agrupa os acidentes em hexágonos H3 e resume cada um.

    Antes, o próprio mapa fazia essa conta no navegador e só sabia
    contar. Fazendo aqui em Python, cada hexágono ganha um "perfil":
    rua, bairro e tipo mais comuns. Devolve os acidentes (com a coluna
    h3) e a tabela de zonas.
    """
    df = df.copy()
    df["h3"] = [h3.latlng_to_cell(la, lo, resolucao) for la, lo in zip(df["lat"], df["lon"])]

    zonas = df.groupby("h3").agg(
        acidentes=("id", "size"),
        noite=("fase", lambda s: s.isin(["Noite", "Madrugada"]).mean()),
    )
    zonas["rua"] = _mais_comum(df, "logradouro")
    zonas["bairro"] = _mais_comum(df, "bairro")
    zonas["tipo"] = _mais_comum(df, "tipo")
    zonas = zonas.reset_index().sort_values("acidentes", ascending=False)
    zonas["ranking"] = range(1, len(zonas) + 1)
    return df, zonas


def mapa_hexagonos_pydeck(zonas: pd.DataFrame, escala_altura: int, em_3d: bool):
    """Desenha os hexágonos. Devolve o hexágono clicado (ou None)."""
    zonas = zonas.copy()
    maximo = zonas["acidentes"].max()
    zonas["cor"] = [_cor(v / maximo) for v in zonas["acidentes"]]
    zonas["noite_txt"] = (zonas["noite"] * 100).round().astype(int).astype(str) + "%"

    camada = pdk.Layer(
        "H3HexagonLayer",
        data=zonas,
        id="zonas",                 # nome usado para ler o clique depois
        get_hexagon="h3",
        get_fill_color="cor",
        get_elevation="acidentes",
        elevation_scale=escala_altura,
        extruded=em_3d,
        opacity=0.8,
        pickable=True,
        auto_highlight=True,
    )
    lat, lon = h3.cell_to_latlng(zonas["h3"].iat[0])  # centraliza no pior hexágono
    vista = pdk.ViewState(latitude=lat, longitude=lon, zoom=ZOOM_INICIAL,
                          pitch=45 if em_3d else 0)
    deck = pdk.Deck(
        layers=[camada],
        initial_view_state=vista,
        map_provider="carto",
        map_style="light",
        tooltip={
            "html": "<b>#{ranking} · {acidentes} acidentes</b><br>"
                    "Rua principal: {rua}<br>Bairro: {bairro}<br>"
                    "Tipo mais comum: {tipo}<br>Noite/madrugada: {noite_txt}",
        },
    )
    # on_select="rerun": ao clicar num hexágono, o app roda de novo
    # e nos diz qual objeto foi clicado.
    evento = st.pydeck_chart(deck, height=ALTURA, on_select="rerun",
                             selection_mode="single-object", key="mapa_hex")
    clicados = evento.selection["objects"].get("zonas", [])
    return clicados[0]["h3"] if clicados else None


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


def mapa_clusters_folium(df: pd.DataFrame, fundo: str):
    """Agrupamentos numerados que se abrem com o zoom. Clicando no ponto,
    aparecem os detalhes do acidente — bom para investigar um hotspot."""
    mapa = _mapa_folium_base(df, fundo)

    popups = (
        "<b>" + df["tipo"].map(escape) + "</b><br>"
        + df["data"].dt.strftime("%d/%m/%Y") + " · " + df["fase"].astype(str) + "<br>"
        + df["logradouro"].astype(str).map(escape) + " — " + df["bairro"].map(escape)
    )
    linhas = list(zip(df["lat"], df["lon"], popups))
    FastMarkerCluster(data=linhas, callback=_CRIAR_MARCADOR_JS).add_to(mapa)

    st_folium(mapa, height=ALTURA, use_container_width=True, returned_objects=[])
