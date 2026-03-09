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

    # normalize nomes de colunas
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

    expected = ["Título", "Artista", "Tom_Original", "BPM", "CifraDriveID", "CifraSimplificadaID"]
    for col in expected:
        if col not in df.columns:
            df[col] = ""

    df = df[expected].fillna("")

    if "Título" in df.columns:
        df = df.sort_values("Título", key=lambda s: s.astype(str).str.lower()).reset_index(drop=True)

    return df


def save_songs_df_to_github(df: pd.DataFrame):
    token, owner, repo, branch, setlists_dir, songs_csv_url = _gh_secrets()

    if not token:
        st.error("Faltou configurar github.token em st.secrets.")
        return

    path = "Data/PDL_musicas.csv"
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

    expected = ["Título", "Artista", "Tom_Original", "BPM", "CifraDriveID", "CifraSimplificadaID"]
    for col in expected:
        if col not in df.columns:
            df[col] = ""

    df = df[expected].fillna("")

    if "Título" in df.columns:
        df = df.sort_values("Título", key=lambda s: s.astype(str).str.lower()).reset_index(drop=True)

    csv_text = df.to_csv(index=False)
    content_b64 = base64.b64encode(csv_text.encode("utf-8")).decode("utf-8")

    sha = None
    r0 = requests.get(api_url + f"?ref={branch}", headers=_gh_headers(token), timeout=20)
    if r0.status_code == 200:
        sha = r0.json().get("sha")

    msg = f"Update songs database ({datetime.utcnow().isoformat()}Z)"
    payload = {"message": msg, "content": content_b64, "branch": branch}
    if sha:
        payload["sha"] = sha

    r = requests.put(api_url, headers=_gh_headers(token), data=json.dumps(payload), timeout=20)

    if r.status_code not in (200, 201):
        st.error(f"Erro ao salvar banco de músicas no GitHub: {r.status_code} - {r.text}")
    else:
        load_songs_df_from_github_csv.clear()
        st.session_state.songs_df = load_songs_df_from_github_csv()
        st.success("Banco de músicas salvo no GitHub com sucesso.")


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
    # ✅ NOVOS CAMPOS (por música)
    "Obs",
    "Preparacao",
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

    # ✅ nova navegação lateral
    if "app_section" not in st.session_state:
        st.session_state.app_section = "setlist"

    # ✅ estado do fullscreen
    if "pdl_fullscreen" not in st.session_state:
        st.session_state.pdl_fullscreen = False


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
                # ✅ novos
                "Obs": "",
                "Preparacao": "",
            }

            if item.get("type") == "music":
                base["SongTitle"] = item.get("title", "")
                base["Artist"] = item.get("artist", "")
                base["Tom"] = item.get("tom", "")
                base["BPM"] = item.get("bpm", "")
                base["CifraDriveID"] = item.get("cifra_id", "")
                base["CifraSimplificadaID"] = item.get("cifra_simplificada_id", "")
                base["UseSimplificada"] = "1" if item.get("use_simplificada", False) else "0"

                # ✅ salva OBS/PREPARAÇÃO por música
                base["Obs"] = item.get("obs", "")
                base["Preparacao"] = item.get("preparacao", "")

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

                # ✅ novos campos (por música)
                obs_saved = str(row.get("Obs", "")).strip()
                prep_saved = str(row.get("Preparacao", "")).strip()

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
                    # ✅ carrega OBS/PREPARAÇÃO do setlist
                    "obs": obs_saved,
                    "preparacao": prep_saved,
                })

        blocks.append({"name": block_name or f"Bloco {len(blocks) + 1}", "items": items})

    st.session_state.blocks = blocks
    st.session_state.setlist_name = setlist_name
    st.session_state.current_item = None
    st.session_state.selected_block_idx = None
    st.session_state.selected_item_idx = None
    st.session_state.screen = "editor"


# ==============================================================
# 10) EDITOR DO ITEM SELECIONADO  — versão responsiva + fonte preview
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

    # ==========================================================
    # 🎵 MÚSICA
    # ==========================================================
    if item.get("type") == "music":

        title = item.get("title", "Nova música")
        artist = item.get("artist", "")

        st.markdown(f"**🎵 {title}**")
        if artist:
            st.caption(artist)

        # ------------------------------------------------------
        # Alternar original / simplificada
        # ------------------------------------------------------
        use_simplificada = item.get("use_simplificada", False)
        btn_label = "Usar cifra ORIGINAL" if use_simplificada else "Usar cifra SIMPLIFICADA"

        if st.button(btn_label, key=f"simpl_toggle_{b_idx}_{i_idx}"):
            item["use_simplificada"] = not use_simplificada
            st.session_state.current_item = (b_idx, i_idx)
            st.rerun()

        cifra_id = (item.get("cifra_id", "") or "").strip()
        cifra_simplificada_id = (item.get("cifra_simplificada_id", "") or "").strip()

        # ======================================================
        # ✨ EDITOR DE CIFRA (NOVO VISUAL)
        # ======================================================
        with st.expander("Ver / editar cifra (texto)", expanded=True):

            if item.get("use_simplificada") and cifra_simplificada_id:
                current_id = cifra_simplificada_id
            elif cifra_id:
                current_id = cifra_id
            else:
                current_id = None

            cifra_text = load_chord_from_drive(current_id) if current_id else item.get("text", "")

            # --------------------------------------------------
            # CSS RESPONSIVO (igual preview)
            # --------------------------------------------------
            st.markdown(
                """
<style>
.pdl-cifra-editor textarea {

  /* mesma fonte do preview */
  font-family: "Courier New", monospace;
  /* nunca quebrar linha */
  white-space: pre !important;
  overflow-x: auto !important;
  overflow-y: auto !important;
  word-break: normal !important;
  overflow-wrap: normal !important;
  text-wrap: nowrap !important;

  /* tamanho automático */
  font-size: clamp(4px, 1.6vw, 14px) !important;
  line-height: 1.25 !important;

  /* altura proporcional à tela */
  height: 60vh !important;
  min-height: 220px !important;
}
</style>
""",
                unsafe_allow_html=True,
            )

            # wrapper para aplicar CSS só neste textarea
            st.markdown('<div class="pdl-cifra-editor">', unsafe_allow_html=True)

            edited = st.text_area(
                "Cifra",
                value=cifra_text,
                key=f"cifra_edit_sel_{b_idx}_{i_idx}",
                label_visibility="collapsed",
            )

            st.markdown("</div>", unsafe_allow_html=True)

            if st.button("Salvar cifra", key=f"save_cifra_sel_{b_idx}_{i_idx}"):
                if current_id:
                    save_chord_to_drive(current_id, edited)
                    st.success("Cifra atualizada no Drive.")
                else:
                    item["text"] = edited
                    st.success("Cifra salva apenas neste setlist.")
                st.rerun()

        # ======================================================
        # BPM + TOM
        # ======================================================
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

        # ======================================================
        # OBS / PREPARAÇÃO
        # ======================================================
        st.markdown("---")
        st.markdown("#### Observações / Preparação")

        item["obs"] = st.text_area(
            "OBS.:",
            value=item.get("obs", ""),
            height=100,
            key=f"obs_sel_{b_idx}_{i_idx}",
        )

        item["preparacao"] = st.text_area(
            "PREPARAÇÃO:",
            value=item.get("preparacao", ""),
            height=100,
            key=f"prep_sel_{b_idx}_{i_idx}",
        )

    # ==========================================================
    # ⏸ PAUSA
    # ==========================================================
    else:
        st.markdown("**⏸ Pausa**")

        item["label"] = st.text_input(
            "Descrição da pausa",
            value=item.get("label", "Pausa"),
            key=f"pause_label_{b_idx}_{i_idx}",
        )

# ==============================================================
# 11) MODAL — Song Picker
# ==============================================================

def _song_uid(row, fallback_i: int) -> str:
    """
    Gera um UID estável e único para cada música.
    Preferência: índice do DataFrame (row.name).
    """
    try:
        return str(row.name)
    except Exception:
        return str(fallback_i)


