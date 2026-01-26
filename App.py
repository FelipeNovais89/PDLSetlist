import streamlit as st
import google.generativeai as genai

# -------------------------------------------------------------
# 1. PEGAR A CHAVE DO GEMINI EM st.secrets
# -------------------------------------------------------------
def get_gemini_api_key():
    """
    Procura pela chave em:
    - st.secrets["gemini_api_key"]
    - st.secrets["sheets"]["gemini_api_key"] (se você quiser reaproveitar)
    """
    try:
        if "gemini_api_key" in st.secrets:
            return st.secrets["gemini_api_key"]
        if "sheets" in st.secrets and "gemini_api_key" in st.secrets["sheets"]:
            return st.secrets["sheets"]["gemini_api_key"]
    except Exception:
        pass
    return None


# Modelo multimodal que funciona bem com a API antiga
GEMINI_MODEL_NAME = "gemini-pro-vision"
GEMINI_API_KEY = get_gemini_api_key()

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    st.warning(
        "⚠️ Gemini API key não encontrada em st.secrets.\n\n"
        "No arquivo `.streamlit/secrets.toml`, coloque por exemplo:\n\n"
        "gemini_api_key = \"SUA_CHAVE_AQUI\""
    )


# -------------------------------------------------------------
# 2. FUNÇÃO DE TRANSCRIÇÃO (COM DEBUG)
# -------------------------------------------------------------
def transcribe_image_with_gemini(uploaded_file):
    if not GEMINI_API_KEY:
        st.error("Gemini API key não configurada. Verifique o secrets.toml.")
        return "", ""

    # Info básica do arquivo
    file_bytes = uploaded_file.getvalue()
    mime = uploaded_file.type or "image/jpeg"

    st.subheader("1️⃣ Arquivo recebido")
    st.write("Nome:", uploaded_file.name)
    st.write("MIME:", mime)
    st.write("Tamanho (bytes):", len(file_bytes))

    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)

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

        st.subheader("2️⃣ Chamando o Gemini")
        st.write("Modelo:", GEMINI_MODEL_NAME)

        response = model.generate_content(
            [
                prompt,
                {"mime_type": mime, "data": file_bytes},
            ]
        )

        raw_text = (getattr(response, "text", "") or "").strip()

        st.subheader("3️⃣ Resposta BRUTA do modelo")
        if raw_text:
            st.code(raw_text[:2000], language="text")  # até 2000 chars
        else:
            st.info("Resposta vazia (string em branco).")

        # --- Limpeza: remover ``` e possíveis cabeçalhos de código ---
        cleaned = raw_text
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if "\n" in cleaned:
                cleaned = "\n".join(cleaned.split("\n")[1:]).strip()

        st.subheader("4️⃣ Texto LIMPO (p/ usar como cifra)")
        if cleaned:
            st.code(cleaned, language="text")
        else:
            st.info("Nada após limpeza – talvez o modelo tenha retornado só markdown vazio.")

        return raw_text, cleaned

    except Exception as e:
        st.error(f"Erro ao chamar Gemini: {e}")
        return "", ""


# -------------------------------------------------------------
# 3. APP STREAMLIT SIMPLES
# -------------------------------------------------------------
def main():
    st.set_page_config(page_title="Teste Gemini – Cifra por imagem", page_icon="🎵")
    st.title("Teste de Transcrição de Cifra com Gemini (Imagem ➜ Texto)")

    if not GEMINI_API_KEY:
        st.stop()

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
            raw, cleaned = transcribe_image_with_gemini(uploaded_file)

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
