````python
#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import requests
import yaml


# ============================================================
# Configuration
# ============================================================

GITLAB_URL = os.environ["GITLAB_URL"].rstrip("/")
PROJECT_ID = os.environ["GITLAB_PROJECT_ID"]
GITLAB_TOKEN = os.environ["GITLAB_TOKEN"]

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

CONFIG_DIR = os.path.join(
    BASE_DIR,
    "config"
)

STATE_DIR = os.path.join(
    BASE_DIR,
    "state"
)

LOG_DIR = os.path.join(
    BASE_DIR,
    "logs"
)

ALLOWLIST_FILE = os.path.join(
    CONFIG_DIR,
    "allowed_playbooks.yml"
)

AUTHORIZED_USERS_FILE = os.path.join(
    CONFIG_DIR,
    "authorized_users.yml"
)

STATE_FILE = os.path.join(
    STATE_DIR,
    "automation_state.json"
)

LOG_FILE = os.path.join(
    LOG_DIR,
    "automation-worker.jsonl"
)

# These paths are deployment-specific and can be changed
# without modifying the request-processing logic.
INVENTORY_FILE = os.environ.get(
    "ANSIBLE_INVENTORY",
    os.path.join(
        BASE_DIR,
        "inventory",
        "hosts"
    )
)

PLAYBOOK_DIR = os.environ.get(
    "ANSIBLE_PLAYBOOK_DIR",
    os.path.join(
        BASE_DIR,
        "playbooks"
    )
)

ANSIBLE_PLAYBOOK = os.environ.get(
    "ANSIBLE_PLAYBOOK_BIN",
    "/usr/bin/ansible-playbook"
)

ANSIBLE_INVENTORY = os.environ.get(
    "ANSIBLE_INVENTORY_BIN",
    "/usr/bin/ansible-inventory"
)

REQUEST_TIMEOUT = int(
    os.environ.get(
        "GITLAB_REQUEST_TIMEOUT",
        "30"
    )
)

