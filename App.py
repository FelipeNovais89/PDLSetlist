import streamlit as st
import pandas as pd
import io

import gspread
from google.oauth2.service_account import Credentials

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.errors import HttpError

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

# --------------------------------------------------------------------
# 1. GOOGLE SHEETS – BANCO DE MÚSICAS
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
        df = pd.DataFrame(
            columns=["Título", "Artista", "Tom_Original", "BPM", "CifraDriveID"]
        )
    else:
        df = pd.DataFrame(records)

    for col in ["Título", "Artista", "Tom_Original", "BPM", "CifraDriveID"]:
        if col not in df.columns:
            df[col] = ""

    return df


def append_song_to_sheet(title: str, artist: str, tom_original: str, bpm, cifra_id: str):
    """Adiciona uma nova linha no Google Sheets."""
    secrets = st.secrets["gcp_service_account"]
    sheet_id = st.secrets["sheets"]["sheet_id"]

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(secrets, scopes=scopes)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(sheet_id)
    ws = sh.sheet1
    ws.append_row([title, artist, tom_original, bpm or "", cifra_id or ""])


# --------------------------------------------------------------------
# 2. GOOGLE DRIVE – CIFRAS .TXT
# --------------------------------------------------------------------
def get_drive_service():
    """Cria um cliente da API Drive usando o mesmo service account."""
    secrets = st.secrets["gcp_service_account"]
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(secrets, scopes=scopes)
    return build("drive", "v3", credentials=creds)


@st.cache_data(ttl=120)
def load_chord_from_drive(file_id: str) -> str:
    """Baixa o .txt da cifra no Drive e retorna como string."""
    if not file_id:
        return ""

    file_id = str(file_id).strip()

    try:
        service = get_drive_service()
        request = service.files().get_media(
            fileId=file_id,
            supportsAllDrives=True,
        )
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()

        fh.seek(0)
        return fh.read().decode("utf-8", errors="replace")

    except HttpError as e:
        return f"Erro ao carregar cifra do Drive (ID: {file_id}):\n{e}"


def save_chord_to_drive(file_id: str, content: str):
    """Sobrescreve o .txt da cifra no Drive com o novo conteúdo."""
    if not file_id:
        return

    file_id = str(file_id).strip()

    try:
        service = get_drive_service()
        fh = io.BytesIO(content.encode("utf-8"))
        media = MediaIoBaseUpload(fh, mimetype="text/plain")

        service.files().update(
            fileId=file_id,
            media_body=media,
            supportsAllDrives=True,
        ).execute()

        # limpa cache da leitura para carregar a versão nova
        load_chord_from_drive.clear()

    except HttpError as e:
        st.error(f"Erro ao salvar cifra no Drive (ID: {file_id}): {e}")


# --------------------------------------------------------------------
# 3. ESTADO INICIAL DO APP
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
        st.session_state.current_item = None  # (block_index, item_index)

    if "setlist_name" not in st.session_state:
        st.session_state.setlist_name = "Pagode do LEC"

    if "cifra_font_size" not in st.session_state:
        st.session_state.cifra_font_size = 14


# --------------------------------------------------------------------
# 4. HELPERS DE EDIÇÃO DO SETLIST
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
# 5. LÓGICA DO RODAPÉ
# --------------------------------------------------------------------
def get_footer_context(blocks, cur_block_idx, cur_item_idx):
    items = blocks[cur_block_idx]["items"]

    if cur_item_idx + 1 < len(items):
        nxt = items[cur_item_idx + 1]
        if nxt["type"] == "pause":
            return "next_pause", nxt
        else:
            return "next_music", nxt

    for b in range(cur_block_idx + 1, len(blocks)):
        if blocks[b]["items"]:
            return "end_block", None

    return "none", None


# --------------------------------------------------------------------
# 6. HTML DO CABEÇALHO / RODAPÉ / PÁGINA
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


def build_footer_next_music(next_title, next_artist, next_tone, next_bpm):
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
    return """
    <div class="sheet-footer sheet-footer-endblock">
        <div class="sheet-endblock-wrapper">
            <div class="sheet-endblock-text">FIM DE BLOCO</div>
        </div>
    </div>
    """


