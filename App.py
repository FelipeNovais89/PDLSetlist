import streamlit as st
import pandas as pd
import io
import re

import gspread
from google.oauth2.service_account import Credentials

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.errors import HttpError

import google.generativeai as genai

# ==============================================================
# 1) GEMINI – API KEY
# ==============================================================

def get_gemini_api_key():
    """Procura a gemini_api_key em st.secrets."""
    try:
        if "gemini_api_key" in st.secrets:
            return st.secrets["gemini_api_key"]
        if "sheets" in st.secrets and "gemini_api_key" in st.secrets["sheets"]:
            return st.secrets["sheets"]["gemini_api_key"]
    except Exception:
        pass
    return None


GEMINI_API_KEY = get_gemini_api_key()
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    st.warning(
        "Gemini API key não encontrada em st.secrets. "
        "Adicione 'gemini_api_key' no topo ou em [sheets]."
    )

# ==============================================================
# 2) CONSTANTES – TRANSPOSIÇÃO
# ==============================================================

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
for r in _TONE_BASES:
    TONE_OPTIONS.append(r)
    TONE_OPTIONS.append(r + "m")


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


# ==============================================================
# 3) GEMINI – TRANSCRIÇÃO DE IMAGEM
# ==============================================================

def transcribe_image_with_gemini(uploaded_file, model_name="models/gemini-2.5-flash"):
    """Recebe um arquivo de imagem do Streamlit e retorna texto da cifra."""
    api_key = get_gemini_api_key()
    if not api_key:
        st.error(
            "Gemini API key não configurada. "
            "Adicione 'gemini_api_key' em st.secrets."
        )
        return ""

    try:
        model = genai.GenerativeModel(model_name)

        prompt = """
        Você está transcrevendo uma cifra (acordes + letra) a partir de uma imagem.

        REGRAS DE FORMATAÇÃO (IMPORTANTES):
        1. Toda linha que contiver apenas ACORDES deve começar com o caractere '|'.
        2. Toda linha de LETRA deve começar com um ESPAÇO em branco.
        3. Mantenha o alinhamento visual dos acordes exatamente acima das sílabas da letra.
        4. Ignore diagramas de braço de instrumento; foque apenas em texto e acordes.
        5. NÃO use markdown, NÃO use ``` e nem cabeçalhos; apenas texto puro.
        """

        mime = uploaded_file.type or "image/jpeg"
        img_data = uploaded_file.getvalue()

        st.info(f"Chamando Gemini com modelo: {model_name}")
        response = model.generate_content(
            [
                prompt,
                {"mime_type": mime, "data": img_data},
            ]
        )

        text = (getattr(response, "text", "") or "").strip()

        # Se vier encapsulado em bloco de código markdown
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = "\n".join(text.split("\n")[1:]).strip()

        return text

    except Exception as e:
        st.error(f"Erro ao chamar Gemini: {e}")
        return ""


# ==============================================================
# 4) GOOGLE DRIVE – ARQUIVOS DE CIFRA
# ==============================================================

def get_drive_service():
    secrets = st.secrets["gcp_service_account"]
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(secrets, scopes=scopes)
    return build("drive", "v3", credentials=creds)


