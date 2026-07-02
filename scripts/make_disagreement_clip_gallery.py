#!/usr/bin/env python3
import argparse
import html
import json
import os
import pathlib
import re
import shutil

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mestimate-sidecar-matplotlib")

try:
    import cv2
    import numpy as np
    import pandas as pd
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing Python dependency. This script needs opencv-python, pandas, and numpy."
    ) from exc


DEFAULT_REGIMES = ("high_cd10_only", "high_smvo_only", "high_both")
DEFAULT_RATIO_CATEGORIES = (
    "high_both_absolute",
    "cd10_only_strong",
    "mv_only_strong",
    "cd10_boundary_mv_high",
    "mv_boundary_cd10_high",
    "high_both_boundary",
    "cd10_high_mv_mid",
    "mv_high_cd10_mid",
)
REGIME_COLORS = {
    "high_cd10_only": (255, 120, 48),
    "high_smvo_only": (0, 180, 120),
    "high_both": (255, 190, 0),
    "high_both_absolute": (255, 190, 0),
    "high_both_boundary": (220, 180, 40),
    "cd10_only_strong": (255, 120, 48),
    "mv_only_strong": (0, 180, 120),
    "cd10_boundary_mv_high": (120, 150, 255),
    "mv_boundary_cd10_high": (40, 170, 170),
    "cd10_high_mv_mid": (220, 130, 80),
    "mv_high_cd10_mid": (50, 150, 110),
    "low_both": (170, 170, 170),
}