def build_sheet_page_html(item, footer_mode, footer_next_item, block_name):
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
        cifra_id = item.get("cifra_id", "")

        if cifra_id:
            body = load_chord_from_drive(cifra_id)
        else:
            body = item.get(
                "text",
                "CIFRA / TEXTO AQUI (ainda não cadastrada).",
            )

    header_html = build_sheet_header_html(title, artist, tom, bpm)

    if footer_mode == "next_music" and footer_next_item is not None:
        next_title = footer_next_item.get("title", "")
        next_artist = footer_next_item.get("artist", "")
        next_tone = footer_next_item.get("tom", "")
        next_bpm = footer_next_item.get("bpm", "")
        footer_html = build_footer_next_music(
            next_title, next_artist, next_tone, next_bpm
        )
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
            grid-template-columns: 2fr 1fr 0.25fr;
            align-items: center;
            padding: 4px 4px 8px;
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
            font-size: 8px;
        }}
        .sheet-header-main .sheet-artist {{
            font-weight: 400;
            font-size: 6px;
        }}
        .sheet-label {{
            font-weight: 700;
            text-align: center;
            font-size: 8px;
        }}
        .sheet-value {{
            text-align: center;
            font-weight: 400;
            font-size: 6px;
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

        .sheet-footer {{
            font-size: 8px;
            margin-top: auto;
            padding-top: 4px;
            border-top: 1px solid #ccc;
        }}

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

        .sheet-next-tombpm-header {{
            display: grid;
            grid-template-columns: 1fr 0.25fr;
            column-gap: 4pt;
            min-width: 70px;
            margin-right: 16px;
            text-align: center;
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
            display: grid;
            grid-template-columns: 1fr 0.25fr;
            column-gap: 4pt;
            min-width: 70px;
            margin-right: 16px;
            text-align: center;
        }}

        .sheet-footer-center {{
            padding-top: 6px;
        }}

        .sheet-next-pause-wrapper {{
            display: flex;
            justify-content: center;
            margin-top: 4px;
        }}

        .sheet-next-pause {{
            font-size: 12px;
            font-weight: 700;
        }}

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
# 6b. PDF DO SETLIST INTEIRO
# --------------------------------------------------------------------
def create_pdf_for_setlist(blocks, setlist_name: str) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    margin_x = 20 * mm
    top_margin = 20 * mm
    bottom_margin = 20 * mm
    line_h = 4 * mm

    for b_idx, block in enumerate(blocks):
        block_name = block["name"]
        items = block["items"]
        for i_idx, item in enumerate(items):
            footer_mode, footer_next_item = get_footer_context(blocks, b_idx, i_idx)

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

                cifra_id = item.get("cifra_id", "")
                if cifra_id:
                    body = load_chord_from_drive(cifra_id)
                else:
                    body = item.get("text", "")

            c.setFont("Courier", 10)
            y = height - top_margin

            c.drawString(
                margin_x,
                y,
                f"{setlist_name}  –  {block_name}",
            )
            y -= line_h * 1.5

            c.setFont("Courier-Bold", 11)
            c.drawString(margin_x, y, (title or "").upper())
            y -= line_h
            c.setFont("Courier", 10)
            if artist:
                c.drawString(margin_x, y, artist)
                y -= line_h

            header_tom = f"TOM: {tom or '-'}"
            header_bpm = f"BPM: {bpm or '-'}"
            c.drawString(margin_x, y, header_tom)
            c.drawRightString(width - margin_x, y, header_bpm)
            y -= line_h
            c.line(margin_x, y, width - margin_x, y)
            y -= line_h

            c.setFont("Courier", 9.5)
            for line in (body or "").splitlines():
                if y < bottom_margin + 15 * mm:
                    break
                c.drawString(margin_x, y, line)
                y -= line_h

            footer_text = ""
            if footer_mode == "next_music" and footer_next_item is not None:
                n_title = footer_next_item.get("title", "")
                n_tom = footer_next_item.get("tom", "")
                n_bpm = footer_next_item.get("bpm", "")
                footer_text = (
                    f"PRÓXIMA: {n_title}  |  TOM {n_tom or '-'}  BPM {n_bpm or '-'}"
                )
            elif footer_mode == "next_pause" and footer_next_item is not None:
                label = footer_next_item.get("label", "Pausa")
                footer_text = f"PRÓXIMA: {label.upper()}"
            elif footer_mode == "end_block":
                footer_text = "FIM DE BLOCO"

            if footer_text:
                c.setFont("Courier", 9)
                c.drawString(margin_x, bottom_margin, footer_text)

            c.showPage()

    c.save()
    buffer.seek(0)
    return buffer.getvalue()


# --------------------------------------------------------------------
# 7. INTERFACE – EDITOR DE BLOCOS
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

                tom_val = item.get("tom", item.get("tom_original", ""))
                new_tom = c3.text_input(
                    "Tom",
                    value=tom_val,
                    key=f"tom_{block_idx}_{i}",
                    placeholder="Tom",
                    label_visibility="collapsed",
                )
                item["tom"] = new_tom

                cifra_id = item.get("cifra_id", "")
                with c1.expander("Ver cifra"):
                    if cifra_id:
                        cifra_text = load_chord_from_drive(cifra_id)
                    else:
                        cifra_text = item.get("text", "")

                    font_size = st.session_state.cifra_font_size

                    col_font_minus, col_font_plus = st.columns(2)
                    if col_font_minus.button(
                        "A﹣", key=f"font_minus_{block_idx}_{i}"
                    ):
                        st.session_state.cifra_font_size = max(8, font_size - 1)
                        st.rerun()
                    if col_font_plus.button(
                        "A﹢", key=f"font_plus_{block_idx}_{i}"
                    ):
                        st.session_state.cifra_font_size = min(24, font_size + 1)
                        st.rerun()

                    edit_key = f"cifra_edit_{block_idx}_{i}"
                    edited = st.text_area(
                        "Cifra",
                        value=cifra_text,
                        height=300,
                        key=edit_key,
                        label_visibility="collapsed",
                    )

                    # CSS global para os textareas
                    st.markdown(
                        f"""
                        <style>
                        textarea[data-testid="stTextArea"] {{
                            font-family: 'Courier New', monospace;
                            font-size: {font_size}px;
                        }}
                        </style>
                        """,
                        unsafe_allow_html=True,
                    )

                    if st.button("Salvar cifra", key=f"save_cifra_{block_idx}_{i}"):
                        if cifra_id:
                            save_chord_to_drive(cifra_id, edited)
                            st.success("Cifra atualizada no Drive.")
                        else:
                            item["text"] = edited
                            st.success(
                                "Cifra salva apenas neste setlist (sem arquivo no Drive)."
                            )
                        st.rerun()

            else:
                label = f"⏸ PAUSA – {item.get('label','')}"
                c1.markdown(label)
                c2.write("")
                c3.write("")

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

    add_col1, add_col2 = st.columns(2)
    if add_col1.button("+ Música", key=f"add_music_btn_{block_idx}"):
        st.session_state[f"show_add_music_{block_idx}"] = True

    if add_col2.button("+ Pausa", key=f"add_pause_btn_{block_idx}"):
        block["items"].append({"type": "pause", "label": "Pausa"})
        st.rerun()

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
                cifra_id = str(row.get("CifraDriveID", "")).strip()
                item = {
                    "type": "music",
                    "title": row.get("Título", ""),
                    "artist": row.get("Artista", ""),
                    "tom_original": row.get("Tom_Original", ""),
                    "tom": row.get("Tom_Original", ""),
                    "bpm": row.get("BPM", ""),
                    "cifra_id": cifra_id,
                    "text": "",
                }
                block["items"].append(item)

            st.session_state[f"show_add_music_{block_idx}"] = False
            st.rerun()


# --------------------------------------------------------------------
# 8. INTERFACE – BANCO DE MÚSICAS
# --------------------------------------------------------------------
def render_song_database():
    st.subheader("Banco de músicas (Google Sheets)")

    df = st.session_state.songs_df
    st.dataframe(df, use_container_width=True, height=240)

    with st.expander("Adicionar nova música ao banco"):
        title = st.text_input("Título")
        artist = st.text_input("Artista")
        tom_original = st.text_input("Tom original (ex.: Fm, C, Gm)")
        bpm = st.text_input("BPM")
        cifra_id = st.text_input("ID da cifra no Drive (opcional)")

        if st.button("Salvar no banco"):
            if title.strip() == "":
                st.warning("Preencha pelo menos o título.")
            else:
                append_song_to_sheet(title, artist, tom_original, bpm, cifra_id)
                st.success("Música adicionada ao Google Sheets!")
                st.session_state.songs_df = load_songs_df()
                st.rerun()


# --------------------------------------------------------------------
# 9. MAIN
# --------------------------------------------------------------------
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

        if cur is not None:
            b_idx, i_idx = cur
            if 0 <= b_idx < len(blocks) and 0 <= i_idx < len(blocks[b_idx]["items"]):
                current_item = blocks[b_idx]["items"][i_idx]
                current_block_name = blocks[b_idx]["name"]
                cur_block_idx, cur_item_idx = b_idx, i_idx

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

            flat_items = []
            for b_idx, block in enumerate(blocks):
                for i_idx, _it in enumerate(block["items"]):
                    flat_items.append((b_idx, i_idx))

            total_pages = len(flat_items)
            try:
                current_pos = flat_items.index((cur_block_idx, cur_item_idx))
            except ValueError:
                current_pos = 0

            nav_prev, nav_info, nav_next, nav_pdf = st.columns([1, 2, 1, 3])

            if nav_prev.button("⬅️", disabled=(current_pos <= 0)):
                new_pos = max(0, current_pos - 1)
                st.session_state.current_item = flat_items[new_pos]
                st.rerun()

            nav_info.markdown(
                f"<div style='text-align:center;'>Página {current_pos+1} de {total_pages}</div>",
                unsafe_allow_html=True,
            )

            if nav_next.button("➡️", disabled=(current_pos >= total_pages - 1)):
                new_pos = min(total_pages - 1, current_pos + 1)
                st.session_state.current_item = flat_items[new_pos]
                st.rerun()

            pdf_bytes = create_pdf_for_setlist(blocks, st.session_state.setlist_name)
            nav_pdf.download_button(
                "💾 PDF do setlist inteiro",
                data=pdf_bytes,
                file_name=f"{st.session_state.setlist_name.replace(' ', '_')}.pdf",
                mime="application/pdf",
            )


if __name__ == "__main__":
    main()