def create_chord_in_drive(filename, content):
    """Cria um novo .txt no Drive e retorna o FileID."""
    if not content.strip():
        return ""

    try:
        service = get_drive_service()

        folder_id = st.secrets.get("sheets", {}).get("folder_id", None)

        file_metadata = {
            "name": f"{filename}.txt",
            "mimeType": "text/plain",
        }
        if folder_id:
            file_metadata["parents"] = [folder_id]

        fh = io.BytesIO(content.encode("utf-8"))
        media = MediaIoBaseUpload(fh, mimetype="text/plain")

        file = (
            service.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return file.get("id", "")
    except HttpError as e:
        st.error(f"Erro no Drive (upload): {e}")
        return ""
    except Exception as e:
        st.error(f"Erro inesperado ao criar arquivo no Drive: {e}")
        return ""


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


# ==============================================================
# 5) GOOGLE SHEETS – BANCO DE MÚSICAS + SETLISTS
# ==============================================================

def get_gspread_client():
    secrets = st.secrets["gcp_service_account"]
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(secrets, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc


def get_spreadsheet():
    gc = get_gspread_client()
    sheet_id = st.secrets["sheets"]["sheet_id"]
    return gc.open_by_key(sheet_id)


@st.cache_data(ttl=300)
def load_songs_df():
    sh = get_spreadsheet()
    ws = sh.sheet1  # primeira aba = banco de músicas

    try:
        # Lê todas as células como matriz de strings
        values = ws.get_all_values()
    except Exception as e:
        st.error(f"Erro ao ler planilha de músicas (get_all_values): {e!r}")
        # volta um DF vazio com as colunas padrão
        return pd.DataFrame(
            columns=[
                "Título",
                "Artista",
                "Tom_Original",
                "BPM",
                "CifraDriveID",
                "CifraSimplificadaID",
            ]
        )

    # Se a planilha estiver completamente vazia
    if not values:
        df = pd.DataFrame(
            columns=[
                "Título",
                "Artista",
                "Tom_Original",
                "BPM",
                "CifraDriveID",
                "CifraSimplificadaID",
            ]
        )
    else:
        # Primeira linha = cabeçalho
        header = values[0]
        rows = values[1:]

        n_cols = len(header)
        norm_rows = []

        # Garante que cada linha tenha o mesmo número de colunas do cabeçalho
        for r in rows:
            if len(r) < n_cols:
                r = r + [""] * (n_cols - len(r))
            elif len(r) > n_cols:
                r = r[:n_cols]
            norm_rows.append(r)

        df = pd.DataFrame(norm_rows, columns=header)

    # Garante que as colunas que o app espera existam
    for col in [
        "Título",
        "Artista",
        "Tom_Original",
        "BPM",
        "CifraDriveID",
        "CifraSimplificadaID",
    ]:
        if col not in df.columns:
            df[col] = ""

    return df


def append_song_to_sheet(
    title: str,
    artist: str,
    tom_original: str,
    bpm,
    cifra_id: str,
    cifra_simplificada_id: str,
):
    sh = get_spreadsheet()
    ws = sh.sheet1
    ws.append_row(
        [
            title,
            artist,
            tom_original,
            bpm or "",
            cifra_id or "",
            cifra_simplificada_id or "",
        ]
    )
    load_songs_df.clear()


SETLIST_COLS = [
    "BlockIndex",
    "BlockName",
    "ItemIndex",
    "ItemType",
    "SongTitle",
    "Artist",
    "Tom",
    "BPM",
    "CifraDriveID",
    "CifraSimplificadaID",
    "UseSimplificada",
    "PauseLabel",
]


def list_setlist_names():
    sh = get_spreadsheet()
    worksheets = sh.worksheets()
    setlists = [ws.title for ws in worksheets[1:]]  # sheet1 é banco
    return setlists


def get_or_create_setlist_ws(name: str):
    sh = get_spreadsheet()
    name = (name or "").strip() or "Setlist sem nome"

    for ws in sh.worksheets():
        if ws.title == name:
            return ws

    ws = sh.add_worksheet(title=name, rows=1000, cols=len(SETLIST_COLS))
    ws.append_row(SETLIST_COLS)
    return ws


def load_setlist_df(name: str) -> pd.DataFrame:
    sh = get_spreadsheet()
    try:
        ws = sh.worksheet(name)
    except gspread.WorksheetNotFound:
        df = pd.DataFrame(columns=SETLIST_COLS)
        return df

    records = ws.get_all_records()
    if not records:
        df = pd.DataFrame(columns=SETLIST_COLS)
    else:
        df = pd.DataFrame(records)

    for col in SETLIST_COLS:
        if col not in df.columns:
            df[col] = ""

    return df


def write_setlist_df(name: str, df: pd.DataFrame):
    ws = get_or_create_setlist_ws(name)
    ws.clear()
    ws.append_row(SETLIST_COLS)
    if not df.empty:
        ws.append_rows(df[SETLIST_COLS].values.tolist())


def save_current_setlist_to_sheet():
    name = (st.session_state.setlist_name or "").strip() or "Setlist sem nome"

    blocks = st.session_state.blocks
    rows = []
    for b_idx, block in enumerate(blocks):
        block_name = block.get("name", f"Bloco {b_idx + 1}")
        items = block.get("items", [])
        for i_idx, item in enumerate(items):
            base = {
                "BlockIndex": b_idx + 1,
                "BlockName": block_name,
                "ItemIndex": i_idx + 1,
                "ItemType": item["type"],
                "SongTitle": "",
                "Artist": "",
                "Tom": "",
                "BPM": "",
                "CifraDriveID": "",
                "CifraSimplificadaID": "",
                "UseSimplificada": "",
                "PauseLabel": "",
            }
            if item["type"] == "music":
                base["SongTitle"] = item.get("title", "")
                base["Artist"] = item.get("artist", "")
                base["Tom"] = item.get("tom", "")
                base["BPM"] = item.get("bpm", "")
                base["CifraDriveID"] = item.get("cifra_id", "")
                base["CifraSimplificadaID"] = item.get("cifra_simplificada_id", "")
                base["UseSimplificada"] = (
                    "1" if item.get("use_simplificada", False) else "0"
                )
            else:
                base["PauseLabel"] = item.get("label", "Pausa")

            rows.append(base)

    df_new = pd.DataFrame(rows, columns=SETLIST_COLS)
    write_setlist_df(name, df_new)


def load_setlist_into_state(setlist_name: str, songs_df: pd.DataFrame):
    df_sel = load_setlist_df(setlist_name)
    if df_sel.empty:
        return

    df_sel["BlockIndex"] = (
        pd.to_numeric(df_sel["BlockIndex"], errors="coerce").fillna(0).astype(int)
    )
    df_sel["ItemIndex"] = (
        pd.to_numeric(df_sel["ItemIndex"], errors="coerce").fillna(0).astype(int)
    )
    df_sel = df_sel.sort_values(["BlockIndex", "ItemIndex"])

    blocks = []
    for (block_idx, block_name), group in df_sel.groupby(
        ["BlockIndex", "BlockName"], sort=True
    ):
        items = []
        for _, row in group.iterrows():
            if row["ItemType"] == "pause":
                items.append(
                    {
                        "type": "pause",
                        "label": row.get("PauseLabel", "Pausa"),
                    }
                )
            else:
                title = row.get("SongTitle", "")
                artist = row.get("Artist", "")
                tom_saved = row.get("Tom", "")
                bpm_saved = row.get("BPM", "")
                cifra_id_saved = str(row.get("CifraDriveID", "")).strip()
                cifra_simplificada_saved = str(
                    row.get("CifraSimplificadaID", "")
                ).strip()
                use_simplificada_saved = str(row.get("UseSimplificada", "0")).strip()
                use_simplificada = use_simplificada_saved in (
                    "1",
                    "true",
                    "True",
                    "Y",
                    "y",
                )

                song_row = songs_df[songs_df["Título"] == title]
                if not song_row.empty:
                    song_row = song_row.iloc[0]
                    tom_original = song_row.get("Tom_Original", "") or tom_saved
                    cifra_id_bank = str(song_row.get("CifraDriveID", "")).strip()
                    cifra_simplificada_bank = str(
                        song_row.get("CifraSimplificadaID", "")
                    ).strip()

                    cifra_id = cifra_id_saved or cifra_id_bank
                    cifra_simplificada_id = (
                        cifra_simplificada_saved or cifra_simplificada_bank
                    )
                else:
                    tom_original = tom_saved
                    cifra_id = cifra_id_saved
                    cifra_simplificada_id = cifra_simplificada_saved

                items.append(
                    {
                        "type": "music",
                        "title": title,
                        "artist": artist,
                        "tom_original": tom_original,
                        "tom": tom_saved or tom_original,
                        "bpm": bpm_saved,
                        "cifra_id": cifra_id,
                        "cifra_simplificada_id": cifra_simplificada_id,
                        "use_simplificada": use_simplificada,
                        "text": "",
                    }
                )

        blocks.append(
            {
                "name": block_name or f"Bloco {len(blocks) + 1}",
                "items": items,
            }
        )

    st.session_state.blocks = blocks
    st.session_state.setlist_name = setlist_name
    st.session_state.current_item = None
    st.session_state.selected_block_idx = None
    st.session_state.selected_item_idx = None
    st.session_state.screen = "editor"


# ==============================================================
# 6) ESTADO INICIAL
# ==============================================================

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

    if "screen" not in st.session_state:
        st.session_state.screen = "home"

    # seleção do modo árvore
    if "selected_block_idx" not in st.session_state:
        st.session_state.selected_block_idx = None
    if "selected_item_idx" not in st.session_state:
        st.session_state.selected_item_idx = None

    # textos das novas músicas (banco)
    if "new_song_cifra_original" not in st.session_state:
        st.session_state.new_song_cifra_original = ""
    if "new_song_cifra_simplificada" not in st.session_state:
        st.session_state.new_song_cifra_simplificada = ""


# ==============================================================
# 7) AUX – ORDEM / REMOÇÃO DE ITENS
# ==============================================================

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


# ==============================================================
# 8) HTML – HEADER / FOOTER / PÁGINA
# ==============================================================

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
        raw_body = "PAUSA / INTERVALO"
        tom_original = ""
        tom_atual = ""
    else:
        title = item.get("title", "NOVA MÚSICA")
        artist = item.get("artist", "")
        tom_original = item.get("tom_original", "") or item.get("tom", "")
        tom = item.get("tom", tom_original)
        bpm = item.get("bpm", "")

        use_simplificada = item.get("use_simplificada", False)
        cifra_id = item.get("cifra_id", "")
        cifra_simplificada_id = item.get("cifra_simplificada_id", "")

        if use_simplificada and cifra_simplificada_id:
            raw_body = load_chord_from_drive(cifra_simplificada_id)
        elif cifra_id:
            raw_body = load_chord_from_drive(cifra_id)
        else:
            raw_body = item.get(
                "text", "CIFRA / TEXTO AQUI (ainda não cadastrada)."
            )
        tom_atual = tom

    if item["type"] == "pause":
        body_final = raw_body
    else:
        body_transposed = transpose_body_text(raw_body, tom_original, tom_atual)
        body_norm = normalize_lyrics_indent(body_transposed)
        body_final = strip_chord_markers_for_display(body_norm)

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


# ==============================================================
# 9) EDITOR EM ÁRVORE (SETLIST)
# ==============================================================

def render_selected_item_editor():
    """Form completo apenas para o item selecionado na árvore."""
    b_idx = st.session_state.get("selected_block_idx", None)
    i_idx = st.session_state.get("selected_item_idx", None)

    if b_idx is None or i_idx is None:
        st.info("Selecione uma música ou pausa na árvore acima para editar os detalhes.")
        return

    blocks = st.session_state.blocks
    if not (0 <= b_idx < len(blocks)):
        st.warning("Bloco selecionado inválido.")
        return

    items = blocks[b_idx]["items"]
    if not (0 <= i_idx < len(items)):
        st.warning("Item selecionado inválido.")
        return

    item = items[i_idx]

    st.markdown("---")
    st.markdown(f"#### Detalhes do item (Bloco {b_idx+1}, posição {i_idx+1})")

    # ---------- MÚSICA ----------
    if item["type"] == "music":
        title = item.get("title", "Nova música")
        artist = item.get("artist", "")
        st.markdown(f"**🎵 {title}**")
        if artist:
            st.caption(artist)

        use_simplificada = item.get("use_simplificada", False)
        btn_label = "Usar cifra ORIGINAL" if use_simplificada else "Usar cifra SIMPLIFICADA"
        if st.button(
            btn_label,
            key=f"simpl_toggle_{b_idx}_{i_idx}",
            help="Alternar entre cifra original e versão simplificada",
        ):
            item["use_simplificada"] = not use_simplificada
            st.session_state.current_item = (b_idx, i_idx)
            st.rerun()

        cifra_id = item.get("cifra_id", "")
        cifra_simplificada_id = item.get("cifra_simplificada_id", "")

        with st.expander("Ver / editar cifra (texto)", expanded=True):
            if item.get("use_simplificada") and cifra_simplificada_id:
                current_id = cifra_simplificada_id
            elif cifra_id:
                current_id = cifra_id
            else:
                current_id = None

            if current_id:
                cifra_text = load_chord_from_drive(current_id)
            else:
                cifra_text = item.get("text", "")

            font_size = st.session_state.cifra_font_size
            col_font_minus, col_font_plus = st.columns(2)
            if col_font_minus.button("A﹣", key=f"font_minus_sel_{b_idx}_{i_idx}"):
                st.session_state.cifra_font_size = max(8, font_size - 1)
                st.rerun()
            if col_font_plus.button("A﹢", key=f"font_plus_sel_{b_idx}_{i_idx}"):
                st.session_state.cifra_font_size = min(24, font_size + 1)
                st.rerun()

            edited = st.text_area(
                "Cifra",
                value=cifra_text,
                height=300,
                key=f"cifra_edit_sel_{b_idx}_{i_idx}",
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

            if st.button("Salvar cifra", key=f"save_cifra_sel_{b_idx}_{i_idx}"):
                if current_id:
                    save_chord_to_drive(current_id, edited)
                    st.success("Cifra atualizada no Drive.")
                else:
                    item["text"] = edited
                    st.success("Cifra salva apenas neste setlist (sem arquivo no Drive).")
                st.rerun()

        bpm_val = item.get("bpm", "")
        tom_original = item.get("tom_original", "") or item.get("tom", "")
        tom_val = item.get("tom", tom_original)

        lab_bpm, lab_tom = st.columns(2)
        lab_bpm.markdown(
            "<p style='text-align:center;font-size:0.8rem;'>BPM</p>",
            unsafe_allow_html=True,
        )
        lab_tom.markdown(
            "<p style='text-align:center;font-size:0.8rem;'>Tom</p>",
            unsafe_allow_html=True,
        )

        col_bpm, col_tom = st.columns(2)

        new_bpm = col_bpm.text_input(
            "BPM",
            value=str(bpm_val) if bpm_val not in ("", None, 0) else "",
            key=f"bpm_sel_{b_idx}_{i_idx}",
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
            key=f"tom_select_sel_{b_idx}_{i_idx}",
            label_visibility="collapsed",
        )
        if selected_tone != tom_val:
            item["tom"] = selected_tone
            st.session_state.current_item = (b_idx, i_idx)
            st.rerun()

    # ---------- PAUSA ----------
    else:
        st.markdown("**⏸ Pausa**")
        new_label = st.text_input(
            "Descrição da pausa",
            value=item.get("label", "Pausa"),
            key=f"pause_label_{b_idx}_{i_idx}",
        )
        item["label"] = new_label


def render_setlist_editor_tree():
    """Estrutura em árvore: Setlist -> Blocos -> Músicas / Pausas."""
    blocks = st.session_state.blocks
    songs_df = st.session_state.songs_df

    st.markdown("### Estrutura da Setlist (modo árvore)")

    if st.button("+ Adicionar bloco", use_container_width=True, key="btn_add_block_global"):
        st.session_state.blocks.append(
            {"name": f"Bloco {len(blocks) + 1}", "items": []}
        )
        st.rerun()

    for b_idx, block in enumerate(blocks):
        with st.expander(f"Bloco {b_idx + 1}: {block['name']}", expanded=False):
            name_col, up_col, down_col, del_col = st.columns([6, 1, 1, 1])
            new_name = name_col.text_input(
                "Nome do bloco",
                value=block["name"],
                key=f"blk_name_{b_idx}",
                label_visibility="collapsed",
            )
            block["name"] = new_name

            if up_col.button("↑", key=f"blk_up_{b_idx}"):
                move_block(b_idx, -1)
                st.rerun()
            if down_col.button("↓", key=f"blk_down_{b_idx}"):
                move_block(b_idx, 1)
                st.rerun()
            if del_col.button("✕", key=f"blk_del_{b_idx}"):
                delete_block(b_idx)
                st.rerun()

            st.markdown("---")

            # Itens dentro do bloco
            for i, item in enumerate(block["items"]):
                col_label, col_btns = st.columns([8, 2])
                if item["type"] == "music":
                    title = item.get("title", "Nova música")
                    artist = item.get("artist", "")
                    label = f"🎵 {title}"
                    if artist:
                        label += f" – {artist}"
                else:
                    label = f"⏸ {item.get('label', 'Pausa')}"

                if col_label.button(label, key=f"sel_item_{b_idx}_{i}"):
                    st.session_state.selected_block_idx = b_idx
                    st.session_state.selected_item_idx = i
                    st.session_state.current_item = (b_idx, i)
                    st.rerun()

                with col_btns:
                    col_u, col_d, col_x, col_p = st.columns(4)
                    if col_u.button("↑", key=f"it_up_{b_idx}_{i}"):
                        move_item(b_idx, i, -1)
                        st.rerun()
                    if col_d.button("↓", key=f"it_down_{b_idx}_{i}"):
                        move_item(b_idx, i, 1)
                        st.rerun()
                    if col_x.button("✕", key=f"it_del_{b_idx}_{i}"):
                        delete_item(b_idx, i)
                        st.rerun()
                    if col_p.button("👁", key=f"it_prev_{b_idx}_{i}"):
                        st.session_state.current_item = (b_idx, i)
                        st.rerun()

            st.markdown("---")

            col_add_mus, col_add_pause = st.columns(2)
            if col_add_mus.button("+ Música do banco", key=f"add_mus_blk_{b_idx}"):
                st.session_state[f"show_add_music_block_{b_idx}"] = True
            if col_add_pause.button("+ Pausa", key=f"add_pause_blk_{b_idx}"):
                block["items"].append({"type": "pause", "label": "Pausa"})
                st.rerun()

            if st.session_state.get(f"show_add_music_block_{b_idx}", False):
                st.markdown("##### Adicionar músicas deste bloco")
                all_titles = list(songs_df["Título"])
                selected = st.multiselect(
                    "Escolha as músicas do banco",
                    options=all_titles,
                    key=f"mus_select_blk_{b_idx}",
                )
                if st.button("Adicionar selecionadas", key=f"confirm_add_mus_blk_{b_idx}"):
                    for title in selected:
                        row = songs_df[songs_df["Título"] == title].iloc[0]
                        cifra_id = str(row.get("CifraDriveID", "")).strip()
                        cifra_simplificada_id = str(
                            row.get("CifraSimplificadaID", "")
                        ).strip()
                        new_item = {
                            "type": "music",
                            "title": row.get("Título", ""),
                            "artist": row.get("Artista", ""),
                            "tom_original": row.get("Tom_Original", ""),
                            "tom": row.get("Tom_Original", ""),
                            "bpm": row.get("BPM", ""),
                            "cifra_id": cifra_id,
                            "cifra_simplificada_id": cifra_simplificada_id,
                            "use_simplificada": False,
                            "text": "",
                        }
                            # append
                        block["items"].append(new_item)

                    st.session_state[f"show_add_music_block_{b_idx}"] = False
                    st.rerun()

    render_selected_item_editor()


# ==============================================================
# 10) BANCO DE MÚSICAS – COM TELA DE CRIAÇÃO / GEMINI
# ==============================================================

def render_song_database():
    st.subheader("Banco de músicas (Google Sheets)")

    df = st.session_state.songs_df
    st.dataframe(df, use_container_width=True, height=240)

    with st.expander("Adicionar nova música ao banco"):
        col1, col2 = st.columns(2)
        with col1:
            title = st.text_input("Título")
            artist = st.text_input("Artista")
        with col2:
            tom_original = st.text_input("Tom original (ex.: Fm, C, Gm)")
            bpm = st.text_input("BPM")

        st.markdown("---")

        # -------- Cifra ORIGINAL --------
        st.markdown("#### 1) Cifra ORIGINAL")
        up_orig = st.file_uploader(
            "Opcional: envie uma imagem (.jpg/.png) ou .txt da cifra original",
            type=["jpg", "jpeg", "png", "txt"],
            key="upload_orig_gemini",
        )

        col_tr_o1, col_tr_o2 = st.columns([1, 3])
        with col_tr_o1:
            if st.button("Transcrever imagem com Gemini (Original)"):
                if up_orig is None:
                    st.warning("Envie uma imagem primeiro.")
                else:
                    if up_orig.type == "text/plain":
                        text = up_orig.getvalue().decode("utf-8", errors="replace")
                    else:
                        text = transcribe_image_with_gemini(up_orig)
                    st.session_state.new_song_cifra_original = text

        with col_tr_o2:
            st.caption(
                "Use esse botão apenas se tiver subido uma imagem. "
                "O resultado aparecerá abaixo para você editar."
            )

        st.session_state.new_song_cifra_original = st.text_area(
            "Texto da cifra ORIGINAL",
            value=st.session_state.new_song_cifra_original,
            height=240,
            key="txt_cifra_original",
        )

        st.markdown("---")

        # -------- Cifra SIMPLIFICADA --------
        st.markdown("#### 2) Cifra SIMPLIFICADA (opcional)")
        up_simpl = st.file_uploader(
            "Opcional: envie uma imagem (.jpg/.png) ou .txt da cifra simplificada",
            type=["jpg", "jpeg", "png", "txt"],
            key="upload_simpl_gemini",
        )

        col_tr_s1, col_tr_s2 = st.columns([1, 3])
        with col_tr_s1:
            if st.button("Transcrever imagem com Gemini (Simplificada)"):
                if up_simpl is None:
                    st.warning("Envie uma imagem primeiro.")
                else:
                    if up_simpl.type == "text/plain":
                        text_s = up_simpl.getvalue().decode("utf-8", errors="replace")
                    else:
                        text_s = transcribe_image_with_gemini(up_simpl)
                    st.session_state.new_song_cifra_simplificada = text_s

        with col_tr_s2:
            st.caption(
                "Também opcional. Se não usar, deixe em branco."
            )

        st.session_state.new_song_cifra_simplificada = st.text_area(
            "Texto da cifra SIMPLIFICADA",
            value=st.session_state.new_song_cifra_simplificada,
            height=240,
            key="txt_cifra_simplificada",
        )

        st.markdown("---")
        st.markdown("#### 3) Salvar no banco (Drive + Sheets)")

        if st.button("Salvar nova música no banco", key="btn_save_new_song"):
            if not title.strip():
                st.warning("Preencha pelo menos o título.")
            else:
                with st.spinner("Criando arquivos no Drive e salvando no Sheets..."):
                    content_orig = st.session_state.new_song_cifra_original or ""
                    content_simpl = st.session_state.new_song_cifra_simplificada or ""

                    final_cifra_id = ""
                    final_simpl_id = ""

                    if content_orig.strip():
                        nome_arquivo_orig = f"{title} - {artist} (Original)"
                        new_id = create_chord_in_drive(nome_arquivo_orig, content_orig)
                        final_cifra_id = new_id or ""

                    if content_simpl.strip():
                        nome_arquivo_simpl = f"{title} - {artist} (Simplificada)"
                        new_s_id = create_chord_in_drive(
                            nome_arquivo_simpl, content_simpl
                        )
                        final_simpl_id = new_s_id or ""

                    append_song_to_sheet(
                        title,
                        artist,
                        tom_original,
                        bpm,
                        final_cifra_id,
                        final_simpl_id,
                    )

                    # limpa textos
                    st.session_state.new_song_cifra_original = ""
                    st.session_state.new_song_cifra_simplificada = ""

                    st.success(f"Música '{title}' cadastrada com sucesso!")
                    st.session_state.songs_df = load_songs_df()
                    st.rerun()


# ==============================================================
# 11) TELA INICIAL
# ==============================================================

def render_home():
    st.title("PDL Setlist")

    setlists = list_setlist_names()

    col_new, col_load = st.columns(2)

    with col_new:
        st.subheader("Nova setlist")
        default_name = st.session_state.get("setlist_name", "Pagode do LEC")
        new_name = st.text_input(
            "Nome da nova setlist (nome da aba)",
            value=default_name,
            key="new_setlist_name",
        )
        if st.button("Criar setlist"):
            st.session_state.setlist_name = new_name.strip() or "Setlist sem nome"
            st.session_state.blocks = [{"name": "Bloco 1", "items": []}]
            st.session_state.current_item = None
            st.session_state.selected_block_idx = None
            st.session_state.selected_item_idx = None
            st.session_state.screen = "editor"
            st.rerun()

    with col_load:
        st.subheader("Carregar setlist existente")
        if setlists:
            selected = st.selectbox(
                "Escolha a setlist (aba)",
                options=setlists,
                key="load_setlist_select",
            )
            if st.button("Carregar esta setlist"):
                load_setlist_into_state(selected, st.session_state.songs_df)
                st.rerun()
        else:
            st.info(
                "Nenhuma aba de setlist encontrada (apenas a primeira é o banco de músicas)."
            )


# ==============================================================
# 12) MAIN
# ==============================================================

def main():
    st.set_page_config(
        page_title="PDL Setlist",
        layout="wide",
        page_icon="🎵",
    )

    init_state()

    if st.session_state.screen == "home":
        render_home()
        return

    top_left, top_right = st.columns([3, 1])
    with top_left:
        st.markdown(f"### Setlist: {st.session_state.setlist_name}")
        st.session_state.setlist_name = st.text_input(
            "Nome do setlist (também será o nome da aba)",
            value=st.session_state.setlist_name,
            label_visibility="collapsed",
        )
    with top_right:
        if st.button("🏠 Voltar à tela inicial", use_container_width=True):
            st.session_state.screen = "home"
            st.rerun()
        if st.button("💾 Salvar setlist (aba)", use_container_width=True):
            save_current_setlist_to_sheet()
            st.success("Setlist salva na aba correspondente do Google Sheets.")

    left_col, right_col = st.columns([1.1, 1])

    with left_col:
        st.subheader("Editor de Setlist (modo árvore)")

        render_setlist_editor_tree()

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

            st.components.v1.html(html, height=1200, scrolling=True)


if __name__ == "__main__":
    main()
