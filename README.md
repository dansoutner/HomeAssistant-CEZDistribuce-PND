# Home Assistant ČEZ Distribuce Portál Naměřených Dat

> [!IMPORTANT]
> 🎉 **Verze v2.0.0: AppDaemon už není potřeba!** 🎉
> Skript nově běží jako samostatný doplněk (add-on) Home Assistanta s prohlížečem Playwright/Chromium. Odpadá ruční instalace systémových balíčků, token a konfigurace AppDaemonu.
> * **Stávající uživatelé:** viz [Migrace z AppDaemon verze](#migrace-z-appdaemon-verze-v1x). Entity zůstávají stejné, dashboardy není nutné měnit.
> * **Požadavky:** Home Assistant OS nebo Supervised (doplňky) na architektuře amd64 nebo aarch64 (např. Raspberry Pi 4/5 s 64bit OS). 32bit armv7 Playwright nepodporuje. Od verze 2.1.0 je potřeba MQTT broker (doplněk **Mosquitto broker**) a integrace **MQTT**.
>
> **v2.1.0:** senzory se publikují přes MQTT discovery, takže **přežijí restart Home Assistanta** a není nutné spouštět doplněk po startu HA. Viz [Migrace na 2.1.0](#migrace-na-210).

Script a nastavení Home Assistant slouží pro vyčítání dat o spotřebě a výrobě elektřiny z distribučního portálu https://www.cezdistribuce.cz/ v denních úhrnech.

Pokud se vám řešení líbí, můžete mne podpořit v další tvorbě a rozvoji - za což vám předem děkuji :)

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/ondrejv)


Po správném nastavení a spuštění doplňku vznikne v Home Assistant zařízení **PND ELM &lt;číslo elektroměru&gt;** (integrace MQTT) s těmito senzory. Entity mají `unique_id`, dají se tedy v UI přejmenovat, přiřadit do oblasti apod., a díky MQTT retain přežijí restart Home Assistanta:

* **sensor.pnd_data** (obsahujíc data výroby a spotřeby za vámi zvolený interval - např období vyúčtování)
* **sensor.pnd_consumption** a **sensor.pnd_production** v KWh je to den zpětně souhrn za den (data se vyčítají po půlnoci za den zpětně)
* **sensor.pnd_total_interval_consumption** resp **sensor.pnd_total_interval_production** v KWh součet za období
* **sensor.pnd_production2consumption** poměr mezi výrobou a spotřebou s max hodnotou 100% (např. jako indikátor Virtuální Baterie)
* **sensor.pnd_production2consumptionfull** stejný jako předchozí, bez omezení na 100%
* **sensor.pnd_production2consumptionfloor** rezerva virtuální baterie, tj cokoliv co je nad 100% s minimem 0% pokud je poměr pod 100%
* **binary_sensor.pnd_running** kontrolní senzor, který se zapne při spuštění a vypne po dokončení (i při chybě nebo pádu doplňku)
* **sensor.pnd_script_duration** kontrolní senzor obsahující délku běhu skriptu (čas se nezapíše pokud se vyskytne chyba)
* **sensor.pnd_script_status** kontrolní senzor obsahující stav skriptu:
  * Stav: Running - atribut Status: OK
  * Stav: Stopped - atribut Status: Finished
  * Stav: Error - atribut Status: chyba, kde se skript zastavil
* **sensor.pnd_app_version** senzor s verzí aplikace PND
* **sensor.pnd_consumption_15min** resp **sensor.pnd_production_15min** v kWh souhrn za včerejší den, v atributech `pndtime` (začátek čtvrthodiny) a `consumption` resp `production` 96 čtvrthodinových hodnot v kWh (vhodné pro ApexCharts)

Kromě senzorů doplněk nahrává 15minutová data za celý interval (`data_interval`) jako **dlouhodobé statistiky** `pnd:consumption` a `pnd:production` (s `id` např. `pnd:consumption_chata`). Home Assistant ukládá statistiky po hodinách, čtvrthodiny se proto sčítají do hodinových kWh. Statistiky lze přidat do **Energy dashboardu** (Nastavení > Energie > Spotřeba ze sítě / Vrácení do sítě > vyhledat "PND Consumption" resp. "PND Production") včetně celé historie z intervalu. Při každém běhu se data za interval přepíšou, takže opravy dat na portále se promítnou zpětně.