def open_song_picker_dialog(block_idx: int, songs_df: pd.DataFrame):
    """
    Abre um modal (st.dialog) com lista de músicas (checkbox),
    busca e botões de ação. A lista tem scroll próprio.
    """

    if "song_picker_nonce" not in st.session_state:
        st.session_state.song_picker_nonce = 0

    if st.session_state.get("song_picker_open", False) and st.session_state.song_picker_nonce == 0:
        st.session_state.song_picker_nonce = 1

    nonce = st.session_state.song_picker_nonce

    sel_key = f"song_picker_selected_{block_idx}"
    if sel_key not in st.session_state:
        st.session_state[sel_key] = set()

    @st.dialog("Adicionar músicas ao bloco", width="large")
    def _dialog():
        st.markdown("Marque as músicas que deseja adicionar neste bloco.")

        q = st.text_input(
            "Buscar",
            placeholder="Digite parte do título ou artista…",
            key=f"sp_search_{nonce}_{block_idx}",
        ).strip().lower()

        df_local = songs_df.copy()

        if not df_local.empty:
            df_local["__t"] = df_local["Título"].astype(str)
            df_local["__a"] = df_local["Artista"].astype(str)

            if q:
                mask = (
                    df_local["__t"].str.lower().str.contains(q, na=False)
                    | df_local["__a"].str.lower().str.contains(q, na=False)
                )
                df_local = df_local[mask]

        # ordena alfabeticamente
        if not df_local.empty and "Título" in df_local.columns:
            df_local = df_local.sort_values(
                "Título",
                key=lambda s: s.astype(str).str.lower()
            )

        list_box = st.container(height=360, border=True)

        selected_set = set(st.session_state[sel_key])

        with list_box:
            if df_local.empty:
                st.info("Nenhuma música encontrada.")
            else:
                for j, (_, row) in enumerate(df_local.iterrows()):
                    titulo = str(row.get("Título", "")).strip()
                    artista = str(row.get("Artista", "")).strip()
                    tom = str(row.get("Tom_Original", "")).strip()

                    if not titulo:
                        continue

                    label = f"{titulo} – {artista}" if artista else titulo
                    if tom:
                        label += f" ({tom})"

                    uid = _song_uid(row, j)
                    cb_key = f"sp_cb_{nonce}_{block_idx}_{uid}"

                    checked = uid in selected_set
                    val = st.checkbox(label, value=checked, key=cb_key)

                    if val:
                        selected_set.add(uid)
                    else:
                        selected_set.discard(uid)

        st.session_state[sel_key] = selected_set

        st.markdown("---")
        c1, c2, c3 = st.columns([1, 1, 2])

        if c1.button("Limpar seleção", use_container_width=True, key=f"sp_clear_{nonce}_{block_idx}"):
            st.session_state[sel_key] = set()
            st.rerun()

        if c2.button("Cancelar", use_container_width=True, key=f"sp_cancel_{nonce}_{block_idx}"):
            st.session_state.song_picker_open = False
            st.session_state.song_picker_block_idx = None
            st.rerun()

        if c3.button("Adicionar selecionadas", use_container_width=True, key=f"sp_add_{nonce}_{block_idx}"):
            chosen = st.session_state[sel_key]

            if not chosen:
                st.warning("Nenhuma música selecionada.")
                return

            df_all = songs_df.copy()

            for uid in chosen:
                try:
                    row = df_all.loc[int(uid)] if uid.isdigit() else df_all.loc[uid]
                except Exception:
                    try:
                        row = df_all.loc[uid]
                    except Exception:
                        continue

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
                    "obs": "",
                    "preparacao": "",
                }

                st.session_state.blocks[block_idx]["items"].append(new_item)

            st.session_state[sel_key] = set()
            st.session_state.song_picker_open = False
            st.session_state.song_picker_block_idx = None
            st.rerun()

    _dialog()
        

# ==============================================================
# 11.5) EDITOR EM ÁRVORE (SETLIST) — versão estável + layout melhorado
# ==============================================================

