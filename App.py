# App.py — PDL Setlist (GitHub CSV para banco/setlists + Google Drive para TXT das cifras)
# ✅ Corrige:
# - Funções duplicadas
# - Indentação quebrada
# - Selectbox no mobile (músicas aparecem)
# - Mantém TXT das cifras no Google Drive (você migrou só o CSV)
#
# Requisitos em st.secrets:
# [github]
# token = "ghp_...."          # (obrigatório p/ salvar setlists)
# owner = "FelipeNovais89"
# repo = "PDLSetlist"
# branch = "main"
# setlists_dir = "Data/Setlists"
# songs_csv_url = "https://raw.githubusercontent.com/FelipeNovais89/PDLSetlist/refs/heads/main/Data/PDL_musicas.csv"
#
# [gcp_service_account]  (JSON do service account do Google)
#
# [drive]
# folder_id = "..."           # (opcional) pasta onde salvar os txt
#
# gemini_api_key = "..."      # (opcional) só se usar transcrição por imagem

import streamlit as st
import pandas as pd
import io
import re
import base64
import json
import requests
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

try:
    import google.generativeai as genai
except Exception:
    genai = None


# ==============================================================
# 1) GEMINI – API KEY
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


GEMINI_API_KEY = get_gemini_api_key()
if GEMINI_API_KEY and genai is not None:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception:
        pass


# ==============================================================
# 2) CONSTANTES + TRANSPOSIÇÃO REAL DA CIFRA
# ==============================================================

import re

# -----------------------------
# Sequências de notas
# -----------------------------
NOTE_SEQ_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_SEQ_FLAT  = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

NOTE_TO_INDEX = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
    "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8,
    "A": 9, "A#": 10, "Bb": 10, "B": 11,
}

# -----------------------------
# Lista de tons (para UI)
# -----------------------------
_TONE_BASES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
TONE_OPTIONS = []
for r in _TONE_BASES:
    TONE_OPTIONS.append(r)
    TONE_OPTIONS.append(r + "m")


# ==============================================================
# AUX: exibição da cifra (remove "|")
# ==============================================================

def strip_chord_markers_for_display(text: str) -> str:
    """
    Remove o marcador '|' das linhas de acorde (apenas para exibição).
    """
    lines = (text or "").splitlines()
    out = []
    for line in lines:
        if line.startswith("|"):
            out.append(line[1:])
        else:
            out.append(line)
    return "\n".join(out)


# ==============================================================
# TRANSPOSIÇÃO REAL DE ACORDES
# ==============================================================

def _split_root_and_suffix(chord: str):
    """
    Divide um acorde em:
      - root (C, C#, Db, etc)
      - suffix (m7, 7(9), sus4, /G, etc)
    """
    chord = (chord or "").strip()
    if not chord:
        return "", ""

    m = re.match(r"^([A-G])([#b]?)(.*)$", chord)
    if not m:
        return chord, ""

    root = m.group(1) + (m.group(2) or "")
    suffix = m.group(3) or ""
    return root, suffix


def _prefer_flats(tone: str) -> bool:
    """
    Decide se deve usar bemóis (Db, Eb, Bb) em vez de sustenidos.
    """
    t = (tone or "").strip()
    if "b" in t:
        return True

    flat_keys = {
        "F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb",
        "Fm", "Bbm", "Ebm", "Abm", "Dbm", "Gbm", "Cbm",
    }
    return t in flat_keys


def _semitone_diff(from_tone: str, to_tone: str) -> int:
    """
    Calcula diferença em semitons (to - from), ignorando 'm'.
    """
    a = (from_tone or "").strip()
    b = (to_tone or "").strip()

    if a.endswith("m"):
        a = a[:-1]
    if b.endswith("m"):
        b = b[:-1]

    if a not in NOTE_TO_INDEX or b not in NOTE_TO_INDEX:
        return 0

    return (NOTE_TO_INDEX[b] - NOTE_TO_INDEX[a]) % 12


def _transpose_root(root: str, delta: int, use_flats: bool) -> str:
    """
    Transpõe apenas a nota fundamental do acorde.
    """
    root = (root or "").strip()
    if not root or root not in NOTE_TO_INDEX:
        return root

    idx = NOTE_TO_INDEX[root]
    new_idx = (idx + delta) % 12
    return (NOTE_SEQ_FLAT if use_flats else NOTE_SEQ_SHARP)[new_idx]


def transpose_chord_symbol(chord: str, delta: int, use_flats: bool) -> str:
    """
    Transpõe um acorde completo:
      C/E, F#m7(b5), G7(13), etc
    """
    chord = (chord or "").strip()
    if not chord:
        return chord

    # Slash chord (baixo)
    if "/" in chord:
        main, bass = chord.split("/", 1)
        main_t = transpose_chord_symbol(main, delta, use_flats)

        bass_root, bass_suf = _split_root_and_suffix(bass)
        bass_t = _transpose_root(bass_root, delta, use_flats) + (bass_suf or "")

        return f"{main_t}/{bass_t}"

    root, suffix = _split_root_and_suffix(chord)
    new_root = _transpose_root(root, delta, use_flats)
    return new_root + (suffix or "")


