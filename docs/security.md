# Security

## Overview

GitLab-Ansible-Automation-Worker is designed with security as a core architectural requirement.

The system is based on a **Pull Model**, explicit authorization, Playbook allowlisting, Version-based execution control, persistent execution state, least-privilege service execution, and structured auditing.

This document describes the security controls implemented by the reference architecture.

---

## Security Objectives

The primary security objectives are:

* Prevent unauthorized automation execution
* Prevent execution of unapproved Playbooks
* Prevent duplicate execution
* Prevent reuse of previously processed Automation Versions
* Avoid inbound connectivity to the Automation Worker
* Separate credentials from application source code
* Provide an auditable execution trail
* Minimize the privileges of the Worker service
* Protect persistent state from accidental corruption

---

## Pull-Based Security Model

The Worker initiates communication with GitLab.

```text
GitLab
   ▲
   │
   │ HTTPS / API
   │
Worker
```

GitLab does not need to initiate an inbound connection to the Worker.

This reduces the network exposure of the Automation Worker and allows firewall policies to restrict the Worker to required outbound connections.

---

## Authentication

The Worker authenticates to the GitLab API using a token supplied through its runtime environment.

Example:

```ini
GITLAB_URL=https://gitlab.example.com
GITLAB_PROJECT_ID=1
GITLAB_TOKEN=<secret>
```

Credentials must not be hard-coded into Python source code or committed to the repository.

The example configuration shown in this repository is intentionally generic.

---

## Authorization

Authentication identifies the GitLab user.

Authorization determines whether that user is allowed to request automation execution.

Authorized users are maintained through a dedicated configuration file.

Example:

```yaml
authorized_users:

  - gitlab_user_id: 1001
    username: automation-admin
    enabled: true
```

Only users explicitly enabled in the authorization configuration should be permitted to execute Automation Requests.

---

## Last Modifier Authorization Model

The reference implementation authorizes the user who most recently modified the Automation Request.

This means the original Issue creator is not necessarily the authoritative user.

Example:

```text
User A creates Issue
        │
        ▼
User B modifies Automation Request
        │
        ▼
Worker evaluates User B
        │
        ├── Authorized → continue
        └── Unauthorized → reject
```

This model ensures that the identity associated with the final request modification is considered during authorization.

---

## Playbook Allowlisting

The Worker must not execute arbitrary Playbook paths supplied by a user.

Only explicitly approved Playbook names are allowed.

Example:

```yaml
allowed_playbooks:
  - endpoint_agent
  - monitoring_agent
  - os_hardening
  - software_install
```

The Playbook requested by the GitLab Issue must match an approved entry.

This provides a basic application-layer control against arbitrary Playbook execution.

---

## Path Restriction

Users should provide a logical Playbook name rather than an arbitrary filesystem path.

For example:

```text
Playbook: os_hardening
```

is preferred over:

```text
Playbook: /tmp/custom.yml
```

The Worker maps approved logical names to controlled Playbook locations.

This prevents users from using the Automation Request as a mechanism for selecting arbitrary files for execution.

---

## Automation Version Security Control

Every Automation Request uses a sequential Automation Version.

Example:

```text
Version 1
Version 2
Version 3
Version 4
```

Once a Version has been processed, it cannot be reused.

This prevents accidental or intentional replay of the same Automation Request.

The Version represents the **processing attempt / request revision**.

A processing attempt consumes its Version regardless of whether the result is:

* Authorization Failure
* Validation Failure
* Ansible Failure
* Timeout
* Success

---

## Duplicate Execution Prevention

The Worker maintains persistent running state for active Automation Requests.

Example:

```json
{
  "934": {
    "version": 7,
    "pid": 816541,
    "started_at": "2026-09-16T06:21:00+00:00"
  }
}
```

If the Worker polls GitLab while the same Issue/Version is still executing, the request is skipped.

```text
Issue 934 / Version 7
        │
        ▼
Already Running
        │
        ▼
Skip duplicate execution
```

This is especially important for long-running Ansible jobs.

---

## Long-Running Execution

Long-running automation is executed by a detached background process.

The main Worker continues polling GitLab.

This provides two security and operational benefits:

1. The Worker remains responsive.
2. Duplicate executions can be detected during subsequent polling cycles.

The persistent Running State allows the Worker to distinguish an active execution from a new request.

---

## Least Privilege

The Worker should run under a dedicated operating-system account.

Example systemd configuration:

```ini
[Service]
User=automation
Group=automation
```

The service should not run as `root` unless the target environment explicitly requires it.

The Worker should have access only to:

* Its application directory
* Required configuration
* State files
* Log files
* Required Ansible resources
* Required network destinations

---

## Systemd Hardening

The reference service configuration includes:

