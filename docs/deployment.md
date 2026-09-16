# Deployment Guide

## Overview

This document describes how to deploy the GitLab-Ansible-Automation-Worker in a generic environment.

The deployment model is intentionally simple:

```text
GitLab
   │
   │ HTTPS / API Polling
   ▼
Automation Worker
   │
   │ Local Ansible Execution
   ▼
Managed Targets
```

The Worker establishes outbound communication to GitLab and does not require an inbound connection from GitLab.

---

## Prerequisites

The deployment requires:

* Linux server
* Python 3.10 or newer
* Ansible
* Network connectivity from Worker to GitLab
* GitLab API access
* GitLab Personal Access Token or equivalent API credential
* SSH access to Linux targets, if required
* WinRM/Kerberos access to Windows targets, if required
* A dedicated operating-system account for the Worker
* Appropriate filesystem permissions

The Worker should be deployed inside a trusted infrastructure network.

---

## Recommended Directory Structure

Example installation layout:

```text
/opt/gitlab-ansible-automation-worker/
├── automation_worker.py
├── automation_request.py
├── .venv/
├── config/
│   ├── allowed_playbooks.yml
│   └── authorized_users.yml
├── state/
├── logs/
└── templates/
```

The exact installation path can be changed according to the target environment.

---

## Create a Dedicated Service Account

The Worker should run under a dedicated non-privileged operating-system account.

Example:

```bash
sudo useradd \
  --system \
  --create-home \
  --home-dir /opt/gitlab-ansible-automation-worker \
  --shell /usr/sbin/nologin \
  automation
```

Create the required directories:

```bash
sudo mkdir -p /opt/gitlab-ansible-automation-worker/{config,state,logs,templates}
```

Set ownership:

```bash
sudo chown -R automation:automation \
  /opt/gitlab-ansible-automation-worker
```

The Worker should not run directly as `root` unless there is a documented operational requirement.

---

## Install Python Virtual Environment

Create a Python virtual environment:

```bash
cd /opt/gitlab-ansible-automation-worker

sudo -u automation python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Install required Python packages:

```bash
pip install --upgrade pip
pip install requests
```

Additional dependencies should be installed only when required by the implementation.

---

## Install Ansible

Ansible can be installed through the operating system package manager or the Python environment.

Example:

```bash
sudo apt update
sudo apt install ansible
```

Verify:

```bash
ansible --version
```

The deployment should use a supported Ansible version appropriate for the target environment.

---

## Install Worker Files

Copy the Worker files into the installation directory:

```text
/opt/gitlab-ansible-automation-worker/
```

Required files:

```text
automation_worker.py
automation_request.py
```

Make the scripts executable if required:

```bash
chmod 750 automation_worker.py
chmod 750 automation_request.py
```

Set ownership:

```bash
sudo chown automation:automation \
  automation_worker.py \
  automation_request.py
```

---

## Configure GitLab Connection

The Worker requires access to the GitLab API.

Configuration should be provided through environment variables rather than hard-coded credentials.

Example:

```bash
GITLAB_URL=https://gitlab.example.com
GITLAB_PROJECT_ID=1
GITLAB_TOKEN=<REDACTED>
```

The actual GitLab URL, Project ID, and token must be specific to the deployment environment.

Credentials must never be committed to the public repository.

---

## Environment File

Create:

```text
/etc/gitlab-ansible-automation-worker/automation-worker.env
```

Example:

```ini
GITLAB_URL=https://gitlab.example.com
GITLAB_PROJECT_ID=1
GITLAB_TOKEN=REPLACE_WITH_SECRET
```

Protect the file:

```bash
sudo chown root:automation \
  /etc/gitlab-ansible-automation-worker/automation-worker.env

sudo chmod 640 \
  /etc/gitlab-ansible-automation-worker/automation-worker.env
```

The token should have only the permissions required by the Worker.

---

## Configure Playbook Allowlist

Create:

```text
config/allowed_playbooks.yml
```

using the provided example:

```text
config/allowed_playbooks.example.yml
```

Example:

```yaml
allowed_playbooks:
  - endpoint_agent
  - monitoring_agent
  - os_hardening
  - software_install
```

Only explicitly approved Playbooks should be added to this list.

The allowlist is a security boundary and should be treated as controlled configuration.

---

## Configure Authorized Users

Create:

```text
config/authorized_users.yml
```

using:

```text
config/authorized_users.example.yml
```

Example:

```yaml
authorized_users:

  - gitlab_user_id: 1001
    username: automation-admin
    enabled: true

  - gitlab_user_id: 1002
    username: infrastructure-operator
    enabled: true
