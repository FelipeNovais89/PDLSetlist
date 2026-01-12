import streamlit as st
from dataclasses import dataclass, field
from typing import List, Optional

# --------------------------------------------------------------------
# "BANCO DE DADOS" SIMPLES DE MÚSICAS (EDITÁVEL)
# --------------------------------------------------------------------

SONG_DB = [
    {"titulo": "Deixa Acontecer", "bpm": 100, "tom": "Fm"},
    {"titulo": "Telegrama", "bpm": 95, "tom": "C"},
    {"titulo": "Eva", "bpm": 104, "tom": "Ab"},
]

MAJOR_KEYS = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MINOR_KEYS = [k + "m" for k in MAJOR_KEYS]


# --------------------------------------------------------------------
# MODELOS DE DADOS
# --------------------------------------------------------------------


@dataclass
class Item:
    id: int
    tipo: str  # "musica" ou "pausa"
    titulo: str = ""
    bpm: Optional[int] = None
    tom: str = ""
    notas: str = ""


@dataclass
class Bloco:
    id: int
    nome: str
    itens: List[Item] = field(default_factory=list)


# --------------------------------------------------------------------
# ESTADO INICIAL
# --------------------------------------------------------------------


def init_state():
    if "blocos" not in st.session_state:
        bloco_inicial = Bloco(id=1, nome="Bloco 1", itens=[])
        st.session_state.blocos: List[Bloco] = [bloco_inicial]
        st.session_state.next_bloco_id = 2
        st.session_state.next_item_id = 1
        st.session_state.current_page_index = 0
        st.session_state.preview_mode = "Preview"
        st.session_state.fullscreen = False

    if "layout_mode" not in st.session_state:
        st.session_state.layout_mode = "Lado a lado"  # ou "Empilhado"


# --------------------------------------------------------------------
# FUNÇÕES AUXILIARES
# --------------------------------------------------------------------


def get_song_from_db(title: str):
    for song in SONG_DB:
        if song["titulo"] == title:
            return song
    return None


def add_bloco():
    nb = Bloco(
        id=st.session_state.next_bloco_id,
        nome=f"Bloco {st.session_state.next_bloco_id}",
        itens=[],
    )
    st.session_state.next_bloco_id += 1
    st.session_state.blocos.append(nb)


def delete_bloco(bloco_id: int):
    st.session_state.blocos = [b for b in st.session_state.blocos if b.id != bloco_id]


def move_bloco(bloco_id: int, direction: int):
    blocos = st.session_state.blocos
    idx = next((i for i, b in enumerate(blocos) if b.id == bloco_id), None)
    if idx is None:
        return
    new_idx = idx + direction
    if 0 <= new_idx < len(blocos):
        blocos[idx], blocos[new_idx] = blocos[new_idx], blocos[idx]


def add_item(bloco_id: int, tipo: str):
    for b in st.session_state.blocos:
        if b.id == bloco_id:
            item = Item(
                id=st.session_state.next_item_id,
                tipo=tipo,
                titulo="Nova música" if tipo == "musica" else "PAUSA",
                bpm=None,
                tom="",
            )
            st.session_state.next_item_id += 1
            b.itens.append(item)
            break


def delete_item(bloco_id: int, item_id: int):
    for b in st.session_state.blocos:
        if b.id == bloco_id:
            b.itens = [it for it in b.itens if it.id != item_id]
            break


def move_item(bloco_id: int, item_id: int, direction: int):
    for b in st.session_state.blocos:
        if b.id == bloco_id:
            idx = next((i for i, it in enumerate(b.itens) if it.id == item_id), None)
            if idx is None:
                return
            new_idx = idx + direction
            if 0 <= new_idx < len(b.itens):
                b.itens[idx], b.itens[new_idx] = b.itens[new_idx], b.itens[idx]
            break