def slug(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_")


def score_events(events):
    events = events.copy()
    cd_threshold = events["effective_cd10_threshold"] if "effective_cd10_threshold" in events else events["cd10_threshold"]
    smvo_threshold = events["effective_smvo_threshold"] if "effective_smvo_threshold" in events else events["smvo_threshold"]
    cd_excess = (events["cd10"] - cd_threshold) / (cd_threshold.abs() + 1.0)
    smvo_excess = np.where(
        smvo_threshold.abs() > 1e-6,
        (events["smvo"] - smvo_threshold) / smvo_threshold.abs(),
        events["smvo"] - smvo_threshold,
    )
    cd_ratio = events["cd10_ratio"] if "cd10_ratio" in events else events["cd10"] / (cd_threshold + 1e-9)
    smvo_ratio = events["smvo_ratio"] if "smvo_ratio" in events else events["smvo"] / (smvo_threshold + 1e-9)
    events["score"] = 0.0
    events.loc[events["regime"].isin(["high_cd10_only", "cd10_only_strong"]), "score"] = cd_excess
    smvo_only = events["regime"].isin(["high_smvo_only", "mv_only_strong"])
    events.loc[smvo_only, "score"] = smvo_excess[smvo_only]
    both = events["regime"].isin(["high_both", "high_both_absolute", "high_both_boundary"])
    events.loc[both, "score"] = cd_ratio[both] + smvo_ratio[both]
    events.loc[events["regime"] == "cd10_boundary_mv_high", "score"] = (
        smvo_ratio - (cd_ratio - 1.0).abs()
    )
    events.loc[events["regime"] == "mv_boundary_cd10_high", "score"] = (
        cd_ratio - (smvo_ratio - 1.0).abs()
    )
    events.loc[events["regime"] == "cd10_high_mv_mid", "score"] = cd_ratio - smvo_ratio
    events.loc[events["regime"] == "mv_high_cd10_mid", "score"] = smvo_ratio - cd_ratio
    events.loc[events["regime"] == "low_both", "score"] = -(events["cd10"] + events["smvo"])
    return events


def select_events(second_bins, regimes, n_per_regime, min_gap_seconds):
    picks = []
    scored = score_events(second_bins[second_bins["regime"].isin(regimes)])
    for regime in regimes:
        chosen = []
        occupied = {}
        sub = scored[scored["regime"] == regime].sort_values("score", ascending=False)
        for _, row in sub.iterrows():
            key = (row["video"], row["well"])
            if any(abs(float(row["second_bin"]) - t) < min_gap_seconds for t in occupied.get(key, [])):
                continue
            chosen.append(row)
            occupied.setdefault(key, []).append(float(row["second_bin"]))
            if len(chosen) >= n_per_regime:
                break
        if chosen:
            picks.append(pd.DataFrame(chosen))
    if not picks:
        return pd.DataFrame(columns=list(second_bins.columns) + ["score"])
    out = pd.concat(picks, ignore_index=True)
    out["event_id"] = [
        f"{i:03d}_{slug(row.regime)}_{slug(row.video)}_{slug(row.well)}_{int(row.second_bin):03d}s"
        for i, row in enumerate(out.itertuples(index=False), start=1)
    ]
    return out


def crop_path(input_root, video, well):
    return pathlib.Path(input_root) / video / "crops" / f"{video}_{well}.mkv"


def comparison_path(input_root, video):
    return pathlib.Path(input_root) / video / "all_wells_comparison.csv"


def load_trace(input_root, video, well):
    df = pd.read_csv(comparison_path(input_root, video))
    df = df[df["well"] == well].copy()
    required = {"time_seconds", "cd10", "motion_metric"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{comparison_path(input_root, video)} missing columns: {sorted(missing)}")
    return df.sort_values("time_seconds")


def draw_label_panel(frame, row, rel_time, clip_start, clip_end):
    h, w = frame.shape[:2]
    color = REGIME_COLORS.get(row.regime, (200, 200, 200))
    cv2.rectangle(frame, (0, 0), (w - 1, h - 1), color, 2)
    cv2.rectangle(frame, (0, 0), (w, 44), (0, 0, 0), -1)
    metric_label = getattr(row, "metric_label", "MV")
    cd_ratio = getattr(row, "cd10_ratio", float("nan"))
    smvo_ratio = getattr(row, "smvo_ratio", float("nan"))
    lines = [
        f"{row.regime} | {row.video} {row.well} | event {row.second_bin:.0f}s | clip {rel_time:.2f}s",
        f"cd10 {row.cd10:.0f} ({cd_ratio:.2f}x) | {metric_label} {row.smvo:.4f} ({smvo_ratio:.2f}x)",
    ]
    for y, text in zip((16, 34), lines):
        cv2.putText(frame, text, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)
    x = int(np.clip((rel_time - clip_start) / max(clip_end - clip_start, 1e-9), 0, 1) * (w - 1))
    cv2.line(frame, (x, h - 9), (x, h - 1), color, 2)


def normalize(values):
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return values
    lo = np.nanpercentile(values, 1)
    hi = np.nanpercentile(values, 99)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        hi = float(np.nanmax(values)) if values.size else 1.0
        lo = float(np.nanmin(values)) if values.size else 0.0
    if hi <= lo:
        return np.zeros_like(values)
    return np.clip((values - lo) / (hi - lo), 0, 1)


def make_trace_panel(trace, now, event_second, width, height):
    panel = np.full((height, width, 3), 245, dtype=np.uint8)
    margin_l, margin_r, margin_t, margin_b = 42, 8, 12, 20
    plot_w = width - margin_l - margin_r
    plot_h = height - margin_t - margin_b
    cv2.rectangle(panel, (margin_l, margin_t), (width - margin_r, height - margin_b), (210, 210, 210), 1)

    if trace.empty:
        return panel

    t = trace["time_seconds"].to_numpy(dtype=float)
    cd = normalize(trace["cd10"].to_numpy(dtype=float))
    sm = normalize(trace["motion_metric"].to_numpy(dtype=float))
    x = margin_l + np.clip(t / max(np.nanmax(t), 1e-9), 0, 1) * plot_w

    def draw_series(yvals, color):
        y = margin_t + (1.0 - yvals) * plot_h
        pts = np.column_stack([x, y]).astype(np.int32)
        if len(pts) > 1:
            cv2.polylines(panel, [pts], False, color, 1, cv2.LINE_AA)

    draw_series(cd, (255, 120, 48))
    draw_series(sm, (0, 160, 100))

    for mark, color, thickness in [
        (event_second, (0, 0, 0), 1),
        (now, (0, 0, 220), 2),
    ]:
        mx = int(margin_l + np.clip(mark / max(np.nanmax(t), 1e-9), 0, 1) * plot_w)
        cv2.line(panel, (mx, margin_t), (mx, height - margin_b), color, thickness)

    cv2.putText(panel, "cd10", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 120, 48), 1, cv2.LINE_AA)
    metric_label = "MV"
    if "motion_metric_label" in trace.columns and len(trace):
        metric_label = str(trace["motion_metric_label"].iloc[0]).replace("MV-", "MV ")
    cv2.putText(panel, metric_label[:6], (6, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 120, 80), 1, cv2.LINE_AA)
    cv2.putText(panel, f"{now:05.2f}s", (margin_l, height - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (40, 40, 40), 1, cv2.LINE_AA)
    return panel


def render_clip(input_root, row, clip_path, radius_seconds, scale, fps_out):
    video_path = crop_path(input_root, row.video, row.well)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open {video_path}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 100.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = frame_count / src_fps if src_fps else 0.0
    start_s = max(0.0, float(row.second_bin) - radius_seconds)
    end_s = min(duration, float(row.second_bin) + radius_seconds)
    start_frame = int(round(start_s * src_fps))
    end_frame = int(round(end_s * src_fps))
    step = max(1, int(round(src_fps / fps_out)))
    actual_fps = src_fps / step

    trace = load_trace(input_root, row.video, row.well)
    out_w = src_w * scale
    video_h = src_h * scale
    trace_h = 82
    out_h = video_h + trace_h
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(clip_path), fourcc, actual_fps, (out_w, out_h))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"failed to create {clip_path}")

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    written = 0
    for frame_idx in range(start_frame, end_frame):
        ok, frame = cap.read()
        if not ok:
            break
        if (frame_idx - start_frame) % step != 0:
            continue
        if scale != 1:
            frame = cv2.resize(frame, (out_w, video_h), interpolation=cv2.INTER_NEAREST)
        now = frame_idx / src_fps
        draw_label_panel(frame, row, now, start_s, end_s)
        panel = make_trace_panel(trace, now, float(row.second_bin), out_w, trace_h)
        writer.write(np.vstack([frame, panel]))
        written += 1

    cap.release()
    writer.release()
    if written == 0:
        raise RuntimeError(f"no frames written for {clip_path}")
    return {
        "clip_path": str(clip_path),
        "source_path": str(video_path),
        "source_fps": float(src_fps),
        "output_fps": float(actual_fps),
        "start_seconds": float(start_s),
        "end_seconds": float(end_s),
        "frames_written": int(written),
    }


