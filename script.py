import requests
from bs4 import BeautifulSoup
import re
import csv
import os
import matplotlib.pyplot as plt
from datetime import datetime
import time

# --- Selenium for MediaMarkt ---
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# ===========================
# CONFIG
# ===========================
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
}

CSV_FILE = "price_history.csv"

# Telegram bot config
BOT_TOKEN = "8007475242:AAHhCsZ8-Mt-sVtaX7rmy4JSFU_egrIbRmc"
CHAT_ID = 350393260  # your chat ID

# Price thresholds for notifications
AMAZON_THRESHOLD = 250
MEDIAWORLD_THRESHOLD = 270
MEDIAMARKT_THRESHOLD = 240

# ===========================
# Functions
# ===========================
def get_chf_to_eur():
    """Get CHF → EUR exchange rate via API"""
    api_url = "https://api.exchangerate-api.com/v4/latest/CHF"
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        rate = data.get("rates", {}).get("EUR")
        if rate is None or not isinstance(rate, (int, float)):
            raise ValueError(f"Invalid rate received: {rate}")
        return rate
    except:
        return 0.95  # fallback

CHF_TO_EUR = get_chf_to_eur()
print(f"Current CHF→EUR rate: {CHF_TO_EUR}")

# --- Telegram notification ---
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except:
        print("Telegram notification failed")

# --- Scraping functions ---
def get_price_amazon(url):
    try:
        r = requests.get(url, headers=HEADERS)
        soup = BeautifulSoup(r.text, 'html.parser')
        price = soup.select_one('span.a-offscreen')
        if price:
            text = price.get_text(strip=True).replace('€', '').replace(',', '.')
            return float(text)
    except:
        return None

def get_price_mediaworld(url):
    try:
        r = requests.get(url, headers=HEADERS)
        soup = BeautifulSoup(r.text, 'html.parser')
        whole = soup.select_one('span[data-test="branded-price-whole-value"]')
        decimal = soup.select_one('span[data-test="branded-price-decimal-value"]')
        if whole and decimal:
            price_text = f"{whole.text.strip()}.{decimal.text.strip()}"
            price_text = re.sub(r'[^\d\.]', '', price_text)
            return float(price_text)
    except:
        return None

def get_price_mediamarkt(url):
    try:
        chrome_options = Options()
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        chrome_options.add_argument("--headless")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        driver.get(url)
        time.sleep(5)
        page_source = driver.page_source
        driver.quit()

        patterns = [
            r'"price"\s*:\s*"([\d\.,]+)"',
            r'"price"\s*:\s*([\d\.,]+)',
            r'currentPrice["\']?\s*:\s*["\']?([\d\.,]+)',
            r'finalPrice["\']?\s*:\s*["\']?([\d\.,]+)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, page_source)
            if matches:
                price = matches[0].strip().rstrip(',')
                price = price.replace(',', '.')
                price = re.sub(r'[^\d.]', '', price)
                if price:
                    return float(price)
        return None
    except Exception as e:
        print("MediaMarkt scraping error:", e)
        return None

# --- CSV ---
def save_csv(timestamp, amazon, mediaworld, mediamarkt_eur):
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "amazon_eur", "mediaworld_eur", "mediamarkt_eur"])
        writer.writerow([
            timestamp,
            amazon if amazon is not None else "",
            mediaworld if mediaworld is not None else "",
            mediamarkt_eur if mediamarkt_eur is not None else ""
        ])