def render_setlist_editor_tree():
    blocks = st.session_state.blocks
    songs_df = st.session_state.songs_df

    st.markdown("### Estrutura da Setlist (modo árvore)")

    # ----------------------------------------------------------
    # CSS dos botões / cards
    # ----------------------------------------------------------
    st.markdown("""
    <style>
    div[data-testid="stButton"] > button {
        border-radius: 16px !important;
    }

    .music-btn div[data-testid="stButton"] > button {
        min-height: 125px !important;
        height: 125px !important;
        white-space: normal !important;
        text-align: left !important;
        justify-content: flex-start !important;
        align-items: center !important;
        line-height: 1.35 !important;
        font-size: 15px !important;
        padding: 14px 16px !important;
        overflow: hidden !important;
    }

    .music-btn div[data-testid="stButton"] > button p,
    .music-btn div[data-testid="stButton"] > button span,
    .music-btn div[data-testid="stButton"] > button div {
        white-space: pre-line !important;
        line-height: 1.35 !important;
        text-align: left !important;
        width: 100% !important;
    }

    .block-add div[data-testid="stButton"] > button {
        min-height: 52px !important;
        font-size: 18px !important;
    }

    .mini-btn div[data-testid="stButton"] > button {
        min-height: 52px !important;
        padding: 0 !important;
        font-size: 18px !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # ==========================================================
    # LOOP DOS BLOCOS
    # ==========================================================
    for b_idx, block in enumerate(blocks):

        block_title = block.get("name", f"Bloco {b_idx+1}")

        with st.expander(
            f"Bloco {b_idx + 1}: {block_title}",
            expanded=False
        ):

            # --------------------------------------------------
            # Cabeçalho do bloco
            # --------------------------------------------------
            head1, head2, head3, head4 = st.columns([7, 1, 1, 1])

            block["name"] = head1.text_input(
                "Nome do bloco",
                value=block.get("name", f"Bloco {b_idx+1}"),
                key=f"blk_name_{b_idx}",
                label_visibility="collapsed",
            )

            with head2:
                st.markdown('<div class="mini-btn">', unsafe_allow_html=True)
                if st.button("⬆️", key=f"blk_up_{b_idx}", use_container_width=True):
                    move_block(b_idx, -1)
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

            with head3:
                st.markdown('<div class="mini-btn">', unsafe_allow_html=True)
                if st.button("⬇️", key=f"blk_down_{b_idx}", use_container_width=True):
                    move_block(b_idx, 1)
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

            with head4:
                st.markdown('<div class="mini-btn">', unsafe_allow_html=True)
                if st.button("❎", key=f"blk_del_{b_idx}", use_container_width=True):
                    delete_block(b_idx)
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

            st.markdown("---")

            # ==================================================
            # ITENS DO BLOCO
            # ==================================================
            for i, item in enumerate(block.get("items", [])):

                col_label, col_btns = st.columns([8.6, 2.4])

                # ---------- label ----------
                if item.get("type") == "music":
                    title = (item.get("title") or "Nova música").strip()
                    artist = (item.get("artist") or "").strip()
                    tom = (item.get("tom") or item.get("tom_original") or "").strip()
                    bpm = str(item.get("bpm") or "").strip()

                    label = (
                        f"🎵 {title}  \n"
                        f"{artist if artist else '-'}  \n"
                        f"Tom: {tom if tom else '-'} | BPM: {bpm if bpm else '-'}"
                    )

                    with col_label:
                        st.markdown('<div class="music-btn">', unsafe_allow_html=True)
                        if st.button(
                            label,
                            key=f"sel_item_{b_idx}_{i}",
                            use_container_width=True
                        ):
                            st.session_state.selected_block_idx = b_idx
                            st.session_state.selected_item_idx = i
                            st.session_state.current_item = (b_idx, i)
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)

                else:
                    pause_label = f"⏸ {item.get('label', 'Pausa')}"

                    with col_label:
                        st.markdown('<div class="music-btn">', unsafe_allow_html=True)
                        if st.button(
                            pause_label,
                            key=f"sel_item_{b_idx}_{i}",
                            use_container_width=True
                        ):
                            st.session_state.selected_block_idx = b_idx
                            st.session_state.selected_item_idx = i
                            st.session_state.current_item = (b_idx, i)
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)

                # ---------- botões ----------
                with col_btns:
                    c_up, c_down, c_del, c_prev = st.columns(4)

                    with c_up:
                        st.markdown('<div class="mini-btn">', unsafe_allow_html=True)
                        if st.button("⬆️", key=f"it_up_{b_idx}_{i}", use_container_width=True):
                            move_item(b_idx, i, -1)
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)

                    with c_down:
                        st.markdown('<div class="mini-btn">', unsafe_allow_html=True)
                        if st.button("⬇️", key=f"it_down_{b_idx}_{i}", use_container_width=True):
                            move_item(b_idx, i, 1)
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)

                    with c_del:
                        st.markdown('<div class="mini-btn">', unsafe_allow_html=True)
                        if st.button("❎", key=f"it_del_{b_idx}_{i}", use_container_width=True):
                            delete_item(b_idx, i)
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)

                    with c_prev:
                        st.markdown('<div class="mini-btn">', unsafe_allow_html=True)
                        if st.button("👁", key=f"it_prev_{b_idx}_{i}", use_container_width=True):
                            st.session_state.current_item = (b_idx, i)
                            st.session_state.selected_block_idx = b_idx
                            st.session_state.selected_item_idx = i
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)

            st.markdown("---")

            # ==================================================
            # BOTÕES ADICIONAR DENTRO DO BLOCO
            # ==================================================
            col_add_mus, col_add_pause = st.columns(2)

            if col_add_mus.button("Música do banco", key=f"add_mus_blk_{b_idx}", use_container_width=True):
                st.session_state.song_picker_open = True
                st.session_state.song_picker_block_idx = b_idx
                st.rerun()

            if col_add_pause.button("Pausa", key=f"add_pause_blk_{b_idx}", use_container_width=True):
                block["items"].append({"type": "pause", "label": "Pausa"})
                st.rerun()

    # ==========================================================
    # BOTÃO GLOBAL ADICIONAR BLOCO (EMBAIXO DOS BLOCOS)
    # ==========================================================
    st.markdown('<div class="block-add">', unsafe_allow_html=True)
    if st.button("+ Adicionar bloco", use_container_width=True, key="btn_add_block_global_bottom"):
        st.session_state.blocks.append({"name": f"Bloco {len(blocks) + 1}", "items": []})
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    # ==========================================================
    # MODAL FORA DO LOOP
    # ==========================================================
    if st.session_state.get("song_picker_open", False):
        target_b = st.session_state.get("song_picker_block_idx", None)
        if target_b is not None and 0 <= target_b < len(st.session_state.blocks):
            open_song_picker_dialog(target_b, songs_df)

    # ==========================================================
    # Editor lateral do item
    # ==========================================================
    render_selected_item_editor()


# ==============================================================
# 12) BANCO DE MÚSICAS (GitHub CSV) + GERAR TXT NO DRIVE
# ==============================================================

def render_song_database():
    st.subheader("Banco de músicas")

    if "songs_editor_df" not in st.session_state:
        df_init = st.session_state.songs_df.copy().fillna("")
        if "Título" in df_init.columns:
            df_init = df_init.sort_values("Título", key=lambda s: s.astype(str).str.lower()).reset_index(drop=True)
        st.session_state.songs_editor_df = df_init

    top_a, top_b = st.columns(2)

    with top_a:
        if st.button("➕ Adicionar nova música", use_container_width=True, key="btn_add_song_row"):
            df_edit = st.session_state.songs_editor_df.copy()
            new_row = {
                "Título": "",
                "Artista": "",
                "Tom_Original": "",
                "BPM": "",
                "CifraDriveID": "",
                "CifraSimplificadaID": "",
            }
            df_edit = pd.concat([df_edit, pd.DataFrame([new_row])], ignore_index=True)
            st.session_state.songs_editor_df = df_edit
            st.rerun()

    with top_b:
        if st.button("🔄 Recarregar do GitHub", use_container_width=True, key="btn_reload_songs"):
            load_songs_df_from_github_csv.clear()
            df_reload = load_songs_df_from_github_csv().fillna("")
            if "Título" in df_reload.columns:
                df_reload = df_reload.sort_values("Título", key=lambda s: s.astype(str).str.lower()).reset_index(drop=True)
            st.session_state.songs_df = df_reload
            st.session_state.songs_editor_df = df_reload.copy()
            st.rerun()

    st.caption("Você pode editar diretamente a tabela abaixo e depois salvar no GitHub.")

    df_editable = st.session_state.songs_editor_df.copy().fillna("")

    edited_df = st.data_editor(
        df_editable,
        use_container_width=True,
        height=380,
        num_rows="dynamic",
        key="songs_data_editor",
        column_config={
            "Título": st.column_config.TextColumn("Título", required=True),
            "Artista": st.column_config.TextColumn("Artista"),
            "Tom_Original": st.column_config.TextColumn("Tom_Original"),
            "BPM": st.column_config.TextColumn("BPM"),
            "CifraDriveID": st.column_config.TextColumn("CifraDriveID"),
            "CifraSimplificadaID": st.column_config.TextColumn("CifraSimplificadaID"),
        },
    )

    edited_df = edited_df.fillna("")

    if "Título" in edited_df.columns:
        edited_df = edited_df.sort_values("Título", key=lambda s: s.astype(str).str.lower()).reset_index(drop=True)

    st.session_state.songs_editor_df = edited_df

    save_col1, save_col2 = st.columns([1, 2])

    with save_col1:
        if st.button("💾 Salvar banco de músicas", use_container_width=True, key="btn_save_songs_db"):
            df_to_save = st.session_state.songs_editor_df.copy().fillna("")

            # remove linhas totalmente vazias
            df_to_save = df_to_save[
                ~(
                    df_to_save["Título"].astype(str).str.strip().eq("") &
                    df_to_save["Artista"].astype(str).str.strip().eq("") &
                    df_to_save["Tom_Original"].astype(str).str.strip().eq("") &
                    df_to_save["BPM"].astype(str).str.strip().eq("") &
                    df_to_save["CifraDriveID"].astype(str).str.strip().eq("") &
                    df_to_save["CifraSimplificadaID"].astype(str).str.strip().eq("")
                )
            ].copy()

            if "Título" in df_to_save.columns:
                df_to_save = df_to_save.sort_values("Título", key=lambda s: s.astype(str).str.lower()).reset_index(drop=True)

            save_songs_df_to_github(df_to_save)
            st.session_state.songs_df = df_to_save.copy()
            st.session_state.songs_editor_df = df_to_save.copy()
            st.rerun()

    with save_col2:
        st.caption("As músicas são organizadas automaticamente em ordem alfabética pelo título.")

    st.markdown("---")

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
                    f"Agora você pode colar esses IDs diretamente na tabela acima:\n\n"
                    f"- CifraDriveID: {final_cifra_id}\n"
                    f"- CifraSimplificadaID: {final_simpl_id}\n\n"
                    f"(Tom_Original: {tom_original} | BPM: {bpm})"
                )
                
# ==============================================================
# 12.5) GEMINI AI — OCR CIFRA (imagem → texto)
# ==============================================================

import google.generativeai as genai
import re
import base64


# --------------------------------------------------------------
# 🔧 Corrige linhas de acordes automaticamente (prefixa "| ")
# --------------------------------------------------------------
def _fix_chord_lines(text: str) -> str:
    lines = text.splitlines()
    out = []

    chord_pattern = re.compile(
        r'^([A-G][#b]?(m|maj|min|sus|dim|aug|add|\d+)?(\([^\)]*\))?\s+)+$'
    )

    for line in lines:
        stripped = line.strip()

        if chord_pattern.match(stripped):
            if not stripped.startswith("|"):
                line = "| " + line

        out.append(line)

    return "\n".join(out)


# --------------------------------------------------------------
# 🔮 Prompt otimizado para cifras
# --------------------------------------------------------------
GEMINI_PROMPT = """
Transcreva a cifra musical presente na imagem.

REGRAS OBRIGATÓRIAS:
- NÃO explique nada
- NÃO comente nada
- NÃO traduza
- NÃO adicione texto extra
- Retorne SOMENTE a cifra

FORMATAÇÃO:
- Preserve quebras de linha
- Preserve espaçamento
- Letras normais NÃO devem ter prefixo
- Linhas contendo acordes devem começar com "| "

EXEMPLO:
| C        G        Am
Hoje eu vou cantar
"""


# --------------------------------------------------------------
# 🔎 Lista modelos compatíveis automaticamente
# --------------------------------------------------------------
def _list_models():
    try:
        models = genai.list_models()
        usable = []

        for m in models:
            if "generateContent" in m.supported_generation_methods:
                usable.append(m.name)

        usable.sort()
        return usable

    except Exception as e:
        return [f"Erro: {e}"]


# --------------------------------------------------------------
# 🎯 Transcrição principal
# --------------------------------------------------------------
def _transcribe_image(image_bytes: bytes, model_name: str):

    model = genai.GenerativeModel(model_name)

    resp = model.generate_content(
        [
            GEMINI_PROMPT,
            {"mime_type": "image/jpeg", "data": image_bytes}
        ]
    )

    return _fix_chord_lines(resp.text)


# --------------------------------------------------------------
# 🖥️ UI Streamlit
# --------------------------------------------------------------
def render_gemini_ocr_section():

    st.markdown("## 🧠 Gemini AI — OCR de Cifras")

    # -------------------------
    # configuração API
    # -------------------------
    api_key = st.secrets.get("gemini_api_key")
    if not api_key:
        st.error("❌ gemini_api_key não encontrada no st.secrets")
        return

    genai.configure(api_key=api_key)

    # -------------------------
    # modelos
    # -------------------------
    if st.button("🔍 Listar modelos disponíveis", key="btn_list_models_ocr"):
        st.session_state._gemini_models = _list_models()

    models = st.session_state.get("_gemini_models", [])
    if models:
        st.success(f"Modelos encontrados: {len(models)}")

    default_model = "models/gemini-2.0-flash"
    model_name = st.selectbox(
        "Escolha o modelo",
        options=models if models else [default_model],
        index=0,
        key="sel_model_ocr"
    )

    st.info("💡 Recomendado: models/gemini-2.0-flash")

    # -------------------------
    # upload imagem
    # -------------------------
    file = st.file_uploader(
        "Envie imagem da cifra",
        type=["jpg", "jpeg", "png"],
        key="uploader_ocr"
    )

    if not file:
        return

    # ✅ use getvalue() (não consome o buffer igual read())
    image_bytes = file.getvalue()

    # ✅ assinatura simples pra detectar troca de arquivo
    file_sig = (file.name, file.size, getattr(file, "type", ""), hash(image_bytes[:2048]))

    # ✅ se trocou a imagem, limpa o resultado antigo automaticamente
    if st.session_state.get("_gemini_last_file_sig") != file_sig:
        st.session_state._gemini_last_file_sig = file_sig
        st.session_state._gemini_result = None

    st.image(image_bytes, caption="Pré-visualização", use_column_width=True)

    # -------------------------
    # transcrever
    # -------------------------
    if st.button("🚀 Transcrever cifra", key="btn_transcribe_ocr"):

        # ✅ força “refazer” mesmo se já tinha resultado
        st.session_state._gemini_result = None

        with st.spinner("Gemini analisando..."):
            try:
                text = _transcribe_image(image_bytes, model_name)

                st.session_state._gemini_result = text

            except Exception as e:
                st.error(f"Erro Gemini:\n\n{e}")
                return

    # -------------------------
    # resultado
    # -------------------------
    result = st.session_state.get("_gemini_result")

    if result:
        st.markdown("### 📄 Resultado")

        # CSS: mira exatamente o textarea pelo label (aria-label)
        st.markdown("""
        <style>
          textarea[aria-label="Cifra transcrita"]{
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace !important;

            font-size: clamp(10px, 1.4vw, 14px) !important;
            line-height: 1.25 !important;

            white-space: pre !important;
            overflow-x: auto !important;
            overflow-y: auto !important;

            overflow-wrap: normal !important;
            word-break: normal !important;
          }
        </style>
        """, unsafe_allow_html=True)

        st.text_area(
            "Cifra transcrita",
            result,
            height=350,
            key="ocr_result_textarea",
        )

        st.download_button(
            "💾 Baixar TXT",
            result,
            file_name="cifra.txt"
        )

# ==============================================================
# 13) PREVIEW (HTML responsivo + auto-fit folha + auto-fit cifra + OBS/PREPARAÇÃO)
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

    title = item.get("title", "")
    artist = item.get("artist", "")
    bpm = item.get("bpm", "")
    tom = item.get("tom", "")

    obs = item.get("obs", "") or ""
    prep = item.get("preparacao", "") or ""

    # ========= cifra =========
    cifra_txt = ""
    use_s = item.get("use_simplificada", False)
    cid = (item.get("cifra_simplificada_id") if use_s else item.get("cifra_id")) or ""

    if cid:
        cifra_txt = load_chord_from_drive(cid)
    else:
        cifra_txt = item.get("text", "")

    # ✅ AQUI: transpõe para o TOM atual do item
    tom_atual = (item.get("tom") or "").strip()
    tom_original = (item.get("tom_original") or tom_atual).strip()

    if cifra_txt and tom_original and tom_atual and tom_original != tom_atual:
        cifra_txt = transpose_chord_text(cifra_txt, tom_original, tom_atual)

    # só depois remove o "|" para exibição
    cifra_show = strip_chord_markers_for_display(cifra_txt)

    # ========= próxima =========
    next_title = ""
    next_artist = ""
    next_tom = ""
    next_bpm = ""

    if footer_mode == "next" and footer_next_item:
        next_title = footer_next_item.get("title", "")
        next_artist = footer_next_item.get("artist", "")
        next_tom = footer_next_item.get("tom", "")
        next_bpm = footer_next_item.get("bpm", "")

    def esc(s: str) -> str:
        return (
            (s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    html = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>

<style>
  html, body {{
      margin:0;
      padding:0;
      background:white;
      color:#1111/* ✅ mata scroll lateral */
      overflow-y:hidden;
  }}

  body {{
      font-family: "Courier New", monospace;
  }}

  /* ✅ wrapper do container */
  .outer {{
      width:100%;
      overflow-x:hidden;
      padding:0;
      margin:0;
  }}

  /* ✅ tudo que é “folha” fica aqui dentro e pode ser escalado */
  .scale-root {{
      width:max-content;       /* mede largura real do conteúdo */
      height:max-content;
      transform-origin: top left; /* escala a partir do topo/esquerda */
  }}

  .sheet {{
      width:clamp(90%, 100%, 100%);   /* mantém seus valores */
      aspect-ratio: 3 / 4;   /* ⭐ AQUI */
      margin:auto;
      padding:clamp(5px, 1vw, 10px);
      box-sizing:border-box;
  }}

  .top {{
      display:grid;
      grid-template-columns: 1fr auto auto;
      gap:10px;
      border-bottom:1px solid #ddd;
      padding-bottom:8px;
  }}

  .title {{
      font-size: clamp(14px, 2.2vw, 22px);
      font-weight:800;
  }}

  .artist {{
      font-size: clamp(11px, 1.8vw, 14px);
      color:#555;
  }}

  .kv {{
      text-align:right;
      font-size: clamp(10px, 1.6vw, 13px);
  }}

  .section-title {{
      margin-top:10px;
      font-weight:800;
      font-size:12px;
  }}

  .box {{
      border-top:1px solid #ddd;
      border-bottom:1px solid #ddd;
      padding:6px 0;
      min-height:20px;
      font-size:12px;
      white-space:pre-wrap;
      box-sizing:border-box;
  }}

  .cifra {{
      font-family: "Courier New", monospace;
      white-space: pre;
      overflow:hidden;
      margin-top:10px;
      padding:10px;
      border:1px solid #eee;
      border-radius:10px;

      font-size:14px;
      line-height:1.25;
      box-sizing:border-box;

      max-width: 100%; /* ✅ impede “estouro” */
  }}

  .next {{
      margin-top:12px;
      border-top:1px solid #ddd;
      padding-top:10px;
      display:grid;
      grid-template-columns: 1fr auto auto;
      gap:10px;
  }}
</style>
</head>

<body>

  <div class="outer" id="outer">
    <div class="scale-root" id="scaleRoot">
      <div class="sheet" id="sheet">

        <div class="top">
          <div>
            <div class="title">{esc(title)}</div>
            <div class="artist">{esc(artist)}</div>
          </div>

          <div class="kv"><b>TOM</b><br>{esc(tom)}</div>
          <div class="kv"><b>BPM</b><br>{esc(bpm)}</div>
        </div>

        <div class="section-title">OBS.:</div>
        <div class="box">{esc(obs)}</div>

        <div class="cifra">{esc(cifra_show)}</div>

        <div class="section-title">PREPARAÇÃO:</div>
        <div class="box">{esc(prep)}</div>

        <div class="next">
          <div>
            <div class="title">{esc(next_title)}</div>
            <div class="artist">{esc(next_artist)}</div>
          </div>
          <div class="kv"><b>TOM</b><br>{esc(next_tom)}</div>
          <div class="kv"><b>BPM</b><br>{esc(next_bpm)}</div>
        </div>

      </div>
    </div>
  </div>

<script>
(function() {{

  // ================================
  // 1) AUTO-FIT DA FOLHA (SCALE)
  // ================================
  const outer = document.getElementById("outer");
  const scaleRoot = document.getElementById("scaleRoot");
  const sheet = document.getElementById("sheet");

  function fitSheet() {{
    if (!outer || !scaleRoot || !sheet) return;

    // reseta escala pra medir “real”
    scaleRoot.style.transform = "scale(1)";

    // largura real do conteúdo (a folha)
    const contentW = scaleRoot.scrollWidth || sheet.scrollWidth || 1;
    const availW = outer.clientWidth || window.innerWidth || 1;

    // escala só pra baixo (nunca aumenta)
    const scale = Math.min(1, availW / contentW);

    scaleRoot.style.transform = "scale(" + scale + ")";

    // ajusta a altura do body pra não cortar (importante em iframe)
    const contentH = (scaleRoot.scrollHeight || sheet.scrollHeight || 1) * scale;
    document.body.style.height = contentH + "px";
  }}

  // ==========================================
  // 2) AUTO-FIT DA CIFRA (REDUZ FONT ATÉ CABER)
  // ==========================================
  const box = document.querySelector('.cifra');

  const MAX = 14;
  const MIN = 8;
  const STEP = 0.5;

  function fitsCifra() {{
    if (!box) return true;
    return box.scrollWidth <= box.clientWidth;
  }}

  function fitCifra() {{
    if (!box) return;

    let px = MAX;
    box.style.fontSize = px + 'px';

    while(px > MIN && !fitsCifra()) {{
      px -= STEP;
      box.style.fontSize = px + 'px';
    }}
  }}

  // roda na carga e resize
  function runAll() {{
    // duas RAFs ajudam o layout estabilizar no Streamlit/iframe
    requestAnimationFrame(() => {{
      requestAnimationFrame(() => {{
        fitCifra();
        fitSheet();
      }});
    }});
  }}

  window.addEventListener('load', runAll);
  window.addEventListener('resize', runAll);

  // em alguns mobiles, "orientationchange" ajuda
  window.addEventListener('orientationchange', runAll);

  // primeira execução
  runAll();

}})();
</script>

</body>
</html>
"""

    return html

# ==============================================================
# 13.5) FULLSCREEN SLIDES VIEWER (SWIPE) — ✅ fullscreen real + swipe
# ==============================================================
# ==============================================================
# 13.5) FULLSCREEN SLIDES VIEWER (SWIPE) — clean + FS btn auto-hide
# ==============================================================

import streamlit.components.v1 as components
import json

def fullscreen_slides_viewer(slides, titles=None, start_index=0, height=900):
    if not slides:
        st.info("Sem itens para exibir em fullscreen.")
        return

    if titles is None:
        titles = [f"{i+1}" for i in range(len(slides))]
    if len(titles) != len(slides):
        titles = (titles + [f"{i+1}" for i in range(len(slides))])[: len(slides)]

    try:
        start_index = int(start_index)
    except Exception:
        start_index = 0
    if start_index < 0 or start_index >= len(slides):
        start_index = 0

    payload = [{"title": t, "html": h} for t, h in zip(titles, slides)]
    payload_json = json.dumps(payload, ensure_ascii=False)
    payload_json_safe = payload_json.replace("</script>", "<\\/script>")

    html = """
<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<style>
  html, body {
    margin:0; padding:0; height:100%; width:100%;
    background:#000; overflow:hidden;
    font-family: system-ui, -apple-system, Segoe UI, Roboto, "Courier New", sans-serif;
  }

  .app { position:fixed; inset:0; background:#000; overflow:hidden; }

  .stage {
    position:fixed;
    top:0; left:0; right:0; bottom:0;
    background:#000;
    overflow:hidden;
    touch-action: pan-y;
  }

  .strip {
    display:flex;
    height:100%;
    width:100%;
    transform: translateX(0);
    transition: transform 220ms ease-out;
  }

  .slide {
    flex: 0 0 100%;
    height:100%;
    overflow:auto;
    -webkit-overflow-scrolling: touch;
    padding: 0;
    box-sizing:border-box;
  }

  .paper {
    width: 100%;
    max-width: 100%;
    margin: 0 auto;
    background: #fff;
    color: #111;
    border-radius: 0;
    padding: 0;
    box-sizing:border-box;
    box-shadow: none;
    overflow: hidden;
  }

  .paper iframe{
    width:100%;
    height: 100vh;
    border:0;
    display:block;
    background:#fff;
  }

  /* ZONAS DE SWIPE (por cima do iframe) */
  .swipe-zone{
    position: fixed;
    top: 0;
    bottom: 0;
    width: 14vw;
    max-width: 140px;
    z-index: 30;
    background: transparent;
    touch-action: pan-y;
  }
  #swipeLeft { left: 0; }
  #swipeRight { right: 0; }

  /* BOTÃO FULLSCREEN MENOR + AUTO-HIDE */
  .fsbtn{
    position: fixed;
    top: 10px;
    right: 10px;
    z-index: 60;

    width: 32px;
    height: 32px;

    border-radius: 999px;
    border: 1px solid rgba(255,255,255,0.25);
    background: rgba(0,0,0,0.42);
    color: #fff;

    font-size: 14px;
    line-height: 32px;
    text-align: center;
    cursor: pointer;

    user-select: none;
    -webkit-tap-highlight-color: transparent;

    opacity: 0;
    pointer-events: none;
    transform: translateY(-4px);
    transition: opacity 160ms ease, transform 160ms ease;
  }

  .fsbtn.show{
    opacity: 1;
    pointer-events: auto;
    transform: translateY(0);
  }

  :fullscreen .fsbtn { background: rgba(0,0,0,0.30); }
</style>
</head>
<body>
  <div class="app" id="appRoot">
    <div class="stage" id="stage">
      <div class="strip" id="strip"></div>
    </div>

    <!-- swipe zones -->
    <div class="swipe-zone" id="swipeLeft"></div>
    <div class="swipe-zone" id="swipeRight"></div>

    <!-- botão -->
    <div class="fsbtn" id="fsBtn" title="Fullscreen">⛶</div>
  </div>

  <script id="payload" type="application/json">__PAYLOAD_JSON__</script>

<script>
  // ===== config =====
  const AUTO_HIDE_MS = 2200; // tempo até sumir (ms)
  // ==================

  let items = [];
  try {
    items = JSON.parse(document.getElementById("payload").textContent || "[]");
  } catch(e) {
    items = [];
  }

  let idx = __START_INDEX__;

  const strip = document.getElementById("strip");
  const appRoot = document.getElementById("appRoot");
  const fsBtn = document.getElementById("fsBtn");
  const stage = document.getElementById("stage");

  function buildSlides() {
    strip.innerHTML = "";
    if (!items.length) {
      const div = document.createElement("div");
      div.style.color = "#fff";
      div.style.padding = "16px";
      div.textContent = "ERRO: items vazio (payload não carregou)";
      strip.appendChild(div);
      return;
    }

    items.forEach((it) => {
      const s = document.createElement("div");
      s.className = "slide";

      const p = document.createElement("div");
      p.className = "paper";

      const fr = document.createElement("iframe");
      fr.srcdoc = it.html;

      p.appendChild(fr);
      s.appendChild(p);
      strip.appendChild(s);
    });
  }

  function updateUI() {
    if (!items.length) return;
    strip.style.transform = "translateX(" + (-idx * 100) + "%)";
    const currentSlide = strip.children[idx];
    if (currentSlide) currentSlide.scrollTo(0, 0);
  }

  function goTo(i) { idx = (i + items.length) % items.length; updateUI(); }
  function next() { goTo(idx + 1); }
  function prev() { goTo(idx - 1); }

  // Swipe pelas zonas laterais
  function bindSwipe(el) {
    let x0=null, y0=null, t0=null;

    el.addEventListener("touchstart", (e) => {
      const t = e.touches[0];
      x0=t.clientX; y0=t.clientY; t0=Date.now();
    }, {passive:true});

    el.addEventListener("touchend", (e) => {
      if (x0===null) return;
      const t = e.changedTouches[0];
      const dx = t.clientX - x0;
      const dy = t.clientY - y0;
      const dt = Date.now() - t0;

      if (Math.abs(dx) > 50 && Math.abs(dy) < 80 && dt < 800) {
        if (dx < 0) next(); else prev();
      }
      x0=null; y0=null; t0=null;
    }, {passive:true});
  }

  bindSwipe(document.getElementById("swipeLeft"));
  bindSwipe(document.getElementById("swipeRight"));

  // Fullscreen toggle
  function enterFullscreen() {
    try {
      if (!document.fullscreenElement && appRoot.requestFullscreen) appRoot.requestFullscreen();
    } catch(e) {}
  }
  function exitFullscreen() {
    try {
      if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen();
    } catch(e) {}
  }

  fsBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (document.fullscreenElement) exitFullscreen();
    else enterFullscreen();
    showFsTemp();
  });

  // AUTO-HIDE do botão: aparece ao tocar/clicar na "folha"
  let hideTimer = null;

  function showFsTemp() {
    fsBtn.classList.add("show");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => fsBtn.classList.remove("show"), AUTO_HIDE_MS);
  }

  // Qualquer toque/click na stage mostra o botão
  stage.addEventListener("click", showFsTemp, {passive:true});
  stage.addEventListener("touchstart", showFsTemp, {passive:true});
  // No desktop, mover mouse também ajuda
  stage.addEventListener("mousemove", showFsTemp, {passive:true});

  // Teclas (pedal costuma mandar setas)
  document.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") next();
    if (e.key === "ArrowLeft") prev();
    if (e.key === "Escape") exitFullscreen();
    // ao usar teclado, mostra o botão rapidinho (pra você saber que está “vivo”)
    showFsTemp();
  });

  buildSlides();
  goTo(idx);

  // Mostra o botão no início por 2s só pra você achar ele
  showFsTemp();
</script>
</body>
</html>
"""

    html = html.replace("__PAYLOAD_JSON__", payload_json_safe)
    html = html.replace("__START_INDEX__", str(start_index))

    components.html(html, height=height, scrolling=False)

# ==============================================================
# 13.6) PDF EXPORT — IGUAL AO PREVIEW (Página atual + Setlist inteira)
#   ✅ Corrigido:
#   - Layout mais parecido com o preview (header + linhas + caixas)
#   - Caixa da cifra com altura dinâmica (usa o espaço que sobra)
#   - Auto-fit da cifra por LARGURA e ALTURA (não corta / não “sobra”)
#   - Fonte mono com suporte a acentos (tenta DejaVuSansMono; fallback Courier)
# ==============================================================

from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os
import textwrap

# -----------------------------
# Fonte (mono) com acentos
# -----------------------------
PDF_FONT_REG = {"name": "Courier", "bold": "Courier-Bold"}  # fallback

def _ensure_pdf_font():
    """
    Tenta registrar DejaVuSansMono (mono, boa p/ cifra, com acentos).
    Se não existir, usa Courier padrão.
    """
    global PDF_FONT_REG

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",  # repetido ok
    ]

    mono_path = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
    mono_bold_path = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

    try:
        if os.path.exists(mono_path):
            pdfmetrics.registerFont(TTFont("PDLMono", mono_path))
            PDF_FONT_REG["name"] = "PDLMono"
        if os.path.exists(mono_bold_path):
            pdfmetrics.registerFont(TTFont("PDLMono-Bold", mono_bold_path))
            PDF_FONT_REG["bold"] = "PDLMono-Bold"
        # se registrou pelo menos a normal, já melhora bastante
    except Exception:
        PDF_FONT_REG = {"name": "Courier", "bold": "Courier-Bold"}

