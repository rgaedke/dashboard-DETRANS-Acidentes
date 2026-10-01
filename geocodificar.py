"""
geocodificar.py — Corrige as coordenadas dos acidentes usando a base oficial
do SIMGeo (Prefeitura de Joinville).

Uso (no seu computador, não no Streamlit):
    python geocodificar.py acidentes.xlsx acidentes_corrigido.xlsx

Regras, em ordem de prioridade:
  1. ESQUINA  — a referência cita um cruzamento ("esquina com a Rua X"):
                o ponto é o cruzamento real dos eixos das duas ruas.
  2. NÚMERO   — Joinville usa numeração métrica: o nº 1500 fica a 1500 m
                do início da rua. A camada Vias_Urbanas traz, para cada
                trecho, a metragem acumulada ("acumulo"), então basta andar
                essa distância sobre o eixo da rua.
  3. ORIGINAL — sem esquina nem número utilizável (ex.: rodovias federais
                e estaduais, que usam km), mantém a coordenada original.

Quando a planilha já tinha "Número exato", a original e a numeração métrica
são duas testemunhas independentes: se concordam (até 150 m), mantemos a
original e a confiança sobe; se discordam, o bairro informado desempata.

As colunas originais NÃO são alteradas; o script acrescenta colunas novas.
"""
import re
import sys
import unicodedata
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process
from shapely import line_merge
from shapely.geometry import Point

PASTA_SIMGEO = Path(__file__).parent / "simgeo"
CRS_METROS = 31982      # SIRGAS 2000 / UTM 22S: coordenadas em metros (SIMGeo)
CRS_GPS = 4326          # latitude/longitude (planilha)
TOLERANCIA_M = 5        # folga entre o fim de um trecho e o início do próximo
FOLGA_BAIRRO_M = 150    # muitas ruas SÃO o limite entre bairros
CONCORDANCIA_M = 150    # original e numeração métrica "concordam" até esta distância

# Posição das colunas na planilha (mesma ordem do dados.py)
COL_RUA, COL_NUM, COL_REF, COL_BAIRRO, COL_GEO, COL_PREC = 6, 7, 8, 9, 10, 11

TIPOS_DE_VIA = {"RUA", "R", "AVENIDA", "AV", "RODOVIA", "ROD", "ESTRADA", "EST",
                "TRAVESSA", "TV", "SERVIDAO", "ALAMEDA", "PRACA", "ACESSO", "BECO"}
ABREVIACOES = {
    "DR": "DOUTOR", "PREF": "PREFEITO", "CEL": "CORONEL", "GEN": "GENERAL",
    "GAL": "GENERAL", "SEN": "SENADOR", "PROF": "PROFESSOR", "STA": "SANTA",
    "STO": "SANTO", "ENG": "ENGENHEIRO", "DEP": "DEPUTADO", "PE": "PADRE",
    "VER": "VEREADOR", "ALM": "ALMIRANTE", "MAL": "MARECHAL", "PRES": "PRESIDENTE",
    "GOV": "GOVERNADOR", "CAP": "CAPITAO", "TEN": "TENENTE", "SGT": "SARGENTO",
}


# ============================================================ textos
def sem_acento(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto))
    return texto.encode("ascii", "ignore").decode().upper()


def chave_rua(nome) -> str:
    """'R. Pref. Baltazar Buschle' -> 'PREFEITO BALTAZAR BUSCHLE'.
    Uma "chave" padronizada permite comparar nomes escritos de jeitos diferentes."""
    if pd.isna(nome):
        return ""
    palavras = re.sub(r"[^A-Z0-9 ]", " ", sem_acento(nome)).split()
    while palavras and palavras[0] in TIPOS_DE_VIA:   # tira "RUA", "AV" do começo
        palavras = palavras[1:]
    return " ".join(ABREVIACOES.get(p, p) for p in palavras)


def parecido(a: str, b: str, **_) -> float:
    """Nota de 0 a 100 de semelhança entre dois nomes, tolerando ordem
    diferente das palavras e nomes abreviados ('ARNO W DOHLER')."""
    nota = fuzz.token_sort_ratio(a, b)
    # token_set dá 100 quando um nome está contido no outro: só aceitamos
    # isso se houver pelo menos 2 palavras em comum (evita 'SANTOS' = 'SANTOS DUMONT')
    if len(set(a.split()) & set(b.split())) >= 2:
        nota = max(nota, fuzz.token_set_ratio(a, b) - 5)
    return nota


