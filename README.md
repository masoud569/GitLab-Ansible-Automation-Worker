# Enterprise Infrastructure Automation Worker

A secure, GitLab-driven infrastructure automation framework built around Ansible.

This project provides a lightweight automation worker that receives infrastructure automation requests through GitLab Issues, validates and authorizes them, executes approved Ansible Playbooks, tracks execution attempts, and maintains an auditable execution history.

The architecture is designed around a **Pull Model**, where the Automation Worker initiates communication with GitLab. GitLab does not require inbound connectivity to the Worker.

---

## Architecture

```text
                    ┌──────────────────────┐
                    │       GitLab         │
                    │                      │
                    │  Automation Request  │
                    │       (Issue)         │
                    └──────────┬───────────┘
                               │
                         HTTPS / API
                               │
                               ▼
                    ┌──────────────────────┐
                    │  Automation Worker   │
                    │                      │
                    │  Pull Model          │
                    │  Authorization       │
                    │  Validation          │
                    │  Version Control     │
                    │  Audit Logging       │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │       Ansible        │
                    │                      │
                    │   Approved Playbooks │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │ Infrastructure       │
                    │ Windows / Linux      │
                    └──────────────────────┘

                         │
                         ▼
                    ┌──────────────┐
                    │     SIEM     │
                    │ Audit / Logs │
                    └──────────────┘
```

---

## Key Features

* GitLab Issue-based automation requests
* Secure Pull-based architecture
* No inbound connection required to the Automation Worker
* Ansible-based infrastructure automation
* Playbook allowlist
* User authorization
* Request validation
* Version-based execution control
* One-time processing per Automation Version
* Duplicate execution prevention
* Persistent running-state tracking
* Support for long-running automation
* Background execution without blocking GitLab polling
* Execution result reporting back to GitLab
* Structured JSONL audit logging
* Splunk-compatible logging
* Local log rotation
* Systemd service integration
* Failure and timeout handling
* Atomic state management

---

## Automation Request Lifecycle

An automation request follows this lifecycle:

```text
GitLab Issue
     │
     ▼
Worker Poll
     │
     ▼
Request Detection
     │
     ▼
Version Validation
     │
     ▼
Attempt Reservation
     │
     ▼
Authorization
     │
     ▼
Request Validation
     │
     ▼
Ansible Execution
     │
     ├───────────────┐
     │               │
     ▼               ▼
  SUCCESS          FAILURE
     │               │
     └───────┬───────┘
             ▼
       Result Reporting
             │
             ▼
          GitLab
```

---

## Automation Version

The `Automation Version` represents the **request attempt / revision number**.

Every new processing attempt must use the next sequential version.

Example:

```text
Version 1 → Validation Failed
Version 2 → Authorization Failed
Version 3 → Ansible Failed
Version 4 → Success
```

Each processed version is consumed regardless of the execution result.

A previously processed version cannot be reused.

This provides both:

* duplicate execution prevention
* a simple audit trail of automation request attempts

---

## Example Automation Request

A GitLab Issue can contain:

```text
[x] execute automation

Automation Version: 1

Playbook: install_application

Targets: server01

Environment: production
```

The Worker detects the request, validates the parameters, authorizes the request, and executes the corresponding approved Ansible Playbook.

---

## Playbook Security

Only explicitly approved Playbooks can be executed.

Example:

```yaml
allowed_playbooks:
  - install_application
  - hardenin_shell
```

The Worker does not execute arbitrary Playbook paths supplied by the user.

---

## Authorization

Automation requests can be restricted to explicitly authorized GitLab users.

Example:

```yaml
authorized_users:
  - gitlab_user_id: 1001
    username: automation-admin
    enabled: true
```

The authorization model is based on the user who most recently modified the Automation Request.

---

## State Management

The Worker maintains persistent state to prevent duplicate processing.

Example:

```json
{
  "25": 7,
  "26": 3
}
```

This means:

```text
Issue 25 → Version 7 has been consumed
Issue 26 → Version 3 has been consumed
```

The state file is updated atomically to reduce the risk of corruption.

---

## Long-Running Automation

Long-running Ansible executions are handled by a detached background process.

The main Worker continues polling GitLab while the automation is running.

```text
Main Worker
    │
    ├── Poll GitLab
    │
    ├── Detect Request
    │
    ├── Start Background Execution
    │
    ├── Continue Polling
    │
    └── Prevent Duplicate Execution

Background Process
    │
    └── Ansible Playbook
```

A persistent running-state file is used to prevent the same Issue/Version from being executed concurrently.

---

## Audit Logging

Automation events are written as JSON Lines.

Example:

```json
{
  "timestamp": "2026-01-01T10:00:00+00:00",
  "level": "INFO",
  "event": "ANSIBLE_EXECUTION_SUCCEEDED",
  "issue": "25",
  "execution_id": "25:7",
  "version": 7,
  "exit_code": 0
}
```

The structured format is suitable for ingestion into SIEM and log-management platforms such as Splunk.

---

## Security Principles

This project follows several security principles:

1. **Pull Model**

   * The Worker initiates communication with GitLab.
   * GitLab does not require inbound connectivity to the Worker.

2. **Explicit Playbook Allowlist**

   * Only approved Playbooks can be executed.

3. **Authorization**

   * Automation execution is restricted to authorized users.

4. **Version Control**

   * Every processing attempt consumes a unique Version.

5. **Duplicate Prevention**

   * Previously processed Versions cannot be executed again.

6. **Persistent Running State**

   * Concurrent execution of the same Automation Request is prevented.

7. **Auditability**

   * Important lifecycle events are recorded in structured logs.

8. **Least Privilege**

   * The Worker runs as a dedicated operating-system user.

9. **Secrets Separation**

   * Authentication credentials are provided through environment/configuration mechanisms rather than hard-coded in source code.

---

## Repository Structure

```text
enterprise-infrastructure-automation-worker/
│
├── README.md
├── LICENSE
├── .gitignore
│
├── worker/
│   ├── automation_worker.py
│   └── automation_request.py
│
├── config/
│   ├── allowed_playbooks.example.yml
│   └── authorized_users.example.yml
│
├── templates/
│   └── Automation_Request.md
│
├── systemd/
│   └── automation-worker.service
│
├── examples/
│   └── automation_test_long.yml
│
└── docs/
    ├── architecture.md
    ├── security.md
    ├── state-management.md
    ├── audit.md
    └── deployment.md
```

---

## Requirements

The reference implementation requires:

* Linux
* Python 3
* Ansible
* GitLab
* GitLab API access
* Network connectivity from the Worker to GitLab
* Network connectivity from the Worker to managed hosts

Optional:

* centralized log-management/SIEM platform
* systemd

---

## Important Note

This repository is a **generic reference implementation**.

Production environments should adapt:

* authentication mechanisms
* authorization policies
* inventory management
* secrets management
* logging and retention
* network controls
* Playbook allowlists
* operating-system permissions

Do not commit production credentials, tokens, private keys, internal hostnames, IP addresses, inventories, or other confidential infrastructure information to this repository.

---

## Project Status

The reference architecture has been validated against:

* Functional execution
* Authorization controls
* Request validation
* Failure handling
* Version management
* Duplicate execution prevention
* Long-running automation
* Audit logging
* Monitoring integration

The project is intended as a foundation for further extensions such as:

* Terraform / IaC integration
* Additional automation engines
* CMDB integration
* Secrets Management integration
* CI/CD integration
* Additional approval workflows
* Expanded policy enforcement
