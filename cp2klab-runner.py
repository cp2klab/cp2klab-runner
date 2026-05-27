#!/usr/bin/env python3

# Copyright 2026 Ole Schütt. All Rights Reserved.

import os
import sys
import json
import gzip
import time
import atexit
import signal
import shutil
import logging
import argparse
import traceback
from subprocess import run, PIPE
import configparser
from pathlib import Path
from http.client import HTTPResponse
from urllib.request import Request, urlopen
from typing import Dict, List, Optional, Tuple, Union, cast

if sys.version_info >= (3, 8):
    from typing import Literal, TypedDict
else:
    Literal = Union
    TypedDict = Dict

logger = logging.getLogger(__name__)

USER_AGENT = "CP2K Lab runner 0.3"


# ======================================================================================
class HelloResponse(TypedDict):
    response: Literal["hello"]
    profiles: List[str]


# ======================================================================================
class SubmitRequest(TypedDict):
    request: Literal["submit"]
    downloads: List[str]
    jobid: int
    workdir: str
    in_file: str
    out_file: str
    profile: str
    wallsecs: int
    num_nodes: int


# ======================================================================================
class SubmitResponse(TypedDict):
    response: Literal["submit"]
    jobid: int
    error: str
    external_id: str


# ======================================================================================
class CancelRequest(TypedDict):
    request: Literal["cancel"]
    external_id: str


# ======================================================================================
class ReportRequest(TypedDict):
    request: Literal["report"]
    external_ids: List[str]


# ======================================================================================
JobState = Literal["UNKNOWN", "QUEUING", "RUNNING", "SUCCEEDED", "CANCELED", "FAILED"]


# ======================================================================================
class JobReport(TypedDict):
    external_id: str
    state: JobState
    start_time: int


# ======================================================================================
class ReportResponse(TypedDict):
    response: Literal["report"]
    jobs: List[JobReport]


# ======================================================================================
class HeartbeatRequest(TypedDict):
    request: Literal["heartbeat"]


# ======================================================================================
RequestMessage = Union[SubmitRequest, ReportRequest, CancelRequest, HeartbeatRequest]
ResponseMessage = Union[SubmitResponse, ReportResponse, HelloResponse]


# ======================================================================================
class Profile:
    def __init__(self, section: configparser.SectionProxy):
        cp2k_template = section.get("cp2k_template")
        assert cp2k_template
        self.cp2k_template = Path(cp2k_template).read_text()
        self.custom_args = {k: v or "" for k, v in section.items()}

    def render_cp2k_slurm_file(self, request: SubmitRequest) -> str:
        wallsecs = request["wallsecs"]
        return self.cp2k_template.format(
            in_file=request["in_file"],
            out_file=request["out_file"],
            walltime=f"{wallsecs//3600}:{wallsecs%3600//60:02d}:{wallsecs%60:02d}",
            num_nodes=request["num_nodes"],
            **self.custom_args,
        )


# ======================================================================================
class RunnerConfig:  # dataclass not available before Python 3.7
    def __init__(
        self,
        pid_file: Path,
        log_file: Path,
        api_token: str,
        basedir: Path,
        slurm_filename: str,
        profiles: Dict[str, Profile],
    ):
        assert len(profiles) > 0
        self.pid_file = pid_file
        self.log_file = log_file
        self.api_token = api_token
        self.basedir = basedir
        self.slurm_filename = slurm_filename
        self.profiles = profiles

    def get_profile(self, name: Optional[str]) -> Profile:
        return self.profiles[name] if name else list(self.profiles.values())[0]


# ======================================================================================
def main() -> None:
    # Parse command line arguments.
    parser = argparse.ArgumentParser(description="CP2K Lab Runner")
    parser.add_argument(
        "-c", "--config", type=Path, default=Path.cwd() / "cp2klab-runner.conf"
    )
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("command", help="start, stop, or restart")
    args = parser.parse_args()

    # Parse config file.
    if not args.config.exists():
        print(f"Error: Config file {args.config} does not exists.")
        sys.exit(1)
    cfgfile = configparser.ConfigParser()
    cfgfile.read(args.config)  # fails silently when file doesn't exists
    profiles = {k[9:]: Profile(v) for k, v in cfgfile.items() if k[:9] == "profiles."}
    cfg = RunnerConfig(
        pid_file=args.config.with_suffix(".pid"),
        log_file=args.config.with_suffix(".log"),
        api_token=cfgfile.get("main", "api_token"),
        basedir=Path(cfgfile.get("main", "base_dir")),
        slurm_filename=cfgfile.get("main", "slurm_filename", fallback="slurm-job.sh"),
        profiles=profiles,
    )

    # Configure logging.
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(filename=cfg.log_file, level=log_level)

    # Run command.
    if args.command == "start":
        start(cfg)
    elif args.command == "stop":
        stop(cfg)
    elif args.command == "restart":
        stop(cfg)
        start(cfg)
    else:
        print(f"Error: Command unknown. Valid commands are start, stop, and restart.")
        sys.exit(1)


