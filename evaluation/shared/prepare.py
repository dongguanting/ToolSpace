from __future__ import annotations
import os, shutil, subprocess, tarfile, urllib.request
from pathlib import Path


def extract(archive, dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        for m in tf.getmembers():
            path = (dest / m.name).resolve()
            if not path.is_relative_to(dest.resolve()):
                raise ValueError("Unsafe archive entry: " + m.name)
        tf.extractall(dest, filter="data")


def github(repo, commit, dest):
    dest = Path(dest)
    marker = dest / ".source-commit"
    if marker.exists() and marker.read_text().strip() == commit:
        return
    if dest.exists() and any(dest.iterdir()):
        raise RuntimeError(f"Nonempty source directory: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest.parent / (dest.name + ".tar.gz")
    urllib.request.urlretrieve(
        f"https://codeload.github.com/{repo}/tar.gz/{commit}", archive
    )
    stage = dest.parent / (dest.name + "-extract")
    extract(archive, stage)
    top = next(stage.iterdir())
    for path in top.iterdir():
        shutil.move(str(path), dest / path.name)
    shutil.rmtree(stage)
    archive.unlink()
    marker.write_text(commit + "\n")


def pip(*args):
    import sys

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"UV_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL"}
    }
    subprocess.run(
        [
            "uv",
            "--no-config",
            "pip",
            "install",
            "--default-index",
            "https://pypi.org/simple",
            "--python",
            sys.executable,
            *map(str, args),
        ],
        check=True,
        env=env,
    )


def install_node_tools():
    node = shutil.which("node")
    if not node or not shutil.which("npm"):
        raise SystemExit("Install Node.js 18+ and npm first")
    major = int(
        subprocess.check_output([node, "--version"], text=True)
        .strip()
        .lstrip("v")
        .split(".")[0]
    )
    if major < 18:
        raise SystemExit("Node.js 18+ is required")
    source = Path(__file__).resolve().parent / "node"
    destination = Path(os.environ["RUNTIME"]) / "tools"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy2(source / name, destination / name)
    subprocess.run(
        [
            "npm",
            "ci",
            "--prefix",
            str(destination),
            "--registry",
            "https://registry.npmjs.org",
        ],
        check=True,
    )
