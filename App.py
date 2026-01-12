import streamlit as st
from dataclasses import dataclass, field
from typing import List, Optional

# --------------------------------------------------------------------
# MODELOS DE DADOS EM MEMÓRIA
# --------------------------------------------------------------------


@dataclass
class Item:
    """Item dentro de um bloco (música ou pausa)."""
    id: int
    tipo: str  # "musica" ou "pausa"
    titulo: str = ""
    bpm: Optional[int] = None
    tom: str = ""
    notas: str = ""  # no futuro: observações / cifra simplificada


@dataclass
class Bloco:
    """Bloco de músicas."""
    id: int
    nome: str
    itens: List[Item] = field(default_factory=list)


# --------------------------------------------------------------------
# INICIALIZAÇÃO DO ESTADO
# --------------------------------------------------------------------


def init_state():
    if "blocos" not in st.session_state:
        bloco_inicial = Bloco(id=1, nome="Bloco 1", itens=[])
        st.session_state.blocos: List[Bloco] = [bloco_inicial]
        st.session_state.next_bloco_id = 2
        st.session_state.next_item_id = 1
        st.session_state.current_page_index = 0  # índice da página no preview
        st.session_state.preview_mode = "Preview"
        st.session_state.fullscreen = False


# --------------------------------------------------------------------
# FUNÇÕES AUXILIARES PARA MANIPULAR LISTA
# --------------------------------------------------------------------


def add_bloco():
    nb = Bloco(
        id=st.session_state.next_bloco_id,
        nome=f"Bloco {st.session_state.next_bloco_id}",
        itens=[],
    )
    st.session_state.next_bloco_id += 1
    st.session_state.blocos.append(nb)


def delete_bloco(bloco_id: int):
    blocos = st.session_state.blocos
    st.session_state.blocos = [b for b in blocos if b.id != bloco_id]


def move_bloco(bloco_id: int, direction: int):
    """direction: -1 para cima, +1 para baixo"""
    blocos = st.session_state.blocos
    idx = next((i for i, b in enumerate(blocos) if b.id == bloco_id), None)
    if idx is None:
        return
    new_idx = idx + direction
    if 0 <= new_idx < len(blocos):
        blocos[idx], blocos[new_idx] = blocos[new_idx], blocos[idx]


def add_item(bloco_id: int, tipo: str):
    """Adiciona música ou pausa a um bloco."""
    blocos = st.session_state.blocos
    for b in blocos:
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
    """Move item para cima/baixo dentro do bloco."""
    for b in st.session_state.blocos:
        if b.id == bloco_id:
            idx = next((i for i, it in enumerate(b.itens) if it.id == item_id), None)
            if idx is None:
                return
            new_idx = idx + direction
            if 0 <= new_idx < len(b.itens):
                b.itens[idx], b.itens[new_idx] = b.itens[new_idx], b.itens[idx]
            break


# --------------------------------------------------------------------
# CONSTRUÇÃO DAS PÁGINAS PARA O PREVIEW
# --------------------------------------------------------------------


def build_pages():
    """
    Constrói uma lista linear de 'páginas' para o preview.
    Cada música e cada pausa conta como uma página.
    Também devolve uma estrutura com blocos -> índices de página,
    para montar a barra inferior.
    """
    pages = []
    index_by_block = []  # lista de (nome_bloco, [indices_de_pages])

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
# RENDER DO PREVIEW (UMA PÁGINA)
# --------------------------------------------------------------------


def render_page(page):
    """Renderiza a página atual (música ou pausa) no preview."""
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

    # Caixa branca simulando folha
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

    # Dados fictícios de banda / próxima música por enquanto
    musica_atual = item.titulo or ("PAUSA" if item.tipo == "pausa" else "Música sem nome")
    banda_atual = bloco.nome  # por enquanto usamos o nome do bloco como "banda"

    tom_atual = item.tom or "-"
    bpm_atual = item.bpm if item.bpm is not None else "-"

    # Próxima página (para rodapé)
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

    cifra_texto = item.notas or "CIFRA / TEXTO AQUI (ainda não cadastrado)."

    # HTML
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
# UI: EDITOR DE SETLIST (LADO ESQUERDO)
# --------------------------------------------------------------------


