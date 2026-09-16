# State Management

## Overview

GitLab-Ansible-Automation-Worker uses a persistent state mechanism to prevent duplicate processing of Automation Requests and to maintain the processing history of each GitLab Issue.

The state is intentionally lightweight and is stored in a local JSON file.

The core concept is:

> **One Automation Version represents one processing attempt.**

A Version is consumed whenever the Worker accepts and processes that attempt, regardless of the final outcome.

---

## Automation Version

The `Automation Version` is a sequential number associated with an Automation Request.

Example:

```text id="u7z4kd"
Version 1
Version 2
Version 3
Version 4
```

Every new processing attempt must use the next sequential Version.

For example:

```text id="9ixv6b"
Version 1 → Validation Failed
Version 2 → Authorization Failed
Version 3 → Ansible Execution Failed
Version 4 → Success
```

All four Versions are considered consumed.

---

## Why Version Is Required

The Version mechanism provides a simple way to distinguish individual processing attempts.

Without Version control, the Worker could repeatedly process the same Issue because GitLab Issues remain available during the polling lookback period.

Version control prevents this behavior.

It also provides a simple audit history:

```text id="h0l2au"
Issue #934

Version 1 → Attempt 1
Version 2 → Attempt 2
Version 3 → Attempt 3
Version 4 → Attempt 4
```

---

## Version Consumption

A Version is consumed after it passes the basic Version Sequence check and the Worker reserves it for processing.

The processing flow is:

```text id="2g7mce"
GitLab Issue
     │
     ▼
Read Automation Version
     │
     ▼
Check Version Sequence
     │
     ├── Invalid → Reject / Do Not Consume
     │
     ▼
Reserve Version
     │
     ▼
Authorization
     │
     ▼
Validation
     │
     ▼
Ansible Execution
```

Once the Version has been reserved, it is considered consumed.

---

## Processing Outcomes

A consumed Version remains consumed regardless of the result.

### Authorization Failure

```text id="q8k3tb"
Version 5
    ↓
Authorization Failed
    ↓
Version 5 consumed
```

### Validation Failure

```text id="5v2jrx"
Version 6
    ↓
Validation Failed
    ↓
Version 6 consumed
```

### Ansible Failure

```text id="d9p6sf"
Version 7
    ↓
Ansible Execution Failed
    ↓
Version 7 consumed
```

### Timeout

```text id="t1s0gq"
Version 8
    ↓
Automation Timeout
    ↓
Version 8 consumed
```

### Success

```text id="z7u4se"
Version 9
    ↓
Ansible Execution Succeeded
    ↓
Version 9 consumed
```

---

## State File

The Worker stores processing state in a local JSON file.

Example:

```json id="cbj9tu"
{
  "934": 7,
  "935": 3
}
```

The key represents the GitLab Issue IID.

The value represents the latest consumed Automation Version.

For example:

```text id="wj6k2e"
Issue 934 → Version 7 consumed
Issue 935 → Version 3 consumed
```

---

## Version Sequence

The next valid Version must be exactly one greater than the latest consumed Version.

Example:

```text id="z1nx8r"
Last processed: 7
Expected next:  8
```

Therefore:

```text id="m1d9jv"
Version 8 → Valid
Version 9 → Invalid
Version 7 → Invalid
Version 6 → Invalid
```

This prevents Version gaps and reuse.

---

## Invalid Version

An invalid Version does not consume a new Version.

Example:

```text id="4w5kxa"
Last processed Version: 7

Requested Version: 10

Expected Version: 8
```

The request is rejected because Version `10` skips Versions `8` and `9`.

The user must submit the next expected Version.

---

## Version Reuse

Previously consumed Versions cannot be reused.

Example:

```text id="g9r0xq"
Last processed Version: 7

Requested Version: 7
```

The Worker does not process the request again.

This prevents accidental or intentional replay of a previously processed Automation Request.

---

## State Update Timing

The Version is recorded before the actual Ansible execution begins.

This is intentional.

```text id="r4v6e2"
Version Validation
       │
       ▼
State Update
       │
       ▼
Authorization
       │
       ▼
Validation
       │
       ▼
Ansible Execution
```