_ensure_pdf_font()


# -----------------------------
# Helpers de texto
# -----------------------------
def _wrap_text_lines(s: str, max_chars: int):
    """
    Wrap por caracteres (mono). Preserva quebras e linhas vazias.
    """
    s = (s or "")
    if not s.strip():
        return [""]

    out = []
    for raw in s.splitlines():
        if not raw.strip():
            out.append("")
            continue
        out.extend(
            textwrap.wrap(
                raw,
                width=max_chars,
                break_long_words=True,
                replace_whitespace=False,
                drop_whitespace=False,
            )
        )
    return out


def _max_line_width_pt(lines, font_name, font_size):
    if not lines:
        return 0
    longest = max(lines, key=lambda x: len(x or ""))
    return pdfmetrics.stringWidth(longest or "", font_name, font_size)


def _calc_mono_font_size_fit(lines, box_w_pt, box_h_pt, font_name, max_fs=12, min_fs=6, padding_pt=10):
    """
    Auto-fit igual ao preview:
    - reduz fonte até CABER na largura e na altura da caixa.
    - considera leading ~ 1.25x
    """
    lines = lines if lines else [""]

    usable_w = max(1, box_w_pt - padding_pt)
    usable_h = max(1, box_h_pt - padding_pt)

    for fs in [x / 2 for x in range(int(max_fs * 2), int(min_fs * 2) - 1, -1)]:  # step 0.5
        # largura
        if _max_line_width_pt(lines, font_name, fs) > usable_w:
            continue

        # altura
        leading = fs * 1.25
        total_h = len(lines) * leading
        if total_h > usable_h:
            continue

        return fs

    return min_fs


