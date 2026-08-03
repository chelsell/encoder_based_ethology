#!/usr/bin/env python3
import argparse
import csv
import json
import os
import pathlib
import shutil
import subprocess
import time


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def task_index_from_env():
    for name in ("SGE_TASK_ID", "SLURM_ARRAY_TASK_ID"):
        value = os.environ.get(name)
        if value:
            return int(value)
    raise SystemExit("Pass --task-index or run under SGE with SGE_TASK_ID set.")


def select_plate_row(plate_manifest, task_index):
    rows = read_csv(plate_manifest)
    if task_index < 1 or task_index > len(rows):
        raise SystemExit(f"task index {task_index} outside manifest range 1-{len(rows)}")
    return rows[task_index - 1], len(rows)


def select_plate_rows_for_chunk(plate_manifest, task_index, chunk_size):
    rows = read_csv(plate_manifest)
    if chunk_size < 1:
        raise SystemExit("--chunk-size must be at least 1")
    start = (task_index - 1) * chunk_size
    stop = min(start + chunk_size, len(rows))
    if task_index < 1 or start >= len(rows):
        total_chunks = (len(rows) + chunk_size - 1) // chunk_size
        raise SystemExit(f"task index {task_index} outside chunk range 1-{total_chunks}")
    return rows[start:stop], len(rows), (len(rows) + chunk_size - 1) // chunk_size


def rows_for_source(well_manifest, source_video_id):
    rows = [row for row in read_csv(well_manifest) if row["source_video_id"] == source_video_id]
    if not rows:
        raise SystemExit(f"no well rows found for source_video_id={source_video_id}")
    return sorted(rows, key=lambda row: int(row.get("well_index") or 0))


def resolve_source_path(plate_row, staged_input_root):
    source_path = pathlib.Path(plate_row["source_path"])
    if staged_input_root:
        return pathlib.Path(staged_input_root) / plate_row["source_video_id"] / source_path.name
    return source_path


def work_root_from_args(args):
    if args.work_root:
        return pathlib.Path(args.work_root)
    tmpdir = os.environ.get("TMPDIR")
    return pathlib.Path(tmpdir) if tmpdir else None


def local_source_path(source_path, work_root, source_video_id):
    return pathlib.Path(work_root) / "input" / source_video_id / pathlib.Path(source_path).name


def rsync_copy_command(source, dest):
    return ["rsync", "-a", "--partial", "--ignore-existing", str(source), str(dest)]


def rsync_tree_command(source_dir, dest_dir):
    return ["rsync", "-a", "--partial", f"{source_dir}/", f"{dest_dir}/"]


def cleanup_local_work(work_root, source_video_id):
    if not work_root:
        return []
    paths = [
        pathlib.Path(work_root) / "input" / source_video_id,
        pathlib.Path(work_root) / "output" / source_video_id,
    ]
    removed = []
    for path in paths:
        if path.exists():
            shutil.rmtree(path)
            removed.append(str(path))
    return removed


def map_well_rows_to_output_dir(well_rows, output_dir):
    output_dir = pathlib.Path(output_dir)
    mapped = []
    for row in well_rows:
        next_row = dict(row)
        final = pathlib.Path(row["well_archive_path"])
        next_row["well_archive_path"] = str(output_dir / "video" / final.name)
        if row.get("well_sidecar_dir"):
            next_row["well_sidecar_dir"] = str(output_dir / "sidecar" / "mv_v1" / row["well_label"])
        mapped.append(next_row)
    return mapped


def prepare_local_source(source_path, work_root, source_video_id, dry_run):
    if not work_root:
        return pathlib.Path(source_path), None
    target = local_source_path(source_path, work_root, source_video_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = rsync_copy_command(source_path, target)
    if dry_run:
        return target, cmd
    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"failed to copy source to local scratch: {' '.join(cmd)}")
    return target, cmd


