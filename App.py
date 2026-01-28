import streamlit as st
import pandas as pd
import io
import re
import base64
import json
import requests
from datetime import datetime
from urllib.parse import quote

import google.generativeai as genai

# ==============================================================
# 0) CONFIG GERAL
# ==============================================================

st.set_page_config(page_title="PDL Setlist", layout="wide", page_icon="🎵")

# ==============================================================
# 1) SECRETS / GITHUB
# ==============================================================

def get_secret(path, default=None):
    """
    Busca segredo em st.secrets, aceitando:
      - get_secret("github.token")
      - get_secret(("github","token"))
    """
    try:
        if isinstance(path, tuple):
            d = st.secrets
            for k in path:
                d = d[k]
            return d
        if "." in path:
            a, b = path.split(".", 1)
            return st.secrets.get(a, {}).get(b, default)
        return st.secrets.get(path, default)
    except Exception:
        return default


GITHUB_TOKEN = get_secret(("github", "token"), None)  # obrigatório para salvar
GITHUB_REPO  = get_secret(("github", "repo"), "FelipeNovais89/PDLSetlist")
GITHUB_BRANCH = get_secret(("github", "branch"), "main")

# caminhos no repo
SONGS_CSV_PATH = get_secret(("github", "songs_csv_path"), "Data/PDL_musicas.csv")
SETLISTS_DIR   = get_secret(("github", "setlists_dir"), "Data/Setlists")
CHORDS_DIR     = get_secret(("github", "chords_dir"), "Data/Chords")

# seu RAW (você passou esse URL)
SONGS_CSV_RAW_URL = get_secret(
    ("github", "songs_csv_raw_url"),
    "https://raw.githubusercontent.com/FelipeNovais89/PDLSetlist/refs/heads/main/Data/PDL_musicas.csv"
)

# ==============================================================
# 2) GEMINI – API KEY
# ==============================================================

def get_gemini_api_key():
    try:
        if "gemini_api_key" in st.secrets:
            return st.secrets["gemini_api_key"]
        if "sheets" in st.secrets and "gemini_api_key" in st.secrets["sheets"]:
            return st.secrets["sheets"]["gemini_api_key"]
        if "gemini" in st.secrets and "api_key" in st.secrets["gemini"]:
            return st.secrets["gemini"]["api_key"]
    except Exception:
        pass
    return None


GEMINI_API_KEY = get_gemini_api_key()
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    st.warning("Gemini API key não encontrada em st.secrets.")

# ==============================================================
# 3) CONSTANTES – TRANSPOSIÇÃO
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

_TONE_BASES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
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
# 4) GEMINI – TRANSCRIÇÃO DE IMAGEM
# ==============================================================

def transcribe_image_with_gemini(uploaded_file, model_name="models/gemini-2.5-flash"):
    api_key = get_gemini_api_key()
    if not api_key:
        st.error("Gemini API key não configurada.")
        return ""

    try:
        model = genai.GenerativeModel(model_name)

        prompt = """
Você está transcrevendo uma cifra (acordes + letra) a partir de uma imagem.

REGRAS DE FORMATAÇÃO (IMPORTANTES):
1) Toda linha que contiver apenas ACORDES deve começar com o caractere '|'.
2) Toda linha de LETRA deve começar com um ESPAÇO em branco.
3) Mantenha o alinhamento visual dos acordes exatamente acima das sílabas da letra.
4) Ignore diagramas de braço de instrumento; foque apenas em texto e acordes.
5) NÃO use markdown, NÃO use ``` e nem cabeçalhos; apenas texto puro.
        """.strip()

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
# 5) GITHUB API – LEITURA / ESCRITA
# ==============================================================

def github_headers():
    if not GITHUB_TOKEN:
        return {}
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def repo_api_url(path: str) -> str:
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"


def raw_url(path: str) -> str:
    # branch -> main
    return f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{path}"


def slugify_filename(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"[^\w\- ]+", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "_", s)
    s = s.strip("_")
    return s or "sem_nome"


def github_get_file(path: str):
    """Retorna dict do GitHub contents API (inclui sha)."""
    r = requests.get(repo_api_url(path), headers=github_headers(), params={"ref": GITHUB_BRANCH})
    if r.status_code == 200:
        return r.json()
    return None


def github_put_file(path: str, content_bytes: bytes, message: str):
    if not GITHUB_TOKEN:
        st.error("Falta github.token em st.secrets para salvar no GitHub.")
        return False

    existing = github_get_file(path)
    sha = existing.get("sha") if existing else None

    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(repo_api_url(path), headers=github_headers(), data=json.dumps(payload))
    if r.status_code in (200, 201):
        return True

    st.error(f"Falha ao salvar no GitHub ({r.status_code}): {r.text[:300]}")
    return False


def github_list_dir(path: str):
    r = requests.get(repo_api_url(path), headers=github_headers(), params={"ref": GITHUB_BRANCH})
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, list):
            return data
    return []

# ==============================================================
# 6) “DRIVE” DE CIFRAS -> AGORA NO GITHUB (TXT)
# ==============================================================