def transpose_key(key: str, semitones: int) -> str:
    """Sobe/desce meio tom (semitones pode ser -1 ou +1)."""
    if not key:
        key = "C"
    is_minor = key.endswith("m")
    base = key[:-1] if is_minor else key
    keys = MAJOR_KEYS
    try:
        idx = keys.index(base)
    except ValueError:
        idx = 0
    new_idx = (idx + semitones) % len(keys)
    new_base = keys[new_idx]
    return new_base + "m" if is_minor else new_base


# --------------------------------------------------------------------
# PÁGINAS DO PREVIEW
# --------------------------------------------------------------------


def build_pages():
    pages = []
    index_by_block = []

    page_idx = 0
    for bloco in st.session_state.blocos:
        pages_indices = []
        for item in bloco.itens:
            pages.append({"bloco": bloco, "item": item, "page_idx": page_idx})
            pages_indices.append(page_idx)
            page_idx += 1
        index_by_block.append((bloco, pages_indices))

    return pages, index_by_block


# --------------------------------------------------------------------
# PREVIEW - UMA PÁGINA
# --------------------------------------------------------------------


def render_page(page):
    if page is None:
        st.markdown(
            "<div style='padding:2rem; text-align:center; color:#888;'>"
            "Nenhuma música na setlist ainda."
            "</div>",
            unsafe_allow_html=True,
        )
        return

    bloco: Bloco = page["bloco"]
    item: Item = page["item"]

    st.markdown(
        """
        <style>
        .sheet {
            background-color: #ffffff;
            color: #000000;
            padding: 24px 32px;
            border-radius: 4px;
            box-shadow: 0 0 12px rgba(0,0,0,0.35);
            font-family: "Courier New", monospace;
        }
        .sheet-header {
            text-align: center;
            border-bottom: 1px solid #999;
            padding-bottom: 8px;
            margin-bottom: 12px;
            font-family: "Arial", sans-serif;
        }
        .sheet-title {
            font-size: 18px;
            font-weight: bold;
        }
        .sheet-band {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .sheet-tombpm-caption {
            font-size: 9px;
            margin-top: 6px;
        }
        .sheet-tombpm-values {
            font-size: 11px;
            font-weight: bold;
        }
        .sheet-body {
            font-size: 11px;
            white-space: pre-wrap;
            margin-top: 8px;
            min-height: 300px;
        }
        .sheet-footer {
            border-top: 1px solid #999;
            margin-top: 16px;
            padding-top: 8px;
            font-size: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-family: "Arial", sans-serif;
        }
        .sheet-next {
            font-weight: bold;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    musica_atual = item.titulo or ("PAUSA" if item.tipo == "pausa" else "MÚSICA SEM NOME")
    banda_atual = bloco.nome

    tom_atual = item.tom or "-"
    bpm_atual = item.bpm if item.bpm is not None else "-"

    pages, _ = build_pages()
    current_idx = page["page_idx"]
    if current_idx + 1 < len(pages):
        prox_item: Item = pages[current_idx + 1]["item"]
        prox_nome = prox_item.titulo or ("PAUSA" if prox_item.tipo == "pausa" else "Música")
        prox_tom = prox_item.tom or "-"
        prox_bpm = prox_item.bpm if prox_item.bpm is not None else "-"
    else:
        prox_nome = "Fim do setlist"
        prox_tom = "-"
        prox_bpm = "-"

    cifra_texto = item.notas or "CIFRA / TEXTO AQUI (ainda não cadastrada)."

    html = f"""
    <div class="sheet">
      <div class="sheet-header">
        <div class="sheet-title">{musica_atual.upper()}</div>
        <div class="sheet-band">{banda_atual.upper()}</div>
        <div class="sheet-tombpm-caption">TOM / BPM</div>
        <div class="sheet-tombpm-values">{tom_atual} / {bpm_atual}</div>
      </div>

      <div class="sheet-body">
{cifra_texto}
      </div>

      <div class="sheet-footer">
        <div class="sheet-next">PRÓXIMA: {prox_nome.upper()}</div>
        <div style="text-align:right;">
          <div class="sheet-tombpm-caption">TOM / BPM</div>
          <div class="sheet-tombpm-values">{prox_tom} / {prox_bpm}</div>
        </div>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


# --------------------------------------------------------------------
# EDITOR (LADO ESQUERDO)
# --------------------------------------------------------------------


def render_editor():
    st.subheader("Editor de Setlist")

    st.button(
        "➕ Adicionar bloco",
        use_container_width=True,
        on_click=add_bloco,
    )

    for bloco in st.session_state.blocos:
        with st.container(border=True):
            col1, col2, col3, col4 = st.columns([6, 1, 1, 1])
            with col1:
                novo_nome = st.text_input(
                    f"Nome do bloco {bloco.id}",
                    value=bloco.nome,
                    key=f"bloco_nome_{bloco.id}",
                    label_visibility="collapsed",
                )
                bloco.nome = novo_nome or bloco.nome

            # espaçamento maior para centralizar melhor
            with col2:
                st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
                if st.button("↑", key=f"bloco_up_{bloco.id}", help="Mover bloco para cima"):
                    move_bloco(bloco.id, -1)
                    st.rerun()
            with col3:
                st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
                if st.button("↓", key=f"bloco_down_{bloco.id}", help="Mover bloco para baixo"):
                    move_bloco(bloco.id, +1)
                    st.rerun()
            with col4:
                st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
                if st.button("✖", key=f"bloco_del_{bloco.id}", help="Excluir bloco"):
                    delete_bloco(bloco.id)
                    st.rerun()

            st.markdown("---")

            for it in bloco.itens:
                with st.container():
                    c0, c1, c2, c3, c4, c5, c6 = st.columns([2, 4, 2, 3, 1, 1, 1])

                    picker_flag = f"song_picker_open_{it.id}"
                    tone_flag = f"tone_picker_open_{it.id}"

                    if it.tipo == "musica":
                        # botão ESCOLHER
                        with c0:
                            st.markdown("<div style='height:3px'></div>", unsafe_allow_html=True)
                            if st.button(
                                "Escolher",
                                key=f"pick_song_{it.id}",
                                help="Selecionar músicas do banco",
                            ):
                                st.session_state[picker_flag] = not st.session_state.get(
                                    picker_flag, False
                                )
                                st.rerun()

                        # nome da música
                        with c1:
                            nome = it.titulo or "(sem música)"
                            st.markdown(f"<b>{nome}</b>", unsafe_allow_html=True)

                        # BPM
                        with c2:
                            bpm_val = st.number_input(
                                "BPM",
                                value=it.bpm if it.bpm is not None else 0,
                                key=f"item_bpm_{it.id}",
                                label_visibility="collapsed",
                                step=1,
                            )
                            it.bpm = int(bpm_val) if bpm_val > 0 else None

                        # TOM: -½ | [Tom] | +½
                        with c3:
                            base_tom = it.tom or "C"
                            c_t1, c_t2, c_t3 = st.columns([1, 1, 1])
                            with c_t1:
                                st.markdown("<div style='height:3px'></div>", unsafe_allow_html=True)
                                if st.button("− ½", key=f"tone_down_{it.id}", help="Descer ½ tom"):
                                    it.tom = transpose_key(it.tom or base_tom, -1)
                                    st.rerun()
                            with c_t2:
                                st.markdown("<div style='height:3px'></div>", unsafe_allow_html=True)
                                label_tom = it.tom or base_tom
                                if st.button(label_tom, key=f"tone_pick_{it.id}", help="Escolher tom"):
                                    st.session_state[tone_flag] = not st.session_state.get(
                                        tone_flag, False
                                    )
                                    st.rerun()
                            with c_t3:
                                st.markdown("<div style='height:3px'></div>", unsafe_allow_html=True)
                                if st.button("+ ½", key=f"tone_up_{it.id}", help="Subir ½ tom"):
                                    it.tom = transpose_key(it.tom or base_tom, +1)
                                    st.rerun()

                    else:  # PAUSA
                        with c0:
                            st.markdown("<div style='height:3px'></div>", unsafe_allow_html=True)
                            st.markdown("Pausa")
                        with c1:
                            novo_titulo = st.text_input(
                                f"Pausa {it.id}",
                                value=it.titulo,
                                key=f"item_titulo_{it.id}",
                                label_visibility="collapsed",
                            )
                            it.titulo = novo_titulo
                        with c2:
                            st.markdown(
                                "<div style='font-size:11px;color:#aaa;'>Pausa</div>",
                                unsafe_allow_html=True,
                            )
                        with c3:
                            st.markdown("")

                    # botões mover/excluir, mais centralizados
                    with c4:
                        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
                        if st.button("↑", key=f"item_up_{it.id}", help="Mover para cima"):
                            move_item(bloco.id, it.id, -1)
                            st.rerun()
                    with c5:
                        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
                        if st.button("↓", key=f"item_down_{it.id}", help="Mover para baixo"):
                            move_item(bloco.id, it.id, +1)
                            st.rerun()
                    with c6:
                        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
                        if st.button("✖", key=f"item_del_{it.id}", help="Excluir item"):
                            delete_item(bloco.id, it.id)
                            st.rerun()

                    # painel de seleção de músicas (checkbox múltiplo)
                    if st.session_state.get(picker_flag, False) and it.tipo == "musica":
                        with st.container():
                            st.markdown("**Selecionar músicas:**")
                            selecionadas = []
                            for song in SONG_DB:
                                key_cb = f"song_cb_{it.id}_{song['titulo']}"
                                checked = st.checkbox(song["titulo"], key=key_cb)
                                if checked:
                                    selecionadas.append(song)

                            b1, b2 = st.columns(2)
                            with b1:
                                if st.button("Confirmar", key=f"song_confirm_{it.id}"):
                                    if selecionadas:
                                        # primeira música vai para este item
                                        first = selecionadas[0]
                                        it.titulo = first["titulo"]
                                        it.bpm = first["bpm"]
                                        it.tom = first["tom"]

                                        # demais são adicionadas logo abaixo
                                        for extra in selecionadas[1:]:
                                            new_item = Item(
                                                id=st.session_state.next_item_id,
                                                tipo="musica",
                                                titulo=extra["titulo"],
                                                bpm=extra["bpm"],
                                                tom=extra["tom"],
                                            )
                                            st.session_state.next_item_id += 1
                                            # insere depois deste item
                                            for b in st.session_state.blocos:
                                                if b.id == bloco.id:
                                                    idx = next(
                                                        i
                                                        for i, it2 in enumerate(b.itens)
                                                        if it2.id == it.id
                                                    )
                                                    b.itens.insert(idx + 1, new_item)
                                                    idx += 1
                                                    break
                                    st.session_state[picker_flag] = False
                                    st.rerun()
                            with b2:
                                if st.button("Cancelar", key=f"song_cancel_{it.id}"):
                                    st.session_state[picker_flag] = False
                                    st.rerun()

                    # painel de seleção de tom (lista de tons)
                    if st.session_state.get(tone_flag, False) and it.tipo == "musica":
                        with st.container():
                            st.markdown("**Selecionar tom:**")
                            base_tom = it.tom or "C"
                            is_minor = base_tom.endswith("m")
                            keys = MINOR_KEYS if is_minor else MAJOR_KEYS
                            idx_tom = keys.index(base_tom) if base_tom in keys else 0
                            escolha_tom = st.radio(
                                "Tom",
                                keys,
                                index=idx_tom,
                                key=f"tone_radio_{it.id}",
                            )
                            b1, b2 = st.columns(2)
                            with b1:
                                if st.button("Confirmar tom", key=f"tone_confirm_{it.id}"):
                                    it.tom = escolha_tom
                                    st.session_state[tone_flag] = False
                                    st.rerun()
                            with b2:
                                if st.button("Cancelar tom", key=f"tone_cancel_{it.id}"):
                                    st.session_state[tone_flag] = False
                                    st.rerun()

            # botões adicionar
            c_add1, c_add2 = st.columns(2)
            with c_add1:
                if st.button("＋ Música", key=f"add_musica_{bloco.id}", use_container_width=True):
                    add_item(bloco.id, "musica")
                    st.rerun()
            with c_add2:
                if st.button("＋ Pausa", key=f"add_pausa_{bloco.id}", use_container_width=True):
                    add_item(bloco.id, "pausa")
                    st.rerun()


# --------------------------------------------------------------------
# PREVIEW (LADO DIREITO)
# --------------------------------------------------------------------


def render_preview(fullscreen=False):
    pages, index_by_block = build_pages()

    if pages:
        if st.session_state.current_page_index >= len(pages):
            st.session_state.current_page_index = len(pages) - 1
        current_page = pages[st.session_state.current_page_index]
    else:
        current_page = None

    if not fullscreen:
        st.subheader("Preview")

    if not fullscreen:
        c1, c2 = st.columns([1, 1])
        with c1:
            st.markdown(
                "<div style='font-size:12px;color:#aaa;'>Preview ▼</div>",
                unsafe_allow_html=True,
            )
        with c2:
            if st.button("🗖 Tela cheia", use_container_width=True):
                st.session_state.fullscreen = True
                st.rerun()

    render_page(current_page)

    if not fullscreen:
        st.markdown("")
        st.markdown(
            "<div style='margin-top:12px;font-size:11px;color:#aaa;'>"
            "Navegação por blocos e páginas:"
            "</div>",
            unsafe_allow_html=True,
        )
        for bloco, page_indices in index_by_block:
            if not page_indices:
                continue
            st.markdown(f"<b>[{bloco.nome}]</b>", unsafe_allow_html=True)
            cols = st.columns(len(page_indices))
            for col, p_idx in zip(cols, page_indices):
                page = pages[p_idx]
                item = page["item"]
                label = item.titulo or ("PAUSA" if item.tipo == "pausa" else f"Página {p_idx+1}")
                short = label if len(label) <= 16 else label[:13] + "..."
                with col:
                    if st.button(short, key=f"goto_{p_idx}"):
                        st.session_state.current_page_index = p_idx
                        st.rerun()


# --------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------


def exit_fullscreen():
    st.session_state.fullscreen = False


def main():
    st.set_page_config(page_title="PDL Setlist", layout="wide")
    init_state()

    # seletor de layout (lado a lado / empilhado)
    layout = st.radio(
        "Layout",
        ["Lado a lado", "Empilhado"],
        index=0 if st.session_state.layout_mode == "Lado a lado" else 1,
        horizontal=True,
    )
    st.session_state.layout_mode = layout

    if st.session_state.fullscreen:
        st.button("⬅ Voltar", on_click=exit_fullscreen, key="back_full")
        render_preview(fullscreen=True)
    else:
        st.markdown(
            "<div style='font-size:14px; margin-bottom:8px;'>"
            "<b>Setlist:</b> Pagode do LEC - Lisboa 2026"
            "</div>",
            unsafe_allow_html=True,
        )

        if st.session_state.layout_mode == "Lado a lado":
            col_left, col_right = st.columns([1.1, 1.4])
            with col_left:
                render_editor()
            with col_right:
                render_preview(fullscreen=False)
        else:
            # Empilhado: editor em cima, preview embaixo
            render_editor()
            st.markdown("---")
            render_preview(fullscreen=False)


if __name__ == "__main__":
    main()
