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
    match = re.search(r'(\{.*\}|\[.*\])', js_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            print(f"Erreur de décodage JSON : {e}")
    return None

def clean_and_format_event(ev):
    """
    Nettoie, formate l'événement et attribue les émojis, couleurs et balises de routes/sens.
    """
    if not isinstance(ev, list) or len(ev) < 4:
        return None, None, None, 15158332

    coords_dict = ev[0]
    event_type = ev[1]
    raw_date = ev[2]
    message_dict = ev[3]

    # Formatage de la date avec l'émoji demandé
    try:
        dt = datetime.fromisoformat(raw_date)
        formatted_date = f"🕒 Date: {dt.strftime('%d/%m/%Y %H:%M:%S')}"
    except Exception:
        formatted_date = f"🕒 Date: {raw_date}"

    # Extraction des coordonnées
    lat, lon = 0.0, 0.0
    if isinstance(coords_dict, dict) and coords_dict:
        first_hour_key = list(coords_dict.keys())[0]
        coords_data = coords_dict[first_hour_key]
        if isinstance(coords_data, list) and len(coords_data) > 0:
            first_point = coords_data[0]
            if isinstance(first_point, list) and len(first_point) >= 2:
                lat, lon = first_point[0], first_point[1]

    # Message
    message = "Information trafic non disponible"
    if isinstance(message_dict, dict):
        message = message_dict.get("FR", message)
    elif isinstance(message_dict, str):
        message = message_dict

    # 1. Extraction de la route (Regex élargie pour capturer A7N, A8, N164, etc.)
    route_match = re.search(r'\b([AND]\d+[A-Z]*)\b', message)
    route_name = route_match.group(1) if route_match else "ABC"

    # 2. Détection du sens ou des voies concernées + émoji associé
    direction_emoji = " "
    direction_text = ""
    
    msg_lower = message.lower()
    if "toutes les voies" in msg_lower:
        direction_emoji = "⟺"
        direction_text = " - Toutes les Voies"
    elif "deux sens" in msg_lower or "les deux sens" in msg_lower:
        direction_emoji = "⇄"
        direction_text = " - Deux sens"
    elif "bretelle d'entrée" in msg_lower:
        direction_emoji = "↖"
        direction_text = " - Bretelle Entrée"
    elif "bretelle de sortie" in msg_lower:
        direction_emoji = "↗"
        direction_text = " - Bretelle Sortie"
    elif "voie de gauche" in msg_lower:
        direction_emoji = "⬅"
        direction_text = " - Voie de Gauche"
    elif "voie de droite" in msg_lower:
        direction_emoji = "➡"
        direction_text = " - Voie de Droite"
    elif "bande d'arrêt d'urgence" in msg_lower:
        direction_emoji = "⇝"
        direction_text = " - BAU"
    elif "aire de service" in msg_lower:
        direction_emoji = "i"
        direction_text = " - Aire de Service"
    elif "voies neutralisée" in msg_lower or "voies neutralisées" in msg_lower or "voie neutralisées" in msg_lower or "voies neutralisée" in msg_lower:
        direction_emoji = "❌"
        direction_text = " - Voie.s Neutralisée.s"
    elif "sens" in msg_lower:
        direction_emoji = "⦽"
        direction_text = " - NA"

    # 3. Analyse du type / contenu pour la couleur et l'émoji principal
    # Couleurs : Rouge (fermeture), Orange (travaux/accident/obstacle), Bleu (info/restriction)
    color_red = 15158332      # Rouge
    color_orange = 15105570   # Orange
    color_blue = 3447003      # Bleu

    if any(keyword in msg_lower for keyword in ["fermée", "ferme", "coupure", "coupé", "voies neutralisées", "voie neutralisée"]):
        main_emoji = "⛔"
        color = color_red
        category_label = "Fermeture"
    elif event_type == "AC" or "accident" in msg_lower:
        main_emoji = "💥"
        color = color_orange
        category_label = "Accident"
    elif event_type == "TR" or "travaux" in msg_lower:
        main_emoji = "🦺"
        color = color_orange
        category_label = "Travaux"
    elif event_type == "IN" or any(kw in msg_lower for kw in ["objet sur", "obstacle sur", "dégradée", "panne", "véhicule arrêté"]):
        main_emoji = "⚠️"
        color = color_orange
        category_label = "Incident"
    elif any(kw in msg_lower for kw in ["réduction de vitesse", "vitesse", "dépassement interdit", "réduction du nombre", "plus de distribution"]):
        main_emoji = "ℹ️"
        color = color_blue
        category_label = "Information"
    else:
        main_emoji = "🚗"
        color = color_blue
        category_label = "Restriction"

    # Construction du Titre dynamique : Ex: 🦺 Travaux A8 - Voie de gauche
    title = f"{main_emoji} {category_label} {route_name}{direction_text}"

    # Lien WME propre
    pwme_url = f"https://www.waze.com/fr/editor?env=row&lat={lat}&lon={lon}&zoomLevel=18"
    bwme_url = f"https://beta.waze.com/fr/editor?env=row&lat={lat}&lon={lon}&zoomLevel=18"

    # Description formatée
    description = (
        f">>> {formatted_date}\n"
        f"📢 **Message:** {message}\n"
        f"📍 **Coordonnées:** {lat}, {lon}\n"
        f"🔗 [Ouvrir dans WME]({pwme_url}) / [Bêta]({bwme_url}) | {direction_emoji}"
    )

    ev_id = f"{event_type}_{lat}_{lon}_{raw_date}"

    return ev_id, title, description, color

def send_discord_alert(title, description, color):
    if not DISCORD_WEBHOOK_URL:
        print(f"[DRY RUN] Couleur: {color}\n{title}\n{description}\n" + "-"*40)
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
        print(f"[DISCORD] Alerte envoyée : {title}")
    except Exception as e:
        print(f"[ERREUR] Échec de l'envoi Discord : {e}")

def run():
    captured_data = {"events": None}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        def handle_response(response):
            url = response.url
            if "autoroutes-trafic.fr" in url and response.status == 200:
                if url.endswith("/events.js"):
                    captured_data["events"] = extract_json_from_js(response.text())
                    print(f"[OK] events.js capturé depuis {url}")

        page.on("response", handle_response)

        print("Chargement de la page Vinci Autoroutes...")
        page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        
        import time
        start_time = time.time()
        while time.time() - start_time < 15:
            if captured_data["events"] is not None:
                break
            page.wait_for_timeout(1000)

        browser.close()

    seen_events = load_seen_events()
    new_seen_events = set(seen_events)

    if captured_data["events"]:
        with open("debug_events.json", "w", encoding="utf-8") as f:
            json.dump(captured_data["events"], f, ensure_ascii=False, indent=2)
            
        events_list = captured_data["events"] if isinstance(captured_data["events"], list) else []
        
        for ev in events_list:
            ev_id, title, description, color = clean_and_format_event(ev)
            if not ev_id:
                continue
                
            if ev_id not in seen_events:
                new_seen_events.add(ev_id)
                send_discord_alert(title, description, color)

    save_seen_events(new_seen_events)
    print("Traitement terminé avec succès.")

if __name__ == "__main__":
    run()
