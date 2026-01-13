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


def append_song_to_sheet(title: str, artist: str, tom: str, bpm: str | int | None):
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
# 4. HTML / CSS PARA O PREVIEW
# --------------------------------------------------------------------
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


def build_footer_html(next_title, next_artist, next_tone, next_bpm):
    """Rodapé no formato:
       PRÓXIMA:
       Deixa a Vida Me Levar              TOM   BPM
       Zeca Pagodinho                     D     115
    """
    if not next_title:
        # Sem próxima música – fim do setlist
        return """
        <div class="sheet-footer">
            <div class="sheet-next-label">FIM DO SETLIST</div>
        </div>
        """

    tone_text = next_tone or "-"
    bpm_text = str(next_bpm) if next_bpm is not None and next_bpm != "" else "-"

    return f"""
    <div class="sheet-footer">
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


def build_sheet_page_html(item, next_item, block_name):
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

    # Próximo item (para o rodapé)
    if next_item is None:
        next_title = None
        next_artist = None
        next_tone = None
        next_bpm = None
    elif next_item["type"] == "pause":
        next_title = "PAUSA"
        next_artist = ""
        next_tone = ""
        next_bpm = ""
    else:
        next_title = next_item.get("title", "")
        next_artist = next_item.get("artist", "")
        next_tone = next_item.get("tom", "")
        next_bpm = next_item.get("bpm", "")

    header_html = build_sheet_header_html(title, artist, tom, bpm)
    footer_html = build_footer_html(next_title, next_artist, next_tone, next_bpm)

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
            grid-template-columns: 2fr 1fr 1fr;
            align-items: center;
            padding: 4px 8px 8px 8px;
            border-bottom: 1px solid #ccc;
            font-size: 11px;
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
            font-size: 10px;
        }}
        .sheet-label {{
            font-weight: 700;
            text-align: center;
            font-size: 10px;
        }}
        .sheet-value {{
            text-align: center;
            font-weight: 400;
            font-size: 11px;
        }}

        .sheet-body {{
            padding: 12px 8px 12px 8px;
            min-height: 420px;
        }}
        .sheet-body-text {{
            white-space: pre-wrap;
            font-size: 11px;
            line-height: 1.3;
        }}

        /* Rodapé novo */
        .sheet-footer {{
            font-size: 10px;
            margin-top: auto;
            padding-top: 6px;
            border-top: 1px solid #ccc;
        }}
        .sheet-next-label {{
            font-weight: 700;
            margin-bottom: 2px;
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
        .sheet-next-tombpm-header {{
            display: flex;
            gap: 12pt;
        }}
        .sheet-next-tom-header,
        .sheet-next-bpm-header {{
            font-weight: 700;
        }}
        .sheet-next-values-row {{
            display: flex;
            justify-content: space-between;
            align-items: baseline;
        }}
        .sheet-next-tombpm-values {{
            display: flex;
            gap: 24pt;
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


def find_next_item(blocks, cur_block_idx, cur_item_idx):
    """Acha o próximo item na ordem do setlist para mostrar no rodapé."""
    items = blocks[cur_block_idx]["items"]
    if cur_item_idx + 1 < len(items):
        return items[cur_item_idx + 1]

    for b in range(cur_block_idx + 1, len(blocks)):
        if blocks[b]["items"]:
            return blocks[b]["items"][0]

    return None


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
        next_item = None

        if cur is not None:
            b_idx, i_idx = cur
            if 0 <= b_idx < len(blocks) and 0 <= i_idx < len(blocks[b_idx]["items"]):
                current_item = blocks[b_idx]["items"][i_idx]
                current_block_name = blocks[b_idx]["name"]
                next_item = find_next_item(blocks, b_idx, i_idx)

        if current_item is None:
            for b_idx, block in enumerate(blocks):
                if block["items"]:
                    current_item = block["items"][0]
                    current_block_name = block["name"]
                    next_item = find_next_item(blocks, b_idx, 0)
                    break

        if current_item is None:
            st.info("Adicione músicas ao setlist para ver o preview.")
        else:
            html = build_sheet_page_html(current_item, next_item, current_block_name)
            st.components.v1.html(html, height=650, scrolling=True)


if __name__ == "__main__":
    main()
