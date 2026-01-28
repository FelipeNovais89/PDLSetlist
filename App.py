import streamlit as st
import pandas as pd
import io
import re
import json
import base64
import requests

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.errors import HttpError

import google.generativeai as genai


# ==============================================================
# 0) CONFIG
# ==============================================================

CSV_RAW_URL_DEFAULT = "https://raw.githubusercontent.com/FelipeNovais89/PDLSetlist/refs/heads/main/Data/PDL_musicas.csv"
DEFAULT_SETLIST_NAME = "Pagode do LEC"
DEFAULT_GEMINI_MODEL = "models/gemini-2.5-flash"

REQUIRED_SONG_COLS = [
    "Título",
    "Artista",
    "Tom_Original",
    "BPM",
    "CifraDriveID",
    "CifraSimplificadaID",
]

SETLIST_COLS = [
    "BlockIndex",
    "BlockName",
    "ItemIndex",
    "ItemType",  # music|pause
    "SongTitle",
    "Artist",
    "Tom",
    "BPM",
    "CifraDriveID",
    "CifraSimplificadaID",
    "UseSimplificada",
    "PauseLabel",
]


# ==============================================================
# 1) SECRETS HELPERS
# ==============================================================

def get_gemini_api_key():
    try:
        if "gemini_api_key" in st.secrets:
            return st.secrets["gemini_api_key"]
        if "sheets" in st.secrets and "gemini_api_key" in st.secrets["sheets"]:
            return st.secrets["sheets"]["gemini_api_key"]
    except Exception:
        pass
    return None


def get_github_token():
    """
    Opcional. Se existir, o app consegue salvar o CSV atualizado no GitHub automaticamente.
    Coloque em secrets:
      github_token = "ghp_...."
    """
    try:
        if "github_token" in st.secrets:
            return st.secrets["github_token"]
    except Exception:
        pass
    return None


def get_github_repo_info():
    """
    Opcional. Se existir, permite salvar CSV no GitHub.
    secrets:
      github_owner = "FelipeNovais89"
      github_repo = "PDLSetlist"
      github_branch = "main"
      github_csv_path = "Data/PDL_musicas.csv"
    """
    owner = st.secrets.get("github_owner", "FelipeNovais89")
    repo = st.secrets.get("github_repo", "PDLSetlist")
    branch = st.secrets.get("github_branch", "main")
    path = st.secrets.get("github_csv_path", "Data/PDL_musicas.csv")
    return owner, repo, branch, path


# ==============================================================
# 2) GEMINI
# ==============================================================

GEMINI_API_KEY = get_gemini_api_key()
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


def transcribe_image_with_gemini(uploaded_file, model_name=DEFAULT_GEMINI_MODEL) -> str:
    if not GEMINI_API_KEY:
        st.error("Gemini API key não configurada em st.secrets.")
        return ""

    try:
        model = genai.GenerativeModel(model_name)

        prompt = """
Você está transcrevendo uma cifra (acordes + letra) a partir de uma imagem.

REGRAS DE FORMATAÇÃO (IMPORTANTES):
1) Toda linha que contiver apenas ACORDES deve começar com o caractere '|'.
2) Toda linha de LETRA deve começar com um ESPAÇO em branco.
3) Mantenha o alinhamento visual dos acordes acima das sílabas da letra.
4) Ignore diagramas/desenhos do braço do instrumento; foque em texto e acordes.
5) NÃO use markdown, NÃO use ``` e nem cabeçalhos; apenas texto puro.
"""

        mime = uploaded_file.type or "image/jpeg"
        img_data = uploaded_file.getvalue()

        st.info(f"Chamando Gemini com modelo: {model_name}")
        resp = model.generate_content([prompt, {"mime_type": mime, "data": img_data}])
        text = (getattr(resp, "text", "") or "").strip()

        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = "\n".join(text.split("\n")[1:]).strip()

        return text

    except Exception as e:
        st.error(f"Erro ao chamar Gemini: {e}")
        return ""


# ==============================================================
# 3) TRANSPOSE HELPERS
# ==============================================================

NOTE_SEQ_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_SEQ_FLAT  = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

