import streamlit as st
import io
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

st.set_page_config(page_title="Drive TXT Debug", layout="wide")


# -----------------------------
# Drive helpers
# -----------------------------
def get_drive_service():
    secrets = st.secrets["gcp_service_account"]
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    creds = Credentials.from_service_account_info(secrets, scopes=scopes)
    return build("drive", "v3", credentials=creds)


def drive_get_metadata(service, file_id: str):
    return service.files().get(
        fileId=file_id,
        fields="id,name,mimeType,size,trashed,owners,driveId,parents",
        supportsAllDrives=True,
    ).execute()


def drive_download_bytes(service, file_id: str) -> bytes:
    """
    Baixa o conteúdo de um arquivo do Drive.
    - Para arquivos normais (text/plain), usa get_media
    - Para Google Docs/Sheets etc, isso NÃO funciona (aí precisaria export)
    """
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()
        if status:
            st.write(f"Download: {int(status.progress() * 100)}%")

    fh.seek(0)
    return fh.read()


def drive_export_text(service, file_id: str, export_mime="text/plain") -> bytes:
    """
    Exporta Google Docs para texto (quando o arquivo for um Google Doc).
    """
    request = service.files().export_media(fileId=file_id, mimeType=export_mime)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()
        if status:
            st.write(f"Export: {int(status.progress() * 100)}%")

    fh.seek(0)
    return fh.read()


# -----------------------------
# UI
# -----------------------------
st.title("🔎 Debug de leitura TXT no Google Drive")

st.markdown(
    """
Este app serve para diagnosticar por que o TXT não está sendo lido.
Ele tenta:
1) Ler metadados do arquivo
2) Baixar conteúdo (get_media)
3) Se não der, tenta export (quando for Google Doc)
"""
)

file_id = st.text_input("Cole aqui o File ID do TXT no Drive", value="").strip()

colA, colB = st.columns(2)
with colA:
    supports_all_drives = st.checkbox("supportsAllDrives=True", value=True)
with colB:
    use_readonly_scope = st.checkbox("Usar scope readonly (recomendado)", value=True)

if st.button("Rodar diagnóstico", type="primary", disabled=not file_id):
    try:
        # scope readonly (ou drive completo)
        secrets = st.secrets["gcp_service_account"]
        scopes = ["https://www.googleapis.com/auth/drive.readonly"] if use_readonly_scope else ["https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(secrets, scopes=scopes)
        service = build("drive", "v3", credentials=creds)

        st.success("✅ Serviço do Drive criado com sucesso (credenciais OK).")

        st.subheader("1) Metadados do arquivo")
        meta = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,trashed,owners,driveId,parents",
            supportsAllDrives=supports_all_drives,
        ).execute()
        st.json(meta)

        mime = meta.get("mimeType", "")
        trashed = meta.get("trashed", False)

        if trashed:
            st.warning("⚠️ Este arquivo está na lixeira (trashed=True). Isso pode bloquear leitura dependendo do caso.")

        st.subheader("2) Tentativa de leitura do conteúdo")
        st.caption("Primeiro tenta get_media (funciona para arquivos normais tipo text/plain).")

        content_bytes = b""
        content_text = ""

        try:
            content_bytes = drive_download_bytes(service, file_id)
            content_text = content_bytes.decode("utf-8", errors="replace")
            st.success("✅ get_media funcionou. Conteúdo lido com sucesso.")
        except HttpError as e:
            st.error("❌ get_media falhou (HttpError).")
            st.code(str(e), language="text")

            # Se for Google Doc, export pode funcionar
            st.caption("Tentando export_media (útil se o arquivo for Google Docs).")
            try:
                content_bytes = drive_export_text(service, file_id, export_mime="text/plain")
                content_text = content_bytes.decode("utf-8", errors="replace")
                st.success("✅ export_media funcionou. Conteúdo exportado com sucesso.")
            except HttpError as e2:
                st.error("❌ export_media também falhou.")
                st.code(str(e2), language="text")
            except Exception as e2:
                st.error("❌ export_media falhou (Exception).")
                st.code(repr(e2), language="text")

        except Exception as e:
            st.error("❌ get_media falhou (Exception).")
            st.code(repr(e), language="text")

        if content_text:
            st.subheader("3) Preview do texto")
            st.text_area("Conteúdo do TXT", value=content_text, height=400)

            st.subheader("4) Sinais comuns de problema")
            if "Erro ao carregar" in content_text or "HttpError" in content_text:
                st.warning("O conteúdo parece ser uma mensagem de erro, não a cifra em si.")
            if mime and mime != "text/plain":
                st.info(f"Obs: mimeType detectado = {mime}. Se for Google Doc, use export; se for arquivo normal, use get_media.")

        st.subheader("5) Checklist rápido (o que mais costuma dar errado)")
        st.markdown(
            """
- **Permissão**: o arquivo/pasta precisa estar **compartilhado** com o `client_email` da service account.
- **ID errado**: você passou ID de pasta ou atalho em vez do arquivo.
- **Tipo do arquivo**: se for `application/vnd.google-apps.document` (Google Docs), `get_media` falha → precisa `export_media`.
- **Shared Drive**: se estiver em Shared Drive, precisa `supportsAllDrives=True` e o usuário/SA ter acesso.
"""
        )

    except HttpError as e:
        st.error("Erro HTTP ao acessar o Drive (provavelmente permissão ou ID inválido).")
        st.code(str(e), language="text")
    except Exception as e:
        st.error("Erro inesperado.")
        st.code(repr(e), language="text")
