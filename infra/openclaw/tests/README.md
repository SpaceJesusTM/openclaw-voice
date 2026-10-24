# Foundation offline checks

From repository root (Python 3.10+):

```sh
python3 -m unittest discover -s infra/openclaw/tests -v
python3 -m py_compile infra/openclaw/manage.py infra/openclaw/workspace/bin/tari-google
python3 -m json.tool infra/openclaw/config/openclaw.json >/dev/null
```

These checks verify scaffold safety, idempotence and manifest syntax only;
they **do not** authenticate the ChatGPT account, build Docker images, run
Higgs/Pipecat, or call Google APIs. Live acceptance must be performed locally.