# -----------------------------
# Reuso da lógica do preview (mesma que você já tinha)
# -----------------------------
def _compose_item_fields(item: dict, blocks, b_idx, i_idx):
    """
    Reusa a MESMA lógica do preview:
    - pega cifra do Drive ou item['text']
    - transpõe se precisar
    - remove '|' só para exibição
    - calcula próxima música
    """
    itype = item.get("type", "")

    # PAUSA
    if itype == "pause":
        footer_mode, footer_next_item = get_footer_context(blocks, b_idx, i_idx)
        next_title = next_artist = next_tom = next_bpm = ""
        if footer_mode == "next" and footer_next_item:
            if footer_next_item.get("type") == "pause":
                next_title = "PAUSA"
                next_artist = footer_next_item.get("label", "Pausa")
            else:
                next_title = footer_next_item.get("title", "")
                next_artist = footer_next_item.get("artist", "")
                next_tom = footer_next_item.get("tom", "")
                next_bpm = footer_next_item.get("bpm", "")

        return {
            "is_pause": True,
            "title": "PAUSA",
            "artist": "",
            "tom": "",
            "bpm": "",
            "obs": item.get("label", "Pausa"),
            "prep": "",
            "cifra_show": "",
            "next_title": next_title,
            "next_artist": next_artist,
            "next_tom": next_tom,
            "next_bpm": next_bpm,
        }

    # MÚSICA
    title = item.get("title", "")
    artist = item.get("artist", "")
    tom = item.get("tom", "")
    bpm = item.get("bpm", "")
    obs = item.get("obs", "") or ""
    prep = item.get("preparacao", "") or ""

    # cifra (mesmo critério do preview)
    cifra_txt = ""
    use_s = item.get("use_simplificada", False)
    cid = (item.get("cifra_simplificada_id") if use_s else item.get("cifra_id")) or ""

    if cid:
        cifra_txt = load_chord_from_drive(cid)
    else:
        cifra_txt = item.get("text", "")

    tom_atual = (item.get("tom") or "").strip()
    tom_original = (item.get("tom_original") or tom_atual).strip()

    if cifra_txt and tom_original and tom_atual and tom_original != tom_atual:
        cifra_txt = transpose_chord_text(cifra_txt, tom_original, tom_atual)

    # só depois remove o "|" para exibição (igual preview)
    cifra_show = strip_chord_markers_for_display(cifra_txt)

    # próxima
    footer_mode, footer_next_item = get_footer_context(blocks, b_idx, i_idx)
    next_title = next_artist = next_tom = next_bpm = ""
    if footer_mode == "next" and footer_next_item:
        if footer_next_item.get("type") == "pause":
            next_title = "PAUSA"
            next_artist = footer_next_item.get("label", "Pausa")
        else:
            next_title = footer_next_item.get("title", "")
            next_artist = footer_next_item.get("artist", "")
            next_tom = footer_next_item.get("tom", "")
            next_bpm = footer_next_item.get("bpm", "")

    return {
        "is_pause": False,
        "title": title,
        "artist": artist,
        "tom": tom,
        "bpm": bpm,
        "obs": obs,
        "prep": prep,
        "cifra_show": cifra_show,
        "next_title": next_title,
        "next_artist": next_artist,
        "next_tom": next_tom,
        "next_bpm": next_bpm,
    }


