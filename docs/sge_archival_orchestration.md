# SGE archival orchestration

This workflow is for a CPU SGE cluster with limited scratch space. The scheduler
unit is one source plate HEVC video. Each task decodes that source once, crops
all wells from a versioned ROI table, writes per-well AV1 outputs, validates the
outputs, and optionally runs per-well sidecar summaries.

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

Use `dt1.wynton.ucsf.edu` or `dt2.wynton.ucsf.edu` for manifest, source-video,
container, and result transfers. Reserve `log1`/`log2` for lightweight `qsub`,
`qstat`, and filesystem checks. Wynton global scratch is not archival storage:
files that are not modified for two weeks are eligible for automatic deletion.

## 3. Submit the SGE array

```bash
python scripts/manage_archival_sge_queue.py submit \
  --plate-manifest /wynton/scratch/$USER/encoder_based_ethology/manifests/valar_96_well_analysis100/plate_archival_jobs.csv \
  --well-manifest /wynton/scratch/$USER/encoder_based_ethology/manifests/valar_96_well_analysis100/well_archival_outputs.csv \
  --staged-input-root /wynton/scratch/$USER/encoder_based_ethology/staged_hevc \
  --repo-dir /wynton/scratch/$USER/encoder_based_ethology/source/encoder_based_ethology_<commit> \
  --image /path/to/archival_pipeline.sif \
  --apptainer-extra-bind /wynton/scratch \
  --chunk-size 10 \
  --max-concurrent 5 \
  --plate-count 6481 \
  --dry-run
```

Remove `--dry-run` to call `qsub`. The generated command submits
`scripts/archival_plate_array.sge` with an array range over chunks. With
`--chunk-size 10`, SGE task 1 processes plate rows 1-10 serially, task 2
processes rows 11-20, and so on. `--max-concurrent` emits SGE `-tc` to cap the
number of simultaneously running array tasks. This avoids submitting or running
one independent job per source video while still letting each CPU process one
video at a time.

`--plate-count` is only needed when printing a dry-run command from a machine
that cannot read the Wynton manifest path. When running the submit command on
Wynton after the manifests have been copied, it can be omitted and the script
will count rows directly from `--plate-manifest`.

The SGE script requests one slot, `mem_free=6G`, `scratch=200G`, and
`h_rt=24:00:00` by default. Adjust `scripts/archival_plate_array.sge` or submit
flags if a benchmarked plate requires more local scratch or runtime.

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
  --final-output-root /archive/encoder_based_ethology/runs \
  --dry-run
```

Collection uses:

```text
rsync -a --partial --remove-source-files <cluster_output_dir>/ <final_output_dir>/
```

This is the intended cluster-side file removal path for validated outputs.

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
- A production full-pipeline image should include this repository's scripts,
  FFmpeg/ffprobe, and `mestimate-sidecar`; the current sidecar SIF is labeled as
  sidecar-only.
- If only a subset of well outputs exists for a plate and `--force` is not set,
  the worker stops rather than silently mixing old and new outputs.
