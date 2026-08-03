# Wynton container build and disk budget

## Rebuild the container on Wynton

Wynton supports Apptainer builds on development nodes, including builds from a
local definition file. The build needs source files because
`mestimate_sidecar.def` copies C/CMake/test inputs into the image. The SGE run
path also needs the Python worker scripts. You do not need the full messy local
working tree; use the deploy bundle from
[repo_cleaning_audit.md](repo_cleaning_audit.md) when copying code to Wynton.

Create the bundle locally:

```bash
scripts/make_wynton_deploy_bundle.sh
```

Then extract it on Wynton and build the production image from that committed
source bundle. Store the SIF on `/wynton/scratch`, not in home.

```bash
ssh <user>@log2.wynton.ucsf.edu
qrsh -l mem_free=4G -l scratch=50G -l h_rt=04:00:00

mkdir -p /wynton/scratch/$USER/encoder_based_ethology/source
cd /wynton/scratch/$USER/encoder_based_ethology/source
tar -xzf /path/to/encoder_based_ethology_<commit>.tar.gz
cd encoder_based_ethology_<commit>

git status --short
git rev-parse HEAD

REPO_DIR=$PWD \
IMAGE_DIR=/wynton/scratch/$USER/encoder_based_ethology/containers \
APPTAINER_CACHE_ROOT=/wynton/scratch/$USER/encoder_based_ethology/apptainer-cache \
scripts/build_wynton_container.sh
```

The helper writes:

```text
/wynton/scratch/$USER/encoder_based_ethology/containers/mestimate_sidecar_<commit>.sif
/wynton/scratch/$USER/encoder_based_ethology/containers/mestimate_sidecar_<commit>.build-manifest.json
```

It records the repository commit, dirty flag, definition-file SHA256, SIF SHA256,
Apptainer version, FFmpeg version, and `mestimate-sidecar --version`. By default
it refuses to build from a dirty checkout. Set `ALLOW_DIRTY=1` only for local
debug images that will not be used as production provenance.

Smoke-test the resulting image in the same path layout used by SGE:

```bash
IMAGE=/wynton/scratch/$USER/encoder_based_ethology/containers/mestimate_sidecar_<commit>.sif

apptainer exec \
  --cleanenv \
  --bind "$PWD:$PWD" \
  --bind "$TMPDIR:$TMPDIR" \
  --bind /wynton/scratch:/wynton/scratch \
  "$IMAGE" \
  ffmpeg -hide_banner -version

apptainer run "$IMAGE" --version
```

The SGE wrapper already binds the repository, staged input root, and job-local
`$TMPDIR`. If outputs or inputs live outside those paths, pass the extra mount
with `--apptainer-extra-bind` through `scripts/manage_archival_sge_queue.py`.

## Disk pools

There are three distinct disk pools to budget:

- **Local compute-node `$TMPDIR`**: active input copy, partial AV1 outputs,
  validation, and optional sidecar work for the currently running plate.
- **Shared staged input root**: bounded HEVC staging area under
  `/wynton/scratch`.
- **Shared cluster output root**: validated well AV1 outputs waiting for
  collection to final storage.

Wynton local `/scratch` is the right place for active intermediate files. Wynton
documents `$TMPDIR` as a job-specific local `/scratch` directory that is removed
when the job terminates, and `-l scratch=200G` requests a node with 200 GiB of
available local scratch for the job.

The worker now removes each plate's `$TMPDIR/input/<source_id>` and
`$TMPDIR/output/<source_id>` after a successful rsync to shared output. This
keeps `--chunk-size > 1` from accumulating many plate videos inside one SGE
task. Use `--keep-local-work` only for debugging failed cleanup or inspecting
local intermediate files.

## Per-running-task local scratch

For one active plate task:

```text
local_peak ~= source_hevc_size
           + sum(96 well AV1 partial/final files)
           + sidecar scratch if --run-sidecar is enabled
           + small logs/manifests
```

