# Working Session

GNU Screen can provide a shared terminal for pairing on a remote host.

## Attach

```bash
ssh <user>@<host>
screen -x bounded-lab
```

Use `screen -x` instead of `screen -r` when another terminal is already attached.

## Detach

Press:

```text
Ctrl-a
d
```

This leaves the session running on the server.

## Roles

- Use an unprivileged account for repository work, Python environments, and normal experiments.
- Use root only for host-level package installation, system services, container runtime changes,
  firewall changes, or Kubernetes installation.
- Do not paste tokens or passwords into a logged or shared terminal.

## Suggested Session

The shared session is named:

```text
bounded-lab
```

Start it in the repository checkout:

```text
$HOME/bounded-systems-lab
```