```

Only users who are authorized to submit automation requests should be enabled.

---

## Configure Ansible Inventory

The Worker relies on the local Ansible inventory configured for the target environment.

Example:

```text
inventory/
├── hosts
└── group_vars/
```

The inventory should contain only the targets that the Worker is authorized to manage.

Credentials should not be stored directly in the inventory when a more secure credential-management mechanism is available.

---

## Linux Target Connectivity

Linux targets normally use SSH.

Example:

```text
Worker
   │
   │ SSH
   ▼
Linux Target
```

The Worker service account should have access to the required SSH credentials.

SSH keys should be protected using appropriate filesystem permissions.

---

## Windows Target Connectivity

Windows Domain environments may use Kerberos and WinRM.

Example:

```text
Worker
   │
   │ Kerberos / WinRM
   ▼
Windows Domain Target
```

The required Kerberos configuration, DNS resolution, domain trust, and WinRM configuration must be completed before automation execution.

The exact configuration is environment-specific and is outside the scope of this generic deployment guide.

---

## Test Ansible Connectivity

Before starting the Worker, verify Ansible connectivity manually.

Linux example:

```bash
ansible all -i inventory/hosts -m ansible.builtin.ping
```

Windows example:

```bash
ansible windows \
  -i inventory/hosts \
  -m ansible.windows.win_ping
```

The Worker should not be used as the first troubleshooting layer.

First establish that Ansible can communicate with the target systems successfully.

---

## Install systemd Service

Copy:

```text
systemd/automation-worker.service
```

to:

```text
/etc/systemd/system/automation-worker.service
```

Reload systemd:

```bash
sudo systemctl daemon-reload
```

Enable the service:

```bash
sudo systemctl enable automation-worker
```

Start the Worker:

```bash
sudo systemctl start automation-worker
```

Check status:

```bash
sudo systemctl status automation-worker
```

---

## Verify Worker Logs

Check systemd logs:

```bash
sudo journalctl \
  -u automation-worker \
  -f
```

The Worker audit log should also be available under:

```text
/opt/gitlab-ansible-automation-worker/logs/
```

---

## Initial Functional Test

Use the provided test Playbook:

```text
examples/automation_test_pass.yml
```

Create a test Automation Request in GitLab.

Example:

```markdown
# Automation Request

[x] execute automation

Automation Version: 1

Playbook: automation_test_pass

Targets: test

Environment: test
```

The Worker should:

1. Detect the Issue.
2. Validate the request.
3. Check authorization.
4. Consume Version 1.
5. Start Ansible.
6. Execute the approved Playbook.
7. Record audit events.
8. Post the result to GitLab.
9. Complete the request.

---

## Failure Test

Use:

```text
examples/automation_test_failed.yml
```

The expected result is:

```text
Automation Version
        │
        ▼
Version consumed
        │
        ▼
Ansible execution
        │
        ▼
Failure
```

The Version must remain consumed even though Ansible failed.

The next attempt must use the next Version.

---

## Long-Running Test

Use:

```text
examples/automation_test_long.yml
```

This Playbook intentionally waits before completing.

While it is running, the Worker should continue polling GitLab.

A duplicate request for the same Issue/Version must not start a second Ansible process.

The audit log should contain an event indicating that the request was already running.

---

## Validate State Management

After processing an Automation Request, inspect the state file.

Example:

```json
{
  "934": 1
}
```

After another valid attempt:

```json
{
  "934": 2
}
```

The Version must increase sequentially.

If Version 2 fails authorization or Ansible execution, Version 2 remains consumed.

The next attempt must therefore use Version 3.

---

## Validate Audit Logging

Inspect:

```text
logs/automation-worker.jsonl
```

Example:

```bash
tail -f logs/automation-worker.jsonl
```

Verify that the request lifecycle produces structured events.

The events should allow the operator to identify:

* Issue
* Version
* Request
* Authorization result
* Validation result
* Execution result
* Completion

---

## Log Rotation

Configure logrotate for the Worker audit log.

Example:

```text
/opt/gitlab-ansible-automation-worker/logs/automation-worker.jsonl {
    daily
    rotate 90
    compress
    delaycompress
    missingok
    notifempty
    su automation automation
    create 0640 automation automation
}
```

The retention period should be adjusted according to organizational requirements.

---

## SIEM Integration

For centralized monitoring, configure the organization's log collector or SIEM forwarder to monitor:

```text
/opt/gitlab-ansible-automation-worker/logs/automation-worker.jsonl
```

The Worker should generate structured events locally while centralized infrastructure handles:

* Long-term retention
* Search
* Correlation
* Dashboards
* Alerting
* Security investigation

---

## Production Security Checklist

Before production use, verify:

```text
[ ] Dedicated Worker service account
[ ] No root execution unless explicitly required
[ ] GitLab token stored outside source code
[ ] GitLab token permissions minimized
[ ] Playbook allowlist configured
[ ] Authorized users configured
[ ] Ansible inventory restricted
[ ] SSH credentials protected
[ ] Kerberos/WinRM configured where required
[ ] State directory protected
[ ] Audit log protected
[ ] Log rotation configured
[ ] SIEM forwarding configured
[ ] Worker service enabled
[ ] Worker health monitoring configured
[ ] Failure alerting configured
[ ] Backup strategy defined
[ ] Recovery procedure tested
```

---

## Network Security Model

The Worker should operate using outbound connections.

Example:

```text
                  HTTPS / API
        ┌─────────────────────────┐
        │                         │
        ▼                         │