Because the current FFmpeg command writes 96 outputs simultaneously, the partial
AV1 set is effectively the output set. After FFmpeg completes, partials are
atomically renamed and then rsynced.

Practical planning numbers before a benchmark:

- **No sidecar**: reserve 3x the source HEVC size per active task, with an
  absolute floor of 50 GiB.
- **With archive-domain sidecar summaries**: reserve 4x the source HEVC size,
  because each well is decoded again for sidecar extraction.
- **Current SGE default**: `scratch=200G` per one-slot task is conservative for
  ordinary 20 minute 96-well HEVC videos and leaves room for unexpectedly large
  sources or output expansion.

Once the first pilot batch completes, replace the multiplier with measured
`source bytes`, `sum output bytes`, and `max TMPDIR usage` from representative
quiet, ordinary, and high-motion plates.

## Shared `/wynton/scratch` budget

If Wynton cannot mount the video store, push source videos from a machine that
can read the store into `/wynton/scratch/$USER/encoder_based_ethology/staged_hevc`
with `manage_archival_sge_queue.py stage-push`. Running SGE jobs then read only
from the staged root and do not depend on a live workstation tunnel or mount.

Let:

```text
S = --max-staged source videos
C = --max-concurrent running SGE tasks
I = average staged HEVC size
O = average per-plate output size after 96 well AV1 files
T = collection interval in hours
R = completed plates per hour, approximately C if each plate takes one hour
```

The approximate shared scratch footprint is:

```text
shared_peak ~= S * I + (R * T) * O + manifests + SIF/cache
```

With an hourly collection loop and the one-hour-per-video upper estimate,
`R ~= C`, so:

```text
shared_peak ~= S * I + C * O + about 2-5 GiB for manifests/container metadata
```

Example steady-state budgets:

| Scenario | `S` | `C` | `I` | `O` | Shared peak |
| --- | ---: | ---: | ---: | ---: | ---: |
| Low | 20 | 5 | 2 GiB | 1 GiB | ~47 GiB |
| Middle | 30 | 10 | 4 GiB | 3 GiB | ~155 GiB |
| Conservative | 50 | 20 | 8 GiB | 6 GiB | ~525 GiB |

If collection is not run until the full 6,481-plate manifest finishes, output
storage dominates:

```text
full_waiting_outputs ~= 6481 * O
```

At `O = 3 GiB`, that is about 19 TiB. At `O = 6 GiB`, it is about 38 TiB. The
orchestrator should therefore collect validated outputs continuously or in
small waves, not only at the end.

## Runtime implication

Using one hour per source video as a conservative planning estimate:

```text
wall_hours ~= 6481 / C
```

| `--max-concurrent` | Approximate wall time |
| ---: | ---: |
| 5 | 54 days |
| 10 | 27 days |
| 20 | 13.5 days |
| 50 | 5.4 days |
| 100 | 2.7 days |

These are lower-bound scheduling estimates after inputs are staged and jobs
start promptly. Fair-share scheduling, staging/collection delays, and retry work
will add overhead.

## Recommended first Wynton pilot

Start with a small wave:

```text
--max-staged 20
--max-concurrent 5
--chunk-size 5
-l scratch=200G
```

Then measure:

- source HEVC bytes;
- sum of 96 well AV1 bytes;
- maximum local `$TMPDIR` usage during FFmpeg;
- wall time per plate;
- rsync time to shared output;
- collection time to final storage.

Benchmark validation separately. `full-decode` decodes all 96 outputs twice and
can materially inflate plate wall time. When source HEVCs have a checksum-verified
cloud backup, the production default is `packet-count-sentinel`: structural and
packet-count checks for every well, plus full decode of five deterministic wells
and rolling full-decode/source-versus-archive sentinel plates.

Those measurements should set the production `scratch`, `max-staged`, and
`max-concurrent` values.