def render_editor():
    st.subheader("Editor de Setlist")

    st.button(
        "➕ Adicionar bloco",
        use_container_width=True,
        on_click=add_bloco,
    )

    for idx, bloco in enumerate(st.session_state.blocos):
        with st.container(border=True):
            # Cabeçalho do bloco
            col1, col2, col3, col4 = st.columns([6, 1, 1, 1])
            with col1:
                novo_nome = st.text_input(
                    f"Nome do bloco {bloco.id}",
                    value=bloco.nome,
                    key=f"bloco_nome_{bloco.id}",
                    label_visibility="collapsed",
                )
                bloco.nome = novo_nome or bloco.nome

            with col2:
                if st.button("↑", key=f"bloco_up_{bloco.id}", help="Mover bloco para cima"):
                    move_bloco(bloco.id, -1)
                    st.experimental_rerun()
            with col3:
                if st.button("↓", key=f"bloco_down_{bloco.id}", help="Mover bloco para baixo"):
                    move_bloco(bloco.id, +1)
                    st.experimental_rerun()
            with col4:
                if st.button("✖", key=f"bloco_del_{bloco.id}", help="Excluir bloco"):
                    delete_bloco(bloco.id)
                    st.experimental_rerun()

            st.markdown("---")

            # Itens (músicas / pausas)
            for it in bloco.itens:
                with st.container():
                    c1, c2, c3, c4, c5, c6 = st.columns([4, 2, 2, 1, 1, 1])
                    with c1:
                        label = "Música" if it.tipo == "musica" else "Pausa"
                        novo_titulo = st.text_input(
                            f"{label} {it.id}",
                            value=it.titulo,
                            key=f"item_titulo_{it.id}",
                            label_visibility="collapsed",
                        )
                        it.titulo = novo_titulo

                    with c2:
                        if it.tipo == "musica":
                            bpm_val = st.number_input(
                                "BPM",
                                value=it.bpm if it.bpm is not None else 0,
                                key=f"item_bpm_{it.id}",
                                label_visibility="collapsed",
                                step=1,
                            )
                            it.bpm = int(bpm_val) if bpm_val > 0 else None
                        else:
                            st.markdown("<div style='font-size:11px;color:#aaa;'>Pausa</div>", unsafe_allow_html=True)

                    with c3:
                        if it.tipo == "musica":
                            tom_val = st.text_input(
                                "Tom",
                                value=it.tom,
                                key=f"item_tom_{it.id}",
                                label_visibility="collapsed",
                                placeholder="Ex: Dm, F#, Bb",
                            )
                            it.tom = tom_val
                        else:
                            st.markdown("")

                    with c4:
                        if st.button("↑", key=f"item_up_{it.id}", help="Mover para cima"):
                            move_item(bloco.id, it.id, -1)
                            st.experimental_rerun()
                    with c5:
                        if st.button("↓", key=f"item_down_{it.id}", help="Mover para baixo"):
                            move_item(bloco.id, it.id, +1)
                            st.experimental_rerun()
                    with c6:
                        if st.button("✖", key=f"item_del_{it.id}", help="Excluir item"):
                            delete_item(bloco.id, it.id)
                            st.experimental_rerun()

            # Botões para adicionar música/pausa
            c_add1, c_add2, _ = st.columns([2, 2, 6])
            with c_add1:
                if st.button("＋ Música", key=f"add_musica_{bloco.id}"):
                    add_item(bloco.id, "musica")
                    st.experimental_rerun()
            with c_add2:
                if st.button("＋ Pausa", key=f"add_pausa_{bloco.id}"):
                    add_item(bloco.id, "pausa")
                    st.experimental_rerun()


# --------------------------------------------------------------------
# UI: PREVIEW (LADO DIREITO)
# --------------------------------------------------------------------


def render_preview(fullscreen=False):
    pages, index_by_block = build_pages()

    if pages:
        # Garante que o índice atual é válido
        if st.session_state.current_page_index >= len(pages):
            st.session_state.current_page_index = 0
        current_page = pages[st.session_state.current_page_index]
    else:
        current_page = None

    if not fullscreen:
        st.subheader("Preview")

    # Botão Tela Cheia (apenas fora do modo fullscreen)
    if not fullscreen:
        c1, c2 = st.columns([1, 1])
        with c1:
            mode_label = "Preview ▼"
            st.markdown(f"<div style='font-size:12px;color:#aaa;'>{mode_label}</div>", unsafe_allow_html=True)
        with c2:
            if st.button("🗖 Tela cheia", use_container_width=True):
                st.session_state.fullscreen = True
                st.experimental_rerun()

    # Render da página
    render_page(current_page)

    if not fullscreen:
        st.markdown("")

        # Barra inferior com blocos e páginas
        st.markdown(
            "<div style='margin-top:12px;font-size:11px;color:#aaa;'>Navegação por blocos e páginas:</div>",
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
                        st.experimental_rerun()


# --------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------


def main():
    st.set_page_config(page_title="PDL Setlist", layout="wide")
    init_state()

    if st.session_state.fullscreen:
        # Modo de tela cheia: só o preview
        st.button("⬅ Voltar", on_click=lambda: exit_fullscreen(), key="back_full")
        render_preview(fullscreen=True)
    else:
        # Topo
        st.markdown(
            "<div style='font-size:14px; margin-bottom:8px;'>"
            "<b>Setlist:</b> Pagode do LEC - Lisboa 2026"
            "</div>",
            unsafe_allow_html=True,
        )

        col_left, col_right = st.columns([1.1, 1.4])

        with col_left:
            render_editor()

        with col_right:
            render_preview(fullscreen=False)


def exit_fullscreen():
    st.session_state.fullscreen = False


if __name__ == "__main__":
    main()