This guarantees that an Authorization or Validation failure also consumes the Version.

It also prevents the same Version from being redispatched during subsequent Worker polling cycles.

---

## Example: Validation Failure

Initial state:

```json id="1f5v3d"
{
  "934": 1
}
```

User submits:

```text id="d4j2q0"
Automation Version: 2
```

Worker validates the Version:

```text
2 = 1 + 1
```

State becomes:

```json id="j0k6qf"
{
  "934": 2
}
```

Validation then fails.

The next polling cycle sees:

```text
Requested Version: 2
Last consumed Version: 2
```

Therefore the request is skipped.

The user must increment the Version for another attempt:

```text id="9sk5r1"
Automation Version: 3
```

---

## Example: Ansible Failure

Initial state:

```json id="q8y0h4"
{
  "934": 2
}
```

User submits Version 3.

State is updated:

```json id="r3j2w7"
{
  "934": 3
}
```

Ansible then fails.

Version 3 remains consumed.

The next attempt must use:

```text id="x4r9vs"
Automation Version: 4
```

---

## Example: Successful Execution

Initial state:

```json id="z8f2ce"
{
  "934": 3
}
```

User submits Version 4.

The Worker consumes Version 4 and executes the Playbook successfully.

Final state:

```json id="2q9v6s"
{
  "934": 4
}
```

The next attempt must use Version 5.

---

## Persistent Running State

Version State and Running State serve different purposes.

### Version State

Answers:

> Has this Version already been consumed?

Example:

```json id="p7h4z2"
{
  "934": 7
}
```

### Running State

Answers:

> Is this Issue/Version currently executing?

Example:

```json id="b4m8cx"
{
  "934": {
    "version": 7,
    "pid": 816541,
    "started_at": "2026-09-16T06:21:00+00:00"
  }
}
```

Both mechanisms are required.

---

## Duplicate Execution Prevention

The Worker checks the persistent Running State before starting a new execution.

```text id="n0z5bh"
Issue 934 / Version 7
        │
        ▼
Running State?
        │
        ├── YES → Skip
        │
        └── NO → Start execution
```

This is particularly important for long-running Playbooks.

---

## Worker Restart

The Running State is persistent.

If the Worker service restarts while an automation is running, the Worker can inspect the recorded process ID and determine whether the process still exists.

Stale entries are removed when the recorded process is no longer alive.

This prevents an abandoned Running State from permanently blocking the Issue.

---

## Atomic State Updates

State updates are written using an atomic file replacement process.

Conceptually:

```text id="x3c9nv"
Create temporary state file
          │
          ▼
Write complete JSON
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

This reduces the risk of leaving a partially written JSON file if the Worker is interrupted during an update.

---

## State File Protection

The state directory should be accessible only to the Automation Worker service account and authorized administrators.

Unauthorized modification of the state can result in:

* Duplicate execution
* Incorrect Version tracking
* Skipped automation attempts
* Incorrect audit information

Therefore, filesystem permissions are an important part of the overall security model.

---

## Audit Relationship

State represents the latest consumed Version.

The audit log contains the detailed history of what happened during each Version.

For example:

```text id="h9s5vl"
State:

Issue 934 → Version 7
```

Audit log:

```text
Version 1 → Validation Failed
Version 2 → Authorization Failed
Version 3 → Ansible Failed
Version 4 → Validation Failed
Version 5 → Ansible Failed
Version 6 → Timeout
Version 7 → Success
```

This separation keeps the state mechanism simple while preserving detailed execution history in the audit log.

---

## Design Summary

The State Management model follows these rules:

1. Each GitLab Issue has a sequential Automation Version.
2. A Version represents one processing attempt.
3. Every accepted processing attempt consumes exactly one Version.
4. Authorization Failure consumes the Version.
5. Validation Failure consumes the Version.
6. Ansible Failure consumes the Version.
7. Timeout consumes the Version.
8. Successful execution consumes the Version.
9. Invalid Version sequences do not consume a Version.
10. Previously consumed Versions cannot be reused.
11. The next Version must be exactly one greater than the latest consumed Version.
12. Running State separately
