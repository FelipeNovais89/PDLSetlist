import streamlit as st
import pandas as pd
import io
import re

import gspread
from gspread.exceptions import WorksheetNotFound
from google.oauth2.service_account import Credentials

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.errors import HttpError

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm


# --------------------------------------------------------------------
# 0. CONSTANTES E FUNÇÕES DE TRANSPOSIÇÃO
# --------------------------------------------------------------------
NOTE_SEQ_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_SEQ_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

NOTE_TO_INDEX = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
}

_TONE_BASES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
TONE_OPTIONS = []
for root in _TONE_BASES:
    TONE_OPTIONS.append(root)        # maior
    TONE_OPTIONS.append(root + "m")  # menor


def split_root_and_suffix(symbol: str):
    s = (symbol or "").strip()
    if not s:
        return "", ""
    root = s[0].upper()
    idx = 1
    if len(s) > 1 and s[1] in ("#", "b"):
        root += s[1]
        idx = 2
    suffix = s[idx:]
    return root, suffix


def parse_root_from_key(key: str):
    root, _ = split_root_and_suffix(key)
    return root or None


def semitone_diff(orig_key: str, target_key: str) -> int:
    r1 = parse_root_from_key(orig_key)
    r2 = parse_root_from_key(target_key)
    if not r1 or not r2:
        return 0
    i1 = NOTE_TO_INDEX.get(r1)
    i2 = NOTE_TO_INDEX.get(r2)
    if i1 is None or i2 is None:
        return 0
    return (i2 - i1) % 12


def transpose_root(root: str, steps: int) -> str:
    if steps == 0:
        return root
    idx = NOTE_TO_INDEX.get(root)
    if idx is None:
        return root

    if "b" in root:
        scale = NOTE_SEQ_FLAT
    elif "#" in root:
        scale = NOTE_SEQ_SHARP
    else:
        scale = NOTE_SEQ_SHARP

    return scale[(idx + steps) % 12]


def transpose_key_by_semitones(key: str, steps: int) -> str:
    key = (key or "").strip()
    if not key or steps == 0:
        return key
    root, suffix = split_root_and_suffix(key)
    if not root:
        return key
    new_root = transpose_root(root, steps)
    return new_root + suffix


def transpose_body_text(body: str, tom_original: str, tom_destino: str) -> str:
    steps = semitone_diff(tom_original, tom_destino)
    if steps == 0:
        return body

    lines = body.splitlines()
    new_lines = []

    for line in lines:
        if not line.startswith("|"):
            new_lines.append(line)
            continue

        marker = line[0]
        text = line[1:]

        def repl(match: re.Match):
            root = match.group(1)
            return transpose_root(root, steps)

        transposed = re.sub(r"([A-G](?:#|b)?)", repl, text)
        new_lines.append(marker + transposed)

    return "\n".join(new_lines)


def normalize_lyrics_indent(text: str) -> str:
    lines = text.splitlines()
    out = []
    for line in lines:
        if line.startswith("|"):
            out.append(line)
        else:
            if line.startswith(" "):
                out.append(line[1:])
            else:
                out.append(line)
    return "\n".join(out)


def strip_chord_markers_for_display(text: str) -> str:
    lines = text.splitlines()
    out = []
    for line in lines:
        if line.startswith("|"):
            out.append(line[1:])
        else:
            out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------
# 1. GOOGLE SHEETS – HELPERS GERAIS
# --------------------------------------------------------------------
def get_spreadsheet():
    """Retorna o objeto Spreadsheet (o mesmo usado para banco e setlists)."""
    secrets = st.secrets["gcp_service_account"]
    sheet_id = st.secrets["sheets"]["sheet_id"]
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(secrets, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id)


@st.cache_data(ttl=300)
def load_songs_df():
    """Aba 1 = banco de músicas."""
    sh = get_spreadsheet()
    ws = sh.sheet1

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
    sh = get_spreadsheet()
    ws = sh.sheet1
    ws.append_row([title, artist, tom_original, bpm or "", cifra_id or ""])


# --------- SETLISTS EM ABAS SEPARADAS --------------------------------
def list_setlist_tabs():
    """Lista títulos das abas de setlist (todas menos a 1ª)."""
    sh = get_spreadsheet()
    worksheets = sh.worksheets()
    if len(worksheets) <= 1:
        return []
    return [ws.title for ws in worksheets[1:]]


