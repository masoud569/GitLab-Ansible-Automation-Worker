# Architecture

## Overview

GitLab-Ansible-Automation-Worker is designed as a lightweight infrastructure automation platform using a **GitLab-driven Pull Model**.

Automation requests are created and maintained as GitLab Issues. The Automation Worker periodically polls GitLab, identifies eligible requests, validates and authorizes them, and executes approved Ansible Playbooks.

GitLab does not initiate an inbound connection to the Automation Worker.

---

## High-Level Architecture

```text
┌──────────────────────────────┐
│            GitLab            │
│                              │
│  Automation Request (Issue)  │
└──────────────┬───────────────┘
               │
               │ HTTPS / GitLab API
               │
               ▼
┌──────────────────────────────┐
│     GitLab Ansible Worker    │
│                              │
│  • Poll GitLab               │
│  • Detect Automation Request │
│  • Authorization             │
│  • Validation                │
│  • Version Control           │
│  • Running State             │
│  • Audit Logging             │
└──────────────┬───────────────┘
               │
               │ Local Ansible Execution
               ▼
┌──────────────────────────────┐
│            Ansible           │
│                              │
│      Approved Playbooks      │
└──────────────┬───────────────┘
               │
        SSH / WinRM
               │
        ┌──────┴──────┐
        ▼             ▼
┌────────────┐  ┌────────────┐
│   Linux    │  │  Windows   │
│   Hosts    │  │   Hosts    │
└────────────┘  └────────────┘

               │
               ▼
        ┌──────────────┐
        │    Splunk    │
        │              │
        │ Audit / Logs │
        └──────────────┘
```

---

## Pull Model

The Worker is responsible for initiating communication with GitLab.

```text
GitLab
   ▲
   │
   │ HTTPS / API
   │
   │
Worker
```

There is no requirement for GitLab to establish an inbound connection to the Worker.

This model can simplify network security because the Worker only requires outbound connectivity to the GitLab API.

---

## Components

### GitLab

GitLab provides:

* Automation Request interface
* Issue lifecycle
* User identity
* Request modification history
* Automation result reporting
* Audit trail for the request

An Issue represents an Automation Request.

---

### Automation Worker

The Worker is the central processing component.

Responsibilities include:

* Polling GitLab
* Detecting Automation Requests
* Reading request parameters
* Checking Automation Version
* Preventing duplicate processing
* Tracking running executions
* Starting automation requests
* Maintaining persistent state
* Writing structured audit logs

The Worker does not execute arbitrary commands supplied by the user.

---

### Automation Request Processor

`automation_request.py` handles the processing of an individual Automation Request.

Its responsibilities include:

* Reading the GitLab Issue
* Identifying the last modifier
* Authorization
* Request parsing
* Playbook validation
* Target validation
* Ansible execution
* Result reporting
* State management

---

### Ansible

Ansible is used as the automation engine.

The Worker executes approved Playbooks against managed infrastructure.

Typical connectivity:

```text
Linux    → SSH
Windows  → WinRM
```

The exact authentication mechanism depends on the target environment.

---

## Automation Request

An Automation Request is represented by a GitLab Issue containing the required fields.

Example:

```text
[x] execute automation

Automation Version: 1

Playbook: automation_test_pass

Targets: server01

Environment: production
```

The Worker detects the request during its polling cycle.

---

## Request Lifecycle

```text
1. User creates/updates GitLab Issue
                │
                ▼
2. Worker polls GitLab
                │
                ▼
3. Worker detects Automation Request
                │
                ▼
4. Version sequence validation
                │
                ▼
5. Version is reserved/consumed
                │
                ▼
6. Authorization
                │
                ▼
7. Request validation
                │
                ▼
8. Ansible execution
                │
        ┌───────┴────────┐
        ▼                ▼
     Success           Failure
        │                │
        └───────┬────────┘
                ▼
9. Result recorded
                │
                ▼
10. Result posted to GitLab
```

---

## Version / Attempt Control

The Automation Version represents the processing attempt of an Automation Request.

Each processing attempt must use the next sequential Version.

For example:

```text
Version 1 → Validation Failed
Version 2 → Authorization Failed
Version 3 → Ansible Failed
Version 4 → Success
```

Every valid processing attempt consumes its Version regardless of the result.

Previously processed Versions cannot be reused.

This mechanism provides:

