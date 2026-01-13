import streamlit as st
import pandas as pd

import gspread
from google.oauth2.service_account import Credentials


# --------------------------------------------------------------------
# 1. CONEXÃO COM O GOOGLE SHEETS
# --------------------------------------------------------------------
@st.cache_data(ttl=300)
def load_songs_df():
    """Lê o banco de músicas do Google Sheets."""
    secrets = st.secrets["gcp_service_account"]
    sheet_id = st.secrets["sheets"]["sheet_id"]

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(secrets, scopes=scopes)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(sheet_id)
    ws = sh.sheet1  # primeira aba

    records = ws.get_all_records()
    if not records:
        df = pd.DataFrame(columns=["Título", "Artista", "Tom", "BPM"])
    else:
        df = pd.DataFrame(records)

    for col in ["Título", "Artista", "Tom", "BPM"]:
        if col not in df.columns:
            df[col] = ""

    return df


def append_song_to_sheet(title: str, artist: str, tom: str, bpm):
    """Adiciona uma nova linha no Google Sheets."""
    secrets = st.secrets["gcp_service_account"]
    sheet_id = st.secrets["sheets"]["sheet_id"]

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(secrets, scopes=scopes)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(sheet_id)
    ws = sh.sheet1
    ws.append_row([title, artist, tom, bpm or ""])


# --------------------------------------------------------------------
# 2. ESTADO INICIAL
# --------------------------------------------------------------------
def init_state():
    if "songs_df" not in st.session_state:
        st.session_state.songs_df = load_songs_df()

    if "blocks" not in st.session_state:
        st.session_state.blocks = [
            {
                "name": "Bloco 1",
                "items": [],  # cada item: {type: "music"/"pause", ...}
            }
        ]

    if "current_item" not in st.session_state:
        # (block_index, item_index)
        st.session_state.current_item = None

    if "setlist_name" not in st.session_state:
        st.session_state.setlist_name = "Pagode do LEC - Lisboa 2026"


# --------------------------------------------------------------------
# 3. HELPERS DE EDIÇÃO DO SETLIST
# --------------------------------------------------------------------
def move_item(block_idx, item_idx, direction):
    items = st.session_state.blocks[block_idx]["items"]
    new_idx = item_idx + direction
    if 0 <= new_idx < len(items):
        items[item_idx], items[new_idx] = items[new_idx], items[item_idx]


def delete_item(block_idx, item_idx):
    items = st.session_state.blocks[block_idx]["items"]
    del items[item_idx]


def move_block(block_idx, direction):
    blocks = st.session_state.blocks
    new_idx = block_idx + direction
    if 0 <= new_idx < len(blocks):
        blocks[block_idx], blocks[new_idx] = blocks[new_idx], blocks[block_idx]


def delete_block(block_idx):
    blocks = st.session_state.blocks
    if len(blocks) > 1:
        del blocks[block_idx]


# --------------------------------------------------------------------
# 4. LÓGICA DO RODAPÉ (PRÓXIMA / PAUSA / FIM DE BLOCO)
# --------------------------------------------------------------------
def get_footer_context(blocks, cur_block_idx, cur_item_idx):
    """
    Decide o que mostrar no rodapé da página atual.

    Retorna (mode, next_item):

    - mode == "next_music"  -> próxima música no MESMO bloco
    - mode == "next_pause"  -> próxima é pausa no MESMO bloco
    - mode == "end_block"   -> não tem próxima no bloco, mas existem blocos depois
    - mode == "none"        -> acabou tudo (última música do último bloco)
    """
    items = blocks[cur_block_idx]["items"]

    # Tem próximo item dentro do mesmo bloco?
    if cur_item_idx + 1 < len(items):
        nxt = items[cur_item_idx + 1]
        if nxt["type"] == "pause":
            return "next_pause", nxt
        else:
            return "next_music", nxt

    # Não tem próximo dentro do bloco: ver se existem blocos depois com músicas
    for b in range(cur_block_idx + 1, len(blocks)):
        if blocks[b]["items"]:
            # Existe outro bloco com músicas depois -> fim de bloco
            return "end_block", None

    # Não tem nada depois -> fim do setlist
    return "none", None


