import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from make_sidecar_slurm_manifest import make_rows  # noqa: E402
from run_sidecar_manifest_task import build_command, load_task, sha256_file  # noqa: E402


def test_make_rows_preserves_relative_layout(tmp_path):
    input_root = tmp_path / "videos"
    video = input_root / "plateA" / "A01.mkv"
    video.parent.mkdir(parents=True)
    video.write_text("not real video", encoding="utf-8")
    output_root = tmp_path / "sidecars"
    args = SimpleNamespace(
        preserve_layout=True,
        method="epzs",
        mb_size=16,
        search_param=12,
        frame_diff_threshold=10,
        frame_output="bin",
        summary_float_precision=6,
        vector_output="none",
        vector_format="bin",
        vector_source="past",
        vector_frame_stride=5,
        vector_spatial_stride=2,
        vector_min_magnitude=0.25,
        force=True,
    )

    rows = make_rows([video], input_root, output_root, args)

    assert rows == [
        {
            "task_id": 1,
            "input_path": str(video.resolve()),
            "output_dir": str((output_root / "plateA" / "A01").resolve()),
            "method": "epzs",
            "mb_size": 16,
            "search_param": 12,
            "frame_diff_threshold": 10,
            "frame_output": "bin",
            "summary_float_precision": 6,
            "vector_output": "none",
            "vector_format": "bin",
            "vector_source": "past",
            "vector_frame_stride": 5,
            "vector_spatial_stride": 2,
            "vector_min_magnitude": 0.25,
            "force": 1,
        }
    ]


def test_load_task_uses_one_based_slurm_array_index(tmp_path):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "task_id,input_path,output_dir,method,mb_size,search_param,frame_diff_threshold,frame_output,summary_float_precision,vector_output,vector_format,vector_source,vector_frame_stride,vector_spatial_stride,vector_min_magnitude,force\n"
        "1,/v/a.mkv,/o/a,epzs,16,12,10,csv,6,all,csv,all,1,1,0.0,0\n"
        "2,/v/b.mkv,/o/b,epzs,16,12,12,bin,5,sampled,bin,past,5,2,0.25,1\n",
        encoding="utf-8",
    )

    row, count = load_task(manifest, 2)

    assert count == 2
    assert row["input_path"] == "/v/b.mkv"
    assert row["force"] == "1"


def test_build_command_binds_input_parent_and_output_dir(tmp_path):
    video_dir = tmp_path / "videos"
    video = video_dir / "A01.mkv"
    output_dir = tmp_path / "sidecars" / "A01"
    image = tmp_path / "mestimate_sidecar.sif"
    video_dir.mkdir(parents=True)
    video.write_text("not real video", encoding="utf-8")
    image.write_text("not real sif", encoding="utf-8")
    args = SimpleNamespace(
        image=str(image),
        apptainer_bin="apptainer",
        bind=[str(tmp_path / "extra")],
        cleanenv=True,
        method="epzs",
        mb_size=16,
        search_param=12,
        frame_diff_threshold=10,
        frame_output="bin",
        summary_float_precision=6,
        vector_output="all",
        vector_format="csv",
        vector_source="all",
        vector_frame_stride=1,
        vector_spatial_stride=1,
        vector_min_magnitude=0.0,
        force=False,
    )
    row = {
        "input_path": str(video),
        "output_dir": str(output_dir),
        "method": "esa",
        "mb_size": "8",
        "search_param": "4",
        "frame_diff_threshold": "12",
        "frame_output": "bin",
        "summary_float_precision": "5",
        "vector_output": "sampled",
        "vector_format": "bin",
        "vector_source": "past",
        "vector_frame_stride": "5",
        "vector_spatial_stride": "2",
        "vector_min_magnitude": "0.25",
        "force": "1",
    }

    cmd, binds = build_command(args, row)

    assert str(video_dir.resolve()) in binds
    assert str(output_dir.resolve()) in binds
    assert cmd[:3] == ["apptainer", "run", "--cleanenv"]
    assert str(image.resolve()) in cmd
    assert cmd[cmd.index("--input") + 1] == str(video.resolve())
    assert cmd[cmd.index("--output-dir") + 1] == str(output_dir.resolve())
    assert cmd[cmd.index("--method") + 1] == "esa"
    assert cmd[cmd.index("--frame-diff-threshold") + 1] == "12"
    assert cmd[cmd.index("--frame-output") + 1] == "bin"
    assert cmd[cmd.index("--summary-float-precision") + 1] == "5"
    assert cmd[cmd.index("--vector-output") + 1] == "sampled"
    assert cmd[cmd.index("--vector-format") + 1] == "bin"
    assert cmd[cmd.index("--vector-source") + 1] == "past"
    assert cmd[cmd.index("--vector-frame-stride") + 1] == "5"
    assert cmd[cmd.index("--vector-spatial-stride") + 1] == "2"
    assert cmd[cmd.index("--vector-min-magnitude") + 1] == "0.25"
    assert "--force" in cmd


def test_sha256_file_hashes_sif_like_file(tmp_path):
    image = tmp_path / "pipeline.sif"
    image.write_text("runtime", encoding="utf-8")

    digest = sha256_file(image)

    assert digest == "d92c6a81b2ff50096bcda80885427d1f59a25b5f483f7055523504925d16ab23"
