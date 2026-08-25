# Current Demo Captures

This directory only accepts screenshots produced from the running deterministic demo:

```bash
make demo
python -m pip install -r requirements-demo-capture.txt
python -m scripts.capture_demo
```

The capture script requires local Chrome and `ffmpeg`. It fails if the page does not visibly disclose `Synthetic Demo`, `Mock Model`, and `Not Investment Advice`; generated or manually mocked UI images are not accepted.