def transpose_chord_text(text: str, from_tone: str, to_tone: str) -> str:
    """
    Transpõe a cifra inteira respeitando o formato:
      - Linhas de acordes começam com '|'
      - Apenas essas linhas são transpostas
    """
    if not text:
        return ""

    delta = _semitone_diff(from_tone, to_tone)
    if delta == 0:
        return text

    use_flats = _prefer_flats(to_tone)

    lines = text.splitlines()
    out_lines = []

    for line in lines:
        if line.startswith("|"):
            raw = line[1:]
            parts = re.split(r"(\s+)", raw)  # preserva espaçamento
            new_parts = []

            for p in parts:
                if not p or p.isspace():
                    new_parts.append(p)
                elif re.match(r"^[A-G][#b]?", p):
                    new_parts.append(transpose_chord_symbol(p, delta, use_flats))
                else:
                    new_parts.append(p)

            out_lines.append("|" + "".join(new_parts))
        else:
            out_lines.append(line)

    return "\n".join(out_lines)


# ==============================================================
# 3) GEMINI – TRANSCRIÇÃO DE IMAGEM
# ==============================================================

def transcribe_image_with_gemini(uploaded_file, model_name="models/gemini-2.5-flash"):
    if genai is None:
        st.error("Pacote google-generativeai não está disponível no ambiente.")
        return ""
    api_key = get_gemini_api_key()
    if not api_key:
        st.error("Gemini API key não configurada em st.secrets.")
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

        response = model.generate_content([prompt, {"mime_type": mime, "data": img_data}])
        text = (getattr(response, "text", "") or "").strip()

        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = "\n".join(text.split("\n")[1:]).strip()

        return text

    except Exception as e:
        st.error(f"Erro ao chamar Gemini: {e}")
        return ""


# ==============================================================
# 4) GOOGLE DRIVE – ARQUIVOS .TXT (CIFRAS)
# ==============================================================

def get_drive_service():
    secrets = st.secrets["gcp_service_account"]
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(secrets, scopes=scopes)
    return build("drive", "v3", credentials=creds)


def create_chord_in_drive(filename, content):
    """Cria um novo .txt no Drive e retorna o FileID."""
    if not (content or "").strip():
        return ""

    try:
        service = get_drive_service()
        folder_id = st.secrets.get("drive", {}).get("folder_id", None)

        file_metadata = {"name": f"{filename}.txt", "mimeType": "text/plain"}
        if folder_id:
            file_metadata["parents"] = [folder_id]

        fh = io.BytesIO(content.encode("utf-8"))
        media = MediaIoBaseUpload(fh, mimetype="text/plain")

        file = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id", supportsAllDrives=True)
            .execute()
        )
        return file.get("id", "")

    except Exception as e:
        st.error(f"Erro ao criar arquivo no Drive: {e}")
        return ""


@st.cache_data(ttl=120)
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

    except Exception as e:
        return f"Erro ao carregar cifra do Drive (ID: {file_id}):\n{e}"


def save_chord_to_drive(file_id: str, content: str):
    if not file_id:
        return
    file_id = str(file_id).strip()

    try:
        service = get_drive_service()
        fh = io.BytesIO((content or "").encode("utf-8"))
        media = MediaIoBaseUpload(fh, mimetype="text/plain")
        service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
        load_chord_from_drive.clear()

    except Exception as e:
        st.error(f"Erro ao salvar cifra no Drive (ID: {file_id}): {e}")


# ==============================================================
# 5) GITHUB – CSV BANCO + CSV SETLISTS
# ==============================================================

def _gh_secrets():
    gh = st.secrets.get("github", {})
    token = gh.get("token", "")
    owner = gh.get("owner", "FelipeNovais89")
    repo = gh.get("repo", "PDLSetlist")
    branch = gh.get("branch", "main")
    setlists_dir = gh.get("setlists_dir", "Data/Setlists")
    songs_csv_url = gh.get(
        "songs_csv_url",
        "https://raw.githubusercontent.com/FelipeNovais89/PDLSetlist/refs/heads/main/Data/PDL_musicas.csv",
    )
    return token, owner, repo, branch, setlists_dir, songs_csv_url


def _gh_headers(token: str):
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _safe_filename(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"[^\w\- ]+", "", name, flags=re.UNICODE)
    name = name.replace(" ", "_")
    return name or "Setlist_sem_nome"


@st.cache_data(ttl=300)
def load_songs_df_from_github_csv() -> pd.DataFrame:
    token, owner, repo, branch, setlists_dir, songs_csv_url = _gh_secrets()

    try:
        r = requests.get(songs_csv_url, timeout=20)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
    except Exception as e:
        st.error(f"Erro carregando CSV do GitHub: {e}")
        df = pd.DataFrame()

    # normalize nomes de colunas (muito comum vir sem acento)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.rename(columns={
        "Titulo": "Título",
        "titulo": "Título",
        "Title": "Título",
        "title": "Título",
        "Artista": "Artista",
        "artist": "Artista",
        "Artist": "Artista",
        "TomOriginal": "Tom_Original",
        "Tom Original": "Tom_Original",
        "Tom_Original": "Tom_Original",
        "Bpm": "BPM",
        "bpm": "BPM",
        "CifraDriveId": "CifraDriveID",
        "CifraSimplificadaId": "CifraSimplificadaID",
    })

    # garante colunas esperadas
    expected = ["Título", "Artista", "Tom_Original", "BPM", "CifraDriveID", "CifraSimplificadaID"]
    for col in expected:
        if col not in df.columns:
            df[col] = ""

    df = df.fillna("")
    return df


