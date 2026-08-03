# SGE archival orchestration

This workflow is for a CPU SGE cluster with limited scratch space. The scheduler
unit is one source plate HEVC video. With the default one encoder process, each
task decodes that source once, crops all wells from a versioned ROI table,
writes per-well AV1 outputs, validates the outputs, and optionally runs per-well
sidecar summaries. Configuring multiple encoder processes repeats the source
decode once per process group, as described below.

The current working architecture retains the 96 independent well videos as the
candidate durable video representation. Whole-plate AV1 remains a comparison
arm. The independent outputs cost 1.74 times as many bytes in a controlled
10-second sample, a premium currently accepted for further end-to-end testing.

On Wynton, each SGE task uses job-local `$TMPDIR` for the active HEVC copy,
partial AV1 outputs, validation, and optional sidecar work. Validated outputs
are then rsynced back to the shared output directory recorded in the manifest.
This keeps heavy encode I/O off shared storage during processing.

## 1. Build manifests

Use the independent ROI-record table as `--roi-table`. The smaller
ROI-solution table is per source video; pass it with `--roi-solution-table` for
provenance, but it is not the per-well crop table.

```bash
python scripts/make_well_archival_manifest.py \
  --source-catalog /home/cole/code/ROI_improvement/data/catalog/valar_96_well_source_catalog.csv \
  --roi-table /home/cole/code/ROI_improvement/data/roi_records/valar_96_well_affine_analysis100_roi_records.csv \
  --roi-solution-table /home/cole/code/ROI_improvement/data/roi_solutions/valar_96_well_analysis100/valar_96_well_analysis100_roi_solutions.csv \
  --roi-repo /home/cole/code/ROI_improvement \
  --fail-dirty-roi-repo \
  --skip-missing-source-path \
  --image /path/to/archival_pipeline.sif \
  --output-root /wynton/scratch/$USER/encoder_based_ethology/valar_96_well_analysis100/runs \
  --well-manifest /media/ssd1/tmp/encoder_based_ethology_manifests/valar_96_well_analysis100/well_archival_outputs.csv
```

This writes `plate_archival_jobs.csv` next to the well manifest. Use the plate
manifest for SGE; use the well manifest for expected outputs and provenance.
Before submitting on Wynton, copy both manifest files to the cluster-visible
manifest directory used in the submission command, for example:

```bash
ssh "$USER@dt2.wynton.ucsf.edu" \
  "mkdir -p /wynton/scratch/$USER/encoder_based_ethology/manifests/valar_96_well_analysis100"

rsync -a --partial \
  /media/ssd1/tmp/encoder_based_ethology_manifests/valar_96_well_analysis100/plate_archival_jobs.csv \
  /media/ssd1/tmp/encoder_based_ethology_manifests/valar_96_well_analysis100/well_archival_outputs.csv \
  "$USER@dt2.wynton.ucsf.edu:/wynton/scratch/$USER/encoder_based_ethology/manifests/valar_96_well_analysis100/"
```

For the current `valar_96_well_analysis100` export, `--skip-missing-source-path`
omits 81 sources that have no archive source path, leaving 6,481 plate tasks and
622,176 expected well outputs.

Here `analysis100` means the ROI export was generated with
`--analysis-scale 1.0`, not that it is version 1. The ROI geometry version is
recorded separately in the manifest rows, for example
`plate_affine_legacy_initialized_v0_1`.

## 2. Stage a bounded number of HEVCs

If the cluster can see the source paths in the manifest, stage from Wynton:

```bash
python scripts/manage_archival_sge_queue.py stage \
  --plate-manifest /wynton/scratch/$USER/encoder_based_ethology/manifests/valar_96_well_analysis100/plate_archival_jobs.csv \
  --staged-input-root /wynton/scratch/$USER/encoder_based_ethology/staged_hevc \
  --max-staged 20 \
  --dry-run
```

If Wynton cannot mount or see the video store, push staging from the machine that
can read `/shire/store`:

```bash
python scripts/manage_archival_sge_queue.py stage-push \
  --plate-manifest /media/ssd1/tmp/encoder_based_ethology_manifests/valar_96_well_analysis100/plate_archival_jobs.csv \
  --remote-host "$USER@dt2.wynton.ucsf.edu" \
  --remote-staged-input-root /wynton/scratch/$USER/encoder_based_ethology/staged_hevc \
  --max-staged 20 \
  --dry-run
```

