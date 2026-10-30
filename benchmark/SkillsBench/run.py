from __future__ import annotations
import json, os, sys, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from common import arguments, select, output, write_json, summarize

import shlex, uuid
from concurrent.futures import ThreadPoolExecutor
import yaml


def docker(*args, **kw):
    return subprocess.run(["docker", *map(str, args)], check=True, text=True, **kw)


def build(task):
    image = (
        os.getenv("SKILLSBENCH_IMAGE_PREFIX", "toolspace-skillsbench") + ":" + task.name
    )
    found = subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if found.returncode:
        docker("build", "-t", image, task / "environment")
    return image


def one(task, root, trial):
    out = root / f"trial-{trial}" / task.name
    out.mkdir(parents=True, exist_ok=True)
    result_path = out / "result.json"
    if result_path.exists():
        previous = json.loads(result_path.read_text())
        if previous.get("reward") is not None:
            return previous
    name = "ts-sb-" + uuid.uuid4().hex[:12]
    network = name + "-net"
    aux = []
    try:
        image = build(task)
        conf = yaml.safe_load(task.joinpath("task.md").read_text().split("---", 2)[1])
        budget = float(conf.get("agent", {}).get("timeout_sec", 900))
        verify_budget = float(conf.get("verifier", {}).get("timeout_sec", 900))
        docker("network", "create", network, stdout=subprocess.DEVNULL)
        if task.name == "fix-visual-stability":
            api_image = "toolspace-skillsbench:fix-visual-stability-api"
            docker(
                "build",
                "-f",
                task / "environment/Dockerfile.api",
                "-t",
                api_image,
                task / "environment",
            )
            api = name + "-api"
            aux.append(api)
            docker(
                "run",
                "-d",
                "--name",
                api,
                "--network",
                network,
                "--network-alias",
                "api",
                api_image,
                stdout=subprocess.DEVNULL,
            )
        proxy_args = []
        for k in [
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        ]:
            if os.getenv(k):
                proxy_args += ["-e", k]
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--privileged",
            *proxy_args,
            image,
            "sleep",
            "infinity",
            stdout=subprocess.DEVNULL,
        )
        wd = (
            docker(
                "image",
                "inspect",
                image,
                "--format",
                "{{.Config.WorkingDir}}",
                capture_output=True,
            ).stdout.strip()
            or "/root"
        )
        # Keep the official image filesystem, including non-/root workdirs.
        shell = out / "shell.sh"
        shell.write_text(
            "#!/usr/bin/env bash\nexec docker exec -i -w "
            + shlex.quote(wd)
            + " "
            + shlex.quote(name)
            + ' /bin/bash "$@"\n'
        )
        shell.chmod(0o755)
        raw = task.joinpath("task.md").read_text().split("---", 2)[2].strip()
        payload = {
            "task_id": task.name,
            "model_config": {
                "id": os.environ["MODEL"],
                "base_url": os.environ["MODEL_BASE_URL"],
                "extra_body": json.loads(os.getenv("MODEL_EXTRA_BODY", "{}")),
            },
            "shell_path": str(shell),
            "workspace": str(out),
            "prompt": f"You are working in the SkillsBench task container. The working directory is {wd}. Use relevant skills under /skills.\n\n"
            + raw,
            "max_iterations": 500,
            "paths": {
                "state_path": str(out / "state.json"),
                "result_path": str(out / "agent-result.json"),
            },
        }
        write_json(out / "config.json", payload)
        agent_env = os.environ.copy()
        agent_env["OPENAI_API_KEY"] = os.environ["MODEL_API_KEY"]
        with (out / "agent.log").open("w") as log:
            try:
                subprocess.run(
                    [sys.executable, str(HERE / "agent.py"), str(out / "config.json")],
                    env=agent_env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=budget,
                    check=True,
                )
            except subprocess.TimeoutExpired:
                # The official verifier evaluates the filesystem at the task budget.
                pass
        # The agent never sees the verifier or the answer files while generating.
        docker("exec", name, "mkdir", "-p", "/logs/verifier")
        docker("cp", task / "verifier", name + ":/verifier")
        with (out / "verifier.log").open("w") as log:
            subprocess.run(
                ["docker", "exec", name, "bash", "/verifier/test.sh"],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=verify_budget,
                check=False,
            )
        reward = docker(
            "exec", name, "cat", "/logs/verifier/reward.txt", capture_output=True
        ).stdout.strip()
        value = float(reward)
        if not 0 <= value <= 1:
            raise ValueError("Invalid verifier reward")
        text = (out / "verifier.log").read_text().lower()
        if value == 0 and any(
            s in text
            for s in [
                "could not resolve host",
                "temporary failure in name resolution",
                "no matching distribution found",
                "uv: command not found",
                "uvx: command not found",
                "no module named pytest",
                "failed to fetch",
            ]
        ):
            raise RuntimeError("Verifier dependency installation failed")
        row = {"task_id": task.name, "trial": trial, "reward": value}
    except Exception as exc:
        row = {
            "task_id": task.name,
            "trial": trial,
            "reward": None,
            "error": type(exc).__name__ + ": " + str(exc)[-500:],
        }
    finally:
        for c in [name, *aux]:
            subprocess.run(
                ["docker", "rm", "-f", c],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        subprocess.run(
            ["docker", "network", "rm", network],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    write_json(result_path, row)
    return row


def main():
    args = arguments("SkillsBench self-contained text-only 79 tasks, OpenHands SDK")
    task_root = Path(os.environ["RUNTIME"]) / "SkillsBench/tasks"
    ids = (HERE / "task_ids.txt").read_text().splitlines()
    assert len(ids) == 79
    tasks = select(ids, args)
    if args.check_only:
        for t in tasks:
            for file in ["task.md", "environment/Dockerfile", "verifier/test.sh"]:
                if not (task_root / t / file).is_file():
                    raise FileNotFoundError(task_root / t / file)
        print(f"SkillsBench: {len(tasks)}/79 task definitions valid")
        return 0
    root = output("SkillsBench", args)
    trials = int(os.getenv("TRIALS", "1" if os.getenv("SCOPE") == "smoke" else "3"))
    if trials < 1:
        raise ValueError("TRIALS must be positive")
    write_json(
        root / "config.json",
        {
            "model": os.environ["MODEL"],
            "trials": trials,
            "task_ids": tasks,
            "harness": "OpenHands SDK 1.23.0",
        },
    )
    jobs = [(task_root / t, root, i) for i in range(1, trials + 1) for t in tasks]
    with ThreadPoolExecutor(max_workers=int(os.getenv("CONCURRENCY", "4"))) as pool:
        rows = list(pool.map(lambda x: one(*x), jobs))
    return summarize(root, rows, len(tasks) * trials, 79 * 3, "mean_verifier_reward")


if __name__ == "__main__":
    raise SystemExit(main())