@st.cache_data(ttl=120)
def load_chord_from_repo(chord_path: str) -> str:
    if not chord_path:
        return ""
    try:
        r = requests.get(raw_url(chord_path))
        if r.status_code == 200:
            return r.text
        return f"Erro ao carregar cifra: {r.status_code}"
    except Exception as e:
        return f"Erro ao carregar cifra: {e!r}"


def save_chord_to_repo(chord_path: str, content: str):
    if not chord_path:
        return False
    ok = github_put_file(
        chord_path,
        content.encode("utf-8"),
        message=f"Update chord {chord_path} ({datetime.utcnow().isoformat()}Z)"
    )
    if ok:
        load_chord_from_repo.clear()
    return ok


def create_chord_in_repo(filename_base: str, content: str) -> str:
    """
    Cria um TXT em Data/Chords e retorna o PATH do arquivo no repo.
    """
    if not content.strip():
        return ""

    fname = slugify_filename(filename_base) + ".txt"
    chord_path = f"{CHORDS_DIR}/{fname}"

    ok = github_put_file(
        chord_path,
        content.encode("utf-8"),
        message=f"Add chord {chord_path} ({datetime.utcnow().isoformat()}Z)"
    )
    if ok:
        load_chord_from_repo.clear()
        return chord_path
    return ""

# ==============================================================
# 7) BANCO DE MÚSICAS (CSV NO GITHUB)
# ==============================================================

EXPECTED_SONG_COLS = ["Título", "Artista", "Tom_Original", "BPM", "CifraPath", "CifraSimplificadaPath"]

@st.cache_data(ttl=120)
def load_songs_df():
    try:
        r = requests.get(SONGS_CSV_RAW_URL)
        if r.status_code != 200:
            st.error(f"Não consegui carregar o CSV (HTTP {r.status_code}).")
            return pd.DataFrame(columns=EXPECTED_SONG_COLS)

        # tenta ler com pandas
        df = pd.read_csv(io.StringIO(r.text))
        # normaliza colunas
        for col in EXPECTED_SONG_COLS:
            if col not in df.columns:
                df[col] = ""

        # limpa strings
        for c in ["Título", "Artista", "Tom_Original", "BPM", "CifraPath", "CifraSimplificadaPath"]:
            df[c] = df[c].astype(str).fillna("").str.strip()

        # remove linhas sem título
        df = df[df["Título"] != ""].copy()
        return df.reset_index(drop=True)

    except Exception as e:
        st.error(f"Erro ao carregar músicas do CSV: {e!r}")
        return pd.DataFrame(columns=EXPECTED_SONG_COLS)


def save_songs_df_to_github(df: pd.DataFrame):
    # garante colunas
    for col in EXPECTED_SONG_COLS:
        if col not in df.columns:
            df[col] = ""

    csv_bytes = df[EXPECTED_SONG_COLS].to_csv(index=False).encode("utf-8")

    ok = github_put_file(
        SONGS_CSV_PATH,
        csv_bytes,
        message=f"Update songs CSV {SONGS_CSV_PATH} ({datetime.utcnow().isoformat()}Z)"
    )
    if ok:
        load_songs_df.clear()
    return ok


def append_song_to_bank(title, artist, tom_original, bpm, cifra_path, cifra_simplificada_path):
    df = load_songs_df()
    new_row = {
        "Título": (title or "").strip(),
        "Artista": (artist or "").strip(),
        "Tom_Original": (tom_original or "").strip(),
        "BPM": (str(bpm) if bpm is not None else "").strip(),
        "CifraPath": (cifra_path or "").strip(),
        "CifraSimplificadaPath": (cifra_simplificada_path or "").strip(),
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    return save_songs_df_to_github(df)

# ==============================================================
# 8) SETLISTS (CSV POR ARQUIVO NO GITHUB)
# ==============================================================

SETLIST_COLS = [
    "BlockIndex","BlockName","ItemIndex","ItemType",
    "SongTitle","Artist","Tom","BPM",
    "CifraPath","CifraSimplificadaPath","UseSimplificada","PauseLabel",
]

def setlist_filename(name: str) -> str:
    safe = slugify_filename(name)
    return f"{SETLISTS_DIR}/{safe}.csv"


def list_setlist_names():
    items = github_list_dir(SETLISTS_DIR)
    names = []
    for it in items:
        if it.get("type") == "file" and it.get("name", "").lower().endswith(".csv"):
            # volta para nome "human"
            names.append(it["name"][:-4].replace("_", " "))
    names.sort()
    return names


@st.cache_data(ttl=120)
def load_setlist_df(setlist_name: str) -> pd.DataFrame:
    path = setlist_filename(setlist_name)
    try:
        r = requests.get(raw_url(path))
        if r.status_code != 200:
            return pd.DataFrame(columns=SETLIST_COLS)
        df = pd.read_csv(io.StringIO(r.text))
        for col in SETLIST_COLS:
            if col not in df.columns:
                df[col] = ""
        return df[SETLIST_COLS]
    except Exception:
        return pd.DataFrame(columns=SETLIST_COLS)


def write_setlist_df(setlist_name: str, df: pd.DataFrame):
    for col in SETLIST_COLS:
        if col not in df.columns:
            df[col] = ""
    df = df[SETLIST_COLS].copy()

    path = setlist_filename(setlist_name)
    ok = github_put_file(
        path,
        df.to_csv(index=False).encode("utf-8"),
        message=f"Update setlist {path} ({datetime.utcnow().isoformat()}Z)"
    )
    if ok:
        load_setlist_df.clear()
    return ok


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
                "ItemType": item.get("type",""),
                "SongTitle": "",
                "Artist": "",
                "Tom": "",
                "BPM": "",
                "CifraPath": "",
                "CifraSimplificadaPath": "",
                "UseSimplificada": "",
                "PauseLabel": "",
            }
            if item.get("type") == "music":
                base["SongTitle"] = item.get("title","")
                base["Artist"] = item.get("artist","")
                base["Tom"] = item.get("tom","")
                base["BPM"] = item.get("bpm","")
                base["CifraPath"] = item.get("cifra_path","")
                base["CifraSimplificadaPath"] = item.get("cifra_simplificada_path","")
                base["UseSimplificada"] = "1" if item.get("use_simplificada", False) else "0"
            else:
                base["PauseLabel"] = item.get("label","Pausa")

            rows.append(base)

    df_new = pd.DataFrame(rows, columns=SETLIST_COLS)
    return write_setlist_df(name, df_new)