# Captura o nome da rua depois de "esquina com a rua", "cruzamento com", "c/ rua"...
RE_ESQUINA = re.compile(
    r"(?:ESQUINA|ESQ\.?|CRUZAMENTO|C/)\s*(?:COM\s+)?(?:(?:A|O)\s+)?"
    r"(?:(?:RUA|R\.|AVENIDA|AV\.?|ESTRADA|TRAVESSA|SERVIDAO|RODOVIA)\s+)?\.?\s*"
    r"([A-Z0-9 .]+?)\s*(?:-|/|,|\.\s|\.$|EM FRENTE|PROX|PERTO|$)"
)


def rua_da_esquina(referencia) -> str:
    if pd.isna(referencia):
        return ""
    achado = RE_ESQUINA.search(sem_acento(referencia))
    return chave_rua(achado.group(1)) if achado else ""


# ============================================================ base SIMGeo
def _pontas(linha):
    """Primeiro e último ponto de uma linha (mesmo que ela tenha várias partes)."""
    partes = getattr(linha, "geoms", [linha])
    return Point(partes[0].coords[0]), Point(partes[-1].coords[-1])


def _orientar_trechos(trechos: gpd.GeoDataFrame) -> pd.Series:
    """Descobre para que lado cada trecho "cresce" na numeração.

    A linha de cada trecho pode ter sido desenhada em qualquer sentido.
    O começo verdadeiro é a ponta que encosta no trecho anterior
    (aquele cujo 'acumulo' termina onde este começa)."""
    invertido = pd.Series(False, index=trechos.index)
    for _, rua in trechos.groupby("codlogra"):
        for i, t in rua.iterrows():
            ponta_a, ponta_b = _pontas(t.geometry)
            anterior = rua[(rua.acumulo - t.ini).abs() <= TOLERANCIA_M].drop(index=i, errors="ignore")
            if len(anterior):
                vizinho = anterior.geometry.union_all()
                invertido[i] = ponta_b.distance(vizinho) < ponta_a.distance(vizinho)
                continue
            seguinte = rua[(rua.ini - t.acumulo).abs() <= TOLERANCIA_M].drop(index=i, errors="ignore")
            if len(seguinte):  # primeiro trecho: o FIM é a ponta que encosta no próximo
                vizinho = seguinte.geometry.union_all()
                invertido[i] = ponta_a.distance(vizinho) < ponta_b.distance(vizinho)
    return invertido


def carregar_simgeo(pasta: Path):
    print("Lendo SIMGeo...")
    urbanas = gpd.read_file(next(pasta.rglob("Vias_Urbanas.shp"))).to_crs(CRS_METROS)
    urbanas = urbanas[["codlogra", "nomelog", "acumulo", "st_length_", "geometry"]]
    urbanas = urbanas[urbanas.geometry.notna() & ~urbanas.geometry.is_empty].copy()
    urbanas["geometry"] = line_merge(urbanas.geometry.values)  # junta trechos picados
    urbanas["codlogra"] = urbanas.codlogra.astype(str)
    urbanas["ini"] = urbanas.acumulo - urbanas.st_length_
    urbanas["chave"] = urbanas.nomelog.map(chave_rua)
    print("Orientando trechos (leva ~1 min)...")
    urbanas["invertido"] = _orientar_trechos(urbanas)

    rurais = gpd.read_file(next(pasta.rglob("Vias_Rurais.shp"))).to_crs(CRS_METROS)
    rurais = rurais.rename(columns={"nmlogradou": "nomelog"})[["nomelog", "geometry"]]
    rurais["codlogra"] = "rural_" + rurais.index.astype(str)
    rurais["chave"] = rurais.nomelog.map(chave_rua)

    # Uma linha por rua inteira (todos os trechos juntos), usada para esquinas
    ruas = pd.concat([urbanas[["codlogra", "chave", "nomelog", "geometry"]], rurais])
    ruas = ruas.dissolve(by="codlogra", aggfunc="first").reset_index()

    bairros = gpd.read_file(next(pasta.rglob("Limite_de_bairros.shp"))).to_crs(CRS_METROS)
    bairros["chave"] = bairros.nome_bairr.map(chave_rua)
    bairros = bairros.set_index("chave")

    # Em quais bairros cada rua passa (para desempatar ruas homônimas)
    cruzam = gpd.sjoin(ruas[["codlogra", "geometry"]], bairros.reset_index()[["chave", "geometry"]])
    ruas["bairros"] = ruas.codlogra.map(cruzam.groupby("codlogra").chave.agg(set)).apply(
        lambda s: s if isinstance(s, set) else set())
    return urbanas, ruas, bairros