NOTE_TO_INDEX = {
    "C": 0, "C#": 1, "Db": 1,
    "D": 2, "D#": 3, "Eb": 3,
    "E": 4,
    "F": 5, "F#": 6, "Gb": 6,
    "G": 7, "G#": 8, "Ab": 8,
    "A": 9, "A#": 10, "Bb": 10,
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
    else:
        scale = NOTE_SEQ_SHARP

    return scale[(idx + steps) % 12]


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
# 4) GOOGLE DRIVE – TXT DAS CIFRAS
# ==============================================================

def get_drive_service():
    secrets = st.secrets["gcp_service_account"]
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(secrets, scopes=scopes)
    return build("drive", "v3", credentials=creds)


def create_chord_in_drive(filename: str, content: str) -> str:
    """Cria um novo arquivo .txt no Drive e retorna o FileID."""
    if not (content or "").strip():
        return ""

    try:
        service = get_drive_service()
        folder_id = st.secrets.get("drive_folder_id", None)  # opcional

        file_metadata = {"name": f"{filename}.txt", "mimeType": "text/plain"}
        if folder_id:
            file_metadata["parents"] = [folder_id]

        fh = io.BytesIO(content.encode("utf-8"))
        media = MediaIoBaseUpload(fh, mimetype="text/plain")

        created = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id", supportsAllDrives=True)
            .execute()
        )
        return created.get("id", "")
    except HttpError as e:
        st.error(f"Erro no Drive (create): {e}")
        return ""
    except Exception as e:
        st.error(f"Erro inesperado no Drive (create): {e}")
        return ""


@st.cache_data(ttl=300)
def load_chord_from_drive(file_id: str) -> str:
    if not file_id:
        return ""
    file_id = str(file_id).strip()

    try:
        service = get_drive_service()
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        fh.seek(0)
        return fh.read().decode("utf-8", errors="replace")

    except HttpError as e:
        return f"Erro ao carregar cifra do Drive (ID: {file_id}):\n{e}"


def save_chord_to_drive(file_id: str, content: str):
    if not file_id:
        st.warning("Sem FileID do Drive para salvar.")
        return

    file_id = str(file_id).strip()
    try:
        service = get_drive_service()
        fh = io.BytesIO((content or "").encode("utf-8"))
        media = MediaIoBaseUpload(fh, mimetype="text/plain")

        service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
        load_chord_from_drive.clear()
    except HttpError as e:
        st.error(f"Erro ao salvar cifra no Drive (ID: {file_id}): {e}")


# ==============================================================
# 5) CSV NO GITHUB – BANCO DE MÚSICAS
# ==============================================================

@st.cache_data(ttl=120)
def load_songs_df_from_github(csv_raw_url: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(csv_raw_url)
    except Exception as e:
        st.error(f"Erro ao ler CSV do GitHub: {e}")
        return pd.DataFrame(columns=REQUIRED_SONG_COLS)

    for col in REQUIRED_SONG_COLS:
        if col not in df.columns:
            df[col] = ""

    # normaliza para string (evita NaN no UI)
    for c in REQUIRED_SONG_COLS:
        df[c] = df[c].fillna("").astype(str)

    return df


def github_get_file_sha(owner: str, repo: str, path: str, token: str, branch: str):
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        return None, r.text
    data = r.json()
    return data.get("sha"), None


def github_put_file(owner: str, repo: str, path: str, token: str, branch: str, content_bytes: bytes, message: str):
    sha, err = github_get_file_sha(owner, repo, path, token, branch)
    if err:
        return False, err

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": branch,
        "sha": sha,
    }
    r = requests.put(url, headers=headers, data=json.dumps(payload), timeout=30)
    if r.status_code not in (200, 201):
        return False, r.text
    return True, None


def save_songs_df_to_github(df: pd.DataFrame) -> bool:
    token = get_github_token()
    if not token:
        return False

    owner, repo, branch, path = get_github_repo_info()
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    ok, err = github_put_file(
        owner, repo, path, token, branch,
        csv_bytes,
        message="Atualiza PDL_musicas.csv (via app Streamlit)"
    )
    if not ok:
        st.error(f"Falha ao salvar CSV no GitHub: {err}")
        return False
    return True


def append_song_to_csv_in_memory(df: pd.DataFrame, row: dict) -> pd.DataFrame:
    for col in REQUIRED_SONG_COLS:
        if col not in row:
            row[col] = ""
    df2 = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    for c in REQUIRED_SONG_COLS:
        df2[c] = df2[c].fillna("").astype(str)
    return df2


