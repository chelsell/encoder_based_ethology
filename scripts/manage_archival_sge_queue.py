#!/usr/bin/env python3
import argparse
import csv
import json
import math
import pathlib
import shlex
import subprocess


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def staged_source_path(row, staged_input_root):
    return pathlib.Path(staged_input_root) / row["source_video_id"] / pathlib.Path(row["source_path"]).name


def final_output_dir(row, final_output_root):
    return pathlib.Path(final_output_root) / row["source_video_id"]


def validation_path(row):
    return pathlib.Path(row["output_dir"]) / "manifest" / "archival_validation.json"


def read_validation(row):
    path = validation_path(row)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def rsync_stage_command(source_path, staged_path):
    return [
        "rsync",
        "-a",
        "--partial",
        "--ignore-existing",
        str(source_path),
        str(staged_path),
    ]


def remote_staged_source_path(row, staged_input_root):
    return f"{staged_input_root.rstrip('/')}/{row['source_video_id']}/{pathlib.Path(row['source_path']).name}"


def remote_parent(path):
    return path.rsplit("/", 1)[0]


def rsync_stage_push_command(source_path, remote_host, remote_path, ssh_command="ssh"):
    remote_dir = remote_parent(remote_path)
    return [
        "rsync",
        "-a",
        "--partial",
        "--ignore-existing",
        "-e",
        ssh_command,
        "--rsync-path",
        f"mkdir -p {remote_dir} && rsync",
        str(source_path),
        f"{remote_host}:{remote_path}",
    ]


def rsync_collect_command(cluster_output_dir, final_dir):
    return [
        "rsync",
        "-a",
        "--partial",
        "--remove-source-files",
        f"{cluster_output_dir}/",
        f"{final_dir}/",
    ]


def rsync_move_file_command(source_file, dest_dir):
    return [
        "rsync",
        "-a",
        "--partial",
        "--remove-source-files",
        str(source_file),
        f"{dest_dir}/",
    ]


def qsub_command(args, plate_count):
    chunk_size = max(1, int(args.chunk_size))
    task_count = math.ceil(plate_count / chunk_size)
    env = {
        "REPO_DIR": str(pathlib.Path(args.repo_dir).resolve()),
        "PLATE_MANIFEST": str(pathlib.Path(args.plate_manifest).resolve()),
        "WELL_MANIFEST": str(pathlib.Path(args.well_manifest).resolve()),
        "STAGED_INPUT_ROOT": str(pathlib.Path(args.staged_input_root).resolve()) if args.staged_input_root else "",
        "IMAGE": str(pathlib.Path(args.image).resolve()) if args.image else "",
        "APPTAINER_EXTRA_BIND": args.apptainer_extra_bind,
        "RUN_SIDECAR": "1" if args.run_sidecar else "0",
        "CHUNK_SIZE": str(chunk_size),
        "ENCODER": args.encoder,
        "CRF": str(args.crf),
        "PRESET": str(args.preset),
        "VALIDATION_MODE": args.validation_mode,
        "VALIDATION_SENTINEL_COUNT": str(args.validation_sentinel_count),
        "MAX_SOURCE_DURATION_SECONDS": str(args.max_source_duration_seconds),
    }
    env_arg = ",".join(f"{k}={v}" for k, v in env.items())
    cmd = ["qsub", "-t", f"1-{task_count}"]
    if args.max_concurrent:
        cmd.extend(["-tc", str(args.max_concurrent)])
    cmd.extend(["-v", env_arg, args.sge_script])
    return cmd


def run_or_print(cmd, dry_run):
    print(shlex.join(cmd))
    if not dry_run:
        subprocess.run(cmd, check=True)


def cmd_stage(args):
    rows = read_csv(args.plate_manifest)
    staged_root = pathlib.Path(args.staged_input_root)
    staged_existing = sum(1 for row in rows if staged_source_path(row, staged_root).exists())
    budget = max(0, args.max_staged - staged_existing) if args.max_staged else len(rows)
    staged = 0
    for row in rows:
        if staged >= budget:
            break
        dest = staged_source_path(row, staged_root)
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        run_or_print(rsync_stage_command(pathlib.Path(row["source_path"]), dest), args.dry_run)
        staged += 1
    print(json.dumps({"already_staged": staged_existing, "newly_staged": staged, "max_staged": args.max_staged}, indent=2))


def cmd_stage_push(args):
    rows = read_csv(args.plate_manifest)
    staged = 0
    missing = 0
    for row in rows:
        if args.max_staged and staged >= args.max_staged:
            break
        source = pathlib.Path(row["source_path"])
        if not source.exists():
            missing += 1
            continue
        remote_path = remote_staged_source_path(row, args.remote_staged_input_root)
        run_or_print(rsync_stage_push_command(source, args.remote_host, remote_path, args.ssh_command), args.dry_run)
        staged += 1
    print(json.dumps({"pushed_or_requested": staged, "missing_local_sources": missing, "max_staged": args.max_staged}, indent=2))


def cmd_submit(args):
    if args.plate_count:
        plate_count = args.plate_count
    else:
        rows = read_csv(args.plate_manifest)
        plate_count = len(rows)
    cmd = qsub_command(args, plate_count)
    run_or_print(cmd, args.dry_run)


