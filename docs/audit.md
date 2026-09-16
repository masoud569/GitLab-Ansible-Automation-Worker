# Audit Logging

## Overview

The GitLab-Ansible-Automation-Worker maintains structured audit information for Automation Requests.

The purpose of audit logging is to provide a complete and traceable record of the Automation Request lifecycle, including:

* Who submitted the request
* Which GitLab Issue was processed
* Which Automation Version was processed
* Which Playbook was requested
* Which targets were selected
* Which authorization and validation checks were performed
* When processing started and finished
* Whether Ansible execution succeeded or failed
* What result was returned to GitLab
* Which Worker processed the request

Audit information is intended for operational troubleshooting, security investigation, compliance, and SIEM integration.

---

## Audit Log Format

The Worker writes audit events in **JSON Lines (JSONL)** format.

Each line represents one independent event.

Example:

```json
{"timestamp":"2026-09-16T06:20:00+00:00","event":"AUTOMATION_REQUEST_ACCEPTED","issue_iid":934,"version":7}
```

JSONL provides several advantages:

* Machine-readable structure
* Easy ingestion into SIEM platforms
* One event per line
* Simple log rotation
* Easy troubleshooting from command line
* No dependency on a database

---

## Audit Log Location

The local audit log is stored under the Worker log directory.

Example:

```text
/opt/gitlab-ansible-automation-worker/logs/automation-worker.jsonl
```

The exact location can be changed according to the deployment environment.

The log file should not be considered the authoritative long-term storage location.

For centralized monitoring environments, the Worker log should be forwarded to the organization's SIEM or logging platform.

---

## Event Lifecycle

A typical successful Automation Request produces a sequence similar to:

```text
AUTOMATION_REQUEST_ACCEPTED
        │
        ▼
AUTOMATION_VERSION_CONSUMED
        │
        ▼
AUTHORIZATION_SUCCEEDED
        │
        ▼
VALIDATION_SUCCEEDED
        │
        ▼
AUTOMATION_STARTED
        │
        ▼
ANSIBLE_EXECUTION_STARTED
        │
        ▼
ANSIBLE_EXECUTION_SUCCEEDED
        │
        ▼
GITLAB_EXECUTION_RESULT_POSTED
        │
        ▼
AUTOMATION_REQUEST_COMPLETED
        │
        ▼
AUTOMATION_REQUEST_FINISHED
```

The exact event sequence may vary depending on the result of processing.

---

## Important Audit Events

### Request Detection

Records that an Automation Request was identified during GitLab polling.

Example:

```json
{
  "event": "AUTOMATION_REQUEST_ACCEPTED",
  "issue_iid": 934,
  "version": 7
}
```

---

### Version Consumption

Records that a valid Version was consumed.

Example:

```json
{
  "event": "AUTOMATION_VERSION_CONSUMED",
  "issue_iid": 934,
  "version": 7
}
```

This event is important because a Version remains consumed even if later authorization, validation, or Ansible execution fails.

---

### Authorization Result

Records the result of the authorization check.

Success:

```json
{
  "event": "AUTHORIZATION_SUCCEEDED",
  "issue_iid": 934,
  "version": 7
}
```

Failure:

```json
{
  "event": "AUTHORIZATION_FAILED",
  "issue_iid": 934,
  "version": 7
}
```

Sensitive authorization information should not be written to the log unless explicitly required.

---

### Validation Result

Records whether the Automation Request passed validation.

Example:

```json
{
  "event": "VALIDATION_SUCCEEDED",
  "issue_iid": 934,
  "version": 7
}
```

For failures, the Worker should record a safe and useful reason without exposing secrets.

---

### Automation Start

Records the beginning of actual automation execution.

Example:

```json
{
  "event": "AUTOMATION_STARTED",
  "issue_iid": 934,
  "version": 7,
  "playbook": "endpoint_agent",
  "target_count": 10
}
```

---

### Ansible Execution

The Worker records the start and result of the Ansible process.

