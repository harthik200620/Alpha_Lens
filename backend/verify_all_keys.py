
import os
import sys
from dotenv import load_dotenv
from google import genai
import requests

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

def check_gemini():
    print("--- Checking Gemini API Keys ---")
    keys = [
        os.environ.get("GEMINI_API_KEY_1"),
        os.environ.get("GEMINI_API_KEY_2"),
        os.environ.get("GEMINI_API_KEY_3"),
        os.environ.get("GEMINI_API_KEY_4")
    ]
    
    for i, key in enumerate(keys):
        if not key:
            print(f"Key {i+1}: MISSING")
            continue
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents="Hello, respond with 'OK'"
            )
            if "OK" in response.text:
                print(f"Key {i+1}: WORKING (ends in ...{key[-4:]})")
            else:
                print(f"Key {i+1}: UNEXPECTED RESPONSE: {response.text}")
        except Exception as e:
            print(f"Key {i+1}: FAILED - {str(e)[:100]}")

def check_sendgrid():
    print("\n--- Checking SendGrid API Key ---")
    key = os.environ.get("SENDGRID_API_KEY")
    if not key:
        print("SendGrid Key: MISSING")
        return
    
    try:
        headers = {'Authorization': f'Bearer {key}'}
        # Attempt to get user profile or just check auth
        response = requests.get('https://api.sendgrid.com/v3/scopes', headers=headers, timeout=10)
        if response.status_code == 200:
            print(f"SendGrid Key: WORKING (ends in ...{key[-4:]})")
        else:
            print(f"SendGrid Key: FAILED (Status {response.status_code}) - {response.text[:100]}")
    except Exception as e:
        print(f"SendGrid Key: ERROR - {str(e)}")

def check_angelone():
    print("\n--- Checking AngelOne Credentials ---")
    client_id = os.environ.get("ANGELONE_CLIENT_ID")
    if not client_id:
        print("AngelOne Credentials: MISSING")
        return
    
    # We'll just check if the credentials exist, as full login requires TOTP which we shouldn't trigger here
    print(f"AngelOne Client ID: {client_id} (Configured)")
    print("AngelOne TOTP Secret: CONFIGURED" if os.environ.get("ANGELONE_TOTP_SECRET") else "AngelOne TOTP Secret: MISSING")

if __name__ == "__main__":
    check_gemini()
    check_sendgrid()
    check_angelone()