### Náklady podle spotové ceny (volitelné)
Pokud máte v HA senzor s aktuální spotovou cenou elektřiny (např. `sensor.current_spot_electricity_price_15min` z integrace spotových cen), vyplňte v nastavení doplňku volbu **spot_price_entity**:
```yaml
spot_price_entity: sensor.current_spot_electricity_price_15min
```
Doplněk pak ke každé čtvrthodině odběru přiřadí spotovou cenu téže čtvrthodiny a vytvoří:
* **sensor.pnd_consumption_cost** – náklady za poslední den v CZK, v atributech `average_price` (vážená průměrná cena v Kč/kWh), `consumption_kwh` a hodinové náklady (`hours` a `cost`)
* **dlouhodobou statistiku `pnd:consumption_cost`** (CZK, po hodinách). V Energy dashboardu ji přiřadíte k odběru ze sítě: Nastavení > Energie > Spotřeba ze sítě > "PND Consumption" > **Použít entitu sledující celkové náklady** > "PND Consumption cost".

Poznámky:
* Počítá se jen **cena silové elektřiny** podle zadaného senzoru, bez distribuce, poplatků a přirážky obchodníka. Zda obsahuje DPH, záleží na nastavení vaší integrace spotových cen.
* Ceny se berou z **historie stavů** senzoru (atributy obvykle obsahují jen dnešek a zítřek). Home Assistant drží historii ve výchozím nastavení 10 dní, náklady tedy nejde dopočítat zpětně za celý `data_interval`, ale jen za dny, které jsou v historii. Statistika se při každém běhu přepočítá za tyto dny a naváže na předchozí sumu.
* Senzor ceny může být v Kč/kWh i Kč/MWh, jednotka se převede automaticky.
* Pokud historie cen chybí, běh neskončí chybou; `sensor.pnd_consumption_cost` bude `unknown` s popisem v atributu `error`.

Výsledkem pak může být například takovýto dashboard (návod na jeho výrobu je popsán níže)

![](/obrazky/00-prehled.png)

> [!CAUTION]
> **POZOR: Pokud již máte ve svém HA entity se stejnými názvy (např. šablony ze starého `sensors.yaml`), odstraňte je před spuštěním doplňku, jinak dostanou nové entity příponu `_2`.**