def list_setlist_files() -> list:
    token, owner, repo, branch, setlists_dir, songs_csv_url = _gh_secrets()
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{setlists_dir}?ref={branch}"

    r = requests.get(url, headers=_gh_headers(token), timeout=20)
    if r.status_code == 404:
        return []
    r.raise_for_status()

    items = r.json()
    names = []
    for it in items:
        if it.get("type") == "file" and it.get("name", "").lower().endswith(".csv"):
            names.append(it["name"])
    names.sort()
    return names


def load_setlist_df_from_github(setlist_name: str) -> pd.DataFrame:
    token, owner, repo, branch, setlists_dir, songs_csv_url = _gh_secrets()
    fn = _safe_filename(setlist_name) + ".csv"
    path = f"{setlists_dir}/{fn}"
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"

    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 404:
            return pd.DataFrame(columns=SETLIST_COLS)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
    except Exception as e:
        st.error(f"Erro ao carregar setlist CSV do GitHub: {e}")
        df = pd.DataFrame(columns=SETLIST_COLS)

    for col in SETLIST_COLS:
        if col not in df.columns:
            df[col] = ""
    df = df.fillna("")
    return df


def save_setlist_df_to_github(setlist_name: str, df: pd.DataFrame):
    token, owner, repo, branch, setlists_dir, songs_csv_url = _gh_secrets()
    if not token:
        st.error("Faltou configurar github.token em st.secrets.")
        return

    fn = _safe_filename(setlist_name) + ".csv"
    path = f"{setlists_dir}/{fn}"
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

    csv_text = df.to_csv(index=False)
    content_b64 = base64.b64encode(csv_text.encode("utf-8")).decode("utf-8")

    # sha se existir
    sha = None
    r0 = requests.get(api_url + f"?ref={branch}", headers=_gh_headers(token), timeout=20)
    if r0.status_code == 200:
        sha = r0.json().get("sha")

    msg = f"Update setlist {fn} ({datetime.utcnow().isoformat()}Z)"
    payload = {"message": msg, "content": content_b64, "branch": branch}
    if sha:
        payload["sha"] = sha

    r = requests.put(api_url, headers=_gh_headers(token), data=json.dumps(payload), timeout=20)
    if r.status_code not in (200, 201):
        st.error(f"Erro ao salvar no GitHub: {r.status_code} - {r.text}")
    else:
        st.success(f"Setlist salva no GitHub: {fn}")


# ==============================================================
# 6) ESTRUTURA SETLIST (colunas do CSV)
# ==============================================================

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


# ==============================================================
# 7) ESTADO INICIAL
# ==============================================================

def init_state():
    if "songs_df" not in st.session_state:
        st.session_state.songs_df = load_songs_df_from_github_csv()

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

    if "selected_block_idx" not in st.session_state:
        st.session_state.selected_block_idx = None
    if "selected_item_idx" not in st.session_state:
        st.session_state.selected_item_idx = None

    if "new_song_cifra_original" not in st.session_state:
        st.session_state.new_song_cifra_original = ""
    if "new_song_cifra_simplificada" not in st.session_state:
        st.session_state.new_song_cifra_simplificada = ""


# ==============================================================
# 8) AUX – ORDEM / REMOÇÃO
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
# 9) PERSISTÊNCIA: salvar/carregar setlist (GitHub CSV)
# ==============================================================

def save_current_setlist_to_github():
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
                "ItemType": item.get("type", ""),
                "SongTitle": "",
                "Artist": "",
                "Tom": "",
                "BPM": "",
                "CifraDriveID": "",
                "CifraSimplificadaID": "",
                "UseSimplificada": "",
                "PauseLabel": "",
            }

            if item.get("type") == "music":
                base["SongTitle"] = item.get("title", "")
                base["Artist"] = item.get("artist", "")
                base["Tom"] = item.get("tom", "")
                base["BPM"] = item.get("bpm", "")
                base["CifraDriveID"] = item.get("cifra_id", "")
                base["CifraSimplificadaID"] = item.get("cifra_simplificada_id", "")
                base["UseSimplificada"] = "1" if item.get("use_simplificada", False) else "0"
            else:
                base["PauseLabel"] = item.get("label", "Pausa")

            rows.append(base)

    df_new = pd.DataFrame(rows, columns=SETLIST_COLS)
    save_setlist_df_to_github(name, df_new)


