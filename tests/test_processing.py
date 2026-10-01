"""Zpracování stažených CSV bez prohlížeče: denní senzory, kompletnost dat, pnd_data.

Spouští se v obrazu doplňku (CI krok „Processing tests“):
    docker run --rm -v "$PWD/tests:/tests:ro" --entrypoint python3 <image> /tests/test_processing.py

Vzorová data jsou skutečná CSV z portálu z 1. 10. 2026 7:23 (číslo elektroměru anonymizované):
complete = 29. 9. (všechny čtvrthodiny „naměřená data OK“), incomplete = 30. 9. (2/96, denní řádek N/A).
"""
import ast
import os
import sys

sys.path.insert(0, "/")
import pnd  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}: {name} {detail}")


class FakeMqtt:
    def __init__(self):
        self.states = {}

    def set_state(self, key, state, attributes=None):
        self.states[key] = (state, attributes or {})


def run_case(case):
    run = pnd.PndRun(None, {}, {"username": "u", "password": "p", "elm": "1", "data_interval": "last_365_days"})
    run.mqtt = FakeMqtt()
    run.download_folder = os.path.join(FIXTURES, case)
    run.process_daily()
    run.process_interval()
    return run


# Kód modulu: žádná funkce ani metoda nesmí být definovaná dvakrát (chybný merge v 2.4.0)
tree = ast.parse(open(pnd.__file__, encoding="utf-8").read())
top = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
check("no duplicate top-level definitions", len(top) == len(set(top)), sorted({n for n in top if top.count(n) > 1}))
for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
    methods = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    check(f"no duplicate methods in {cls.name}", len(methods) == len(set(methods)),
          sorted({n for n in methods if methods.count(n) > 1}))

# Kompletní den 29. 9.
run = run_case("complete")
st = run.mqtt.states
check("complete: consumption = 3.769", st.get("consumption", (None,))[0] == 3.769, st.get("consumption"))
check("complete: consumption date = 2026-09-29 (den D, ne D-1)", st["consumption"][1].get("date") == "2026-09-29", st["consumption"][1])
check("complete: production date = 2026-09-29", st.get("production", (None, {}))[1].get("date") == "2026-09-29")
check("complete: 15min sum = 3.769", st.get("consumption_15min", (None,))[0] == "3.769", st.get("consumption_15min", (None,))[0])
check("complete: 15min has 96 quarters", len(st["consumption_15min"][1].get("consumption", [])) == 96)
check("complete: 15min date matches daily", st["consumption_15min"][1].get("date") == st["consumption"][1].get("date"))
status, attrs = run.completeness_status()
check("complete: status Finished, data_complete", status == "Finished" and attrs == {"data_complete": True, "data_date": "2026-09-29"}, (status, attrs))

# Neúplný den 30. 9.: denní senzory se nepřepisují
run = run_case("incomplete")
st = run.mqtt.states
for key in ("consumption", "production", "consumption_15min", "production_15min"):
    check(f"incomplete: {key} not written", key not in st)
status, attrs = run.completeness_status()
check("incomplete: data_complete False for 2026-09-30", attrs == {"data_complete": False, "data_date": "2026-09-30"}, attrs)
check("incomplete: status says 2/96", "2/96" in status, status)

# Interval: neúplný 30. 9. na konci se odřízne, data se párují podle pnddate
data = st["data"][1]
check("interval: pnddate ends 2026-09-29", data["pnddate"][-1] == "2026-09-29", data["pnddate"])
check("interval: first day of the 26.9.-30.9. sample is 26.9.", data["pnddate"][0] == "2026-09-26", data["pnddate"][0])
check("interval: arrays aligned", len(data["pnddate"]) == len(data["consumption"]) == len(data["production"]) == 4)
check("interval: last consumption 3.769", data["consumption"][-1] == "3.769", data["consumption"])
check("interval: total 26.9.-29.9. = 11.05", st["total_interval_consumption"][0] == "11.05", st["total_interval_consumption"][0])

print("ALL PASS" if all(results) else "SOME FAILED")
sys.exit(0 if all(results) else 1)