# -----------------------------
# Desenho de 1 página (layout “igual preview”)
# -----------------------------
def _draw_item_page(c: canvas.Canvas, fields: dict, page_w, page_h):
    FONT = PDF_FONT_REG["name"]
    FONT_B = PDF_FONT_REG["bold"]

    margin_x = 14 * mm
    top_margin = 14 * mm
    bottom_margin = 14 * mm

    # Áreas principais
    x0 = margin_x
    x1 = page_w - margin_x
    w = x1 - x0

    y = page_h - top_margin

    # =========================
    # HEADER (título + artista) + TOM/BPM à direita
    # =========================
    c.setFont(FONT_B, 16)
    c.drawString(x0, y, (fields.get("title", "") or "")[:80])

    # TOM/BPM (direita)
    c.setFont(FONT_B, 11)
    c.drawRightString(x1, y, f"TOM: {fields.get('tom','')}")
    c.setFont(FONT_B, 11)
    c.drawRightString(x1, y - 7 * mm, f"BPM: {fields.get('bpm','')}")

    y -= 7 * mm

    c.setFont(FONT, 11)
    if fields.get("artist"):
        c.drawString(x0, y, (fields.get("artist", "") or "")[:110])

    # linha separadora (igual preview)
    y -= 6 * mm
    c.setLineWidth(0.8)
    c.line(x0, y, x1, y)
    y -= 7 * mm

    # =========================
    # OBS.: (label + box com linhas top/bottom)
    # =========================
    c.setFont(FONT_B, 11)
    c.drawString(x0, y, "OBS.:")
    y -= 5 * mm

    obs_font = 10
    c.setFont(FONT, obs_font)

    # heurística por “chars” para wrap (mono)
    # (aprox. 95 chars cabem bem nessa margem com fonte 10)
    obs_lines = _wrap_text_lines(fields.get("obs", ""), max_chars=95)

    # altura desejada (dinâmica, cap)
    obs_line_h = obs_font * 1.25
    obs_pad = 2 * mm
    obs_max_lines = 6
    obs_used_lines = min(len(obs_lines), obs_max_lines)
    obs_box_h = obs_pad * 2 + obs_used_lines * (obs_line_h * 0.3528)  # pt->mm aprox? (vamos desenhar em pt, então melhor em pt)
    # ↑ Vamos trabalhar em “pt” com o canvas; então vamos calcular em pt abaixo, mais correto.

    # Melhor: define box em pt diretamente
    obs_pad_pt = 6
    obs_line_h_pt = obs_font * 1.25
    obs_used_lines = max(1, min(len(obs_lines), obs_max_lines))
    obs_box_h_pt = obs_pad_pt * 2 + obs_used_lines * obs_line_h_pt

    # linhas superior/inferior do box
    # (no preview é border-top/bottom)
    obs_top_y = y
    c.line(x0, obs_top_y, x1, obs_top_y)
    obs_bottom_y = obs_top_y - obs_box_h_pt
    c.line(x0, obs_bottom_y, x1, obs_bottom_y)

    # escreve texto dentro
    tx = c.beginText()
    tx.setTextOrigin(x0, obs_top_y - obs_pad_pt - obs_font)
    tx.setFont(FONT, obs_font)
    tx.setLeading(obs_line_h_pt)

    for ln in obs_lines[:obs_max_lines]:
        tx.textLine(ln)
    c.drawText(tx)

    y = obs_bottom_y - 10  # gap

    # =========================
    # PREPARAÇÃO (vamos desenhar depois da CIFRA, mas precisamos reservar espaço)
    # =========================
    prep_font = 10
    prep_lines = _wrap_text_lines(fields.get("prep", ""), max_chars=95)
    prep_max_lines = 6
    prep_used_lines = max(1, min(len(prep_lines), prep_max_lines))
    prep_line_h_pt = prep_font * 1.25
    prep_pad_pt = 6
    prep_box_h_pt = prep_pad_pt * 2 + prep_used_lines * prep_line_h_pt

    # Label PREPARAÇÃO ocupa ~ (11 + gap)
    prep_label_h_pt = 11 + 8  # label + respiro

    # =========================
    # FOOTER PRÓXIMA (reservar)
    # =========================
    footer_h_pt = 34 * mm  # reserva semelhante ao preview
    footer_top_y = bottom_margin + footer_h_pt  # linha de separação do footer

    # =========================
    # CIFRA (caixa ocupa o espaço que sobrar)
    # =========================
    # área disponível até antes da preparação + footer
    cifra_top_y = y
    cifra_bottom_limit = footer_top_y + prep_label_h_pt + prep_box_h_pt + 10  # 10 pt gap
    cifra_h_pt = max(120, cifra_top_y - cifra_bottom_limit)  # mínimo

    # caixa da cifra (retângulo com borda leve)
    cifra_box_y = cifra_top_y
    cifra_box_h = cifra_h_pt
    cifra_box_w = w

    c.setLineWidth(0.8)
    c.rect(x0, cifra_box_y - cifra_box_h, cifra_box_w, cifra_box_h, stroke=1, fill=0)

    # texto da cifra (auto-fit por largura e altura)
    cifra = fields.get("cifra_show", "") or ""
    cifra_lines = cifra.splitlines() if cifra else [""]

    # padding interno
    pad_left = 8
    pad_top = 10
    pad_right = 8
    pad_bottom = 10

    fs = _calc_mono_font_size_fit(
        lines=cifra_lines,
        box_w_pt=cifra_box_w,
        box_h_pt=cifra_box_h,
        font_name=FONT,
        max_fs=12,
        min_fs=6,
        padding_pt=(pad_left + pad_right + 4),
    )

    leading = fs * 1.25

    # quantas linhas cabem (segurança)
    usable_h = max(1, cifra_box_h - pad_top - pad_bottom)
    max_lines = int(usable_h / leading) if leading > 0 else len(cifra_lines)
    max_lines = max(1, max_lines)

    t = c.beginText()
    t.setFont(FONT, fs)
    t.setLeading(leading)
    t.setTextOrigin(x0 + pad_left, cifra_box_y - pad_top - fs)

    for ln in cifra_lines[:max_lines]:
        t.textLine((ln or "").rstrip("\n"))
    c.drawText(t)

    y = (cifra_box_y - cifra_box_h) - 12  # gap

    # =========================
    # PREPARAÇÃO (label + box com linhas top/bottom)
    # =========================
    c.setFont(FONT_B, 11)
    c.drawString(x0, y, "PREPARAÇÃO:")
    y -= 5 * mm

    # box (linhas sup/inf)
    c.setFont(FONT, prep_font)

    prep_top_y = y
    c.line(x0, prep_top_y, x1, prep_top_y)
    prep_bottom_y = prep_top_y - prep_box_h_pt
    c.line(x0, prep_bottom_y, x1, prep_bottom_y)

    tp = c.beginText()
    tp.setTextOrigin(x0, prep_top_y - prep_pad_pt - prep_font)
    tp.setFont(FONT, prep_font)
    tp.setLeading(prep_line_h_pt)

    for ln in prep_lines[:prep_max_lines]:
        tp.textLine(ln)
    c.drawText(tp)

    # =========================
    # FOOTER PRÓXIMA
    # =========================
    # linha do footer
    c.setLineWidth(0.8)
    c.line(x0, footer_top_y, x1, footer_top_y)

    c.setFont(FONT_B, 11)
    c.drawString(x0, footer_top_y - 12, "PRÓXIMA:")

    # texto da próxima
    c.setFont(FONT, 10)
    nxt = (fields.get("next_title", "") or "")
    if fields.get("next_artist"):
        nxt += f" – {fields.get('next_artist','')}"
    c.drawString(x0, footer_top_y - 24, nxt[:140])

    # TOM/BPM da próxima (direita)
    c.setFont(FONT, 10)
    c.drawRightString(x1, footer_top_y - 12, f"TOM: {fields.get('next_tom','')}")
    c.drawRightString(x1, footer_top_y - 24, f"BPM: {fields.get('next_bpm','')}")