Staging uses `rsync -a --partial --ignore-existing`. It does not remove the
archive copy. In the push-staging case, the Wynton jobs do not access the video
store or your workstation connection; they read only from the staged HEVC copies
under `/wynton/scratch/$USER/encoder_based_ethology/staged_hevc`.

Use a dedicated Wynton data-transfer node for manifest, source-video, container,
and result transfers. `dt2.wynton.ucsf.edu` is the tested path for this project.
Reserve `log1`/`log2` for lightweight `qsub`, `qstat`, and filesystem checks.
Wynton global scratch is not archival storage: files that are not modified for
two weeks are eligible for automatic deletion.

## 3. Submit the SGE array

```bash
python scripts/manage_archival_sge_queue.py submit \
  --plate-manifest /wynton/scratch/$USER/encoder_based_ethology/manifests/valar_96_well_analysis100/plate_archival_jobs.csv \
  --well-manifest /wynton/scratch/$USER/encoder_based_ethology/manifests/valar_96_well_analysis100/well_archival_outputs.csv \
  --staged-input-root /wynton/scratch/$USER/encoder_based_ethology/staged_hevc \
  --repo-dir /wynton/scratch/$USER/encoder_based_ethology/source/encoder_based_ethology_<commit> \
  --sge-log-dir /wynton/scratch/$USER/encoder_based_ethology/sge_logs/<test-wave> \
  --sge-slots 1 \
  --mem-free 4G \
  --scratch 20G \
  --h-rt 24:00:00 \
  --image /path/to/archival_pipeline.sif \
  --apptainer-extra-bind /wynton/scratch \
  --chunk-size 1 \
  --max-concurrent 5 \
  --encoder-processes 1 \
  --encoder-threads 1 \
  --progress-interval-seconds 30 \
  --validation-mode packet-count-sentinel \
  --validation-sentinel-count 5 \
  --max-source-duration-seconds 3600 \
  --plate-count 6481 \
  --dry-run
```

Remove `--dry-run` to call `qsub`. The generated command submits
`scripts/archival_plate_array.sge` with an array range over chunks. Current
benchmarks use `--chunk-size 1`, so each array task processes exactly one source
plate. `--max-concurrent` emits SGE `-tc` to cap simultaneously running tasks.
Larger serial chunks remain supported, but should not be used until per-plate
runtime and failure recovery are well characterized.

For a real submission, the queue manager creates `--sge-log-dir` before calling
`qsub` and rejects a non-directory at that path. Keep scheduler output outside
the immutable commit checkout. SGE resolves its output target before the job
wrapper runs, so the directory cannot be created reliably from inside the job
itself.

`--plate-count` is only needed when printing a dry-run command from a machine
that cannot read the Wynton manifest path. When running the submit command on
Wynton after the manifests have been copied, it can be omitted and the script
will count rows directly from `--plate-manifest`.

The checked-in SGE script has conservative directives of one slot,
`mem_free=6G`, `scratch=200G`, and `h_rt=24:00:00`. Current direct pilot
submissions override these to one slot, `mem_free=4G`, and `scratch=20G` for
ordinary sources; the p90/p99 compressed-size stress tests request 50G scratch.
Observed maximum virtual memory is about 2.3G. No production scratch default has
been frozen because no full-length task had completed at the benchmark snapshot.

Each AV1 output is explicitly limited to `--encoder-threads` threads.
`--encoder-processes` partitions the wells into balanced groups and runs one
FFmpeg process per group against the node-local source. This repeats source
decode per group but allows independent tiny-well encoders to run concurrently;
the one-process FFmpeg path did not use more than roughly one core even with
libaom `-threads 8`. Input decoding and each output encoder are explicitly
limited to one thread in the process-parallel benchmark.

The submission helper emits `qsub -pe smp <n>` from `--sge-slots` and rejects
`--encoder-processes * --encoder-threads` larger than that allocation. The
worker records `$NSLOTS` and independently rejects an over-threaded launch.
Increasing these values requires a matched throughput benchmark;
one-process/one-thread/one-slot operation is a diagnostic baseline, not a
production throughput default.

The SGE wrapper passes `NSLOTS` explicitly through Apptainer `--cleanenv`; do
not rely on the scheduler environment surviving clean-container launch.

`--mem-free` is a per-slot SGE request. Set it with the slot count in mind;
requesting eight slots at `--mem-free 6G` reserves approximately 48G. `--scratch`
is the node-local work request per task, and `--h-rt` is the hard runtime limit.