def av1_options(encoder, crf, preset):
    if encoder == "libaom-av1":
        return ["-c:v", encoder, "-crf", str(crf), "-cpu-used", str(preset)]
    if encoder == "libsvtav1":
        return ["-c:v", encoder, "-crf", str(crf), "-preset", str(preset)]
    return ["-c:v", encoder, "-crf", str(crf)]


def build_crop_filter(well_rows):
    split_labels = "".join(f"[s{i}]" for i in range(len(well_rows)))
    parts = [f"[0:v]format=gray,split={len(well_rows)}{split_labels}"]
    for i, row in enumerate(well_rows):
        x0 = int(float(row["roi_x0"]))
        y0 = int(float(row["roi_y0"]))
        width = int(float(row.get("roi_width") or (float(row["roi_x1"]) - float(row["roi_x0"]))))
        height = int(float(row.get("roi_height") or (float(row["roi_y1"]) - float(row["roi_y0"]))))
        parts.append(f"[s{i}]crop={width}:{height}:{x0}:{y0}[v{i}]")
    return ";".join(parts)


def final_and_partial_paths(well_rows):
    pairs = []
    for row in well_rows:
        final = pathlib.Path(row["well_archive_path"])
        partial = final.with_name(f"{final.stem}.partial{final.suffix}")
        pairs.append((final, partial))
    return pairs


def build_ffmpeg_command(ffmpeg, source_path, well_rows, encoder, crf, preset, force):
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error"]
    if force:
        cmd.append("-y")
    else:
        cmd.append("-n")
    cmd.extend(["-i", str(source_path), "-filter_complex", build_crop_filter(well_rows)])
    for i, (_final, partial) in enumerate(final_and_partial_paths(well_rows)):
        partial.parent.mkdir(parents=True, exist_ok=True)
        cmd.extend(["-map", f"[v{i}]", "-an"])
        cmd.extend(av1_options(encoder, crf, preset))
        cmd.append(str(partial))
    return cmd


def outputs_complete(well_rows):
    return all(pathlib.Path(row["well_archive_path"]).exists() for row in well_rows)


def existing_final_outputs(well_rows):
    return [pathlib.Path(row["well_archive_path"]) for row in well_rows if pathlib.Path(row["well_archive_path"]).exists()]


def promote_partials(well_rows):
    for final, partial in final_and_partial_paths(well_rows):
        if not partial.exists():
            raise RuntimeError(f"missing partial output after ffmpeg: {partial}")
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, final)


def ffprobe_frame_count(ffprobe, path):
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_frames",
        "-show_entries",
        "stream=nb_read_frames",
        "-of",
        "default=nokey=1:noprint_wrappers=1",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    return int(out) if out and out != "N/A" else 0


def ffprobe_duration_seconds(ffprobe, path):
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nokey=1:noprint_wrappers=1",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    duration = float(out) if out and out != "N/A" else 0.0
    if duration <= 0.0:
        raise RuntimeError(f"ffprobe reported no positive duration for {path}")
    return duration


def ffprobe_packet_summary(ffprobe, path):
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_packets",
        "-show_entries",
        "stream=codec_name,width,height,duration,nb_read_packets",
        "-of",
        "json",
        str(path),
    ]
    payload = json.loads(subprocess.check_output(cmd, text=True))
    streams = payload.get("streams") or []
    if len(streams) != 1:
        raise RuntimeError(f"expected one video stream in {path}; observed {len(streams)}")
    stream = streams[0]
    return {
        "codec_name": stream.get("codec_name") or "",
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration_s": float(stream["duration"]) if stream.get("duration") not in (None, "N/A") else None,
        "packet_count": int(stream.get("nb_read_packets") or 0),
    }


