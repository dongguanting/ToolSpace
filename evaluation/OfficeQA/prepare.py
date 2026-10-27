from pathlib import Path
import os, sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))

RUNTIME = Path(os.environ["RUNTIME"])
if os.environ.get("SKIP_DATA") != "1":
    from huggingface_hub import snapshot_download
    import zipfile

    dest = RUNTIME / "OfficeQA/data"
    corpus = dest / "corpus/jsons"
    if not corpus.is_dir() or len(list(corpus.glob("*.json"))) != 697:
        try:
            snapshot_download(
                repo_id="databricks/officeqa",
                repo_type="dataset",
                revision="763a8366abf2a3605c381d53586d844dc60fa756",
                allow_patterns=["treasury_bulletins_parsed/jsons/*.zip"],
                local_dir=dest / "download",
            )
        except Exception as exc:
            raise SystemExit(
                "OfficeQA corpus download failed. Accept access at https://huggingface.co/datasets/databricks/officeqa and set HF_TOKEN. "
                + type(exc).__name__
            )
        corpus.mkdir(parents=True, exist_ok=True)
        for archive in (dest / "download").rglob("*.zip"):
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    if info.filename.endswith(".json"):
                        target = corpus / Path(info.filename).name
                        if target.exists() and target.read_bytes() != z.read(info):
                            raise ValueError("Conflicting corpus filename")
                        target.write_bytes(z.read(info))
    if len(list(corpus.glob("*.json"))) != 697:
        raise SystemExit("Expected 697 OfficeQA corpus documents")
