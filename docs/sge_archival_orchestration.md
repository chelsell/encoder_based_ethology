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

For the current `valar_96_well_analysis100` export, `--skip-missing-source-path`
omits 81 sources that have no archive source path, leaving 6,481 plate tasks and
622,176 expected well outputs.

Here `analysis100` means the ROI export was generated with
`--analysis-scale 1.0`, not that it is version 1. The ROI geometry version is
recorded separately in the manifest rows, for example
`plate_affine_legacy_initialized_v0_1`.

## 2. Stage a bounded number of HEVCs

```bash
python scripts/manage_archival_sge_queue.py stage \
  --plate-manifest /media/ssd1/tmp/encoder_based_ethology_manifests/valar_96_well_analysis100/plate_archival_jobs.csv \
  --staged-input-root /cluster/scratch/$USER/staged_hevc \
  --max-staged 20 \
  --dry-run
```

Staging uses `rsync -a --partial --ignore-existing`. It does not remove the
archive copy.

## 3. Submit the SGE array

```bash
python scripts/manage_archival_sge_queue.py submit \
  --plate-manifest /media/ssd1/tmp/encoder_based_ethology_manifests/valar_96_well_analysis100/plate_archival_jobs.csv \
  --well-manifest /media/ssd1/tmp/encoder_based_ethology_manifests/valar_96_well_analysis100/well_archival_outputs.csv \
  --staged-input-root /cluster/scratch/$USER/staged_hevc \
  --repo-dir /home/cole/code/encoder_based_ethology \
  --image /path/to/archival_pipeline.sif \
  --apptainer-extra-bind /cluster/scratch \
  --chunk-size 10 \
  --max-concurrent 5 \
  --dry-run
```

Remove `--dry-run` to call `qsub`. The generated command submits
`scripts/archival_plate_array.sge` with an array range over chunks. With
`--chunk-size 10`, SGE task 1 processes plate rows 1-10 serially, task 2
processes rows 11-20, and so on. `--max-concurrent` emits SGE `-tc` to cap the
number of simultaneously running array tasks. This avoids submitting or running
one independent job per source video while still letting each CPU process one
video at a time.

The SGE script requests one slot, `mem_free=6G`, `scratch=200G`, and
`h_rt=24:00:00` by default. Adjust `scripts/archival_plate_array.sge` or submit
flags if a benchmarked plate requires more local scratch or runtime.

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
  --plate-manifest /media/ssd1/tmp/encoder_based_ethology_manifests/valar_96_well_analysis100/plate_archival_jobs.csv \
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
  --plate-manifest /media/ssd1/tmp/encoder_based_ethology_manifests/valar_96_well_analysis100/plate_archival_jobs.csv \
  --staged-input-root /cluster/scratch/$USER/staged_hevc \
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