# ============================================================ geocodificador
class Geocodificador:
    def __init__(self, urbanas, ruas, bairros):
        self.trechos = {cod: g for cod, g in urbanas.groupby("codlogra")}
        self.ruas = ruas.set_index("codlogra")
        self.bairros = bairros
        self.indice_nomes = ruas.groupby("chave").codlogra.agg(list).to_dict()
        self.nomes = list(self.indice_nomes)
        self._cache = {}

    def bairro_oficial(self, bairro_planilha):
        if bairro_planilha not in self._cache:
            self._cache[bairro_planilha] = self._bairro_oficial(bairro_planilha)
        return self._cache[bairro_planilha]

    def _bairro_oficial(self, bairro_planilha):
        chave = chave_rua(bairro_planilha)
        if chave in self.bairros.index:
            return chave
        achado = process.extractOne(chave, self.bairros.index, scorer=fuzz.token_sort_ratio, score_cutoff=85)
        return achado[0] if achado else None

    def encontrar_rua(self, nome_chave, bairro, candidatos=None):
        """Devolve o codlogra da rua com esse nome (preferindo a do bairro)."""
        if not nome_chave:
            return None
        if candidatos is None:
            codigos = self.indice_nomes.get(nome_chave)
            if not codigos:
                # busca aproximada é lenta: guardamos o resultado de cada nome
                if ("rua", nome_chave) not in self._cache:
                    achado = process.extractOne(nome_chave, self.nomes, scorer=parecido, score_cutoff=88)
                    self._cache[("rua", nome_chave)] = achado[0] if achado else None
                achado = self._cache[("rua", nome_chave)]
                codigos = self.indice_nomes[achado] if achado else []
        else:  # esquina: procurar só entre as ruas que cruzam a principal
            notas = [(parecido(nome_chave, self.ruas.at[c, "chave"]), c) for c in candidatos]
            codigos = [c for n, c in sorted(notas, key=lambda x: x[0], reverse=True) if n >= 82][:1]
        if not codigos:
            return None
        no_bairro = [c for c in codigos if bairro in self.ruas.at[c, "bairros"]]
        return (no_bairro or codigos)[0]

    def ponto_por_numero(self, codlogra, numero, perto_de):
        trechos = self.trechos.get(codlogra)
        if trechos is None or pd.isna(numero):
            return None
        cand = trechos[(trechos.ini - TOLERANCIA_M <= numero) & (numero <= trechos.acumulo + TOLERANCIA_M)]
        pontos = []
        for t in cand.itertuples():
            dist = float(np.clip(numero - t.ini, 0, t.geometry.length))
            if t.invertido:
                dist = t.geometry.length - dist
            pontos.append(t.geometry.interpolate(dist))
        if not pontos:
            return None
        # ruas com ramificações podem ter 2 trechos com a mesma metragem
        return min(pontos, key=lambda p: p.distance(perto_de)) if perto_de is not None else pontos[0]

    def ponto_da_esquina(self, cod_principal, nome_cruzamento, bairro, perto_de):
        geo_principal = self.ruas.at[cod_principal, "geometry"]
        vizinhas = self.ruas.index[self.ruas.geometry.intersects(geo_principal.buffer(15))]
        cod_cruz = self.encontrar_rua(nome_cruzamento, bairro, candidatos=[c for c in vizinhas if c != cod_principal])
        if cod_cruz is None:
            return None, None
        encontro = self.ruas.at[cod_cruz, "geometry"].intersection(geo_principal.buffer(15))
        partes = getattr(encontro, "geoms", [encontro])
        # ponto do cruzamento: centro de cada encontro, trazido para o eixo da rua principal
        pontos = [geo_principal.interpolate(geo_principal.project(p.centroid)) for p in partes if not p.is_empty]
        if not pontos:
            return None, None
        ponto = min(pontos, key=lambda p: p.distance(perto_de)) if perto_de is not None else pontos[0]
        return ponto, self.ruas.at[cod_cruz, "nomelog"]

    def no_bairro(self, ponto, bairro) -> bool:
        return (ponto is not None and bairro is not None
                and self.bairros.at[bairro, "geometry"].buffer(FOLGA_BAIRRO_M).contains(ponto))

    def geocodificar(self, rua, numero, referencia, bairro_planilha, ponto_original, original_exato):
        """Devolve (ponto, método, rua oficial, confiança)."""
        bairro = self.bairro_oficial(bairro_planilha)
        cod = self.encontrar_rua(chave_rua(rua), bairro)
        if cod is None:
            return ponto_original, "original (rua não encontrada)", None, self._conf(ponto_original, bairro)

        nome_oficial = self.ruas.at[cod, "nomelog"]
        ponto_num = self.ponto_por_numero(cod, numero, ponto_original)

        # 1. esquina
        cruzamento = rua_da_esquina(referencia)
        if cruzamento:
            perto = ponto_num if ponto_num is not None else ponto_original
            ponto, nome_cruz = self.ponto_da_esquina(cod, cruzamento, bairro, perto)
            if ponto is not None:
                return ponto, "esquina", f"{nome_oficial} x {nome_cruz}", self._conf(ponto, bairro)

        # 2. número
        if ponto_num is not None:
            if original_exato and ponto_original is not None:
                if ponto_num.distance(ponto_original) <= CONCORDANCIA_M:
                    return ponto_original, "original confirmada", nome_oficial, "muito alta"
                # discordam: o bairro informado desempata
                if self.no_bairro(ponto_original, bairro) and not self.no_bairro(ponto_num, bairro):
                    return ponto_original, "original (bairro confirma)", nome_oficial, "média"
            return ponto_num, "numeração métrica", nome_oficial, self._conf(ponto_num, bairro)

        # 3. nada a fazer
        return ponto_original, "original (sem número utilizável)", nome_oficial, self._conf(ponto_original, bairro)

    def _conf(self, ponto, bairro):
        if ponto is None:
            return None
        if bairro is None:
            return "média (bairro não reconhecido)"
        return "alta" if self.no_bairro(ponto, bairro) else "verificar (fora do bairro)"