# ==============================================================
# 6) ESTADO
# ==============================================================

def init_state():
    if "csv_raw_url" not in st.session_state:
        st.session_state.csv_raw_url = st.secrets.get("csv_raw_url", CSV_RAW_URL_DEFAULT)

    if "songs_df" not in st.session_state:
        st.session_state.songs_df = load_songs_df_from_github(st.session_state.csv_raw_url)

    if "blocks" not in st.session_state:
        st.session_state.blocks = [{"name": "Bloco 1", "items": []}]

    if "setlist_name" not in st.session_state:
        st.session_state.setlist_name = DEFAULT_SETLIST_NAME

    if "screen" not in st.session_state:
        st.session_state.screen = "home"

    if "current_item" not in st.session_state:
        st.session_state.current_item = None

    if "selected_block_idx" not in st.session_state:
        st.session_state.selected_block_idx = None
    if "selected_item_idx" not in st.session_state:
        st.session_state.selected_item_idx = None

    if "cifra_font_size" not in st.session_state:
        st.session_state.cifra_font_size = 14

    if "new_song_cifra_original" not in st.session_state:
        st.session_state.new_song_cifra_original = ""
    if "new_song_cifra_simplificada" not in st.session_state:
        st.session_state.new_song_cifra_simplificada = ""

    if "gemini_model" not in st.session_state:
        st.session_state.gemini_model = DEFAULT_GEMINI_MODEL


