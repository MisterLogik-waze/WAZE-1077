from datetime import datetime
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

def clean_and_format_event(ev):
    """
    Nettoie et formate un événement brut de la liste events.js
    Structure brute attendue : [coords_dict, event_type, raw_date, message_dict]
    """
    if not isinstance(ev, list) or len(ev) < 4:
        return None, None, None

    coords_dict = ev[0]
    event_type = ev[1]
    raw_date = ev[2]
    message_dict = ev[3]

    # Formatage de la date
    try:
        dt = datetime.fromisoformat(raw_date)
        formatted_date = dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        formatted_date = raw_date

    # Extraction propre des coordonnées (on évite les doublons du dictionnaire horaire)
    lat, lon = 0.0, 0.0
    if isinstance(coords_dict, dict) and coords_dict:
        first_hour_key = list(coords_dict.keys())[0]
        coords_data = coords_dict[first_hour_key]
        if isinstance(coords_data, list) and len(coords_data) > 0:
            first_point = coords_data[0]
            if isinstance(first_point, list) and len(first_point) >= 2:
                lat, lon = first_point[0], first_point[1]

    # Extraction du message
    message = "Information trafic non disponible"
    if isinstance(message_dict, dict):
        message = message_dict.get("FR", message)
    elif isinstance(message_dict, str):
        message = message_dict

    # Correspondance des types d'événements pour le titre
    type_labels = {
        "TR": "Travaux",
        "AC": "Accident",
        "IN": "Incident / Véhicule arrêté",
        "IF": "Information réseau"
    }
    label = type_labels.get(event_type, "Trafic")
    title = f"🚧 {label} ({event_type})"

    # Création du lien WME propre (sans la virgule erronée dans le paramètre lat)
    wme_url = f"https://www.waze.com/fr/editor?env=row&lat={lat}&lon={lon}&zoomLevel=18"

    # Construction de la description formatée
    description = (
        f">>> **Date:** {formatted_date}\n"
        f"📢 **Message:** {message}\n"
        f"📍 **Coordonnées:** {lat}, {lon}\n"
        f"🔗 [Ouvrir dans WME]({wme_url})"
    )

    # Identifiant unique de l'événement pour éviter les doublons
    ev_id = f"{event_type}_{lat}_{lon}_{raw_date}"

    return ev_id, title, description

def send_discord_alert(title, description, color=15158332):
    if not DISCORD_WEBHOOK_URL:
        print(f"[DRY RUN] {title}\n{description}\n" + "-"*40)
        return
    
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color
        }]
    }
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        response.raise_for_status()
        print(f"[DISCORD] Alerte envoyée avec succès : {title}")
    except Exception as e:
        print(f"[ERREUR] Échec de l'envoi Discord : {e}")

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
        
        # Attente active que events.js soit bien intercepté
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
            
        events_list = captured_data["events"] if isinstance(captured_data["events"], list) else []
        
        for ev in events_list:
            ev_id, title, description = clean_and_format_event(ev)
            if not ev_id:
                continue
                
            if ev_id not in seen_events:
                new_seen_events.add(ev_id)
                send_discord_alert(title, description)

    save_seen_events(new_seen_events)
    print("Traitement terminé avec succès.")

if __name__ == "__main__":
    run()