Example:

```json
{
  "event": "ANSIBLE_EXECUTION_STARTED",
  "issue_iid": 934,
  "version": 7
}
```

Successful execution:

```json
{
  "event": "ANSIBLE_EXECUTION_SUCCEEDED",
  "issue_iid": 934,
  "version": 7
}
```

Failed execution:

```json
{
  "event": "ANSIBLE_EXECUTION_FAILED",
  "issue_iid": 934,
  "version": 7
}
```

---

## GitLab Result Posting

After automation execution, the Worker posts the result back to the corresponding GitLab Issue.

Example:

```json
{
  "event": "GITLAB_EXECUTION_RESULT_POSTED",
  "issue_iid": 934,
  "version": 7
}
```

This allows the audit trail to distinguish between:

1. Automation execution
2. Result publication

These are separate operations and may fail independently.

---

## Request Completion

The Worker records final processing state.

Example:

```json
{
  "event": "AUTOMATION_REQUEST_COMPLETED",
  "issue_iid": 934,
  "version": 7,
  "result": "success"
}
```

For a failed execution:

```json
{
  "event": "AUTOMATION_REQUEST_COMPLETED",
  "issue_iid": 934,
  "version": 7,
  "result": "failed"
}
```

---

## Request Finished

The final lifecycle event indicates that Worker processing for the request has ended.

Example:

```json
{
  "event": "AUTOMATION_REQUEST_FINISHED",
  "issue_iid": 934,
  "version": 7
}
```

---

## Duplicate Execution Events

When an Issue/Version is already running, the Worker does not start another Ansible process.

Instead, it records a skip event.

Example:

```json
{
  "event": "AUTOMATION_REQUEST_SKIPPED_ALREADY_RUNNING",
  "issue_iid": 934,
  "version": 7,
  "pid": 816541
}
```

This is particularly important for long-running automation.

It demonstrates that the Worker continued polling while the existing automation was still executing.

---

## Already Processed Requests

If a previously consumed Version is encountered again, the Worker skips the request.

Example:

```json
{
  "event": "AUTOMATION_REQUEST_SKIPPED_ALREADY_PROCESSED",
  "issue_iid": 934,
  "version": 7
}
```

This event is useful for detecting duplicate requests or repeated GitLab polling results.

---

## Invalid Version Sequence

If the submitted Version does not match the expected next Version, the Worker rejects the request without consuming the Version.

Example:

```json
{
  "event": "AUTOMATION_REQUEST_REJECTED_INVALID_VERSION",
  "issue_iid": 934,
  "version": 10,
  "expected_version": 8
}
```

This is different from a consumed Version that later fails authorization or validation.

---

## Traceability

Every important event should contain enough information to correlate events belonging to the same request.

The primary correlation attributes are:

```text
GitLab Project
GitLab Issue IID
Automation Version
```

Example:

```text
Project: automation
Issue:   934
Version: 7
```

A SIEM can therefore reconstruct the complete lifecycle of Version 7.

---

## Recommended Audit Fields

Depending on the event, the Worker may record fields such as:

```text
timestamp
event
project_id
issue_iid
version
requester
playbook
target_count
result
duration
pid
worker
error
```

Not every field is required for every event.

The Worker should avoid unnecessarily logging:

* Access tokens
* Passwords
* Private keys
* API credentials
* Sensitive request content
* Secrets passed to Ansible
* Confidential target data

---

## Error Logging

Errors should be recorded in structured form.

Example:

```json
{
  "event": "ANSIBLE_EXECUTION_FAILED",
  "issue_iid": 934,
  "version": 7,
  "result": "failed",
  "error": "Ansible exited with non-zero return code"
}
```

Error messages should provide enough information for troubleshooting without exposing credentials or sensitive information.

---

## Log Rotation

The Worker audit log is expected to be rotated to prevent uncontrolled disk consumption.

A typical deployment may use:

```text
daily
rotate 90
compress
delaycompress
missingok
notifempty
```

