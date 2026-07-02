#!/usr/bin/env python3
import argparse
import os
import pathlib
import subprocess


def frame(width, height, t):
    data = bytearray([18] * (width * height))

    # Static background structure.
    for x in range(8, 75):
        data[12 * width + x] = 55
    for y in range(15, 72):
        data[y * width + 66] = 48

    x0, y0 = 18, 36
    if 10 <= t <= 20:
        x0 += 2 * (t - 10)
    elif 21 <= t <= 29:
        x0 += 20
    elif 30 <= t <= 40:
        x0 += 20
        y0 -= 2 * (t - 30)
    elif t > 40:
        x0 += 20
        y0 -= 20

    for y in range(y0, y0 + 12):
        for x in range(x0, x0 + 12):
            if 0 <= x < width and 0 <= y < height:
                data[y * width + x] = 220
    return bytes(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--raw", default=None)
    parser.add_argument("--frames", type=int, default=50)
    parser.add_argument("--fps", type=int, default=25)
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_BIN", "ffmpeg"))
    args = parser.parse_args()

    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = pathlib.Path(args.raw) if args.raw else output.with_suffix(".gray")

    width = height = 83
    with raw.open("wb") as f:
        for t in range(args.frames):
            f.write(frame(width, height, t))

    subprocess.run(
        [
            args.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-s",
            f"{width}x{height}",
            "-r",
            str(args.fps),
            "-i",
            str(raw),
            "-c:v",
            "ffv1",
            str(output),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
