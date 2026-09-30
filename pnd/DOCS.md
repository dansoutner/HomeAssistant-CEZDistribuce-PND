# ČEZ Distribuce PND

Doplněk se přihlásí do [Portálu naměřených dat](https://pnd.cezdistribuce.cz/), stáhne denní, intervalová a 15minutová data spotřeby a výroby a zapíše je do senzorů `sensor.pnd_*`. 15minutová data za celý interval nahraje jako hodinové statistiky `pnd:consumption` a `pnd:production`, které lze použít v Energy dashboardu. Po doběhnutí se sám vypne.

## Nastavení

```yaml
meters:
  - username: "vas@email.cz"
    password: "heslo"
    elm: "3000012345"
    data_interval: "27.10.2023 00:00 - 27.10.2024 00:00"
```

Pro více elektroměrů přidejte další položku a vyplňte `id`. Entity pak dostanou příponu, např. `sensor.pnd_consumption_chata`.

## Spouštění

Doplněk se spouští automatizací:

```yaml
alias: Run PND
triggers:
  - trigger: time
    at: "00:30:00"
actions:
  - action: hassio.addon_start
    data:
      addon: xxxxxxxx_pnd   # slug doplňku, viz URL stránky doplňku
mode: single
```

## Řešení problémů

Průběh je vidět v záložce **Log** doplňku. Screenshoty, stažená CSV a `debug.zip` jsou ve složce `/share/pnd` (s `id` ve `/share/pnd_<id>`).