def build_sheet_header_html(title, artist, tom, bpm):
    tom_display = tom if tom else "- / -"
    bpm_display = bpm if bpm not in (None, "", 0) else "BPM"

    return f"""
    <div class="sheet-header">
        <div class="sheet-header-col sheet-header-main">
            <div class="sheet-title">{title or "NOVA MÚSICA"}</div>
            <div class="sheet-artist">{artist or ""}</div>
        </div>
        <div class="sheet-header-col sheet-header-tom">
            <div class="sheet-label">TOM</div>
            <div class="sheet-value">{tom_display}</div>
        </div>
        <div class="sheet-header-col sheet-header-bpm">
            <div class="sheet-label">BPM</div>
            <div class="sheet-value">{bpm_display}</div>
        </div>
    </div>
    """


def build_footer_next_music(next_title, next_artist, next_tone, next_bpm):
    """Rodapé quando há próxima música no MESMO bloco."""
    tone_text = next_tone or "-"
    bpm_text = str(next_bpm) if next_bpm not in (None, "", 0) else "-"

    return f"""
    <div class="sheet-footer sheet-footer-grid">
        <div class="sheet-next-label">PRÓXIMA:</div>

        <div class="sheet-next-header-row">
            <div class="sheet-next-title">{next_title}</div>
            <div class="sheet-next-tombpm-header">
                <span class="sheet-next-tom-header">TOM</span>
                <span class="sheet-next-bpm-header">BPM</span>
            </div>
        </div>

        <div class="sheet-next-values-row">
            <div class="sheet-next-artist">{next_artist or ""}</div>
            <div class="sheet-next-tombpm-values">
                <span class="sheet-next-tom-value">{tone_text}</span>
                <span class="sheet-next-bpm-value">{bpm_text}</span>
            </div>
        </div>
    </div>
    """


def build_footer_next_pause(label):
    """Rodapé quando a próxima é uma PAUSA no mesmo bloco."""
    txt = (label or "Pausa").upper()
    return f"""
    <div class="sheet-footer sheet-footer-center">
        <div class="sheet-next-label">PRÓXIMA:</div>
        <div class="sheet-next-pause-wrapper">
            <div class="sheet-next-pause">{txt}</div>
        </div>
    </div>
    """


def build_footer_end_of_block():
    """Rodapé quando acabou o bloco, mas há um próximo bloco depois."""
    return """
    <div class="sheet-footer sheet-footer-endblock">
        <div class="sheet-endblock-wrapper">
            <div class="sheet-endblock-text">FIM DE BLOCO</div>
        </div>
    </div>
    """