# ======================================================================================
def start(cfg: RunnerConfig) -> None:
    if cfg.pid_file.exists():
        print("CP2K Lab Runner is already active.")
        sys.exit(1)

    # Daemonize
    sys.stdout.flush()
    sys.stderr.flush()
    if os.fork() > 0:
        print("CP2K Lab Runner started.")
        sys.exit(0)
    os.setsid()
    cfg.pid_file.write_text(str(os.getpid()))
    atexit.register(lambda: cfg.pid_file.unlink())

    # Close standard file descriptors.
    devnull = open(os.devnull, "a+")
    os.dup2(devnull.fileno(), sys.stdin.fileno())
    os.dup2(devnull.fileno(), sys.stdout.fileno())
    os.dup2(devnull.fileno(), sys.stderr.fileno())

    # Say hello.
    send_message(cfg, HelloResponse(response="hello", profiles=list(cfg.profiles)))
    logger.info("Runner started")

    # Main loop
    while True:
        try:
            msg = get_message(cfg)
            if not msg:
                wait_for_message(cfg)
            elif msg["request"] == "submit":
                handle_submit(cfg, msg)
            elif msg["request"] == "cancel":
                handle_cancel(msg)
            elif msg["request"] == "report":
                handle_report(cfg, msg)
            elif msg["request"] == "heartbeat":
                pass  # nothing to do
            else:
                logger.error(f"Unprocessable message: {msg}")
        except Exception as e:
            logger.exception(e)
            logger.info("Sleeping for 10 seconds.")
            time.sleep(10)


# ======================================================================================
def stop(cfg: RunnerConfig) -> None:
    if not cfg.pid_file.exists():
        print("CP2K Lab Runner was not active.")
    else:
        pid = int(cfg.pid_file.read_text())
        try:
            while True:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.1)
        except OSError as err:
            if "No such process" in str(err.args):
                print("CP2K Lab Runner stopped.")
                if cfg.pid_file.exists():
                    cfg.pid_file.unlink()
            else:
                raise err


# ======================================================================================
def handle_submit(cfg: RunnerConfig, request: SubmitRequest) -> None:

    # Resolve and clear workdir.
    local_workdir = (cfg.basedir / request["workdir"]).resolve()
    assert is_relative_to(local_workdir, cfg.basedir)
    if local_workdir.exists():
        shutil.rmtree(local_workdir)

    # Download files.
    for remote_path in request["downloads"]:
        download_file(cfg, remote_path)

    # Write and upload slurm job file.
    external_id, error = "", ""
    try:
        profile = cfg.get_profile(request["profile"])
        slurm_file_content = profile.render_cp2k_slurm_file(request)
        local_slurm_file = local_workdir / cfg.slurm_filename
        local_slurm_file.write_text(slurm_file_content)
        upload_file(cfg, local_slurm_file)
    except Exception as e:
        logger.exception(e)

    if not error:
        # Invoke sbatch.
        cmd = ["sbatch", "--parsable", str(local_slurm_file)]
        p = run(cmd, cwd=local_workdir, check=False, stdout=PIPE, stderr=PIPE)
        if p.returncode == 0:
            external_id = p.stdout.decode("utf8").strip()
            logger.info(f"Submitted {external_id}")
        else:
            error = p.stderr.decode("utf8")
            logger.error(f"Command failed: {cmd}")

    # Report back.
    response = SubmitResponse(
        response="submit", jobid=request["jobid"], external_id=external_id, error=error
    )
    send_message(cfg, response)


# ======================================================================================
def handle_cancel(request: CancelRequest) -> None:
    cmd = ["scancel", request["external_id"]]
    p = run(cmd, check=False)
    if p.returncode != 0:
        logger.error(f"Command failed: {cmd}")
    logger.info(f"Canceled {request['external_id']}")


# ======================================================================================
def handle_report(cfg: RunnerConfig, request: ReportRequest) -> None:
    reports: List[JobReport] = []

    for external_id in request["external_ids"]:
        state, local_workdir, start_time = get_job_state(external_id)
        if local_workdir:
            upload_workdir(cfg, local_workdir)
        reports.append(
            JobReport(external_id=external_id, state=state, start_time=start_time)
        )

    send_message(cfg, ReportResponse(response="report", jobs=reports))


