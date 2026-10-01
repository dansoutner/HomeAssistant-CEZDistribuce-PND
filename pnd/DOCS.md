# ČEZ Distribuce PND

Doplněk se přihlásí do [Portálu naměřených dat](https://pnd.cezdistribuce.cz/), stáhne denní, intervalová a 15minutová data spotřeby a výroby a zapíše je do senzorů `sensor.pnd_*`. 15minutová data za celý interval nahraje jako hodinové statistiky `pnd:consumption` a `pnd:production`, které lze použít v Energy dashboardu. Po doběhnutí se sám vypne.

Senzory se publikují přes **MQTT discovery** (zařízení „PND ELM &lt;číslo&gt;“) s retain, takže přežijí restart Home Assistanta. Doplněk proto vyžaduje doplněk **Mosquitto broker** a integraci **MQTT**. Přístup k brokeru si převezme automaticky.

## Nastavení

```yaml
meters:
  - username: "vas@email.cz"
    password: "heslo"
    elm: "3000012345"
    data_interval: "last_365_days"
```

Pro více elektroměrů přidejte další položku a vyplňte `id`. Entity pak dostanou příponu, např. `sensor.pnd_consumption_chata`.

Volitelně `spot_price_entity: sensor.current_spot_electricity_price_15min` (senzor spotové ceny za kWh nebo MWh): doplněk spočítá náklady na odběr do `sensor.pnd_consumption_cost` a statistiky `pnd:consumption_cost` pro Energy dashboard. Ceny bere z historie senzoru (výchozí retence 10 dní) a jde jen o silovou elektřinu bez distribuce a poplatků.

## Spouštění

Data za včerejšek portál zveřejňuje až během dne; do té doby doplněk denní senzory nepřepisuje (`sensor.pnd_script_status` má `data_complete: false`). Doporučená automatizace proto zkouší každou hodinu, dokud nejsou kompletní:

```yaml
alias: Run PND
triggers:
  - trigger: time_pattern
    minutes: "15"
conditions:
  - condition: time
    after: "06:00:00"
    before: "23:00:00"
  - condition: state
    entity_id: binary_sensor.pnd_running
    state: "off"
  - condition: template
    value_template: >-
      {{ state_attr('sensor.pnd_consumption', 'date') != (now().date() - timedelta(days=1)) | string }}
actions:
  - action: hassio.app_start
    data:
      app: xxxxxxxx_pnd   # slug doplňku, viz URL stránky doplňku
mode: single
```

Spouštěč na start Home Assistanta není potřeba, senzory mají po restartu hned poslední hodnoty z MQTT.

## Řešení problémů

Průběh je vidět v záložce **Log** doplňku. Screenshoty, stažená CSV a `debug.zip` jsou ve složce `/share/pnd` (s `id` ve `/share/pnd_<id>`).