ANSIBLE_TIMEOUT = int(
    os.environ.get(
        "ANSIBLE_TIMEOUT",
        "3600"
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
    Create required runtime directories.
    """

    os.makedirs(
        CONFIG_DIR,
        exist_ok=True
    )

    os.makedirs(
        STATE_DIR,
        exist_ok=True
    )

    os.makedirs(
        LOG_DIR,
        exist_ok=True
    )


# ============================================================
# Logging
# ============================================================

def write_log(
    level,
    event,
    **fields
):
    """
    Write one structured JSONL audit event.

    Never pass credentials, access tokens, passwords, or private
    keys to this function.
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
# Audit Correlation
# ============================================================

def execution_id(
    issue_iid,
    version
):
    """
    Return a stable logical execution identifier.
    """

    return (
        f"{issue_iid}:{version}"
    )


# ============================================================
# State
# ============================================================

def load_state():
    """
    Load the latest consumed Version for each Issue.

    State format:

        {
            "934": 7,
            "935": 3
        }

    The value represents the latest consumed Automation Version.
    """

    ensure_directories()

    if not os.path.exists(
        STATE_FILE
    ):
        return {}

    with open(
        STATE_FILE,
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
                f"Invalid state for Issue {issue_iid}."
            )

    return normalized


def save_state(state):
    """
    Persist state atomically.

    State is written to a temporary file, flushed to disk, and
    atomically replaced.
    """

    ensure_directories()

    temp_file = (
        STATE_FILE + ".tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            state,
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
        temp_file,
        STATE_FILE
    )


def consume_version(
    issue_iid,
    version
):
    """
    Validate the Version sequence and consume the Version.

    A Version is consumed exactly once.

    Returns:

        (True, previous_version, None)

    on success.

    Returns:

        (False, previous_version, error_message)

    when the Version is invalid.

    Invalid or skipped Versions are NOT consumed.
    """

    state = load_state()

    issue_key = str(
        issue_iid
    )

    last_version = int(
        state.get(
            issue_key,
            0
        )
    )

    expected_version = (
        last_version + 1
    )

    # --------------------------------------------------------
    # Already consumed
    # --------------------------------------------------------

    if version <= last_version:

        message = (
            f"Version {version} has already been consumed. "
            f"Last consumed version is {last_version}."
        )

        write_log(
            "WARNING",
            "VERSION_ALREADY_CONSUMED",
            issue=issue_iid,
            version=version,
            last_consumed_version=last_version
        )

        return (
            False,
            last_version,
            message
        )

    # --------------------------------------------------------
    # Sequence gap
    # --------------------------------------------------------

    if version != expected_version:

        message = (
            f"Invalid Version {version}. "
            f"Expected Version {expected_version}."
        )

        write_log(
            "ERROR",
            "VERSION_SEQUENCE_INVALID",
            issue=issue_iid,
            version=version,
            expected_version=expected_version,
            last_consumed_version=last_version
        )

        return (
            False,
            last_version,
            message
        )

    # --------------------------------------------------------
    # Consume Version
    # --------------------------------------------------------

    state[
        issue_key
    ] = version

    save_state(
        state
    )

    write_log(
        "INFO",
        "AUTOMATION_VERSION_CONSUMED",
        issue=issue_iid,
        execution_id=execution_id(
            issue_iid,
            version
        ),
        version=version,
        previous_version=last_version,
        new_version=version
    )

    return (
        True,
        last_version,
        None
    )


# ============================================================
# GitLab
# ============================================================

def get_issue(
    issue_iid
):
    """
    Retrieve a GitLab Issue.
    """

    url = (
        f"{GITLAB_URL}/api/v4/projects/"
        f"{PROJECT_ID}/issues/{issue_iid}"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


def add_issue_note(
    issue_iid,
    message
):
    """
    Add a Note to a GitLab Issue.
    """

    url = (
        f"{GITLAB_URL}/api/v4/projects/"
        f"{PROJECT_ID}/issues/{issue_iid}/notes"
    )

    response = requests.post(
        url,
        headers=HEADERS,
        json={
            "body": message
        },
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


def get_issue_notes(
    issue_iid
):
    """
    Retrieve all Issue Notes from GitLab.

    Pagination is followed so that the Worker can determine the
    latest relevant system note even when the Issue contains many
    notes.
    """

    notes = []

    page = 1

    while True:

        url = (
            f"{GITLAB_URL}/api/v4/projects/"
            f"{PROJECT_ID}/issues/{issue_iid}/notes"
        )

        response = requests.get(
            url,
            headers=HEADERS,
            params={
                "sort": "desc",
                "order_by": "created_at",
                "per_page": 100,
                "page": page
            },
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        page_notes = response.json()

        if not isinstance(
            page_notes,
            list
        ):
            raise RuntimeError(
                "GitLab Notes API returned "
                "an unexpected response."
            )

        notes.extend(
            page_notes
        )

        next_page = response.headers.get(
            "X-Next-Page"
        )

        if not next_page:
            break

        page = int(
            next_page
        )

    return notes


# ============================================================
# Authorization
# ============================================================

def load_authorized_users():
    """
    Load GitLab users authorized to submit Automation Requests.

    GitLab User ID is the primary identity.
    Username is retained for readability and audit purposes.
    """

    with open(
        AUTHORIZED_USERS_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        config = (
            yaml.safe_load(file)
            or {}
        )

    authorized_users = {}

    for entry in config.get(
        "authorized_users",
        []
    ):

        if not isinstance(
            entry,
            dict
        ):
            continue

        user_id = entry.get(
            "gitlab_user_id"
        )

        if (
            isinstance(
                user_id,
                int
            )
            and user_id > 0
        ):

            authorized_users[
                user_id
            ] = {
                "username": entry.get(
                    "username"
                ),
                "enabled": bool(
                    entry.get(
                        "enabled",
                        False
                    )
                )
            }

    return authorized_users


def get_last_issue_modifier(
    issue_iid,
    issue
):
    """
    Determine the last user who modified the Automation Request.

    Title and description changes are represented by GitLab system
    notes.

    Worker-generated comments are normal notes and therefore do not
    change the authorization identity.

    If no title or description modification exists, the Issue author
    is treated as the initial requester.
    """

    notes = get_issue_notes(
        issue_iid
    )

    automation_edit_actions = {
        "changed the description",
        "changed the title"
    }

    for note in notes:

        if not note.get(
            "system"
        ):
            continue

        body = str(
            note.get(
                "body",
                ""
            )
        ).strip().lower()

        if body not in automation_edit_actions:
            continue

        author = (
            note.get(
                "author"
            )
            or {}
        )

        user_id = author.get(
            "id"
        )

        if isinstance(
            user_id,
            int
        ):

            return {
                "user_id": user_id,
                "username": author.get(
                    "username"
                ),
                "name": author.get(
                    "name"
                ),
                "source": "system_note",
                "note_id": note.get(
                    "id"
                ),
                "action": body,
                "created_at": note.get(
                    "created_at"
                )
            }

    author = (
        issue.get(
            "author"
        )
        or {}
    )

    return {
        "user_id": author.get(
            "id"
        ),
        "username": author.get(
            "username"
        ),
        "name": author.get(
            "name"
        ),
        "source": "issue_author",
        "note_id": None,
        "action": "issue_created",
        "created_at": issue.get(
            "created_at"
        )
    }


def authorize_issue_request(
    issue_iid,
    issue
):
    """
    Authorize the Automation Request using the latest relevant
    Issue modifier.

    Returns:

        (True, modifier, None)

    or:

        (False, modifier, error_message)
    """

    try:

        authorized_users = (
            load_authorized_users()
        )

    except Exception as exc:

        write_log(
            "ERROR",
            "AUTHORIZED_USERS_LOAD_FAILED",
            issue=issue_iid,
            error=str(exc)
        )

        return (
            False,
            None,
            "Authorization configuration could not be loaded."
        )

    try:

        modifier = (
            get_last_issue_modifier(
                issue_iid,
                issue
            )
        )

    except Exception as exc:

        write_log(
            "ERROR",
            "LAST_MODIFIER_LOOKUP_FAILED",
            issue=issue_iid,
            error=str(exc)
        )

        return (
            False,
            None,
            "Last Issue Modifier could not be determined."
        )

    user_id = modifier.get(
        "user_id"
    )

    user_record = authorized_users.get(
        user_id
    )

    if (
        user_record
        and user_record.get(
            "enabled"
        ) is True
    ):

        write_log(
            "INFO",
            "AUTHORIZATION_PASSED",
            issue=issue_iid,
            gitlab_user_id=user_id,
            username=modifier.get(
                "username"
            ),
            modifier_source=modifier.get(
                "source"
            ),
            modifier_action=modifier.get(
                "action"
            ),
            note_id=modifier.get(
                "note_id"
            )
        )

        return (
            True,
            modifier,
            None
        )

    write_log(
        "ERROR",
        "AUTHORIZATION_FAILED",
        issue=issue_iid,
        gitlab_user_id=user_id,
        username=modifier.get(
            "username"
        ),
        modifier_source=modifier.get(
            "source"
        ),
        modifier_action=modifier.get(
            "action"
        ),
        note_id=modifier.get(
            "note_id"
        )
    )

    return (
        False,
        modifier,
        "Last Issue Modifier is not authorized "
        "to execute Automation."
    )


# ============================================================
# Playbook Configuration
# ============================================================

def load_allowed_playbooks():
    """
    Load the approved Playbook allowlist.
    """

    with open(
        ALLOWLIST_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        config = (
            yaml.safe_load(file)
            or {}
        )

    return set(
        config.get(
            "allowed_playbooks",
            []
        )
    )


# ============================================================
# Request Parser
# ============================================================

def parse_automation_request(
    description
):
    """
    Parse an Automation Request from the Issue description.

    Required fields:

        [x] Execute Automation
        Automation Version:
        Playbook:
        Targets:
        Environment:
    """

    if not description:
        return None

    # --------------------------------------------------------
    # Execute Automation
    # --------------------------------------------------------

    execute_match = re.search(
        r"-\s*\[([xX ])\]\s*Execute Automation",
        description
    )

    if not execute_match:
        return None

    execute = (
        execute_match.group(
            1
        ).lower()
        == "x"
    )

    if not execute:
        return None

    # --------------------------------------------------------
    # Automation Version
    # --------------------------------------------------------

    version_match = re.search(
        r"^\s*Automation Version:\s*(.*?)\s*$",
        description,
        re.MULTILINE | re.IGNORECASE
    )

    version = None

    version_status = "missing"

    version_error = (
        "Automation Version is missing. "
        "It is required and must be a positive integer."
    )

    if version_match:

        version_value = (
            version_match.group(
                1
            ).strip()
        )

        if re.fullmatch(
            r"[1-9]\d*",
            version_value
        ):

            version = int(
                version_value
            )

            version_status = "valid"

            version_error = None

        else:

            version_status = "invalid"

            version_error = (
                "Automation Version is invalid. "
                "It must be a positive integer."
            )

    # --------------------------------------------------------
    # Playbook
    # --------------------------------------------------------

    playbook_match = re.search(
        r"^\s*Playbook:\s*(.+?)\s*$",
        description,
        re.MULTILINE | re.IGNORECASE
    )

    playbook = (
        playbook_match.group(
            1
        ).strip()
        if playbook_match
        else None
    )

    # --------------------------------------------------------
    # Environment
    # --------------------------------------------------------

    environment_match = re.search(
        r"^\s*Environment:\s*(.+?)\s*$",
        description,
        re.MULTILINE | re.IGNORECASE
    )

    environment = (
        environment_match.group(
            1
        ).strip()
        if environment_match
        else None
    )

    # --------------------------------------------------------
    # Targets
    # --------------------------------------------------------

    targets = []

    targets_match = re.search(
        r"^\s*Targets:\s*\n"
        r"((?:\s*-\s*.+\n?)+)",
        description,
        re.MULTILINE | re.IGNORECASE
    )

    if targets_match:

        for line in (
            targets_match.group(
                1
            ).splitlines()
        ):

            match = re.match(
                r"^\s*-\s*(.+?)\s*$",
                line
            )

            if match:

                target = (
                    match.group(
                        1
                    ).strip()
                )

                if target:
                    targets.append(
                        target
                    )

    return {
        "version": version,
        "version_status": version_status,
        "version_error": version_error,
        "playbook": playbook,
        "targets": targets,
        "environment": environment
    }


# ============================================================
# Inventory Validation
# ============================================================

def load_inventory_entities():
    """
    Read inventory entities through ansible-inventory.

    Only explicit inventory host and group names are accepted.
    """

    result = subprocess.run(
        [
            ANSIBLE_INVENTORY,
            "-i",
            INVENTORY_FILE,
            "--list"
        ],
        capture_output=True,
        text=True,
        timeout=REQUEST_TIMEOUT,
        check=False
    )

    if result.returncode != 0:

        raise RuntimeError(
            "ansible-inventory failed: "
            + (
                result.stderr.strip()
                or "unknown error"
            )
        )

    inventory = json.loads(
        result.stdout
    )

    if not isinstance(
        inventory,
        dict
    ):
        raise RuntimeError(
            "Unexpected inventory format."
        )

    groups = set()

    hosts = set()

    for name, value in inventory.items():

        if name in {
            "_meta",
            "all"
        }:
            continue

        if not isinstance(
            value,
            dict
        ):
            continue

        if (
            "hosts" in value
            or "children" in value
        ):

            groups.add(
                name
            )

        group_hosts = value.get(
            "hosts",
            []
        )

        if isinstance(
            group_hosts,
            list
        ):

            hosts.update(
                group_hosts
            )

    meta = inventory.get(
        "_meta",
        {}
    )

    hostvars = meta.get(
        "hostvars",
        {}
    )

    if isinstance(
        hostvars,
        dict
    ):

        hosts.update(
            hostvars.keys()
        )

    return (
        hosts,
        groups
    )


def validate_targets(
    targets,
    issue_iid,
    version
):
    """
    Validate targets as explicit inventory hosts or groups.

    Ansible compound patterns are intentionally not accepted.

    Examples of rejected patterns:

        all
        *
        host1:host2
        group:!host
        group1,group2
    """

    errors = []

    try:

        inventory_hosts, inventory_groups = (
            load_inventory_entities()
        )

    except Exception as exc:

        write_log(
            "ERROR",
            "INVENTORY_VALIDATION_FAILED",
            issue=issue_iid,
            version=version,
            error=str(exc)
        )

        return [
            "Inventory validation could not be completed."
        ]

    for target in targets:

        if not re.fullmatch(
            r"[A-Za-z0-9_.-]+",
            target
        ):

            errors.append(
                f"Target '{target}' is invalid. "
                "Only explicit inventory host or group "
                "names are allowed."
            )

            continue

        if target.lower() == "all":

            errors.append(
                "Target 'all' is not allowed."
            )

            continue

        if target in inventory_hosts:

            write_log(
                "INFO",
                "TARGET_VALIDATED",
                issue=issue_iid,
                version=version,
                target=target,
                target_type="host"
            )

            continue

        if target in inventory_groups:

            write_log(
                "INFO",
                "TARGET_VALIDATED",
                issue=issue_iid,
                version=version,
                target=target,
                target_type="group"
            )

            continue

        errors.append(
            f"Target '{target}' does not exist "
            "as an inventory host or group."
        )

        write_log(
            "ERROR",
            "TARGET_NOT_FOUND",
            issue=issue_iid,
            version=version,
            target=target
        )

    return errors


# ============================================================
# Validation
# ============================================================

def validate_request(
    request,
    issue_iid
):
    """
    Validate the request after Version consumption.

    Version sequencing is deliberately NOT checked here because
    consume_version() has already reserved the Version.

    This allows Authorization and Validation failures to consume
    the Version as required by the execution model.
    """

    errors = []

    playbook = request.get(
        "playbook"
    )

    targets = request.get(
        "targets"
    )

    environment = request.get(
        "environment"
    )

    version = request.get(
        "version"
    )

    # --------------------------------------------------------
    # Required fields
    # --------------------------------------------------------

    if (
        request.get(
            "version_status"
        )
        != "valid"
    ):

        errors.append(
            request.get(
                "version_error",
                "Automation Version is invalid."
            )
        )

        write_log(
            "ERROR",
            "AUTOMATION_VERSION_INVALID",
            issue=issue_iid,
            version=version,
            version_status=request.get(
                "version_status"
            )
        )

    if not playbook:

        errors.append(
            "Playbook is missing."
        )

    if not targets:

        errors.append(
            "Targets are missing."
        )

    if not environment:

        errors.append(
            "Environment is missing."
        )

    # --------------------------------------------------------
    # Playbook Allowlist
    # --------------------------------------------------------

    if playbook:

        allowed_playbooks = (
            load_allowed_playbooks()
        )

        if playbook not in allowed_playbooks:

            errors.append(
                f"Playbook '{playbook}' is not allowed."
            )

            write_log(
                "ERROR",
                "PLAYBOOK_NOT_ALLOWED",
                issue=issue_iid,
                version=version,
                playbook=playbook
            )

    # --------------------------------------------------------
    # Environment
    # --------------------------------------------------------

    allowed_environments = {
        "production",
        "staging",
        "development",
        "test"
    }

    if (
        environment
        and environment not in allowed_environments
    ):

        errors.append(
            f"Environment '{environment}' is not allowed."
        )

        write_log(
            "ERROR",
            "ENVIRONMENT_NOT_ALLOWED",
            issue=issue_iid,
            version=version,
            environment=environment
        )

    # --------------------------------------------------------
    # Target Validation
    # --------------------------------------------------------

    if targets:

        target_errors = (
            validate_targets(
                targets,
                issue_iid,
                version
            )
        )

        errors.extend(
            target_errors
        )

    return errors


# ============================================================
# Ansible Execution
# ============================================================

def build_ansible_command(
    request
):
    """
    Build the Ansible command using controlled paths.

    User input is used only after allowlist and target validation.
    """

    playbook = request[
        "playbook"
    ]

    playbook_path = os.path.join(
        PLAYBOOK_DIR,
        f"{playbook}.yml"
    )

    command = [
        ANSIBLE_PLAYBOOK,
        "-i",
        INVENTORY_FILE,
        playbook_path
    ]

    for target in request[
        "targets"
    ]:

        command.extend(
            [
                "--limit",
                target
            ]
        )

    return command


def execute_ansible(
    request,
    issue_iid
):
    """
    Execute the validated Ansible request.

    shell=False is intentional. User-controlled values are never
    interpreted by a shell.
    """

    command = (
        build_ansible_command(
            request
        )
    )

    write_log(
        "INFO",
        "ANSIBLE_EXECUTION_STARTED",
        issue=issue_iid,
        execution_id=execution_id(
            issue_iid,
            request["version"]
        ),
        version=request["version"],
        playbook=request["playbook"],
        targets=request["targets"],
        environment=request["environment"]
    )

    print()
    print(
        "Ansible Execution"
    )
    print(
        "-----------------"
    )

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=ANSIBLE_TIMEOUT,
            check=False
        )

    except subprocess.TimeoutExpired as exc:

        stdout = (
            exc.stdout
            or ""
        )

        stderr = (
            exc.stderr
            or ""
        )

        write_log(
            "ERROR",
            "ANSIBLE_EXECUTION_TIMEOUT",
            issue=issue_iid,
            version=request["version"],
            playbook=request["playbook"],
            targets=request["targets"],
            timeout=ANSIBLE_TIMEOUT
        )

        return (
            False,
            None,
            stdout,
            stderr
        )

    except OSError as exc:

        write_log(
            "ERROR",
            "ANSIBLE_EXECUTION_START_FAILED",
            issue=issue_iid,
            version=request["version"],
            playbook=request["playbook"],
            targets=request["targets"],
            error=str(exc)
        )

        return (
            False,
            None,
            "",
            str(exc)
        )

    stdout = (
        result.stdout
        or ""
    )

    stderr = (
        result.stderr
        or ""
    )

    if result.returncode == 0:

        write_log(
            "INFO",
            "ANSIBLE_EXECUTION_SUCCEEDED",
            issue=issue_iid,
            execution_id=execution_id(
                issue_iid,
                request["version"]
            ),
            version=request["version"],
            playbook=request["playbook"],
            targets=request["targets"],
            exit_code=result.returncode
        )

        return (
            True,
            result.returncode,
            stdout,
            stderr
        )

    write_log(
        "ERROR",
        "ANSIBLE_EXECUTION_FAILED",
        issue=issue_iid,
        execution_id=execution_id(
            issue_iid,
            request["version"]
        ),
        version=request["version"],
        playbook=request["playbook"],
        targets=request["targets"],
        exit_code=result.returncode
    )

    return (
        False,
        result.returncode,
        stdout,
        stderr
    )


# ============================================================
# GitLab Result
# ============================================================

def extract_recap_lines(
    stdout
):
    """
    Extract useful Ansible recap lines without posting the full
    command output to GitLab.
    """

    recap_lines = []

    for line in (
        stdout or ""
    ).strip().splitlines():

        if (
            "PLAY RECAP" in line
            or (
                "ok=" in line
                and "failed=" in line
            )
        ):

            recap_lines.append(
                line
            )

    return recap_lines[
        -5:
    ]


def post_execution_result_note(
    issue_iid,
    request,
    success,
    exit_code,
    state_updated,
    stdout="",
    stderr=""
):
    """
    Post the execution result to the GitLab Issue.
    """

    execution_status = (
        "SUCCESS"
        if success
        else "FAILED"
    )

    execution_icon = (
        "✅"
        if success
        else "❌"
    )

    state_status = (
        "UPDATED"
        if state_updated
        else "UNCHANGED"
    )

    recap_lines = (
        extract_recap_lines(
            stdout
        )
    )

    output_section = ""

    if recap_lines:

        output_section = (
            "\n### Ansible Result\n\n"
            "```text\n"
            + "\n".join(
                recap_lines
            )
            + "\n```\n"
        )

    note = f"""🤖 **Automation Execution Result**

**Issue:** #{issue_iid}
**Version:** `{request['version']}`

**Playbook:** `{request['playbook']}`
**Targets:** {", ".join(request['targets'])}
**Environment:** `{request['environment']}`

### Result

{execution_icon} **Validation:** PASSED

{execution_icon} **Execution:** {execution_status}

**Exit Code:** `{exit_code if exit_code is not None else "N/A"}`
**State:** `{state_status}`
{output_section}"""

    try:

        add_issue_note(
            issue_iid,
            note
        )

        write_log(
            "INFO",
            "GITLAB_EXECUTION_RESULT_POSTED",
            issue=issue_iid,
            execution_id=execution_id(
                issue_iid,
                request["version"]
            ),
            version=request["version"],
            execution=execution_status,
            exit_code=exit_code,
            state_updated=state_updated
        )

        return True

    except Exception as exc:

        write_log(
            "ERROR",
            "GITLAB_EXECUTION_RESULT_POST_FAILED",
            issue=issue_iid,
            execution_id=execution_id(
                issue_iid,
                request["version"]
            ),
            version=request["version"],
            execution=execution_status,
            exit_code=exit_code,
            state_updated=state_updated,
            error=str(exc)
        )

        return False


def post_authorization_failure(
    issue_iid,
    request,
    modifier,
    authorization_error
):
    """
    Post a safe authorization failure message.

    The message identifies the modifier for auditability but does
    not expose authentication material.
    """

    modifier_username = (
        modifier.get(
            "username"
        )
        if modifier
        else "unknown"
    )

    modifier_user_id = (
        modifier.get(
            "user_id"
        )
        if modifier
        else "unknown"
    )

    note = f"""🤖 **Automation Authorization Result**

**Issue:** #{issue_iid}
**Version:** `{request['version']}`

**Last Modifier:** `{modifier_username}`
**GitLab User ID:** `{modifier_user_id}`

### Authorization

❌ **FAILED**

- {authorization_error}

**Execution:** NOT STARTED
"""

    try:

        add_issue_note(
            issue_iid,
            note
        )

    except Exception as exc:

        write_log(
            "ERROR",
            "GITLAB_AUTHORIZATION_RESULT_POST_FAILED",
            issue=issue_iid,
            version=request["version"],
            error=str(exc)
        )


def post_validation_failure(
    issue_iid,
    request,
    errors
):
    """
    Post validation failure to GitLab.
    """

    error_lines = "\n".join(
        f"- {error}"
        for error in errors
    )

    note = f"""🤖 **Automation Request Validation**

**Issue:** #{issue_iid}
**Version:** `{request['version']}`

**Playbook:** `{request.get('playbook') or 'N/A'}`
**Targets:** {", ".join(request.get('targets') or []) or 'N/A'}
**Environment:** `{request.get('environment') or 'N/A'}`

### Validation

❌ **FAILED**

{error_lines}

**Execution:** NOT STARTED
"""

    add_issue_note(
        issue_iid,
        note
    )


# ============================================================
# Main
# ============================================================

def main():

    ensure_directories()

    if len(sys.argv) != 2:

        print(
            f"Usage: {sys.argv[0]} <issue_iid>",
            file=sys.stderr
        )

        sys.exit(1)

    issue_iid = sys.argv[1]

    write_log(
        "INFO",
        "ISSUE_PROCESSING_STARTED",
        issue=issue_iid
    )

    print(
        f"Reading GitLab Issue #{issue_iid}..."
    )

    issue = get_issue(
        issue_iid
    )

    description = (
        issue.get(
            "description",
            ""
        )
    )

    request = parse_automation_request(
        description
    )

    if request is None:

        write_log(
            "INFO",
            "NO_AUTOMATION_REQUEST",
            issue=issue_iid
        )

        print(
            "No Automation Request detected."
        )

        sys.exit(0)

    write_log(
        "INFO",
        "AUTOMATION_REQUEST_DETECTED",
        issue=issue_iid,
        version=request.get(
            "version"
        ),
        playbook=request.get(
            "playbook"
        ),
        targets=request.get(
            "targets"
        ),
        environment=request.get(
            "environment"
        )
    )

    # ========================================================
    # Version Sequence + Consumption
    # ========================================================

    version = request.get(
        "version"
    )

    if (
        request.get(
            "version_status"
        )
        != "valid"
    ):

        write_log(
            "ERROR",
            "AUTOMATION_VERSION_INVALID",
            issue=issue_iid,
            version=version
        )

        try:

            post_validation_failure(
                issue_iid,
                request,
                [
                    request.get(
                        "version_error",
                        "Automation Version is invalid."
                    )
                ]
            )

        except Exception as exc:

            write_log(
                "ERROR",
                "GITLAB_VALIDATION_RESULT_POST_FAILED",
                issue=issue_iid,
                version=version,
                error=str(exc)
            )

        sys.exit(2)

    consumed, previous_version, version_error = (
        consume_version(
            issue_iid,
            version
        )
    )

    if not consumed:

        try:

            add_issue_note(
                issue_iid,
                f"""🤖 **Automation Version Validation**

**Issue:** #{issue_iid}
**Version:** `{version}`

❌ **Version rejected**

{version_error}

**Execution:** NOT STARTED
"""
            )

        except Exception as exc:

            write_log(
                "ERROR",
                "GITLAB_VERSION_RESULT_POST_FAILED",
                issue=issue_iid,
                version=version,
                error=str(exc)
            )

        sys.exit(2)

    # ========================================================
    # Authorization
    # ========================================================

    print(
        "Authorization"
    )

    authorized, modifier, authorization_error = (
        authorize_issue_request(
            issue_iid,
            issue
        )
    )

    if not authorized:

        post_authorization_failure(
            issue_iid,
            request,
            modifier,
            authorization_error
        )

        write_log(
            "ERROR",
            "AUTOMATION_REQUEST_AUTHORIZATION_FAILED",
            issue=issue_iid,
            execution_id=execution_id(
                issue_iid,
                version
            ),
            version=version,
            playbook=request.get(
                "playbook"
            ),
            targets=request.get(
                "targets"
            ),
            environment=request.get(
                "environment"
            ),
            gitlab_user_id=(
                modifier.get(
                    "user_id"
                )
                if modifier
                else None
            ),
            username=(
                modifier.get(
                    "username"
                )
                if modifier
                else None
            ),
            state_updated=True
        )

        sys.exit(2)

    print(
        "Authorization: PASSED"
    )

    write_log(
        "INFO",
        "AUTHORIZATION_COMPLETED",
        issue=issue_iid,
        execution_id=execution_id(
            issue_iid,
            version
        ),
        version=version,
        gitlab_user_id=modifier.get(
            "user_id"
        ),
        username=modifier.get(
            "username"
        ),
        modifier_source=modifier.get(
            "source"
        ),
        modifier_action=modifier.get(
            "action"
        )
    )

    # ========================================================
    # Validation
    # ========================================================

    write_log(
        "INFO",
        "VALIDATION_STARTED",
        issue=issue_iid,
        execution_id=execution_id(
            issue_iid,
            version
        ),
        version=version
    )

    errors = validate_request(
        request,
        issue_iid
    )

    if errors:

        print(
            "Validation: FAILED"
        )

        for error in errors:
            print(
                f"  - {error}"
            )

        try:

            post_validation_failure(
                issue_iid,
                request,
                errors
            )

        except Exception as exc:

            write_log(
                "ERROR",
                "GITLAB_VALIDATION_RESULT_POST_FAILED",
                issue=issue_iid,
                version=version,
                error=str(exc)
            )

        write_log(
            "ERROR",
            "VALIDATION_FAILED",
            issue=issue_iid,
            execution_id=execution_id(
                issue_iid,
                version
            ),
            version=version,
            playbook=request.get(
                "playbook"
            ),
            targets=request.get(
                "targets"
            ),
            environment=request.get(
                "environment"
            ),
            errors=errors,
            state_updated=True
        )

        sys.exit(2)

    print(
        "Validation: PASSED"
    )

    write_log(
        "INFO",
        "VALIDATION_COMPLETED",
        issue=issue_iid,
        execution_id=execution_id(
            issue_iid,
            version
        ),
        version=version,
        playbook=request["playbook"],
        targets=request["targets"],
        environment=request["environment"]
    )

    # ========================================================
    # Execution
    # ========================================================

    print(
        "Execution: STARTED"
    )

    success, exit_code, stdout, stderr = (
        execute_ansible(
            request,
            issue_iid
        )
    )

    # ========================================================
    # Execution Result
    # ========================================================

    if not success:

        print(
            "Execution: FAILED"
        )

        write_log(
            "ERROR",
            "AUTOMATION_REQUEST_FAILED",
            issue=issue_iid,
            execution_id=execution_id(
                issue_iid,
                version
            ),
            version=version,
            playbook=request["playbook"],
            targets=request["targets"],
            environment=request["environment"],
            exit_code=exit_code,
            state_updated=True
        )

        post_execution_result_note(
            issue_iid,
            request,
            success=False,
            exit_code=exit_code,
            state_updated=True,
            stdout=stdout,
            stderr=stderr
        )

        sys.exit(3)

    # ========================================================
    # Success
    # ========================================================

    print(
        "Execution: SUCCESS"
    )

    write_log(
        "INFO",
        "AUTOMATION_REQUEST_COMPLETED",
        issue=issue_iid,
        execution_id=execution_id(
            issue_iid,
            version
        ),
        version=version,
        playbook=request["playbook"],
        targets=request["targets"],
        environment=request["environment"],
        exit_code=exit_code,
        state_updated=True
    )

    post_execution_result_note(
        issue_iid,
        request,
        success=True,
        exit_code=exit_code,
        state_updated=True,
        stdout=stdout,
        stderr=stderr
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
````