## Co je potřeba
1. Přihlášení do Distribučního Portálu
2. [HomeAssistant](#homeassistant) (OS nebo Supervised)
   - [MQTT broker](#mqtt-broker)
   - [Doplněk ČEZ Distribuce PND](#instalace-doplňku)
   - [Naplánování automatické aktualizace](#nastavení-automatické-aktualizace-dat)
   - [Řešení problémů](#%C5%99e%C5%A1en%C3%AD-probl%C3%A9m%C5%AF-se-skriptem)
   - [Migrace na 2.1.0](#migrace-na-210)
   - [Migrace z AppDaemon verze](#migrace-z-appdaemon-verze-v1x)
   - [Odinstalace](#odinstalace)
   - [ApexCharts Card](#instalace-apexcharts-card)
3. [Tvorba Dashboardu](#tvorba-dashboardu)
4. [Nápady a plány](#pl%C3%A1ny-a-n%C3%A1pady)
5. [Změny (Changelog)](#změny)
6. [Přispěvatelé a licence](#přispěvatelé)


## Distribuční portál
Zažádejte si o přihlášení do Distribučního Portálu na webu https://dip.cezdistribuce.cz/irj/portal/ obvykle vyřízeno do druhého dne.

Po přihlášení ověřte, že máte k dispozici váš elektroměr v sekci "Množina zařízení". V tuto chvíli script stahuje všechna data, tedy pokud máte více elektroměrů, nemusí script fungovat správně.

**Pozn.: Skript prozatím neumí správně pracovat s uživatelskými sestavami a více elektroměry.** Zvolte v portále "Rychlá sestava" a "Všechny EANy" nebo odpovídající elektroměr a odhlaste se z portálu.

![](/obrazky/01-pnd.png)

## HomeAssistant
Pokud toto čtete, více k čemu je HomeAssistant dobrý, pokud přeci ne, více na [stránkách projektu](https://www.home-assistant.io/). Doplněk vyžaduje instalaci s podporou doplňků (Home Assistant OS nebo Supervised).

### MQTT broker
Doplněk předává data do Home Assistanta přes MQTT. Pokud MQTT ještě nepoužíváte:
1. V "Nastavení" > "Doplňky" > "Obchod s doplňky" nainstalujte a spusťte doplněk **Mosquitto broker**.
2. V "Nastavení" > "Zařízení a služby" potvrďte nalezenou integraci **MQTT**.

Přihlašovací údaje k brokeru si doplněk PND převezme od Home Assistanta sám, nic dalšího se nenastavuje.

### Instalace doplňku
1. V HA zvolte "Nastavení" > "Doplňky" > "Obchod s doplňky"
2. Vpravo nahoře klikněte na tři tečky > "Repozitáře" a přidejte URL `https://github.com/dansoutner/HomeAssistant-CEZDistribuce-PND`
3. Obnovte stránku, v seznamu se objeví doplněk **ČEZ Distribuce PND**. Otevřete jej a klikněte na "Nainstalovat" (stáhne se hotový obraz, cca 230 MB).
4. Na záložce "Nastavení" vyplňte (přepněte do YAML režimu):
   * **username** je váš email s přihlášením do portálu
   * **password** je heslo pro přihlášení
   * **data_interval** je interval dat, které budete chtít stahovat. Doporučená hodnota je `last_365_days` (obecně `last_N_days`): při každém běhu se přepočítá na posledních N celých dní do dnešní půlnoci, takže se statistiky a Energy dashboard doplňují samy. Pevný interval ve tvaru `27.10.2023 00:00 - 27.10.2024 00:00` (např. období fixace smlouvy) funguje dál, ale po jeho konci se do statistik nové dny nedostanou. Nedoporučuji víc jak rok, mohlo by zahltit databázi.
   * **elm** je číslo (pouze číslo, nikoliv ELM 3000000000) vašeho elektroměru, který chcete sledovat v HA. Zjistíte jej v Portále - viz obrázek níže
   * **id** (volitelné) přípona entit, pokud sledujete více elektroměrů, např. `chata` → `sensor.pnd_consumption_chata`. Převede se na malá písmena bez diakritiky a mezer (`Chata Šumava` → `chata_sumava`), protože Home Assistant jiné znaky v `entity_id` ani ve statistikách nepovoluje.

![](/obrazky/pnd-cislo-elektromeru.png)

```yaml
meters:
  - username: "vas email s prihlasenim do portalu distribuce"
    password: "vase heslo do portalu distribuce"
    elm: "3000012345"
    data_interval: "last_365_days"
```
Pro více elektroměrů přidejte do seznamu `meters` další položku s vyplněným `id`. Elektroměry se zpracují postupně v jednom běhu.

5. Uložte. Doplněk nechte zastavený a **nezapínejte** "Spustit při startu" ani "Watchdog": doplněk se po stažení dat sám vypne.

Aktualizace doplňku hlídá Home Assistant sám a nabídne je stejně jako u ostatních doplňků.

> [!CAUTION]
> **POZOR: Data se neaktualizují sama od sebe, ale pouze automatickým nebo ručním spuštěním doplňku (viz níže)**, případně tlačítkem "Spustit" na stránce doplňku.

Při úspěšném chodu skriptu:
* jsou vytvořeny soubory ve složce /share/pnd
* je vypnut binární senzor pnd_running (pokud není tento senzor vypnut po cca 2 minutách, přejděte na [řešení problémů](#%C5%99e%C5%A1en%C3%AD-probl%C3%A9m%C5%AF-se-skriptem)

### Nastavení automatické aktualizace dat
Nejsnazší cestou je vytvoření automatizace, která doplněk v pravidelném čase spustí akcí `hassio.addon_start`.
1. Zjistěte slug doplňku: je to poslední část URL na stránce doplňku, např. `a1b2c3d4_pnd`
2. V Home Assistant zvolte "Nastavení" > "Automatizace a scény" a vytvořte novou automatizaci
   * spouštěč "Čas": data na portále jsou dostupná několik minut po půlnoci, můžete nastavit např. 00:30:00
   * akce "Home Assistant Supervisor: Spustit doplněk" a vyberte ČEZ Distribuce PND
3. Uložte automatizaci

Ověřte funkčnost nastavení > vpravo nahoře tři tečky > "Spustit"

Chod skriptu trvá cca 1 minutu, poté byste měli vidět odpovídající entity v HA.

YAML kód automatizace
```yaml
alias: Run PND
description: ""
triggers:
  - trigger: time
    at: "00:30:00"
conditions: []
actions:
  - action: hassio.addon_start
    data:
      addon: a1b2c3d4_pnd
mode: single
```

Spouštěč na start Home Assistanta není potřeba: senzory jsou uložené v MQTT brokeru a po restartu HA mají hned poslední hodnoty.

### Řešení problémů se skriptem
Nejprve zkuste spustit znovu, skript simuluje pohyb na webové stránce a není garantováno, že stránka bude vždy stejná a skript doběhne úspěšně dokonce.

Pokud se vyskytne problém (např data se nestahují):
* Na stránce doplňku je záložka "Log", zobrazí kde přesně skript selhal (a stav najdete i v atributu `status` senzoru `sensor.pnd_script_status`) - **přidejte tento log do problému zde na GITu nebo v osobní komunikaci (na FB posílejte otisk obrazovky)**
* Po každém běhu (i neúspěšném) je vytvořený soubor /share/pnd/debug.zip se screenshoty a staženými daty. Soubor neobsahuje přihlašovací údaje - **při řešení problémů připojte tento soubor.** Ke složce /share se dostanete např. doplňkem File Editor nebo Samba.

#### Časté problémy
* Postupoval jsem dle návodu, ale entity se neobjevily: Řešení - vytvořili jste automatizaci pro spuštění doplňku? Pokud ještě neuplynul čas do spuštění, spusťte automatizaci ručně
* Doplněk nejde nainstalovat: zkontrolujte architekturu (Nastavení > Systém > Opravy > tři tečky > Informace o systému). Podporováno je jen amd64 a aarch64. Dále je nutný nainstalovaný [MQTT broker](#mqtt-broker).
* Entity mají příponu `_2` (např. `sensor.pnd_consumption_2`): při prvním spuštění 2.1.x v HA ještě existovala stará entita nebo stav se stejným názvem (šablona ze `sensors.yaml`, nebo stav zapsaný starší verzí doplňku či AppDaemonem, který zmizí až restartem HA). Odstraňte šablony, restartujte HA a pak v "Nastavení" > "Entity" přejmenujte entity `…_2` zpět na původní názvy.

### Migrace na 2.1.0
Senzory se nově vytvářejí přes MQTT discovery místo přímého zápisu stavů. `entity_id` zůstanou stejná, **pokud v HA v okamžiku prvního spuštění 2.1.x neexistuje entita ani stav se stejným názvem** (viz krok 3). Dashboardy a historie pak fungují dál. Zobrazované názvy se změní na „PND ELM &lt;číslo&gt; …“ a dají se přejmenovat v UI.
1. Nainstalujte [MQTT broker](#mqtt-broker), pokud jej ještě nemáte.
2. Pokud máte v konfiguraci šablony ze souboru `sensors.yaml` (PND Data, PND Consumption, …), odstraňte je.
3. Aktualizujte doplněk a **před jeho prvním spuštěním restartujte Home Assistant**. Stavy zapsané starší verzí doplňku nebo AppDaemonem nejsou v registru entit, ale do restartu obsazují názvy entit, takže by nové entity dostaly příponu `_2`.
4. Z automatizace odstraňte spouštěč na start Home Assistanta (`trigger: homeassistant`, `event: start`) i případnou prodlevu. Už není potřeba.
5. Spusťte doplněk.

### Migrace z AppDaemon verze (v1.x)
1. Nainstalujte a nastavte doplněk dle [návodu výše](#instalace-doplňku). Hodnoty převezměte z `apps.yaml`: `PNDUserName` → `username`, `PNDUserPassword` → `password`, `ELM` → `elm`, `DataInterval` → `data_interval`, `id` → `id`.
2. V automatizaci nahraďte akci `event: run_pnd` akcí `hassio.addon_start` (viz výše). Spouštěče `APPDAEMON_READY` a start Home Assistanta smažte.
3. V `apps.yaml` odstraňte sekce `pnd` a `init_helper`, v HACS odinstalujte "CEZ Distribuce PND". Pokud AppDaemon nepoužíváte pro nic jiného, můžete jej odinstalovat.
4. Proveďte také kroky z [Migrace na 2.1.0](#migrace-na-210) (MQTT broker, odstranění `sensors.yaml`, restart HA před prvním spuštěním).

Při dodržení těchto kroků `entity_id` zůstanou stejná a dashboardy není nutné měnit.

### Odinstalace
Entity jsou uložené v MQTT brokeru (retain), po odinstalaci doplňku proto v HA zůstanou. Odstraníte je smazáním zařízení **PND ELM &lt;číslo&gt;** v "Nastavení" > "Zařízení a služby" > "MQTT", případně vyčištěním topiců v brokeru:
```
mosquitto_sub -h <broker> -u <uživatel> -P <heslo> -t 'homeassistant/+/pnd_<elm>/#' -t 'pnd/<elm>/#' --remove-retained -W 2
```

### Instalace ApexCharts Card
Postup instalace je uvedený na [stránkách projektu](https://github.com/RomRider/apexcharts-card)

## Tvorba Dashboardu
Cílem návodu není do detailu popisovat jak v Home Assistant vytvářet dashboardy, níže uvádím ukázky grafů, které lze s výše získaných dat vytvořit. Pokud vytvoříte nějaký super graf, přidejte kód zde na Gitu.

Pokud jste postupovali dle návodu a máte data v Home Assistantu, pak stačí vytvořit novou "Manuální kartu" a do ní zkopírovat kód jednotlivých karet níže.

### PND Včerejší stav spotřeby/výroby
Využívá senzory _sensor.pnd_consumption_ a _sensor.pnd_production_ které obsahují denní spotřebu resp výrobu za **předchozí den**. Senzory jsou třídy (device_class) energy a jsou tedy automaticky ukládány do dlouhodobých dat v HomeAssistant
```
type: custom:apexcharts-card
stacked: true
graph_span: 7d
span:
  end: day
header:
  show: true
  title: PND Včerejší stav
series:
  - entity: sensor.pnd_consumption
    name: Spotřeba
    color: var(--error-color)
    opacity: 0.8
    invert: true
    type: column
    group_by:
      func: last
      duration: 1d
  - entity: sensor.pnd_production
    name: Výroba
    color: var(--success-color)
    opacity: 0.8
    type: column
    group_by:
      func: last
      duration: 1d
```
![](/obrazky/pnd-vcerejsi-stav.png)

### Přehled celkové výroby / spotřeby
Používá kartu rychlý náhled. Jsou využita data ze senzorů _sensor.pnd_total_interval_consumption_ resp _sensor.pnd_total_interval_production_

```
show_name: true
show_icon: true
show_state: true
type: glance
entities:
  - entity: sensor.pnd_total_interval_consumption
    name: Spotřeba za Období
  - entity: sensor.pnd_total_interval_production
    name: Výroba za Období
  - entity: sensor.pnd_production2consumption
    name: Využití VB
  - entity: sensor.pnd_production2consumptionfull
    name: Výroba/Spotřeba
  - entity: sensor.pnd_production2consumptionfloor
    name: Rezerva VB
state_color: false
title: Celkový přehled
```
![](/obrazky/pnd-celkem-nahled.png)

### Stav využití Virtuální Baterie
Jedná se o poměr mezi výrobou a spotřebou, maximální hodnota je 100 (tj pokud je výroba větší nežli spotřeba, bude hodnota 100%). Využívá standardní kartu Home Assistanta

```
type: gauge
entity: sensor.pnd_production2consumption
name: Využití Virtuální Baterie
min: 0
max: 100
needle: false
```
![](/obrazky/pnd-pomer.png)

### Přehled celkové výroby / spotřeby v koláčovém grafu
Jsou využita data ze senzorů _sensor.pnd_total_interval_consumption_ resp _sensor.pnd_total_interval_production_
```
type: custom:apexcharts-card
chart_type: donut
header:
  show: true
  title: PND Shrnutí Období
apex_config:
  plotOptions:
    pie:
      donut:
        total:
          show: true
          showAlways: true
series:
  - entity: sensor.pnd_total_interval_production
    name: Výroba
    color: var(--success-color)
  - entity: sensor.pnd_total_interval_consumption
    name: Spotřeba
    color: var(--error-color)
```
![](/obrazky/pnd-celkem-kolac.png)

### Přehled výroby / spotřeby za posledních 10 dní
Využívá data _sensor.pnd_data_

```
type: custom:apexcharts-card
stacked: true
graph_span: 10d
span:
  end: day
header:
  show: true
  title: PND Posledních 10 dní
series:
  - entity: sensor.pnd_data
    name: Výroba
    attribute: production
    data_generator: |
      return entity.attributes.pnddate.map((pnd, index) => {
        return [new Date(pnd).getTime(), entity.attributes.production[index]];
      });
    color: var(--success-color)
    opacity: 0.8
    invert: false
    type: column
  - entity: sensor.pnd_data
    name: Spotřeba
    attribute: consumption
    data_generator: |
      return entity.attributes.pnddate.map((pnd, index) => {
        return [new Date(pnd).getTime(), entity.attributes.consumption[index]];
      });
    color: var(--error-color)
    opacity: 0.8
    invert: true
    type: column
```
![](/obrazky/pnd-poslednich10dni.png)

### Všechna data výroby / spotřeby z intervalu, agregace po týdnech

```
viz /grafy/ApexCard-xxxxxxxx.yaml
```
![](/obrazky/pnd-vsechnadata-tydenni.png)

### Všechna data výroby / spotřeby z intervalu, agregace po měsících

```
viz /grafy/ApexCard-xxxxxxxx.yaml
```
![](/obrazky/pnd-vsechnadata-mesicni.png)

# Plány a nápady
Pokud máte nějaké přání, nápad na vylepšení - vytvořte požadavek zde na GitHubu
- [ ] Zpracování více EANů (Elektroměrů)
- [x] Vyřešit unikátní ID senzorů, aby senzor byl spravovatelný v HA
- [x] Distribuce a aktualizace přes HACS
- [x] Refactor některých částí pro stabilitu při timeoutech, bezpečnost a kvalitu kódu
      
# Změny

## v2.3.0
 - [x] Oprava data u `sensor.pnd_consumption` a `sensor.pnd_production`: atribut `date` byl o den pozadu (např. `2026-09-29T23:59:00` u hodnoty za 30. 9.). Řádek `D 24:00:00` z portálu je spotřeba za den D; atribut je teď `2026-09-30`, stejně jako u 15min senzorů a v `pnddate`. Chyba pocházela už z v1 (`conv_date` − 1 den), `pnddate` a statistiky byly správně.
 - [x] Klouzavý interval: `data_interval: last_365_days` (obecně `last_N_days`) se při každém běhu přepočítá na posledních N dní do dnešní půlnoci. Pevný interval přestane po svém konci plnit statistiky a Energy dashboard.

## v2.2.0
 - [x] Volitelné náklady na odběr podle spotové ceny (volba `spot_price_entity`): senzor `sensor.pnd_consumption_cost` za poslední den a hodinová statistika `pnd:consumption_cost` pro Energy dashboard.
 - [x] Delší čekání na první profil po „Vyhledat data“: portál si pamatuje naposledy otevřený profil z minulého běhu (klidně 15min data za rok) a jeho načítání překrývalo odkazy.

## v2.1.1
 - [x] Při selhání otevření portálu se do logu i do atributu `status` senzoru `pnd_script_status` zapíše skutečná příčina (např. síťová chyba prohlížeče).
 - [x] Oprava návodu k migraci na 2.1.x: před prvním spuštěním je nutný restart HA, jinak entity dostanou příponu `_2`.

## v2.1.0
 - [x] Senzory se publikují přes MQTT discovery s retain: přežijí restart Home Assistanta, spouštěč na start HA už není potřeba.
 - [x] Entity mají `unique_id` a jsou seskupené pod zařízením „PND ELM &lt;číslo&gt;“ (spravovatelné v UI). `entity_id` zůstávají stejná, pokud se HA před prvním spuštěním restartuje (viz [Migrace na 2.1.0](#migrace-na-210)).
 - [x] Když doplněk spadne uprostřed běhu, `binary_sensor.pnd_running` se díky MQTT last will přepne na off.
 - [x] Odstraněn ukázkový `sensors.yaml` (placeholdery už nejsou potřeba a kolidovaly by s MQTT entitami).
 - [x] Nová závislost: MQTT broker (Mosquitto) a integrace MQTT.

## v2.0.0
 - [x] AppDaemon nahrazen samostatným doplňkem Home Assistanta (add-on repozitář), HACS distribuce ukončena.
 - [x] Selenium (Chrome + Firefox fallback) nahrazeno Playwright/Chromium v oficiálním image, odpadá ruční instalace balíčků a token.
 - [x] Spouštění akcí `hassio.addon_start` místo události `run_pnd`, `init_helper` odstraněn.
 - [x] Spolehlivější stahování CSV (čekání na skutečné stažení místo pevné pauzy).
 - [x] Při jakékoliv chybě se vypne `pnd_running`, nastaví `pnd_script_status` na Error a vytvoří `debug.zip` se screenshotem `error.png`.
 - [x] Podpora více elektroměrů v jednom běhu (seznam `meters`).
 - [x] Stahování 15minutových profilů (01/02): senzory `pnd_consumption_15min` / `pnd_production_15min` se čtvrthodinami za včerejšek a hodinové dlouhodobé statistiky `pnd:consumption` / `pnd:production` pro Energy dashboard. CSV soubory `*-15min.csv` jsou v `/share/pnd`.

## 20.4.2026 v1.0.1
 - [x] Přidána podpora pro automatické spuštění po restartu AppDaemon (díky @wejto).
 - [x] Oprava chybějícího importu modulu `math`.

## 18.4.2026 v1.0.0
 - [x] Sjednocení enginů - podpora automatického přepnutí z Google Chrome na Mozilla Firefox v případě pádu či chybějícího ovladače.
 - [x] Úprava struktury projektu pro plnou kompatibilitu a možnost aktualizací přes repozitář aplikací HACS.
 - [x] Odstraněna závislost na starší verzi numpy (již není nutné specifikovat `numpy==1.26.4`, skript funguje s nejnovější verzí).

## 3.11.2025 - 0.9.9.8
 - [x] Oprava zápisu nulových hodnot do atributu, týká se především probíhajícího období (měsíce) a budoucnosti [#81](https://github.com/ondrejvysek/HomeAssistant-CEZDistribuce-PND/issues/81). Pro správné fungování je nutné opravit také karty zobrazující měsíční / týdenní agregaci!
 - [x] Oprava zápisu procentuálních hodnot - již to není typ Energy, který nepodporuje procenta
 - [x] Definice Apex karet pro měsíční a týdenní agregaci odebrána z tohoto návodu a přesunuta do vlastních .yaml souborů (*Zakladni.yaml obsahuje agregaci a formátování logikou Apex, *Rozsirene.yaml obsahuje vlastní agregace a formátování - vhodné pro agregace od 1. v měsíci)

## 3.10.2025 - 0.9.9.7
 - [x] Oprava způsobu přihlašování [#79](https://github.com/ondrejvysek/HomeAssistant-CEZDistribuce-PND/issues/79)

## 2.10.2025 - 0.9.9.6 - Interní test verze

## 2.2.2025 - 0.9.9.3
- [x] Oprava časů v datech, aby seděly datumy [#61](https://github.com/ondrejvysek/HomeAssistant-CEZDistribuce-PND/pull/61), +omezení velikosti dat [#60](https://github.com/ondrejvysek/HomeAssistant-CEZDistribuce-PND/issues/60) díky @waclaw66
- [x] Aktualizace APEX charts pro zarovnání dat na konce měsíců [#62](https://github.com/ondrejvysek/HomeAssistant-CEZDistribuce-PND/pull/62) díky @waclaw66
## 13.10.2024 - 0.9.9.2
- [x] Oprava ghost procesu chromedriver [#52](https://github.com/ondrejvysek/HomeAssistant-CEZDistribuce-PND/issues/52) díky @waclaw66
- [x] Oprava zarovnání grafů [#54](https://github.com/ondrejvysek/HomeAssistant-CEZDistribuce-PND/pull/54) díky @waclaw66

## 26.7.2024 - 0.9.9.1
- [x] Vylepšené stahování souborů, již by se nemělo stávat, že datové soubory jsou stažené jako obrázky [#43](https://github.com/ondrejvysek/HomeAssistant-CEZDistribuce-PND/issues/43)
- [x] Detekce modálních oken portálu s upozorněním pro uživatele (obvykle info o nedostupnosti portálu), dojde k automatickému zavření okna a pokračování skriptu
## 19.6.2024 - 0.9.9
- [x] Oprava aktualizace s ohledem na novou verzi Numpy
- [x] Doplněny detailní ladící informace do skriptu týkající se prostředí na čem AppDaemon běží, systémových a python modulů a verze chrome driver
## 14.6.2024 - 0.9.8
- [x] Oprava dělení nulou u procentuálních výpočtů

## 4.6.2024 - 0.9.7
- [x] Oprava zaokrouhlování sensor.pnd_production2consumptionfull a sensor.pnd_production2consumptionfloor

## 28.5.2024 - 0.9.6
- [x] Přidán senzor: sensor.pnd_production2consumptionfull - zobrazuje poměr mezi výrobou a spoetřebou, bez omezení na 100%
- [x] Přidán senzor: sensor.pnd_production2consumptionfloor - např rezerva virtuální baterie, tj cokoliv co je nad 100% s minimem 0% pokud je poměr pod 100%. např: poměr je 60%, tento senzor bude 0%, poměr je 115%, tento senzor bude 15%

## 20.5.2024 - 0.9.5
- [x] Lepší ošetření chybových stavů, kontroly interakce s portálem (např chybné heslo) [#16](https://github.com/ondrejvysek/HomeAssistant-CEZDistribuce-PND/issues/16)
- [x] Přidán sensor.pnd_script_duration pro sledování délky běhu při normálním chodu
- [x] Přidán sensor.pnd_script_status pro sledování stavu, chování a případné chyby skriptu
- [x] Přidán sensor.pnd_app_version s verzí aplikace PND
- [x] Optimalizace časování skriptu - zrychlení
- [x] Vyčištění kódu o pracovní části
- [x] Oprava návodu u grafu měsíční agregace [#15](https://github.com/ondrejvysek/HomeAssistant-CEZDistribuce-PND/issues/15)
- [x] Přidán ukázkový soubor pro senzory sensors.yaml [#4](https://github.com/ondrejvysek/HomeAssistant-CEZDistribuce-PND/issues/4)
## 9.5.2024 - 0.9.4.6
- [x] Přidána kontrola dostupných ELM
- [x] Doplněna závislost na modulu bs4 - viz [nastavení doplňku AppDaemon](#instalace-a-nastaven%C3%AD-appdaemon)
      
## 9.5.2024 - 0.9.4.5
- [x] Oprava chyby #7 - přidána podpora více oken v PND
      
## 9.5.2024 - 0.9.4.4
- [x] Kompletní přepracování způsobu a kontroly výběru ELM
- [x] Vylepšené logování, po dokončení skriptu (pokud neskončí chybou) vytvořený **debug.zip** - prosím zasílejte tento soubor při řešení nesouladu dat

## 8.5.2024 - 0.9.4
- [x] Změna označení elektromůru z EAN na ELM v konfiguračním souboru + aktualizace dokumentace
- [x] Změna logování scriptu, detailní běh není ukládán do pnd.log ale přímo do AppDaemon logu - při řešení problémů prosím o zaslání Doplňky > AppDaemon > Log
- [x] U kritických fází je ukládán otisk obrazovky (identická cesta, kam jsou ukládány soubory s daty - parametr DownloadFolder v pnd.yaml) pro případné řešení problémů - v případě potřeby prosím o jejich poskytnutí
- [x] Optimalizace kódu 

## 6.5.2024 - 0.9.3
- [x] Přidán sensor.pnd_production2consumption jako procentuální poměr mezi výrobou a spotřebou (např. využití Virtuální Baterie)
- [x] Aktualizovaná karta celkového přehledu o sensor.pnd_production2consumption
- [x] Přidán parametr EAN do konfigurace
- [x] Přidán výběr EAN a Rychlá sestava pro případ, kdy si nastavení portálu pamatuje předchozí hodnoty EAN a uživatelské sestavy
- [x] Lepší ošetření chybových stavů a rozložení Portálu
- [x] Změna způsobu exportu dat z portálu z tabulky
- [x] upřesnění dílčích částí návodu
      
## 5.5.2024 - 0.9.2
- [x] Změna vyhledání intervalu z ID na nadřazený název
- [x] Vynucení "Výchozí sestava" a "Všechny EANy"
- [x] Vytvořen changelog

# Přispěvatelé
* **[ondrejvysek](https://github.com/ondrejvysek)**: autor projektu
* **Daniel Soutner**: přechod z AppDaemon na samostatný doplněk s Playwrightem, 15minutová data a statistiky pro Energy dashboard (v2.0.0)
* **[Claude Code](https://claude.com/claude-code)** (AI asistent od Anthropic): implementace v2.0.0 ve spolupráci s Danielem Soutnerem
* [@waclaw66](https://github.com/waclaw66), [@wejto](https://github.com/wejto), [@tomasherman](https://github.com/tomasherman), Karel Lowprize K a další, viz [seznam přispěvatelů na GitHubu](https://github.com/ondrejvysek/HomeAssistant-CEZDistribuce-PND/graphs/contributors)

Chcete se přidat? Pošlete pull request a doplňte se do tohoto seznamu.

# Licence
Projekt je šířen pod licencí [MIT](LICENSE).