```ini
NoNewPrivileges=true
PrivateTmp=true
```

### NoNewPrivileges

Prevents the service and its child processes from gaining additional Linux privileges through mechanisms such as set-user-ID or file capabilities.

### PrivateTmp

Provides the service with an isolated temporary directory.

Additional systemd hardening controls can be introduced according to the deployment environment.

---

## Secret Management

Secrets should never be stored in:

* Python source code
* GitLab Issue descriptions
* Git commits
* README files
* Example configuration files
* Audit logs

The GitLab API token should be supplied through a protected runtime configuration mechanism.

Example:

```text
/etc/gitlab-ansible-automation-worker/automation-worker.env
```

The file should have restrictive permissions.

Example:

```bash
chmod 600 /etc/gitlab-ansible-automation-worker/automation-worker.env
```

The exact ownership and permission model should be adapted to the service account and deployment environment.

---

## State File Protection

The Worker maintains persistent processing state.

Example:

```json
{
  "934": 7,
  "935": 3
}
```

The state file should not be writable by arbitrary users.

The Worker uses atomic file replacement when updating JSON state.

Conceptually:

```text
Write temporary file
       │
       ▼
Flush data
       │
       ▼
Atomic replace
       │
       ▼
State file
```

This reduces the likelihood of leaving a partially written JSON file after an interruption.

---

## Running State Protection

The persistent Running State should also be protected from unauthorized modification.

Unauthorized changes could potentially cause:

* Duplicate execution
* Incorrect execution status
* An Issue to remain blocked
* An active execution to be incorrectly considered finished

Therefore, access to the state directory should be restricted to the Worker service account and authorized administrators.

---

## Audit Logging

Security-relevant lifecycle events are written as structured JSONL records.

Examples include:

```text
AUTHORIZATION_PASSED
AUTHORIZATION_FAILED
VALIDATION_PASSED
VALIDATION_FAILED
STATE_UPDATED
ANSIBLE_EXECUTION_STARTED
ANSIBLE_EXECUTION_SUCCEEDED
ANSIBLE_EXECUTION_FAILED
AUTOMATION_REQUEST_TIMEOUT
AUTOMATION_REQUEST_SKIPPED_ALREADY_RUNNING
AUTOMATION_REQUEST_SKIPPED_ALREADY_PROCESSED
```

These events provide evidence of:

* Who requested execution
* Which Issue was processed
* Which Version was used
* Which Playbook was selected
* Which targets were requested
* Whether authorization passed
* Whether validation passed
* Whether Ansible succeeded or failed
* When the execution occurred

---

## Centralized Logging

The Worker can forward its JSONL audit logs to a centralized SIEM or log-management platform.

Example:

```text
Worker
  │
  ▼
JSONL Audit Log
  │
  ▼
Log Forwarder
  │
  ▼
SIEM / Splunk
```

Centralized logging enables:

* Security monitoring
* Alerting
* Dashboards
* Long-term retention
* Cross-system correlation

The Worker is responsible for generating audit events. Alerting and dashboard implementation can be handled by the organization's SIEM/Monitoring platform.

---

## Network Security Requirements

The Worker requires network access to:

### GitLab

```text
HTTPS / TCP 443
```

### Managed Linux Hosts

Typically:

```text
SSH / TCP 22
```

### Managed Windows Hosts

Typically:

```text
WinRM / TCP 5985
WinRM over HTTPS / TCP 5986
```

Only the required destinations and ports should be permitted by network security controls.

---

## Threat Considerations

The following threats should be considered during deployment:

### Unauthorized User

**Threat:** An unauthorized GitLab user attempts to execute automation.

**Control:**

* Authorized user list
* Last modifier authorization
* Audit logging

---

### Unauthorized Playbook

**Threat:** A user attempts to execute an unapproved Playbook.

**Control:**

* Explicit Playbook allowlist
* Controlled Playbook paths
* Request validation

---

### Replay of Previous Request

**Threat:** A previously processed Version is reused.

**Control:**

* Persistent Version State
* Sequential Version validation

---

### Duplicate Execution

**Threat:** Multiple Worker polling cycles start the same Automation Request.

**Control:**

* Persistent Running State
* Background execution tracking

---

### Credential Exposure

**Threat:** GitLab API credentials are exposed through source code or Git history.

**Control:**

* Runtime environment configuration
* Secret separation
* `.gitignore`
* Restricted configuration file permissions

---

### State Corruption

**Threat:** Worker interruption during state update causes invalid state.

**Control:**

* Atomic state file replacement
* Restricted state file access

---

### Worker Compromise

If the Worker host is compromised, an attacker may potentially gain access to the automation capabilities available to the Worker.

Therefore, the Worker host should be protected using normal infrastructure security controls, including:

* OS hardening
* Patch management
* Endpoint