The exact retention period should be determined by the organization's logging and compliance requirements.

---

## SIEM Integration

The Worker audit log can be forwarded to a centralized SIEM.

For example:

```text
Automation Worker
       │
       │ JSONL
       ▼
Splunk Universal Forwarder
       │
       ▼
Splunk
       │
       ├── Dashboards
       ├── Alerts
       ├── Search
       └── SOC Investigation
```

The Worker itself should remain focused on automation.

Detection and correlation logic should be implemented in the organization's centralized monitoring/SIEM layer.

---

## Security Monitoring

The following audit events are particularly useful for security monitoring:

```text
AUTHORIZATION_FAILED
AUTOMATION_REQUEST_REJECTED_INVALID_VERSION
AUTOMATION_REQUEST_SKIPPED_ALREADY_PROCESSED
AUTOMATION_REQUEST_SKIPPED_ALREADY_RUNNING
ANSIBLE_EXECUTION_FAILED
```

Repeated authorization failures or unexpected execution failures can be investigated by the security or operations teams.

The Worker should provide the events; the SIEM/SOC layer determines the appropriate alerting and response.

---

## Audit Integrity

Audit logs should be protected against unauthorized modification.

Recommended controls include:

* Restricted filesystem permissions
* Dedicated Worker service account
* Centralized log forwarding
* Log retention controls
* SIEM access control
* Monitoring of Worker log forwarding health

For higher-assurance environments, centralized logging should be treated as the primary long-term audit repository.

---

## Audit and State Separation

State and Audit Logs serve different purposes.

### State

Answers:

> What is the latest consumed Version for this Issue?

Example:

```json
{
  "934": 7
}
```

### Audit Log

Answers:

> What happened during each processing attempt?

Example:

```text
Version 1 → Validation Failed
Version 2 → Authorization Failed
Version 3 → Ansible Failed
Version 4 → Success
Version 5 → Ansible Failed
Version 6 → Timeout
Version 7 → Success
```

The Worker therefore does not need to store the entire execution history in its state file.

---

## Audit Reconstruction

Given a GitLab Issue and Version, an operator should be able to correlate:

```text
GitLab Issue
     │
     ├── Requester
     ├── Automation Version
     ├── Requested Playbook
     └── Requested Targets
              │
              ▼
       Worker Audit Events
              │
              ├── Authorization
              ├── Validation
              ├── Execution Start
              ├── Ansible Result
              └── Completion
              │
              ▼
       GitLab Result Comment
```

This provides end-to-end traceability from request submission to execution result.

---

## Operational Use Cases

The audit trail supports several operational scenarios.

### Troubleshooting

An administrator can determine where processing failed:

```text
Request
  ↓
Authorization
  ↓
Validation
  ↓
Ansible
  ↓
GitLab Result
```

---

### Security Investigation

Security teams can investigate:

* Unauthorized requests
* Repeated authorization failures
* Unexpected Playbook execution
* Repeated execution failures
* Duplicate execution attempts

---

### Compliance

The audit trail can provide evidence that:

* Requests were processed through defined controls
* Authorization was checked
* Approved Playbooks were used
* Execution results were recorded
* Processing timestamps were captured

---

### Operational Reporting

Centralized audit events can be used to build dashboards showing:

```text
Total Requests
Successful Executions
Failed Executions
Authorization Failures
Validation Failures
Average Execution Duration
Currently Running Automations
```

---

## Design Principles

The audit design follows these principles:

1. Every important lifecycle transition produces a structured event.
2. Events are machine-readable.
3. Events can be correlated using Issue IID and Automation Version.
4. State and audit history are kept separate.
5. Secrets and credentials are never written to audit logs.
6. Local logs are protected and rotated.
7. Centralized SIEM forwarding is supported.
8. Audit data is used for troubleshooting, security monitoring, and operational visibility.
9. The Worker records events but does not replace the organization's SIEM.
10. The audit trail should allow an operator to reconstruct the lifecycle of an Automation Request.