def load_setlist_into_state_from_github(setlist_name: str, songs_df: pd.DataFrame):
    df_sel = load_setlist_df_from_github(setlist_name)
    if df_sel.empty:
        return

    df_sel["BlockIndex"] = pd.to_numeric(df_sel["BlockIndex"], errors="coerce").fillna(0).astype(int)
    df_sel["ItemIndex"] = pd.to_numeric(df_sel["ItemIndex"], errors="coerce").fillna(0).astype(int)
    df_sel = df_sel.sort_values(["BlockIndex", "ItemIndex"])

    blocks = []
    for (block_idx, block_name), group in df_sel.groupby(["BlockIndex", "BlockName"], sort=True):
        items = []
        for _, row in group.iterrows():
            if str(row.get("ItemType", "")).strip() == "pause":
                items.append({"type": "pause", "label": row.get("PauseLabel", "Pausa")})
            else:
                title = row.get("SongTitle", "")
                artist = row.get("Artist", "")
                tom_saved = row.get("Tom", "")
                bpm_saved = row.get("BPM", "")

                cifra_id_saved = str(row.get("CifraDriveID", "")).strip()
                cifra_simplificada_saved = str(row.get("CifraSimplificadaID", "")).strip()

                use_simplificada_saved = str(row.get("UseSimplificada", "0")).strip()
                use_simplificada = use_simplificada_saved in ("1", "true", "True", "Y", "y")

                # tenta casar com banco
                song_row = songs_df[songs_df["Título"].astype(str) == str(title)]
                if not song_row.empty:
                    sr = song_row.iloc[0]
                    tom_original = (sr.get("Tom_Original", "") or tom_saved).strip()
                    cifra_id_bank = str(sr.get("CifraDriveID", "")).strip()
                    cifra_simplificada_bank = str(sr.get("CifraSimplificadaID", "")).strip()

                    cifra_id = cifra_id_saved or cifra_id_bank
                    cifra_simplificada_id = cifra_simplificada_saved or cifra_simplificada_bank
                else:
                    tom_original = tom_saved
                    cifra_id = cifra_id_saved
                    cifra_simplificada_id = cifra_simplificada_saved

                items.append({
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
                })

        blocks.append({"name": block_name or f"Bloco {len(blocks) + 1}", "items": items})

    st.session_state.blocks = blocks
    st.session_state.setlist_name = setlist_name
    st.session_state.current_item = None
    st.session_state.selected_block_idx = None
    st.session_state.selected_item_idx = None
    st.session_state.screen = "editor"


# ==============================================================
# 10) EDITOR DO ITEM SELECIONADO
# ==============================================================

def render_selected_item_editor():
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

    if item.get("type") == "music":
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

        cifra_id = (item.get("cifra_id", "") or "").strip()
        cifra_simplificada_id = (item.get("cifra_simplificada_id", "") or "").strip()

        with st.expander("Ver / editar cifra (texto)", expanded=True):
            if item.get("use_simplificada") and cifra_simplificada_id:
                current_id = cifra_simplificada_id
            elif cifra_id:
                current_id = cifra_id
            else:
                current_id = None

            cifra_text = load_chord_from_drive(current_id) if current_id else item.get("text", "")

            font_size = st.session_state.cifra_font_size
            c1, c2 = st.columns(2)
            if c1.button("A﹣", key=f"font_minus_sel_{b_idx}_{i_idx}"):
                st.session_state.cifra_font_size = max(8, font_size - 1)
                st.rerun()
            if c2.button("A﹢", key=f"font_plus_sel_{b_idx}_{i_idx}"):
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

        col_bpm, col_tom = st.columns(2)

        item["bpm"] = col_bpm.text_input(
            "BPM",
            value=str(bpm_val) if bpm_val not in ("", None, 0) else "",
            key=f"bpm_sel_{b_idx}_{i_idx}",
        )

        if (tom_original or "").endswith("m"):
            tone_list = [t for t in TONE_OPTIONS if t.endswith("m")]
        else:
            tone_list = [t for t in TONE_OPTIONS if not t.endswith("m")]

        if tom_val and tom_val not in tone_list:
            tone_list = [tom_val] + tone_list
        idx_tone = tone_list.index(tom_val) if tom_val in tone_list else 0

        selected_tone = col_tom.selectbox(
            "Tom",
            options=tone_list,
            index=idx_tone,
            key=f"tom_sel_{b_idx}_{i_idx}",
        )
        if selected_tone != tom_val:
            item["tom"] = selected_tone
            st.session_state.current_item = (b_idx, i_idx)
            st.rerun()

    else:
        st.markdown("**⏸ Pausa**")
        item["label"] = st.text_input(
            "Descrição da pausa",
            value=item.get("label", "Pausa"),
            key=f"pause_label_{b_idx}_{i_idx}",
        )


# ==============================================================
# 11) EDITOR EM ÁRVORE (SETLIST) — ✅ versão única + BPM/Tom visíveis
# ==============================================================