def sentinel_indices(n_items, sentinel_count):
    if n_items <= 0 or sentinel_count <= 0:
        return set()
    if sentinel_count >= n_items:
        return set(range(n_items))
    if sentinel_count == 1:
        return {n_items // 2}
    return {round(i * (n_items - 1) / (sentinel_count - 1)) for i in range(sentinel_count)}


def validate_outputs(ffprobe, well_rows, mode="packet-count-sentinel", sentinel_count=5, decode_sentinels=True):
    if mode not in {"full-decode", "packet-count", "packet-count-sentinel"}:
        raise ValueError(f"unsupported validation mode: {mode}")
    sentinels = sentinel_indices(len(well_rows), sentinel_count) if mode == "packet-count-sentinel" else set()
    rows = []
    for index, row in enumerate(well_rows):
        path = pathlib.Path(row["well_archive_path"])
        if not path.exists():
            raise RuntimeError(f"missing well archive: {path}")
        observed = {
            "well_label": row["well_label"],
            "well_archive_path": str(path),
            "bytes": path.stat().st_size,
            "validation_mode": mode,
        }
        if mode == "full-decode":
            frames = ffprobe_frame_count(ffprobe, path)
            if frames <= 0:
                raise RuntimeError(f"ffprobe reported no decoded frames for {path}")
            observed.update({"frame_count": frames, "count_basis": "decoded_frames", "sentinel_full_decode": True})
        else:
            summary = ffprobe_packet_summary(ffprobe, path)
            expected_width = int(float(row.get("roi_width") or (float(row["roi_x1"]) - float(row["roi_x0"]))))
            expected_height = int(float(row.get("roi_height") or (float(row["roi_y1"]) - float(row["roi_y0"]))))
            if summary["codec_name"] != "av1":
                raise RuntimeError(f"expected AV1 video in {path}; observed {summary['codec_name']!r}")
            if (summary["width"], summary["height"]) != (expected_width, expected_height):
                raise RuntimeError(
                    f"unexpected geometry for {path}: observed {summary['width']}x{summary['height']}, "
                    f"expected {expected_width}x{expected_height}"
                )
            if summary["packet_count"] <= 0:
                raise RuntimeError(f"ffprobe reported no video packets for {path}")
            observed.update(summary)
            observed.update({"frame_count": summary["packet_count"], "count_basis": "video_packets"})
            is_sentinel = index in sentinels
            observed["sentinel_full_decode"] = bool(is_sentinel and decode_sentinels)
            if is_sentinel and decode_sentinels:
                decoded_frames = ffprobe_frame_count(ffprobe, path)
                if decoded_frames != summary["packet_count"]:
                    raise RuntimeError(
                        f"decoded frame count differs from packet count for {path}: "
                        f"{decoded_frames} != {summary['packet_count']}"
                    )
                observed["decoded_frame_count"] = decoded_frames
        rows.append(observed)
    counts = {row["frame_count"] for row in rows}
    if len(counts) != 1:
        raise RuntimeError(f"well output frame/packet counts differ: {sorted(counts)}")
    return rows


def run_sidecars(sidecar_bin, well_rows, force):
    rows = []
    for row in well_rows:
        archive = pathlib.Path(row["well_archive_path"])
        sidecar_dir = pathlib.Path(row["well_sidecar_dir"])
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sidecar_bin,
            "--input",
            str(archive),
            "--output-dir",
            str(sidecar_dir),
            "--frame-diff-threshold",
            row.get("frame_diff_threshold") or "10",
            "--vector-output",
            row.get("sidecar_vector_output") or "none",
            "--vector-format",
            row.get("sidecar_vector_format") or "bin",
        ]
        if force:
            cmd.append("--force")
        started = time.monotonic()
        completed = subprocess.run(cmd, check=False)
        rows.append(
            {
                "well_label": row["well_label"],
                "sidecar_dir": str(sidecar_dir),
                "returncode": completed.returncode,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "domain": "archive_av1_decode",
            }
        )
        if completed.returncode != 0:
            raise RuntimeError(f"sidecar failed for {archive}: returncode {completed.returncode}")
    return rows


