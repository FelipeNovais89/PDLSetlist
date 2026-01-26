import streamlit as st
import google.generativeai as genai

# -------------------------------------------------------------
# 1. PEGAR A CHAVE DO GEMINI EM st.secrets
# -------------------------------------------------------------
def get_gemini_api_key():
    """
    Procura pela chave em:
    - st.secrets["gemini_api_key"]
    - st.secrets["sheets"]["gemini_api_key"]
    """
    try:
        if "gemini_api_key" in st.secrets:
            return st.secrets["gemini_api_key"]
        if "sheets" in st.secrets and "gemini_api_key" in st.secrets["sheets"]:
            return st.secrets["sheets"]["gemini_api_key"]
    except Exception:
        pass
    return None


API_KEY = get_gemini_api_key()

if API_KEY:
    genai.configure(api_key=API_KEY)
else:
    st.error(
        "⚠️ Gemini API key não encontrada em st.secrets.\n\n"
        "No arquivo `.streamlit/secrets.toml`, coloque por exemplo:\n\n"
        "gemini_api_key = \"SUA_CHAVE_AQUI\""
    )
    st.stop()


# -------------------------------------------------------------
# 2. CARREGAR LISTA DE MODELOS DISPONÍVEIS
# -------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def get_models_with_generate_content():
    all_models = list(genai.list_models())
    usable = []
    for m in all_models:
        # alguns SDKs usam "generateContent", outros "generateText"
        methods = getattr(m, "supported_generation_methods", []) or []
        if "generateContent" in methods or "generateText" in methods:
            usable.append(m)
    return usable


# -------------------------------------------------------------
# 3. FUNÇÃO DE TRANSCRIÇÃO (usa o modelo escolhido)
# -------------------------------------------------------------
def transcribe_image_with_gemini(uploaded_file, model_name: str):
    file_bytes = uploaded_file.getvalue()
    mime = uploaded_file.type or "image/jpeg"

    st.subheader("1️⃣ Arquivo recebido")
    st.write("Nome:", uploaded_file.name)
    st.write("MIME:", mime)
    st.write("Tamanho (bytes):", len(file_bytes))

    try:
        st.subheader("2️⃣ Chamando o Gemini")
        st.write("Modelo selecionado:", model_name)

        model = genai.GenerativeModel(model_name)

        prompt = """
        Você é especialista em transcrever cifras de cavaquinho/violão.

        REGRAS DE FORMATAÇÃO:
        1. Toda linha que contiver apenas ACORDES deve começar com o caractere '|'.
        2. Toda linha de LETRA deve começar com um ESPAÇO em branco.
        3. Mantenha o alinhamento dos acordes exatamente acima das sílabas da letra.
        4. Ignore diagramas de braço de instrumento, foque no texto e cifras.
        5. NÃO use markdown, NÃO use ``` nem cabeçalhos; apenas texto puro.
        6. Retorne SOMENTE o texto da cifra, sem explicações adicionais.
        """

        response = model.generate_content(
            [
                prompt,
                {"mime_type": mime, "data": file_bytes},
            ]
        )

        raw_text = (getattr(response, "text", "") or "").strip()

        st.subheader("3️⃣ Resposta BRUTA do modelo")
        if raw_text:
            st.code(raw_text[:2000], language="text")
        else:
            st.info("Resposta vazia (string em branco).")

        # --- Limpeza de markdown / bloco de código ---
        cleaned = raw_text
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if "\n" in cleaned:
                cleaned = "\n".join(cleaned.split("\n")[1:]).strip()

        st.subheader("4️⃣ Texto LIMPO (para usar como cifra)")
        if cleaned:
            st.code(cleaned, language="text")
        else:
            st.info("Nada após limpeza – talvez o modelo tenha retornado só markdown vazio.")

        return raw_text, cleaned

    except Exception as e:
        st.error(f"Erro ao chamar Gemini com o modelo '{model_name}': {e}")
        return "", ""


# -------------------------------------------------------------
# 4. APP STREAMLIT
# -------------------------------------------------------------
def main():
    st.set_page_config(
        page_title="Gemini – Teste de Cifra por Imagem",
        page_icon="🎵",
        layout="centered",
    )

    st.title("Teste de Transcrição de Cifra com Gemini (Imagem ➜ Texto)")

    # ---- Lista de modelos disponíveis ----
    st.markdown("### 🔍 Modelos disponíveis com `generateContent` / `generateText`")

    try:
        models = get_models_with_generate_content()
    except Exception as e:
        st.error(f"Erro ao listar modelos: {e}")
        return

    if not models:
        st.error("Nenhum modelo com generateContent/generateText disponível para essa API key.")
        return

    # mostrar em tabela/expander
    with st.expander("Ver lista completa de modelos"):
        for m in models:
            methods = getattr(m, "supported_generation_methods", []) or []
            st.write(f"- **{m.name}** — métodos: {methods}")

    # opções de selectbox: usar exatamente m.name
    model_names = [m.name for m in models]

    st.markdown("### 🎯 Escolha o modelo para testar")
    selected_model = st.selectbox(
        "Modelo",
        options=model_names,
        index=0,
        help="Só modelos que suportam generateContent/generateText.",
    )

    st.markdown("---")
    st.markdown(
        """
        **Passos:**
        1. Faça upload de uma **imagem da cifra** (JPG/PNG).
        2. Clique em **Transcrever cifra**.
        3. Veja a resposta bruta, o texto limpo e edite o resultado.
        """
    )

    uploaded_file = st.file_uploader(
        "Envie uma imagem da cifra (JPG ou PNG)",
        type=["jpg", "jpeg", "png"],
    )

    if uploaded_file is not None:
        if st.button("Transcrever cifra", type="primary"):
            raw, cleaned = transcribe_image_with_gemini(
                uploaded_file, selected_model
            )

            st.subheader("5️⃣ Área de edição / cópia da cifra")
            final_text = st.text_area(
                "Você pode ajustar manualmente aqui:",
                value=cleaned or raw,
                height=400,
            )
            st.write("✔️ Copie esse texto e use onde quiser (Drive, Sheets, etc.).")
    else:
        st.info("Envie uma imagem para começar.")


if __name__ == "__main__":
    main()
