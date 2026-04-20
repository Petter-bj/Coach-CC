"""`baselines` — rolling baselines per metrikk + vindu."""

from __future__ import annotations

import typer

from src.analysis.baselines import refresh_baselines
from src.cli._common import emit
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def refresh(json_output: bool = typer.Option(False, "--json")) -> None:
    """Beregn rolling baselines for alle (metric, window)-par på nytt."""
    with connect() as c:
        n = refresh_baselines(c)
    emit({"written": n}, as_json=json_output,
         text=f"✓ Oppdaterte {n} baseline-rader\n")


@app.command()
def show(json_output: bool = typer.Option(False, "--json")) -> None:
    """Vis siste beregnede baselines."""
    with connect() as c:
        rows = [dict(r) for r in c.execute(
            """
            SELECT metric, window_days, value, median, mad, sample_size,
                   insufficient_data, computed_at
              FROM user_baselines
             ORDER BY metric, window_days
            """
        ).fetchall()]
    data = {"count": len(rows), "rows": rows}
    if json_output:
        emit(data, as_json=True)
        return
    if not rows:
        emit(data, as_json=False, text="Ingen baselines ennå — kjør `refresh`\n")
        return

    # Gruppér per metric for pen output
    from collections import defaultdict
    by_metric: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_metric[r["metric"]].append(r)

    lines = ["# Rolling baselines"]
    for metric, window_rows in sorted(by_metric.items()):
        lines.append(f"\n## {metric}")
        for w in window_rows:
            if w["insufficient_data"]:
                lines.append(
                    f"  {w['window_days']:>2}d: (for lite data — {w['sample_size']} punkter)"
                )
            else:
                lines.append(
                    f"  {w['window_days']:>2}d: median={w['value']} "
                    f"(mad={w['mad']}, n={w['sample_size']})"
                )
    emit(data, as_json=False, text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