def load_setlist_into_state(setlist_name: str, songs_df: pd.DataFrame):
    df_sel = load_setlist_df(setlist_name)
    if df_sel.empty:
        return

    df_sel["BlockIndex"] = pd.to_numeric(df_sel["BlockIndex"], errors="coerce").fillna(0).astype(int)
    df_sel["ItemIndex"] = pd.to_numeric(df_sel["ItemIndex"], errors="coerce").fillna(0).astype(int)
    df_sel = df_sel.sort_values(["BlockIndex","ItemIndex"])

    blocks = []
    for (block_idx, block_name), group in df_sel.groupby(["BlockIndex","BlockName"], sort=True):
        items = []
        for _, row in group.iterrows():
            if row.get("ItemType","") == "pause":
                items.append({"type":"pause","label": row.get("PauseLabel","Pausa")})
            else:
                title = str(row.get("SongTitle","")).strip()
                artist = str(row.get("Artist","")).strip()
                tom_saved = str(row.get("Tom","")).strip()
                bpm_saved = str(row.get("BPM","")).strip()

                cifra_path_saved = str(row.get("CifraPath","")).strip()
                cifra_simpl_saved = str(row.get("CifraSimplificadaPath","")).strip()
                use_simpl = str(row.get("UseSimplificada","0")).strip().lower() in ("1","true","y","yes")

                # tenta puxar do banco (pra preencher paths se faltarem)
                song_row = songs_df[songs_df["Título"] == title]
                if not song_row.empty:
                    sr = song_row.iloc[0]
                    tom_original = sr.get("Tom_Original","") or tom_saved
                    cifra_path_bank = sr.get("CifraPath","")
                    cifra_simpl_bank = sr.get("CifraSimplificadaPath","")

                    cifra_path = cifra_path_saved or cifra_path_bank
                    cifra_simpl = cifra_simpl_saved or cifra_simpl_bank
                else:
                    tom_original = tom_saved
                    cifra_path = cifra_path_saved
                    cifra_simpl = cifra_simpl_saved

                items.append({
                    "type":"music",
                    "title": title,
                    "artist": artist,
                    "tom_original": tom_original,
                    "tom": tom_saved or tom_original,
                    "bpm": bpm_saved,
                    "cifra_path": cifra_path,
                    "cifra_simplificada_path": cifra_simpl,
                    "use_simplificada": use_simpl,
                    "text": "",
                })

        blocks.append({"name": block_name or f"Bloco {len(blocks)+1}", "items": items})

    st.session_state.blocks = blocks
    st.session_state.setlist_name = setlist_name
    st.session_state.current_item = None
    st.session_state.selected_block_idx = None
    st.session_state.selected_item_idx = None
    st.session_state.screen = "editor"

# ==============================================================
# 9) ESTADO INICIAL
# ==============================================================

def init_state():
    if "songs_df" not in st.session_state:
        st.session_state.songs_df = load_songs_df()

    if "blocks" not in st.session_state:
        st.session_state.blocks = [{"name":"Bloco 1", "items": []}]

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
# 10) AUX – ORDEM / REMOÇÃO
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
# 11) HTML – PREVIEW
# ==============================================================

def get_footer_context(blocks, cur_block_idx, cur_item_idx):
    items = blocks[cur_block_idx]["items"]

    if cur_item_idx + 1 < len(items):
        nxt = items[cur_item_idx + 1]
        if nxt["type"] == "pause":
            return "next_pause", nxt
        return "next_music", nxt

    for b in range(cur_block_idx + 1, len(blocks)):
        if blocks[b]["items"]:
            return "end_block", None

    return "none", None


