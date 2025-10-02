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

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import traceback
# ===========================
# CONFIG
# ===========================
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Accept-Language': 'it-IT,it;q=0.9',
    'DNT': '1'
}

CSV_FILE = "price_history.csv"

# Telegram bot config
BOT_TOKEN = "8007475242:AAHhCsZ8-Mt-sVtaX7rmy4JSFU_egrIbRmc"
CHAT_ID = 350393260  # your chat ID

# Price thresholds for notifications
AMAZON_THRESHOLD = 240
MEDIAWORLD_THRESHOLD = 240
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

def send_telegram_photo(photo_path, caption=""):
    """Send photo to Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            files = {'photo': photo}
            data = {'chat_id': CHAT_ID, 'caption': caption}
            response = requests.post(url, files=files, data=data)
            return response.status_code == 200
    except Exception as e:
        print(f"Telegram photo send failed: {e}")
        return False

# --- Scraping functions ---
def get_price_amazon(url, retries=3, delay=5):
    """Scrape Amazon price robustly, return float or None"""
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                print(f"Amazon request failed with status {r.status_code}")
                continue

            soup = BeautifulSoup(r.text, 'html.parser')

            price_selectors = [
                'span.a-offscreen',               
                'span#corePriceDisplay_desktop_feature_div span.a-offscreen',
                'span#priceblock_ourprice',
                'span#priceblock_dealprice'
            ]

            price_text = None
            for sel in price_selectors:
                el = soup.select_one(sel)
                if el and el.get_text(strip=True):
                    price_text = el.get_text(strip=True)
                    break

            if not price_text:
                print(f"Attempt {attempt}: Amazon price not found")
                time.sleep(delay)
                continue

            price_text = price_text.replace('€', '').replace(',', '.').strip()
            price_text = re.sub(r'[^\d.]', '', price_text)
            if not price_text:
                continue

            return float(price_text)

        except Exception as e:
            print(f"Attempt {attempt}: Amazon scraping error: {e}")
            time.sleep(delay)

    return None


def get_price_mediaworld(url, wait_time=20):
    """
    Scrape MediaWorld using Selenium (headless). Returns a float price in EUR or None on error.
    Robust parsing to avoid double dots or strange characters in the price.
    """
    driver = None
    try:
        chrome_options = Options()
        try:
            chrome_options.add_argument("--headless=new")
        except Exception:
            chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)

        # Make webdriver less detectable
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        })

        driver.get(url)
        wait = WebDriverWait(driver, wait_time)

        # Trova il prezzo principale
        whole = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, 'span[data-test="branded-price-whole-value"]')
        ))

        # Decimal potrebbe non esistere
        try:
            decimal = driver.find_element(By.CSS_SELECTOR, 'span[data-test="branded-price-decimal-value"]')
        except:
            decimal = None

        # --- Pulizia robusta dei prezzi ---
        whole_text = re.sub(r'[^\d]', '', whole.text.strip())
        decimal_text = re.sub(r'[^\d]', '', decimal.text.strip() if decimal else "")

        if decimal_text:
            price_text = f"{whole_text}.{decimal_text}"
        else:
            price_text = whole_text

        # Ultimo controllo: se ci sono più punti, tieni solo l'ultimo
        if price_text.count('.') > 1:
            parts = price_text.split('.')
            price_text = ''.join(parts[:-1]) + '.' + parts[-1]

        return float(price_text)

    except Exception as e:
        print("MediaWorld scraping error:", e)
        try:
            if driver:
                print(driver.page_source[:2000])
        except:
            pass
        return None
    finally:
        if driver:
            driver.quit()


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
    print("=== Starting Price Tracker on GitHub Actions ===")
    print(f"Current directory: {os.getcwd()}")
    print(f"Files in directory: {os.listdir('.')}")
    
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
        print(f"Attempt {attempt}/{max_attempts}")
        
        if amazon is None:
            print("Scraping Amazon...")
            amazon = urls["Amazon"][1](urls["Amazon"][0])
            print(f"Amazon result: {amazon}")
            
        if mediaworld is None:
            print("Scraping MediaWorld...")
            mediaworld = urls["MediaWorld"][1](urls["MediaWorld"][0])
            print(f"MediaWorld result: {mediaworld}")
            
        if mediamarkt_chf is None:
            print("Scraping MediaMarkt...")
            mediamarkt_chf = urls["MediaMarkt"][1](urls["MediaMarkt"][0])
            print(f"MediaMarkt result: {mediamarkt_chf}")

        if amazon is not None and mediaworld is not None and mediamarkt_chf is not None:
            print("✅ All prices retrieved successfully!")
            break
        else:
            print("⏳ Some prices missing, retrying...")
            time.sleep(10) 

    missing = []
    if amazon is None:
        missing.append("Amazon")
    if mediaworld is None:
        missing.append("MediaWorld")
    if mediamarkt_chf is None:
        missing.append("MediaMarkt")

    if missing:
        missing_str = ", ".join(missing)
        error_msg = f"⚠️ {PRODUCT_NAME} - Error: could not retrieve prices for: {missing_str} after {max_attempts} attempts!\nHowever the prices founded are: \nAmazon {amazon}€\Mediamarkt {round(mediamarkt_chf * CHF_TO_EUR, 2)}€\Mediaworld {mediaworld}€"
        print(error_msg)
        send_telegram(error_msg)
        exit() 

    # --- all prices present ---
    mediamarkt_eur = round(mediamarkt_chf * CHF_TO_EUR, 2)
    timestamp = datetime.now().isoformat(timespec="seconds")

    print("Prices found:")
    print(f"Amazon: {amazon} EUR")
    print(f"MediaWorld: {mediaworld} EUR")
    print(f"MediaMarkt: {mediamarkt_chf} CHF → {mediamarkt_eur} EUR")

    # Salva CSV
    save_csv(timestamp, amazon, mediaworld, mediamarkt_eur)
    print("✅ CSV saved")
    
    # Crea e salva il plot
    plot_success = update_plot()
    
    # Verifica se i file sono stati creati
    csv_exists = os.path.isfile(CSV_FILE)
    plot_exists = os.path.isfile("price_history.png")
    
    print(f"CSV exists: {csv_exists}")
    print(f"Plot exists: {plot_exists}")
    print(f"Current files: {os.listdir('.')}")

    # Prepara il messaggio per Telegram
    message = f"📊🎧 {PRODUCT_NAME} - Daily Price Update:\n\n"
    message += f"🛒 Amazon: {amazon} EUR ( {urls['Amazon'][0]} ) \n"
    message += f"🛒 MediaWorld: {mediaworld} EUR ( {urls['MediaWorld'][0]} ) \n" 
    message += f"🛒 MediaMarkt: {mediamarkt_eur} EUR ( {urls['MediaMarkt'][0]} ) \n\n"
    message += f"⏰ {timestamp}"

    # Invia prima il messaggio di testo
    # send_telegram(message)
    print("✅ Price message sent to Telegram")

    # Poi invia il plot come immagine se esiste
    if plot_exists:
        if send_telegram_photo("price_history.png", message):
            print("✅ Plot sent to Telegram")
        else:
            print("❌ Failed to send plot to Telegram")
            send_telegram("⚠️ Could not send price chart image")
    else:
        print("❌ Plot file not found")
        send_telegram("⚠️ Could not generate price chart")

    # Notifiche per prezzi bassi
    alerts_sent = False
    if amazon < AMAZON_THRESHOLD:
        send_telegram(f"🎯🎧 PRICE ALERT! {PRODUCT_NAME} - Amazon price dropped to {amazon} EUR!")
        alerts_sent = True

    if mediaworld < MEDIAWORLD_THRESHOLD:
        send_telegram(f"🎯🎧 PRICE ALERT! {PRODUCT_NAME} - MediaWorld price dropped to {mediaworld} EUR!")
        alerts_sent = True

    if mediamarkt_eur < MEDIAMARKT_THRESHOLD:
        send_telegram(f"🎯🎧 PRICE ALERT! {PRODUCT_NAME} - MediaMarkt price dropped to {mediamarkt_eur} EUR!")
        alerts_sent = True

    if not alerts_sent:
        print("✅ No price alerts triggered")

    print("✅ Script completed successfully!")
