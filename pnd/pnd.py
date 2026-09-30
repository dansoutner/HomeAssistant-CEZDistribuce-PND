ver = "v2.1.0"
import csv
import datetime
import json
import math
import os
import platform
import re
import shutil
import sys
import time
import unicodedata
import zipfile
from datetime import datetime as dt
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt
import requests
import websocket
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

OPTIONS_FILE = os.environ.get("PND_OPTIONS", "/data/options.json")
OUTPUT_ROOT = os.environ.get("PND_OUTPUT", "/share")
PNDURL = "https://pnd.cezdistribuce.cz/cezpnd2/external/dashboard/view"
PND_TZ = ZoneInfo("Europe/Prague")
UTC = datetime.timezone.utc
QUARTER = datetime.timedelta(minutes=15)
HOUR = datetime.timedelta(hours=1)
STATS_CHUNK = 1000
DISCOVERY_PREFIX = os.environ.get("MQTT_DISCOVERY_PREFIX", "homeassistant")

# Entity publikované přes MQTT discovery. Klíč tvoří entity_id (sensor.pnd_<klíč>[_<id>]), vlastnosti
# odpovídají atributům, které dřív doplněk posílal přes /api/states.
ENERGY = {"device_class": "energy", "unit_of_measurement": "kWh"}
PERCENT = {"unit_of_measurement": "%", "state_class": "measurement"}
DIAGNOSTIC = {"entity_category": "diagnostic"}
ENTITIES = {
    "consumption": {"component": "sensor", "name": "Consumption", **ENERGY},
    "production": {"component": "sensor", "name": "Production", **ENERGY},
    "consumption_15min": {"component": "sensor", "name": "Consumption 15min", **ENERGY},
    "production_15min": {"component": "sensor", "name": "Production 15min", **ENERGY},
    "data": {"component": "sensor", "name": "Data", "icon": "mdi:database-arrow-down"},
    "total_interval_consumption": {"component": "sensor", "name": "Total interval consumption", **ENERGY},
    "total_interval_production": {"component": "sensor", "name": "Total interval production", **ENERGY},
    "production2consumption": {"component": "sensor", "name": "Production to consumption",
                               "icon": "mdi:home-battery-outline", **PERCENT},
    "production2consumptionfull": {"component": "sensor", "name": "Production to consumption full", **PERCENT},
    "production2consumptionfloor": {"component": "sensor", "name": "Production to consumption floor", **PERCENT},
    "app_version": {"component": "sensor", "name": "App version", "icon": "mdi:check-decagram", **DIAGNOSTIC},
    "script_status": {"component": "sensor", "name": "Script status", "icon": "mdi:list-status", **DIAGNOSTIC},
    "script_duration": {"component": "sensor", "name": "Script duration", "icon": "mdi:timelapse", **DIAGNOSTIC},
    "running": {"component": "binary_sensor", "name": "Running", "device_class": "running",
                "payload_on": "on", "payload_off": "off", **DIAGNOSTIC},
}


class Colors:
    RED = '\033[31m'   # Red text
    GREEN = '\033[32m' # Green text
    YELLOW = '\033[33m' # Yellow text
    BLUE = '\033[34m'  # Blue text
    MAGENTA = '\033[35m' # Magenta text
    CYAN = '\033[36m'  # Cyan text
    RESET = '\033[0m'  # Reset to default color


class PndError(Exception):
    """Chyba se zprávou, která se zapíše do atributu status senzoru pnd_script_status."""


def log(msg, color=""):
    print(dt.now().strftime("%Y-%m-%d %H:%M:%S") + ": " + (f"{color}{msg}{Colors.RESET}" if color else msg), flush=True)


def delete_folder_contents(folder_path):
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)  # Removes each file.
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)  # Removes directories and their contents recursively.
        except Exception as e:
            print(f"Failed to delete {file_path}. Reason: {e}")


def zip_folder(folder_path, output_path):
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                if os.path.abspath(file_path) == os.path.abspath(output_path):
                    continue
                zipf.write(file_path, arcname=os.path.relpath(file_path, start=folder_path))


def slugify(text):
    """'TajnejHouse' -> 'tajnejhouse', 'Chata Šumava' -> 'chata_sumava'.

    HA povoluje v entity_id i statistic_id jen [a-z0-9_] bez podtržítka na krajích a bez '__'.
    """
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def conv_date(s):
    s = s.replace("24:00:00", "23:59:00")
    return datetime.datetime.strptime(s, "%d.%m.%Y %H:%M:%S")


