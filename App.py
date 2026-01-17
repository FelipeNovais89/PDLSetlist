import streamlit as st
import pandas as pd
import io

import gspread
from google.oauth2.service_account import Credentials

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.errors import HttpError


# --------------------------------------------------------------------
# 0. TONS E FUNÇÃO DE TRANSPOSIÇÃO
# --------------------------------------------------------------------
# Escalas cromáticas com sustenidos e bemóis
SHARP_SCALE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_SCALE =  ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

# Lista geral de tons possíveis (maiores + menores)
TONE_OPTIONS = SHARP_SCALE + [t + "m" for t in SHARP_SCALE] + \
               FLAT_SCALE + [t + "m" for t in FLAT_SCALE]


def transpose_key_by_semitones(key: str, semitones: int) -> str:
    """
    Transpõe um tom (ex.: 'C', 'Fm', 'Bb') em n semitons.
    Mantém se é maior/menor e tenta respeitar se a nota usa bemol ou sustenido.
    """
    if not key:
        return key

    key = key.strip()
    is_minor = key.endswith("m")
    base = key[:-1] if is_minor else key

    # decide se usa escala com bemóis ou sustenidos
    use_flats = ("b" in base) and ("#" not in base)
    scale = FLAT_SCALE if use_flats else SHARP_SCALE

    try:
        idx = scale.index(base)
    except ValueError:
        # se não achar, tenta na outra escala
        alt_scale = SHARP_SCALE if use_flats else FLAT_SCALE
        try:
            idx = alt_scale.index(base)
            scale = alt_scale
        except ValueError:
            # fallback pra C
            idx = 0
            scale = SHARP_SCALE

    new_idx = (idx + semitones) % 12
    new_base = scale[new_idx]
    return new_base + ("m" if is_minor else "")


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

    # Garante que as colunas existem mesmo se a planilha ainda não tiver todas
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
# 5. LÓGICA DO RODAPÉ (PRÓXIMA / PAUSA / FIM DE BLOCO)
# --------------------------------------------------------------------
def get_footer_context(blocks, cur_block_idx, cur_item_idx):
    """
    Decide o que mostrar no rodapé da página atual.

    Retorna (mode, next_item):

    - "next_music"  -> próxima música no MESMO bloco
    - "next_pause"  -> próxima é pausa no MESMO bloco
    - "end_block"   -> não tem próxima no bloco, mas existem blocos depois
    - "none"        -> acabou tudo (última música do último bloco)
    """
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

    # HTML completo
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
# 7. INTERFACE – EDITOR DE BLOCOS
# --------------------------------------------------------------------
def render_block_editor(block, block_idx, songs_df):
    st.markdown(f"### Bloco {block_idx + 1}")

    # CSS para deixar botões um pouco menores (inclui −½ e +½)
    st.markdown(
        """
        <style>
        button[kind="secondary"] {
            font-size: 0.8rem !important;
            padding-top: 0.15rem !important;
            padding-bottom: 0.15rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

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
            # Linha com título / artista + botões laterais
            c_title, c_up, c_down, c_del, c_prev = st.columns([6, 1, 1, 1, 1])

            if item["type"] == "music":
                title = item.get("title", "Nova música")
                artist = item.get("artist", "")
                label = f"🎵 {title}"
                if artist:
                    label += f" – {artist}"
                c_title.markdown(label)

                if c_up.button("↑", key=f"item_up_{block_idx}_{i}"):
                    move_item(block_idx, i, -1)
                    st.rerun()
                if c_down.button("↓", key=f"item_down_{block_idx}_{i}"):
                    move_item(block_idx, i, 1)
                    st.rerun()
                if c_del.button("✕", key=f"item_del_{block_idx}_{i}"):
                    delete_item(block_idx, i)
                    st.rerun()
                if c_prev.button("Preview", key=f"preview_{block_idx}_{i}"):
                    st.session_state.current_item = (block_idx, i)
                    st.rerun()

                # ---- Expander de cifra (ver / editar) ----
                cifra_id = item.get("cifra_id", "")
                with c_title.expander("Ver cifra"):
                    if cifra_id:
                        cifra_text = load_chord_from_drive(cifra_id)
                    else:
                        cifra_text = item.get("text", "")

                    edit_key = f"cifra_edit_{block_idx}_{i}"
                    edited = st.text_area(
                        "Cifra",
                        value=cifra_text,
                        height=300,
                        key=edit_key,
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

                # ---- Linha BPM / TOM ----
                bpm_val = item.get("bpm", "")
                tom_original = item.get("tom_original", "") or item.get("tom", "")
                tom_val = item.get("tom", tom_original)

                # labels centralizados e menores
                lab_bpm, _, lab_tom, _ = st.columns([1.5, 0.7, 1.4, 0.7])
                lab_bpm.markdown(
                    "<p style='text-align:center;font-size:0.8rem;'>BPM</p>",
                    unsafe_allow_html=True,
                )
                lab_tom.markdown(
                    "<p style='text-align:center;font-size:0.8rem;'>Tom</p>",
                    unsafe_allow_html=True,
                )

                col_bpm, col_minus, col_tom, col_plus = st.columns([1.5, 0.7, 1.4, 0.7])

                # BPM
                new_bpm = col_bpm.text_input(
                    "BPM",
                    value=str(bpm_val) if bpm_val not in ("", None, 0) else "",
                    key=f"bpm_{block_idx}_{i}",
                    label_visibility="collapsed",
                    placeholder="BPM",
                )
                item["bpm"] = new_bpm

                # define lista de tons: só maiores ou só menores, conforme Tom_Original
                if tom_original.endswith("m"):
                    tone_list = [t for t in TONE_OPTIONS if t.endswith("m")]
                else:
                    tone_list = [t for t in TONE_OPTIONS if not t.endswith("m")]

                if tom_val and tom_val not in tone_list:
                    tone_list = [tom_val] + tone_list

                # chave do selectbox de tom
                tom_widget_key = f"tom_select_{block_idx}_{i}"

                # garante que o valor do widget segue o tom atual do item
                if tom_val and st.session_state.get(tom_widget_key) != tom_val:
                    st.session_state[tom_widget_key] = tom_val

                # botão -½
                if col_minus.button("−½", key=f"tom_minus_{block_idx}_{i}"):
                    base_key = st.session_state.get(
                        tom_widget_key, tom_val or tom_original or "C"
                    )
                    new_tone = transpose_key_by_semitones(base_key, -1)
                    if new_tone in tone_list:
                        item["tom"] = new_tone
                        st.session_state[tom_widget_key] = new_tone
                    st.rerun()

                # selectbox de tom
                selected_tone = col_tom.selectbox(
                    "Tom",
                    options=tone_list,
                    key=tom_widget_key,
                    label_visibility="collapsed",
                )
                if selected_tone != item.get("tom", tom_original):
                    item["tom"] = selected_tone
                    st.rerun()

                # botão +½
                if col_plus.button("+½", key=f"tom_plus_{block_idx}_{i}"):
                    base_key = st.session_state.get(
                        tom_widget_key, tom_val or tom_original or "C"
                    )
                    new_tone = transpose_key_by_semitones(base_key, +1)
                    if new_tone in tone_list:
                        item["tom"] = new_tone
                        st.session_state[tom_widget_key] = new_tone
                    st.rerun()

            else:
                # item de pausa
                label = f"⏸ PAUSA – {item.get('label','')}"
                c_title.markdown(label)

                if c_up.button("↑", key=f"item_up_{block_idx}_{i}"):
                    move_item(block_idx, i, -1)
                    st.rerun()
                if c_down.button("↓", key=f"item_down_{block_idx}_{i}"):
                    move_item(block_idx, i, 1)
                    st.rerun()
                if c_del.button("✕", key=f"item_del_{block_idx}_{i}"):
                    delete_item(block_idx, i)
                    st.rerun()
                if c_prev.button("Preview", key=f"preview_{block_idx}_{i}"):
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

    # Seleção de músicas do banco
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

    # Coluna esquerda – editor
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

    # Coluna direita – preview
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


if __name__ == "__main__":
    main()
