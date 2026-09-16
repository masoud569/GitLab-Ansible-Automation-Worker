```python
#!/usr/bin/env python3

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests


# ============================================================
# Configuration
# ============================================================

GITLAB_URL = os.environ["GITLAB_URL"].rstrip("/")
PROJECT_ID = os.environ["GITLAB_PROJECT_ID"]
GITLAB_TOKEN = os.environ["GITLAB_TOKEN"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

AUTOMATION_REQUEST = os.path.join(
    BASE_DIR,
    "automation_request.py"
)

PYTHON_BIN = os.path.join(
    BASE_DIR,
    ".venv",
    "bin",
    "python"
)

LOG_DIR = os.path.join(
    BASE_DIR,
    "logs"
)

STATE_DIR = os.path.join(
    BASE_DIR,
    "state"
)

LOG_FILE = os.path.join(
    LOG_DIR,
    "automation-worker.jsonl"
)

STATE_FILE = os.path.join(
    STATE_DIR,
    "automation_state.json"
)

RUNNING_FILE = os.path.join(
    STATE_DIR,
    "automation_running.json"
)

POLL_INTERVAL = int(
    os.environ.get(
        "AUTOMATION_POLL_INTERVAL",
        "60"
    )
)

LOOKBACK_SECONDS = int(
    os.environ.get(
        "AUTOMATION_LOOKBACK_SECONDS",
        str(24 * 60 * 60)
    )
)

REQUEST_TIMEOUT = int(
    os.environ.get(
        "AUTOMATION_REQUEST_TIMEOUT",
        "30"
    )
)

WORKER_TIMEOUT = int(
    os.environ.get(
        "AUTOMATION_WORKER_TIMEOUT",
        "3700"
    )
)

HEADERS = {
    "PRIVATE-TOKEN": GITLAB_TOKEN
}


# ============================================================
# Initialization
# ============================================================

def ensure_directories():
    """
    Create runtime directories if they do not already exist.
    """

    os.makedirs(
        LOG_DIR,
        exist_ok=True
    )

    os.makedirs(
        STATE_DIR,
        exist_ok=True
    )


# ============================================================
# Logging
# ============================================================

def write_log(level, event, **fields):
    """
    Write one structured JSONL audit event.

    Secrets and authentication headers must never be included
    in the fields passed to this function.
    """

    ensure_directories()

    entry = {
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),

        "level": level,

        "event": event,

        **fields
    }

    with open(
        LOG_FILE,
        "a",
        encoding="utf-8"
    ) as file:

        file.write(
            json.dumps(
                entry,
                ensure_ascii=False
            ) + "\n"
        )


# ============================================================
# Atomic JSON Helpers
# ============================================================

def atomic_write_json(path, data):
    """
    Write JSON atomically to reduce the risk of partial state files.
    """

    directory = os.path.dirname(path)

    os.makedirs(
        directory,
        exist_ok=True
    )

    temp_path = f"{path}.tmp"

    with open(
        temp_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

        file.write("\n")

        file.flush()

        os.fsync(
            file.fileno()
        )

    os.replace(
        temp_path,
        path
    )


# ============================================================
# Version State
# ============================================================

def load_state():
    """
    Load the latest consumed Automation Version for each Issue.

    State format:

        {
            "934": 7,
            "935": 3
        }

    The value represents the latest consumed Version.
    """

    if not os.path.exists(STATE_FILE):
        return {}

    with open(
        STATE_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        state = json.load(file)

    if not isinstance(state, dict):
        raise RuntimeError(
            "Automation state must be a JSON object."
        )

    normalized = {}

    for issue_iid, version in state.items():

        try:
            normalized[
                str(issue_iid)
            ] = int(version)

        except (
            TypeError,
            ValueError
        ):

            raise RuntimeError(
                f"Invalid Version State for Issue {issue_iid}."
            )

    return normalized


# ============================================================
# GitLab
# ============================================================

def get_open_issues():
    """
    Return open GitLab Issues updated within the configured
    lookback period.

    Pagination is followed so that Issues are not missed when
    more than one API page exists.
    """

    issues = []

    page = 1

    updated_after = (
        datetime.now(
            timezone.utc
        ).timestamp()
        - LOOKBACK_SECONDS
    )

    updated_after_iso = datetime.fromtimestamp(
        updated_after,
        tz=timezone.utc
    ).isoformat()

    while True:

        url = (
            f"{GITLAB_URL}/api/v4/projects/"
            f"{PROJECT_ID}/issues"
        )

        response = requests.get(
            url,
            headers=HEADERS,
            params={
                "state": "opened",
                "updated_after": updated_after_iso,
                "per_page": 100,
                "page": page,
                "order_by": "updated_at",
                "sort": "desc"
            },
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        page_issues = response.json()

        if not isinstance(
            page_issues,
            list
        ):
            raise RuntimeError(
                "GitLab Issues API returned "
                "an unexpected response."
            )

        issues.extend(
            page_issues
        )

        next_page = response.headers.get(
            "X-Next-Page"
        )

        if not next_page:
            break

        page = int(
            next_page
        )

    return issues


# ============================================================
# Request Detection
# ============================================================

def is_automation_request(issue):
    """
    Determine whether the Issue contains the execution marker.
    """

    description = (
        issue.get("description")
        or ""
    )

    return (
        "[x] execute automation"
        in description.lower()
    )


def get_issue_version(issue):
    """
    Extract Automation Version from the Issue description.

    The Version must be a positive integer.
    """

    import re

    description = (
        issue.get("description")
        or ""
    )

    match = re.search(
        r"^\s*Automation Version:\s*(.*?)\s*$",
        description,
        re.MULTILINE | re.IGNORECASE
    )

    if not match:
        return None

    value = match.group(1).strip()

    if not re.fullmatch(
        r"[1-9]\d*",
        value
    ):
        return None

    return int(value)


# ============================================================
# Dispatch Decision
# ============================================================

def should_dispatch(issue, state):
    """
    Determine whether the Issue contains a newer Version.

    Version sequencing is NOT consumed here.

    The actual Version validation and consumption occur inside
    automation_request.py.

    This separation is intentional because Authorization and
    Validation failures must also consume the Version once the
    request is accepted for processing.
    """

    issue_iid = issue.get(
        "iid"
    )

    version = get_issue_version(
        issue
    )

    if issue_iid is None:
        return False

    if version is None:
        return False

    last_version = state.get(
        str(issue_iid),
        0
    )

    return version > int(
        last_version
    )


# ============================================================
# Persistent Running State
# ============================================================

def load_running_state():
    """
    Load persistent information about currently running
    automation processes.
    """

    if not os.path.exists(
        RUNNING_FILE
    ):
        return {}

    with open(
        RUNNING_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        state = json.load(
            file
        )

    if not isinstance(
        state,
        dict
    ):
        raise RuntimeError(
            "Automation running state "
            "must be a JSON object."
        )

    return state


def save_running_state(state):
    """
    Persist running state.

    When there are no running requests, the file is removed.
    """

    if state:

        atomic_write_json(
            RUNNING_FILE,
            state
        )

    elif os.path.exists(
        RUNNING_FILE
    ):

        os.remove(
            RUNNING_FILE
        )


def process_is_alive(pid):
    """
    Check whether a process still exists.
    """

    try:

        os.kill(
            int(pid),
            0
        )

        return True

    except (
        ValueError,
        TypeError,
        ProcessLookupError,
        PermissionError
    ):

        return False


def clear_stale_running_entries():
    """
    Remove running entries whose process no longer exists.

    This prevents a stale process record from permanently
    blocking an Issue after a Worker restart or process failure.
    """

    running = load_running_state()

    changed = False

    for issue_iid, info in list(
        running.items()
    ):

        if not isinstance(
            info,
            dict
        ):
            del running[issue_iid]

            changed = True

            continue

        pid = info.get(
            "pid"
        )

        if pid is None:

            del running[issue_iid]

            changed = True

            continue

        if not process_is_alive(
            pid
        ):

            write_log(
                "WARNING",
                "AUTOMATION_RUNNING_STATE_STALE",
                issue=issue_iid,
                version=info.get(
                    "version"
                ),
                pid=pid
            )

            del running[
                issue_iid
            ]

            changed = True

    if changed:
        save_running_state(
            running
        )

    return running


def is_running(
    issue_iid,
    version,
    running
):
    """
    Determine whether the same Issue/Version is already running.
    """

    info = running.get(
        str(issue_iid)
    )

    if not isinstance(
        info,
        dict
    ):
        return False

    return (
        int(
            info.get(
                "version",
                0
            )
        )
        == int(version)
    )


def mark_running(
    issue_iid,
    version
):
    """
    Reserve an Issue/Version for execution.

    The operation is intentionally performed before starting the
    detached helper process to close the duplicate-dispatch race.
    """

    running = load_running_state()

    issue_key = str(
        issue_iid
    )

    if issue_key in running:
        return False

    entry = {
        "version": int(
            version
        ),
        "pid": os.getpid(),
        "started_at": datetime.now(
            timezone.utc
        ).isoformat()
    }

    running[
        issue_key
    ] = entry

    save_running_state(
        running
    )

    write_log(
        "INFO",
        "AUTOMATION_REQUEST_MARKED_RUNNING",
        issue=issue_iid,
        version=version,
        pid=os.getpid()
    )

    return True


def unmark_running(
    issue_iid,
    version=None
):
    """
    Remove the persistent running record for an Issue.
    """

    running = load_running_state()

    issue_key = str(
        issue_iid
    )

    info = running.get(
        issue_key
    )

    if info is None:
        return

    if (
        version is not None
        and int(
            info.get(
                "version",
                0
            )
        )
        != int(version)
    ):
        return

    del running[
        issue_key
    ]

    save_running_state(
        running
    )

    write_log(
        "INFO",
        "AUTOMATION_REQUEST_MARKED_FINISHED",
        issue=issue_iid,
        version=version
    )


# ============================================================
# Background Execution
# ============================================================

def run_background_request(
    issue_iid,
    version
):
    """
    Execute automation_request.py in helper mode.

    The helper waits for the automation to finish and then
    clears the persistent running state.
    """

    command = [
        PYTHON_BIN,
        AUTOMATION_REQUEST,
        str(issue_iid)
    ]

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=WORKER_TIMEOUT,
            check=False
        )

        if result.stdout:

            write_log(
                "INFO",
                "AUTOMATION_REQUEST_STDOUT",
                issue=issue_iid,
                version=version,
                output=result.stdout[-4000:]
            )

        if result.stderr:

            write_log(
                "ERROR",
                "AUTOMATION_REQUEST_STDERR",
                issue=issue_iid,
                version=version,
                output=result.stderr[-4000:]
            )

        write_log(
            "INFO"
            if result.returncode == 0
            else "ERROR",
            "AUTOMATION_REQUEST_FINISHED",
            issue=issue_iid,
            version=version,
            exit_code=result.returncode
        )

    except subprocess.TimeoutExpired as exc:

        write_log(
            "ERROR",
            "AUTOMATION_REQUEST_TIMEOUT",
            issue=issue_iid,
            version=version,
            error=str(exc)
        )

    except Exception as exc:

        write_log(
            "ERROR",
            "AUTOMATION_REQUEST_PROCESS_ERROR",
            issue=issue_iid,
            version=version,
            error=str(exc)
        )

    finally:

        unmark_running(
            issue_iid,
            version
        )


# ============================================================
# Request Execution
# ============================================================

def execute_request(
    issue_iid,
    version
):
    """
    Start the automation request as a detached helper process.

    The main Worker immediately returns to the polling loop.
    """

    helper_args = [
        PYTHON_BIN,
        os.path.abspath(
            __file__
        ),
        "--run-background",
        str(issue_iid),
        str(version)
    ]

    write_log(
        "INFO",
        "AUTOMATION_REQUEST_DISPATCHED",
        issue=issue_iid,
        version=version
    )

    try:

        process = subprocess.Popen(
            helper_args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True
        )

        running = load_running_state()

        info = running.get(
            str(issue_iid)
        )

        if isinstance(
            info,
            dict
        ):

            info["pid"] = process.pid

            info["started_at"] = (
                datetime.now(
                    timezone.utc
                ).isoformat()
            )

            save_running_state(
                running
            )

        write_log(
            "INFO",
            "AUTOMATION_REQUEST_STARTED",
            issue=issue_iid,
            version=version,
            pid=process.pid
        )

        return 0

    except Exception as exc:

        unmark_running(
            issue_iid,
            version
        )

        write_log(
            "ERROR",
            "AUTOMATION_REQUEST_START_FAILED",
            issue=issue_iid,
            version=version,
            error=str(exc)
        )

        return 1


# ============================================================
# Poll Cycle
# ============================================================

def poll_once():
    """
    Execute one GitLab polling cycle.
    """

    running = (
        clear_stale_running_entries()
    )

    issues = get_open_issues()

    state = load_state()

    updated_after = (
        datetime.now(
            timezone.utc
        ).timestamp()
        - LOOKBACK_SECONDS
    )

    updated_after_iso = (
        datetime.fromtimestamp(
            updated_after,
            tz=timezone.utc
        ).isoformat()
    )

    write_log(
        "INFO",
        "POLL_COMPLETED",
        open_issue_count=len(
            issues
        ),
        running_issue_count=len(
            running
        ),
        lookback_seconds=LOOKBACK_SECONDS,
        updated_after=updated_after_iso
    )

    for issue in issues:

        issue_iid = issue.get(
            "iid"
        )

        if issue_iid is None:
            continue

        if not is_automation_request(
            issue
        ):
            continue

        version = get_issue_version(
            issue
        )

        if version is None:

            write_log(
                "WARNING",
                "AUTOMATION_REQUEST_SKIPPED_INVALID_VERSION",
                issue=issue_iid
            )

            continue

        if is_running(
            issue_iid,
            version,
            running
        ):

            write_log(
                "INFO",
                "AUTOMATION_REQUEST_SKIPPED_ALREADY_RUNNING",
                issue=issue_iid,
                version=version,
                pid=running[
                    str(issue_iid)
                ].get("pid")
            )

            continue

        if not should_dispatch(
            issue,
            state
        ):

            write_log(
                "INFO",
                "AUTOMATION_REQUEST_SKIPPED_ALREADY_PROCESSED",
                issue=issue_iid,
                version=version,
                last_processed_version=state.get(
                    str(issue_iid),
                    0
                )
            )

            continue

        write_log(
            "INFO",
            "AUTOMATION_REQUEST_DETECTED_BY_WORKER",
            issue=issue_iid,
            version=version
        )

        # Reserve the Issue/Version before starting the helper.
        # Version itself is consumed by automation_request.py.
        if not mark_running(
            issue_iid,
            version
        ):

            write_log(
                "INFO",
                "AUTOMATION_REQUEST_SKIPPED_ALREADY_RUNNING",
                issue=issue_iid,
                version=version
            )

            continue

        running = load_running_state()

        execute_request(
            issue_iid,
            version
        )

        # Reload state after dispatch because the background
        # process may update the shared Version State.
        state = load_state()

        running = load_running_state()


# ============================================================
# Main
# ============================================================

def main():

    ensure_directories()

    # --------------------------------------------------------
    # Background helper mode
    # --------------------------------------------------------

    if (
        len(sys.argv) == 4
        and sys.argv[1]
        == "--run-background"
    ):

        issue_iid = int(
            sys.argv[2]
        )

        version = int(
            sys.argv[3]
        )

        run_background_request(
            issue_iid,
            version
        )

        return

    # --------------------------------------------------------
    # Main Worker
    # --------------------------------------------------------

    write_log(
        "INFO",
        "WORKER_STARTED",
        poll_interval=POLL_INTERVAL
    )

    print(
        "GitLab-Ansible-Automation-Worker started."
    )

    print(
        f"Polling every {POLL_INTERVAL} seconds."
    )

    while True:

        try:

            poll_once()

        except requests.RequestException as exc:

            write_log(
                "ERROR",
                "GITLAB_POLL_FAILED",
                error=str(exc)
            )

            print(
                f"GitLab polling failed: {exc}"
            )

        except Exception as exc:

            write_log(
                "ERROR",
                "WORKER_UNHANDLED_EXCEPTION",
                error=str(exc)
            )

            print(
                f"Worker error: {exc}"
            )

        time.sleep(
            POLL_INTERVAL
        )


if __name__ == "__main__":
    main()
```
