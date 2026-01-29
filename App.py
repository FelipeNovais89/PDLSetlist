# App.py — DEBUG ONLY (teste isolado do seletor de músicas)
# Coloque este arquivo sozinho no seu repositório Streamlit e rode.
# Ele tenta carregar o CSV do GitHub (via st.secrets se existir) e mostra
# exatamente por que o selectbox fica vazio.

import streamlit as st
import pandas as pd
import io
import requests


# ----------------------------
# Config
# ----------------------------
def _songs_csv_url_from_secrets() -> str:
    """
    Lê a URL do CSV do GitHub em st.secrets["github"]["songs_csv_url"] se existir.
    Caso não exista, usa uma URL padrão (ajuste aqui se quiser).
    """
    try:
        gh = st.secrets.get("github", {})
        url = gh.get(
            "songs_csv_url",
            "https://raw.githubusercontent.com/FelipeNovais89/PDLSetlist/refs/heads/main/Data/PDL_musicas.csv",
        )
        return url
    except Exception:
        return "https://raw.githubusercontent.com/FelipeNovais89/PDLSetlist/refs/heads/main/Data/PDL_musicas.csv"


@st.cache_data(ttl=300)
def load_songs_df(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df = df.fillna("")
    return df


def debug_music_picker(songs_df: pd.DataFrame):
    st.subheader("DEBUG: Seletor de músicas")

    # 1) Status geral
    st.write("✅ songs_df type:", type(songs_df))
    st.write("✅ songs_df shape:", getattr(songs_df, "shape", None))

    # 2) Colunas
    cols = list(songs_df.columns) if hasattr(songs_df, "columns") else []
    st.write("✅ Colunas encontradas:", cols)

    # 3) Preview
    if hasattr(songs_df, "head"):
        st.dataframe(songs_df.head(30), use_container_width=True)

    # 4) Monta opções e loga descartes
    options = []
    discard_no_title = 0
    discard_other = 0

    # tenta mapear automaticamente colunas caso estejam sem acento
    col_title_candidates = ["Título", "Titulo", "title", "Title", "song", "SongTitle"]
    col_artist_candidates = ["Artista", "Artist", "artist"]
    col_tom_candidates = ["Tom_Original", "TomOriginal", "Tom", "Key", "key"]

    def pick_col(cands):
        for c in cands:
            if c in cols:
                return c
        return None

    c_title = pick_col(col_title_candidates)
    c_artist = pick_col(col_artist_candidates)
    c_tom = pick_col(col_tom_candidates)

    st.write("🔎 Coluna usada para Título:", c_title)
    st.write("🔎 Coluna usada para Artista:", c_artist)
    st.write("🔎 Coluna usada para Tom:", c_tom)

    if songs_df is None or len(cols) == 0:
        st.error("songs_df não é um DataFrame válido ou não tem colunas.")
        return

    # garante strings
    df2 = songs_df.copy()
    try:
        for c in cols:
            df2[c] = df2[c].astype(str).fillna("")
    except Exception:
        pass

    for _, row in df2.reset_index(drop=True).iterrows():
        try:
            titulo = str(row.get(c_title, "")).strip() if c_title else ""
            artista = str(row.get(c_artist, "")).strip() if c_artist else ""
            tom = str(row.get(c_tom, "")).strip() if c_tom else ""
        except Exception:
            discard_other += 1
            continue

        if not titulo or titulo.lower() in ("nan", "none"):
            discard_no_title += 1
            continue

        label = f"{titulo} – {artista}" if artista else titulo
        if tom and tom.lower() not in ("nan", "none", ""):
            label += f" ({tom})"
        options.append(label)

    st.write("✅ Opções criadas:", len(options))
    st.write("🗑️ Linhas descartadas (sem título):", discard_no_title)
    st.write("🗑️ Linhas descartadas (erro):", discard_other)

    if not options:
        st.error(
            "❌ options ficou vazio. "
            "Ou o CSV carregou vazio, ou a coluna de título não existe/está com nome diferente."
        )
        st.info("Veja acima: colunas detectadas e qual coluna foi usada como Título.")
        return

    pick = st.selectbox("Escolha uma música (DEBUG)", options=options)
    st.success(f"Selecionada: {pick}")


def main():
    st.set_page_config(page_title="PDL Debug – Seletor", layout="centered")
    st.title("PDL Debug – Teste do seletor de músicas")

    url_default = _songs_csv_url_from_secrets()

    st.caption("1) Confira a URL do CSV abaixo. 2) Clique em 'Carregar CSV'. 3) Veja o debug do selectbox.")
    url = st.text_input("URL do CSV (raw)", value=url_default)

    col1, col2 = st.columns([1, 1])
    if col1.button("Carregar CSV", use_container_width=True):
        try:
            st.session_state.df = load_songs_df(url)
            st.success("CSV carregado com sucesso.")
        except Exception as e:
            st.error(f"Erro carregando CSV: {e}")
            st.session_state.df = pd.DataFrame()

    if col2.button("Limpar cache", use_container_width=True):
        load_songs_df.clear()
        st.success("Cache limpo. Carregue novamente.")

    st.markdown("---")

    df = st.session_state.get("df", None)
    if df is None:
        st.info("Clique em **Carregar CSV** para iniciar o teste.")
        return

    debug_music_picker(df)


if __name__ == "__main__":
    main()