def save_setlist_to_tab(tab_title: str, blocks):
    """Salva a estrutura de blocos numa aba específica."""
    sh = get_spreadsheet()
    try:
        ws = sh.worksheet(tab_title)
    except WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_title, rows=500, cols=9)

    ws.clear()
    headers = [
        "Bloco",
        "Tipo",
        "Título",
        "Artista",
        "Tom_Original",
        "Tom",
        "BPM",
        "CifraDriveID",
        "Label",
    ]
    ws.append_row(headers)

    rows = []
    for block in blocks:
        block_name = block["name"]
        for item in block["items"]:
            if item["type"] == "music":
                rows.append(
                    [
                        block_name,
                        "music",
                        item.get("title", ""),
                        item.get("artist", ""),
                        item.get("tom_original", ""),
                        item.get("tom", ""),
                        item.get("bpm", ""),
                        item.get("cifra_id", ""),
                        "",
                    ]
                )
            else:
                rows.append(
                    [
                        block_name,
                        "pause",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        item.get("label", "Pausa"),
                    ]
                )

    if rows:
        ws.append_rows(rows, value_input_option="RAW")


def load_setlist_from_tab(tab_title: str):
    """Lê uma aba de setlist e reconstrói a estrutura de blocos."""
    sh = get_spreadsheet()
    try:
        ws = sh.worksheet(tab_title)
    except WorksheetNotFound:
        return []

    records = ws.get_all_records()
    if not records:
        return []

    blocks = []
    block_index = {}

    for rec in records:
        block_name = rec.get("Bloco", "") or "Bloco 1"
        if block_name not in block_index:
            blocks.append({"name": block_name, "items": []})
            block_index[block_name] = len(blocks) - 1

        blk = blocks[block_index[block_name]]
        tipo = (rec.get("Tipo", "") or "").lower()

        if tipo == "music":
            item = {
                "type": "music",
                "title": rec.get("Título", ""),
                "artist": rec.get("Artista", ""),
                "tom_original": rec.get("Tom_Original", ""),
                "tom": rec.get("Tom", "") or rec.get("Tom_Original", ""),
                "bpm": rec.get("BPM", ""),
                "cifra_id": str(rec.get("CifraDriveID", "")).strip(),
                "text": "",
            }
        else:
            item = {
                "type": "pause",
                "label": rec.get("Label", "Pausa"),
            }

        blk["items"].append(item)

    return blocks


# --------------------------------------------------------------------
# 2. GOOGLE DRIVE – CIFRAS .TXT
# --------------------------------------------------------------------
def get_drive_service():
    secrets = st.secrets["gcp_service_account"]
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(secrets, scopes=scopes)
    return build("drive", "v3", credentials=creds)


@st.cache_data(ttl=120)
def load_chord_from_drive(file_id: str) -> str:
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

        load_chord_from_drive.clear()

    except HttpError as e:
        st.error(f"Erro ao salvar cifra no Drive (ID: {file_id}): {e}")


# --------------------------------------------------------------------
# 3. ESTADO INICIAL
# --------------------------------------------------------------------
def init_state():
    if "songs_df" not in st.session_state:
        st.session_state.songs_df = load_songs_df()

    if "blocks" not in st.session_state:
        st.session_state.blocks = [{"name": "Bloco 1", "items": []}]

    if "current_item" not in st.session_state:
        st.session_state.current_item = None

    if "setlist_name" not in st.session_state:
        st.session_state.setlist_name = "Pagode do LEC"

    if "cifra_font_size" not in st.session_state:
        st.session_state.cifra_font_size = 14

    if "preview_index" not in st.session_state:
        st.session_state.preview_index = 0

    if "current_setlist_tab" not in st.session_state:
        st.session_state.current_setlist_tab = None


# --------------------------------------------------------------------
# 4. HELPERS DE BLOCO
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
# 5. RODAPÉ / PÁGINAS
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


def build_pages_structure(blocks):
    pages = []
    for b_idx, block in enumerate(blocks):
        for i_idx, item in enumerate(block["items"]):
            footer_mode, footer_next_item = get_footer_context(blocks, b_idx, i_idx)
            pages.append(
                {
                    "item": item,
                    "block_name": block["name"],
                    "footer_mode": footer_mode,
                    "footer_next_item": footer_next_item,
                }
            )
    return pages


# --------------------------------------------------------------------
# 6. HTML E TEXTO PARA PDF
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


def _compute_body_and_header_fields(item, block_name):
    if item["type"] == "pause":
        title = item.get("label", "PAUSA")
        artist = block_name
        tom_original = ""
        tom = ""
        bpm = ""
        raw_body = "PAUSA / INTERVALO"
    else:
        title = item.get("title", "NOVA MÚSICA")
        artist = item.get("artist", "")
        tom_original = item.get("tom_original", "") or item.get("tom", "")
        tom = item.get("tom", tom_original)
        bpm = item.get("bpm", "")

        cifra_id = item.get("cifra_id", "")
        if cifra_id:
            raw_body = load_chord_from_drive(cifra_id)
        else:
            raw_body = item.get("text", "CIFRA / TEXTO AQUI (ainda não cadastrada).")

    if item["type"] == "pause":
        body_final = raw_body
    else:
        body_transposed = transpose_body_text(raw_body, tom_original, tom)
        body_norm = normalize_lyrics_indent(body_transposed)
        body_final = strip_chord_markers_for_display(body_norm)

    return title, artist, tom, bpm, body_final


