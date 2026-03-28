---
name: agent-teams
description: Multi-agent collaboration with persistent teammates and message bus
trigger: when coordinating multiple agents or delegating tasks to teammates
---

# Agent Teams Skill

Use when you need to coordinate multiple agents working together on a task.

## Architecture

```
.team/
  config.json           <- 团队名册 + 状态
  inbox/
    {name}.jsonl        <- append-only, drain-on-read
```

## Tools

| Tool | Description |
|------|-------------|
| spawn | Create a new teammate with name/role/prompt |
| send | Send message to a teammate's inbox |
| read_inbox | Read and clear your inbox |

## Usage

### 1. Spawn a Teammate

```
spawn(name="alice", role="coder", prompt="You are a Python developer. Focus on writing clean code.")
```

### 2. Send Message

```
send(to="alice", content="Please implement the login feature")
```

### 3. Check Inbox

```
read_inbox()  # Returns all pending messages
```

## Communication Flow

```
lead (you) --send--> alice.jsonl
alice reads inbox, processes, responds
alice --send--> lead.jsonl
lead reads inbox
```

## Lifecycle

```
spawn -> WORKING -> IDLE -> WORKING -> ... -> SHUTDOWN
```

## Example Session

```
User: Spawn alice as a coder to help with the API

Agent:
▸ spawn
  name: alice
  role: coder
  prompt: You are a Python API developer...
  Spawned teammate 'alice' (role: coder)

User: Send alice a message asking her to review the endpoints

Agent:
▸ send
  to: alice
  content: Please review all API endpoints in main.py
  Message sent to alice
```

## Best Practices

- Give each teammate a clear, specific role
- Use descriptive names (alice-coder, bob-tester)
- Check inbox regularly for responses
- Coordinate through messages, not direct calls