# ============================================================ principal
def main(entrada: str, saida: str):
    planilha = pd.read_excel(entrada)
    print(f"{len(planilha)} linhas lidas de {entrada}")

    geo = Geocodificador(*carregar_simgeo(PASTA_SIMGEO))

    # coordenada original -> ponto em metros (ou None)
    coords = planilha.iloc[:, COL_GEO].astype(str).str.extract(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)").astype(float)
    originais = gpd.GeoSeries(gpd.points_from_xy(coords[1], coords[0]), crs=CRS_GPS).to_crs(CRS_METROS)
    tem_coord = coords.notna().all(axis=1)
    originais = [p if ok else None for p, ok in zip(originais, tem_coord)]

    resultados = []
    print("Geocodificando...")
    for i, linha in enumerate(planilha.itertuples(index=False)):
        exato = str(linha[COL_PREC]).startswith("Número exato")
        ponto, metodo, rua_oficial, confianca = geo.geocodificar(
            linha[COL_RUA], linha[COL_NUM], linha[COL_REF], linha[COL_BAIRRO], originais[i], exato)
        # coordenada "Aproximado: ponto central da rua" sem correção possível
        if metodo.startswith("original (sem") and str(linha[COL_PREC]).startswith("Aproximado"):
            confianca = "baixa (centro da rua)"
        resultados.append({
            "ponto": ponto,
            "metodo_geo": metodo if ponto is not None else "não localizado",
            "rua_simgeo": rua_oficial,
            "confianca_geo": confianca,
            "desloc_m": (round(ponto.distance(originais[i]))
                         if ponto is not None and originais[i] is not None else None),
        })
        if i % 2000 == 0:
            print(f"  {i}/{len(planilha)}")

    res = pd.DataFrame(resultados)
    pontos_gps = gpd.GeoSeries(res.ponto, crs=CRS_METROS).to_crs(CRS_GPS)
    planilha["lat_corrigida"] = pontos_gps.y.round(6).values
    planilha["lon_corrigida"] = pontos_gps.x.round(6).values
    for col in ["metodo_geo", "confianca_geo", "rua_simgeo", "desloc_m"]:
        planilha[col] = res[col].values

    planilha.to_excel(saida, index=False)
    print(f"\nPronto! Arquivo salvo em {saida}\n")
    print(planilha["metodo_geo"].value_counts().to_string())
    print()
    print(planilha["confianca_geo"].value_counts().to_string())


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Uso: python geocodificar.py entrada.xlsx saida.xlsx")
    main(sys.argv[1], sys.argv[2])
