# CFEngine command line interface (CLI)

A CLI for humans to interact with CFEngine, enabling downloading and installing packages, building policy sets, deploying, and enforcing policy.
It is practically a wrapper around our other tools, like `cf-agent`, `cf-hub`, `cf-remote` and `cfbs`.

**Warning:** This is an early version.
Things might be missing or changed.
Proceed with caution and excitement.

## Installation

Install using pip:

```bash
pip install cfengine
```

## Usage

### Run the CFEngine agent - evaluate and enforce policy

```bash
cfengine run
```

### CFEngine CLI help

```bash
cfengine help
```

### Print CFEngine CLI version

```bash
cfengine version
```

### Automatically format source code

```bash
cfengine format
```

### Check for errors in source code

```bash
cfengine lint
```

You can also specify filenames or folders;

```bash
cfengine lint main.cf
```

When it finds a mistake, it points out where the problem is like this;

```
    "Hello, CFEngine"
      ifvarclass => "cfengine";
      ^--------^
Deprecation: Use 'if' instead of 'ifvarclass' at main.cf:5:7
FAIL: main.cf (1 errors)
Failure, 1 errors in total.
```

Note that since we use a different parser than `cf-agent` / `cf-promises`, they are not 100% in sync.
`cf-agent` could point out something as a syntax error, while `cfengine lint` does not and vice versa.
We aim to make the tree-sitter parser (used in this tool) more strict in general, so that when `cfengine lint` is happy with your policy, `cf-agent` will also accept it.
(But the opposite is not a goal, that `cfengine lint` must accept any policy `cf-agent` would find acceptable).

### Build a policy set

```bash
cfengine build
```

(This is equivalent to running `cfbs build`).

## Supported platforms and versions

This tool will only support a limited number of platforms, it is not intended to run everywhere CFEngine runs.
Currently we are targeting:

- Officially supported versions of macOS, Ubuntu, and Fedora.
- Officially supported versions of Python.

It is not intended to be installed on all hosts in your infrastructure.
CFEngine itself supports a wide range of platforms, but this tool is intended to run on your laptop, your workstation, or the hub in your infrastructure, not all the other hosts.

## Backwards compatibility

This CLI is entirely intended for humans.
If you put it into scripts and automation, expect it to break in the future.
In order to make the user experience better, we might add, change, or remove commands.
We will also be experimenting with different types of interactive prompts and input.

## Development, maintenance, contributions, and releases

Looking for more information related to contributing code, releasing new versions or otherwise maintaining the CFEngine CLI?
Please see the [HACKING.md](./HACKING.md) file.