def render_setlist_editor_tree():
    blocks = st.session_state.blocks
    songs_df = st.session_state.songs_df

    st.markdown("### Estrutura da Setlist (modo árvore)")

    if st.button("+ Adicionar bloco", use_container_width=True, key="btn_add_block_global"):
        st.session_state.blocks.append({"name": f"Bloco {len(blocks) + 1}", "items": []})
        st.rerun()

    for b_idx, block in enumerate(blocks):
        with st.expander(
            f"Bloco {b_idx + 1}: {block.get('name', f'Bloco {b_idx+1}')}",
            expanded=False
        ):
            name_col, up_col, down_col, del_col = st.columns([6, 1, 1, 1])

            block["name"] = name_col.text_input(
                "Nome do bloco",
                value=block.get("name", f"Bloco {b_idx+1}"),
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

            # ==========================================================
            # ITENS DO BLOCO (✅ BPM/Tom sempre visíveis na linha)
            # ==========================================================
            for i, item in enumerate(block.get("items", [])):
                col_label, col_btns = st.columns([8, 2])

                if item.get("type") == "music":
                    title = (item.get("title") or "Nova música").strip()
                    artist = (item.get("artist") or "").strip()
                    tom = (item.get("tom") or item.get("tom_original") or "").strip()
                    bpm = (item.get("bpm") or "").strip()

                    # ✅ texto do label com BPM/Tom visíveis
                    label_main = f"🎵 {title}"
                    if artist:
                        label_main += f" – {artist}"

                    meta = []
                    if tom:
                        meta.append(f"Tom: {tom}")
                    if bpm:
                        meta.append(f"BPM: {bpm}")

                    if meta:
                        label = label_main + "  ·  " + " | ".join(meta)
                    else:
                        label = label_main

                else:
                    label = f"⏸ {item.get('label', 'Pausa')}"

                if col_label.button(label, key=f"sel_item_{b_idx}_{i}"):
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

            # ==========================================================
            # ADD MÚSICA / PAUSA
            # ==========================================================
            col_add_mus, col_add_pause = st.columns(2)
            if col_add_mus.button("Música do banco", key=f"add_mus_blk_{b_idx}"):
                st.session_state[f"show_add_music_block_{b_idx}"] = True
            if col_add_pause.button("Pausa", key=f"add_pause_blk_{b_idx}"):
                block["items"].append({"type": "pause", "label": "Pausa"})
                st.rerun()

            # ==========================================================
            # ADD MÚSICA (mobile-safe selectbox)
            # ==========================================================
            if st.session_state.get(f"show_add_music_block_{b_idx}", False):
                st.markdown("##### Adicionar músicas deste bloco")

                options = []
                idx_map = {}

                df_local = songs_df.reset_index(drop=True).copy()
                for idx, row in df_local.iterrows():
                    titulo = str(row.get("Título", "")).strip()
                    artista = str(row.get("Artista", "")).strip()
                    tom = str(row.get("Tom_Original", "")).strip()

                    if not titulo:
                        continue

                    label_opt = f"{titulo} – {artista}" if artista else titulo
                    if tom:
                        label_opt += f" ({tom})"

                    options.append(label_opt)
                    idx_map[label_opt] = int(idx)

                if not options:
                    st.warning("Banco de músicas vazio (ou coluna 'Título' está vazia).")
                    st.caption("Dica: confira se o CSV tem a coluna Título/Titulo e se há linhas preenchidas.")
                else:
                    selected_label = st.selectbox(
                        "Escolha uma música",
                        options=options,
                        key=f"song_pick_{b_idx}",
                    )

                    ca, cb = st.columns(2)
                    if ca.button("Adicionar", key=f"confirm_add_one_{b_idx}"):
                        row = df_local.iloc[idx_map[selected_label]]

                        cifra_id = str(row.get("CifraDriveID", "")).strip()
                        cifra_simplificada_id = str(row.get("CifraSimplificadaID", "")).strip()

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
                        block["items"].append(new_item)
                        st.session_state[f"show_add_music_block_{b_idx}"] = False
                        st.rerun()

                    if cb.button("Fechar", key=f"close_add_music_{b_idx}"):
                        st.session_state[f"show_add_music_block_{b_idx}"] = False
                        st.rerun()

    # Editor do item selecionado (fora do loop)
    render_selected_item_editor()


# ==============================================================
# 12) BANCO DE MÚSICAS (GitHub CSV) + GERAR TXT NO DRIVE
# ==============================================================

def render_song_database():
    st.subheader("Banco de músicas (GitHub CSV)")
    df = st.session_state.songs_df

    st.dataframe(df, use_container_width=True, height=240)

    with st.expander("Gerar TXT no Drive (para depois colar os IDs no CSV)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            title = st.text_input("Título", key="new_title")
            artist = st.text_input("Artista", key="new_artist")
        with c2:
            tom_original = st.text_input("Tom original (ex.: Fm, C, Gm)", key="new_tom")
            bpm = st.text_input("BPM", key="new_bpm")

        st.markdown("---")

        st.markdown("#### 1) Cifra ORIGINAL")
        up_orig = st.file_uploader(
            "Envie imagem (.jpg/.png) ou .txt da cifra original",
            type=["jpg", "jpeg", "png", "txt"],
            key="upload_orig",
        )

        col_tr1, col_tr2 = st.columns([1, 3])
        with col_tr1:
            if st.button("Transcrever com Gemini (Original)", key="btn_tr_orig"):
                if up_orig is None:
                    st.warning("Envie uma imagem ou .txt primeiro.")
                else:
                    if up_orig.type == "text/plain":
                        text = up_orig.getvalue().decode("utf-8", errors="replace")
                    else:
                        text = transcribe_image_with_gemini(up_orig)
                    st.session_state.new_song_cifra_original = text
        with col_tr2:
            st.caption("Se você enviar um .txt, não precisa transcrever. Se enviar imagem, o Gemini tenta extrair.")

        st.session_state.new_song_cifra_original = st.text_area(
            "Texto da cifra ORIGINAL",
            value=st.session_state.new_song_cifra_original,
            height=220,
            key="txt_orig",
        )

        st.markdown("---")

        st.markdown("#### 2) Cifra SIMPLIFICADA (opcional)")
        up_simpl = st.file_uploader(
            "Envie imagem (.jpg/.png) ou .txt da cifra simplificada",
            type=["jpg", "jpeg", "png", "txt"],
            key="upload_simpl",
        )

        if st.button("Transcrever com Gemini (Simplificada)", key="btn_tr_simpl"):
            if up_simpl is None:
                st.warning("Envie uma imagem ou .txt primeiro.")
            else:
                if up_simpl.type == "text/plain":
                    text_s = up_simpl.getvalue().decode("utf-8", errors="replace")
                else:
                    text_s = transcribe_image_with_gemini(up_simpl)
                st.session_state.new_song_cifra_simplificada = text_s

        st.session_state.new_song_cifra_simplificada = st.text_area(
            "Texto da cifra SIMPLIFICADA",
            value=st.session_state.new_song_cifra_simplificada,
            height=220,
            key="txt_simpl",
        )

        st.markdown("---")
        st.markdown("#### 3) Criar arquivos no Drive (TXT)")
        if st.button("Criar TXT no Drive", key="btn_create_txt"):
            if not (title or "").strip():
                st.warning("Preencha pelo menos o título.")
            else:
                with st.spinner("Criando arquivos no Drive..."):
                    content_orig = st.session_state.new_song_cifra_original or ""
                    content_simpl = st.session_state.new_song_cifra_simplificada or ""

                    final_cifra_id = ""
                    final_simpl_id = ""

                    if content_orig.strip():
                        final_cifra_id = create_chord_in_drive(f"{title} - {artist} (Original)", content_orig)

                    if content_simpl.strip():
                        final_simpl_id = create_chord_in_drive(f"{title} - {artist} (Simplificada)", content_simpl)

                st.success("TXT criado no Drive.")
                st.info(
                    f"Agora edite o CSV do banco e cole esses IDs:\n\n"
                    f"- CifraDriveID: {final_cifra_id}\n"
                    f"- CifraSimplificadaID: {final_simpl_id}\n\n"
                    f"(Tom_Original: {tom_original} | BPM: {bpm})"
                )


# ==============================================================
# 13) PREVIEW (HTML responsivo + portrait/landscape + fontes clamp)
# ==============================================================

def get_footer_context(blocks, cur_block_idx, cur_item_idx):
    """Retorna (modo, next_item_dict) onde modo pode ser 'next' ou 'none'."""
    if cur_block_idx is None or cur_item_idx is None:
        return "none", None

    b = cur_block_idx
    i = cur_item_idx + 1

    while b < len(blocks):
        items = blocks[b].get("items", [])
        if i < len(items):
            return "next", items[i]
        b += 1
        i = 0

    return "none", None


def build_sheet_page_html(item, footer_mode, footer_next_item, block_name):
    # ------------------------
    # DADOS DO CABEÇALHO
    # ------------------------
    title = (item.get("title", "") if item.get("type") == "music" else item.get("label", "Pausa")) or ""
    artist = item.get("artist", "") if item.get("type") == "music" else ""
    bpm = (item.get("bpm", "") if item.get("type") == "music" else "") or ""
    tom = (item.get("tom", "") if item.get("type") == "music" else "") or ""

    # ------------------------
    # CIFRA (original/simplificada)
    # ------------------------
    cifra_txt = ""
    if item.get("type") == "music":
        use_s = item.get("use_simplificada", False)
        cid = (item.get("cifra_simplificada_id") if use_s else item.get("cifra_id")) or ""
        cid = str(cid).strip()
        if cid:
            cifra_txt = load_chord_from_drive(cid)
        else:
            cifra_txt = item.get("text", "")
    cifra_show = strip_chord_markers_for_display(cifra_txt)

    # ------------------------
    # PRÓXIMO
    # ------------------------
    next_title = ""
    if footer_mode == "next" and footer_next_item:
        if footer_next_item.get("type") == "music":
            next_title = footer_next_item.get("title", "")
        else:
            next_title = footer_next_item.get("label", "Pausa")

    # Escapa básico (evita quebrar HTML com caracteres especiais)
    def esc(s: str) -> str:
        s = str(s or "")
        return (
            s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
        )

    title_e = esc(title)
    artist_e = esc(artist)
    block_e = esc(block_name)
    bpm_e = esc(bpm) if bpm else "-"
    tom_e = esc(tom) if tom else "-"
    cifra_e = esc(cifra_show)
    next_e = esc(next_title)

    html = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
  :root {{
    /* ====== TIPOGRAFIA RESPONSIVA (min, ideal, max) ====== */
    --fs-title: clamp(16px, 2.4vw, 26px);
    --fs-sub:   clamp(11px, 1.3vw, 14px);
    --fs-meta:  clamp(11px, 1.2vw, 14px);
    --fs-chord: clamp(11px, 1.05vw, 15px);
    --fs-foot:  clamp(10px, 1.1vw, 13px);

    /* ====== ESPAÇAMENTOS ====== */
    --pad: clamp(10px, 1.8vw, 18px);
    --radius: 14px;
    --border: 1px solid #e9e9e9;
  }}

  html, body {{
    height: 100%;
  }}

  body {{
    font-family: Arial, sans-serif;
    margin: 0;
    padding: 0;
    background: #ffffff;
    color: #111;
  }}

  /* “Página” responsiva (fica tipo PDF, mas adaptável) */
  .sheet {{
    width: 96vw;             /* %/vw: ocupa quase toda a largura disponível */
    max-width: 980px;        /* limite p/ desktop */
    margin: 0 auto;
    padding: var(--pad);
    box-sizing: border-box;
  }}

  .top {{
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: start;
    gap: clamp(8px, 1.2vw, 14px);
    border-bottom: 1px solid #ddd;
    padding-bottom: clamp(8px, 1.1vw, 12px);
    margin-bottom: clamp(8px, 1.1vw, 12px);
  }}

  .title {{
    font-size: var(--fs-title);
    font-weight: 800;
    margin: 0;
    line-height: 1.1;
    word-break: break-word;
  }}

  .artist {{
    font-size: var(--fs-sub);
    margin-top: 4px;
    color: #444;
    line-height: 1.2;
  }}

  /* Meta em “colunas”: BPM e TOM lado a lado */
  .meta {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: clamp(8px, 1vw, 14px);
    font-size: var(--fs-meta);
    color: #222;
    text-align: right;
    min-width: 180px;
  }}

  .metaCard {{
    border: var(--border);
    border-radius: var(--radius);
    padding: clamp(8px, 1vw, 12px);
    background: #fff;
  }}

  .metaCard b {{
    display: block;
    font-size: var(--fs-meta);
    margin-bottom: 6px;
  }}

  /* CIFRA: ocupa altura baseada na tela (vh) */
  .cifra {{
    font-family: "Courier New", monospace;
    font-size: var(--fs-chord);
    line-height: 1.25;
    white-space: pre-wrap;
    border: var(--border);
    padding: clamp(10px, 1.2vw, 14px);
    border-radius: var(--radius);

    /* altura responsiva */
    min-height: 42vh;
    max-height: 72vh;

    overflow: auto;
    box-sizing: border-box;
  }}

  .footer {{
    margin-top: clamp(8px, 1.1vw, 12px);
    font-size: var(--fs-foot);
    color: #555;
    display: flex;
    justify-content: space-between;
    border-top: 1px solid #eee;
    padding-top: clamp(8px, 1.1vw, 12px);
    gap: 12px;
  }}

  /* ===========================
     PORTRAIT (celular em pé)
     =========================== */
  @media (orientation: portrait) {{
    .sheet {{
      width: 98vw;
      max-width: 980px;
      padding: clamp(10px, 3vw, 18px);
    }}

    /* Empilha meta abaixo do título (melhor no mobile) */
    .top {{
      grid-template-columns: 1fr;
    }}

    .meta {{
      text-align: left;
      min-width: 0;
      grid-template-columns: 1fr 1fr; /* mantém BPM/TOM lado a lado */
    }}

    .cifra {{
      min-height: 48vh;
      max-height: 68vh;
    }}
  }}

  /* ===========================
     LANDSCAPE (celular deitado / tela larga)
     =========================== */
  @media (orientation: landscape) {{
    .sheet {{
      width: 96vw;
      max-width: 1200px; /* mais largo em landscape */
    }}

    .top {{
      grid-template-columns: 1fr auto;
    }}

    .meta {{
      min-width: 240px;
    }}

    .cifra {{
      min-height: 55vh;
      max-height: 78vh;
    }}
  }}

  /* ===========================
     TELAS MUITO PEQUENAS
     (garante legibilidade)
     =========================== */
  @media (max-width: 420px) {{
    .meta {{
      grid-template-columns: 1fr; /* BPM em cima, Tom embaixo */
    }}
  }}
</style>
</head>
<body>
  <div class="sheet">
    <div class="top">
      <div>
        <div class="title">{title_e}</div>
        <div class="artist">{artist_e}</div>
        <div class="artist">Bloco: {block_e}</div>
      </div>

      <div class="meta">
        <div class="metaCard">
          <b>BPM</b>
          {bpm_e}
        </div>
        <div class="metaCard">
          <b>Tom</b>
          {tom_e}
        </div>
      </div>
    </div>

    <div class="cifra">{cifra_e}</div>

    <div class="footer">
      <div>Pagode do LEC</div>
      <div>{("Próxima: " + next_e) if next_title else ""}</div>
    </div>
  </div>
</body>
</html>
"""
    return html


# ==============================================================
# 14) HOME
# ==============================================================

def render_home():
    st.title("PDL Setlist")

    setlist_files = list_setlist_files()
    setlist_names = [f.replace(".csv", "").replace("_", " ") for f in setlist_files]

    col_new, col_load = st.columns(2)

    with col_new:
        st.subheader("Nova setlist")
        default_name = st.session_state.get("setlist_name", "Pagode do LEC")
        new_name = st.text_input("Nome da nova setlist", value=default_name, key="new_setlist_name")
        if st.button("Criar setlist", key="btn_create_setlist"):
            st.session_state.setlist_name = new_name.strip() or "Setlist sem nome"
            st.session_state.blocks = [{"name": "Bloco 1", "items": []}]
            st.session_state.current_item = None
            st.session_state.selected_block_idx = None
            st.session_state.selected_item_idx = None
            st.session_state.screen = "editor"
            st.rerun()

    with col_load:
        st.subheader("Carregar setlist existente (GitHub)")
        if setlist_names:
            selected = st.selectbox("Escolha", options=setlist_names, key="load_setlist_select")
            if st.button("Carregar", key="btn_load_setlist"):
                load_setlist_into_state_from_github(selected, st.session_state.songs_df)
                st.rerun()
        else:
            st.info("Nenhuma setlist encontrada ainda em Data/Setlists.")


# ==============================================================
# 15) MAIN
# ==============================================================

def main():
    st.set_page_config(page_title="PDL Setlist", layout="wide", page_icon="🎵")

    # ---------- ESTADO INICIAL ----------
    init_state()

    # ---------- TELA HOME ----------
    if st.session_state.screen == "home":
        render_home()
        return

    # ---------- CABEÇALHO ----------
    top_left, top_right = st.columns([3, 1])

    with top_left:
        st.markdown(f"### Setlist: {st.session_state.setlist_name}")
        st.session_state.setlist_name = st.text_input(
            "Nome do setlist",
            value=st.session_state.setlist_name,
            label_visibility="collapsed",
        )

    with top_right:
        if st.button("🏠 Voltar à tela inicial", use_container_width=True):
            st.session_state.screen = "home"
            st.rerun()

        if st.button("💾 Salvar setlist (GitHub CSV)", use_container_width=True):
            save_current_setlist_to_github()

    # ---------- LAYOUT PRINCIPAL ----------
    left_col, right_col = st.columns([1.1, 1])

    # ==========================================================
    # COLUNA ESQUERDA — EDITORES
    # ==========================================================
    with left_col:
        st.subheader("Editor de Setlist (modo árvore)")
        render_setlist_editor_tree()

        st.markdown("---")
        render_song_database()

    # ==========================================================
    # COLUNA DIREITA — PREVIEW (CORRIGIDO)
    # ==========================================================
    with right_col:
        st.subheader("Preview")

        blocks = st.session_state.blocks

        current_item = None
        current_block_name = ""
        cur_block_idx = None
        cur_item_idx = None

        # --------------------------------------------------
        # PRIORIDADE 1 — ITEM SELECIONADO NO EDITOR
        # --------------------------------------------------
        sel_b = st.session_state.selected_block_idx
        sel_i = st.session_state.selected_item_idx

        if sel_b is not None and sel_i is not None:
            if (
                0 <= sel_b < len(blocks)
                and 0 <= sel_i < len(blocks[sel_b]["items"])
            ):
                current_item = blocks[sel_b]["items"][sel_i]
                current_block_name = blocks[sel_b]["name"]
                cur_block_idx = sel_b
                cur_item_idx = sel_i

        # --------------------------------------------------
        # PRIORIDADE 2 — ITEM MARCADO COM 👁 (current_item)
        # --------------------------------------------------
        if current_item is None:
            cur = st.session_state.current_item
            if cur is not None:
                b_idx, i_idx = cur
                if (
                    0 <= b_idx < len(blocks)
                    and 0 <= i_idx < len(blocks[b_idx]["items"])
                ):
                    current_item = blocks[b_idx]["items"][i_idx]
                    current_block_name = blocks[b_idx]["name"]
                    cur_block_idx = b_idx
                    cur_item_idx = i_idx

        # --------------------------------------------------
        # PRIORIDADE 3 — PRIMEIRA MÚSICA DO SETLIST
        # --------------------------------------------------
        if current_item is None:
            for b_idx, block in enumerate(blocks):
                if block["items"]:
                    current_item = block["items"][0]
                    current_block_name = block["name"]
                    cur_block_idx = b_idx
                    cur_item_idx = 0
                    break

        # --------------------------------------------------
        # RENDERIZAÇÃO FINAL DO PREVIEW
        # --------------------------------------------------
        if current_item is None:
            st.info("Adicione músicas ao setlist para ver o preview.")
        else:
            footer_mode, footer_next_item = get_footer_context(
                blocks,
                cur_block_idx,
                cur_item_idx,
            )

            html = build_sheet_page_html(
                current_item,
                footer_mode,
                footer_next_item,
                current_block_name,
            )

            st.components.v1.html(
                html,
                height=1200,
                scrolling=True,
            )


# ==============================================================
# EXECUÇÃO
# ==============================================================

if __name__ == "__main__":
    main()