# ======================================================================================
def get_job_state(external_id: str) -> Tuple[JobState, Optional[Path], int]:
    # Try squeue first because it's cheaper and has start_time.
    cmd = ["squeue", "--json", "-j", external_id]
    p = run(cmd, stdout=PIPE, check=False)
    if p.returncode != 0:
        logger.error(f"Command failed: {cmd}")
        return "UNKNOWN", None, 0

    output = json.loads(p.stdout)
    if output["jobs"]:
        slurm_job = output["jobs"][0]
        assert str(slurm_job["job_id"]) == external_id
        state = parse_slurm_job_state(slurm_job["job_state"][0])
        local_workdir = Path(slurm_job["current_working_directory"])
        start_time = int(slurm_job["start_time"]["number"])
        return state, local_workdir, start_time

    # Job not found by squeue, let's try sacct next.
    cmd = ["sacct", "--json", "-j", external_id]
    p = run(cmd, stdout=PIPE, check=False)
    if p.returncode != 0:
        logger.error(f"Command failed: {cmd}")
        return "UNKNOWN", None, 0

    output = json.loads(p.stdout)
    if output["jobs"]:
        slurm_job = output["jobs"][0]
        assert str(slurm_job["job_id"]) == external_id
        state = parse_slurm_job_state(slurm_job["state"]["current"][0])
        local_workdir = Path(slurm_job["working_directory"])
        return state, local_workdir, 0

    logger.error(f"Slurm could not find job {external_id}")
    return "UNKNOWN", None, 0


# ======================================================================================
def parse_slurm_job_state(slurm_state: str) -> JobState:
    if slurm_state == "PENDING":
        return "QUEUING"
    elif slurm_state == "RUNNING":
        return "RUNNING"
    elif slurm_state == "COMPLETED":
        return "SUCCEEDED"
    elif slurm_state == "FAILED":
        return "FAILED"
    elif slurm_state == "CANCELLED":
        return "CANCELED"  # american english
    else:
        logger.error(f"Got unexpected slurm job state: {slurm_state}")
        return "UNKNOWN"


# ======================================================================================
def download_file(cfg: RunnerConfig, remote_path: str) -> None:
    r = http_request(cfg, "GET", f"files/{remote_path}")
    assert r.status == 200
    local_path = (cfg.basedir / remote_path).resolve()
    assert is_relative_to(local_path, cfg.basedir)
    local_path.parent.mkdir(exist_ok=True, parents=True)
    local_path.write_bytes(r.read())
    logger.info(f"Downloaded {local_path}")


# ======================================================================================
def upload_workdir(cfg: RunnerConfig, local_workdir: Path) -> None:
    # Use mtime of slurm job file to track uploads.
    local_slurm_file = local_workdir / cfg.slurm_filename
    last_upload_time = local_slurm_file.stat().st_mtime
    local_slurm_file.touch()

    # Find files in workdir that are younger than last_upload_time.
    for local_path in local_workdir.iterdir():
        if local_path.is_file() and local_path != local_slurm_file:
            if local_path.stat().st_mtime > last_upload_time:
                upload_file(cfg, local_path)


# ======================================================================================
def upload_file(cfg: RunnerConfig, local_path: Path) -> None:
    assert is_relative_to(local_path, cfg.basedir)
    remote_path = local_path.relative_to(cfg.basedir)
    content = local_path.read_bytes()
    r = http_request(cfg, "POST", f"files/{remote_path}", data=content)
    assert r.status == 204
    logger.info(f"Uploaded {local_path}")


# ======================================================================================
def send_message(cfg: RunnerConfig, message: ResponseMessage) -> None:
    logger.debug(f"Sending message: {message}")
    r = http_request(cfg, "POST", "message", json_data=message)
    assert r.status == 204


# ======================================================================================
def get_message(cfg: RunnerConfig) -> Optional[RequestMessage]:
    r = http_request(cfg, "GET", "message")
    if r.status == 204:
        return None
    assert r.status == 200
    message = cast(RequestMessage, json.load(r))
    logger.debug(f"Received message: {message}")
    return message


# ======================================================================================
def wait_for_message(cfg: RunnerConfig) -> None:
    r = http_request(cfg, "GET", "wait")
    assert r.status == 204


# ======================================================================================
def http_request(
    cfg: RunnerConfig,
    method: str,
    url_path: str,
    data: Optional[bytes] = None,
    json_data: Optional[ResponseMessage] = None,
) -> HTTPResponse:

    url = f"https://lab.cp2k.com/api/external/{url_path}"
    headers = {"User-Agent": USER_AGENT, "Cookie": f"token={cfg.api_token};"}

    if json_data:
        assert data is None
        data = json.dumps(json_data).encode("utf8")
        headers["Content-Type"] = "application/json"

    if data:
        data = gzip.compress(data)
        headers["Content-Encoding"] = "gzip"
        headers["Content-Length"] = str(len(data))

    response = urlopen(Request(url, headers=headers, data=data, method=method))
    return cast(HTTPResponse, response)


# ======================================================================================
def is_relative_to(p: Path, u: Path) -> bool:  # not in pathlib before Python 3.9
    return u == p or u in p.parents


# ======================================================================================
main()

# EOF