def build_sheet_page_html(item, footer_mode, footer_next_item, block_name):
    # Dados da música/pausa atual
    if item["type"] == "pause":
        title = item.get("label", "PAUSA")
        artist = block_name
        tom = ""
        bpm = ""
        body = "PAUSA / INTERVALO"
    else:
        title = item.get("title", "NOVA MÚSICA")
        artist = item.get("artist", "")
        tom = item.get("tom", "")
        bpm = item.get("bpm", "")
        body = item.get(
            "text",
            "CIFRA / TEXTO AQUI (ainda não cadastrado).",
        )

    header_html = build_sheet_header_html(title, artist, tom, bpm)

    # Monta o rodapé de acordo com o modo
    if footer_mode == "next_music" and footer_next_item is not None:
        next_title = footer_next_item.get("title", "")
        next_artist = footer_next_item.get("artist", "")
        next_tone = footer_next_item.get("tom", "")
        next_bpm = footer_next_item.get("bpm", "")
        footer_html = build_footer_next_music(next_title, next_artist, next_tone, next_bpm)
    elif footer_mode == "next_pause" and footer_next_item is not None:
        label = footer_next_item.get("label", "Pausa")
        footer_html = build_footer_next_pause(label)
    elif footer_mode == "end_block":
        footer_html = build_footer_end_of_block()
    else:
        footer_html = ""

    body_html = f"""
        <div class="sheet-body">
          <pre class="sheet-body-text">{body}</pre>
        </div>
    """

    # HTML completo da página
    return f"""
    <html>
    <head>
      <style>
        body {{
            margin: 0;
            padding: 16px;
            background: #111;
        }}
        .sheet {{
            width: 100%;
            height: 100%;
            background: white;
            padding: 16px 24px;
            box-sizing: border-box;
            font-family: "Courier New", monospace;
        }}

        .sheet-header {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            align-items: center;
            padding: 11px 11px 8px;
            border-bottom: 1px solid #ccc;
            font-size: 10px;
        }}
        .sheet-header-col {{
            display: flex;
            flex-direction: column;
            justify-content: center;
        }}
        .sheet-header-main .sheet-title {{
            font-weight: 700;
            text-transform: uppercase;
        }}
        .sheet-header-main .sheet-artist {{
            font-weight: 400;
            font-size: 8px;
        }}
        .sheet-label {{
            font-weight: 700;
            text-align: center;
            font-size: 8px;
        }}
        .sheet-value {{
            text-align: center;
            font-weight: 400;
            font-size: 8px;
        }}

        .sheet-body {{
            padding: 12px 8px 12px 8px;
            min-height: 420px;
        }}
        .sheet-body-text {{
            white-space: pre-wrap;
            font-size: 10px;
            line-height: 1.3;
        }}

        /* Rodapé base */
        .sheet-footer {{
            font-size: 10px;
            margin-top: auto;
            padding-top: 6px;
            border-top: 1px solid #ccc;
        }}

        /* CENÁRIO 1: próxima música -> layout em linhas/colunas */
        .sheet-footer-grid {{
            display: flex;
            flex-direction: column;
        }}

        .sheet-next-label {{
            font-weight: 700;
            margin-bottom: 2px;
            text-align: left;
        }}

        .sheet-next-header-row {{
            display: flex;
            justify-content: space-between;
            align-items: baseline;
        }}

        .sheet-next-title {{
            font-weight: 700;
            text-transform: uppercase;
        }}

        /* Bloco TOM / BPM no rodapé (header) */
        .sheet-next-tombpm-header {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            column-gap: 4pt;
            min-width: 70px;
            margin-right: 16px;  /* puxa um pouco pra esquerda */
            text-align: center;
        }}

        .sheet-next-tom-header,
        .sheet-next-bpm-header {{
            font-weight: 700;
        }}

        /* Linha de baixo: artista + valores D / 115 */
        .sheet-next-values-row {{
            display: flex;
            justify-content: space-between;
            align-items: baseline;
        }}

        .sheet-next-tombpm-values {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            column-gap: 4pt;
            min-width: 70px;
            margin-right: 16px;
            text-align: center;  /* valores alinhados com TOM/BPM */
        }}

        /* CENÁRIO 2: próxima é PAUSA -> 'Pausa' centralizada na página */
        .sheet-footer-center {{
            padding-top: 6px;
        }}

        .sheet-next-pause-wrapper {{
            display: flex;
            justify-content: center;  /* centraliza no eixo horizontal da página */
            margin-top: 4px;
        }}

        .sheet-next-pause {{
            font-size: 12px;
            font-weight: 700;
        }}

        /* CENÁRIO FIM DE BLOCO -> só 'FIM DE BLOCO' centralizado */
        .sheet-footer-endblock {{
            padding-top: 6px;
        }}

        .sheet-endblock-wrapper {{
            display: flex;
            justify-content: center;
            margin-top: 4px;
        }}

        .sheet-endblock-text {{
            font-size: 12px;
            font-weight: 700;
        }}

      </style>
    </head>
    <body>
      <div class="sheet">
        {header_html}
        {body_html}
        {footer_html}
      </div>
    </body>
    </html>
    """