def write_index(events, output_dir, metadata):
    index = pathlib.Path(output_dir) / "index.html"
    rows = []
    for row in events.itertuples(index=False):
        rel_clip = f"clips/{html.escape(pathlib.Path(row.clip_file).name)}"
        cd_ratio = getattr(row, "cd10_ratio", float("nan"))
        smvo_ratio = getattr(row, "smvo_ratio", float("nan"))
        metric_label = getattr(row, "metric_label", "MV metric")
        rows.append(
            f"""
            <article class="clip {html.escape(row.regime)}">
              <h2>{html.escape(row.regime)} <span>{html.escape(row.video)} {html.escape(row.well)} t={row.second_bin:.0f}s</span></h2>
              <video controls preload="metadata" src="{rel_clip}"></video>
              <table>
                <tr><th>cd10</th><td>{row.cd10:.0f} / threshold {row.cd10_threshold:.0f} / ratio {cd_ratio:.3f}</td></tr>
                <tr><th>{html.escape(metric_label)}</th><td>{row.smvo:.5f} / threshold {row.smvo_threshold:.5f} / ratio {smvo_ratio:.3f}</td></tr>
                <tr><th>coherence</th><td>{row.coherence:.4f}</td></tr>
                <tr><th>raw MV energy</th><td>{row.raw_mv_energy:.4f}</td></tr>
                <tr><th>score</th><td>{row.score:.4f}</td></tr>
              </table>
            </article>
            """
        )
    body = "\n".join(rows)
    meta = html.escape(json.dumps(metadata, indent=2))
    index.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>cd10 / MV metric clip gallery</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; background: #f7f7f4; color: #202020; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    .note {{ max-width: 960px; color: #555; margin-bottom: 22px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 18px; align-items: start; }}
    article {{ background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 12px; }}
    article.high_cd10_only {{ border-top: 5px solid #ff7830; }}
    article.high_smvo_only {{ border-top: 5px solid #00a878; }}
    article.high_both {{ border-top: 5px solid #00bede; }}
    article.high_both_absolute {{ border-top: 5px solid #ffbe00; }}
    article.high_both_boundary {{ border-top: 5px solid #dcb428; }}
    article.cd10_only_strong {{ border-top: 5px solid #ff7830; }}
    article.mv_only_strong {{ border-top: 5px solid #00a878; }}
    article.cd10_boundary_mv_high {{ border-top: 5px solid #7896ff; }}
    article.mv_boundary_cd10_high {{ border-top: 5px solid #28aaaa; }}
    article.cd10_high_mv_mid {{ border-top: 5px solid #dc8250; }}
    article.mv_high_cd10_mid {{ border-top: 5px solid #32966e; }}
    h2 {{ font-size: 15px; margin: 0 0 8px; }}
    h2 span {{ display: block; color: #666; font-weight: 500; margin-top: 2px; }}
    video {{ width: 100%; background: #111; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }}
    th {{ text-align: left; width: 120px; color: #666; font-weight: 600; }}
    td, th {{ padding: 3px 0; border-bottom: 1px solid #eee; }}
    details {{ margin: 20px 0; }}
    pre {{ background: #eee; padding: 10px; overflow-x: auto; }}
  </style>
</head>
<body>
  <h1>cd10 / MV metric clip gallery</h1>
  <p class="note">Each card is a short cropped-well clip centered on a selected one-second bin. The lower trace panel shows normalized cd10 in orange and the selected MV metric in green over the full crop window; the black line marks the selected event and the red line follows the current frame.</p>
  <details><summary>Run metadata</summary><pre>{meta}</pre></details>
  <section class="grid">
    {body}
  </section>
</body>
</html>
""",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="Make short clips for cd(10) and sMVO disagreement events.")
    parser.add_argument("--input-root", default="outputs/mv_active_cd10_multivideo")
    parser.add_argument("--regime-table", default="outputs/mv_active_cd10_multivideo/disagreements/second_bin_regimes.csv")
    parser.add_argument("--output-dir", default="outputs/mv_active_cd10_multivideo/disagreement_clip_gallery")
    parser.add_argument("--regimes", nargs="+", default=None)
    parser.add_argument("--n-per-regime", type=int, default=6)
    parser.add_argument("--min-gap-seconds", type=float, default=8.0)
    parser.add_argument("--clip-radius-seconds", type=float, default=1.5)
    parser.add_argument("--scale", type=int, default=4)
    parser.add_argument("--fps", type=float, default=25.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output_dir = pathlib.Path(args.output_dir)
    clips_dir = output_dir / "clips"
    if output_dir.exists() and not args.force:
        raise SystemExit(f"output exists: {output_dir} (pass --force to replace files)")
    if args.force and clips_dir.exists():
        shutil.rmtree(clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)

    second_bins = pd.read_csv(args.regime_table)
    if args.regimes is None:
        available = set(second_bins["regime"])
        ratio_categories = [r for r in DEFAULT_RATIO_CATEGORIES if r in available]
        args.regimes = ratio_categories if ratio_categories else [r for r in DEFAULT_REGIMES if r in available]
    if "metric_label" not in second_bins.columns:
        second_bins["metric_label"] = "MV metric"
    events = select_events(second_bins, args.regimes, args.n_per_regime, args.min_gap_seconds)
    if events.empty:
        raise SystemExit("No events matched the requested regimes.")

    render_meta = []
    clip_files = []
    for row in events.itertuples(index=False):
        clip_file = clips_dir / f"{row.event_id}.mp4"
        meta = render_clip(args.input_root, row, clip_file, args.clip_radius_seconds, args.scale, args.fps)
        render_meta.append(meta)
        clip_files.append(str(clip_file))

    events = events.copy()
    events["clip_file"] = clip_files
    events.to_csv(output_dir / "selected_events.csv", index=False)
    metadata = {
        "unit": "video x well x selected one-second bin",
        "input_root": args.input_root,
        "regime_table": args.regime_table,
        "regimes": args.regimes,
        "n_per_regime": args.n_per_regime,
        "min_gap_seconds_within_video_well": args.min_gap_seconds,
        "clip_radius_seconds": args.clip_radius_seconds,
        "output_fps_requested": args.fps,
        "scale": args.scale,
        "n_events": int(len(events)),
        "rendered_clips": render_meta,
    }
    (output_dir / "gallery_summary.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    write_index(events, output_dir, metadata)
    print(json.dumps({"output_dir": str(output_dir), "n_events": int(len(events))}, indent=2))


if __name__ == "__main__":
    main()
