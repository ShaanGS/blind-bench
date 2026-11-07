"""Two synthetic fixtures. Only the bench agent receives the armed fixture."""

import csv
import hashlib
import io
import json
import math
import secrets

DC_VOLTS = 4.958
LIMIT_MVPP = 50.0
BOARDS = {"clean": 39.6, "noisy": 132.0}


def commitment(run_id, board, nonce):
    return hashlib.sha256(f"{run_id}:{board}:{nonce}".encode()).hexdigest()


def arm(directory, run_id, board):
    if board not in BOARDS:
        raise ValueError("Unknown fixture")
    directory.mkdir(parents=True, exist_ok=True)
    record = {"board": board, "nonce": secrets.token_hex(16)}
    with (directory / f"{run_id}.json").open("x", encoding="utf-8") as file:
        json.dump(record, file)
    return commitment(run_id, **record)


def scope(run_id, board):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    times = [index / 32 / 40_000 * 1_000_000 for index in range(256)]
    samples = [
        DC_VOLTS + BOARDS[board] / 2_000 * math.sin(2 * math.pi * index / 32)
        for index in range(256)
    ]
    ripple = round((max(samples) - min(samples)) * 1_000, 1)
    inside_limit = ripple < LIMIT_MVPP
    trace_color = "#55deb1" if inside_limit else "#ff826c"
    fig, ax = plt.subplots(figsize=(10.4, 4.2), layout="constrained")
    fig.patch.set_facecolor("#0c161c")
    ax.set_facecolor("#101f28")
    ax.axhspan(-25, 25, color="#94c8da", alpha=0.16, label="50 mVpp reference band")
    ax.plot(times, [(voltage - DC_VOLTS) * 1_000 for voltage in samples], color=trace_color, linewidth=2)
    ax.set(xlim=(0, 200), ylim=(-80, 80), xlabel="Time (µs)", ylabel="AC ripple about DC level (mV)")
    ax.set_title(f"{run_id}   /   {ripple:g} mVpp measured", loc="left", color=trace_color, weight="bold", pad=14)
    ax.set_title("SIMULATED SCOPE  •  2 A LOAD", loc="right", color="#b6ced8", fontsize=9, pad=15)
    ax.grid(color="#36515e", alpha=0.5)
    ax.tick_params(colors="#b6ced8")
    for axis in (ax.xaxis, ax.yaxis):
        axis.label.set_color("#b6ced8")
    for spine in ax.spines.values():
        spine.set_color("#36515e")
    ax.legend(loc="lower right", facecolor="#101f28", labelcolor="#b6ced8")
    png = io.BytesIO()
    fig.savefig(png, format="png", dpi=140, facecolor=fig.get_facecolor())
    plt.close(fig)
    csv_text = io.StringIO()
    writer = csv.writer(csv_text)
    writer.writerow(("time_us", "voltage_v"))
    writer.writerows((f"{time:.6f}", f"{sample:.9f}") for time, sample in zip(times, samples))
    return ripple, [
        (f"{run_id}-scope.png", png.getvalue(), "image/png"),
        (f"{run_id}-samples.csv", csv_text.getvalue().encode(), "text/csv"),
    ]