def write_json(path, payload):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_one_plate(args, plate_row, task_index, task_count, chunk_index=None, chunk_size=1):
    manifest_well_rows = rows_for_source(args.well_manifest, plate_row["source_video_id"])
    source_path = resolve_source_path(plate_row, args.staged_input_root)
    output_dir = pathlib.Path(plate_row["output_dir"])
    work_root = work_root_from_args(args)
    local_output_dir = pathlib.Path(work_root) / "output" / plate_row["source_video_id"] if work_root else output_dir
    well_rows = map_well_rows_to_output_dir(manifest_well_rows, local_output_dir)
    log_path = output_dir / "manifest" / "archival_plate_task.json"
    validation_path = output_dir / "manifest" / "archival_validation.json"

    payload = {
        "plate_manifest": str(pathlib.Path(args.plate_manifest).resolve()),
        "well_manifest": str(pathlib.Path(args.well_manifest).resolve()),
        "task_index": task_index,
        "task_count": task_count,
        "chunk_index": chunk_index,
        "chunk_size": chunk_size,
        "source_video_id": plate_row["source_video_id"],
        "source_path": str(source_path),
        "output_dir": str(output_dir),
        "work_root": str(work_root) if work_root else "",
        "local_output_dir": str(local_output_dir),
        "n_wells": len(well_rows),
        "encoder": args.encoder,
        "crf": args.crf,
        "preset": args.preset,
        "validation_mode": args.validation_mode,
        "validation_sentinel_count": args.validation_sentinel_count,
        "max_source_duration_seconds": args.max_source_duration_seconds,
        "run_sidecar": args.run_sidecar,
        "started_monotonic": time.monotonic(),
    }

    if not source_path.exists():
        payload["status"] = "missing_source"
        write_json(log_path, payload)
        raise SystemExit(f"source video does not exist: {source_path}")

    source_duration_seconds = ffprobe_duration_seconds(args.ffprobe, source_path)
    payload["source_duration_seconds"] = source_duration_seconds
    if args.max_source_duration_seconds > 0 and source_duration_seconds > args.max_source_duration_seconds:
        payload["status"] = "source_duration_exceeds_limit"
        write_json(log_path, payload)
        raise SystemExit(
            f"source duration {source_duration_seconds:.3f}s exceeds "
            f"limit {args.max_source_duration_seconds:.3f}s"
        )

    if outputs_complete(manifest_well_rows) and not args.force:
        payload["status"] = "skipped_existing"
        payload["validation"] = validate_outputs(
            args.ffprobe,
            manifest_well_rows,
            mode=args.validation_mode,
            sentinel_count=args.validation_sentinel_count,
        )
        write_json(validation_path, payload)
        write_json(log_path, payload)
        return payload

    existing = existing_final_outputs(manifest_well_rows)
    if existing and not args.force:
        payload["status"] = "partial_existing_outputs"
        payload["existing_outputs"] = [str(path) for path in existing]
        write_json(log_path, payload)
        raise SystemExit("some well outputs already exist; pass --force or resolve partial outputs")

    local_source, source_copy_cmd = prepare_local_source(source_path, work_root, plate_row["source_video_id"], args.dry_run)
    if source_copy_cmd:
        payload["source_copy_command"] = source_copy_cmd
    cmd = build_ffmpeg_command(args.ffmpeg, local_source, well_rows, args.encoder, args.crf, args.preset, args.force)
    payload["ffmpeg_command"] = cmd
    if args.dry_run:
        payload["status"] = "dry_run"
        write_json(log_path, payload)
        print(" ".join(cmd))
        return payload

    started = time.monotonic()
    completed = subprocess.run(cmd, check=False)
    payload["ffmpeg_elapsed_seconds"] = round(time.monotonic() - started, 6)
    payload["ffmpeg_returncode"] = completed.returncode
    if completed.returncode != 0:
        payload["status"] = "ffmpeg_failed"
        write_json(log_path, payload)
        raise SystemExit(completed.returncode)

    promote_partials(well_rows)
    validation_rows = validate_outputs(
        args.ffprobe,
        well_rows,
        mode=args.validation_mode,
        sentinel_count=args.validation_sentinel_count,
    )
    payload["validation"] = validation_rows
    if args.run_sidecar:
        payload["sidecars"] = run_sidecars(args.sidecar_bin, well_rows, args.force)
    payload["status"] = "validated"
    payload["elapsed_seconds"] = round(time.monotonic() - payload["started_monotonic"], 6)
    if work_root:
        local_validation_path = local_output_dir / "manifest" / "archival_validation.json"
        local_log_path = local_output_dir / "manifest" / "archival_plate_task.json"
        write_json(local_validation_path, payload)
        write_json(local_log_path, payload)
        rsync_cmd = rsync_tree_command(local_output_dir, output_dir)
        payload["output_rsync_command"] = rsync_cmd
        output_dir.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(rsync_cmd, check=False)
        if completed.returncode != 0:
            payload["status"] = "output_rsync_failed"
            payload["output_rsync_returncode"] = completed.returncode
            write_json(log_path, payload)
            raise SystemExit(completed.returncode)
        payload["final_validation"] = validate_outputs(
            args.ffprobe,
            manifest_well_rows,
            mode=args.validation_mode,
            sentinel_count=args.validation_sentinel_count,
            decode_sentinels=False,
        )
        if not args.keep_local_work:
            payload["cleaned_local_work"] = cleanup_local_work(work_root, plate_row["source_video_id"])
        payload["status"] = "validated"
    write_json(validation_path, payload)
    write_json(log_path, payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description="Run one SGE/array plate archival task.")
    parser.add_argument("--plate-manifest", required=True)
    parser.add_argument("--well-manifest", required=True)
    parser.add_argument("--task-index", type=int, default=None)
    parser.add_argument("--chunk-size", type=int, default=1, help="Number of plate-manifest rows to process serially per array task.")
    parser.add_argument("--staged-input-root", default="")
    parser.add_argument("--work-root", default=os.environ.get("TMPDIR", ""), help="Job-local work root; defaults to TMPDIR when set.")
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_BIN", "ffmpeg"))
    parser.add_argument("--ffprobe", default=os.environ.get("FFPROBE_BIN", "ffprobe"))
    parser.add_argument("--encoder", default="libaom-av1")
    parser.add_argument("--crf", type=int, default=35)
    parser.add_argument("--preset", type=int, default=8)
    parser.add_argument(
        "--validation-mode",
        choices=("full-decode", "packet-count", "packet-count-sentinel"),
        default="packet-count-sentinel",
        help="Output validation tier; packet-count-sentinel fully decodes a deterministic subset.",
    )
    parser.add_argument("--validation-sentinel-count", type=int, default=5)
    parser.add_argument(
        "--max-source-duration-seconds",
        type=float,
        default=3600.0,
        help="Reject longer sources before copying/encoding; set 0 to disable.",
    )
    parser.add_argument("--run-sidecar", action="store_true")
    parser.add_argument("--sidecar-bin", default=os.environ.get("MESTIMATE_SIDECAR_BIN", "mestimate-sidecar"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--keep-local-work", action="store_true", help="Keep per-plate files under TMPDIR after successful rsync.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    task_index = args.task_index if args.task_index is not None else task_index_from_env()
    plate_rows, plate_count, chunk_count = select_plate_rows_for_chunk(args.plate_manifest, task_index, args.chunk_size)
    chunk_payloads = []
    for plate_row in plate_rows:
        chunk_payloads.append(
            run_one_plate(
                args,
                plate_row,
                task_index=task_index,
                task_count=plate_count,
                chunk_index=task_index,
                chunk_size=args.chunk_size,
            )
        )
    if args.chunk_size > 1:
        summary_dir = pathlib.Path(plate_rows[0]["output_dir"]).parent / "_array_chunks"
        write_json(
            summary_dir / f"chunk_{task_index:05d}.json",
            {
                "chunk_index": task_index,
                "chunk_count": chunk_count,
                "chunk_size": args.chunk_size,
                "plate_count": plate_count,
                "source_video_ids": [row["source_video_id"] for row in plate_rows],
                "statuses": {payload["source_video_id"]: payload["status"] for payload in chunk_payloads},
            },
        )


if __name__ == "__main__":
    main()