# --------------------------------------------------------------------
# 5. INTERFACE
# --------------------------------------------------------------------
def render_block_editor(block, block_idx, songs_df):
    st.markdown(f"### Bloco {block_idx + 1}")

    name_col, up_col, down_col, del_col = st.columns([6, 1, 1, 1])
    new_name = name_col.text_input(
        "Nome do bloco",
        value=block["name"],
        key=f"block_name_{block_idx}",
        label_visibility="collapsed",
    )
    block["name"] = new_name

    if up_col.button("↑", key=f"block_up_{block_idx}"):
        move_block(block_idx, -1)
        st.rerun()
    if down_col.button("↓", key=f"block_down_{block_idx}"):
        move_block(block_idx, 1)
        st.rerun()
    if del_col.button("✕", key=f"block_del_{block_idx}"):
        delete_block(block_idx)
        st.rerun()

    st.markdown("---")

    # Itens do bloco
    for i, item in enumerate(block["items"]):
        row = st.container()
        with row:
            c1, c2, c3, c4, c5 = st.columns([5, 1.5, 1.5, 1, 1])

            if item["type"] == "music":
                title = item.get("title", "Nova música")
                artist = item.get("artist", "")
                label = f"🎵 {title}"
                if artist:
                    label += f" – {artist}"
                c1.markdown(label)

                bpm_val = item.get("bpm", "")
                placeholder = "BPM" if bpm_val in ("", None, 0) else str(bpm_val)
                new_bpm = c2.text_input(
                    "BPM",
                    value=str(bpm_val) if bpm_val not in ("", None, 0) else "",
                    key=f"bpm_{block_idx}_{i}",
                    placeholder=placeholder,
                    label_visibility="collapsed",
                )
                item["bpm"] = new_bpm

                tom_val = item.get("tom", "")
                new_tom = c3.text_input(
                    "Tom",
                    value=tom_val,
                    key=f"tom_{block_idx}_{i}",
                    placeholder="Tom",
                    label_visibility="collapsed",
                )
                item["tom"] = new_tom

            else:
                label = f"⏸ PAUSA – {item.get('label','')}"
                c1.markdown(label)
                c2.write("")
                c3.write("")

            # botões mover / deletar / preview
            col_up, col_down, col_del = c4, c5, st.columns(1)[0]
            if col_up.button("↑", key=f"item_up_{block_idx}_{i}"):
                move_item(block_idx, i, -1)
                st.rerun()
            if col_down.button("↓", key=f"item_down_{block_idx}_{i}"):
                move_item(block_idx, i, 1)
                st.rerun()
            if col_del.button("✕", key=f"item_del_{block_idx}_{i}"):
                delete_item(block_idx, i)
                st.rerun()

            if st.button("Preview", key=f"preview_{block_idx}_{i}"):
                st.session_state.current_item = (block_idx, i)
                st.rerun()

    st.markdown("")

    # Adicionar músicas / pausa
    add_col1, add_col2 = st.columns(2)
    if add_col1.button("+ Música", key=f"add_music_btn_{block_idx}"):
        st.session_state[f"show_add_music_{block_idx}"] = True

    if add_col2.button("+ Pausa", key=f"add_pause_btn_{block_idx}"):
        block["items"].append({"type": "pause", "label": "Pausa"})
        st.rerun()

    # Seção de seleção de músicas do banco
    if st.session_state.get(f"show_add_music_{block_idx}", False):
        st.markdown("#### Selecionar músicas do banco")
        all_titles = list(songs_df["Título"])
        selected = st.multiselect(
            "Músicas",
            options=all_titles,
            key=f"music_select_{block_idx}",
        )
        if st.button("Adicionar ao bloco", key=f"confirm_add_music_{block_idx}"):
            for title in selected:
                row = songs_df[songs_df["Título"] == title].iloc[0]
                item = {
                    "type": "music",
                    "title": row.get("Título", ""),
                    "artist": row.get("Artista", ""),
                    "tom": row.get("Tom", ""),
                    "bpm": row.get("BPM", ""),
                    "text": "",
                }
                block["items"].append(item)
            st.session_state[f"show_add_music_{block_idx}"] = False
            st.rerun()