def cmd_collect(args):
    rows = read_csv(args.plate_manifest)
    collected = 0
    skipped = 0
    for row in rows:
        validation = read_validation(row)
        if not validation or validation.get("status") != "validated":
            skipped += 1
            continue
        final_dir = final_output_dir(row, args.final_output_root)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        run_or_print(rsync_collect_command(pathlib.Path(row["output_dir"]), final_dir), args.dry_run)
        collected += 1
    print(json.dumps({"validated_collected": collected, "not_ready": skipped}, indent=2))


def cmd_retire_staged_inputs(args):
    rows = read_csv(args.plate_manifest)
    retired_count = 0
    skipped = 0
    for row in rows:
        validation = read_validation(row)
        if not validation or validation.get("status") != "validated":
            skipped += 1
            continue
        staged = staged_source_path(row, args.staged_input_root)
        if not staged.exists():
            skipped += 1
            continue
        retired_dir = pathlib.Path(args.retired_input_root) / row["source_video_id"]
        retired_dir.mkdir(parents=True, exist_ok=True)
        run_or_print(rsync_move_file_command(staged, retired_dir), args.dry_run)
        retired_count += 1
    print(json.dumps({"retired_staged_inputs": retired_count, "skipped": skipped}, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Manage bounded SGE archival staging and collection.")
    sub = parser.add_subparsers(dest="command", required=True)

    stage = sub.add_parser("stage", help="Stage source videos onto cluster scratch.")
    stage.add_argument("--plate-manifest", required=True)
    stage.add_argument("--staged-input-root", required=True)
    stage.add_argument("--max-staged", type=int, default=0, help="Maximum staged source videos; 0 means no cap.")
    stage.add_argument("--dry-run", action="store_true")
    stage.set_defaults(func=cmd_stage)

    stage_push = sub.add_parser(
        "stage-push",
        help="Push source videos from a machine that can see the video store to Wynton scratch over rsync/ssh.",
    )
    stage_push.add_argument("--plate-manifest", required=True)
    stage_push.add_argument(
        "--remote-host",
        required=True,
        help="SSH data-transfer host, e.g. user@dt2.wynton.ucsf.edu. Do not use a Wynton login node for bulk data.",
    )
    stage_push.add_argument(
        "--remote-staged-input-root",
        required=True,
        help="Cluster-visible staged root, e.g. /wynton/scratch/$USER/encoder_based_ethology/staged_hevc.",
    )
    stage_push.add_argument("--max-staged", type=int, default=0, help="Maximum source videos to push; 0 means no cap.")
    stage_push.add_argument("--ssh-command", default="ssh")
    stage_push.add_argument("--dry-run", action="store_true")
    stage_push.set_defaults(func=cmd_stage_push)

    submit = sub.add_parser("submit", help="Print or run qsub for the plate array.")
    submit.add_argument("--plate-manifest", required=True)
    submit.add_argument("--well-manifest", required=True)
    submit.add_argument("--staged-input-root", default="")
    submit.add_argument("--repo-dir", default=".")
    submit.add_argument("--image", default="")
    submit.add_argument("--apptainer-extra-bind", default="", help="Additional same-path bind, e.g. /scratch or /cluster.")
    submit.add_argument("--sge-script", default="scripts/archival_plate_array.sge")
    submit.add_argument("--encoder", default="libaom-av1")
    submit.add_argument("--crf", type=int, default=35)
    submit.add_argument("--preset", type=int, default=8)
    submit.add_argument(
        "--validation-mode",
        choices=("full-decode", "packet-count", "packet-count-sentinel"),
        default="packet-count-sentinel",
    )
    submit.add_argument("--validation-sentinel-count", type=int, default=5)
    submit.add_argument(
        "--max-source-duration-seconds",
        type=float,
        default=3600.0,
        help="Reject longer sources before encoding; set 0 to disable.",
    )
    submit.add_argument("--run-sidecar", action="store_true")
    submit.add_argument("--chunk-size", type=int, default=1, help="Plate videos processed serially by each SGE task.")
    submit.add_argument("--max-concurrent", type=int, default=0, help="SGE -tc concurrency cap for array tasks; 0 omits -tc.")
    submit.add_argument(
        "--plate-count",
        type=int,
        default=0,
        help="Expected plate rows. Useful for dry-run from a machine that cannot read the cluster-side manifest path.",
    )
    submit.add_argument("--dry-run", action="store_true")
    submit.set_defaults(func=cmd_submit)

    collect = sub.add_parser("collect", help="Collect validated outputs using rsync --remove-source-files.")
    collect.add_argument("--plate-manifest", required=True)
    collect.add_argument("--final-output-root", required=True)
    collect.add_argument("--dry-run", action="store_true")
    collect.set_defaults(func=cmd_collect)

    retire = sub.add_parser("retire-staged-inputs", help="Move staged inputs with rsync --remove-source-files after validation.")
    retire.add_argument("--plate-manifest", required=True)
    retire.add_argument("--staged-input-root", required=True)
    retire.add_argument("--retired-input-root", required=True)
    retire.add_argument("--dry-run", action="store_true")
    retire.set_defaults(func=cmd_retire_staged_inputs)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