# ==============================================================
# 7) AÇÕES EM BLOCOS/ITENS
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
# 8) HTML PREVIEW
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

        # >>> AQUI É ONDE ELE PEGA O TXT: GOOGLE DRIVE <<<
        if use_simplificada and cifra_simplificada_id:
            raw_body = load_chord_from_drive(cifra_simplificada_id)
        elif cifra_id:
            raw_body = load_chord_from_drive(cifra_id)
        else:
            raw_body = item.get("text", "CIFRA / TEXTO AQUI (sem arquivo no Drive).")

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
        footer_html = build_footer_next_music(next_title, next_artist, next_tone, next_bpm)
    elif footer_mode == "next_pause" and footer_next_item is not None:
        footer_html = build_footer_next_pause(footer_next_item.get("label", "Pausa"))
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

        .sheet-title {{
            font-weight: 700;
            text-transform: uppercase;
            font-size: 10px;
        }}
        .sheet-artist {{
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
            font-size: 10px;
        }}

        .sheet-body {{
            padding: 12px 8px 12px 8px;
            min-height: 420px;
        }}
        .sheet-body-text {{
            white-space: pre-wrap;
            font-family: "Courier New", monospace;
            font-size: 12px;
            line-height: 1.3;
        }}

        .sheet-footer {{
            font-size: 10px;
            margin-top: auto;
            padding-top: 4px;
            border-top: 1px solid #ccc;
        }}

        .sheet-next-pause {{
            font-size: 14px;
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
# 9) UI – EDITOR EM ÁRVORE
# ==============================================================

def render_selected_item_editor():
    b_idx = st.session_state.get("selected_block_idx", None)
    i_idx = st.session_state.get("selected_item_idx", None)

    if b_idx is None or i_idx is None:
        st.info("Selecione uma música ou pausa na árvore acima para editar.")
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

    if item["type"] == "music":
        title = item.get("title", "Nova música")
        artist = item.get("artist", "")
        st.markdown(f"**🎵 {title}**")
        if artist:
            st.caption(artist)

        use_simplificada = item.get("use_simplificada", False)
        btn_label = "Usar cifra ORIGINAL" if use_simplificada else "Usar cifra SIMPLIFICADA"
        if st.button(btn_label, key=f"simpl_toggle_{b_idx}_{i_idx}"):
            item["use_simplificada"] = not use_simplificada
            st.session_state.current_item = (b_idx, i_idx)
            st.rerun()

        cifra_id = item.get("cifra_id", "")
        cifra_simplificada_id = item.get("cifra_simplificada_id", "")

        with st.expander("Ver / editar cifra (Drive .txt)", expanded=True):
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
            colm, colp = st.columns(2)
            if colm.button("A﹣", key=f"font_minus_{b_idx}_{i_idx}"):
                st.session_state.cifra_font_size = max(8, font_size - 1)
                st.rerun()
            if colp.button("A﹢", key=f"font_plus_{b_idx}_{i_idx}"):
                st.session_state.cifra_font_size = min(24, font_size + 1)
                st.rerun()

            edited = st.text_area(
                "Cifra",
                value=cifra_text,
                height=320,
                key=f"cifra_edit_{b_idx}_{i_idx}",
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

            if st.button("Salvar cifra no Drive", key=f"save_cifra_{b_idx}_{i_idx}"):
                if current_id:
                    save_chord_to_drive(current_id, edited)
                    st.success("Cifra atualizada no Drive.")
                else:
                    item["text"] = edited
                    st.warning("Sem ID do Drive. Salvei só no setlist (memória).")
                st.rerun()

        bpm_val = item.get("bpm", "")
        tom_original = item.get("tom_original", "") or item.get("tom", "")
        tom_val = item.get("tom", tom_original)

        colb, colt = st.columns(2)
        item["bpm"] = colb.text_input(
            "BPM",
            value=str(bpm_val) if bpm_val not in ("", None, 0) else "",
            key=f"bpm_{b_idx}_{i_idx}",
        )

        if tom_original.endswith("m"):
            tone_list = [t for t in TONE_OPTIONS if t.endswith("m")]
        else:
            tone_list = [t for t in TONE_OPTIONS if not t.endswith("m")]

        if tom_val and tom_val not in tone_list:
            tone_list = [tom_val] + tone_list

        idx = tone_list.index(tom_val) if tom_val in tone_list else 0
        new_tom = colt.selectbox(
            "Tom",
            options=tone_list,
            index=idx,
            key=f"tom_{b_idx}_{i_idx}",
        )
        item["tom"] = new_tom

    else:
        st.markdown("**⏸ Pausa**")
        item["label"] = st.text_input(
            "Descrição da pausa",
            value=item.get("label", "Pausa"),
            key=f"pause_label_{b_idx}_{i_idx}",
        )


def render_setlist_editor_tree():
    blocks = st.session_state.blocks
    songs_df = st.session_state.songs_df

    st.markdown("### Estrutura da Setlist (modo árvore)")

    if st.button("+ Adicionar bloco", use_container_width=True):
        st.session_state.blocks.append({"name": f"Bloco {len(blocks) + 1}", "items": []})
        st.rerun()

    for b_idx, block in enumerate(blocks):
        with st.expander(f"Bloco {b_idx + 1}: {block['name']}", expanded=False):
            name_col, up_col, down_col, del_col = st.columns([6, 1, 1, 1])
            block["name"] = name_col.text_input(
                "Nome do bloco",
                value=block["name"],
                key=f"blk_name_{b_idx}",
                label_visibility="collapsed",
            )

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

            for i, item in enumerate(block["items"]):
                col_label, col_btns = st.columns([8, 2])

                if item["type"] == "music":
                    label = f"🎵 {item.get('title','')}"
                    if item.get("artist"):
                        label += f" – {item.get('artist')}"
                else:
                    label = f"⏸ {item.get('label','Pausa')}"

                if col_label.button(label, key=f"sel_{b_idx}_{i}"):
                    st.session_state.selected_block_idx = b_idx
                    st.session_state.selected_item_idx = i
                    st.session_state.current_item = (b_idx, i)
                    st.rerun()

                with col_btns:
                    cu, cd, cx, cp = st.columns(4)
                    if cu.button("↑", key=f"it_up_{b_idx}_{i}"):
                        move_item(b_idx, i, -1)
                        st.rerun()
                    if cd.button("↓", key=f"it_down_{b_idx}_{i}"):
                        move_item(b_idx, i, 1)
                        st.rerun()
                    if cx.button("✕", key=f"it_del_{b_idx}_{i}"):
                        delete_item(b_idx, i)
                        st.rerun()
                    if cp.button("👁", key=f"it_prev_{b_idx}_{i}"):
                        st.session_state.current_item = (b_idx, i)
                        st.rerun()

            st.markdown("---")

            col_add_m, col_add_p = st.columns(2)
            if col_add_m.button("+ Música do banco", key=f"add_m_{b_idx}"):
                st.session_state[f"show_add_music_{b_idx}"] = True
            if col_add_p.button("+ Pausa", key=f"add_p_{b_idx}"):
                block["items"].append({"type": "pause", "label": "Pausa"})
                st.rerun()

            if st.session_state.get(f"show_add_music_{b_idx}", False):
                all_titles = list(songs_df["Título"].astype(str))
                selected = st.multiselect("Escolha as músicas", options=all_titles, key=f"ms_{b_idx}")
                if st.button("Adicionar selecionadas", key=f"ms_ok_{b_idx}"):
                    for title in selected:
                        row = songs_df[songs_df["Título"].astype(str) == str(title)].iloc[0]
                        new_item = {
                            "type": "music",
                            "title": row.get("Título", ""),
                            "artist": row.get("Artista", ""),
                            "tom_original": row.get("Tom_Original", ""),
                            "tom": row.get("Tom_Original", ""),
                            "bpm": row.get("BPM", ""),
                            "cifra_id": str(row.get("CifraDriveID", "")).strip(),
                            "cifra_simplificada_id": str(row.get("CifraSimplificadaID", "")).strip(),
                            "use_simplificada": False,
                            "text": "",
                        }
                        block["items"].append(new_item)

                    st.session_state[f"show_add_music_{b_idx}"] = False
                    st.rerun()

    render_selected_item_editor()


# ==============================================================
# 10) BANCO DE MÚSICAS (CSV GitHub) + CRIAÇÃO TXT DRIVE
# ==============================================================

def render_song_database():
    st.subheader("Banco de músicas (CSV no GitHub)")

    df = st.session_state.songs_df
    st.dataframe(df, use_container_width=True, height=240)

    with st.expander("Configuração do CSV (GitHub)", expanded=False):
        st.session_state.csv_raw_url = st.text_input(
            "CSV RAW URL",
            value=st.session_state.csv_raw_url,
        )
        if st.button("Recarregar CSV agora"):
            st.session_state.songs_df = load_songs_df_from_github(st.session_state.csv_raw_url)
            st.rerun()

    with st.expander("Adicionar nova música ao banco", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            title = st.text_input("Título", key="new_title")
            artist = st.text_input("Artista", key="new_artist")
        with col2:
            tom_original = st.text_input("Tom original (ex.: Fm, C, Gm)", key="new_tom")
            bpm = st.text_input("BPM", key="new_bpm")

        st.markdown("---")

        st.session_state.gemini_model = st.text_input(
            "Modelo Gemini",
            value=st.session_state.gemini_model,
            help="Ex.: models/gemini-2.5-flash",
        )

        # ORIGINAL
        st.markdown("#### 1) Cifra ORIGINAL (gera .txt no Drive)")
        up_orig = st.file_uploader(
            "Envie imagem (.jpg/.png) ou .txt (Original)",
            type=["jpg", "jpeg", "png", "txt"],
            key="up_orig",
        )

        c1, c2 = st.columns([1, 3])
        with c1:
            if st.button("Transcrever (Original)"):
                if up_orig is None:
                    st.warning("Envie uma imagem/arquivo primeiro.")
                else:
                    if up_orig.type == "text/plain":
                        text = up_orig.getvalue().decode("utf-8", errors="replace")
                    else:
                        text = transcribe_image_with_gemini(up_orig, st.session_state.gemini_model)
                    st.session_state.new_song_cifra_original = text
        with c2:
            st.caption("Transcreve e coloca abaixo para você editar antes de salvar.")

        st.session_state.new_song_cifra_original = st.text_area(
            "Texto da cifra ORIGINAL",
            value=st.session_state.new_song_cifra_original,
            height=220,
            key="txt_orig",
        )

        st.markdown("---")

        # SIMPLIFICADA
        st.markdown("#### 2) Cifra SIMPLIFICADA (opcional)")
        up_simpl = st.file_uploader(
            "Envie imagem (.jpg/.png) ou .txt (Simplificada)",
            type=["jpg", "jpeg", "png", "txt"],
            key="up_simpl",
        )

        s1, s2 = st.columns([1, 3])
        with s1:
            if st.button("Transcrever (Simplificada)"):
                if up_simpl is None:
                    st.warning("Envie uma imagem/arquivo primeiro.")
                else:
                    if up_simpl.type == "text/plain":
                        text_s = up_simpl.getvalue().decode("utf-8", errors="replace")
                    else:
                        text_s = transcribe_image_with_gemini(up_simpl, st.session_state.gemini_model)
                    st.session_state.new_song_cifra_simplificada = text_s
        with s2:
            st.caption("Opcional. Se não usar, deixe vazio.")

        st.session_state.new_song_cifra_simplificada = st.text_area(
            "Texto da cifra SIMPLIFICADA",
            value=st.session_state.new_song_cifra_simplificada,
            height=220,
            key="txt_simpl",
        )

        st.markdown("---")
        st.markdown("#### 3) Salvar (Drive + CSV GitHub)")

        if st.button("Salvar nova música", key="btn_save_song"):
            if not title.strip():
                st.warning("Preencha pelo menos o título.")
                return

            with st.spinner("Criando .txt no Drive..."):
                content_orig = (st.session_state.new_song_cifra_original or "").strip()
                content_simpl = (st.session_state.new_song_cifra_simplificada or "").strip()

                cifra_id = ""
                cifra_s_id = ""

                if content_orig:
                    cifra_id = create_chord_in_drive(f"{title} - {artist} (Original)", content_orig)

                if content_simpl:
                    cifra_s_id = create_chord_in_drive(f"{title} - {artist} (Simplificada)", content_simpl)

                new_row = {
                    "Título": title.strip(),
                    "Artista": artist.strip(),
                    "Tom_Original": tom_original.strip(),
                    "BPM": str(bpm).strip(),
                    "CifraDriveID": cifra_id,
                    "CifraSimplificadaID": cifra_s_id,
                }

                st.session_state.songs_df = append_song_to_csv_in_memory(st.session_state.songs_df, new_row)

            token = get_github_token()
            if token:
                with st.spinner("Salvando CSV no GitHub..."):
                    ok = save_songs_df_to_github(st.session_state.songs_df)
                if ok:
                    st.success("Salvo no GitHub ✅")
                    load_songs_df_from_github.clear()
                else:
                    st.warning("Não consegui salvar no GitHub automaticamente. Veja o erro acima.")
            else:
                st.warning("Sem github_token em secrets. Vou te deixar baixar o CSV atualizado para você commitar manualmente.")

            # download do CSV atualizado (sempre disponível)
            csv_bytes = st.session_state.songs_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Baixar CSV atualizado",
                data=csv_bytes,
                file_name="PDL_musicas.csv",
                mime="text/csv",
            )

            # limpar
            st.session_state.new_song_cifra_original = ""
            st.session_state.new_song_cifra_simplificada = ""
            st.success(f"Música '{title}' adicionada no banco (memória).")


# ==============================================================
# 11) HOME
# ==============================================================

def render_home():
    st.title("PDL Setlist")

    st.markdown("### Entrar no Editor")
    if st.button("Abrir Editor"):
        st.session_state.screen = "editor"
        st.rerun()


# ==============================================================
# 12) MAIN
# ==============================================================

def main():
    st.set_page_config(page_title="PDL Setlist", layout="wide", page_icon="🎵")
    init_state()

    if st.session_state.screen == "home":
        render_home()
        return

    top_left, top_right = st.columns([3, 1])
    with top_left:
        st.markdown(f"### Setlist: {st.session_state.setlist_name}")
        st.session_state.setlist_name = st.text_input(
            "Nome do setlist",
            value=st.session_state.setlist_name,
            label_visibility="collapsed",
        )

    with top_right:
        if st.button("🏠 Voltar", use_container_width=True):
            st.session_state.screen = "home"
            st.rerun()

    left_col, right_col = st.columns([1.1, 1])

    with left_col:
        st.subheader("Editor (árvore)")
        render_setlist_editor_tree()
        st.markdown("---")
        render_song_database()

    with right_col:
        st.subheader("Preview (TXT vem do Drive)")

        blocks = st.session_state.blocks
        cur = st.session_state.current_item

        current_item = None
        current_block_name = ""
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
            footer_mode, footer_next_item = get_footer_context(blocks, cur_block_idx, cur_item_idx)
            html = build_sheet_page_html(current_item, footer_mode, footer_next_item, current_block_name)
            st.components.v1.html(html, height=1200, scrolling=True)


if __name__ == "__main__":
    main()