Before FFmpeg starts, the worker publishes
`manifest/archival_plate_task.json` with `status: encoding`. FFmpeg writes a
shared heartbeat to `logs/ffmpeg_progress.log` at the configured interval. This
small control-plane write is intentionally on shared scratch so progress remains
visible while large partial videos stay in job-local `$TMPDIR`. For the current
96-output graph, the heartbeat reliably exposes modification time, file growth,
and aggregate FPS, but may be dominated by per-stream quantizer fields before a
useful aggregate `out_time` appears. Treat it as a liveness signal unless the
specific progress keys needed for an ETA are present.

The default output check is `--validation-mode packet-count-sentinel`: every
well is checked for AV1 codec, expected geometry, positive and mutually
consistent video-packet counts, and five deterministic wells spanning the plate
are fully decoded. Local validated outputs are checked again after rsync, without
repeating the sentinel decodes. Use `--validation-mode full-decode` for the older
conservative behavior that fully decodes all 96 wells at both stages. The lighter
default is appropriate only when the source HEVC has an independently verified,
recoverable backup; record that backup identity and checksum before reclaiming
the primary source copy.

Run full-decode and source-versus-AV1 checks on a rolling sentinel subset of
plates even when routine jobs use the lighter tier. The validation mode and
sentinel count are recorded in each plate task manifest.

Test waves default to `--max-source-duration-seconds 3600`. The worker probes
duration before copying a staged source into node-local scratch and records the
observed duration and configured limit. Set the limit to `0` only when longer
sources have been deliberately included in a separately budgeted production
wave.

Set `--run-sidecar` only when you want post-archive AV1-domain sidecar summaries
from the well videos. Source-domain MV extraction from cropped streams is not
implemented in this worker.

For Wynton container rebuilds and disk-budget estimates, see
[wynton_container_and_disk_budget.md](wynton_container_and_disk_budget.md).

## 4. Collect validated outputs

After tasks finish, each successful plate output directory contains:

```text
manifest/archival_plate_task.json
manifest/archival_validation.json
video/<source_id>_<well>.av1.mkv
```

Within each job the path is:

```text
shared staged HEVC
  -> rsync copy to $TMPDIR/input/<source_id>/
  -> encode and validate under $TMPDIR/output/<source_id>/
  -> rsync validated output tree to shared cluster output
  -> remove that source's $TMPDIR input/output directories unless --keep-local-work is set
```

Collect only validated outputs:

```bash
python scripts/manage_archival_sge_queue.py collect \
  --plate-manifest /wynton/scratch/$USER/encoder_based_ethology/manifests/valar_96_well_analysis100/plate_archival_jobs.csv \
  --final-output-root /shire/store/<durable-archival-prefix> \
  --dry-run
```

Collection uses:

```text
rsync -a --partial --remove-source-files <cluster_output_dir>/ <final_output_dir>/
```

This is the intended cluster-side file removal path for validated outputs.
The exact `/shire/store` layout is not frozen. The current candidate is a
versioned product alongside each submission's existing `camera` tree. Never
collect directly over an existing HEVC path, and do not remove a primary source
until its cloud backup identity and checksum have been recorded.

## 5. Retire staged inputs

If staged HEVC files also need to be removed through rsync semantics, use:

```bash
python scripts/manage_archival_sge_queue.py retire-staged-inputs \
  --plate-manifest /wynton/scratch/$USER/encoder_based_ethology/manifests/valar_96_well_analysis100/plate_archival_jobs.csv \
  --staged-input-root /wynton/scratch/$USER/encoder_based_ethology/staged_hevc \
  --retired-input-root /archive/encoder_based_ethology/retired_staged_hevc \
  --dry-run
```

This uses `rsync -a --partial --remove-source-files` on the staged HEVC files.
It copies those staged files to the retired-input root; use it only if that
extra copy is actually wanted. Otherwise leave scratch cleanup to the cluster
retention policy.

## Important limitations

- The worker currently creates AV1 well videos from the source HEVC and can run
  optional sidecars after that. Those sidecars are archive-domain measurements,
  not source-domain cropped-stream measurements.
- `RUN_SIDECAR=0` is used for the active encoder/validation benchmarks. It does
  not produce the required historical source-domain QC product.
- A production full-pipeline image should include this repository's scripts,
  FFmpeg/ffprobe, and `mestimate-sidecar`; the current sidecar SIF is labeled as
  sidecar-only.
- If only a subset of well outputs exists for a plate and `--force` is not set,
  the worker stops rather than silently mixing old and new outputs.