# -----------------------------
# PDF: Página atual
# -----------------------------
def make_pdf_for_single_item(item, blocks, b_idx, i_idx, filename_base="PDL_Preview"):
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    fields = _compose_item_fields(item, blocks, b_idx, i_idx)
    _draw_item_page(c, fields, w, h)

    c.showPage()
    c.save()
    pdf_bytes = buf.getvalue()
    buf.close()

    return pdf_bytes, f"{filename_base}.pdf"


# -----------------------------
# PDF: Setlist inteira
# -----------------------------
def make_pdf_for_full_setlist(blocks, filename_base="PDL_Setlist"):
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    # achata tudo em ordem
    flat = []
    for b_idx, block in enumerate(blocks):
        for i_idx, it in enumerate(block.get("items", [])):
            flat.append((b_idx, i_idx, it))

    for (b_idx, i_idx, it) in flat:
        fields = _compose_item_fields(it, blocks, b_idx, i_idx)
        _draw_item_page(c, fields, w, h)
        c.showPage()

    c.save()
    pdf_bytes = buf.getvalue()
    buf.close()

    return pdf_bytes, f"{filename_base}.pdf"

# ==============================================================
# 14) HOME
# ==============================================================

def render_home():
       
    st.image(
    "https://raw.githubusercontent.com/FelipeNovais89/PDLSetlist/main/Data/file_000000009f20720aa44d499e2a2763b0.png",
    use_container_width=True
    )

    st.markdown("---")

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
# 14.5) TELA SETLIST
# ==============================================================