def build_sheet_header_html(title, artist, tom, bpm):
    tom_display = tom if tom else "- / -"
    bpm_display = bpm if bpm not in (None, "", 0, "0", "None") else "BPM"

    return f"""
    <div class="sheet-header">
        <div class="sheet-header-col sheet-header-main">
            <div class="sheet-title">{(title or "NOVA MÚSICA").upper()}</div>
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
    bpm_text = str(next_bpm) if next_bpm not in (None, "", 0, "0", "None") else "-"

    return f"""
    <div class="sheet-footer sheet-footer-grid">
        <div class="sheet-next-label">PRÓXIMA:</div>

        <div class="sheet-next-header-row">
            <div class="sheet-next-title">{(next_title or "").upper()}</div>
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
        cifra_path = item.get("cifra_path", "")
        cifra_simpl_path = item.get("cifra_simplificada_path", "")

        if use_simplificada and cifra_simpl_path:
            raw_body = load_chord_from_repo(cifra_simpl_path)
        elif cifra_path:
            raw_body = load_chord_from_repo(cifra_path)
        else:
            raw_body = item.get("text", "CIFRA / TEXTO AQUI (ainda não cadastrada).")

        tom_atual = tom

    if item["type"] == "pause":
        body_final = raw_body
    else:
        body_transposed = transpose_body_text(raw_body, tom_original, tom_atual)
        body_norm = normalize_lyrics_indent(body_transposed)
        body_final = strip_chord_markers_for_display(body_norm)

    header_html = build_sheet_header_html(title, artist, tom, bpm)

    if footer_mode == "next_music" and footer_next_item is not None:
        footer_html = build_footer_next_music(
            footer_next_item.get("title",""),
            footer_next_item.get("artist",""),
            footer_next_item.get("tom",""),
            footer_next_item.get("bpm",""),
        )
    elif footer_mode == "next_pause" and footer_next_item is not None:
        footer_html = build_footer_next_pause(footer_next_item.get("label","Pausa"))
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

        .sheet-next-pause {{
            font-size: 12px;
            font-weight: 700;
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
# 12) EDITOR EM ÁRVORE
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

        cifra_path = item.get("cifra_path", "")
        cifra_simpl_path = item.get("cifra_simplificada_path", "")

        with st.expander("Ver / editar cifra (texto)", expanded=True):
            if item.get("use_simplificada") and cifra_simpl_path:
                current_path = cifra_simpl_path
            elif cifra_path:
                current_path = cifra_path
            else:
                current_path = None

            if current_path:
                cifra_text = load_chord_from_repo(current_path)
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
                if current_path:
                    ok = save_chord_to_repo(current_path, edited)
                    if ok:
                        st.success("Cifra atualizada no GitHub.")
                    else:
                        st.error("Falha ao salvar cifra no GitHub.")
                else:
                    item["text"] = edited
                    st.success("Cifra salva apenas no setlist (sem arquivo).")
                st.rerun()

        bpm_val = item.get("bpm", "")
        tom_original = item.get("tom_original", "") or item.get("tom", "")
        tom_val = item.get("tom", tom_original)

        lab_bpm, lab_tom = st.columns(2)
        lab_bpm.markdown("<p style='text-align:center;font-size:0.8rem;'>BPM</p>", unsafe_allow_html=True)
        lab_tom.markdown("<p style='text-align:center;font-size:0.8rem;'>Tom</p>", unsafe_allow_html=True)

        col_bpm, col_tom = st.columns(2)

        new_bpm = col_bpm.text_input(
            "BPM",
            value=str(bpm_val) if bpm_val not in ("", None, 0, "0", "None") else "",
            key=f"bpm_sel_{b_idx}_{i_idx}",
            label_visibility="collapsed",
            placeholder="BPM",
        )
        item["bpm"] = new_bpm

        if str(tom_original).endswith("m"):
            tone_list = [t for t in TONE_OPTIONS if t.endswith("m")]
        else:
            tone_list = [t for t in TONE_OPTIONS if not t.endswith("m")]

        if tom_val not in tone_list and tom_val:
            tone_list = [tom_val] + tone_list

        idx_tone = tone_list.index(tom_val) if tom_val in tone_list else 0
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

    else:
        st.markdown("**⏸ Pausa**")
        new_label = st.text_input(
            "Descrição da pausa",
            value=item.get("label", "Pausa"),
            key=f"pause_label_{b_idx}_{i_idx}",
        )
        item["label"] = new_label


def render_setlist_editor_tree():
    blocks = st.session_state.blocks
    songs_df = st.session_state.songs_df

    st.markdown("### Estrutura da Setlist (modo árvore)")

    if st.button("+ Adicionar bloco", use_container_width=True, key="btn_add_block_global"):
        st.session_state.blocks.append({"name": f"Bloco {len(blocks) + 1}", "items": []})
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

            for i, item in enumerate(block["items"]):
                col_label, col_btns = st.columns([8, 2])

                if item["type"] == "music":
                    title = item.get("title", "Nova música")
                    artist = item.get("artist", "")
                    label = f"🎵 {title}" + (f" — {artist}" if artist else "")
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

            # ==========================
            # MULTISELECT (CORRIGIDO)
            # ==========================
            if st.session_state.get(f"show_add_music_block_{b_idx}", False):
                st.markdown("##### Adicionar músicas deste bloco")

                df_opt = songs_df.copy()
                for col in EXPECTED_SONG_COLS:
                    if col not in df_opt.columns:
                        df_opt[col] = ""
                df_opt["Título"] = df_opt["Título"].astype(str).fillna("").str.strip()
                df_opt["Artista"] = df_opt["Artista"].astype(str).fillna("").str.strip()
                df_opt["Tom_Original"] = df_opt["Tom_Original"].astype(str).fillna("").str.strip()
                df_opt["BPM"] = df_opt["BPM"].astype(str).fillna("").str.strip()

                df_opt = df_opt[df_opt["Título"] != ""].copy()
                df_opt["_key"] = df_opt.index.astype(str)

                def _label_for_key(k: str) -> str:
                    try:
                        idx = int(k)
                        r = df_opt.loc[idx]
                    except Exception:
                        r = df_opt.iloc[0]
                    title = r.get("Título","")
                    artist = r.get("Artista","")
                    tom = r.get("Tom_Original","")
                    bpm = r.get("BPM","")
                    extra = []
                    if tom: extra.append(tom)
                    if bpm and bpm.lower() != "none": extra.append(f"{bpm} BPM")
                    extra_txt = f" ({' / '.join(extra)})" if extra else ""
                    return f"{title} — {artist}{extra_txt}" if artist else f"{title}{extra_txt}"

                selected_keys = st.multiselect(
                    "Escolha as músicas do banco",
                    options=df_opt["_key"].tolist(),
                    format_func=_label_for_key,
                    key=f"mus_select_blk_{b_idx}",
                )

                if st.button("Adicionar selecionadas", key=f"confirm_add_mus_blk_{b_idx}"):
                    for k in selected_keys:
                        idx = int(k)
                        row = df_opt.loc[idx]

                        new_item = {
                            "type": "music",
                            "title": row.get("Título",""),
                            "artist": row.get("Artista",""),
                            "tom_original": row.get("Tom_Original",""),
                            "tom": row.get("Tom_Original",""),
                            "bpm": row.get("BPM",""),
                            "cifra_path": row.get("CifraPath",""),
                            "cifra_simplificada_path": row.get("CifraSimplificadaPath",""),
                            "use_simplificada": False,
                            "text": "",
                        }
                        block["items"].append(new_item)

                    st.session_state[f"show_add_music_block_{b_idx}"] = False
                    st.rerun()

    render_selected_item_editor()

# ==============================================================
# 13) BANCO DE MÚSICAS – CRIAÇÃO (GEMINI + TXT NO GITHUB)
# ==============================================================

def render_song_database():
    st.subheader("Banco de músicas (GitHub CSV)")

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
            st.caption("Use esse botão apenas se tiver subido uma imagem. O resultado aparece abaixo para editar.")

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
            st.caption("Opcional. Se não usar, deixe em branco.")

        st.session_state.new_song_cifra_simplificada = st.text_area(
            "Texto da cifra SIMPLIFICADA",
            value=st.session_state.new_song_cifra_simplificada,
            height=240,
            key="txt_cifra_simplificada",
        )

        st.markdown("---")
        st.markdown("#### 3) Salvar no banco (GitHub TXT + CSV)")

        if st.button("Salvar nova música no banco", key="btn_save_new_song"):
            if not (title or "").strip():
                st.warning("Preencha pelo menos o título.")
            else:
                with st.spinner("Criando arquivos TXT e atualizando o CSV no GitHub..."):
                    content_orig = st.session_state.new_song_cifra_original or ""
                    content_simpl = st.session_state.new_song_cifra_simplificada or ""

                    cifra_path = ""
                    cifra_simpl_path = ""

                    if content_orig.strip():
                        nome_arquivo_orig = f"{title} - {artist} (Original)"
                        cifra_path = create_chord_in_repo(nome_arquivo_orig, content_orig)

                    if content_simpl.strip():
                        nome_arquivo_simpl = f"{title} - {artist} (Simplificada)"
                        cifra_simpl_path = create_chord_in_repo(nome_arquivo_simpl, content_simpl)

                    ok = append_song_to_bank(
                        title=title,
                        artist=artist,
                        tom_original=tom_original,
                        bpm=bpm,
                        cifra_path=cifra_path,
                        cifra_simplificada_path=cifra_simpl_path,
                    )

                    if ok:
                        st.session_state.new_song_cifra_original = ""
                        st.session_state.new_song_cifra_simplificada = ""
                        st.session_state.songs_df = load_songs_df()
                        st.success(f"Música '{title}' cadastrada com sucesso!")
                        st.rerun()
                    else:
                        st.error("Falha ao salvar no GitHub (ver token/permissions).")

# ==============================================================
# 14) HOME
# ==============================================================

def render_home():
    st.title("PDL Setlist")

    setlists = list_setlist_names()

    col_new, col_load = st.columns(2)

    with col_new:
        st.subheader("Nova setlist")
        default_name = st.session_state.get("setlist_name", "Pagode do LEC")
        new_name = st.text_input("Nome da nova setlist", value=default_name, key="new_setlist_name")
        if st.button("Criar setlist"):
            st.session_state.setlist_name = (new_name or "").strip() or "Setlist sem nome"
            st.session_state.blocks = [{"name":"Bloco 1", "items": []}]
            st.session_state.current_item = None
            st.session_state.selected_block_idx = None
            st.session_state.selected_item_idx = None
            st.session_state.screen = "editor"
            st.rerun()

    with col_load:
        st.subheader("Carregar setlist existente (GitHub)")
        if setlists:
            selected = st.selectbox("Escolha a setlist", options=setlists, key="load_setlist_select")
            if st.button("Carregar esta setlist"):
                load_setlist_into_state(selected, st.session_state.songs_df)
                st.rerun()
        else:
            st.info("Nenhuma setlist encontrada em Data/Setlists.")

# ==============================================================
# 15) MAIN
# ==============================================================

def main():
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
        if st.button("🏠 Voltar à tela inicial", use_container_width=True):
            st.session_state.screen = "home"
            st.rerun()
        if st.button("💾 Salvar setlist (GitHub CSV)", use_container_width=True):
            ok = save_current_setlist_to_github()
            if ok:
                st.success("Setlist salva no GitHub.")
            else:
                st.error("Falha ao salvar setlist no GitHub (token/permissões).")

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
            footer_mode, footer_next_item = get_footer_context(blocks, cur_block_idx, cur_item_idx)
            html = build_sheet_page_html(current_item, footer_mode, footer_next_item, current_block_name)
            st.components.v1.html(html, height=1200, scrolling=True)


if __name__ == "__main__":
    main()        raise ValueError("Diretório não retornou lista.")
    return data


def github_put_file(path: str, content_bytes: bytes, commit_message: str, sha: str | None = None):
    """Cria/atualiza arquivo no GitHub (requer token com Contents write)."""
    if not GITHUB_TOKEN:
        raise PermissionError("Sem github.token no secrets.")

    url = github_api_url(path)
    payload = {
        "message": commit_message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(url, headers=github_headers(), json=payload, timeout=45)
    r.raise_for_status()
    return r.json()


def slugify_filename(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "setlist"
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^a-zA-Z0-9\-_ ]+", "", name).strip().replace(" ", "_")
    name = re.sub(r"_+", "_", name)
    return name[:80] or "setlist"


def ensure_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]


def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def csv_bytes_to_df(b: bytes) -> pd.DataFrame:
    text = b.decode("utf-8", errors="replace")
    return pd.read_csv(io.StringIO(text))


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
# 5) BANCO (CSV NO GITHUB) – MÚSICAS + SETLISTS
# ==============================================================

MUSIC_COLS = [
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


@st.cache_data(ttl=120)
def load_songs_df():
    """
    Lê o banco de músicas do GitHub CSV.
    Se falhar (arquivo não existe), retorna vazio com colunas padrão.
    """
    try:
        b, _sha = github_get_file(MUSIC_CSV_PATH)
        df = csv_bytes_to_df(b)
        df = ensure_columns(df, MUSIC_COLS)
        return df
    except Exception:
        return pd.DataFrame(columns=MUSIC_COLS)


def save_songs_df_to_github(df: pd.DataFrame):
    """
    Salva o banco de músicas no GitHub (commit).
    """
    df = ensure_columns(df, MUSIC_COLS)
    csv_bytes = df_to_csv_bytes(df)

    sha = None
    try:
        _b, sha = github_get_file(MUSIC_CSV_PATH)
    except Exception:
        sha = None

    msg = "Atualiza banco de músicas (PDL_musicas.csv)"
    github_put_file(MUSIC_CSV_PATH, csv_bytes, commit_message=msg, sha=sha)
    load_songs_df.clear()


def append_song_to_bank(
    title: str,
    artist: str,
    tom_original: str,
    bpm,
    cifra_id: str,
    cifra_simplificada_id: str,
):
    df = load_songs_df()
    new_row = {
        "Título": title,
        "Artista": artist,
        "Tom_Original": tom_original,
        "BPM": bpm or "",
        "CifraDriveID": cifra_id or "",
        "CifraSimplificadaID": cifra_simplificada_id or "",
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    save_songs_df_to_github(df)


@st.cache_data(ttl=120)
def list_setlist_names():
    """
    Lista setlists como arquivos CSV dentro de Data/setlists.
    Retorna lista de nomes “humanos” (sem extensão).
    """
    try:
        items = github_list_dir(SETLISTS_DIR)
        files = []
        for it in items:
            if it.get("type") == "file" and str(it.get("name", "")).lower().endswith(".csv"):
                files.append(it.get("name"))
        # remove extensão
        return [f[:-4] for f in sorted(files)]
    except Exception:
        return []


def setlist_path_from_name(setlist_name: str) -> str:
    # usa slug para nome de arquivo; mas exibe o nome original na UI
    filename = slugify_filename(setlist_name) + ".csv"
    return f"{SETLISTS_DIR.strip('/')}/{filename}"


@st.cache_data(ttl=120)
def load_setlist_df(setlist_name: str) -> pd.DataFrame:
    path = setlist_path_from_name(setlist_name)
    try:
        b, _sha = github_get_file(path)
        df = csv_bytes_to_df(b)
        df = ensure_columns(df, SETLIST_COLS)
        return df
    except Exception:
        return pd.DataFrame(columns=SETLIST_COLS)


def write_setlist_df(setlist_name: str, df: pd.DataFrame):
    """
    Salva a setlist no GitHub em Data/setlists/<slug>.csv
    """
    df = ensure_columns(df, SETLIST_COLS)
    csv_bytes = df_to_csv_bytes(df)
    path = setlist_path_from_name(setlist_name)

    sha = None
    try:
        _b, sha = github_get_file(path)
    except Exception:
        sha = None

    msg = f"Atualiza setlist: {setlist_name}"
    github_put_file(path, csv_bytes, commit_message=msg, sha=sha)
    list_setlist_names.clear()
    load_setlist_df.clear()


def save_current_setlist_to_sheet():
    """
    Mantive o nome da função para não mexer no resto do app,
    mas agora ela salva no GitHub (CSV).
    """
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
                base["UseSimplificada"] = "1" if item.get("use_simplificada", False) else "0"
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
    for (block_idx, block_name), group in df_sel.groupby(["BlockIndex", "BlockName"], sort=True):
        items = []
        for _, row in group.iterrows():
            if row["ItemType"] == "pause":
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

                song_row = songs_df[songs_df["Título"] == title]
                if not song_row.empty:
                    song_row = song_row.iloc[0]
                    tom_original = song_row.get("Tom_Original", "") or tom_saved
                    cifra_id_bank = str(song_row.get("CifraDriveID", "")).strip()
                    cifra_simplificada_bank = str(song_row.get("CifraSimplificadaID", "")).strip()

                    cifra_id = cifra_id_saved or cifra_id_bank
                    cifra_simplificada_id = cifra_simplificada_saved or cifra_simplificada_bank
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

        blocks.append({"name": block_name or f"Bloco {len(blocks) + 1}", "items": items})

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

    if "selected_block_idx" not in st.session_state:
        st.session_state.selected_block_idx = None
    if "selected_item_idx" not in st.session_state:
        st.session_state.selected_item_idx = None

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
            raw_body = item.get("text", "CIFRA / TEXTO AQUI (ainda não cadastrada).")

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
        lab_bpm.markdown("<p style='text-align:center;font-size:0.8rem;'>BPM</p>", unsafe_allow_html=True)
        lab_tom.markdown("<p style='text-align:center;font-size:0.8rem;'>Tom</p>", unsafe_allow_html=True)

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

        idx_tone = tone_list.index(tom_val) if tom_val in tone_list else 0

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

    else:
        st.markdown("**⏸ Pausa**")
        new_label = st.text_input(
            "Descrição da pausa",
            value=item.get("label", "Pausa"),
            key=f"pause_label_{b_idx}_{i_idx}",
        )
        item["label"] = new_label


def render_setlist_editor_tree():
    blocks = st.session_state.blocks
    songs_df = st.session_state.songs_df

    st.markdown("### Estrutura da Setlist (modo árvore)")

    if st.button("+ Adicionar bloco", use_container_width=True, key="btn_add_block_global"):
        st.session_state.blocks.append({"name": f"Bloco {len(blocks) + 1}", "items": []})
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

            for i, item in enumerate(block["items"]):
                col_label, col_btns = st.columns([8, 2])

                if item["type"] == "music":
                    title = item.get("title", "Nova música")
                    artist = item.get("artist", "")
                    label = f"🎵 {title}" + (f" – {artist}" if artist else "")
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
                st.markdown("##### Adicionar músicas deste bloco")

# 1) Garante que DF tem as colunas esperadas (caso CSV venha diferente)
for col in ["Título", "Artista", "Tom_Original", "BPM", "CifraDriveID", "CifraSimplificadaID"]:
    if col not in songs_df.columns:
        songs_df[col] = ""

# 2) Normaliza strings e cria uma "key" única por linha
df_opt = songs_df.copy()

df_opt["Título"] = df_opt["Título"].astype(str).fillna("").str.strip()
df_opt["Artista"] = df_opt["Artista"].astype(str).fillna("").str.strip()
df_opt["Tom_Original"] = df_opt["Tom_Original"].astype(str).fillna("").str.strip()
df_opt["BPM"] = df_opt["BPM"].astype(str).fillna("").str.strip()

# remove linhas totalmente sem título (isso é o que vira "opção em branco")
df_opt = df_opt[df_opt["Título"] != ""].copy()

# chave única (string) -> evita problema com índices não-serializáveis
df_opt["_key"] = df_opt.index.astype(str)

# label que aparece no dropdown
def _label_for_key(k: str) -> str:
    r = df_opt.loc[int(k)] if k.isdigit() else df_opt.iloc[0]
    title = r.get("Título", "")
    artist = r.get("Artista", "")
    tom = r.get("Tom_Original", "")
    bpm = r.get("BPM", "")
    extra = []
    if tom: extra.append(tom)
    if bpm and bpm.lower() != "none": extra.append(f"{bpm} BPM")
    extra_txt = f" ({' / '.join(extra)})" if extra else ""
    if artist:
        return f"{title} — {artist}{extra_txt}"
    return f"{title}{extra_txt}"

# 3) multiselect usando keys + format_func para exibir label
selected_keys = st.multiselect(
    "Escolha as músicas do banco",
    options=df_opt["_key"].tolist(),
    format_func=_label_for_key,
    key=f"mus_select_blk_{b_idx}",
)

if st.button("Adicionar selecionadas", key=f"confirm_add_mus_blk_{b_idx}"):
    for k in selected_keys:
        row = df_opt.loc[int(k)]

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

    render_selected_item_editor()


# ==============================================================
# 10) BANCO DE MÚSICAS – COM TELA DE CRIAÇÃO / GEMINI
# ==============================================================

def render_song_database():
    st.subheader("Banco de músicas (CSV no GitHub)")

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
            st.caption("Use esse botão apenas se tiver subido uma imagem. O resultado aparece abaixo para editar.")

        st.session_state.new_song_cifra_original = st.text_area(
            "Texto da cifra ORIGINAL",
            value=st.session_state.new_song_cifra_original,
            height=240,
            key="txt_cifra_original",
        )

        st.markdown("---")

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
            st.caption("Também opcional. Se não usar, deixe em branco.")

        st.session_state.new_song_cifra_simplificada = st.text_area(
            "Texto da cifra SIMPLIFICADA",
            value=st.session_state.new_song_cifra_simplificada,
            height=240,
            key="txt_cifra_simplificada",
        )

        st.markdown("---")
        st.markdown("#### 3) Salvar no banco (Drive + GitHub CSV)")

        if st.button("Salvar nova música no banco", key="btn_save_new_song"):
            if not title.strip():
                st.warning("Preencha pelo menos o título.")
            elif not GITHUB_TOKEN:
                st.error("Para salvar no GitHub, configure [github].token no secrets.")
            else:
                with st.spinner("Criando arquivos no Drive e salvando no GitHub..."):
                    content_orig = st.session_state.new_song_cifra_original or ""
                    content_simpl = st.session_state.new_song_cifra_simplificada or ""

                    final_cifra_id = ""
                    final_simpl_id = ""

                    if content_orig.strip():
                        nome_arquivo_orig = f"{title} - {artist} (Original)"
                        final_cifra_id = create_chord_in_drive(nome_arquivo_orig, content_orig) or ""

                    if content_simpl.strip():
                        nome_arquivo_simpl = f"{title} - {artist} (Simplificada)"
                        final_simpl_id = create_chord_in_drive(nome_arquivo_simpl, content_simpl) or ""

                    append_song_to_bank(
                        title.strip(),
                        artist.strip(),
                        tom_original.strip(),
                        bpm,
                        final_cifra_id,
                        final_simpl_id,
                    )

                    st.session_state.new_song_cifra_original = ""
                    st.session_state.new_song_cifra_simplificada = ""

                    st.success(f"Música '{title}' cadastrada com sucesso no GitHub ✅")
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
            "Nome da nova setlist (salva como arquivo CSV)",
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
        st.subheader("Carregar setlist existente (GitHub)")
        if setlists:
            selected = st.selectbox(
                "Escolha a setlist",
                options=setlists,
                key="load_setlist_select",
            )
            if st.button("Carregar esta setlist"):
                load_setlist_into_state(selected, st.session_state.songs_df)
                st.rerun()
        else:
            st.info("Nenhuma setlist encontrada em Data/setlists/.")


# ==============================================================
# 12) MAIN
# ==============================================================

def main():
    st.set_page_config(page_title="PDL Setlist", layout="wide", page_icon="🎵")

    with st.sidebar:
        st.caption("Status GitHub")
        st.write("Repo:", f"{GITHUB_OWNER}/{GITHUB_REPO}@{GITHUB_BRANCH}")
        st.write("Banco:", MUSIC_CSV_PATH)
        st.write("Setlists dir:", SETLISTS_DIR)
        st.write("Token:", "✅ OK" if GITHUB_TOKEN else "❌ faltando (não salva)")
        if st.button("🔄 Recarregar banco (limpar cache)", use_container_width=True):
            load_songs_df.clear()
            list_setlist_names.clear()
            load_setlist_df.clear()
            st.rerun()

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
        if st.button("🏠 Voltar à tela inicial", use_container_width=True):
            st.session_state.screen = "home"
            st.rerun()
        if st.button("💾 Salvar setlist (GitHub CSV)", use_container_width=True):
            if not GITHUB_TOKEN:
                st.error("Para salvar setlist no GitHub, configure [github].token no secrets.")
            else:
                save_current_setlist_to_sheet()
                st.success("Setlist salva no GitHub ✅")

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
            footer_mode, footer_next_item = get_footer_context(blocks, cur_block_idx, cur_item_idx)

            html = build_sheet_page_html(
                current_item,
                footer_mode,
                footer_next_item,
                current_block_name,
            )
            st.components.v1.html(html, height=1200, scrolling=True)


if __name__ == "__main__":
    main()
