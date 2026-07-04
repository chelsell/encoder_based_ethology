Recommended Bundle

  cliptriage_export_<dataset>_<date>/
  ├── README.md
  ├── manifest.parquet
  ├── manifest.csv
  ├── taxonomy_v0_2.yaml
  ├── clips/
  │   └── <clip_id[0:2]>/
  │       └── <clip_id>/
  │           ├── preview.mp4
  │           └── clip_metadata.json
  ├── annotations/
  │   ├── annotation_events.parquet
  │   ├── latest_annotations.parquet
  │   ├── presentations.parquet
  │   ├── annotation_sessions.parquet
  │   └── label_summary.csv
  └── provenance/
      ├── sampling_config.yaml
      ├── render_results.csv
      └── export_metadata.json

  Naming Scheme
  Keep clip_id as the stable identity. Current archival layout is good:

  clips/<first_two_chars_of_clip_id>/<clip_id>/preview.mp4
  clips/<first_two_chars_of_clip_id>/<clip_id>/clip_metadata.json

  Example:

  clips/75/751aa4a1ce4e71e8e90d/preview.mp4

  That avoids giant flat directories and keeps URLs/object-storage keys stable.

  Core Tables
  manifest.parquet: one row per stable source clip. Must include source provenance, frame bounds, ROI, sampling stratum, Valar ids, config hash, random seed.

  presentations.parquet: one row per task occurrence of a clip. This is where repeats/blinding/task order live.

  annotation_events.parquet: append-only raw history. Never collapse this as the only export.

  latest_annotations.parquet: convenience table with the latest annotation per presentation_id, annotator_id, and task_id.

  Important Rule
  Copy only clips referenced by the exported manifest. The render directory currently contains stale smoke clips from earlier runs, so directory-copy export would include unrelated
  assets.

  Another Project Summary
  This dataset contains short browser-playable well-crop clips from SauronX recordings. Each clip has stable source-frame provenance back to /shire/store, Valar run/well metadata, ROI
  coordinates, deterministic clip_id, sampling stratum, rendered H.264 preview, and append-only human annotation events. Labels are observational, taxonomy-versioned, and separated
  from clip identity through presentation records so repeated/blinded review and multiple annotators remain possible.
