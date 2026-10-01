"""Navazování kumulativních statistik (last_sum_before) a řetězení importu nákladů.

Spouští se v obrazu doplňku (CI krok „Tests“). Recorder simuluje FakeRecorder včetně chování
skutečného HA ověřeného 1. 10. 2026: period month/day vrací u posledního bucketu sumu celého
bucketu, i když end_time padne doprostřed; period hour vrací jen řádky se start < end_time.
"""
import datetime
import os
import sys

sys.path.insert(0, "/")
import pnd  # noqa: E402

UTC = datetime.timezone.utc
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}: {name} {detail}")


class FakeRecorder(pnd.HomeAssistant):
    """Hodinové sumy v paměti; statistics_during_period jako HA, import přepisuje řádky."""

    def __init__(self):
        self.stats = {}  # statistic_id -> {start (datetime UTC): sum}
        self.calls = []

    @property
    def rows(self):
        """Řádky jediné statistiky v testu řetězení."""
        return next(iter(self.stats.values()))

    def ws(self, *commands):
        out = []
        for c in commands:
            self.calls.append(c)
            if c["type"] == "recorder/statistics_during_period":
                data = self.stats.get(c["statistic_ids"][0], {})
                start = datetime.datetime.fromisoformat(c["start_time"])
                end = datetime.datetime.fromisoformat(c["end_time"])
                hours = sorted(t for t in data if start <= t < end)
                if c["period"] == "hour":
                    rows = [{"start": t, "sum": data[t]} for t in hours]
                else:  # month: jeden bucket se sumou ke KONCI bucketu (i za end_time) – chování HA
                    month_end = max((t for t in data if hours and t.month == hours[-1].month), default=None)
                    rows = [{"start": hours[-1], "sum": data[month_end]}] if hours else []
                out.append({c["statistic_ids"][0]: rows} if rows else {})
            elif c["type"] == "recorder/import_statistics":
                data = self.stats.setdefault(c["metadata"]["statistic_id"], {})
                for s in c["stats"]:
                    data[datetime.datetime.fromisoformat(s["start"])] = s["sum"]
                out.append(None)
        return out


rec = FakeRecorder()
base = datetime.datetime(2026, 9, 20, 0, tzinfo=UTC)
# 10 dní hodinových nákladů 1 Kč/h
rec.import_sum_statistics("pnd:x", "x", "CZK", None, [{"start": (base + datetime.timedelta(hours=h)).isoformat(), "sum": float(h + 1)} for h in range(240)])

before = base + datetime.timedelta(days=2)
got = rec.last_sum_before("pnd:x", before)
check("sum before 22.9. = 48 (not end of month 240)", got == 48.0, got)
last = rec.calls[-1]
check("queries hourly rows", last["period"] == "hour", last["period"])
check("end_time = before", last["end_time"] == before.isoformat(), last["end_time"])
check("empty statistic -> 0", rec.last_sum_before("pnd:none", before) == 0.0)
far = base + datetime.timedelta(days=60)
check("gap > 35 days -> falls back to full history", rec.last_sum_before("pnd:x", far) == 240.0, rec.last_sum_before("pnd:x", far))
check("before first row -> 0", rec.last_sum_before("pnd:x", base) == 0.0)

# Opakovaný import se posunutým začátkem (jako každý běh) nesmí sumu nafukovat
rec2 = FakeRecorder()
for run in range(5):
    start = base + datetime.timedelta(hours=10 * run)  # okno historie cen se posouvá
    hours = [start + datetime.timedelta(hours=h) for h in range(240 - 10 * run)]
    b = rec2.last_sum_before("pnd:c", start)
    stats, total = [], b
    for t in hours:
        total += 1.0
        stats.append({"start": t.isoformat(), "sum": total})
    rec2.import_sum_statistics("pnd:c", "c", "CZK", None, stats)
final = rec2.rows[max(rec2.rows)]
check("5 runs with moving start: total stays 240", final == 240.0, final)
diffs = [rec2.rows[t] - rec2.rows[t - datetime.timedelta(hours=1)] for t in sorted(rec2.rows)[1:]]
check("5 runs: every hour +1 (no jumps)", all(abs(d - 1.0) < 1e-9 for d in diffs), sorted(set(round(d, 4) for d in diffs)))

print("ALL PASS" if all(results) else "SOME FAILED")
sys.exit(0 if all(results) else 1)