# --- Plot ---
def update_plot():
    timestamps, amazon, mediaworld, mediamarkt = [], [], [], []
    if not os.path.isfile(CSV_FILE):
        return
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            timestamps.append(datetime.fromisoformat(row["timestamp"]))
            amazon.append(float(row["amazon_eur"]) if row["amazon_eur"] else None)
            mediaworld.append(float(row["mediaworld_eur"]) if row["mediaworld_eur"] else None)
            mediamarkt.append(float(row["mediamarkt_eur"]) if row["mediamarkt_eur"] else None)

    fig, ax = plt.subplots(figsize=(10, 6))
    fig.patch.set_facecolor('#2E2E2E')   
    ax.set_facecolor('#1E1E1E')          

    ax.plot(timestamps, amazon, color="gold", label="Amazon")
    ax.plot(timestamps, mediaworld, color="red", label="MediaWorld")
    ax.plot(timestamps, mediamarkt, color="darkred", label="MediaMarkt (CHF→EUR)")

    ax.set_xlabel("Time", color="white")
    ax.set_ylabel("Price (EUR)", color="white")
    ax.set_title("Price Trends in EUR", color="white")
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.6, color='gray')
    
    ax.tick_params(colors='white', which='both')
    
    plt.tight_layout()
    plt.savefig("price_history.png")
    plt.close()


# ===========================
# MAIN with retry and conditional save
# ===========================
if __name__ == "__main__":
    urls = {
        "Amazon": (
            "https://www.amazon.it/gp/product/B0F66XD5LF/ref=ox_sc_act_title_6?smid=A11IL2PNWYJU7H&th=1",
            get_price_amazon
        ),
        "MediaWorld": (
            "https://www.mediaworld.it/it/product/_nothing-headphone-1-cuffie-bluetooth-nero-bianco-159488484.html",
            get_price_mediaworld
        ),
        "MediaMarkt": (
            "https://www.mediamarkt.ch/fr/product/_nothing-headphone-1-over-ear-kopfhorer-bluetooth-weiss-2279652.html",
            get_price_mediamarkt
        ),
    }

    PRODUCT_NAME = "Nothing Headphones 1"
    max_attempts = 10
    attempt = 0

    amazon = mediaworld = mediamarkt_chf = None

    # --- Retry loop ---
    while attempt < max_attempts:
        attempt += 1
        amazon = urls["Amazon"][1](urls["Amazon"][0])
        mediaworld = urls["MediaWorld"][1](urls["MediaWorld"][0])
        mediamarkt_chf = urls["MediaMarkt"][1](urls["MediaMarkt"][0])

        if amazon is not None and mediaworld is not None and mediamarkt_chf is not None:
            # Tutti i prezzi trovati
            break
        else:
            time.sleep(5)  # attendi 10 secondi prima di ritentare

    # --- Controllo finale ---
    missing = []
    if amazon is None:
        missing.append("Amazon")
    if mediaworld is None:
        missing.append("MediaWorld")
    if mediamarkt_chf is None:
        missing.append("MediaMarkt")

    if missing:
        missing_str = ", ".join(missing)
        send_telegram(f"⚠️ {PRODUCT_NAME} - Error: could not retrieve prices for: {missing_str} after {max_attempts} attempts!")
        exit()  # Ferma l'esecuzione, niente CSV, niente plot

    # --- Tutti i prezzi presenti: calcolo EUR e salvataggio ---
    mediamarkt_eur = round(mediamarkt_chf * CHF_TO_EUR, 2)
    timestamp = datetime.now().isoformat(timespec="seconds")

    print("Prices found:")
    print(f"Amazon: {amazon} EUR")
    print(f"MediaWorld: {mediaworld} EUR")
    print(f"MediaMarkt: {mediamarkt_chf} CHF → {mediamarkt_eur} EUR")

    save_csv(timestamp, amazon, mediaworld, mediamarkt_eur)
    update_plot()
    print("✅ Prices saved and plot updated (price_history.png)")

    # --- Telegram notifications if below threshold ---
    if amazon < AMAZON_THRESHOLD:
        send_telegram(f"📉 {PRODUCT_NAME} - Amazon price dropped to {amazon} EUR!")

    if mediaworld < MEDIAWORLD_THRESHOLD:
        send_telegram(f"📉 {PRODUCT_NAME} - MediaWorld price dropped to {mediaworld} EUR!")

    if mediamarkt_eur < MEDIAMARKT_THRESHOLD:
        send_telegram(f"📉 {PRODUCT_NAME} - MediaMarkt price dropped to {mediamarkt_eur} EUR!")