* Duplicate execution prevention
* Sequential request tracking
* Simple auditability
* Clear request revision history

---

## Persistent Running State

The Worker maintains a persistent running-state file.

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

When a long-running automation is executing, subsequent polling cycles detect the running state.

```text
Poll
 │
 ├── Issue 934 / Version 7
 │
 └── Already Running
          │
          ▼
       Skip
```

This prevents duplicate execution of the same Issue/Version.

---

## Background Execution

Long-running Automation Requests are executed by a detached background process.

The main Worker does not wait for the Ansible process to finish.

```text
                    ┌─────────────────────┐
                    │    Main Worker      │
                    │                     │
                    │ GitLab Polling      │
                    └──────────┬──────────┘
                               │
                               │ Start
                               ▼
                    ┌─────────────────────┐
                    │ Background Process  │
                    │                     │
                    │ automation_request  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Ansible Playbook     │
                    │                     │
                    │ Long Running Job     │
                    └─────────────────────┘
```

While the automation is running, the Worker continues its normal polling cycle.

---

## State Management

The Worker maintains persistent processing state.

The state records the latest consumed Version for each Issue.

Example:

```json
{
  "934": 7,
  "935": 3
}
```

State updates are written atomically to minimize the risk of partial or corrupted state files.

---

## Audit Logging

The Worker generates structured JSONL audit events.

Important lifecycle events include:

```text
WORKER_STARTED
POLL_COMPLETED
AUTOMATION_REQUEST_DETECTED_BY_WORKER
AUTOMATION_REQUEST_DISPATCHED
AUTOMATION_REQUEST_STARTED
AUTHORIZATION_PASSED
AUTHORIZATION_FAILED
VALIDATION_STARTED
VALIDATION_PASSED
VALIDATION_FAILED
STATE_UPDATED
ANSIBLE_EXECUTION_STARTED
ANSIBLE_EXECUTION_SUCCEEDED
ANSIBLE_EXECUTION_FAILED
AUTOMATION_REQUEST_COMPLETED
AUTOMATION_REQUEST_FAILED
AUTOMATION_REQUEST_TIMEOUT
GITLAB_EXECUTION_RESULT_POSTED
AUTOMATION_REQUEST_SKIPPED_ALREADY_PROCESSED
AUTOMATION_REQUEST_SKIPPED_ALREADY_RUNNING
```

These events provide an end-to-end execution trail.

---

## Logging and SIEM Integration

The Worker writes audit events locally as JSONL.

The log files can be collected by a log forwarder and sent to a centralized SIEM or log-management platform.

Example:

```text
Automation Worker
       │
       ▼
JSONL Audit Log
       │
       ▼
Log Forwarder
       │
       ▼
Splunk / SIEM
```

The Worker itself is responsible for generating structured events. Alerting, dashboards, and centralized retention can be handled by the organization's monitoring/SIEM platform.

---

## Network Security Model

The intended communication model is:

```text
                 HTTPS
GitLab ◄──────────────────── Worker
                               │
                               │
                         SSH / WinRM
                               │
                               ▼
                         Managed Hosts
```

The Worker requires outbound access to GitLab and connectivity to managed infrastructure.

No inbound GitLab-to-Worker connection is required.

---

## Security Boundaries

The following controls are implemented at the Worker layer:

* Explicit Playbook allowlist
* Authorized user list
* Version sequencing
* Duplicate execution prevention
* Persistent running-state tracking
* Dedicated service account
* Systemd service isolation
* Structured audit logging
* Separation of secrets from source code

Additional security controls such as network segmentation, firewall policy, centralized secrets management, and SIEM alerting should be implemented according to the target environment.

---

## Extensibility

The architecture can be extended beyond Ansible.

Potential future automation engines include:

```text
GitLab
   │
   ▼
Automation Worker
   │
   ├── Ansible
   ├── Terraform / IaC
   ├── Scripts
   └── API-based Automation
```

The Worker can therefore act as an automation orchestration layer rather than being permanently coupled to a single automation engine.

---

## Design Principles

The architecture is based on the following principles:

1. **Pull instead of Push**
2. **Explicit authorization**
3. **Explicit Playbook allowlisting**
4. **One Version per processing attempt**
5. **No duplicate execution**
6. **Non-blocking long-running execution**
7. **Persistent state**
8. **Structured auditability**
9. **Separation of secrets and source code**
10. **Extensibility for additional automation engines**