def read_profile_csv(path):
    """Načte denní profil (07/08) jako [(datum, hodnota)].

    Hodnoty se převádí na text stejně jako dřív přes pandas: má-li sloupec desetinná čísla,
    je celý float ('0.0'), jinak int ('0').
    """
    with open(path, encoding="cp1250", newline="") as f:
        rows = [r for r in list(csv.reader(f, delimiter=";"))[1:] if r and r[0]]
    is_float = any("." in r[1] for r in rows)
    return [(r[0], float(r[1]) if is_float else int(r[1])) for r in rows]


def parse_local_end(s):
    """'29.09.2026 24:00:00' -> naive 30.09.2026 00:00 (PND značí konec intervalu, 24:00 = půlnoc)."""
    if s.endswith("24:00:00"):
        return datetime.datetime.strptime(s[:10], "%d.%m.%Y") + datetime.timedelta(days=1)
    return datetime.datetime.strptime(s, "%d.%m.%Y %H:%M:%S")


def read_15min_csv(path):
    """Načte 15min profil (01/02) jako [(konec_intervalu_utc, kWh, naměřeno)].

    Časy v CSV jsou místní a při přechodu na zimní čas se opakují, proto se UTC čas počítá
    z pořadí řádků (každý řádek = další čtvrthodina) a ne z textu. Hodnoty jsou v kW
    (průměrný výkon za čtvrthodinu), energie = kW * 0,25.
    """
    with open(path, encoding="cp1250", newline="") as f:
        rows = [r for r in list(csv.reader(f, delimiter=";"))[1:] if r and r[0]]
    if not rows:
        return []
    start_utc = (parse_local_end(rows[0][0]).replace(tzinfo=PND_TZ) - QUARTER).astimezone(UTC)
    quarters = []
    for i, r in enumerate(rows):
        end = start_utc + (i + 1) * QUARTER
        quarters.append((end, float(r[1].replace(",", ".")) * 0.25, "OK" in r[2]))
    last_label = parse_local_end(rows[-1][0])
    if quarters[-1][0].astimezone(PND_TZ).replace(tzinfo=None) != last_label:
        log(f"WARNING: {os.path.basename(path)}: čas posledního řádku {rows[-1][0]} nesedí s počtem řádků", Colors.YELLOW)
    return quarters


def hourly_statistics(quarters, base_sum):
    """Sečte čtvrthodiny do hodin (HA statistiky jsou hodinové) s kumulativní sumou od base_sum.

    Bere se jen po poslední naměřenou čtvrthodinu (budoucnost a dnešek bez dat se vynechají)
    a jen celé hodiny.
    """
    measured = [i for i, q in enumerate(quarters) if q[2]]
    if not measured:
        return []
    quarters = quarters[:measured[-1] + 1]
    last_end = quarters[-1][0]
    hours = {}
    for end, kwh, _ in quarters:
        hour_start = (end - QUARTER).replace(minute=0, second=0, microsecond=0)
        hours[hour_start] = hours.get(hour_start, 0.0) + kwh
    stats = []
    total = base_sum
    for hour_start, kwh in hours.items():
        if hour_start + HOUR > last_end:
            break
        total += kwh
        stats.append({"start": hour_start.isoformat(), "sum": round(total, 4)})
    return stats


def _normalize_ha_state(value):
    if value is None:
        return "unknown"
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return "unknown"
    if isinstance(value, datetime.timedelta):
        value = str(value)
    s = str(value)
    s = " ".join(s.replace("\xa0", " ").split())
    return s[:255]  # HA hard limit


class HomeAssistant:
    """Websocket API pro statistiky. V add-onu přes Supervisor proxy, mimo HA přes HA_URL a HA_TOKEN."""

    def __init__(self):
        token = os.environ.get("SUPERVISOR_TOKEN")
        if token:
            self.base = "http://supervisor/core/api"
            self.ws_url = "ws://supervisor/core/websocket"
        else:
            token = os.environ.get("HA_TOKEN")
            self.base = os.environ.get("HA_URL", "").rstrip("/")
            if not token or not self.base:
                raise RuntimeError("Chybí SUPERVISOR_TOKEN nebo HA_URL a HA_TOKEN")
            self.ws_url = self.base.replace("http", "ws", 1) + "/websocket"
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def ws(self, *commands):
        """Pošle příkazy přes websocket API a vrátí jejich výsledky (statistiky nemají REST API)."""
        conn = websocket.create_connection(self.ws_url, timeout=120)
        try:
            conn.recv()  # auth_required
            conn.send(json.dumps({"type": "auth", "access_token": self.token}))
            auth = json.loads(conn.recv())
            if auth.get("type") != "auth_ok":
                raise RuntimeError(f"Websocket auth failed: {auth}")
            results = []
            for msg_id, command in enumerate(commands, 1):
                conn.send(json.dumps({"id": msg_id, **command}))
                while True:
                    msg = json.loads(conn.recv())
                    if msg.get("id") == msg_id and msg.get("type") == "result":
                        break
                if not msg.get("success"):
                    raise RuntimeError(f"{command['type']}: {msg.get('error')}")
                results.append(msg.get("result"))
            return results
        finally:
            conn.close()

    def last_sum_before(self, statistic_id, before):
        """Poslední kumulativní suma statistiky před daným časem (0, pokud žádná není)."""
        [result] = self.ws({
            "type": "recorder/statistics_during_period",
            "start_time": "2000-01-01T00:00:00+00:00",
            "end_time": before.isoformat(),
            "statistic_ids": [statistic_id],
            "period": "month",
            "types": ["sum"],
        })
        rows = (result or {}).get(statistic_id) or []
        return (rows[-1].get("sum") or 0.0) if rows else 0.0

    def import_energy_statistics(self, statistic_id, name, stats):
        metadata = {
            "has_sum": True,
            "mean_type": 0,
            "name": name,
            "source": statistic_id.split(":", 1)[0],
            "statistic_id": statistic_id,
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        }
        self.ws(*({"type": "recorder/import_statistics", "metadata": metadata, "stats": stats[i:i + STATS_CHUNK]}
                  for i in range(0, len(stats), STATS_CHUNK)))


