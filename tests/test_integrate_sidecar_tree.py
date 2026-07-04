import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from integrate_sidecar_tree import (  # noqa: E402
    discover_sidecars,
    output_complete,
    relative_output_dir,
    sidecar_stem_from_frames,
)


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


def test_sidecar_stem_from_frames_removes_exact_suffix():
    stem = sidecar_stem_from_frames("A01.mestimate-v1.frames.csv.gz")
    assert stem == "A01"


def test_discover_sidecars_pairs_matching_files_and_reports_missing_vectors(tmp_path):
    good = tmp_path / "run" / "A01"
    touch(good / "A01.mestimate-v1.frames.csv.gz")
    touch(good / "A01.mestimate-v1.vectors.csv.gz")
    touch(good / "A01.mestimate-v1.metadata.json")

    good_binary = tmp_path / "run" / "A03"
    touch(good_binary / "A03.mestimate-v1.frames.csv.gz")
    touch(good_binary / "A03.mestimate-v1.vectors.bin.gz")

    bad = tmp_path / "run" / "A02"
    touch(bad / "A02.mestimate-v1.frames.csv.gz")

    sidecars, missing = discover_sidecars(tmp_path)

    assert len(sidecars) == 2
    assert sidecars[0].stem == "A01"
    assert sidecars[0].metadata == good / "A01.mestimate-v1.metadata.json"
    assert sidecars[1].stem == "A03"
    assert sidecars[1].vectors == good_binary / "A03.mestimate-v1.vectors.bin.gz"
    assert len(missing) == 1
    assert missing[0]["stem"] == "A02"
    assert missing[0]["missing"] == "vectors"


def test_relative_output_dir_preserves_layout_under_input_root(tmp_path):
    input_root = tmp_path / "input"
    sidecar_dir = input_root / "run1" / "sidecars" / "A01"
    output_root = tmp_path / "derived"

    observed = relative_output_dir(input_root, output_root, sidecar_dir)

    assert observed == output_root / "run1" / "sidecars" / "A01"


def test_output_complete_requires_frame_metadata_and_all_bins(tmp_path):
    stem = "A01"
    touch(tmp_path / f"{stem}.mv-features-v1.frames.csv.gz")
    touch(tmp_path / f"{stem}.mv-features-v1.metadata.json")
    touch(tmp_path / f"{stem}.mv-features-v1.bin-50ms.csv.gz")

    assert output_complete(tmp_path, stem, [50])
    assert not output_complete(tmp_path, stem, [50, 100])