def render_setlist_screen():
    left_col, right_col = st.columns([1.1, 1])

    # ==========================================================
    # COLUNA ESQUERDA — EDITOR DA SETLIST
    # ==========================================================
    with left_col:
        st.subheader("Editor de Setlist (modo árvore)")
        render_setlist_editor_tree()

    # ==========================================================
    # COLUNA DIREITA — PREVIEW / PDF / FULLSCREEN
    # ==========================================================
    with right_col:
        st.subheader("Preview")

        blocks = st.session_state.blocks

        # ==========================================================
        # BOTÕES PDF
        # ==========================================================
        pdf_col1, pdf_col2 = st.columns(2)

        with pdf_col1:
            if blocks:
                pdf_all_bytes, pdf_all_name = make_pdf_for_full_setlist(
                    blocks,
                    filename_base=f"{st.session_state.setlist_name} (Setlist)"
                )
                st.download_button(
                    "📄 Baixar PDF (Setlist inteira)",
                    data=pdf_all_bytes,
                    file_name=pdf_all_name,
                    mime="application/pdf",
                    use_container_width=True,
                    key="dl_pdf_full"
                )

        with pdf_col2:
            st.caption("PDF da página atual aparece após selecionar uma música (ou usar 👁).")

        # ==========================================================
        # BOTÕES FULLSCREEN
        # ==========================================================
        b1, b2 = st.columns([1, 1])

        with b1:
            if st.button("🖥️ Fullscreen (slides / swipe)", use_container_width=True, key="btn_fs_on"):
                st.session_state.pdl_fullscreen = True
                st.rerun()

        with b2:
            if st.button("⬅️ Voltar", use_container_width=True, key="btn_fs_off"):
                st.session_state.pdl_fullscreen = False
                st.rerun()

        # --------------------------------------------------
        # Seleção do current_item
        # --------------------------------------------------
        current_item = None
        current_block_name = ""
        cur_block_idx = None
        cur_item_idx = None

        # PRIORIDADE 1 — ITEM SELECIONADO NO EDITOR
        sel_b = st.session_state.selected_block_idx
        sel_i = st.session_state.selected_item_idx

        if sel_b is not None and sel_i is not None:
            if 0 <= sel_b < len(blocks) and 0 <= sel_i < len(blocks[sel_b]["items"]):
                current_item = blocks[sel_b]["items"][sel_i]
                current_block_name = blocks[sel_b]["name"]
                cur_block_idx = sel_b
                cur_item_idx = sel_i

        # PRIORIDADE 2 — ITEM MARCADO COM 👁
        if current_item is None:
            cur = st.session_state.current_item
            if cur is not None:
                b_idx, i_idx = cur
                if 0 <= b_idx < len(blocks) and 0 <= i_idx < len(blocks[b_idx]["items"]):
                    current_item = blocks[b_idx]["items"][i_idx]
                    current_block_name = blocks[b_idx]["name"]
                    cur_block_idx = b_idx
                    cur_item_idx = i_idx

        # PRIORIDADE 3 — PRIMEIRO ITEM DO SETLIST
        if current_item is None:
            for b_idx, block in enumerate(blocks):
                if block.get("items"):
                    current_item = block["items"][0]
                    current_block_name = block.get("name", f"Bloco {b_idx+1}")
                    cur_block_idx = b_idx
                    cur_item_idx = 0
                    break

        if current_item is None:
            st.info("Adicione músicas ao setlist para ver o preview.")
            return

        # ==========================================================
        # PDF DA PÁGINA ATUAL
        # ==========================================================
        if current_item is not None and cur_block_idx is not None and cur_item_idx is not None:
            pdf_one_bytes, pdf_one_name = make_pdf_for_single_item(
                current_item,
                blocks,
                cur_block_idx,
                cur_item_idx,
                filename_base=f"{st.session_state.setlist_name} (Página atual)"
            )
            st.download_button(
                "📄 Baixar PDF (Página atual)",
                data=pdf_one_bytes,
                file_name=pdf_one_name,
                mime="application/pdf",
                use_container_width=True,
                key="dl_pdf_one"
            )

        # --------------------------------------------------
        # MODO NORMAL
        # --------------------------------------------------
        if not st.session_state.pdl_fullscreen:
            footer_mode, footer_next_item = get_footer_context(blocks, cur_block_idx, cur_item_idx)
            html_current = build_sheet_page_html(
                current_item,
                footer_mode,
                footer_next_item,
                current_block_name
            )

            st.components.v1.html(
                html_current,
                height=700,
                scrolling=False,
            )
            return

        # --------------------------------------------------
        # MODO FULLSCREEN
        # --------------------------------------------------
        flat = []
        for b_idx, block in enumerate(blocks):
            items = block.get("items", [])
            for i_idx, it in enumerate(items):
                flat.append((b_idx, i_idx, block.get("name", f"Bloco {b_idx+1}"), it))

        if not flat:
            st.info("Sem itens para exibir em fullscreen.")
            return

        start_index = 0
        if cur_block_idx is not None and cur_item_idx is not None:
            for k, (b, i, _, _) in enumerate(flat):
                if b == cur_block_idx and i == cur_item_idx:
                    start_index = k
                    break

        slides = []
        titles = []

        def _pretty_title(it: dict) -> str:
            if not isinstance(it, dict):
                return "Cifra"
            if it.get("type") == "pause":
                return f"Pausa — {it.get('label','Pausa')}"
            s = (it.get("title") or "Música").strip()
            a = (it.get("artist") or "").strip()
            return f"{s} - {a}".strip(" -")

        for (b_idx, i_idx, blk_name, it) in flat:
            footer_mode, footer_next_item = get_footer_context(blocks, b_idx, i_idx)
            html = build_sheet_page_html(it, footer_mode, footer_next_item, blk_name)
            slides.append(html)
            titles.append(_pretty_title(it))

        fullscreen_slides_viewer(
            slides=slides,
            titles=titles,
            start_index=start_index,
            height=700
        )

# ==============================================================
# 14.6) TELA BANCO DE MÚSICAS
# ==============================================================

def render_song_database_screen():
    st.subheader("Banco de Músicas")

    st.caption("Aqui você pode visualizar o banco atual e gerenciar os arquivos TXT das cifras no Google Drive.")

    render_song_database()

# ==============================================================
# 14.7) TELA GERENCIAMENTO DE CIFRAS
# ==============================================================

def render_chord_management_screen():
    st.subheader("Gerenciamento de Cifras")

    st.caption("Use o Gemini AI para transcrever imagens de cifras em texto TXT.")

    render_gemini_ocr_section()

# ==============================================================
# 14.8) SIDEBAR / NAVEGAÇÃO
# ==============================================================

def render_sidebar_navigation():
    with st.sidebar:
        st.markdown("## 🎵 PDL Setlist")

        st.markdown("---")
        st.markdown(f"**Setlist atual:**")
        st.markdown(f"`{st.session_state.setlist_name}`")

        st.markdown("---")
        selected_section = st.radio(
            "Navegação",
            options=["setlist", "banco", "cifras"],
            format_func=lambda x: {
                "setlist": f"Setlist — {st.session_state.setlist_name}",
                "banco": "Banco de Músicas",
                "cifras": "Gerenciamento de Cifras",
            }[x],
            index=["setlist", "banco", "cifras"].index(
                st.session_state.get("app_section", "setlist")
            ),
            key="sidebar_app_section_radio"
        )

        st.session_state.app_section = selected_section

        st.markdown("---")

        if st.button("🏠 Voltar à tela inicial", use_container_width=True, key="sidebar_go_home"):
            st.session_state.screen = "home"
            st.rerun()

        if st.button("💾 Salvar setlist (GitHub CSV)", use_container_width=True, key="sidebar_save_setlist"):
            save_current_setlist_to_github()
    
# ==============================================================
# 15) MAIN  (SEÇÃO INTEIRA — ✅ FULLSCREEN SLIDES com TODAS as páginas)
# ==============================================================

# ==============================================================
# 15) MAIN
# ==============================================================

def main():
    st.set_page_config(
        page_title="PDL Setlist",
        layout="wide",
        page_icon="🎵",
        initial_sidebar_state="collapsed"
    )

    st.markdown(
    """
    <style>

    /* BACKGROUND IMAGE */
    .stApp {
        background-image: linear-gradient(
            rgba(0,0,0,0.80),
            rgba(0,0,0,0.85)
        ),
        url("https://raw.githubusercontent.com/FelipeNovais89/PDLSetlist/main/Data/IMG-20260202-WA0019.jpg");

        background-size: cover;
        background-position: center;
        background-repeat: no-repeat;
        background-attachment: fixed;
    }

    </style>
    """,
    unsafe_allow_html=True
    )
    # ---------- ESTADO INICIAL ----------
    init_state()

    # ---------- TELA HOME ----------
    if st.session_state.screen == "home":
        render_home()
        return

    # ---------- SIDEBAR ----------
    render_sidebar_navigation()

    # ---------- CABEÇALHO ----------
    top_left, top_right = st.columns([3, 1])

    with top_left:
        st.markdown(f"### Setlist: {st.session_state.setlist_name}")
        st.session_state.setlist_name = st.text_input(
            "Nome do setlist",
            value=st.session_state.setlist_name,
            label_visibility="collapsed",
            key="main_setlist_name_input"
        )

    with top_right:
        st.info(f"Seção atual: **{ {'setlist':'Setlist', 'banco':'Banco de Músicas', 'cifras':'Gerenciamento de Cifras'}[st.session_state.app_section] }**")

    st.markdown("---")

    # ---------- ROTEAMENTO DAS TELAS ----------
    if st.session_state.app_section == "setlist":
        render_setlist_screen()

    elif st.session_state.app_section == "banco":
        render_song_database_screen()

    elif st.session_state.app_section == "cifras":
        render_chord_management_screen()

# ==============================================================
# EXECUÇÃO
# ==============================================================

if __name__ == "__main__":
    main()
