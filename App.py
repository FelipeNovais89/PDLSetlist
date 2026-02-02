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

        # ==========================================================
        # ✅ NOVO: OBS / PREPARAÇÃO (por música)
        # ==========================================================
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

    else:
        st.markdown("**⏸ Pausa**")
        item["label"] = st.text_input(
            "Descrição da pausa",
            value=item.get("label", "Pausa"),
            key=f"pause_label_{b_idx}_{i_idx}",
        )

# ==============================================================
# 11) MODAL — Song Picker (popup com busca + checkbox + scroll SÓ na lista)
# ==============================================================
# ==============================================================
# 11.5) MODAL — Song Picker (popup com busca + checkbox + scroll SÓ na lista)
# ==============================================================

def open_song_picker_dialog(block_idx: int, songs_df: pd.DataFrame):
    # ---- estado local do modal ----
    if "song_picker_selected" not in st.session_state:
        st.session_state.song_picker_selected = set()
    if "song_picker_query" not in st.session_state:
        st.session_state.song_picker_query = ""

    @st.dialog("Adicionar músicas ao bloco", width="small")
    def _dialog():
        st.caption("Marque as músicas que deseja adicionar neste bloco.")

        # 🔎 Busca
        st.session_state.song_picker_query = st.text_input(
            "Buscar",
            value=st.session_state.song_picker_query,
            placeholder="Digite parte do título ou artista…",
            key="song_picker_search_input",
        ).strip()

        # CSS: scroll APENAS na lista
        st.markdown(
            """
            <style>
            /* Não mexe no modal inteiro. Só na caixa da lista */
            .song-list-box{
                max-height: 42vh;          /* altura da área rolável */
                overflow-y: auto;          /* scroll só aqui */
                padding: 10px 10px 6px 10px;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 12px;
                background: rgba(255,255,255,0.02);
            }
            /* deixa os checkboxes mais compactos */
            div[data-testid="stCheckbox"]{
                margin-bottom: -6px;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        # Filtra dataframe
        df = songs_df.copy()
        df["Título"] = df["Título"].astype(str)
        df["Artista"] = df["Artista"].astype(str)

        q = st.session_state.song_picker_query.lower()
        if q:
            df = df[
                df["Título"].str.lower().str.contains(q, na=False)
                | df["Artista"].str.lower().str.contains(q, na=False)
            ]

        df = df.reset_index(drop=True)

        # (Opcional) limitar render pra ficar leve
        MAX_SHOW = 250
        total = len(df)
        if total > MAX_SHOW:
            st.info(f"Mostrando {MAX_SHOW} de {total} músicas. Use a busca para refinar.")
            df = df.head(MAX_SHOW)

        # ---------- LISTA ROLÁVEL (somente aqui) ----------
        st.markdown('<div class="song-list-box">', unsafe_allow_html=True)

        for idx, row in df.iterrows():
            titulo = (row.get("Título", "") or "").strip()
            artista = (row.get("Artista", "") or "").strip()
            tom = (row.get("Tom_Original", "") or "").strip()

            if not titulo:
                continue

            label = f"{titulo} – {artista}" if artista else titulo
            if tom:
                label += f" ({tom})"

            # ✅ id estável por música (melhor que idx do df)
            song_id = f"{titulo}||{artista}"

            # ✅ KEY ÚNICA por música + por bloco (evita DuplicateElementKey)
            key = f"song_pick_cb__b{block_idx}__{abs(hash(song_id))}"

            checked = song_id in st.session_state.song_picker_selected
            val = st.checkbox(label, value=checked, key=key)

            if val:
                st.session_state.song_picker_selected.add(song_id)
            else:
                st.session_state.song_picker_selected.discard(song_id)

        st.markdown("</div>", unsafe_allow_html=True)
        # ---------- FIM LISTA ROLÁVEL ----------

        st.markdown("---")

        # Botões fixos (fora da lista rolável)
        c1, c2, c3 = st.columns([1.2, 1, 1.4])

        if c1.button("Limpar seleção", use_container_width=True, key=f"song_pick_clear__b{block_idx}"):
            st.session_state.song_picker_selected = set()
            st.rerun()

        if c2.button("Cancelar", use_container_width=True, key=f"song_pick_cancel__b{block_idx}"):
            st.session_state.song_picker_open = False
            st.session_state.song_picker_block_idx = None
            st.rerun()

        if c3.button("Adicionar selecionadas", use_container_width=True, key=f"song_pick_add__b{block_idx}"):
            # Monta mapa rápido do banco (por song_id)
            # Observação: se tiver músicas repetidas (mesmo título/artista), isso vira ambíguo.
            # Se acontecer, a gente adiciona também BPM+Tom no song_id.
            bank = {}
            for _, r in songs_df.iterrows():
                t = str(r.get("Título", "") or "").strip()
                a = str(r.get("Artista", "") or "").strip()
                sid = f"{t}||{a}"
                bank[sid] = r

            target_block = st.session_state.blocks[block_idx]
            added = 0

            for sid in list(st.session_state.song_picker_selected):
                if sid not in bank:
                    continue
                r = bank[sid]

                cifra_id = str(r.get("CifraDriveID", "") or "").strip()
                cifra_simplificada_id = str(r.get("CifraSimplificadaID", "") or "").strip()

                new_item = {
                    "type": "music",
                    "title": r.get("Título", ""),
                    "artist": r.get("Artista", ""),
                    "tom_original": r.get("Tom_Original", ""),
                    "tom": r.get("Tom_Original", ""),
                    "bpm": r.get("BPM", ""),
                    "cifra_id": cifra_id,
                    "cifra_simplificada_id": cifra_simplificada_id,
                    "use_simplificada": False,
                    "text": "",
                    # se você já usa obs/preparacao por música:
                    "obs": "",
                    "preparacao": "",
                }
                target_block["items"].append(new_item)
                added += 1

            # fecha modal e limpa seleção
            st.session_state.song_picker_selected = set()
            st.session_state.song_picker_open = False
            st.session_state.song_picker_block_idx = None
            st.success(f"Adicionadas: {added}")
            st.rerun()

    _dialog()
    
# ==============================================================
# 11.5) EDITOR EM ÁRVORE (SETLIST) — versão estável + modal picker
# ==============================================================

def render_setlist_editor_tree():
    blocks = st.session_state.blocks
    songs_df = st.session_state.songs_df

    st.markdown("### Estrutura da Setlist (modo árvore)")

    # ----------------------------------------------------------
    # Adicionar bloco
    # ----------------------------------------------------------
    if st.button("+ Adicionar bloco", use_container_width=True, key="btn_add_block_global"):
        st.session_state.blocks.append({"name": f"Bloco {len(blocks) + 1}", "items": []})
        st.rerun()

    # ==========================================================
    # LOOP DOS BLOCOS
    # ==========================================================
    for b_idx, block in enumerate(blocks):

        with st.expander(
            f"Bloco {b_idx + 1}: {block.get('name', f'Bloco {b_idx+1}')}",
            expanded=False
        ):

            # --------------------------------------------------
            # Cabeçalho do bloco
            # --------------------------------------------------
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

            # ==================================================
            # ITENS DO BLOCO
            # ==================================================
            for i, item in enumerate(block.get("items", [])):

                col_label, col_btns = st.columns([8, 2])

                # ---------- label ----------
                if item.get("type") == "music":
                    title = (item.get("title") or "Nova música").strip()
                    artist = (item.get("artist") or "").strip()
                    tom = (item.get("tom") or item.get("tom_original") or "").strip()
                    bpm = (item.get("bpm") or "").strip()

                    label_main = f"🎵 {title}"
                    if artist:
                        label_main += f" – {artist}"

                    meta = []
                    if tom:
                        meta.append(f"Tom: {tom}")
                    if bpm:
                        meta.append(f"BPM: {bpm}")

                    label = label_main + ("  ·  " + " | ".join(meta) if meta else "")

                else:
                    label = f"⏸ {item.get('label', 'Pausa')}"

                # selecionar item
                if col_label.button(label, key=f"sel_item_{b_idx}_{i}"):
                    st.session_state.selected_block_idx = b_idx
                    st.session_state.selected_item_idx = i
                    st.session_state.current_item = (b_idx, i)
                    st.rerun()

                # ---------- botões ----------
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

            # ==================================================
            # BOTÕES ADICIONAR
            # ==================================================
            col_add_mus, col_add_pause = st.columns(2)

            # ✅ abre modal (NÃO chama função aqui)
            if col_add_mus.button("Música do banco", key=f"add_mus_blk_{b_idx}", use_container_width=True):
                st.session_state.song_picker_open = True
                st.session_state.song_picker_block_idx = b_idx
                st.rerun()

            if col_add_pause.button("Pausa", key=f"add_pause_blk_{b_idx}", use_container_width=True):
                block["items"].append({"type": "pause", "label": "Pausa"})
                st.rerun()

    # ==========================================================
    # ✅ MODAL FORA DO LOOP (CRÍTICO)
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
# 13) PREVIEW (HTML responsivo + auto-fit cifra + OBS/PREPARAÇÃO)
# ==============================================================

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
      font-family: Arial, sans-serif;
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
# 15) MAIN  (SEÇÃO INTEIRA — ✅ FULLSCREEN SLIDES com TODAS as páginas)
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
    # COLUNA DIREITA — PREVIEW (COM FULLSCREEN SLIDES)
    # ==========================================================
    with right_col:
        st.subheader("Preview")

        blocks = st.session_state.blocks

        # estado do fullscreen (persistente)
        if "pdl_fullscreen" not in st.session_state:
            st.session_state.pdl_fullscreen = False

        # botões de controle
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
        # Seleção do "current_item" (para preview normal)
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

        # PRIORIDADE 2 — ITEM MARCADO COM 👁 (current_item)
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

        # --------------------------------------------------
        # MODO NORMAL (preview único)
        # --------------------------------------------------
        if not st.session_state.pdl_fullscreen:
            footer_mode, footer_next_item = get_footer_context(blocks, cur_block_idx, cur_item_idx)
            html_current = build_sheet_page_html(current_item, footer_mode, footer_next_item, current_block_name)

            st.components.v1.html(
                html_current,
                height=700,
                scrolling=False,
            )
            return

        # --------------------------------------------------
        # MODO FULLSCREEN (slides) — ✅ TODAS as páginas da setlist
        # --------------------------------------------------

        # 1) “achata” todos os itens em ordem (bloco por bloco)
        flat = []
        for b_idx, block in enumerate(blocks):
            items = block.get("items", [])
            for i_idx, it in enumerate(items):
                flat.append((b_idx, i_idx, block.get("name", f"Bloco {b_idx+1}"), it))

        if not flat:
            st.info("Sem itens para exibir em fullscreen.")
            return

        # 2) define o índice inicial (mesmo item que está no preview)
        start_index = 0
        if cur_block_idx is not None and cur_item_idx is not None:
            for k, (b, i, _, _) in enumerate(flat):
                if b == cur_block_idx and i == cur_item_idx:
                    start_index = k
                    break

        # 3) monta slides e títulos
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

        # 4) renderiza o viewer (com fullscreen via requestFullscreen dentro do HTML)
        fullscreen_slides_viewer(
            slides=slides,
            titles=titles,
            start_index=start_index,
            height=700
        )


# ==============================================================
# EXECUÇÃO
# ==============================================================

if __name__ == "__main__":
    main()
