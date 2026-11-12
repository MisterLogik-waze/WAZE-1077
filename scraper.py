import json
import os
import re
import requests
from playwright.sync_api import sync_playwright

TARGET_URL = "https://www.vinci-autoroutes.com/fr/autoroutes-temps-reel/"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
STATE_FILE = "seen_events.json"

def load_seen_events():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_seen_events(seen_set):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_set), f, ensure_ascii=False, indent=2)

def extract_json_from_js(js_text):
    """
    Extrait l'objet ou le tableau JSON contenu dans le fichier .js
    """
    match = re.search(r'(\{.*\}|\[.*\])', js_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            print(f"Erreur de décodage JSON : {e}")
    return None

def send_discord_alert(title, description, color=15158332):
    if not DISCORD_WEBHOOK_URL:
        print(f"[DRY RUN] {title} - {description}")
        return
    
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color
        }]
    }
    response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
    response.raise_for_status()

def run():
    captured_data = {
        "events": None,
        "alerts": None,
        "info": None
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        def handle_response(response):
            url = response.url
            if "autoroutes-trafic.fr" in url and response.status == 200:
                try:
                    if url.endswith("/events.js"):
                        captured_data["events"] = extract_json_from_js(response.text())
                        print(f"[OK] events.js capturé depuis {url}")
                    elif url.endswith("/trafficAlert.js"):
                        captured_data["alerts"] = extract_json_from_js(response.text())
                        print(f"[OK] trafficAlert.js capturé depuis {url}")
                    elif url.endswith("/info.js"):
                        captured_data["info"] = extract_json_from_js(response.text())
                        print(f"[OK] info.js capturé depuis {url}")
                except Exception as e:
                    print(f"Erreur lecture réponse {url}: {e}")

        page.on("response", handle_response)

        print("Chargement de la page Vinci Autoroutes...")
        page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        
        # Attente active que les 3 éléments clés soient bien interceptés
        import time
        timeout = 15
        start_time = time.time()
        while time.time() - start_time < timeout:
            if captured_data["events"] is not None:
                break
            page.wait_for_timeout(1000)

        browser.close()

    # Traitement des données capturées
    seen_events = load_seen_events()
    new_seen_events = set(seen_events)

    if captured_data["events"]:
        # Sauvegarde brute pour inspection
        with open("debug_events.json", "w", encoding="utf-8") as f:
            json.dump(captured_data["events"], f, ensure_ascii=False, indent=2)
            
        # Si c'est directement une liste, on l'utilise telle quelle
        events_list = captured_data["events"] if isinstance(captured_data["events"], list) else []
        
        for ev in events_list:
            # Sécurisation si un élément de la liste n'est pas un dictionnaire
            if not isinstance(ev, dict):
                continue
                
            ev_id = str(ev.get("id") or ev.get("eventId") or hash(str(ev)))
            if ev_id not in seen_events:
                new_seen_events.add(ev_id)
                titre = ev.get("title", "Nouvel événement trafic")
                desc = ev.get("description", str(ev)[:200])
                send_discord_alert(f"🚧 {titre}", desc)

    save_seen_events(new_seen_events)
    print("Traitement terminé avec succès.")

if __name__ == "__main__":
    run()
