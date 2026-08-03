# Cluster reproducibility checklist

This project should treat historical archival runs as claim-producing extraction
jobs, not as disposable transcoding jobs.

## Scheduling unit

Schedule one array task per source plate video. Do not schedule 96 independent
jobs that each decode the same plate HEVC to produce one well. The intended
pattern is:

```text
one source HEVC decode
  -> 96 deterministic ROI crops
  -> 96 well AV1 archives
  -> per-well image-dynamics summaries
  -> optional per-well MV summaries or sampled vectors
  -> one plate-level QC/common-mode summary
```

The per-well manifest is an output inventory. The plate manifest is the cluster
job list.

Independent well AV1 files are the current working durable-video candidate. A
10-second same-codec measurement found a 1.74x storage cost relative to one
whole-plate AV1. This premium is being accepted for further testing because the
well files are expected to simplify downstream storage and analysis. Preserve a
checksum-verified recoverable HEVC backup while this decision remains under
validation.

For the current SGE command flow, see
[sge_archival_orchestration.md](sge_archival_orchestration.md). For Wynton
container rebuilds and disk-budget planning, see
[wynton_container_and_disk_budget.md](wynton_container_and_disk_budget.md).

## Minimum provenance

Every plate job should record:

- source path and source fingerprint / SHA256;
- independent ROI-record table path and SHA256;
- ROI-solution table path and SHA256 when the ROI records were generated from a
  separate solution export;
- ROI repository commit and dirty flag;
- Apptainer image path and SHA256 when the image is a SIF file;
- exact FFmpeg version and encoder settings;
- crop coordinates, padding, well label, and coordinate domain;
- frame count, fps, and timebase for the parent and every well output;
- sidecar/image-dynamics parameter set;
- job ID, array task ID, hostname, start/end time, return code.

If `ROI_improvement` is dirty, either export and hash the exact ROI table used
or fail the production run. A dirty checkout alone is not reproducible.

## Container guidance

A SIF image is preferable for cluster production because it can be hashed and
stored read-only. A sandbox is acceptable for local development and smoke tests,
but it is weaker provenance unless the directory tree is separately archived or
hashed.

The current SGE wrapper binds the repository into the container and executes the
Python worker from that checkout. The image therefore needs FFmpeg, ffprobe,
Python, and any compiled sidecar/runtime dependencies; the exact orchestration
code is pinned by the repository commit recorded with the manifests. A future
fully sealed production image can also bake the Python scripts into the SIF, but
then the image build manifest must record that repository commit explicitly.

Pin the base image by digest before production-scale historical runs. Record the
SIF SHA256 in the plate and well manifests.

## Motion channel defaults

The minimum archival motion product should be lagged image dynamics on each
well crop. `mestimate` summaries are useful but should remain optional until
benchmarks on representative plate videos show acceptable throughput and
storage. Full or sampled vector rows should be reserved for validation/audit
subsets unless a storage budget explicitly approves wider retention.