┌──────────────┐                  │
│    GitLab    │                  │
└──────────────┘                  │
        ▲                         │
        │                         │
        │ Pull                    │
        │                         │
┌──────────────┐                  │
│ Automation   │──────────────────┘
│   Worker     │
└──────────────┘
        │
        │ Ansible
        ▼
┌──────────────────────┐
│ Managed Infrastructure│
└──────────────────────┘
```

No inbound GitLab-to-Worker connection is required.

This model reduces the exposed attack surface of the Worker.

---

## Updating the Worker

Before updating the Worker:

1. Review the change.
2. Validate the code in a test environment.
3. Back up configuration and state.
4. Stop the Worker if required.
5. Deploy the updated files.
6. Verify permissions.
7. Start the Worker.
8. Review service logs.
9. Execute a controlled test request.

Example:

```bash
sudo systemctl stop automation-worker
```

Deploy the new version.

Then:

```bash
sudo systemctl start automation-worker
```

Verify:

```bash
sudo systemctl status automation-worker
```

---

## Backup and Recovery

The Worker itself is stateless with respect to the actual managed infrastructure, but local state and configuration are important for correct operation.

The following should be included in the backup strategy:

```text
config/
state/
systemd configuration
environment configuration
```

Audit logs may be retained centrally in the SIEM and do not necessarily need to be included in the same backup mechanism.

In virtualized environments, VM-level backup can be used to recover the Worker host.

---

## Recovery Considerations

After recovery:

1. Verify the Worker filesystem.
2. Verify configuration files.
3. Verify GitLab API connectivity.
4. Verify Ansible connectivity.
5. Verify state integrity.
6. Verify the Worker service.
7. Verify audit logging.
8. Confirm that no automation is unintentionally executed twice.

The persistent Version State is particularly important because it prevents previously consumed Versions from being processed again.

---

## Troubleshooting

### Worker Does Not Start

Check:

```bash
sudo systemctl status automation-worker
```

Then:

```bash
sudo journalctl -u automation-worker -n 100
```

---

### GitLab API Failure

Verify:

```text
GitLab URL
Project ID
API token
Network connectivity
TLS certificate validation
```

Do not disable TLS verification as a permanent troubleshooting solution.

---

### Ansible Failure

Test the Playbook manually:

```bash
ansible-playbook \
  -i inventory/hosts \
  playbook.yml
```

Then inspect:

* Inventory
* Credentials
* Target connectivity
* Playbook variables
* Ansible output

---

### Request Is Not Executed

Check:

1. Issue contains the required execution marker.
2. Automation Version is correct.
3. Requester is authorized.
4. Playbook is allowlisted.
5. Targets pass validation.
6. Another execution is not already running.
7. Worker polling is active.

---

## Deployment Principle

The Worker should remain a small and controlled execution component.

Its responsibilities are:

```text
GitLab Polling
      │
      ▼
Request Parsing
      │
      ▼
Version Control
      │
      ▼
Authorization
      │
      ▼
Validation
      │
      ▼
Ansible Execution
      │
      ▼
Result Reporting
      │
      ▼
Audit Logging
```

Functions such as centralized secret management, SIEM correlation, enterprise inventory, and advanced workflow orchestration should be provided by dedicated platform components as the architecture evolves.

---

## Scope of This Reference Deployment

This repository provides a generic reference implementation.

It intentionally does not include:

* Organization-specific GitLab URLs
* Internal IP addresses
* Production hostnames
* Real credentials
* Real GitLab users
* Production inventory
* Organization-specific Playbooks
* Internal security policies
* Confidential infrastructure information

Each deployment must adapt the configuration and security controls to its own environment.
