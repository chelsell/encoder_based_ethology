# Cliptriage annotation import

This repository consumes human clip-triage labels from the sibling
`well_annotation` project without importing that package or depending on its web
server. The adapter reads the SQLite database directly and exports stable CSV
tables for analysis.

Default import:

```bash
python scripts/import_cliptriage_annotations.py \
  --database /home/cole/code/well_annotation/data/annotations/cliptriage.sqlite \
  --output-dir data/annotations/cliptriage_import
```

For an exported bundle matching `docs/annotation_spec.md`:

```bash
python scripts/import_cliptriage_annotations.py \
  --bundle-dir /path/to/cliptriage_export_<dataset>_<date> \
  --output-dir data/annotations/cliptriage_import
```

To restrict to one annotation task or taxonomy version:

```bash
python scripts/import_cliptriage_annotations.py \
  --database /home/cole/code/well_annotation/data/annotations/cliptriage.sqlite \
  --output-dir data/annotations/cliptriage_import_v0_2 \
  --task-id v0_2_smoke \
  --taxonomy-version v0.2
```

Outputs:

```text
cliptriage_annotation_history.csv
cliptriage_current_annotations.csv
cliptriage_clip_label_summary.csv
cliptriage_annotation_import_metadata.json
```

## Units

`cliptriage_annotation_history.csv`

One row per append-only annotation event. This keeps corrections and repeated
actions visible.

`cliptriage_current_annotations.csv`

One row per latest annotation under the selected scope. The default scope is
`presentation_annotator_task`, meaning one current event per displayed clip
presentation per annotator per task. This matches the bundle spec's
`latest_annotations.parquet` rule.

`cliptriage_clip_label_summary.csv`

One row per clip, summarizing the current labels across annotators. If all
non-skipped current annotations agree, `consensus_primary_label_path` is filled.
Otherwise `has_label_disagreement` is `1`.

## Provenance

The importer carries forward clip provenance from the annotator manifest JSON,
including:

```text
source_video_id
source_path
run
run_tag
well
well_label
start_frame
end_frame_exclusive
start_time_s
end_time_s
duration_s
fps_nominal
sampling_stratum
roi_x0
roi_y0
roi_x1
roi_y1
```

This is enough to join labels to motion-sidecar features by source video, well,
and time window.

Human labels describe visible clip content under the annotation taxonomy. They
are not prevalence estimates or validated biological outcomes.
