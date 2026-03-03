PYTHON ?= python

.PHONY: setup run report dry-run validator-dry-run cleanup

setup:
	cd testing-agent && $(PYTHON) -m pip install -r requirements.txt
	cd sample-app/frontend && npm install

run:
	$(PYTHON) run_harness.py

dry-run:
	$(PYTHON) run_harness.py --dry-run

validator-dry-run:
	$(PYTHON) testing-agent/validator.py --dry-run --failure-id 1 --branch failure-01 --repository owner/repo

report:
	$(PYTHON) -c "import json;from pathlib import Path;p=Path('reports/harness-report.json');print(json.dumps(json.loads(p.read_text()),indent=2) if p.exists() else 'No report found')"

cleanup:
	$(PYTHON) -c "import shutil; shutil.rmtree('reports', ignore_errors=True)"

