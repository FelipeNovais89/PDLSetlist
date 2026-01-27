import gspread
from google.oauth2.service_account import Credentials

def test_sheets_quota():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file("service_account.json", scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key("YOUR_ID")
    try:
        sh.sheet1.append_row(["TESTE"])
        print("OK: conseguiu escrever")
    except Exception as e:
        print("ERRO:", e)