def build_sheet_page_html(item, footer_mode, footer_next_item, block_name):
    title, artist, tom, bpm, body_final = _compute_body_and_header_fields(
        item, block_name
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
          <pre class="sheet-body-text">{body_final}</pre>
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
            width: 800px;
            height: 1130px;
            background: white;
            padding: 40px 40px 60px 40px;
            box-sizing: border-box;
            font-family: "Courier New", monospace;
            margin: 0 auto;
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
            font-family: "Courier New", monospace;
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


def build_page_text_for_pdf(item, block_name, footer_mode, footer_next_item):
    title, artist, tom, bpm, body_final = _compute_body_and_header_fields(
        item, block_name
    )

    lines = []

    lines.append(block_name)
    lines.append("")
    lines.append(title or "NOVA MÚSICA")
    if artist:
        lines.append(artist)
    tom_display = tom or "-"
    bpm_display = bpm if bpm not in (None, "", 0) else "-"
    lines.append(f"TOM: {tom_display}    BPM: {bpm_display}")
    lines.append("")
    lines.extend(body_final.splitlines())
    lines.append("")

    if footer_mode == "next_music" and footer_next_item is not None:
        nt = footer_next_item.get("title", "")
        na = footer_next_item.get("artist", "")
        nn = footer_next_item.get("tom", "") or "-"
        nb = footer_next_item.get("bpm", "") or "-"
        footer_line = f"PRÓXIMA: {nt} ({na}) | TOM {nn} | BPM {nb}"
        lines.append(footer_line)
    elif footer_mode == "next_pause" and footer_next_item is not None:
        lbl = footer_next_item.get("Label", "") or footer_next_item.get("label", "Pausa")
        lines.append(f"PRÓXIMA: PAUSA – {lbl}")
    elif footer_mode == "end_block":
        lines.append("FIM DE BLOCO")

    return "\n".join(lines)


def export_pdf(pages_text_list):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    left_margin = 20 * mm
    top_margin = height - 20 * mm
    line_height = 4.5 * mm

    c.setFont("Courier", 9)

    for page_text in pages_text_list:
        y = top_margin
        for line in page_text.split("\n"):
            c.drawString(left_margin, y, line)
            y -= line_height
            if y < 15 * mm:
                c.showPage()
                c.setFont("Courier", 9)
                y = top_margin
        c.showPage()

    c.save()
    buffer.seek(0)
    return buffer


# --------------------------------------------------------------------
# 7. EDITOR DE BLOCOS
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
        container = st.container()
        with container:
            left, right = st.columns([8, 2])

            with left:
                if item["type"] == "music":
                    title = item.get("title", "Nova música")
                    artist = item.get("artist", "")
                    label = f"🎵 {title}"
                    if artist:
                        label += f" – {artist}"
                    st.markdown(f"**{label}**")

                    cifra_id = item.get("cifra_id", "")
                    with st.expander("Ver cifra"):
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

                    bpm_val = item.get("bpm", "")
                    tom_original = item.get("tom_original", "") or item.get("tom", "")
                    tom_val = item.get("tom", tom_original)

                    lab_bpm, _, lab_tom, _ = st.columns([1.5, 0.7, 1.4, 0.7])
                    lab_bpm.markdown(
                        "<p style='text-align:center;font-size:0.8rem;'>BPM</p>",
                        unsafe_allow_html=True,
                    )
                    lab_tom.markdown(
                        "<p style='text-align:center;font-size:0.8rem;'>Tom</p>",
                        unsafe_allow_html=True,
                    )

                    col_bpm, _, col_tom, _ = st.columns([1.5, 0.7, 1.4, 0.7])

                    new_bpm = col_bpm.text_input(
                        "BPM",
                        value=str(bpm_val) if bpm_val not in ("", None, 0) else "",
                        key=f"bpm_{block_idx}_{i}",
                        label_visibility="collapsed",
                        placeholder="BPM",
                    )
                    item["bpm"] = new_bpm

                    if tom_original.endswith("m"):
                        tone_list = [t for t in TONE_OPTIONS if t.endswith("m")]
                    else:
                        tone_list = [t for t in TONE_OPTIONS if not t.endswith("m")]

                    if tom_val not in tone_list and tom_val:
                        tone_list = [tom_val] + tone_list

                    if tom_val in tone_list:
                        idx_tone = tone_list.index(tom_val)
                    else:
                        idx_tone = 0

                    selected_tone = col_tom.selectbox(
                        "Tom",
                        options=tone_list,
                        index=idx_tone,
                        key=f"tom_select_{block_idx}_{i}",
                        label_visibility="collapsed",
                    )
                    if selected_tone != tom_val:
                        item["tom"] = selected_tone
                        st.rerun()

                else:
                    label = f"⏸ PAUSA – {item.get('label','')}"
                    st.markdown(f"**{label}**")

            with right:
                if st.button("⬆️", key=f"item_up_{block_idx}_{i}"):
                    move_item(block_idx, i, -1)
                    st.rerun()
                if st.button("⬇️", key=f"item_down_{block_idx}_{i}"):
                    move_item(block_idx, i, 1)
                    st.rerun()
                if st.button("❌", key=f"item_del_{block_idx}_{i}"):
                    delete_item(block_idx, i)
                    st.rerun()
                if st.button("Prev", key=f"preview_{block_idx}_{i}"):
                    global_index = 0
                    for b_i, blk in enumerate(st.session_state.blocks):
                        for it in blk["items"]:
                            if blk is block and it is item:
                                st.session_state.preview_index = global_index
                                st.rerun()
                            global_index += 1

        st.markdown("---")

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
# 8. BANCO DE MÚSICAS
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

    # ------------------ CONTROLE DAS SETLISTS ------------------------
    st.markdown("#### Setlists salvas no Google Sheets")

    col_new, col_load = st.columns(2)

    with col_new:
        new_name = st.text_input("Nova setlist", key="new_setlist_name")
        if st.button("Criar nova setlist"):
            name = new_name.strip() or "Nova Setlist"
            st.session_state.setlist_name = name
            st.session_state.blocks = [{"name": "Bloco 1", "items": []}]
            st.session_state.current_setlist_tab = name
            save_setlist_to_tab(name, st.session_state.blocks)
            st.success(f"Setlist '{name}' criada.")
            st.rerun()

    with col_load:
        existing_tabs = list_setlist_tabs()
        options = ["(Nenhuma)"] + existing_tabs
        selected_tab = st.selectbox(
            "Carregar setlist existente",
            options=options,
            index=0,
            key="load_setlist_select",
        )
        if st.button("Carregar setlist"):
            if selected_tab != "(Nenhuma)":
                blocks = load_setlist_from_tab(selected_tab)
                st.session_state.blocks = blocks if blocks else [{"name": "Bloco 1", "items": []}]
                st.session_state.setlist_name = selected_tab
                st.session_state.current_setlist_tab = selected_tab
                st.session_state.preview_index = 0
                st.success(f"Setlist '{selected_tab}' carregada.")
                st.rerun()
            else:
                st.info("Selecione uma setlist na lista.")

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
            render_block_editor(block, idx, st.session_state.songs_df)

        render_song_database()

        st.markdown("---")
        if st.session_state.current_setlist_tab:
            if st.button("Salvar setlist no Google Sheets", use_container_width=True):
                save_setlist_to_tab(
                    st.session_state.current_setlist_tab, st.session_state.blocks
                )
                st.success(
                    f"Setlist salva na aba '{st.session_state.current_setlist_tab}'."
                )
        else:
            st.info("Crie ou carregue uma setlist para poder salvar.")

    with right_col:
        st.subheader("Preview")

        blocks = st.session_state.blocks
        pages = build_pages_structure(blocks)

        if not pages:
            st.info("Adicione músicas ao setlist para ver o preview.")
        else:
            if st.session_state.preview_index >= len(pages):
                st.session_state.preview_index = 0

            current_page = pages[st.session_state.preview_index]
            current_item = current_page["item"]
            current_block_name = current_page["block_name"]
            footer_mode = current_page["footer_mode"]
            footer_next_item = current_page["footer_next_item"]

            html = build_sheet_page_html(
                current_item,
                footer_mode,
                footer_next_item,
                current_block_name,
            )
            st.components.v1.html(html, height=1200, scrolling=True)

            col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 3, 2])

            if col2.button("⬅️", disabled=st.session_state.preview_index == 0):
                if st.session_state.preview_index > 0:
                    st.session_state.preview_index -= 1
                    st.rerun()

            if col3.button(
                "➡️", disabled=st.session_state.preview_index == len(pages) - 1
            ):
                if st.session_state.preview_index < len(pages) - 1:
                    st.session_state.preview_index += 1
                    st.rerun()

            pages_text = [
                build_page_text_for_pdf(
                    p["item"],
                    p["block_name"],
                    p["footer_mode"],
                    p["footer_next_item"],
                )
                for p in pages
            ]
            pdf_buffer = export_pdf(pages_text)
            col4.download_button(
                "Export PDF",
                data=pdf_buffer,
                file_name=f"{st.session_state.setlist_name}.pdf",
                mime="application/pdf",
            )


if __name__ == "__main__":
    main()
