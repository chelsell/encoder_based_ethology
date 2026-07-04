# Repository cleaning audit

## Immediate answer for Wynton

The full working tree does not need to be copied to Wynton. The current Wynton
build does need source files because `mestimate_sidecar.def` copies C/CMake/test
inputs into the image at build time, and the SGE wrapper runs Python scripts from
a repository-like directory.

Use `scripts/make_wynton_deploy_bundle.sh` to create a small source bundle for
Wynton instead of rsyncing the full repo:

```bash
ALLOW_DIRTY=1 scripts/make_wynton_deploy_bundle.sh
```

After the current work is committed, omit `ALLOW_DIRTY=1`. The bundle defaults
to:

```text
/media/ssd1/tmp/encoder_based_ethology_deploy/encoder_based_ethology_<commit>.tar.gz
/media/ssd1/tmp/encoder_based_ethology_deploy/encoder_based_ethology_<commit>.manifest.json
```

On Wynton:

```bash
mkdir -p /wynton/scratch/$USER/encoder_based_ethology/source
cd /wynton/scratch/$USER/encoder_based_ethology/source
tar -xzf /path/to/encoder_based_ethology_<commit>.tar.gz
cd encoder_based_ethology_<commit>
scripts/build_wynton_container.sh
```

## Required production surface

These files are needed for the current Wynton build/run path:

- `CMakeLists.txt`
- `include/mestimate_sidecar.h`
- `src/mestimate_sidecar.c`
- `mestimate_sidecar.def`
- `tests/make_synthetic_translation.py`
- `tests/test_synthetic_translation.sh`
- `tests/test_schema.py`
- `scripts/archival_plate_array.sge`
- `scripts/build_wynton_container.sh`
- `scripts/make_well_archival_manifest.py`
- `scripts/manage_archival_sge_queue.py`
- `scripts/run_archival_plate_task.py`
- focused orchestrator tests:
  - `tests/test_archival_plate_task.py`
  - `tests/test_manage_archival_sge_queue.py`
  - `tests/test_make_well_archival_manifest.py`

The docs are not required to execute, but the deploy bundle includes the Wynton
and SGE docs because they are operational runbooks.

## Generated local material

These should not be copied to Wynton as source and should not be committed:

- `mestimate_sidecar.sandbox/` (~1 GB)
- `mestimate_sidecar.sandbox.broken_20260702/`
- `build/`
- `build-current/`
- `.pytest_cache/`
- `scripts/__pycache__/`
- `tests/__pycache__/`
- large generated manifests under `/media/ssd1/tmp/encoder_based_ethology_manifests/`
- generated deploy bundles under `/media/ssd1/tmp/encoder_based_ethology_deploy/`

The generated reports under `reports/` are scientifically useful examples, but
they should be treated as outputs. If they are needed for a paper trail, move
them to an output archive or a dedicated results branch rather than mixing them
with production pipeline code.

## Candidate stale or exploratory surface

These are not part of the Wynton archival transcode path. Keep them only if they
remain useful for active analysis, otherwise move them under
`notebooks/`, `experiments/`, or an archived branch:

- `ffmpeg_build.def`
- `make_mestimate_overlays.sh`
- Slurm-only sidecar helpers:
  - `scripts/sidecar_array.sbatch`
  - `scripts/make_sidecar_slurm_manifest.py`
  - `scripts/run_sidecar_manifest_task.py`
- exploratory MV/cd10 analysis scripts:
  - `scripts/analyze_mv_derived_feature_profiles.py`
  - `scripts/analyze_mv_feature_separability.py`
  - `scripts/analyze_mv_target_separability.py`
  - `scripts/analyze_trace_specificity.py`
  - `scripts/compare_well_motion_cd10.py`
  - `scripts/inspect_cd10_smvo_disagreements.py`
  - `scripts/iterate_mv_feature_candidates.py`
  - `scripts/make_disagreement_clip_gallery.py`
  - `scripts/scan_motion_filter_thresholds.py`
  - `scripts/screen_mv_features_for_stimulus_timing.py`

Several of those scripts are still useful for method development, but they
should not be implied to be production dependencies for the Wynton archival run.

## Recommended cleanup order

1. Commit the current production Wynton/orchestrator changes.
2. Add or update `.gitignore` for sandbox directories, reports, caches, and
   deploy bundles.
3. Move existing local generated directories out of the repo or remove them
   after confirming they are backed up.
4. Create a `scripts/experimental/` or `experiments/` area for exploratory
   analysis scripts that are not in the production path.
5. Keep the Wynton deployment bundle as the authority for what must be present
   on the cluster.