def render_song_database():
    st.subheader("Banco de músicas (Google Sheets)")

    df = st.session_state.songs_df
    st.dataframe(df, use_container_width=True, height=240)

    with st.expander("Adicionar nova música ao banco"):
        title = st.text_input("Título")
        artist = st.text_input("Artista")
        tom = st.text_input("Tom (ex.: Fm, C, Gm...)")
        bpm = st.text_input("BPM")

        if st.button("Salvar no banco"):
            if title.strip() == "":
                st.warning("Preencha pelo menos o título.")
            else:
                append_song_to_sheet(title, artist, tom, bpm)
                st.success("Música adicionada ao Google Sheets!")
                st.session_state.songs_df = load_songs_df()
                st.rerun()


def main():
    st.set_page_config(
        page_title="PDL Setlist",
        layout="wide",
        page_icon="🎵",
    )

    init_state()

    st.markdown(f"### Setlist: {st.session_state.setlist_name}")
    st.session_state.setlist_name = st.text_input(
        "Nome do setlist",
        value=st.session_state.setlist_name,
        label_visibility="collapsed",
    )

    left_col, right_col = st.columns([1.1, 1])

    # -------------------- COLUNA ESQUERDA – EDITOR --------------------
    with left_col:
        st.subheader("Editor de Setlist")

        if st.button("+ Adicionar bloco", use_container_width=True):
            st.session_state.blocks.append(
                {"name": f"Bloco {len(st.session_state.blocks)+1}", "items": []}
            )

        for idx, block in enumerate(st.session_state.blocks):
            block_container = st.container()
            with block_container:
                render_block_editor(block, idx, st.session_state.songs_df)
                st.markdown("---")

        render_song_database()

    # -------------------- COLUNA DIREITA – PREVIEW --------------------
    with right_col:
        st.subheader("Preview")

        blocks = st.session_state.blocks
        cur = st.session_state.current_item

        current_item = None
        current_block_name = ""
        footer_mode = "none"
        footer_next_item = None
        cur_block_idx = None
        cur_item_idx = None

        # Se já existe um item selecionado para preview
        if cur is not None:
            b_idx, i_idx = cur
            if 0 <= b_idx < len(blocks) and 0 <= i_idx < len(blocks[b_idx]["items"]):
                current_item = blocks[b_idx]["items"][i_idx]
                current_block_name = blocks[b_idx]["name"]
                cur_block_idx, cur_item_idx = b_idx, i_idx

        # Se ainda não selecionou nada: pegar a primeira música que existir
        if current_item is None:
            for b_idx, block in enumerate(blocks):
                if block["items"]:
                    current_item = block["items"][0]
                    current_block_name = block["name"]
                    cur_block_idx, cur_item_idx = b_idx, 0
                    break

        if current_item is None:
            st.info("Adicione músicas ao setlist para ver o preview.")
        else:
            # Descobre o que vai no rodapé desta página (próxima música, pausa ou fim de bloco)
            footer_mode, footer_next_item = get_footer_context(
                blocks, cur_block_idx, cur_item_idx
            )

            html = build_sheet_page_html(
                current_item,
                footer_mode,
                footer_next_item,
                current_block_name,
            )
            st.components.v1.html(html, height=650, scrolling=True)


if __name__ == "__main__":
    main()
