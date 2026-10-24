# Foundation offline checks

From repository root (Python 3.10+):

```sh
python3 -m unittest discover -s infra/openclaw/tests -v
python3 -m py_compile infra/openclaw/manage.py infra/openclaw/workspace/bin/tari-google
python3 -m json.tool infra/openclaw/config/openclaw.json >/dev/null
```

These checks verify bootstrap isolation, idempotence, permissions, legacy configuration compatibility,
CLI forwarding and the read-only Google boundary with fictional mocks;
they **do not** authenticate the ChatGPT account, build Docker images, run
Higgs/Pipecat, or call Google APIs. Run the actual Docker checks with
`python3 infra/openclaw/manage.py doctor` after starting the demo.
See [audit evidence](../../../docs/openclaw-audit.md) and
[owner acceptance steps](../../../docs/openclaw-setup.md).