def mqtt_settings():
    """Přístup k brokeru od Supervisoru (services: mqtt:need), mimo HA z env MQTT_*."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if token:
        r = requests.get("http://supervisor/services/mqtt", headers={"Authorization": f"Bearer {token}"}, timeout=30)
        r.raise_for_status()
        return r.json()["data"]
    return {
        "host": os.environ.get("MQTT_HOST", "localhost"),
        "port": int(os.environ.get("MQTT_PORT", "1883")),
        "username": os.environ.get("MQTT_USER"),
        "password": os.environ.get("MQTT_PASSWORD"),
        "ssl": os.environ.get("MQTT_SSL", "").lower() == "true",
    }


class MqttPublisher:
    """Entity jednoho elektroměru přes MQTT discovery. Vše retained, takže entity přežijí restart HA."""

    def __init__(self, settings, elm, suffix):
        self.elm = elm
        self.suffix = suffix
        self.base = f"pnd/{elm}"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"pnd-addon-{elm}")
        try:
            if settings.get("username"):
                self.client.username_pw_set(settings["username"], settings.get("password"))
            if settings.get("ssl"):
                self.client.tls_set()
            # Když doplněk spadne uprostřed běhu, broker sám přepne "running" na off
            self.client.will_set(self.state_topic("running"), "off", qos=1, retain=True)
            self.client.connect(settings["host"], int(settings["port"]), keepalive=60)
            self.client.loop_start()
            for _ in range(100):
                if self.client.is_connected():
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError(f"Nepodařilo se připojit k MQTT brokeru {settings['host']}:{settings['port']}")
        except Exception:
            # Konstruktor nedoběhne, volající nemá co zavřít: zastavit vlákno smyčky tady
            self.close()
            raise

    def state_topic(self, key):
        return f"{self.base}/{key}/state"

    def _publish(self, topic, payload):
        info = self.client.publish(topic, payload, qos=1, retain=True)
        info.wait_for_publish(timeout=30)

    def publish_discovery(self):
        device = {
            "identifiers": [f"pnd_{self.elm}"],
            "name": f"PND ELM {self.elm}",
            "manufacturer": "ČEZ Distribuce",
            "model": "Portál naměřených dat",
            "sw_version": ver,
        }
        origin = {"name": "ČEZ Distribuce PND add-on", "sw_version": ver,
                  "support_url": "https://github.com/dansoutner/HomeAssistant-CEZDistribuce-PND"}
        for key, entity in ENTITIES.items():
            config = {k: v for k, v in entity.items() if k != "component"}
            config.update({
                "unique_id": f"pnd_{self.elm}_{key}",
                "default_entity_id": f"{entity['component']}.pnd_{key}{self.suffix}",
                "state_topic": self.state_topic(key),
                "json_attributes_topic": f"{self.base}/{key}/attributes",
                "device": device,
                "origin": origin,
            })
            self._publish(f"{DISCOVERY_PREFIX}/{entity['component']}/pnd_{self.elm}/{key}/config", json.dumps(config))

    def set_state(self, key, state, attributes=None):
        state = _normalize_ha_state(state)
        # Pro unknown ponecháme doslovný řetězec "unknown" ("None" by se v HA zobrazilo jako text)
        self._publish(self.state_topic(key), state)
        self._publish(f"{self.base}/{key}/attributes", json.dumps(attributes or {}, ensure_ascii=False))

    def close(self):
        """Odpojí klienta a zastaví vlákno smyčky; bezpečné volat i po chybě nebo opakovaně."""
        # Řádné odpojení = broker nepošle last will (running už je nastavené explicitně)
        try:
            self.client.disconnect()
        except Exception as e:
            log(f"MQTT disconnect failed: {e}", Colors.YELLOW)
        finally:
            self.client.loop_stop()


class PndRun:
    def __init__(self, ha, mqtt_config, meter):
        self.ha = ha
        self.mqtt_config = mqtt_config
        self.mqtt = None
        self.username = meter["username"]
        self.password = meter["password"]
        self.datainterval = meter["data_interval"]
        self.ELM = str(meter["elm"])
        # id jde do entity_id, statistic_id i názvu složky, proto slug
        self.id = slugify(meter.get("id") or "")
        self.suffix = f"_{self.id}" if self.id else ""
        if meter.get("id") and meter["id"] != self.id:
            log(f"id '{meter['id']}' upraveno na '{self.id}' (entity_id a statistic_id smí obsahovat jen a-z, 0-9 a _)", Colors.YELLOW)
        self.download_folder = os.path.join(OUTPUT_ROOT, f"pnd{self.suffix}")
        self.page = None

    # --- helpers -----------------------------------------------------------

    def set_state(self, key, state, attributes=None):
        """Stav entity z katalogu ENTITIES; atributy jsou jen ty dynamické (zbytek je v discovery)."""
        try:
            self.mqtt.set_state(key, state, attributes)
        except Exception as e:
            log(f"ERROR: Failed to publish state of {key}: {e}", Colors.RED)

    def x(self, xpath, root=None):
        return (root or self.page).locator(f"xpath={xpath}").first

    def screenshot(self, name):
        try:
            self.page.screenshot(path=os.path.join(self.download_folder, name), full_page=True)
        except Exception as e:
            log(f"Screenshot {name} failed: {e}", Colors.YELLOW)

    def click_body(self):
        try:
            self.page.locator("body").click(timeout=2000)
        except PlaywrightTimeout:
            pass

    def download_csv(self, link_text, filename, phase, timeout=10000):
        """Otevře menu 'Exportovat data', klikne na CSV a uloží stažený soubor."""
        try:
            toggle_button = self.x("//button[contains(text(), 'Exportovat data')]")
            toggle_button.wait_for(state="visible", timeout=timeout)
            time.sleep(1)  # tabulka se po výběru profilu ještě překresluje
            toggle_button.click(timeout=timeout)
            log(f"Downloading CSV file for {link_text}")
            with self.page.expect_download(timeout=max(timeout, 30000)) as download_info:
                self.x("//a[normalize-space()='CSV']").click()
            path = os.path.join(self.download_folder, filename)
            download_info.value.save_as(path)
        except Exception as e:
            log(f"ERROR: Failed to download CSV file for {link_text}: {e}", Colors.RED)
            raise PndError(f"ERROR: Nepodařilo se stáhnout CSV soubor pro {phase} export {link_text}")
        log(f"File downloaded and saved as: {path} {round(os.path.getsize(path)/1024, 2)} KB", Colors.GREEN)

    def open_profile(self, link_text, phase, prefix, hover=False, timeout=10000):
        """Klikne na odkaz profilu (01/02/07/08) v prvním okně PND. Timeout pokrývá i načítání předchozího profilu."""
        log(f"Selecting {link_text}")
        try:
            link = self.x(f".//a[contains(text(), '{link_text}')]", self.first_pnd_window)
            link.wait_for(state="visible", timeout=timeout)
            log(link.inner_text())
            if hover:
                link.hover(timeout=timeout)
            time.sleep(1)
            self.screenshot(f"{prefix}a.png")
            link.click(timeout=timeout)
            self.screenshot(f"{prefix}b.png")
            time.sleep(1)
            self.click_body()
            self.screenshot(f"{prefix}c.png")
        except PlaywrightTimeout:
            log(f"ERROR: Failed to find link {link_text}", Colors.RED)
            raise PndError(f"ERROR: Nepodařilo se najít odkaz pro {phase} export {link_text}")

    def select_period(self, option):
        dropdown_label = self.x("//label[contains(text(), 'Období')]")
        dropdown_label.wait_for(state="visible", timeout=10000)
        self.x("//label[contains(text(), 'Období')]/following-sibling::div//div[contains(@class, 'multiselect__select')]").click()
        self.x(f"//span[contains(text(), '{option}') and contains(@class, 'multiselect__option')]").click(timeout=5000)

    # --- main flow ---------------------------------------------------------

    def run(self):
        log(f"********************* Starting {ver}{self.suffix} *********************", Colors.CYAN)
        self.mqtt = None
        try:
            self.mqtt = MqttPublisher(self.mqtt_config, self.ELM, self.suffix)
            self.mqtt.publish_discovery()
            log(f"MQTT discovery published for ELM {self.ELM}")
            return self._run()
        except Exception as e:
            # Chyba MQTT (bez brokeru nejde stav nikam zapsat) nebo nečekaná chyba běhu:
            # zbývá jen log doplňku, ostatní elektroměry se zpracují dál
            log(f"ERROR: {type(e).__name__}: {e}", Colors.RED)
            return False
        finally:
            # Úklid na všech cestách, i když selže publish_discovery() po úspěšném připojení
            if self.mqtt is not None:
                self.mqtt.close()
                self.mqtt = None

    def _run(self):
        script_start_time = dt.now()
        self.set_state("running", state="on")
        self.set_state("script_status", state="Running", attributes={"status": "OK"})
        os.makedirs(self.download_folder, exist_ok=True)
        delete_folder_contents(self.download_folder)

        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=True)
                except Exception as e:
                    log(f"ERROR: Unable to launch Chromium: {e}", Colors.RED)
                    raise PndError("ERROR: Nepodařilo se spustit prohlížeč Chromium")
                log(f"Chromium {browser.version} launched successfully")
                try:
                    context = browser.new_context(viewport={"width": 1920, "height": 1080}, accept_downloads=True)
                    context.set_default_timeout(10000)
                    self.page = context.new_page()
                    self.scrape()
                except Exception:
                    self.screenshot("error.png")
                    raise
                finally:
                    browser.close()
            log("All Done - BROWSER CLOSED")
        except Exception as e:
            status = str(e) if isinstance(e, PndError) else f"ERROR: {type(e).__name__}: {e}"
            log(status, Colors.RED)
            self.set_state("running", state="off")
            self.set_state("script_status", state="Error", attributes={"status": status[:255]})
            self.zip_debug()
            return False

        self.set_state("running", state="off")
        log("Sensor State Set to OFF")
        self.zip_debug()
        script_duration = dt.now() - script_start_time
        self.set_state("script_duration", state=script_duration)
        self.set_state("script_status", state="Stopped", attributes={"status": "Finished"})
        log(f"********************* Duration: {script_duration} *********************", Colors.CYAN)
        log(f"********************* Finished {ver}{self.suffix} *********************", Colors.CYAN)
        return True

    def zip_debug(self):
        try:
            zip_folder(self.download_folder, os.path.join(self.download_folder, "debug.zip"))
            log("Debug Files Zipped")
        except Exception as e:
            log(f"Failed to zip debug files: {e}", Colors.YELLOW)

    def scrape(self):
        page = self.page
        try:
            log(f"Opening Website: {PNDURL}")
            page.goto(PNDURL, wait_until="load", timeout=60000)
            log("Website Opened")
        except Exception:
            log("ERROR: Unable to open website - exiting", Colors.RED)
            raise PndError("ERROR: Nepodařilo se otevřít webovou stránku PND portálu")
        log(f"Current URL: {page.url}")

        # Cookie banner
        try:
            page.locator("#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowallSelection").click(timeout=3000)
        except PlaywrightTimeout:
            log("No cookie banner found")

        # Login
        try:
            page.get_by_placeholder("Zadejte svůj e-mail").fill(self.username)
            page.get_by_placeholder("Zadejte své heslo").fill(self.password)
            login_button = self.x("//button[@type='submit' and contains(@class, 'mui-btn--primary')]")
            log("Login button found, clicking it")
            self.screenshot("00.png")
            login_button.click()
        except PlaywrightTimeout:
            log("ERROR: Failed to enter login details or find and click the login button", Colors.RED)
            raise PndError("ERROR: Nepodařilo se vyplnit přihlašovací údaje nebo najít a kliknout na tlačítko pro přihlášení")

        h1_text = "Naměřená data"
        try:
            self.x(f"//h1[contains(text(), '{h1_text}')]").wait_for(timeout=30000)
        except PlaywrightTimeout:
            try:
                alert_widget_content = page.locator(".alertWidget__content").first.inner_text(timeout=2000)
                log(f"ERROR: {alert_widget_content}", Colors.RED)
            except PlaywrightTimeout:
                log(f"ERROR: H1 tag with text '{h1_text}' is not found.", Colors.RED)
            raise PndError("ERROR: Není možné se přihlásit do aplikace")
        log(f"Current URL: {page.url}")
        self.screenshot("01.png")
        log(f"H1 tag with text '{h1_text}' is present.")

        # Modal dialog (obvykle info o nedostupnosti portálu)
        modal_dialog = page.locator(".modal-dialog").first
        if modal_dialog.count():
            log("Modal Dialog found", Colors.YELLOW)
            try:
                self.screenshot("01-modal.png")
                log("Closing Modal Dialog", Colors.YELLOW)
                self.x(".//button[contains(@class, 'btn pnd-btn btn-primary') and contains(text(), 'Přečteno')]", modal_dialog).click()
                time.sleep(2)
                page.reload()
                log("Modal Dialog closed successfully, page reloaded", Colors.GREEN)
            except PlaywrightTimeout:
                log("ERROR: Close button not found in the modal dialog.", Colors.RED)
        else:
            log("Modal dialog not found. Continuing without closing modal.", Colors.GREEN)

        # App version
        version_element = self.x("//div[contains(text(), 'Verze aplikace:')]")
        version_text = (version_element.text_content() or "").replace("\xa0", " ")
        parts = version_text.split(":", 1)
        version_number = (parts[1].strip() if len(parts) > 1 else version_text.strip()) or "unknown"
        self.set_state("app_version", state=version_number)
        log(f"App Version: {version_number}")

        self.first_pnd_window = page.locator(".pnd-window").first
        self.first_pnd_window.wait_for(state="visible", timeout=20000)
        self.x(".//button[@title='Export']", self.first_pnd_window).click()
        self.screenshot("02.png")

        # Sestava -> Rychlá sestava
        option_text = "Rychlá sestava"
        for _ in range(10):
            self.x("//label[contains(text(), 'Sestava')]/following-sibling::div//div[contains(@class, 'multiselect__tags')]").click(timeout=2000)
            self.x(f"//span[contains(text(), '{option_text}')]").click(timeout=2000)
            self.click_body()
            try:
                self.x("//span[@class='multiselect__single']").filter(has_text=option_text).wait_for(timeout=2000)
                break
            except PlaywrightTimeout:
                continue
        else:
            log("ERROR: Rychla Sestava neni mozne vybrat!", Colors.RED)
            raise PndError("ERROR: Nebylo možné vybrat 'Rychlá sestava' po 10 pokusech. Zkuste skript spustit později znovu.")
        log("Rychla Sestava selected successfully!", Colors.GREEN)
        self.screenshot("03.png")
        time.sleep(1)

        # Množina zařízení -> ELM
        log(f"Selecting ELM '{self.ELM}'")
        # Pozn.: portál přepisuje JS builtiny, takže nelze používat evaluate()/all_inner_texts() (běží v kontextu stránky)
        elm_values = [t for t in (o.text_content().strip() for o in page.locator("span.multiselect__option").all()) if t.startswith("ELM")]
        log(f"Valid ELM numbers '{', '.join(elm_values)}'")

        elm_label_xpath = "//label[contains(text(), 'Množina zařízení')]"
        form_group = self.x(f"{elm_label_xpath}/ancestor::div[contains(@class, 'form-group')]")
        debug_elm = os.path.join(self.download_folder, "debug-ELM.txt")
        with open(debug_elm, "w") as file:
            file.write(">>>Debug ELM<<<\n")
            file.write(form_group.inner_html() + "\n")
        dropdown = self.x(f"{elm_label_xpath}/following-sibling::div//div[contains(@class, 'multiselect__select')]")

        for i in range(10):
            dropdown.click(timeout=2000)
            time.sleep(1)
            self.screenshot(f"03-{i}-a.png")
            try:
                self.x(f"//span[contains(text(), '{self.ELM}')]").click(timeout=2000)
            except PlaywrightTimeout:
                log(f"ERROR: Failed to find '{self.ELM}' in the selection - check ELM option of the add-on", Colors.RED)
                raise PndError(f"ERROR: Nebylo možné najít '{self.ELM}' v nabídce. Zkontrolujte ELM v nastavení doplňku.")
            self.screenshot(f"03-{i}-b.png")
            self.click_body()
            class_attribute = self.x("//button[contains(., 'Vyhledat data')]").get_attribute("class") or ""
            single = form_group.locator("xpath=.//span[@class='multiselect__single']").first
            span = single.inner_text() if single.count() else ""
            log(f"ELM Status: {span} - {self.ELM}", Colors.CYAN)
            with open(debug_elm, "a") as file:
                file.write(f">>>Iteration {i}<<<\n")
                file.write("ELM Span content: " + span + "\n")
                file.write(form_group.inner_html() + "\n")
            if 'disabled' not in class_attribute and span.strip() != '':
                log(f"Iteration {i}: Vyhledat Button NOT disabled", Colors.GREEN)
                break
            log(f"Iteration {i}: Vyhledat Button IS disabled", Colors.YELLOW)
        else:
            log(f"ERROR: Failed to find '{self.ELM}' after 10 attempts", Colors.RED)
            raise PndError(f"ERROR: Nebylo možné najít '{self.ELM}' po 10 pokusech. Zkontrolujte ELM v nastavení doplňku.")
        log(f"Device ELM '{self.ELM}' selected successfully!", Colors.GREEN)
        self.screenshot("04.png")

        # Období -> Včera
        try:
            self.select_period("Včera")
        except PlaywrightTimeout:
            log("ERROR: Failed to select 'Včera' in the dropdown", Colors.RED)
            raise PndError("ERROR: Nepodařilo se vybrat 'Včera' v nabídce")
        self.screenshot("05.png")

        try:
            self.x("//button[contains(., 'Vyhledat data')]").click()
            log("Button 'Vyhledat data' clicked successfully!", Colors.GREEN)
        except PlaywrightTimeout as e:
            log(f"Failed to find or click the 'Vyhledat data' button: {e}", Colors.RED)
            raise PndError("ERROR: Nepodařilo se nalézt nebo kliknout na tlačítko 'Vyhledat data'")
        self.screenshot("06.png")
        time.sleep(2)
        self.click_body()
        self.screenshot("07.png")

        # ------------------ DAILY -----------------------------
        # 15min profily (01/02) jako první: portál po přepnutí zobrazení znovu načítá naposledy otevřený
        # profil, a to má být lehký denní 08, ne 15min data za celý interval.
        link_text = "01 Profil spotřeby (+A)"
        self.open_profile(link_text, "denní", "daily-body-01")
        self.download_csv(link_text, "daily-consumption-15min.csv", "denní")
        link_text = "02 Profil výroby (-A)"
        self.open_profile(link_text, "denní", "daily-body-02")
        self.download_csv(link_text, "daily-production-15min.csv", "denní")

        link_text = "07 Profil spotřeby za den (+A)"
        self.open_profile(link_text, "denní", "daily-body-07")
        self.download_csv(link_text, "daily-consumption.csv", "denní")

        self.screenshot("08.png")
        link_text = "08 Profil výroby za den (-A)"
        self.open_profile(link_text, "denní", "daily-body-08")
        self.download_csv(link_text, "daily-production.csv", "denní")
        log("All Done - DAILY DATA DOWNLOADED")

        # Poslední řádek = včerejší den
        date_consumption_str, consumption_value = read_profile_csv(os.path.join(self.download_folder, 'daily-consumption.csv'))[-1]
        date_production_str, production_value = read_profile_csv(os.path.join(self.download_folder, 'daily-production.csv'))[-1]
        yesterday_consumption = conv_date(date_consumption_str) - datetime.timedelta(days=1)
        yesterday_production = conv_date(date_production_str) - datetime.timedelta(days=1)

        log(f"Latest entry: {date_consumption_str} - {consumption_value} kWh", Colors.GREEN)
        log(f"Latest entry: {date_production_str} - {production_value} kWh", Colors.GREEN)

        self.set_state("consumption", state=consumption_value, attributes={"date": yesterday_consumption.isoformat()})
        self.set_state("production", state=production_value, attributes={"date": yesterday_production.isoformat()})
        for kind in ("consumption", "production"):
            quarters = read_15min_csv(os.path.join(self.download_folder, f"daily-{kind}-15min.csv"))
            self.set_state(f"{kind}_15min", state=f"{sum(q[1] for q in quarters):.3f}", attributes={
                "date": (quarters[0][0] - QUARTER).astimezone(PND_TZ).date().isoformat() if quarters else None,
                "pndtime": [(end - QUARTER).astimezone(PND_TZ).isoformat() for end, _, _ in quarters],
                kind: [round(kwh, 4) for _, kwh, _ in quarters],
            })
        log("All Done - DAILY DATA PROCESSED")

        # ------------------ INTERVAL -----------------------------
        try:
            self.select_period("Vlastní")
            input_field = self.x("//label[contains(text(), 'Vlastní období')]/following::input[1]")
            input_field.fill("")
            input_field.press_sequentially(self.datainterval)
            input_field.press("Tab")
            self.click_body()
        except PlaywrightTimeout:
            log("ERROR: Failed to select 'Vlastní období' in the dropdown", Colors.RED)
            raise PndError("ERROR: Nepodařilo se vybrat 'Vlastní období' v nabídce")
        log(f"Data Interval Entered - '{self.datainterval}'")
        time.sleep(1)

        try:
            self.x("//button[@title='Tabulka dat']").click()
            time.sleep(1)
            self.x("//button[@title='Export']").click()
            self.click_body()
        except PlaywrightTimeout:
            log("ERROR: Failed to click 'Tabulka dat' button", Colors.RED)
            raise PndError("ERROR: Nepodařilo se kliknout na tlačítko 'Tabulka dat'")

        link_text = "07 Profil spotřeby za den (+A)"
        self.open_profile(link_text, "interval", "interval-body-07", hover=True)
        log("Exporting data")
        self.download_csv(link_text, "range-consumption.csv", "interval")

        link_text = "08 Profil výroby za den (-A)"
        self.open_profile(link_text, "interval", "interval-body-08", hover=True)
        log("Exporting data")
        self.download_csv(link_text, "range-production.csv", "interval")

        # 15min data za celý interval se načítají dlouho (rok = ~35 000 řádků)
        link_text = "01 Profil spotřeby (+A)"
        self.open_profile(link_text, "interval", "interval-body-01", hover=True)
        self.download_csv(link_text, "range-consumption-15min.csv", "interval", timeout=180000)
        link_text = "02 Profil výroby (-A)"
        self.open_profile(link_text, "interval", "interval-body-02", hover=True, timeout=180000)
        self.download_csv(link_text, "range-production-15min.csv", "interval", timeout=180000)
        log("All Done - INTERVAL DATA DOWNLOADED")

        data_consumption = read_profile_csv(os.path.join(self.download_folder, 'range-consumption.csv'))
        data_production = read_profile_csv(os.path.join(self.download_folder, 'range-production.csv'))

        date_str = [conv_date(d).date().isoformat() for d, _ in data_consumption]
        consumption_str = [str(v) for _, v in data_consumption]
        production_str = [str(v) for _, v in data_production]

        self.set_state("data", state=dt.now().strftime("%Y-%m-%d %H:%M:%S"), attributes={
            "pnddate": date_str, "consumption": consumption_str, "production": production_str
        })
        total_consumption = "{:.2f}".format(sum(v for _, v in data_consumption))
        total_production = "{:.2f}".format(sum(v for _, v in data_production))
        self.set_state("total_interval_consumption", state=total_consumption)
        self.set_state("total_interval_production", state=total_production)
        try:
            float_total_consumption = float(total_consumption)
            float_total_production = float(total_production)
            if float_total_consumption > 0:
                percentage_diff = round((float_total_production / float_total_consumption) * 100, 2)
            else:
                percentage_diff = 0
        except Exception:
            percentage_diff = 0
        capped_percentage_diff = round(min(float(percentage_diff), 100), 2)
        floored_min_percentage_diff = round(max(float(percentage_diff) - 100, 0), 2)

        self.set_state("production2consumption", state=str(capped_percentage_diff))
        self.set_state("production2consumptionfull", state=str(percentage_diff))
        self.set_state("production2consumptionfloor", state=str(floored_min_percentage_diff))
        log("All Done - INTERVAL DATA PROCESSED")
        self.import_statistics()

    def import_statistics(self):
        """Nahraje 15min data celého intervalu jako hodinové externí statistiky (Energy dashboard)."""
        for kind, name in (("consumption", "PND Consumption"), ("production", "PND Production")):
            statistic_id = f"pnd:{kind}{self.suffix}"
            quarters = read_15min_csv(os.path.join(self.download_folder, f"range-{kind}-15min.csv"))
            try:
                first_hour = (quarters[0][0] - QUARTER).replace(minute=0) if quarters else None
                base = self.ha.last_sum_before(statistic_id, first_hour) if first_hour else 0.0
                stats = hourly_statistics(quarters, base)
                if stats:
                    self.ha.import_energy_statistics(statistic_id, name, stats)
            except Exception as e:
                log(f"ERROR: Failed to import statistics {statistic_id}: {e}", Colors.RED)
                raise PndError(f"ERROR: Nepodařilo se importovat statistiku {statistic_id} do Home Assistanta")
            log(f"Statistics {statistic_id}: {len(stats)} hours imported, base {base:.3f}, "
                f"last sum {stats[-1]['sum'] if stats else '-'} kWh", Colors.GREEN)
        log("All Done - STATISTICS IMPORTED")


def main():
    log(f">>>>>>>>>>>> PND {ver}")
    log(f"Platform: {platform.platform()}, Python {platform.python_version()}, Architecture: {platform.machine()}")
    with open(OPTIONS_FILE) as f:
        options = json.load(f)
    meters = options.get("meters") or []
    if not meters:
        log("ERROR: V nastavení doplňku není vyplněn žádný elektroměr (meters)", Colors.RED)
        return 1
    ha = HomeAssistant()
    try:
        mqtt_config = mqtt_settings()
    except Exception as e:
        log(f"ERROR: Nepodařilo se zjistit přístup k MQTT brokeru (je nainstalovaný Mosquitto?): {e}", Colors.RED)
        return 1
    ok = True
    for meter in meters:
        ok = PndRun(ha, mqtt_config, meter).run() and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
