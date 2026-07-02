#!/usr/bin/env python3
import argparse
import math
import pathlib

try:
    import cv2
    import numpy as np
    import pandas as pd
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing Python visualization dependency. Install the optional "
        "visualization packages from requirements-visualize.txt."
    ) from exc


def color_for_magnitude(mag, high):
    high = max(float(high), 1e-9)
    t = min(max(float(mag) / high, 0.0), 1.0)
    # BGR: cyan for low values, yellow/red for high values.
    return (int(255 * (1.0 - t)), int(220 * (1.0 - 0.35 * t)), int(80 + 175 * t))


def draw_label(img, text, origin):
    x, y = origin
    cv2.putText(img, text, (x + 1, y + 1), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (245, 245, 245), 1, cv2.LINE_AA)


def gray_frame(frame):
    if frame.ndim == 2:
        return frame
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def block_bounds(row, width, height, padding):
    # FFmpeg motion-vector coordinates are block centers for this use case.
    half_w = max(int(row.w) // 2, 1) + padding
    half_h = max(int(row.h) // 2, 1) + padding
    cx = int(round(row.dst_x))
    cy = int(round(row.dst_y))
    x0 = max(0, cx - half_w)
    x1 = min(width, cx + half_w)
    y0 = max(0, cy - half_h)
    y1 = min(height, cy + half_h)
    return x0, y0, x1, y1


def passes_local_diff_filters(row, diff, args):
    if diff is None:
        return not (
            args.min_block_mean_absdiff > 0
            or args.min_block_max_absdiff > 0
            or args.min_block_cd_pixels > 0
        )
    x0, y0, x1, y1 = block_bounds(row, diff.shape[1], diff.shape[0], args.block_padding)
    if x1 <= x0 or y1 <= y0:
        return False
    block = diff[y0:y1, x0:x1]
    if args.min_block_mean_absdiff > 0 and float(block.mean()) < args.min_block_mean_absdiff:
        return False
    if args.min_block_max_absdiff > 0 and float(block.max()) < args.min_block_max_absdiff:
        return False
    if args.min_block_cd_pixels > 0:
        cd_pixels = int(np.count_nonzero(block > args.cd_threshold))
        if cd_pixels < args.min_block_cd_pixels:
            return False
    return True


def frame_passes(summary, args):
    if summary is None:
        return True
    _, sum_mag, coherence = summary
    if args.min_frame_energy > 0 and sum_mag < args.min_frame_energy:
        return False
    if args.max_frame_energy is not None and sum_mag > args.max_frame_energy:
        return False
    if args.min_coherence is not None and coherence < args.min_coherence:
        return False
    if args.max_coherence is not None and coherence > args.max_coherence:
        return False
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Render a video overlay from mestimate-sidecar vector CSV data."
    )
    parser.add_argument("--input", required=True, help="Original input video.")
    parser.add_argument("--vectors", required=True, help="*.mestimate-v1.vectors.csv.gz file.")
    parser.add_argument("--frames", default=None, help="Optional *.mestimate-v1.frames.csv.gz file for labels.")
    parser.add_argument("--output", required=True, help="Output overlay video, usually .mp4.")
    parser.add_argument("--scale", type=int, default=6, help="Integer display scale for small well videos.")
    parser.add_argument("--min-magnitude", type=float, default=0.25, help="Skip shorter vectors.")
    parser.add_argument("--max-magnitude", type=float, default=None, help="Skip longer vectors.")
    parser.add_argument("--min-frame-energy", type=float, default=0.0, help="Require frame sum_magnitude_px.")
    parser.add_argument("--max-frame-energy", type=float, default=None, help="Reject frame sum_magnitude_px above this value.")
    parser.add_argument("--min-coherence", type=float, default=None, help="Require frame coherence at least this value.")
    parser.add_argument("--max-coherence", type=float, default=None, help="Require frame coherence at most this value.")
    parser.add_argument("--cd-threshold", type=int, default=10, help="Pixel-difference threshold for block cd pixels.")
    parser.add_argument("--min-block-mean-absdiff", type=float, default=0.0, help="Require local block mean abs frame difference.")
    parser.add_argument("--min-block-max-absdiff", type=float, default=0.0, help="Require local block max abs frame difference.")
    parser.add_argument("--min-block-cd-pixels", type=int, default=0, help="Require this many local block pixels above --cd-threshold.")
    parser.add_argument("--block-padding", type=int, default=0, help="Extra pixels around each vector block for local diff filters.")
    parser.add_argument("--max-frames", type=int, default=0, help="Stop after this many source frames; 0 means all.")
    parser.add_argument("--force", action="store_true", help="Replace an existing output file.")
    args = parser.parse_args()

    output = pathlib.Path(args.output)
    if output.exists() and not args.force:
        raise SystemExit(f"Output exists; pass --force to replace: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    vectors = pd.read_csv(args.vectors, compression="gzip")
    vectors = vectors[vectors["magnitude_px"] >= args.min_magnitude].copy()
    if args.max_magnitude is not None:
        vectors = vectors[vectors["magnitude_px"] <= args.max_magnitude].copy()
    grouped = {int(k): v for k, v in vectors.groupby("frame_index", sort=False)}

    frame_summaries = {}
    if args.frames:
        frames = pd.read_csv(args.frames, compression="gzip")
        frame_summaries = {
            int(r.frame_index): (int(r.n_vectors), float(r.sum_magnitude_px), float(r.coherence))
            for r in frames.itertuples(index=False)
        }
    elif (
        args.min_frame_energy > 0
        or args.max_frame_energy is not None
        or args.min_coherence is not None
        or args.max_coherence is not None
    ):
        raise SystemExit("Frame-level filters require --frames.")

    cap = cv2.VideoCapture(args.input)
    if not cap.isOpened():
        raise SystemExit(f"Failed to open input video: {args.input}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    out_size = (width * args.scale, height * args.scale)
    writer = cv2.VideoWriter(
        str(output),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        out_size,
        True,
    )
    if not writer.isOpened():
        cap.release()
        raise SystemExit(f"Failed to open output video writer: {output}")

    high_mag = vectors["magnitude_px"].quantile(0.95) if len(vectors) else 1.0
    frame_index = 0
    rendered = 0
    drawn = 0
    prev_gray = None

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if args.max_frames and frame_index >= args.max_frames:
            break

        current_gray = gray_frame(frame)
        diff = None
        if prev_gray is not None:
            diff = cv2.absdiff(current_gray, prev_gray)

        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        display = cv2.resize(frame, out_size, interpolation=cv2.INTER_NEAREST)
        rows = grouped.get(frame_index)
        summary = frame_summaries.get(frame_index)
        frame_drawn = 0
        if rows is not None and frame_passes(summary, args):
            for row in rows.itertuples(index=False):
                if not passes_local_diff_filters(row, diff, args):
                    continue
                sx = int(round(row.src_x * args.scale))
                sy = int(round(row.src_y * args.scale))
                dx = int(round(row.dst_x * args.scale))
                dy = int(round(row.dst_y * args.scale))
                color = color_for_magnitude(row.magnitude_px, high_mag)
                thickness = 1 if row.magnitude_px < high_mag else 2
                cv2.arrowedLine(display, (sx, sy), (dx, dy), color, thickness, cv2.LINE_AA, tipLength=0.35)
                frame_drawn += 1
                drawn += 1

        label = f"frame {frame_index}"
        if frame_index in frame_summaries:
            n, sum_mag, coherence = frame_summaries[frame_index]
            label += f"  vectors {n}  sum {sum_mag:.1f}  coh {coherence:.2f}"
        label += f"  drawn {frame_drawn}"
        draw_label(display, label, (8, 18))

        writer.write(display)
        prev_gray = current_gray.copy()
        frame_index += 1
        rendered += 1

    writer.release()
    cap.release()
    print(f"rendered {rendered} frames, drew {drawn} vectors to {output}")


if __name__ == "__main__":
    main()
